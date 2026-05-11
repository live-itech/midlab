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
from lib.utils import get_logger

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

        # Tutup koneksi lama jika ada
        await self._close_connection()

        self._reader = reader
        self._writer = writer
        self._connected = True

        # Spawn komponen dan jalankan receive loop
        self._init_components()
        await self._start_components()

        try:
            await self._receive_loop()
        finally:
            self._logger.info(f"{self._tag} Koneksi dari {peer} terputus")
            self._connected = False
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

                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        self._config.ip_address, self._config.port
                    ),
                    timeout=30,
                )

                self._connected = True
                self._logger.info(
                    f"{self._tag} Connected to "
                    f"{self._config.ip_address}:{self._config.port}"
                )

                # Spawn komponen dan jalankan receive loop
                self._init_components()
                await self._start_components()

                try:
                    await self._receive_loop()
                finally:
                    self._connected = False
                    await self._stop_components()
                    await self._close_connection()

            except (OSError, asyncio.TimeoutError) as e:
                self._logger.warning(
                    f"{self._tag} Gagal konek: {e}, retry dalam "
                    f"{RECONNECT_DELAY}s..."
                )

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

    async def _receive_loop(self):
        """
        Loop utama baca data dari socket.
        Dispatch ke ResultReceiver, dan jika ada query trigger
        ke QueryHandler.
        """
        self._logger.info(f"{self._tag} Receive loop dimulai")

        while self._running and self._connected:
            try:
                data = await self._reader.read(READ_BUFFER_SIZE)

                if not data:
                    # Koneksi ditutup oleh remote
                    self._logger.info(
                        f"{self._tag} Koneksi ditutup oleh remote"
                    )
                    break

                self._logger.info(
                    f"{self._tag} Menerima {len(data)} bytes"
                )

                # Dispatch ke ResultReceiver
                is_query = await self._receiver.handle_data(
                    data, self._writer
                )

                # Jika ResultReceiver mendeteksi query trigger
                if is_query and self._query_handler:
                    query_data = self._receiver.last_query_data
                    if query_data:
                        await self._query_handler.handle_query(query_data)
                        self._receiver.clear_last_query()
                    else:
                        # ASTM: data Q record ada dalam combined frames
                        # yang sudah di-handle oleh receiver
                        await self._query_handler.handle_query(data)

            except ConnectionResetError:
                self._logger.warning(
                    f"{self._tag} Connection reset by remote"
                )
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(
                    f"{self._tag} Error dalam receive loop: {e}"
                )
                break

        self._logger.info(f"{self._tag} Receive loop selesai")

    # ============================================================
    # Component Management
    # ============================================================

    def _init_components(self):
        """
        Inisialisasi komponen internal sesuai mode operasi.

        unidirectional:    ResultReceiver
        broadcast:         ResultReceiver + BroadcastWorker + Lock
        query:             ResultReceiver + QueryHandler
        broadcast+query:   ResultReceiver + BroadcastWorker + QueryHandler + Lock
        """
        # ResultReceiver selalu ada
        self._receiver = ResultReceiver(
            self._config, self._protocol, self._socket_lock
        )

        # BroadcastWorker jika mode broadcast
        if self._config.has_broadcast:
            self._broadcast_worker = BroadcastWorker(
                self._config, self._protocol,
                self._reader, self._writer, self._socket_lock
            )
            self._logger.info(
                f"{self._tag} BroadcastWorker initialized "
                f"(interval={self._config.broadcast_interval}s)"
            )

        # QueryHandler jika mode query
        if self._config.has_query:
            self._query_handler = QueryHandler(
                self._config, self._protocol,
                self._reader, self._writer, self._socket_lock
            )
            self._logger.info(f"{self._tag} QueryHandler initialized")

        mode_desc = self._describe_mode()
        self._logger.info(
            f"{self._tag} Komponen initialized: {mode_desc}"
        )

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
        """Tutup koneksi TCP aktif."""
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
