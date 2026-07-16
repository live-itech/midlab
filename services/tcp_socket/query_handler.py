"""
services/tcp_socket/query_handler.py — QueryHandler

Komponen TCPSocketService untuk mode bidirectional query.
Deteksi query (ENQ/QBP) dari alat, lookup order di database,
kirim response atau not-found, update status order.

State machine: WAIT_ENQ → ENQ_RX → LOOKUP → SEND_RESP → WAIT_ACK → UPDATE

Dua jenis query didukung, dibedakan lewat `type` yang dikembalikan
protocol_module.handle_enq():

- `query` (default)  — alat minta order untuk satu sample_id/barcode
- `group_query`      — alat minta semua order yang belum dikirim sekaligus
                       (batch download), satu pesan per order
- `cancel`           — alat membatalkan group download yang sedang berjalan

Group download hanya aktif bila protocol module menyediakan
format_group_query_response(); protocol tanpa method itu tetap membalas
not-found seperti sebelumnya.
"""

import asyncio
from enum import Enum

from lib.db import DBManager, TblOrder, update_order_status
from lib.utils import get_logger
from lib.comm_logger import CommLogger
from protocols.base import is_mllp_protocol


# Konstanta ASTM
ASTM_ACK = 0x06
ASTM_NAK = 0x15
ASTM_EOT = 0x04
ASTM_ENQ = 0x05

# Batas order yang dikirim dalam satu group download. Alat minta "semua sampel
# hari ini"; kalau backlog order menumpuk (mis. LIS baru pulih), pengiriman
# ratusan DSR sekaligus menahan receive loop terlalu lama. Sisanya ikut di
# group query berikutnya karena statusnya masih pending.
GROUP_QUERY_MAX_ORDERS = 100


class QueryState(Enum):
    """State machine untuk QueryHandler."""
    WAIT_ENQ = "WAIT_ENQ"
    ENQ_RX = "ENQ_RX"
    LOOKUP = "LOOKUP"
    SEND_RESP = "SEND_RESP"
    WAIT_ACK = "WAIT_ACK"
    UPDATE = "UPDATE"


