"""
services/tcp_socket/service.py — TCPSocketService untuk MidLab

Service utama yang mengelola koneksi TCP per alat lab.
Mendukung mode server (listen) dan client (connect ke alat).
Spawn komponen internal sesuai mode operasi:
- Unidirectional:    ResultReceiver
- Broadcast:         ResultReceiver + BroadcastWorker + Lock
- Query:             ResultReceiver + QueryHandler
- Broadcast+Query:   ResultReceiver + BroadcastWorker + QueryHandler + Lock

Fitur:
- Dynamic protocol loading via importlib (ASTM/HL7/BCI)
- Auto-reconnect jika koneksi putus
- Graceful shutdown via SIGTERM/SIGINT
"""

import asyncio
import signal
import sys

from protocols.base import load_module
from lib.db import enqueue_lis_event
from lib.utils import get_logger
from lib.comm_logger import CommLogger

from services.tcp_socket.config import InstrumentConfig
from services.tcp_socket.receiver import ResultReceiver
from services.tcp_socket.broadcast_worker import BroadcastWorker
from services.tcp_socket.query_handler import QueryHandler


# Interval reconnect (detik)
RECONNECT_DELAY = 5
# Timeout baca socket (detik) — 0 = blocking sampai data ada
READ_TIMEOUT = None
# Ukuran buffer baca
READ_BUFFER_SIZE = 4096


