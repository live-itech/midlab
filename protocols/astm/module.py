"""
protocols/astm/module.py — ASTM Protocol Module untuk MidLab

Implementasi lengkap BaseProtocolModule untuk protokol ASTM E1381/E1394.
Mendukung:
- Unidirectional: terima hasil dari alat (parse → ResultObject)
- Bidirectional broadcast: kirim order ke alat secara periodik
- Bidirectional query: respond permintaan order dari alat (ENQ/Q record)

Class ini dipanggil oleh TCPSocketService dan di-load secara dynamic
via protocols.base.load_module("ASTM").
"""

from lib.utils import get_logger, generate_message_id, format_datetime
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.astm.constants import (
    ENQ, ACK, NAK, EOT,
    ENQ_BYTE, ACK_BYTE, NAK_BYTE, EOT_BYTE,
    STX, ETX, ETB, CR, LF,
    RECORD_HEADER, RECORD_PATIENT, RECORD_ORDER, RECORD_RESULT,
    RECORD_TERMINATOR, RECORD_QUERY,
)
from protocols.astm.parser import ASTMParser
from protocols.astm.builder import ASTMBuilder


class ASTMModule(BaseProtocolModule):
    """
    Protocol module ASTM E1381/E1394 untuk MidLab.

    Menangani semua aspek komunikasi ASTM:
    - Parsing hasil pemeriksaan dari alat
    - Building order messages untuk broadcast mode
    - Handling query (ENQ) dari alat untuk query mode
    - ACK/NAK/EOT detection
    """

    def __init__(self):
        self._parser = ASTMParser()
        self._builder = ASTMBuilder()
        self._logger = get_logger("astm_module")

    # ============================================================
    # Properties
    # ============================================================

    @property
    def PROTOCOL_NAME(self) -> str:
        return "ASTM"

    @property
    def VERSION(self) -> str:
        return "1.0.0"

    # ============================================================
    # parse() — Raw bytes → ResultObject dict
    # ============================================================

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Parse raw bytes dari alat menjadi ResultObject dict.

        Alur:
        1. Split raw bytes menjadi frame-frame individu
        2. Parse setiap frame → extract records
        3. Gabung data dari H/P/O/R/L records → ResultObject

        Args:
            raw_bytes: Data mentah dari TCP socket (bisa multi-frame)
            instrument: Dict info instrumen dari tbl_instrument

        Returns:
            Dict ResultObject sesuai format CLAUDE.md
        """
        instrument_id = instrument.get("id", 0)
        self._logger.info(
            f"Mulai parsing {len(raw_bytes)} bytes dari instrument {instrument_id}"
        )

        parse_errors = []

        # Split raw bytes menjadi frame-frame berdasarkan STX..ETX/ETB
        frames = self._split_into_frames(raw_bytes)
        self._logger.info(f"Ditemukan {len(frames)} frame(s)")

        if not frames:
            # Coba parse sebagai plain text records (beberapa alat kirim tanpa framing)
            frames = self._split_plain_records(raw_bytes)
            if not frames:
                parse_errors.append("Tidak ada frame valid ditemukan")
                return ResultObject(
                    instrument_id=instrument_id,
                    protocol="ASTM",
                    parse_errors=parse_errors,
                ).to_dict()

        # Parse semua frame → list of records
        records = self._parser.parse_message(frames)

        if not records:
            parse_errors.append("Tidak ada record valid setelah parsing")
            return ResultObject(
                instrument_id=instrument_id,
                protocol="ASTM",
                parse_errors=parse_errors,
            ).to_dict()

        # Gabung records menjadi ResultObject
        result = self._assemble_result(records, instrument_id)

        # Tambahkan parse errors jika ada
        if parse_errors:
            result.parse_errors.extend(parse_errors)

        self._logger.info(
            f"Parsing selesai: {len(result.results)} hasil, "
            f"{len(result.parse_errors)} error(s)"
        )
        return result.to_dict()

    def _split_into_frames(self, raw_bytes: bytes) -> list:
        """Split raw bytes menjadi individual frames berdasarkan STX..ETX/ETB."""
        frames = []
        i = 0
        while i < len(raw_bytes):
            # Cari STX
            stx_pos = raw_bytes.find(STX, i)
            if stx_pos == -1:
                break

            # Cari ETX atau ETB setelah STX
            end_pos = None
            for j in range(stx_pos + 1, len(raw_bytes)):
                if raw_bytes[j] in (ETX, ETB):
                    end_pos = j
                    break

            if end_pos is None:
                break

            # Ambil frame lengkap termasuk checksum + CRLF
            # Format: STX...ETX/ETB + 2 char checksum + CR + LF
            frame_end = min(end_pos + 5, len(raw_bytes))  # +2 checksum +2 CRLF +1
            frame = raw_bytes[stx_pos:frame_end]
            frames.append(frame)

            i = frame_end

        return frames

    def _split_plain_records(self, raw_bytes: bytes) -> list:
        """Split plain text records (tanpa STX/ETX framing) berdasarkan CR/LF."""
        text = raw_bytes.decode("ascii", errors="replace")
        lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
        # Filter hanya baris yang dimulai dengan record type valid
        valid_types = {"H", "P", "O", "R", "L", "Q", "C"}
        records = [line.encode("ascii") for line in lines if line and line[0] in valid_types]
        return records if records else []

    def _assemble_result(self, records: list, instrument_id: int) -> ResultObject:
        """Gabung list of parsed records menjadi satu ResultObject."""
        result = ResultObject(
            instrument_id=instrument_id,
            protocol="ASTM",
        )

        for rec in records:
            rtype = rec.get("record_type", "")

            if rtype == RECORD_PATIENT:
                result.patient = PatientInfo(
                    patient_id=rec.get("patient_id", ""),
                    name=rec.get("name", ""),
                    dob=rec.get("dob", ""),
                    gender=rec.get("gender", ""),
                    physician=rec.get("physician", ""),
                )

            elif rtype == RECORD_ORDER:
                result.specimen = SpecimenInfo(
                    sample_id=rec.get("sample_id", ""),
                    sample_type="",
                    collected_at=rec.get("collected_at", ""),
                )
                result.order = OrderInfo(
                    order_id=rec.get("instrument_specimen_id", ""),
                    panel=rec.get("panel", ""),
                )

            elif rtype == RECORD_RESULT:
                result.results.append(TestResult(
                    test_code=rec.get("test_code", ""),
                    test_name=rec.get("test_name", ""),
                    value=rec.get("value", ""),
                    unit=rec.get("unit", ""),
                    reference_range=rec.get("reference_range", ""),
                    flag=rec.get("flag", ""),
                    status=rec.get("status", ""),
                ))

        return result

    # ============================================================
    # format_order() — Order → bytes untuk broadcast mode
    # ============================================================

    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        Format order menjadi frames ASTM untuk broadcast ke alat.

        Args:
            order: Dict OrderObject dari tbl_order.order_json
            instrument: Dict instrumen

        Returns:
            List of bytes frames [H, P, O, L]
        """
        self._logger.info(
            f"Formatting order {order.get('order_id', '?')} "
            f"untuk instrument {instrument.get('name', '?')}"
        )
        return self._builder.build_enq_response(order, instrument)

    # ============================================================
    # ENQ / Query handling — bidirectional support
    # ============================================================

    def is_enq(self, raw_bytes: bytes) -> bool:
        """
        Deteksi apakah data merupakan ENQ dari alat.

        ASTM ENQ = byte 0x05 (bisa standalone atau prefix).
        Juga deteksi Q record sebagai query trigger.

        Args:
            raw_bytes: Data yang diterima dari socket

        Returns:
            True jika merupakan ENQ atau query trigger
        """
        if not raw_bytes:
            return False

        # Cek ENQ byte langsung
        if raw_bytes[0:1] == ENQ_BYTE:
            self._logger.info("ENQ byte terdeteksi")
            return True

        # Cek Q record dalam frame
        try:
            text = raw_bytes.decode("ascii", errors="replace")
            # Q record bisa di dalam frame atau plain text
            if "Q|" in text:
                self._logger.info("Q record terdeteksi sebagai query trigger")
                return True
        except Exception:
            pass

        return False

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Handle ENQ/query dari alat, ekstrak informasi query.

        Dua skenario:
        1. ENQ byte saja → alat ingin kirim data/query, return basic info
        2. Frame berisi Q record → parse sample_id dari Q record

        Args:
            raw_bytes: Data ENQ/query dari alat
            instrument: Dict info instrumen

        Returns:
            Dict: {type, sample_id, patient_id, raw_query}
        """
        result = {
            "type": "enq",
            "sample_id": "",
            "patient_id": "",
            "raw_query": raw_bytes.hex(),
        }

        # Skenario 1: ENQ byte saja
        if raw_bytes[0:1] == ENQ_BYTE and len(raw_bytes) <= 2:
            self._logger.info(
                f"ENQ saja dari instrument {instrument.get('name', '?')}"
            )
            return result

        # Skenario 2: Coba parse Q record
        try:
            text = raw_bytes.decode("ascii", errors="replace")

            # Cari Q record
            for line in text.replace("\r\n", "\n").split("\n"):
                line = line.strip()
                if line.startswith("Q|"):
                    q_rec = self._parser.parse_q_record(line)
                    result["type"] = "query"
                    result["sample_id"] = q_rec.get("sample_id", "")
                    self._logger.info(
                        f"Query parsed: sample_id={result['sample_id']}"
                    )
                    return result

            # Jika ada frame, parse frame dulu
            frames = self._split_into_frames(raw_bytes)
            if frames:
                records = self._parser.parse_message(frames)
                for rec in records:
                    if rec.get("record_type") == RECORD_QUERY:
                        result["type"] = "query"
                        result["sample_id"] = rec.get("sample_id", "")
                        self._logger.info(
                            f"Query dari frame: sample_id={result['sample_id']}"
                        )
                        return result

        except Exception as e:
            self._logger.warning(f"Error parsing ENQ/query: {e}")

        return result

    # ============================================================
    # format_query_response() — Response untuk query mode
    # ============================================================

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        """
        Format response berisi order data untuk query mode.
        Dipanggil setelah order ditemukan di database berdasarkan sample_id.

        Args:
            order: Dict OrderObject yang ditemukan
            instrument: Dict instrumen

        Returns:
            List of bytes frames
        """
        self._logger.info(
            f"Building query response untuk order {order.get('order_id', '?')}"
        )
        return self._builder.build_query_response(order, instrument)

    def format_query_not_found(self, instrument: dict) -> bytes:
        """
        Format response jika order tidak ditemukan (query mode).
        Mengirim H + L(termination=I) sebagai indikasi "no order".

        Args:
            instrument: Dict instrumen

        Returns:
            List of bytes frames (H + L)
        """
        self._logger.info(
            f"Order tidak ditemukan untuk instrument {instrument.get('name', '?')}"
        )
        return self._builder.build_not_found_response()

    # ============================================================
    # handle_ack() — Deteksi ACK/NAK/EOT
    # ============================================================

    def handle_ack(self, raw_bytes: bytes) -> str:
        """
        Identifikasi tipe acknowledgement dari alat.

        Args:
            raw_bytes: Data yang diterima

        Returns:
            'ACK', 'NAK', 'EOT', atau 'UNKNOWN'
        """
        if not raw_bytes:
            return "UNKNOWN"

        first_byte = raw_bytes[0:1]

        if first_byte == ACK_BYTE:
            self._logger.info("ACK diterima")
            return "ACK"
        elif first_byte == NAK_BYTE:
            self._logger.warning("NAK diterima — alat menolak frame")
            return "NAK"
        elif first_byte == EOT_BYTE:
            self._logger.info("EOT diterima — sesi selesai")
            return "EOT"

        # Cek dalam seluruh data (beberapa alat kirim prefix sebelum kontrol byte)
        if ACK in raw_bytes:
            self._logger.info("ACK ditemukan dalam data")
            return "ACK"
        if NAK in raw_bytes:
            self._logger.warning("NAK ditemukan dalam data")
            return "NAK"
        if EOT in raw_bytes:
            self._logger.info("EOT ditemukan dalam data")
            return "EOT"

        self._logger.warning(f"Ack type tidak dikenali: {raw_bytes[:10].hex()}")
        return "UNKNOWN"


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test ASTMModule ===\n")
    mod = ASTMModule()

    # Test properties
    print(f"Protocol: {mod.PROTOCOL_NAME}")
    print(f"Version:  {mod.VERSION}")
    assert mod.PROTOCOL_NAME == "ASTM"
    assert mod.VERSION == "1.0.0"
    print("OK: Properties benar\n")

    # Test is_enq
    assert mod.is_enq(b"\x05") is True, "ENQ byte harus terdeteksi"
    assert mod.is_enq(b"\x06") is False, "ACK bukan ENQ"
    assert mod.is_enq(b"") is False, "Empty bukan ENQ"
    assert mod.is_enq(b"Q|1|^SAMP001") is True, "Q record harus terdeteksi"
    print("OK: is_enq() benar\n")

    # Test handle_ack
    assert mod.handle_ack(b"\x06") == "ACK"
    assert mod.handle_ack(b"\x15") == "NAK"
    assert mod.handle_ack(b"\x04") == "EOT"
    assert mod.handle_ack(b"\x01") == "UNKNOWN"
    assert mod.handle_ack(b"") == "UNKNOWN"
    print("OK: handle_ack() benar\n")

    # Test handle_enq — ENQ saja
    enq_result = mod.handle_enq(b"\x05", {"name": "TestInst"})
    assert enq_result["type"] == "enq"
    print(f"OK: handle_enq (ENQ only): type={enq_result['type']}")

    # Test handle_enq — Q record
    q_data = b"Q|1|^SAMP001|||||||||||O"
    enq_result = mod.handle_enq(q_data, {"name": "TestInst"})
    assert enq_result["type"] == "query"
    assert enq_result["sample_id"] == "SAMP001"
    print(f"OK: handle_enq (Q record): type={enq_result['type']}, "
          f"sample_id={enq_result['sample_id']}\n")

    # Test parse — full message simulasi
    msg = (
        b"H|\\^&|||Sysmex XN-1000|||||||P|1|20240101120000\r\n"
        b"P|1||PAT001||Doe^John||19900515|M\r\n"
        b"O|1|SAMP001||^^^WBC|R\r\n"
        b"R|1|^^^WBC|5.2|10^3/uL|4.0-10.0|N||F\r\n"
        b"R|2|^^^RBC|4.8|10^6/uL|3.5-5.5|N||F\r\n"
        b"L|1|N\r\n"
    )
    instrument = {"id": 1, "name": "Sysmex XN-1000"}
    parsed = mod.parse(msg, instrument)

    print(f"Parsed result:")
    print(f"  instrument_id: {parsed['instrument_id']}")
    print(f"  protocol: {parsed['protocol']}")
    print(f"  patient_id: {parsed['patient']['patient_id']}")
    print(f"  patient_name: {parsed['patient']['name']}")
    print(f"  sample_id: {parsed['specimen']['sample_id']}")
    print(f"  results: {len(parsed['results'])} items")
    for r in parsed["results"]:
        print(f"    {r['test_code']}: {r['value']} {r['unit']} [{r['flag']}]")
    print(f"  parse_errors: {parsed['parse_errors']}")

    assert parsed["instrument_id"] == 1
    assert parsed["protocol"] == "ASTM"
    assert parsed["patient"]["patient_id"] == "PAT001"
    assert len(parsed["results"]) == 2
    assert parsed["results"][0]["test_code"] == "WBC"
    assert parsed["results"][0]["value"] == "5.2"
    print("\nOK: parse() benar\n")

    # Test format_order
    order = {
        "order_id": "ORD-001",
        "patient": {"patient_id": "PAT001", "name": "John Doe", "dob": "19900515", "gender": "M"},
        "specimen": {"sample_id": "SAMP001", "sample_type": "Blood", "priority": "R"},
        "tests": [{"test_code": "WBC", "test_name": "White Blood Cell"}],
    }
    frames = mod.format_order(order, instrument)
    print(f"OK: format_order(): {len(frames)} frames")
    assert len(frames) == 4
    # Verifikasi checksum frame valid
    from protocols.astm.parser import ASTMParser
    validator = ASTMParser()
    for i, f in enumerate(frames):
        assert validator.validate_checksum(f), f"Frame {i} checksum invalid"
    print("OK: Semua frame checksum valid\n")

    # Test format_query_response
    q_frames = mod.format_query_response(order, instrument)
    assert len(q_frames) == 4
    print("OK: format_query_response(): 4 frames\n")

    # Test format_query_not_found
    nf_frames = mod.format_query_not_found(instrument)
    assert len(nf_frames) == 2
    print("OK: format_query_not_found(): 2 frames (H + L)\n")

    # Test parse framed message (dengan STX/ETX)
    from protocols.astm.builder import ASTMBuilder
    builder = ASTMBuilder()
    framed_msg = b""
    plain_records = [
        "H|\\^&|||Cobas 6000|||||||P|1|20240101120000",
        "P|1||PAT002||Smith^Jane||19850101|F",
        "O|1|SAMP002||^^^GLU|R",
        "R|1|^^^GLU|95|mg/dL|70-100|N||F",
        "L|1|N",
    ]
    for i, rec in enumerate(plain_records):
        framed_msg += builder.wrap_frame(rec, i + 1)

    instrument2 = {"id": 2, "name": "Cobas 6000"}
    parsed2 = mod.parse(framed_msg, instrument2)
    print(f"Framed parse:")
    print(f"  patient: {parsed2['patient']['patient_id']} - {parsed2['patient']['name']}")
    print(f"  results: {len(parsed2['results'])} items")
    for r in parsed2["results"]:
        print(f"    {r['test_code']}: {r['value']} {r['unit']}")
    assert parsed2["patient"]["patient_id"] == "PAT002"
    assert len(parsed2["results"]) == 1
    assert parsed2["results"][0]["test_code"] == "GLU"
    print("OK: Framed message parsing benar\n")

    print("=== Semua test ASTMModule PASSED ===")
