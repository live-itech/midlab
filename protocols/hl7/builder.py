"""
protocols/hl7/builder.py — HL7 Message Builder

Membangun pesan HL7 v2.x lengkap dengan MLLP envelope untuk dikirim ke alat lab.
Digunakan di mode bidirectional (broadcast dan query) untuk mengirim order data
dari LIS ke alat, serta membangun ACK dan query response.

Alur: OrderObject → build segments → join → wrap_mllp() → bytes siap kirim
"""

from lib import timeutil

from lib.utils import get_logger, generate_message_id
from protocols.hl7.constants import (
    MLLP_START, MLLP_END, MLLP_CR,
    MLLP_START_BYTE, MLLP_END_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, COMPONENT_SEP, REPEAT_SEP, ENCODING_CHARACTERS,
    SEGMENT_TERMINATOR,
    SEG_MSH, SEG_PID, SEG_PV1, SEG_OBR, SEG_OBX, SEG_MSA, SEG_QAK, SEG_QPD,
    MSG_ACK, MSG_RSP, MSG_ORM,
    EVENT_ACK_R01, EVENT_RSP_K22, EVENT_ORM_O01,
    ACK_AA, ACK_AE, ACK_AR,
    PROC_PRODUCTION, HL7_VERSION,
)


logger = get_logger("hl7_builder")


