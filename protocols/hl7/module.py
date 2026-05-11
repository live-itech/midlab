"""
protocols/hl7/module.py — HL7 Protocol Module untuk MidLab

Implementasi lengkap BaseProtocolModule untuk protokol HL7 v2.x dengan MLLP transport.
Mendukung:
- Unidirectional: terima hasil dari alat (parse ORU^R01 → ResultObject)
- Bidirectional broadcast: kirim order ke alat (ORM^O01)
- Bidirectional query: respond QBP^Q22 dengan RSP^K22

Class ini dipanggil oleh TCPSocketService dan di-load secara dynamic
via protocols.base.load_module("HL7").
"""

from lib.utils import get_logger, generate_message_id, format_datetime
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.hl7.constants import (
    SEG_MSH, SEG_PID, SEG_PV1, SEG_OBR, SEG_OBX, SEG_NTE, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    MSG_QBP, MSG_QRY,
    QUERY_MESSAGE_TYPES, QUERY_EVENTS,
)
from protocols.hl7.parser import HL7Parser
from protocols.hl7.builder import HL7Builder


class HL7Module(BaseProtocolModule):
    """
    Protocol module HL7 v2.x untuk MidLab.

    Menangani semua aspek komunikasi HL7:
    - Parsing ORU^R01 (hasil pemeriksaan) → ResultObject
    - Building ORM^O01 (order broadcast) ke alat
    - Handling QBP^Q22 query dari alat → RSP^K22 response
    - ACK message handling (AA/AE/AR)
    """

    def __init__(self):
        self._parser = HL7Parser()
        self._builder = HL7Builder()
        self._logger = get_logger("hl7_module")

    # ============================================================
    # Properties
    # ============================================================

    @property
    def PROTOCOL_NAME(self) -> str:
        return "HL7"

    @property
    def VERSION(self) -> str:
        return "1.0.0"

    # ============================================================
    # parse() — Raw bytes → ResultObject dict
    # ============================================================

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Parse raw bytes HL7 dari alat menjadi ResultObject dict.

        Alur:
        1. Unwrap MLLP envelope
        2. Parse message → extract segments
        3. Gabung data dari MSH/PID/OBR/OBX → ResultObject

        Args:
            raw_bytes: Data mentah dari TCP socket (MLLP wrapped)
            instrument: Dict info instrumen dari tbl_instrument

        Returns:
            Dict ResultObject sesuai format CLAUDE.md
        """
        instrument_id = instrument.get("id", 0)
        self._logger.info(
            f"Mulai parsing {len(raw_bytes)} bytes HL7 dari instrument {instrument_id}"
        )

        parse_errors = []

        # Unwrap MLLP
        hl7_bytes = self._parser.unwrap_mllp(raw_bytes)
        if not hl7_bytes:
            parse_errors.append("Data kosong setelah MLLP unwrap")
            return ResultObject(
                instrument_id=instrument_id,
                protocol="HL7",
                parse_errors=parse_errors,
            ).to_dict()

        # Parse message
        parsed_msg = self._parser.parse_message(hl7_bytes)
        segments = parsed_msg.get("segments", [])

        if not segments:
            parse_errors.append("Tidak ada segment valid setelah parsing")
            return ResultObject(
                instrument_id=instrument_id,
                protocol="HL7",
                parse_errors=parse_errors,
            ).to_dict()

        # Assemble ResultObject dari segments
        result = self._assemble_result(segments, instrument_id)

        if parse_errors:
            result.parse_errors.extend(parse_errors)

        self._logger.info(
            f"Parsing selesai: {len(result.results)} hasil, "
            f"{len(result.parse_errors)} error(s)"
        )
        return result.to_dict()

    def _assemble_result(self, segments: list, instrument_id: int) -> ResultObject:
        """Gabung list of parsed segments menjadi satu ResultObject."""
        result = ResultObject(
            instrument_id=instrument_id,
            protocol="HL7",
        )

        for seg in segments:
            seg_type = seg.get("segment_type", "")

            if seg_type == SEG_MSH:
                # Tidak perlu data khusus dari MSH untuk ResultObject
                pass

            elif seg_type == SEG_PID:
                result.patient = PatientInfo(
                    patient_id=seg.get("patient_id", ""),
                    name=seg.get("name", ""),
                    dob=seg.get("dob", ""),
                    gender=seg.get("gender", ""),
                    physician="",  # Physician ada di PV1
                )

            elif seg_type == SEG_PV1:
                # Update physician dari PV1
                result.patient.physician = seg.get("physician", "")

            elif seg_type == SEG_OBR:
                result.specimen = SpecimenInfo(
                    sample_id=seg.get("sample_id", ""),
                    sample_type=seg.get("specimen_source", ""),
                    collected_at=seg.get("observation_datetime", ""),
                )
                result.order = OrderInfo(
                    order_id=seg.get("order_id", ""),
                    panel=seg.get("panel", ""),
                )

            elif seg_type == SEG_OBX:
                result.results.append(TestResult(
                    test_code=seg.get("test_code", ""),
                    test_name=seg.get("test_name", ""),
                    value=seg.get("value", ""),
                    unit=seg.get("unit", ""),
                    reference_range=seg.get("reference_range", ""),
                    flag=seg.get("flag", ""),
                    status=seg.get("status", ""),
                ))

        return result

    # ============================================================
    # format_order() — Order → bytes untuk broadcast mode
    # ============================================================

    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        Format order menjadi HL7 ORM^O01 message untuk broadcast ke alat.

        Args:
            order: Dict OrderObject dari tbl_order.order_json
            instrument: Dict instrumen

        Returns:
            Bytes ORM message lengkap dengan MLLP envelope
        """
        self._logger.info(
            f"Formatting order {order.get('order_id', '?')} "
            f"untuk instrument {instrument.get('name', '?')}"
        )
        return self._builder.build_order_response(order, instrument)

    # ============================================================
    # ENQ / Query handling — bidirectional support
    # ============================================================

    def is_enq(self, raw_bytes: bytes) -> bool:
        """
        Deteksi apakah data merupakan query message dari alat.

        HL7: Query terdeteksi via MSH message type QBP^Q22 atau QRY^Q01.

        Args:
            raw_bytes: Data yang diterima dari socket

        Returns:
            True jika merupakan query message
        """
        if not raw_bytes:
            return False

        is_query = self._parser.is_query_message(raw_bytes)
        if is_query:
            msg_type = self._parser.get_message_type(raw_bytes)
            self._logger.info(f"Query message terdeteksi: {msg_type}")

        return is_query

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Handle query message dari alat, ekstrak informasi query.

        Parse QBP^Q22 message, ekstrak patient_id/sample_id dari QPD segment.

        Args:
            raw_bytes: Data query message dari alat
            instrument: Dict info instrumen

        Returns:
            Dict: {type, sample_id, patient_id, raw_query}
        """
        result = {
            "type": "query",
            "sample_id": "",
            "patient_id": "",
            "raw_query": raw_bytes.hex(),
        }

        try:
            # Unwrap dan parse message
            hl7_bytes = self._parser.unwrap_mllp(raw_bytes)
            parsed_msg = self._parser.parse_message(hl7_bytes)

            # Simpan parsed MSH untuk digunakan nanti (format_query_response)
            for seg in parsed_msg.get("segments", []):
                seg_type = seg.get("segment_type", "")

                if seg_type == SEG_MSH:
                    result["_msh"] = seg  # Internal: dipakai oleh caller

                elif seg_type == "QPD":
                    # QPD berisi parameter query (sample_id / patient_id)
                    param = seg.get("parameter_value", "")
                    if param:
                        # Bisa jadi patient_id atau sample_id tergantung context
                        result["sample_id"] = param
                        result["patient_id"] = param

                elif seg_type == SEG_PID:
                    result["patient_id"] = seg.get("patient_id", "")

            self._logger.info(
                f"Query parsed: sample_id={result['sample_id']}, "
                f"patient_id={result['patient_id']}"
            )

        except Exception as e:
            self._logger.warning(f"Error parsing query message: {e}")

        return result

    # ============================================================
    # format_query_response() — Response untuk query mode
    # ============================================================

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        """
        Format response berisi order data untuk query mode (RSP^K22).

        Catatan: Method ini menggunakan MSH default karena BaseProtocolModule
        contract tidak menyertakan query_msh. Untuk full RSP^K22 dengan
        reference ke query asli, gunakan format_query_response_full().

        Args:
            order: Dict OrderObject yang ditemukan
            instrument: Dict instrumen

        Returns:
            Bytes RSP^K22 message dengan MLLP envelope
        """
        self._logger.info(
            f"Building query response untuk order {order.get('order_id', '?')}"
        )
        # Buat default query_msh (tanpa referensi ke query asli)
        default_query_msh = {
            "message_control_id": generate_message_id()[:20],
            "sending_application": instrument.get("name", ""),
        }
        return self._builder.build_query_response(order, instrument, default_query_msh)

    def format_query_response_full(self, order: dict, instrument: dict,
                                   query_msh: dict) -> bytes:
        """
        Format RSP^K22 response lengkap dengan referensi ke query asli.

        Args:
            order: Dict OrderObject yang ditemukan
            instrument: Dict instrumen
            query_msh: Dict parsed MSH dari query message

        Returns:
            Bytes RSP^K22 message dengan MLLP envelope
        """
        return self._builder.build_query_response(order, instrument, query_msh)

    def format_query_not_found(self, instrument: dict) -> bytes:
        """
        Format response jika order tidak ditemukan (query mode).
        Mengirim RSP^K22 dengan MSA code AE dan QAK status NF.

        Args:
            instrument: Dict instrumen

        Returns:
            Bytes RSP^K22 not-found message dengan MLLP envelope
        """
        self._logger.info(
            f"Order tidak ditemukan untuk instrument {instrument.get('name', '?')}"
        )
        default_query_msh = {
            "message_control_id": generate_message_id()[:20],
            "sending_application": instrument.get("name", ""),
        }
        return self._builder.build_not_found_response(default_query_msh)

    def format_query_not_found_full(self, instrument: dict,
                                    query_msh: dict) -> bytes:
        """
        Format not-found response lengkap dengan referensi ke query asli.

        Args:
            instrument: Dict instrumen
            query_msh: Dict parsed MSH dari query message

        Returns:
            Bytes RSP^K22 not-found message dengan MLLP envelope
        """
        return self._builder.build_not_found_response(query_msh)

    # ============================================================
    # handle_ack() — Deteksi ACK dari alat
    # ============================================================

    def handle_ack(self, raw_bytes: bytes) -> str:
        """
        Identifikasi tipe acknowledgement dari alat.
        Parse MSA segment untuk mendapatkan ACK code.

        Args:
            raw_bytes: Data ACK message yang diterima

        Returns:
            'ACK', 'NAK', atau 'UNKNOWN'
        """
        if not raw_bytes:
            return "UNKNOWN"

        try:
            hl7_bytes = self._parser.unwrap_mllp(raw_bytes)
            parsed_msg = self._parser.parse_message(hl7_bytes)

            for seg in parsed_msg.get("segments", []):
                if seg.get("segment_type") == SEG_MSA:
                    ack_code = seg.get("ack_code", "")
                    if ack_code == ACK_AA:
                        self._logger.info("ACK (AA) diterima")
                        return "ACK"
                    elif ack_code == ACK_AE:
                        self._logger.warning("NAK (AE) diterima — application error")
                        return "NAK"
                    elif ack_code == ACK_AR:
                        self._logger.warning("NAK (AR) diterima — application reject")
                        return "NAK"

        except Exception as e:
            self._logger.warning(f"Error parsing ACK message: {e}")

        self._logger.warning(f"Ack type tidak dikenali: {raw_bytes[:20].hex()}")
        return "UNKNOWN"


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test HL7Module ===\n")
    mod = HL7Module()

    # Test properties
    print(f"Protocol: {mod.PROTOCOL_NAME}")
    print(f"Version:  {mod.VERSION}")
    assert mod.PROTOCOL_NAME == "HL7"
    assert mod.VERSION == "1.0.0"
    print("OK: Properties benar\n")

    # --- Test parse() — full ORU^R01 message ---
    oru_raw = (
        b"\x0b"
        b"MSH|^~\\&|Sysmex|Lab|LIS|Hospital|20240101120000||ORU^R01|MSG001|P|2.5.1\r"
        b"PID|1||PAT001^^^MR||Doe^John||19900515|M\r"
        b"PV1|1|O|||||DOC001^Smith^Dr\r"
        b"OBR|1|ORD001|SAMP001|CBC^Complete Blood Count|||20240101120000\r"
        b"OBX|1|NM|WBC^White Blood Cell||5.2|10*3/uL|4.0-10.0|N|||F\r"
        b"OBX|2|NM|RBC^Red Blood Cell||4.8|10*6/uL|3.5-5.5|N|||F\r"
        b"OBX|3|NM|HGB^Hemoglobin||14.2|g/dL|12.0-16.0|N|||F\r"
        b"NTE|1|L|Normal results\r"
        b"\x1c\x0d"
    )
    instrument = {"id": 1, "name": "Sysmex XN-1000"}
    parsed = mod.parse(oru_raw, instrument)

    print("Parsed ORU^R01:")
    print(f"  instrument_id: {parsed['instrument_id']}")
    print(f"  protocol: {parsed['protocol']}")
    print(f"  patient_id: {parsed['patient']['patient_id']}")
    print(f"  patient_name: {parsed['patient']['name']}")
    print(f"  physician: {parsed['patient']['physician']}")
    print(f"  sample_id: {parsed['specimen']['sample_id']}")
    print(f"  order_id: {parsed['order']['order_id']}")
    print(f"  results: {len(parsed['results'])} items")
    for r in parsed["results"]:
        print(f"    {r['test_code']}: {r['value']} {r['unit']} [{r['flag']}] ({r['status']})")
    print(f"  parse_errors: {parsed['parse_errors']}")

    assert parsed["instrument_id"] == 1
    assert parsed["protocol"] == "HL7"
    assert parsed["patient"]["patient_id"] == "PAT001"
    assert parsed["patient"]["name"] == "John Doe"
    assert "Smith" in parsed["patient"]["physician"]
    assert parsed["specimen"]["sample_id"] == "SAMP001"
    assert parsed["order"]["order_id"] == "ORD001"
    assert len(parsed["results"]) == 3
    assert parsed["results"][0]["test_code"] == "WBC"
    assert parsed["results"][0]["value"] == "5.2"
    assert parsed["results"][0]["flag"] == "N"
    assert parsed["results"][0]["status"] == "F"
    assert parsed["results"][2]["test_code"] == "HGB"
    print("\nOK: parse() benar\n")

    # --- Test parse() — data kosong ---
    empty_result = mod.parse(b"", instrument)
    assert len(empty_result["parse_errors"]) > 0
    print("OK: parse() handle data kosong\n")

    # --- Test is_enq() ---
    qbp_raw = (
        b"\x0b"
        b"MSH|^~\\&|Inst|Lab|LIS||20240101||QBP^Q22|QRY001|P|2.5.1\r"
        b"QPD|Q22^Find Candidates|QRY001|PAT001\r"
        b"\x1c\x0d"
    )
    assert mod.is_enq(qbp_raw) is True, "QBP^Q22 harus terdeteksi sebagai query"
    assert mod.is_enq(oru_raw) is False, "ORU^R01 bukan query"
    assert mod.is_enq(b"") is False, "Empty bukan query"
    print("OK: is_enq() benar\n")

    # --- Test handle_enq() ---
    enq_result = mod.handle_enq(qbp_raw, instrument)
    print(f"handle_enq result:")
    print(f"  type: {enq_result['type']}")
    print(f"  sample_id: {enq_result['sample_id']}")
    print(f"  patient_id: {enq_result['patient_id']}")
    assert enq_result["type"] == "query"
    assert enq_result["sample_id"] == "PAT001"
    assert "_msh" in enq_result  # Internal MSH tersimpan
    print("OK: handle_enq() benar\n")

    # --- Test handle_ack() ---
    # Build ACK messages untuk test
    from protocols.hl7.builder import HL7Builder
    builder = HL7Builder()

    msh_for_ack = {
        "message_type": "ORU^R01",
        "message_control_id": "MSG001",
        "sending_application": "Sysmex",
    }
    ack_aa = builder.build_ack(msh_for_ack, "AA")
    ack_ae = builder.build_ack(msh_for_ack, "AE", "Error occurred")
    ack_ar = builder.build_ack(msh_for_ack, "AR", "Rejected")

    assert mod.handle_ack(ack_aa) == "ACK", "AA harus ACK"
    assert mod.handle_ack(ack_ae) == "NAK", "AE harus NAK"
    assert mod.handle_ack(ack_ar) == "NAK", "AR harus NAK"
    assert mod.handle_ack(b"") == "UNKNOWN", "Empty harus UNKNOWN"
    assert mod.handle_ack(b"random data") == "UNKNOWN", "Random harus UNKNOWN"
    print("OK: handle_ack() benar\n")

    # --- Test format_order() — broadcast mode ---
    order = {
        "order_id": "ORD-001",
        "patient": {
            "patient_id": "PAT001",
            "name": "John Doe",
            "dob": "19900515",
            "gender": "M",
        },
        "specimen": {
            "sample_id": "SAMP001",
            "sample_type": "Blood",
            "priority": "R",
        },
        "tests": [
            {"test_code": "WBC", "test_name": "White Blood Cell"},
            {"test_code": "RBC", "test_name": "Red Blood Cell"},
        ],
    }
    orm_bytes = mod.format_order(order, instrument)
    assert b"\x0b" == orm_bytes[0:1], "Harus dimulai MLLP"
    assert b"ORM^O01" in orm_bytes
    assert b"PAT001" in orm_bytes
    assert b"WBC" in orm_bytes
    print("OK: format_order() benar\n")

    # --- Test format_query_response() ---
    qr_bytes = mod.format_query_response(order, instrument)
    assert b"RSP^K22" in qr_bytes
    assert b"PAT001" in qr_bytes
    assert b"OBR|" in qr_bytes
    print("OK: format_query_response() benar\n")

    # --- Test format_query_response_full() — dengan query_msh ---
    query_msh = enq_result.get("_msh", {
        "message_control_id": "QRY001",
        "sending_application": "Inst",
    })
    qr_full = mod.format_query_response_full(order, instrument, query_msh)
    assert b"RSP^K22" in qr_full
    assert b"QRY001" in qr_full  # Referensi ke query asli
    print("OK: format_query_response_full() benar\n")

    # --- Test format_query_not_found() ---
    nf_bytes = mod.format_query_not_found(instrument)
    assert b"RSP^K22" in nf_bytes
    assert b"MSA|AE" in nf_bytes
    assert b"QAK|" in nf_bytes
    print("OK: format_query_not_found() benar\n")

    # --- Test format_query_not_found_full() ---
    nf_full = mod.format_query_not_found_full(instrument, query_msh)
    assert b"RSP^K22" in nf_full
    assert b"MSA|AE|QRY001" in nf_full
    assert b"QAK|QRY001|NF" in nf_full
    print("OK: format_query_not_found_full() benar\n")

    # --- Test round-trip: build → parse ---
    print("--- Round-trip Test ---")
    from protocols.hl7.parser import HL7Parser
    rt_parser = HL7Parser()

    # Parse ORM yang dibuat format_order
    orm_unwrapped = rt_parser.unwrap_mllp(orm_bytes)
    orm_parsed = rt_parser.parse_message(orm_unwrapped)
    assert orm_parsed["message_type"] == "ORM^O01"

    # Cari PID
    pid_found = False
    for seg in orm_parsed["segments"]:
        if seg.get("segment_type") == "PID":
            assert seg["patient_id"] == "PAT001"
            pid_found = True
    assert pid_found, "PID segment harus ada"
    print("OK: Round-trip build→parse benar\n")

    print("=== Semua test HL7Module PASSED ===")
