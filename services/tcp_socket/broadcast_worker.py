"""
services/tcp_socket/broadcast_worker.py — BroadcastWorker

Komponen TCPSocketService untuk mode bidirectional broadcast.
Poll tbl_order secara periodik, kirim order ke alat via socket,
tunggu ACK, dan update status order di database.

State machine: IDLE → CHECK_DB → SEND → WAIT_ACK → UPDATE → IDLE
"""

import asyncio
from datetime import datetime, timezone
from enum import Enum

from lib.db import get_pending_orders, update_order_status
from lib.utils import get_logger
from lib.comm_logger import CommLogger


# Konstanta transport ASTM
ASTM_ENQ = 0x05
ASTM_ACK = 0x06
ASTM_NAK = 0x15
ASTM_EOT = 0x04


class BroadcastState(Enum):
    """State machine untuk BroadcastWorker."""
    IDLE = "IDLE"
    CHECK_DB = "CHECK_DB"
    SEND = "SEND"
    WAIT_ACK = "WAIT_ACK"
    UPDATE = "UPDATE"


class BroadcastWorker:
    """
    Worker yang mengirim order ke alat secara periodik (broadcast mode).

    Alur:
    1. IDLE — tunggu broadcast_interval detik
    2. CHECK_DB — poll tbl_order untuk order pending
    3. SEND — format order via protocol module, kirim ke socket
    4. WAIT_ACK — tunggu ACK dari alat (timeout 15 detik)
    5. UPDATE — update tbl_order status (sent/failed)
    6. Kembali ke IDLE

    Menggunakan asyncio.Lock untuk prevent collision dengan ResultReceiver.
    """

    def __init__(self, instrument_config, protocol_module,
                 reader, writer, socket_lock):
        """
        Args:
            instrument_config: InstrumentConfig
            protocol_module: Instance protocol module
            reader: asyncio.StreamReader dari koneksi aktif
            writer: asyncio.StreamWriter dari koneksi aktif
            socket_lock: asyncio.Lock bersama dengan ResultReceiver
        """
        self._config = instrument_config
        self._protocol = protocol_module
        self._reader = reader
        self._writer = writer
        self._lock = socket_lock
        self._logger = get_logger("tcp_socket", instrument_config.id)
        self._inst_name = instrument_config.name
        self._comm = CommLogger.for_instrument(instrument_config.id)

        self._state = BroadcastState.IDLE
        self._running = False
        self._task = None  # asyncio.Task

        # Statistik
        self._total_sent = 0
        self._total_failed = 0

    # ============================================================
    # Lifecycle
    # ============================================================

    async def start(self):
        """Start broadcast worker sebagai asyncio task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        self._logger.info(
            f"[{self._inst_name}] BroadcastWorker started, "
            f"interval={self._config.broadcast_interval}s"
        )

    async def stop(self):
        """Stop broadcast worker gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._logger.info(f"[{self._inst_name}] BroadcastWorker stopped")

    def update_streams(self, reader, writer):
        """Update reader/writer saat reconnect."""
        self._reader = reader
        self._writer = writer

    # ============================================================
    # Main Loop
    # ============================================================

    async def _run_loop(self):
        """Loop utama broadcast worker."""
        while self._running:
            try:
                # IDLE — tunggu interval
                self._set_state(BroadcastState.IDLE)
                await asyncio.sleep(self._config.broadcast_interval)

                if not self._running:
                    break

                # CHECK_DB — cari order pending
                self._set_state(BroadcastState.CHECK_DB)
                orders = await asyncio.get_event_loop().run_in_executor(
                    None,
                    get_pending_orders,
                    self._config.id,
                    10,
                )

                if not orders:
                    continue

                self._logger.info(
                    f"[{self._inst_name}] {len(orders)} pending order ditemukan"
                )

                # Proses satu per satu
                for order in orders:
                    if not self._running:
                        break
                    await self._process_order(order)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(
                    f"[{self._inst_name}] BroadcastWorker error: {e}"
                )
                await asyncio.sleep(5)  # Backoff sebelum retry

    async def _process_order(self, order):
        """
        Proses satu order: format → send → wait ACK → update.

        Args:
            order: TblOrder object dari database
        """
        order_id = order.id
        order_json = order.order_json or {}
        instrument_dict = self._config.to_dict()

        self._logger.info(
            f"[{self._inst_name}] Memproses order #{order_id}"
        )

        try:
            # SEND — format order via protocol module
            self._set_state(BroadcastState.SEND)
            formatted = self._protocol.format_order(order_json, instrument_dict)

            # Kirim ke alat (acquire lock agar tidak collision)
            async with self._lock:
                success = await self._send_formatted_data(formatted)

            if success:
                # WAIT_ACK berhasil → UPDATE sent
                self._set_state(BroadcastState.UPDATE)
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    update_order_status,
                    order_id,
                    "sent",
                    None,
                    None,
                )
                self._total_sent += 1
                self._logger.info(
                    f"[{self._inst_name}] Order #{order_id} sent successfully"
                )
            else:
                # Gagal → UPDATE failed
                self._set_state(BroadcastState.UPDATE)
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    update_order_status,
                    order_id,
                    "failed",
                    "broadcast_worker_send",
                    "ACK timeout atau NAK diterima",
                )
                self._total_failed += 1
                self._logger.warning(
                    f"[{self._inst_name}] Order #{order_id} failed to send"
                )

        except Exception as e:
            self._set_state(BroadcastState.UPDATE)
            await asyncio.get_event_loop().run_in_executor(
                None,
                update_order_status,
                order_id,
                "failed",
                "broadcast_worker",
                str(e),
            )
            self._total_failed += 1
            self._logger.error(
                f"[{self._inst_name}] Order #{order_id} error: {e}"
            )

    # ============================================================
    # Send Logic (protocol-aware)
    # ============================================================

    async def _send_formatted_data(self, formatted) -> bool:
        """
        Kirim data yang sudah diformat ke alat.

        ASTM: formatted = list of frame bytes
              Flow: ENQ → wait ACK → send frames (ACK per frame) → EOT
        HL7:  formatted = bytes (MLLP wrapped)
              Flow: send message → wait ACK message

        Args:
            formatted: Output dari protocol_module.format_order()

        Returns:
            True jika berhasil (ACK diterima)
        """
        protocol = self._config.protocol.upper()

        if protocol == "ASTM" and isinstance(formatted, list):
            return await self._send_astm_frames(formatted)
        else:
            return await self._send_hl7_message(formatted)

    async def _send_astm_frames(self, frames: list) -> bool:
        """
        Kirim frames ASTM dengan handshake ENQ/ACK/EOT.

        Flow:
        1. Kirim ENQ
        2. Tunggu ACK (timeout 15s)
        3. Kirim setiap frame, tunggu ACK per frame
        4. Kirim EOT
        """
        try:
            # 1. Kirim ENQ
            self._comm.tx(bytes([ASTM_ENQ]))
            self._writer.write(bytes([ASTM_ENQ]))
            await self._writer.drain()
            self._logger.info(f"[{self._inst_name}] ENQ sent")

            # 2. Tunggu ACK
            self._set_state(BroadcastState.WAIT_ACK)
            ack = await self._wait_for_ack(timeout=15)
            if ack != "ACK":
                self._logger.warning(
                    f"[{self._inst_name}] ENQ tidak di-ACK: {ack}"
                )
                return False

            # 3. Kirim frame satu per satu
            self._set_state(BroadcastState.SEND)
            for i, frame in enumerate(frames):
                self._comm.tx(frame)
                self._writer.write(frame)
                await self._writer.drain()
                self._logger.info(
                    f"[{self._inst_name}] Frame {i+1}/{len(frames)} sent "
                    f"({len(frame)} bytes)"
                )

                # Tunggu ACK per frame
                self._set_state(BroadcastState.WAIT_ACK)
                ack = await self._wait_for_ack(timeout=15)
                if ack != "ACK":
                    self._logger.warning(
                        f"[{self._inst_name}] Frame {i+1} tidak di-ACK: {ack}"
                    )
                    return False
                self._set_state(BroadcastState.SEND)

            # 4. Kirim EOT
            self._comm.tx(bytes([ASTM_EOT]))
            self._writer.write(bytes([ASTM_EOT]))
            await self._writer.drain()
            self._logger.info(f"[{self._inst_name}] EOT sent, sesi selesai")

            return True

        except (ConnectionError, OSError) as e:
            self._logger.error(
                f"[{self._inst_name}] Connection error saat broadcast: {e}"
            )
            return False

    async def _send_hl7_message(self, message: bytes) -> bool:
        """
        Kirim HL7 message (MLLP wrapped) dan tunggu ACK.

        Flow:
        1. Kirim MLLP message
        2. Tunggu ACK message (parse MSA segment)
        """
        try:
            self._comm.tx(message)
            self._writer.write(message)
            await self._writer.drain()
            self._logger.info(
                f"[{self._inst_name}] HL7 message sent ({len(message)} bytes)"
            )

            # Tunggu ACK
            self._set_state(BroadcastState.WAIT_ACK)
            ack = await self._wait_for_hl7_ack(timeout=15)
            return ack == "ACK"

        except (ConnectionError, OSError) as e:
            self._logger.error(
                f"[{self._inst_name}] Connection error saat broadcast: {e}"
            )
            return False

    # ============================================================
    # ACK Waiting
    # ============================================================

    async def _wait_for_ack(self, timeout: float = 15) -> str:
        """
        Tunggu ACK byte dari alat (ASTM).

        Returns: 'ACK', 'NAK', 'EOT', atau 'TIMEOUT'
        """
        try:
            data = await asyncio.wait_for(
                self._reader.read(1024),
                timeout=timeout,
            )
            if not data:
                return "TIMEOUT"
            self._comm.rx(data)

            return self._protocol.handle_ack(data)

        except asyncio.TimeoutError:
            self._logger.warning(
                f"[{self._inst_name}] ACK timeout ({timeout}s)"
            )
            return "TIMEOUT"
        except (ConnectionError, OSError):
            return "TIMEOUT"

    async def _wait_for_hl7_ack(self, timeout: float = 15) -> str:
        """
        Tunggu ACK message HL7 (berisi MSA segment).

        Returns: 'ACK', 'NAK', atau 'TIMEOUT'
        """
        try:
            data = await asyncio.wait_for(
                self._reader.read(4096),
                timeout=timeout,
            )
            if not data:
                return "TIMEOUT"
            self._comm.rx(data)

            return self._protocol.handle_ack(data)

        except asyncio.TimeoutError:
            self._logger.warning(
                f"[{self._inst_name}] HL7 ACK timeout ({timeout}s)"
            )
            return "TIMEOUT"
        except (ConnectionError, OSError):
            return "TIMEOUT"

    # ============================================================
    # State Management
    # ============================================================

    def _set_state(self, state: BroadcastState):
        self._state = state

    @property
    def state(self) -> BroadcastState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "state": self._state.value,
            "running": self._running,
            "total_sent": self._total_sent,
            "total_failed": self._total_failed,
        }


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test BroadcastWorker ===\n")

    # Test BroadcastState enum
    assert BroadcastState.IDLE.value == "IDLE"
    assert BroadcastState.CHECK_DB.value == "CHECK_DB"
    assert BroadcastState.SEND.value == "SEND"
    assert BroadcastState.WAIT_ACK.value == "WAIT_ACK"
    assert BroadcastState.UPDATE.value == "UPDATE"
    print("OK: BroadcastState enum benar")

    # Mock objects
    class MockConfig:
        id = 1
        name = "TestInstrument"
        protocol = "ASTM"
        broadcast_interval = 5
        def to_dict(self):
            return {"id": self.id, "name": self.name}

    class MockProtocol:
        def format_order(self, order, instrument):
            return [b"\x02frame1\x03", b"\x02frame2\x03"]
        def handle_ack(self, data):
            if data[0:1] == b"\x06":
                return "ACK"
            return "UNKNOWN"

    lock = asyncio.Lock()
    config = MockConfig()
    protocol = MockProtocol()

    # Test construction (tanpa real streams)
    worker = BroadcastWorker(config, protocol, None, None, lock)
    assert worker.state == BroadcastState.IDLE
    assert worker.is_running is False
    assert worker.stats["total_sent"] == 0
    assert worker.stats["total_failed"] == 0
    print("OK: BroadcastWorker created")

    # Test state transitions
    worker._set_state(BroadcastState.CHECK_DB)
    assert worker.state == BroadcastState.CHECK_DB
    worker._set_state(BroadcastState.SEND)
    assert worker.state == BroadcastState.SEND
    print("OK: State transitions benar")

    # Test stats
    worker._total_sent = 5
    worker._total_failed = 2
    stats = worker.stats
    assert stats["total_sent"] == 5
    assert stats["total_failed"] == 2
    print("OK: Stats benar")

    print("\n=== Semua test BroadcastWorker PASSED ===")