class HL7Builder:
    """
    Builder untuk pesan HL7 v2.x dengan MLLP transport.

    Membangun:
    - ACK messages
    - ORM order messages (broadcast mode)
    - RSP^K22 query responses (query mode)
    - Not-found responses
    """

    def __init__(self):
        self.field_sep = FIELD_SEPARATOR
        self.component_sep = COMPONENT_SEP

    # ============================================================
    # MLLP Transport
    # ============================================================

    def wrap_mllp(self, hl7_message: str) -> bytes:
        """
        Bungkus HL7 message string dalam MLLP envelope.

        Format: <VT>(HL7 message)<FS><CR>

        Args:
            hl7_message: String HL7 message (segments dipisahkan CR)

        Returns:
            Bytes lengkap dengan MLLP envelope
        """
        msg_bytes = hl7_message.encode("ascii", errors="replace")
        return MLLP_START_BYTE + msg_bytes + MLLP_TRAILER

    # ============================================================
    # MSH Builder
    # ============================================================

    def build_msh(self, message_type: str, instrument: dict,
                  message_control_id: str = None) -> str:
        """
        Build MSH (Message Header) segment.

        Args:
            message_type: Message type string, misal "ACK^R01" atau "RSP^K22"
            instrument: Dict instrumen (name dipakai sebagai receiving_application)
            message_control_id: ID unik message. Jika None, auto-generate.

        Returns:
            String MSH segment
        """
        if message_control_id is None:
            message_control_id = generate_message_id()[:20]

        # MSH-7 tanpa offset = jam lokal menurut HL7. Kirim jam lab, bukan UTC.
        timestamp = timeutil.stamp("%Y%m%d%H%M%S")
        instrument_name = instrument.get("name", "Instrument")

        # MSH-9: message type → components (type^trigger)
        msg_type_field = message_type.replace("^", self.component_sep)

        fields = [
            f"MSH{self.field_sep}",       # MSH-1 = field separator (termasuk dalam prefix)
            ENCODING_CHARACTERS,            # MSH-2: ^~\&
            "MidLab",                       # MSH-3: Sending Application
            "MidLab",                       # MSH-4: Sending Facility
            instrument_name,                # MSH-5: Receiving Application
            "",                             # MSH-6: Receiving Facility
            timestamp,                      # MSH-7: Date/Time
            "",                             # MSH-8: Security
            msg_type_field,                 # MSH-9: Message Type
            message_control_id,             # MSH-10: Message Control ID
            PROC_PRODUCTION,                # MSH-11: Processing ID
            HL7_VERSION,                    # MSH-12: Version
        ]

        # MSH spesial: dimulai dengan "MSH|" lalu encoding chars, lalu fields dari MSH-3
        # Format: MSH|^~\&|sending_app|sending_facility|...
        segment = f"MSH{self.field_sep}{self.field_sep.join(fields[1:])}"
        return segment

    # ============================================================
    # ACK Builder
    # ============================================================

    def build_ack(self, msh_segment: dict, ack_code: str,
                  text_message: str = "") -> bytes:
        """
        Build ACK message sebagai response ke message yang diterima.

        Struktur: MSH + MSA

        Args:
            msh_segment: Dict parsed MSH dari message yang di-ACK
            ack_code: Kode ACK (AA, AE, AR)
            text_message: Pesan opsional (untuk error description)

        Returns:
            Bytes ACK message lengkap dengan MLLP envelope
        """
        original_control_id = msh_segment.get("message_control_id", "")
        original_type = msh_segment.get("message_type", "")

        # Tentukan ACK event berdasarkan original message
        ack_type = f"ACK^{original_type.split('^')[1]}" if "^" in original_type else "ACK"

        # Build instrument dict dari MSH info
        instrument = {"name": msh_segment.get("sending_application", "")}

        # MSH untuk ACK
        msh = self.build_msh(ack_type, instrument)

        # MSA segment
        msa_fields = [
            SEG_MSA,
            ack_code,
            original_control_id,
        ]
        if text_message:
            msa_fields.append(text_message)
        msa = self.field_sep.join(msa_fields)

        # Gabung segments
        message = SEGMENT_TERMINATOR.join([msh, msa]) + SEGMENT_TERMINATOR

        logger.info(f"Built ACK: code={ack_code}, ref={original_control_id}")
        return self.wrap_mllp(message)

    # ============================================================
    # Order Response (Broadcast Mode)
    # ============================================================

    def build_order_response(self, order: dict, instrument: dict) -> bytes:
        """
        Build ORM message berisi order data untuk broadcast mode.
        MidLab mengirim order ke alat secara periodik.

        Struktur: MSH + PID + OBR

        Args:
            order: Dict OrderObject dari tbl_order.order_json
            instrument: Dict instrumen

        Returns:
            Bytes ORM message lengkap dengan MLLP envelope
        """
        logger.info(f"Building ORM untuk order {order.get('order_id', '?')}")

        # MSH
        msh = self.build_msh(EVENT_ORM_O01, instrument)

        # PID
        pid = self._build_pid(order)

        # OBR — satu per test atau gabungan
        obr_segments = self._build_obr_list(order)

        # Gabung segments
        segments = [msh, pid] + obr_segments
        message = SEGMENT_TERMINATOR.join(segments) + SEGMENT_TERMINATOR

        logger.info(f"Built ORM: {len(segments)} segments")
        return self.wrap_mllp(message)

    # ============================================================
    # Query Response (Query Mode) — RSP^K22
    # ============================================================

    def build_query_response(self, order: dict, instrument: dict,
                             query_msh: dict) -> bytes:
        """
        Build RSP^K22 response untuk query mode.
        Alat mengirim QBP^Q22, MidLab membalas dengan RSP^K22 berisi order data.

        Struktur: MSH + MSA + QAK + QPD + PID + OBR

        Args:
            order: Dict OrderObject yang ditemukan
            instrument: Dict instrumen
            query_msh: Dict parsed MSH dari query message (untuk referensi)

        Returns:
            Bytes RSP^K22 message lengkap dengan MLLP envelope
        """
        original_control_id = query_msh.get("message_control_id", "")
        logger.info(
            f"Building RSP^K22 untuk query {original_control_id}, "
            f"order {order.get('order_id', '?')}"
        )

        # MSH
        msh = self.build_msh(EVENT_RSP_K22, instrument)

        # MSA — acknowledge query
        msa = self.field_sep.join([SEG_MSA, ACK_AA, original_control_id])

        # QAK — query status OK
        qak = self.field_sep.join([SEG_QAK, original_control_id, "OK"])

        # QPD — echo back query parameters (minimal)
        qpd = self.field_sep.join([
            SEG_QPD,
            f"Q22{self.component_sep}Find Candidates",
            original_control_id,
        ])

        # PID
        pid = self._build_pid(order)

        # OBR
        obr_segments = self._build_obr_list(order)

        # Gabung segments
        segments = [msh, msa, qak, qpd, pid] + obr_segments
        message = SEGMENT_TERMINATOR.join(segments) + SEGMENT_TERMINATOR

        logger.info(f"Built RSP^K22: {len(segments)} segments")
        return self.wrap_mllp(message)

    def build_not_found_response(self, query_msh: dict) -> bytes:
        """
        Build RSP^K22 response dengan status "not found" (AE).
        Dikirim jika order tidak ditemukan untuk query yang diterima.

        Struktur: MSH + MSA(AE) + QAK(NF)

        Args:
            query_msh: Dict parsed MSH dari query message

        Returns:
            Bytes RSP^K22 not-found message dengan MLLP envelope
        """
        original_control_id = query_msh.get("message_control_id", "")
        instrument = {"name": query_msh.get("sending_application", "")}

        logger.info(f"Building not-found RSP^K22 untuk query {original_control_id}")

        # MSH
        msh = self.build_msh(EVENT_RSP_K22, instrument)

        # MSA — error (not found)
        msa = self.field_sep.join([
            SEG_MSA, ACK_AE, original_control_id, "Order not found"
        ])

        # QAK — not found
        qak = self.field_sep.join([SEG_QAK, original_control_id, "NF"])

        # Gabung segments
        segments = [msh, msa, qak]
        message = SEGMENT_TERMINATOR.join(segments) + SEGMENT_TERMINATOR

        logger.info("Built not-found RSP^K22")
        return self.wrap_mllp(message)

    # ============================================================
    # Segment Builders (internal)
    # ============================================================

    def _build_pid(self, order: dict) -> str:
        """Build PID segment dari order data."""
        patient = order.get("patient", {})
        patient_id = patient.get("patient_id", "")
        name = patient.get("name", "")
        dob = patient.get("dob", "")
        gender = patient.get("gender", "")

        # Nama: coba split "First Last" → "Last^First"
        name_parts = name.split(" ", 1) if name else []
        if len(name_parts) == 2:
            hl7_name = f"{name_parts[1]}{self.component_sep}{name_parts[0]}"
        elif len(name_parts) == 1:
            hl7_name = name_parts[0]
        else:
            hl7_name = ""

        fields = [
            SEG_PID,
            "1",                # PID-1: Set ID
            "",                 # PID-2: External ID
            patient_id,         # PID-3: Patient ID
            "",                 # PID-4: Alternate ID
            hl7_name,           # PID-5: Patient Name
            "",                 # PID-6: Mother's Maiden Name
            dob,                # PID-7: DOB
            gender,             # PID-8: Sex
        ]
        return self.field_sep.join(fields)

    def _build_obr_list(self, order: dict) -> list:
        """Build OBR segment(s) dari order data."""
        specimen = order.get("specimen", {})
        sample_id = specimen.get("sample_id", "")
        order_id = order.get("order_id", "")
        tests = order.get("tests", [])

        obr_segments = []
        if tests:
            for i, test in enumerate(tests, start=1):
                test_code = test.get("test_code", "")
                test_name = test.get("test_name", "")
                universal_service = (
                    f"{test_code}{self.component_sep}{test_name}"
                    if test_name else test_code
                )

                fields = [
                    SEG_OBR,
                    str(i),              # OBR-1: Set ID
                    order_id,            # OBR-2: Placer Order Number
                    sample_id,           # OBR-3: Filler Order Number
                    universal_service,   # OBR-4: Universal Service ID
                ]
                obr_segments.append(self.field_sep.join(fields))
        else:
            # OBR minimal tanpa test info
            fields = [SEG_OBR, "1", order_id, sample_id, ""]
            obr_segments.append(self.field_sep.join(fields))

        return obr_segments


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test HL7Builder ===\n")
    builder = HL7Builder()

    # Test 1: wrap_mllp
    msg = "MSH|^~\\&|Test\rPID|1||PAT001\r"
    wrapped = builder.wrap_mllp(msg)
    print(f"OK: wrap_mllp length: {len(wrapped)} bytes")
    assert wrapped[0:1] == b"\x0b", "Harus dimulai VT (0x0B)"
    assert wrapped[-2:] == b"\x1c\x0d", "Harus diakhiri FS+CR"
    # Isi di tengah harus utuh
    inner = wrapped[1:-2]
    assert inner == msg.encode("ascii")
    print("OK: MLLP envelope benar")

    # Test 2: build_msh
    instrument = {"name": "Sysmex XN-1000", "id": 1}
    msh = builder.build_msh("ORU^R01", instrument, message_control_id="TEST001")
    print(f"\nMSH: {msh}")
    assert msh.startswith("MSH|^~\\&|")
    assert "MidLab" in msh
    assert "ORU^R01" in msh
    assert "TEST001" in msh
    assert HL7_VERSION in msh
    print("OK: build_msh() benar")

    # Test 3: build_ack
    msh_parsed = {
        "message_type": "ORU^R01",
        "message_control_id": "MSG001",
        "sending_application": "Sysmex",
    }
    ack = builder.build_ack(msh_parsed, ACK_AA)
    print(f"\nACK length: {len(ack)} bytes")
    ack_text = ack[1:-2].decode("ascii")
    print(f"ACK content:\n{ack_text}")
    assert b"\x0b" == ack[0:1], "ACK harus dimulai MLLP"
    assert b"MSA|AA|MSG001" in ack
    assert b"ACK^R01" in ack
    print("OK: build_ack(AA) benar")

    # Test ACK dengan AE
    ack_err = builder.build_ack(msh_parsed, ACK_AE, "Parse error")
    assert b"MSA|AE|MSG001|Parse error" in ack_err
    print("OK: build_ack(AE) benar")

    # Test 4: build_order_response (broadcast)
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
    orm = builder.build_order_response(order, instrument)
    orm_text = orm[1:-2].decode("ascii")
    print(f"\nORM message:\n{orm_text}")
    assert b"MSH|" in orm
    assert b"PID|" in orm
    assert b"OBR|" in orm
    assert b"ORM^O01" in orm
    assert b"PAT001" in orm
    assert b"WBC" in orm
    assert b"RBC" in orm
    # Harus ada 2 OBR (satu per test)
    assert orm_text.count("OBR|") == 2
    print("OK: build_order_response() benar")

    # Test 5: build_query_response (RSP^K22)
    query_msh = {
        "message_type": "QBP^Q22",
        "message_control_id": "QRY001",
        "sending_application": "Sysmex",
    }
    rsp = builder.build_query_response(order, instrument, query_msh)
    rsp_text = rsp[1:-2].decode("ascii")
    print(f"\nRSP^K22 message:\n{rsp_text}")
    assert b"RSP^K22" in rsp
    assert b"MSA|AA|QRY001" in rsp
    assert b"QAK|QRY001|OK" in rsp
    assert b"QPD|" in rsp
    assert b"PID|" in rsp
    assert b"OBR|" in rsp
    print("OK: build_query_response() benar")

    # Test 6: build_not_found_response
    nf = builder.build_not_found_response(query_msh)
    nf_text = nf[1:-2].decode("ascii")
    print(f"\nNot-found RSP:\n{nf_text}")
    assert b"RSP^K22" in nf
    assert b"MSA|AE|QRY001" in nf
    assert b"QAK|QRY001|NF" in nf
    assert b"PID|" not in nf  # Tidak ada data pasien
    print("OK: build_not_found_response() benar")

    # Test 7: Cross-check — parse hasil builder
    from protocols.hl7.parser import HL7Parser
    parser = HL7Parser()

    # Parse ORM
    orm_unwrapped = parser.unwrap_mllp(orm)
    orm_parsed = parser.parse_message(orm_unwrapped)
    seg_types = [s["segment_type"] for s in orm_parsed["segments"]]
    print(f"\nCross-check ORM: segments={seg_types}")
    assert "MSH" in seg_types
    assert "PID" in seg_types
    assert "OBR" in seg_types
    print("OK: ORM cross-check benar")

    # Parse RSP
    rsp_unwrapped = parser.unwrap_mllp(rsp)
    rsp_parsed = parser.parse_message(rsp_unwrapped)
    seg_types_rsp = [s["segment_type"] for s in rsp_parsed["segments"]]
    print(f"Cross-check RSP: segments={seg_types_rsp}")
    assert "MSA" in seg_types_rsp
    assert "QAK" in seg_types_rsp
    print("OK: RSP cross-check benar")

    print("\n=== Semua test HL7Builder PASSED ===")