class QueryHandler:
    """
    Handler untuk query bidirectional dari alat lab.

    Alur:
    1. WAIT_ENQ — ResultReceiver mendeteksi query trigger
    2. ENQ_RX — Parse query, ekstrak sample_id / patient_id
    3. LOOKUP — Cari order di tbl_order berdasarkan sample_id
    4. SEND_RESP — Kirim response (order data atau not-found)
    5. WAIT_ACK — Tunggu ACK dari alat
    6. UPDATE — Update tbl_order status

    ASTM query: ENQ → Q record dengan sample_id
    HL7 query:  QBP^Q22 message dengan QPD segment
    """

    def __init__(self, instrument_config, protocol_module,
                 reader, writer, socket_lock):
        """
        Args:
            instrument_config: InstrumentConfig
            protocol_module: Instance protocol module
            reader: asyncio.StreamReader
            writer: asyncio.StreamWriter
            socket_lock: asyncio.Lock bersama
        """
        self._config = instrument_config
        self._protocol = protocol_module
        self._reader = reader
        self._writer = writer
        self._lock = socket_lock
        self._logger = get_logger("tcp_socket", instrument_config.id)
        self._inst_name = instrument_config.name
        self._comm = CommLogger.for_instrument(instrument_config.id)

        self._state = QueryState.WAIT_ENQ

        # Data mentah terakhir saat menunggu ACK — dipakai mendeteksi pesan
        # cancel yang datang di posisi ACK saat group download.
        self._last_ack_data = b""

        # Statistik
        self._total_queries = 0
        self._total_found = 0
        self._total_not_found = 0

    # ============================================================
    # Public API
    # ============================================================

    async def handle_query(self, raw_bytes: bytes) -> bool:
        """
        Handle query yang terdeteksi oleh ResultReceiver.

        Args:
            raw_bytes: Data query (ENQ/Q record untuk ASTM, QBP message untuk HL7)

        Returns:
            True jika query berhasil diproses
        """
        self._total_queries += 1
        self._logger.info(
            f"[{self._inst_name}] Query #{self._total_queries} diterima "
            f"({len(raw_bytes)} bytes)"
        )

        instrument_dict = self._config.to_dict()

        try:
            # ENQ_RX — Parse query
            self._set_state(QueryState.ENQ_RX)
            enq_info = self._protocol.handle_enq(raw_bytes, instrument_dict)

            sample_id = enq_info.get("sample_id", "")
            patient_id = enq_info.get("patient_id", "")
            query_msh = enq_info.get("_msh")  # HL7: MSH dari query message
            query_type = enq_info.get("type", "")

            self._logger.info(
                f"[{self._inst_name}] Query parsed: type={query_type or 'query'}, "
                f"sample_id={sample_id}, patient_id={patient_id}"
            )

            # Cancel di luar group download yang sedang jalan: tidak ada yang
            # perlu dihentikan, dan alat tidak menunggu response.
            if query_type == "cancel":
                self._logger.info(
                    f"[{self._inst_name}] Cancel diterima saat idle — diabaikan"
                )
                return True

            if query_type == "group_query":
                return await self._handle_group_query(instrument_dict, query_msh)

            # LOOKUP — Cari order di database
            self._set_state(QueryState.LOOKUP)
            order = await self._lookup_order(sample_id, patient_id)

            if order:
                # Order ditemukan → kirim response
                order_id = order.id
                order_json = order.order_json or {}
                self._logger.info(
                    f"[{self._inst_name}] Order #{order_id} ditemukan "
                    f"untuk sample_id={sample_id}"
                )

                # SEND_RESP — Format dan kirim response
                self._set_state(QueryState.SEND_RESP)
                success = await self._send_query_response(
                    order_json, instrument_dict, query_msh
                )

                # UPDATE — Update status order
                self._set_state(QueryState.UPDATE)
                await self._update_order_result(order_id, success)

                if success:
                    self._total_found += 1
                    self._logger.info(
                        f"[{self._inst_name}] Order #{order_id} sent via query response"
                    )
                else:
                    self._logger.warning(
                        f"[{self._inst_name}] Order #{order_id} query response failed"
                    )

            else:
                # Order tidak ditemukan → kirim not-found
                self._logger.info(
                    f"[{self._inst_name}] Order tidak ditemukan "
                    f"untuk sample_id={sample_id}"
                )

                self._set_state(QueryState.SEND_RESP)
                await self._send_not_found(instrument_dict, query_msh)
                self._total_not_found += 1

        except Exception as e:
            self._logger.error(
                f"[{self._inst_name}] Error handling query: {e}"
            )
            return False

        finally:
            self._set_state(QueryState.WAIT_ENQ)

        return True

    def update_streams(self, reader, writer):
        """Update reader/writer saat reconnect."""
        self._reader = reader
        self._writer = writer

    # ============================================================
    # Group Download — alat minta semua order sekaligus
    # ============================================================

    async def _handle_group_query(self, instrument_dict: dict,
                                  query_msh: dict = None) -> bool:
        """
        Handle group download: kirim seluruh order pending untuk alat ini,
        satu pesan per order, lalu update status masing-masing.

        Alat boleh membatalkan di tengah jalan — pengiriman berhenti setelah
        order yang sedang dikirim selesai, sisanya tetap pending dan ikut di
        group query berikutnya.
        """
        formatter = getattr(self._protocol, "format_group_query_response", None)
        if formatter is None:
            self._logger.warning(
                f"[{self._inst_name}] Protocol {self._config.protocol} belum "
                f"mendukung group download — dibalas not-found"
            )
            self._set_state(QueryState.SEND_RESP)
            await self._send_not_found(instrument_dict, query_msh)
            self._total_not_found += 1
            return True

        # LOOKUP — ambil order yang belum terkirim ke alat ini
        self._set_state(QueryState.LOOKUP)
        orders = await self._lookup_pending_orders(GROUP_QUERY_MAX_ORDERS)

        if not orders:
            self._logger.info(
                f"[{self._inst_name}] Group download: tidak ada order pending"
            )
            self._set_state(QueryState.SEND_RESP)
            await self._send_not_found(instrument_dict, query_msh)
            self._total_not_found += 1
            return True

        self._logger.info(
            f"[{self._inst_name}] Group download: {len(orders)} order pending"
        )

        # SEND_RESP — protocol module membangun satu payload per order,
        # sejajar berdasarkan index (payload[i] milik orders[i]).
        self._set_state(QueryState.SEND_RESP)
        try:
            payloads = formatter(
                [o.order_json or {} for o in orders], instrument_dict, query_msh
            )
        except Exception as e:
            self._logger.error(
                f"[{self._inst_name}] Error formatting group response: {e}"
            )
            return False

        if len(payloads) != len(orders):
            self._logger.error(
                f"[{self._inst_name}] Group response tidak sejajar: "
                f"{len(payloads)} payload untuk {len(orders)} order — dibatalkan"
            )
            return False

        terkirim = 0
        for index, (order, payload) in enumerate(zip(orders, payloads)):
            self._set_state(QueryState.SEND_RESP)
            async with self._lock:
                success = await self._send_data(payload)

            # Cancel dicek sebelum update status: order yang kena cancel bukan
            # gagal kirim, jadi biarkan `pending` supaya ikut group query
            # berikutnya. Kalau ditandai `failed` ia keluar dari antrian dan
            # baru terkirim lagi setelah retry manual.
            if not success and self._is_cancel_query(self._last_ack_data):
                self._logger.info(
                    f"[{self._inst_name}] Group download dibatalkan alat setelah "
                    f"{terkirim}/{len(orders)} order — order #{order.id} dan "
                    f"sisanya tetap pending"
                )
                break

            self._set_state(QueryState.UPDATE)
            await self._update_order_result(order.id, success)

            if success:
                terkirim += 1
                self._total_found += 1
                continue

            self._logger.warning(
                f"[{self._inst_name}] Group download berhenti di order "
                f"#{order.id} ({index + 1}/{len(orders)}): response tidak di-ACK"
            )
            break

        self._logger.info(
            f"[{self._inst_name}] Group download selesai: "
            f"{terkirim}/{len(orders)} order terkirim"
        )
        return True

    async def _update_order_result(self, order_id: int, success: bool):
        """Tandai order sebagai sent/failed setelah percobaan kirim."""
        if success:
            await asyncio.get_event_loop().run_in_executor(
                None, update_order_status, order_id, "sent", None, None,
            )
        else:
            await asyncio.get_event_loop().run_in_executor(
                None,
                update_order_status,
                order_id,
                "failed",
                "query_handler_send",
                "ACK timeout atau NAK saat query response",
            )

    def _is_cancel_query(self, data: bytes) -> bool:
        """
        True bila data yang diterima (saat menunggu ACK) ternyata pesan cancel
        dari alat, bukan acknowledgment.
        """
        if not data:
            return False
        try:
            if not self._protocol.is_enq(data):
                return False
            info = self._protocol.handle_enq(data, self._config.to_dict())
            return info.get("type") == "cancel"
        except Exception as e:
            self._logger.warning(f"[{self._inst_name}] Gagal cek cancel: {e}")
            return False

    # ============================================================
    # Database Lookup
    # ============================================================

    async def _lookup_order(self, sample_id: str, patient_id: str):
        """
        Cari order di tbl_order berdasarkan sample_id atau patient_id.

        Urutan pencarian:
        1. Cari berdasarkan sample_id di order_json.specimen.sample_id
        2. Fallback: cari berdasarkan patient_id di order_json.patient.patient_id

        Returns:
            TblOrder object atau None
        """
        def _db_lookup():
            db = DBManager()
            session = db.get_session()
            try:
                # Cari order pending untuk instrument ini
                orders = (
                    session.query(TblOrder)
                    .filter(
                        TblOrder.instrument_id == self._config.id,
                        TblOrder.instrument_status == "pending",
                    )
                    .order_by(TblOrder.created_at.asc())
                    .all()
                )

                # Cari berdasarkan sample_id
                if sample_id:
                    for order in orders:
                        oj = order.order_json or {}
                        specimen = oj.get("specimen", {})
                        if specimen.get("sample_id") == sample_id:
                            session.expunge(order)
                            return order

                # Fallback: cari berdasarkan patient_id
                if patient_id:
                    for order in orders:
                        oj = order.order_json or {}
                        patient = oj.get("patient", {})
                        if patient.get("patient_id") == patient_id:
                            session.expunge(order)
                            return order

                return None

            except Exception as e:
                self._logger.error(
                    f"[{self._inst_name}] DB lookup error: {e}"
                )
                return None
            finally:
                session.close()

        return await asyncio.get_event_loop().run_in_executor(None, _db_lookup)

    async def _lookup_pending_orders(self, limit: int) -> list:
        """
        Ambil order pending untuk alat ini (paling lama duluan), untuk group
        download.

        Rentang waktu di query alat (QRF-2/QRF-3, "semua sampel hari ini" vs
        "sampel terbaru") sengaja tidak dipakai sebagai filter: flag `pending`
        sudah berarti "belum pernah dikirim ke alat", dan order otomatis jadi
        `sent` setelah terkirim — jadi group query berikutnya hanya dapat yang
        baru, persis seperti maksud mode "sampel terbaru". Membandingkan jam
        alat (waktu lokal) dengan created_at (UTC) justru berisiko membuang
        order secara diam-diam.
        """
        def _db_lookup():
            db = DBManager()
            session = db.get_session()
            try:
                orders = (
                    session.query(TblOrder)
                    .filter(
                        TblOrder.instrument_id == self._config.id,
                        TblOrder.instrument_status == "pending",
                    )
                    .order_by(TblOrder.created_at.asc())
                    .limit(limit)
                    .all()
                )
                for order in orders:
                    session.expunge(order)

                if len(orders) == limit:
                    self._logger.warning(
                        f"[{self._inst_name}] Group download dibatasi {limit} order; "
                        f"sisanya menyusul di group query berikutnya"
                    )
                return orders

            except Exception as e:
                self._logger.error(
                    f"[{self._inst_name}] DB lookup group error: {e}"
                )
                return []
            finally:
                session.close()

        return await asyncio.get_event_loop().run_in_executor(None, _db_lookup)

    # ============================================================
    # Send Response
    # ============================================================

    async def _send_query_response(self, order_json: dict, instrument_dict: dict,
                                   query_msh: dict = None) -> bool:
        """
        Format dan kirim response berisi order data.

        ASTM: format_query_response → kirim frames dengan ENQ/ACK/EOT
        HL7:  format_query_response_full → kirim MLLP message

        Returns: True jika ACK diterima
        """
        protocol = self._config.protocol.upper()

        try:
            if is_mllp_protocol(protocol) and query_msh and hasattr(self._protocol, "format_query_response_full"):
                formatted = self._protocol.format_query_response_full(
                    order_json, instrument_dict, query_msh
                )
            else:
                formatted = self._protocol.format_query_response(
                    order_json, instrument_dict
                )

            async with self._lock:
                return await self._send_data(formatted)

        except Exception as e:
            self._logger.error(
                f"[{self._inst_name}] Error formatting query response: {e}"
            )
            return False

    async def _send_not_found(self, instrument_dict: dict,
                              query_msh: dict = None):
        """Kirim response not-found ke alat."""
        protocol = self._config.protocol.upper()

        try:
            if is_mllp_protocol(protocol) and query_msh and hasattr(self._protocol, "format_query_not_found_full"):
                formatted = self._protocol.format_query_not_found_full(
                    instrument_dict, query_msh
                )
            else:
                formatted = self._protocol.format_query_not_found(instrument_dict)

            # Sebagian alat tidak membalas response not-found sama sekali
            # (Mindray: QCK dengan QAK NF tidak di-ACK). Menunggu ACK di situ
            # bukan cuma stall — read()-nya ikut menelan pesan alat berikutnya.
            expect_ack = getattr(self._protocol, "ACK_EXPECTED_ON_NOT_FOUND", True)

            async with self._lock:
                await self._send_data(formatted, expect_ack=expect_ack)

        except Exception as e:
            self._logger.warning(
                f"[{self._inst_name}] Error sending not-found: {e}"
            )

    async def _send_data(self, formatted, expect_ack: bool = True) -> bool:
        """
        Kirim data ke alat (protocol-aware).

        ASTM (list of frames): ENQ → ACK → frames (ACK per frame) → EOT
        HL7 (bytes):           send message → wait ACK (bila expect_ack)
        """
        protocol = self._config.protocol.upper()

        if protocol == "ASTM" and isinstance(formatted, list):
            return await self._send_astm_frames(formatted)
        else:
            return await self._send_hl7_message(formatted, expect_ack=expect_ack)

    async def _send_astm_frames(self, frames: list) -> bool:
        """Kirim frames ASTM dengan handshake."""
        try:
            # Kirim ENQ
            self._comm.tx(bytes([ASTM_ENQ]))
            self._writer.write(bytes([ASTM_ENQ]))
            await self._writer.drain()

            # Tunggu ACK
            self._set_state(QueryState.WAIT_ACK)
            ack = await self._wait_for_ack(timeout=15)
            if ack != "ACK":
                self._logger.warning(
                    f"[{self._inst_name}] Query ENQ tidak di-ACK: {ack}"
                )
                return False

            # Kirim frame satu per satu
            self._set_state(QueryState.SEND_RESP)
            for i, frame in enumerate(frames):
                self._comm.tx(frame)
                self._writer.write(frame)
                await self._writer.drain()

                self._set_state(QueryState.WAIT_ACK)
                ack = await self._wait_for_ack(timeout=15)
                if ack != "ACK":
                    self._logger.warning(
                        f"[{self._inst_name}] Query frame {i+1} tidak di-ACK"
                    )
                    return False
                self._set_state(QueryState.SEND_RESP)

            # Kirim EOT
            self._comm.tx(bytes([ASTM_EOT]))
            self._writer.write(bytes([ASTM_EOT]))
            await self._writer.drain()

            return True

        except (ConnectionError, OSError) as e:
            self._logger.error(
                f"[{self._inst_name}] Connection error saat query response: {e}"
            )
            return False

    async def _send_hl7_message(self, message: bytes, expect_ack: bool = True) -> bool:
        """Kirim HL7 message; tunggu ACK kecuali alat memang tidak membalas."""
        try:
            self._comm.tx(message)
            self._writer.write(message)
            await self._writer.drain()

            if not expect_ack:
                return True

            self._set_state(QueryState.WAIT_ACK)
            ack = await self._wait_for_ack(timeout=15)
            return ack == "ACK"

        except (ConnectionError, OSError) as e:
            self._logger.error(
                f"[{self._inst_name}] Connection error saat query response: {e}"
            )
            return False

    # ============================================================
    # ACK Waiting
    # ============================================================

    async def _wait_for_ack(self, timeout: float = 15) -> str:
        """
        Tunggu ACK dari alat.

        Data mentahnya disimpan di `_last_ack_data` karena yang datang belum
        tentu ACK — saat group download, alat bisa mengirim pesan cancel di
        posisi ini (lihat _is_cancel_query).
        """
        self._last_ack_data = b""
        try:
            data = await asyncio.wait_for(
                self._reader.read(4096),
                timeout=timeout,
            )
            if not data:
                return "TIMEOUT"
            self._comm.rx(data)
            self._last_ack_data = data

            return self._protocol.handle_ack(data)

        except asyncio.TimeoutError:
            self._logger.warning(
                f"[{self._inst_name}] Query ACK timeout ({timeout}s)"
            )
            return "TIMEOUT"
        except (ConnectionError, OSError):
            return "TIMEOUT"

    # ============================================================
    # State & Stats
    # ============================================================

    def _set_state(self, state: QueryState):
        self._state = state

    @property
    def state(self) -> QueryState:
        return self._state

    @property
    def stats(self) -> dict:
        return {
            "state": self._state.value,
            "total_queries": self._total_queries,
            "total_found": self._total_found,
            "total_not_found": self._total_not_found,
        }


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test QueryHandler ===\n")

    # Test QueryState enum
    assert QueryState.WAIT_ENQ.value == "WAIT_ENQ"
    assert QueryState.ENQ_RX.value == "ENQ_RX"
    assert QueryState.LOOKUP.value == "LOOKUP"
    assert QueryState.SEND_RESP.value == "SEND_RESP"
    assert QueryState.WAIT_ACK.value == "WAIT_ACK"
    assert QueryState.UPDATE.value == "UPDATE"
    print("OK: QueryState enum benar")

    # Mock objects
    class MockConfig:
        id = 1
        name = "TestInstrument"
        protocol = "ASTM"
        broadcast_interval = 30
        def to_dict(self):
            return {"id": self.id, "name": self.name}

    class MockProtocol:
        def handle_enq(self, raw_bytes, instrument):
            return {"type": "query", "sample_id": "SAMP001", "patient_id": ""}
        def format_query_response(self, order, instrument):
            return [b"frame1", b"frame2"]
        def format_query_not_found(self, instrument):
            return [b"not_found"]
        def handle_ack(self, data):
            return "ACK"

    lock = asyncio.Lock()
    handler = QueryHandler(MockConfig(), MockProtocol(), None, None, lock)

    assert handler.state == QueryState.WAIT_ENQ
    assert handler.stats["total_queries"] == 0
    assert handler.stats["total_found"] == 0
    assert handler.stats["total_not_found"] == 0
    print("OK: QueryHandler created")

    # Test state transitions
    handler._set_state(QueryState.ENQ_RX)
    assert handler.state == QueryState.ENQ_RX
    handler._set_state(QueryState.LOOKUP)
    assert handler.state == QueryState.LOOKUP
    handler._set_state(QueryState.WAIT_ENQ)
    assert handler.state == QueryState.WAIT_ENQ
    print("OK: State transitions benar")

    # Test stats
    handler._total_queries = 10
    handler._total_found = 7
    handler._total_not_found = 3
    stats = handler.stats
    assert stats["total_queries"] == 10
    assert stats["total_found"] == 7
    assert stats["total_not_found"] == 3
    print("OK: Stats benar")

    print("\n=== Semua test QueryHandler PASSED ===")
