"""
protocols/astm/builder.py — ASTM Message Builder

Membangun pesan ASTM lengkap untuk dikirim ke alat lab.
Digunakan di mode bidirectional (broadcast dan query) untuk mengirim
order data dari LIS ke alat.

Alur: OrderObject → build records → wrap_frame() → bytes siap kirim
"""

from lib import timeutil

from lib.utils import get_logger
from protocols.astm.constants import (
    ENQ, ACK, NAK, EOT, STX, ETX, ETB, CR, LF,
    ENQ_BYTE, ACK_BYTE, NAK_BYTE, EOT_BYTE,
    STX_BYTE, ETX_BYTE, CR_BYTE, LF_BYTE, CRLF,
    FIELD_DELIMITER, COMPONENT_DELIMITER, DEFAULT_DELIMITERS,
    MAX_FRAME_SIZE,
)


logger = get_logger("astm_builder")


class ASTMBuilder:
    """
    Builder untuk pesan ASTM E1381/E1394.

    Membangun record H, P, O, L dan membungkusnya dalam frame
    dengan STX/ETX/checksum sesuai spesifikasi transport layer.
    """

    def __init__(self):
        self.field_delim = FIELD_DELIMITER
        self.component_delim = COMPONENT_DELIMITER

    # ============================================================
    # High-level builders
    # ============================================================

    def build_enq_response(self, order: dict, instrument: dict) -> bytes:
        """
        Build pesan order lengkap untuk broadcast mode.
        Kirim: ENQ → [wait ACK] → H + P + O + L → EOT

        Mengembalikan payload frame saja (ENQ dan EOT ditangani oleh
        TCPSocketService karena butuh handshake ACK di antara).

        Args:
            order: Dict OrderObject (tbl_order.order_json)
            instrument: Dict instrumen dari tbl_instrument

        Returns:
            List bytes: [frame_H, frame_P, frame_O, frame_L]
        """
        logger.info(
            f"Building broadcast message untuk order {order.get('order_id', '?')}"
        )

        frame_num = 1
        frames = []

        # H record — Header
        h_record = self._build_h_record(instrument)
        frames.append(self.wrap_frame(h_record, frame_num))
        frame_num = (frame_num % 7) + 1

        # P record — Patient
        p_record = self._build_p_record(order)
        frames.append(self.wrap_frame(p_record, frame_num))
        frame_num = (frame_num % 7) + 1

        # O record — Order (satu per test, atau gabungan)
        o_record = self._build_o_record(order)
        frames.append(self.wrap_frame(o_record, frame_num))
        frame_num = (frame_num % 7) + 1

        # L record — Terminator
        l_record = self._build_l_record()
        frames.append(self.wrap_frame(l_record, frame_num))

        logger.info(f"Built {len(frames)} frames untuk broadcast")
        return frames

    def build_query_response(self, order: dict, instrument: dict) -> bytes:
        """
        Build response untuk query mode (alat minta order via ENQ/Q record).
        Format sama dengan broadcast: H + P + O + L dalam frame.

        Args:
            order: Dict OrderObject yang ditemukan
            instrument: Dict instrumen

        Returns:
            List bytes: [frame_H, frame_P, frame_O, frame_L]
        """
        logger.info(
            f"Building query response untuk order {order.get('order_id', '?')}"
        )
        # Sama dengan broadcast — alat menerima format yang identik
        return self.build_enq_response(order, instrument)

    def build_not_found_response(self) -> bytes:
        """
        Build response NAK jika order tidak ditemukan di database.
        Kirim: H + L (tanpa P/O, termination_code=I) lalu EOT.

        Returns:
            List bytes: [frame_H, frame_L]
        """
        logger.info("Building not-found response (H + L only)")

        frames = []

        # H record minimal
        h_record = f"H{self.field_delim}\\^&"
        frames.append(self.wrap_frame(h_record, 1))

        # L record dengan termination code I (incomplete / not found)
        l_record = f"L{self.field_delim}1{self.field_delim}I"
        frames.append(self.wrap_frame(l_record, 2))

        return frames

    # ============================================================
    # Record builders
    # ============================================================

    def _build_h_record(self, instrument: dict) -> str:
        """Build H (Header) record string."""
        # Jam lokal lab: alat membandingkan H-record timestamp dengan jam
        # internalnya sendiri, yang juga lokal. UTC di sini bikin meleset 7 jam.
        timestamp = timeutil.stamp("%Y%m%d%H%M%S")
        sender = instrument.get("name", "MidLab")
        # H|\\^&|||sender||||||host|P|1|timestamp
        fields = [
            "H",               # [0] record type
            DEFAULT_DELIMITERS, # [1] delimiters
            "",                 # [2] message control id
            "",                 # [3] access password
            sender,             # [4] sender name/id
            "",                 # [5] sender address
            "",                 # [6] reserved
            "",                 # [7] sender phone
            "",                 # [8] sender characteristics
            "",                 # [9] receiver id
            "",                 # [10] comment
            "P",                # [11] processing id (P=production)
            "1",                # [12] version (LIS2-A2 = 1)
            timestamp,          # [13] timestamp
        ]
        return self.field_delim.join(fields)

    def _build_p_record(self, order: dict) -> str:
        """Build P (Patient) record string dari order data."""
        patient = order.get("patient", {})
        patient_id = patient.get("patient_id", "")
        name = patient.get("name", "")
        dob = patient.get("dob", "")
        gender = patient.get("gender", "")

        # P|1||patient_id||name||dob|gender
        fields = [
            "P",          # [0] record type
            "1",          # [1] sequence
            "",           # [2] practice patient id
            patient_id,   # [3] lab patient id
            "",           # [4] patient id 3
            name,         # [5] patient name
            "",           # [6] mother's maiden name
            dob,          # [7] date of birth
            gender,       # [8] patient sex
        ]
        return self.field_delim.join(fields)

    def _build_o_record(self, order: dict) -> str:
        """Build O (Order) record string dari order data."""
        specimen = order.get("specimen", {})
        sample_id = specimen.get("sample_id", "")
        sample_type = specimen.get("sample_type", "")
        priority = specimen.get("priority", "R")  # R=routine, S=stat

        # Build test list — gabung semua test_code dengan component delimiter
        tests = order.get("tests", [])
        if tests:
            # Format: ^^^test1\^^^test2\^^^test3
            test_parts = []
            for t in tests:
                code = t.get("test_code", "")
                test_parts.append(f"^^^{code}")
            test_field = "\\".join(test_parts)
        else:
            test_field = ""

        order_id = order.get("order_id", "")

        # O|1|sample_id|order_id|test_field|priority|created|collected
        fields = [
            "O",          # [0] record type
            "1",          # [1] sequence
            sample_id,    # [2] specimen id
            order_id,     # [3] instrument specimen id
            test_field,   # [4] universal test id
            priority,     # [5] priority
            "",           # [6] requested date
            "",           # [7] collected date
            "",           # [8] collection end
            "",           # [9] collection volume
            "",           # [10] collector id
            "N",          # [11] action code (N=new)
        ]
        return self.field_delim.join(fields)

    def _build_l_record(self) -> str:
        """Build L (Terminator) record string."""
        return f"L{self.field_delim}1{self.field_delim}N"

    # ============================================================
    # Frame wrapping & checksum
    # ============================================================

    def wrap_frame(self, record: str, frame_number: int) -> bytes:
        """
        Bungkus record string dalam frame ASTM transport.

        Format: <STX><frame_num><record><CR><ETX><checksum><CR><LF>

        Args:
            record: String isi record (misal "H|\\^&|||...")
            frame_number: Nomor frame (0-7)

        Returns:
            Bytes frame lengkap siap kirim
        """
        fn = str(frame_number % 8)
        # Data yang dihitung checksum: frame_number + record + CR + ETX
        body = fn.encode("ascii") + record.encode("ascii") + CR_BYTE + ETX_BYTE
        cs = self.calculate_checksum(body)
        # Frame lengkap: STX + body + checksum + CR + LF
        frame = STX_BYTE + body + cs.encode("ascii") + CRLF
        return frame

    def calculate_checksum(self, data: bytes) -> str:
        """
        Hitung checksum ASTM: sum semua bytes mod 256, format 2-char hex uppercase.

        Args:
            data: Bytes dari frame_number sampai ETX/ETB inclusive

        Returns:
            String 2-char hex, misal '3A'
        """
        checksum = sum(data) % 256
        return f"{checksum:02X}"


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test ASTMBuilder ===\n")
    builder = ASTMBuilder()

    # Test 1: calculate_checksum
    cs = builder.calculate_checksum(b"1H|\\^&\r\x03")
    print(f"OK: Checksum: {cs}")
    assert len(cs) == 2
    assert all(c in "0123456789ABCDEF" for c in cs)

    # Test 2: wrap_frame
    frame = builder.wrap_frame("H|\\^&|||TestHost|||||||P|1|20240101", 1)
    print(f"OK: Frame length: {len(frame)} bytes")
    assert frame[0:1] == STX_BYTE, "Harus dimulai STX"
    assert frame[-2:] == CRLF, "Harus diakhiri CRLF"
    # Byte setelah STX = frame number '1'
    assert chr(frame[1]) == "1", "Frame number harus 1"
    # Cek ETX ada di frame
    assert ETX in frame, "Harus ada ETX"

    # Test 3: wrap_frame — validasi checksum bisa di-parse
    from protocols.astm.parser import ASTMParser
    parser = ASTMParser()
    assert parser.validate_checksum(frame), "Checksum frame harus valid"
    print("OK: Frame checksum valid (cross-check dengan parser)")

    # Test 4: build_enq_response
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
    instrument = {"name": "Sysmex XN-1000", "id": 1}

    frames = builder.build_enq_response(order, instrument)
    print(f"OK: build_enq_response: {len(frames)} frames")
    assert len(frames) == 4, "Harus 4 frames: H, P, O, L"

    # Validasi setiap frame
    for i, f in enumerate(frames):
        assert f[0:1] == STX_BYTE, f"Frame {i} harus dimulai STX"
        parsed = parser.parse_frame(f)
        assert parsed["checksum_valid"], f"Frame {i} checksum invalid"
        print(f"    Frame {i}: type={parsed['record_type']}, valid={parsed['checksum_valid']}")

    # Test 5: build_query_response (sama format dengan broadcast)
    q_frames = builder.build_query_response(order, instrument)
    assert len(q_frames) == 4
    print("OK: build_query_response: 4 frames")

    # Test 6: build_not_found_response
    nf_frames = builder.build_not_found_response()
    print(f"OK: build_not_found_response: {len(nf_frames)} frames")
    assert len(nf_frames) == 2, "Harus 2 frames: H, L"
    # Parse L record untuk cek termination code
    l_parsed = parser.parse_frame(nf_frames[1])
    assert "I" in l_parsed["data"], "L record harus punya termination code I"

    # Test 7: frame number wrapping
    frame_7 = builder.wrap_frame("R|1|test", 7)
    assert chr(frame_7[1]) == "7"
    frame_8 = builder.wrap_frame("R|1|test", 8)
    assert chr(frame_8[1]) == "0", "Frame 8 harus wrap ke 0"
    print("OK: Frame number wrapping benar (7 → 0)")

    print("\n=== Semua test ASTMBuilder PASSED ===")