class TCPSocketService:
    """
    Service TCP per instrument.

    Lifecycle:
    1. __init__ — simpan config, load protocol module
    2. start() — buka koneksi (server/client), spawn komponen, masuk receive loop
    3. stop() — tutup semua task, socket, cleanup

    Reconnect otomatis jika koneksi putus (client mode: konek ulang,
    server mode: tunggu koneksi baru).
    """

    def __init__(self, config: InstrumentConfig):
        """
        Args:
            config: InstrumentConfig dari load_instrument_config()
        """
        self._config = config
        self._logger = get_logger("tcp_socket", config.id)
        self._tag = f"[TCP_{config.id}] [{config.name}]"
        self._comm = CommLogger.for_instrument(config.id)

        # Load protocol module secara dynamic
        self._protocol = load_module(config.protocol)
        self._logger.info(
            f"{self._tag} Protocol loaded: {self._protocol.PROTOCOL_NAME} "
            f"v{self._protocol.VERSION}"
        )

        # Shared lock untuk akses socket (broadcast + receiver)
        self._socket_lock = asyncio.Lock()

        # Komponen internal (dibuat saat koneksi aktif)
        self._receiver: ResultReceiver | None = None
        self._broadcast_worker: BroadcastWorker | None = None
        self._query_handler: QueryHandler | None = None

        # Koneksi
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._server: asyncio.Server | None = None  # Mode server

        # Penanda per-koneksi: diset _close_connection() saat MidLab yang
        # menutup. Receive loop memegang referensi dict-nya sendiri, jadi
        # koneksi lama tidak terpengaruh penanda koneksi baru.
        self._konteks_koneksi: dict | None = None

        # State
        self._running = False
        self._connected = False
        self._shutdown_event = asyncio.Event()

    # ============================================================
    # Public API
    # ============================================================

    async def start(self):
        """
        Start service: setup signal handler, buka koneksi, masuk main loop.
        Blocking sampai service di-stop atau SIGTERM diterima.
        """
        self._running = True
        self._logger.info(
            f"{self._tag} Starting service — mode={self._config.mode} "
            f"bidir={self._config.bidir_mode} conn={self._config.connection}"
        )

        # Setup signal handlers untuk graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_handler, sig)

        try:
            if self._config.is_server:
                await self._run_server_mode()
            else:
                await self._run_client_mode()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self):
        """Stop service: tutup semua komponen dan koneksi."""
        if not self._running:
            return

        self._running = False
        self._logger.info(f"{self._tag} Stopping service...")

        # Stop komponen internal
        await self._stop_components()

        # Tutup koneksi
        await self._close_connection()

        # Tutup server socket (mode server)
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        self._shutdown_event.set()
        self._logger.info(f"{self._tag} Service stopped")

    def _emit_lis_status(self, status: str, error_message: str | None = None):
        """Enqueue status event ke tbl_lis_event_queue (di-drain LisBridgeService)."""
        payload = {"status": status}
        if error_message:
            payload["error_message"] = error_message[:500]
        try:
            enqueue_lis_event(
                instrument_id=self._config.id,
                event_type="status",
                payload=payload,
            )
        except Exception as e:
            self._logger.warning(f"{self._tag} enqueue status={status} gagal: {e}")

    def _signal_handler(self, sig):
        """Handle SIGTERM/SIGINT — trigger graceful shutdown."""
        sig_name = signal.Signals(sig).name
        self._logger.info(f"{self._tag} {sig_name} diterima, shutting down...")
        self._running = False
        self._shutdown_event.set()

    # ============================================================
    # Server Mode — listen, terima koneksi dari alat
    # ============================================================

    async def _run_server_mode(self):
        """
        Mode server: buka listen socket, tunggu koneksi dari alat.
        Setelah koneksi masuk, jalankan receive loop.
        Jika koneksi putus, tunggu koneksi baru (auto-accept).
        """
        while self._running:
            try:
                self._logger.info(
                    f"{self._tag} Mendengarkan di "
                    f"{self._config.ip_address}:{self._config.port}..."
                )

                self._server = await asyncio.start_server(
                    self._handle_server_connection,
                    host=self._config.ip_address,
                    port=self._config.port,
                )

                # Tunggu sampai shutdown
                await self._shutdown_event.wait()
                break

            except OSError as e:
                self._logger.error(
                    f"{self._tag} Gagal bind {self._config.ip_address}:"
                    f"{self._config.port}: {e}"
                )
                # Catat error agar UI Services bisa render row merah saat bind
                # listener gagal (mis. port already in use, IP salah).
                self._emit_lis_status("error", str(e))
                if not self._running:
                    break
                await asyncio.sleep(RECONNECT_DELAY)

    async def _handle_server_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """
        Callback saat alat konek ke server.
        Satu koneksi aktif pada satu waktu per instrument.
        """
        peer = writer.get_extra_info("peername")
        self._logger.info(f"{self._tag} Koneksi masuk dari {peer}")

        # Tutup koneksi lama beserta komponennya SEBELUM membangun yang baru.
        # Urutan ini disengaja: kalau cleanup koneksi lama dibiarkan berjalan
        # di task-nya sendiri, ia bisa menimpa komponen milik koneksi baru.
        await self._close_connection()
        await self._stop_components()

        konteks = {"ditutup_midlab": False}
        self._reader = reader
        self._writer = writer
        self._konteks_koneksi = konteks
        self._connected = True
        self._emit_lis_status("online")

        # Spawn komponen dan jalankan receive loop
        receiver = self._init_components(reader, writer)
        await self._start_components()

        try:
            await self._receive_loop(reader, writer, receiver, konteks)
        finally:
            await self._selesaikan_koneksi(peer, receiver)

    async def _selesaikan_koneksi(self, peer, receiver: ResultReceiver):
        """
        Cleanup setelah receive loop satu koneksi selesai.

        State bersama (self._connected, komponen) hanya dibersihkan bila
        koneksi ini masih koneksi aktif. Alat ini membuka koneksi baru tiap
        beberapa detik, jadi cleanup koneksi lama kerap berjalan setelah
        koneksi penerus sudah siap — tanpa penjagaan ini, koneksi lama akan
        mematikan receiver milik penerusnya dan hasil bisa hilang.
        """
        self._logger.info(f"{self._tag} Koneksi dari {peer} terputus")

        if self._receiver is not receiver:
            # Sudah digantikan koneksi lain — jangan sentuh state bersama.
            receiver.reset_buffer()
            return

        self._connected = False
        self._emit_lis_status("offline")
        await self._stop_components()

    # ============================================================
    # Client Mode — konek ke IP:port alat
    # ============================================================

    async def _run_client_mode(self):
        """
        Mode client: konek ke IP:port alat.
        Jika koneksi putus, reconnect otomatis setelah delay.
        """
        while self._running:
            try:
                self._logger.info(
                    f"{self._tag} Connecting to "
                    f"{self._config.ip_address}:{self._config.port}..."
                )

                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        self._config.ip_address, self._config.port
                    ),
                    timeout=30,
                )
                konteks = {"ditutup_midlab": False}
                self._reader, self._writer = reader, writer
                self._konteks_koneksi = konteks

                self._connected = True
                self._logger.info(
                    f"{self._tag} Connected to "
                    f"{self._config.ip_address}:{self._config.port}"
                )
                self._emit_lis_status("online")

                # Spawn komponen dan jalankan receive loop
                receiver = self._init_components(reader, writer)
                await self._start_components()

                try:
                    await self._receive_loop(reader, writer, receiver, konteks)
                finally:
                    self._connected = False
                    self._emit_lis_status("offline")
                    await self._stop_components()
                    await self._close_connection()

            except (OSError, asyncio.TimeoutError) as e:
                self._logger.warning(
                    f"{self._tag} Gagal konek: {e}, retry dalam "
                    f"{RECONNECT_DELAY}s..."
                )
                self._emit_lis_status("error", str(e))

            if not self._running:
                break

            # Reconnect delay
            self._logger.info(
                f"{self._tag} Reconnect dalam {RECONNECT_DELAY}s..."
            )
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=RECONNECT_DELAY,
                )
                # Jika shutdown_event di-set, keluar
                break
            except asyncio.TimeoutError:
                # Timeout = reconnect delay selesai, lanjut loop
                pass

    # ============================================================
    # Receive Loop — inti penerimaan data dari alat
    # ============================================================

    async def _receive_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        receiver: ResultReceiver,
        konteks: dict,
    ):
        """
        Loop utama baca data dari socket.
        Dispatch ke ResultReceiver, dan jika ada query trigger
        ke QueryHandler.

        reader/writer/receiver/konteks diterima sebagai parameter, bukan dibaca
        dari self._*: satu alat bisa punya koneksi lama dan baru hidup bersamaan
        sesaat, dan loop koneksi lama tidak boleh ikut membaca socket koneksi
        baru saat atribut instance ditimpa.
        """
        self._logger.info(f"{self._tag} Receive loop dimulai")

        while self._running:
            # Dicek di awal iterasi, bukan hanya sebagai syarat while: penutupan
            # oleh MidLab bisa terjadi sebelum read() sempat dipanggil lagi, dan
            # kasus itu tetap harus tercatat sebabnya.
            if konteks["ditutup_midlab"]:
                self._log_penutupan(konteks, reset=False)
                break

            try:
                data = await reader.read(READ_BUFFER_SIZE)
                if data:
                    self._comm.rx(data)

                if not data:
                    self._log_penutupan(konteks, reset=False)
                    break

                self._logger.info(
                    f"{self._tag} Menerima {len(data)} bytes"
                )

                # Dispatch ke ResultReceiver
                is_query = await receiver.handle_data(data, writer)

                # Jika ResultReceiver mendeteksi query trigger
                if is_query and self._query_handler:
                    query_data = receiver.last_query_data
                    if query_data:
                        await self._query_handler.handle_query(query_data)
                        receiver.clear_last_query()
                    else:
                        # ASTM: data Q record ada dalam combined frames
                        # yang sudah di-handle oleh receiver
                        await self._query_handler.handle_query(data)

            except ConnectionResetError:
                self._log_penutupan(konteks, reset=True)
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(
                    f"{self._tag} Error dalam receive loop: {e}"
                )
                break

        self._logger.info(f"{self._tag} Receive loop selesai")

    def _log_penutupan(self, konteks: dict, reset: bool):
        """
        Catat sebab koneksi berakhir, dibedakan siapa yang memutus.

        Dulu semua kasus dilaporkan "by remote", termasuk koneksi yang MidLab
        sendiri tutup lewat _close_connection() saat alat membuka koneksi
        baru. Akibatnya log penuh WARNING reset palsu yang menyamarkan reset
        sungguhan dari alat.

        Penanda diset _close_connection(), BUKAN dibaca dari
        writer.is_closing(). is_closing() tidak bisa membedakan keduanya:
        pada RST dari alat, asyncio menutup transport sisi kita juga sehingga
        is_closing() ikut True. Diukur dengan socket lokal:

            klien tutup baik-baik (FIN) -> writer.is_closing() = False
            klien abort (RST)           -> writer.is_closing() = True

        Dikunci oleh tests/test_tcp_connection_lifecycle.py::
        test_rst_alat_tidak_tertukar_dengan_penutupan_midlab.
        """
        if konteks["ditutup_midlab"]:
            sebab = "service berhenti" if not self._running \
                else "digantikan koneksi baru"
            self._logger.info(
                f"{self._tag} Koneksi ditutup MidLab ({sebab})"
            )
        elif reset:
            self._logger.warning(
                f"{self._tag} Koneksi di-reset alat (RST)"
            )
        else:
            self._logger.info(
                f"{self._tag} Koneksi ditutup alat (FIN)"
            )

    # ============================================================
    # Component Management
    # ============================================================

    def _init_components(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> ResultReceiver:
        """
        Inisialisasi komponen internal sesuai mode operasi.

        unidirectional:    ResultReceiver
        broadcast:         ResultReceiver + BroadcastWorker + Lock
        query:             ResultReceiver + QueryHandler
        broadcast+query:   ResultReceiver + BroadcastWorker + QueryHandler + Lock

        Returns:
            ResultReceiver milik koneksi ini. Pemanggil memegangnya sebagai
            variabel lokal agar cleanup-nya bisa dibedakan dari koneksi lain.
        """
        # ResultReceiver selalu ada
        receiver = ResultReceiver(
            self._config, self._protocol, self._socket_lock
        )
        self._receiver = receiver

        # BroadcastWorker jika mode broadcast
        if self._config.has_broadcast:
            self._broadcast_worker = BroadcastWorker(
                self._config, self._protocol,
                reader, writer, self._socket_lock
            )
            self._logger.info(
                f"{self._tag} BroadcastWorker initialized "
                f"(interval={self._config.broadcast_interval}s)"
            )

        # QueryHandler jika mode query
        if self._config.has_query:
            self._query_handler = QueryHandler(
                self._config, self._protocol,
                reader, writer, self._socket_lock
            )
            self._logger.info(f"{self._tag} QueryHandler initialized")

        mode_desc = self._describe_mode()
        self._logger.info(
            f"{self._tag} Komponen initialized: {mode_desc}"
        )
        return receiver

    async def _start_components(self):
        """Start komponen async (BroadcastWorker task)."""
        if self._broadcast_worker:
            await self._broadcast_worker.start()

    async def _stop_components(self):
        """Stop semua komponen internal."""
        if self._broadcast_worker:
            await self._broadcast_worker.stop()
            self._broadcast_worker = None

        self._query_handler = None

        if self._receiver:
            self._receiver.reset_buffer()
            self._receiver = None

    async def _close_connection(self):
        """Tutup koneksi TCP aktif dan tandai bahwa MidLab yang menutupnya."""
        if self._konteks_koneksi is not None:
            self._konteks_koneksi["ditutup_midlab"] = True
            self._konteks_koneksi = None

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    # ============================================================
    # Helpers
    # ============================================================

    def _describe_mode(self) -> str:
        """Deskripsi komponen aktif untuk logging."""
        parts = ["ResultReceiver"]
        if self._broadcast_worker:
            parts.append("BroadcastWorker")
        if self._query_handler:
            parts.append("QueryHandler")
        if self._config.has_broadcast:
            parts.append("Lock")
        return " + ".join(parts)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> dict:
        """Statistik lengkap service dan komponen."""
        result = {
            "instrument_id": self._config.id,
            "instrument_name": self._config.name,
            "running": self._running,
            "connected": self._connected,
            "mode": self._config.mode,
            "bidir_mode": self._config.bidir_mode,
            "connection": self._config.connection,
            "protocol": self._config.protocol,
        }
        if self._receiver:
            result["receiver"] = self._receiver.stats
        if self._broadcast_worker:
            result["broadcast_worker"] = self._broadcast_worker.stats
        if self._query_handler:
            result["query_handler"] = self._query_handler.stats
        return result
