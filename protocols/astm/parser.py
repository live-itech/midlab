"""
protocols/astm/parser.py — ASTM Message Parser

Parsing raw bytes dari alat lab (ASTM E1381 transport + E1394 data format).
Menangani frame decoding, checksum validation, dan record parsing untuk
semua tipe record: H, P, O, R, L, Q, C.

Alur: raw_bytes → parse_frame() → parse_message() → parse_X_record()
"""

from lib.utils import get_logger
from protocols.astm.constants import (
    STX, ETX, ETB, CR, LF,
    FIELD_DELIMITER, REPEAT_DELIMITER, COMPONENT_DELIMITER, ESCAPE_DELIMITER,
    RECORD_HEADER, RECORD_PATIENT, RECORD_ORDER, RECORD_RESULT,
    RECORD_TERMINATOR, RECORD_QUERY, RECORD_COMMENT,
    VALID_RECORD_TYPES,
)


logger = get_logger("astm_parser")


class ASTMParser:
    """
    Parser untuk pesan ASTM E1381/E1394.

    Mendukung:
    - Single-frame dan multi-frame messages
    - Checksum validation
    - Parsing semua record type (H, P, O, R, L, Q, C)
    - Custom delimiters (diambil dari H record)
    """

    def __init__(self):
        # Default delimiters, bisa di-override dari H record
        self.field_delim = FIELD_DELIMITER
        self.repeat_delim = REPEAT_DELIMITER
        self.component_delim = COMPONENT_DELIMITER
        self.escape_delim = ESCAPE_DELIMITER

    # ============================================================
    # Frame-level parsing
    # ============================================================

    def parse_frame(self, raw_bytes: bytes) -> dict:
        """
        Parse satu frame ASTM mentah.

        Format frame: <STX><FN><data><ETX|ETB><C1><C2><CR><LF>
        - FN   = frame number (1 digit, '0'-'7')
        - data = isi record
        - C1C2 = 2-char hex checksum

        Args:
            raw_bytes: Bytes satu frame lengkap

        Returns:
            Dict: {frame_number, data, checksum_valid, record_type, raw}
        """
        result = {
            "frame_number": None,
            "data": "",
            "checksum_valid": False,
            "record_type": None,
            "raw": raw_bytes,
        }

        if not raw_bytes:
            logger.warning("Frame kosong diterima")
            return result

        # Cari posisi STX
        try:
            stx_pos = raw_bytes.index(STX)
        except ValueError:
            # Tidak ada STX — mungkin record tanpa framing (beberapa alat lama)
            text = raw_bytes.decode("ascii", errors="replace").strip()
            if text and text[0] in VALID_RECORD_TYPES:
                result["data"] = text
                result["record_type"] = text[0]
                result["checksum_valid"] = True  # Tanpa frame, skip checksum
                logger.info(f"Frame tanpa STX, record type: {text[0]}")
            return result

        # Cari ETX atau ETB setelah STX
        etx_pos = None
        is_intermediate = False
        for i in range(stx_pos + 1, len(raw_bytes)):
            if raw_bytes[i] == ETX:
                etx_pos = i
                break
            elif raw_bytes[i] == ETB:
                etx_pos = i
                is_intermediate = True
                break

        if etx_pos is None:
            logger.warning("Frame tanpa ETX/ETB")
            return result

        # Ekstrak frame number dan data
        # Format setelah STX: <frame_number><data><ETX/ETB>
        frame_content = raw_bytes[stx_pos + 1 : etx_pos]
        if len(frame_content) < 1:
            logger.warning("Frame content kosong")
            return result

        # Frame number = karakter pertama setelah STX
        frame_num_char = chr(frame_content[0])
        if frame_num_char.isdigit():
            result["frame_number"] = int(frame_num_char)
            data_bytes = frame_content[1:]
        else:
            # Beberapa alat tidak pakai frame number
            result["frame_number"] = 0
            data_bytes = frame_content

        # Decode data
        result["data"] = data_bytes.decode("ascii", errors="replace")

        # Deteksi record type dari karakter pertama data
        if result["data"]:
            first_char = result["data"][0]
            if first_char in VALID_RECORD_TYPES:
                result["record_type"] = first_char

        # Validate checksum
        result["checksum_valid"] = self.validate_checksum(raw_bytes)

        if not result["checksum_valid"]:
            logger.warning(
                f"Checksum invalid pada frame {result['frame_number']}, "
                f"record type: {result['record_type']}"
            )

        return result

    def parse_message(self, frames: list) -> list:
        """
        Parse kumpulan frame menjadi list of parsed records.

        Menggabungkan multi-frame records, lalu parse tiap record
        sesuai tipe-nya (H, P, O, R, L, Q, C).

        Args:
            frames: List of raw_bytes per frame

        Returns:
            List of dict, masing-masing berisi parsed record
        """
        records = []
        current_data = ""

        for frame_bytes in frames:
            frame = self.parse_frame(frame_bytes)

            if frame["data"]:
                current_data += frame["data"]

            # Jika frame terakhir (ETX, bukan ETB), finalisasi record
            is_intermediate = False
            if isinstance(frame_bytes, bytes):
                is_intermediate = ETB in frame_bytes and ETX not in frame_bytes

            if not is_intermediate and current_data:
                record = self._parse_record(current_data)
                if record:
                    records.append(record)
                current_data = ""

        # Jika ada sisa data yang belum diproses
        if current_data:
            record = self._parse_record(current_data)
            if record:
                records.append(record)

        logger.info(f"Parsed {len(records)} records dari {len(frames)} frames")
        return records

    def _parse_record(self, data: str) -> dict | None:
        """Parse satu record string berdasarkan tipe-nya."""
        if not data:
            return None

        record_type = data[0] if data[0] in VALID_RECORD_TYPES else None
        if record_type is None:
            logger.warning(f"Record type tidak dikenali: {data[:20]}")
            return None

        # Update delimiters dari H record jika ada
        if record_type == RECORD_HEADER:
            self._extract_delimiters(data)

        # Dispatch ke parser spesifik
        parsers = {
            RECORD_HEADER:     self.parse_h_record,
            RECORD_PATIENT:    self.parse_p_record,
            RECORD_ORDER:      self.parse_o_record,
            RECORD_RESULT:     self.parse_r_record,
            RECORD_TERMINATOR: self.parse_l_record,
            RECORD_QUERY:      self.parse_q_record,
            RECORD_COMMENT:    self.parse_c_record,
        }

        parser_func = parsers.get(record_type)
        if parser_func:
            return parser_func(data)

        # Record type valid tapi tidak ada parser khusus
        return {"record_type": record_type, "raw": data}

    def _extract_delimiters(self, h_record: str):
        """Ekstrak custom delimiters dari H record (field ke-2)."""
        # Format H record: H|\^&|...
        # Karakter setelah 'H' adalah field delimiter, lalu 3 karakter berikutnya
        if len(h_record) >= 5:
            self.field_delim = h_record[1]
            self.repeat_delim = h_record[2]
            self.component_delim = h_record[3]
            self.escape_delim = h_record[4]
            logger.info(
                f"Delimiters: field='{self.field_delim}' "
                f"repeat='{self.repeat_delim}' "
                f"component='{self.component_delim}' "
                f"escape='{self.escape_delim}'"
            )

    def _split_fields(self, record: str) -> list:
        """Split record menjadi fields berdasarkan field delimiter."""
        return record.split(self.field_delim)

    def _split_components(self, field_value: str) -> list:
        """Split satu field menjadi komponen berdasarkan component delimiter."""
        return field_value.split(self.component_delim)

    # ============================================================
    # Record-type specific parsers
    # ============================================================

    def parse_h_record(self, record: str) -> dict:
        """
        Parse Header record.

        Format: H|\\^&|||host_id||||||LIS2-A2|P|1|yyyymmddHHMMSS
        Fields: [0]type [1]delimiters [4]sender [9]receiver [11]processing_id [12]version [13]timestamp
        """
        fields = self._split_fields(record)

        parsed = {
            "record_type": RECORD_HEADER,
            "delimiters": fields[1] if len(fields) > 1 else "",
            "sender": fields[4] if len(fields) > 4 else "",
            "receiver": fields[9] if len(fields) > 9 else "",
            "processing_id": fields[11] if len(fields) > 11 else "",
            "version": fields[12] if len(fields) > 12 else "",
            "timestamp": fields[13] if len(fields) > 13 else "",
        }
        logger.info(f"H record: sender={parsed['sender']}, timestamp={parsed['timestamp']}")
        return parsed

    def parse_p_record(self, record: str) -> dict:
        """
        Parse Patient record.

        Format: P|seq|practice_id|lab_id|patient_id|...|name|...|dob|gender|...|physician
        Fields: [0]type [1]seq [2]practice_id [3]lab_id [4]patient_id [5]name
                [7]dob [8]gender [13]physician
        """
        fields = self._split_fields(record)

        # Patient ID bisa di field 3 (lab_id) atau field 4
        patient_id = ""
        if len(fields) > 3 and fields[3]:
            patient_id = fields[3]
        elif len(fields) > 4 and fields[4]:
            patient_id = fields[4]

        # Nama pasien — bisa berisi komponen (last^first^middle)
        name = ""
        if len(fields) > 5 and fields[5]:
            name_parts = self._split_components(fields[5])
            name = " ".join(p for p in name_parts if p)

        parsed = {
            "record_type": RECORD_PATIENT,
            "sequence": fields[1] if len(fields) > 1 else "",
            "patient_id": patient_id,
            "name": name,
            "dob": fields[7] if len(fields) > 7 else "",
            "gender": fields[8] if len(fields) > 8 else "",
            "physician": fields[13] if len(fields) > 13 else "",
        }
        logger.info(f"P record: patient_id={parsed['patient_id']}, name={parsed['name']}")
        return parsed

    def parse_o_record(self, record: str) -> dict:
        """
        Parse Order record.

        Format: O|seq|specimen_id||test_id|priority|...|collected_at|...
        Fields: [0]type [1]seq [2]specimen_id [3]instrument_id [4]test_id
                [5]priority [7]collected_at [11]action_code
        """
        fields = self._split_fields(record)

        # Specimen/sample ID
        sample_id = ""
        if len(fields) > 2 and fields[2]:
            sample_id = fields[2].strip()

        # Test ID — bisa berisi komponen (test_code^test_name^...)
        test_code = ""
        test_name = ""
        panel = ""
        if len(fields) > 4 and fields[4]:
            parts = self._split_components(fields[4])
            # Beberapa alat: ^^^test_code atau component ke-4
            # Coba ambil yang non-empty
            for p in parts:
                if p:
                    if not test_code:
                        test_code = p
                    elif not test_name:
                        test_name = p
                    break
            panel = fields[4]  # Simpan raw sebagai panel

        parsed = {
            "record_type": RECORD_ORDER,
            "sequence": fields[1] if len(fields) > 1 else "",
            "sample_id": sample_id,
            "instrument_specimen_id": fields[3] if len(fields) > 3 else "",
            "test_code": test_code,
            "test_name": test_name,
            "panel": panel,
            "priority": fields[5] if len(fields) > 5 else "",
            "collected_at": fields[7] if len(fields) > 7 else "",
            "action_code": fields[11] if len(fields) > 11 else "",
        }
        logger.info(f"O record: sample_id={parsed['sample_id']}, test={parsed['test_code']}")
        return parsed

    def parse_r_record(self, record: str) -> dict:
        """
        Parse Result record.

        Format: R|seq|test_id|value|unit|reference_range|flag|...|status|...
        Fields: [0]type [1]seq [2]test_id [3]value [4]unit
                [5]reference_range [6]flag [8]status [12]timestamp
        """
        fields = self._split_fields(record)

        # Test ID — komponen (test_code^test_name^...)
        test_code = ""
        test_name = ""
        if len(fields) > 2 and fields[2]:
            parts = self._split_components(fields[2])
            # Ambil komponen non-empty
            non_empty = [p for p in parts if p]
            if len(non_empty) >= 1:
                test_code = non_empty[0]
            if len(non_empty) >= 2:
                test_name = non_empty[1]

        parsed = {
            "record_type": RECORD_RESULT,
            "sequence": fields[1] if len(fields) > 1 else "",
            "test_code": test_code,
            "test_name": test_name,
            "test_id_raw": fields[2] if len(fields) > 2 else "",
            "value": fields[3] if len(fields) > 3 else "",
            "unit": fields[4] if len(fields) > 4 else "",
            "reference_range": fields[5] if len(fields) > 5 else "",
            "flag": fields[6] if len(fields) > 6 else "",
            "status": fields[8] if len(fields) > 8 else "",
            "timestamp": fields[12] if len(fields) > 12 else "",
        }
        logger.info(
            f"R record: test={parsed['test_code']}, "
            f"value={parsed['value']} {parsed['unit']}, flag={parsed['flag']}"
        )
        return parsed

    def parse_l_record(self, record: str) -> dict:
        """
        Parse Terminator record.

        Format: L|1|N (atau L|1|F)
        Fields: [0]type [1]seq [2]termination_code
        N=normal, I=incomplete, F=final
        """
        fields = self._split_fields(record)

        parsed = {
            "record_type": RECORD_TERMINATOR,
            "sequence": fields[1] if len(fields) > 1 else "1",
            "termination_code": fields[2] if len(fields) > 2 else "N",
        }
        logger.info(f"L record: termination={parsed['termination_code']}")
        return parsed

    def parse_q_record(self, record: str) -> dict:
        """
        Parse Query record (bidirectional — alat minta data order).

        Format: Q|seq|specimen_id||||||||||O
        Fields: [0]type [1]seq [2]specimen_id [12]request_type
        """
        fields = self._split_fields(record)

        # Sample ID dari query
        sample_id = ""
        if len(fields) > 2 and fields[2]:
            # Bisa berisi komponen: ^sample_id atau sample_id langsung
            parts = self._split_components(fields[2])
            non_empty = [p for p in parts if p]
            sample_id = non_empty[0] if non_empty else fields[2]

        parsed = {
            "record_type": RECORD_QUERY,
            "sequence": fields[1] if len(fields) > 1 else "",
            "sample_id": sample_id,
            "sample_id_raw": fields[2] if len(fields) > 2 else "",
            "request_type": fields[12] if len(fields) > 12 else "O",
        }
        logger.info(f"Q record: sample_id={parsed['sample_id']}")
        return parsed

    def parse_c_record(self, record: str) -> dict:
        """
        Parse Comment record.

        Format: C|seq|source|comment_text|comment_type
        Fields: [0]type [1]seq [2]source [3]text [4]type
        """
        fields = self._split_fields(record)

        parsed = {
            "record_type": RECORD_COMMENT,
            "sequence": fields[1] if len(fields) > 1 else "",
            "source": fields[2] if len(fields) > 2 else "",
            "text": fields[3] if len(fields) > 3 else "",
            "comment_type": fields[4] if len(fields) > 4 else "",
        }
        logger.info(f"C record: {parsed['text'][:50]}")
        return parsed

    # ============================================================
    # Checksum
    # ============================================================

    def validate_checksum(self, frame: bytes) -> bool:
        """
        Validasi checksum frame ASTM.

        Checksum dihitung dari frame_number sampai ETX/ETB (inclusive),
        lalu dibandingkan dengan 2-char hex setelah ETX/ETB.

        Args:
            frame: Raw bytes satu frame lengkap

        Returns:
            True jika checksum valid
        """
        try:
            stx_pos = frame.index(STX)
        except ValueError:
            return True  # Tanpa STX, skip validasi

        # Cari ETX atau ETB
        etx_pos = None
        for i in range(stx_pos + 1, len(frame)):
            if frame[i] in (ETX, ETB):
                etx_pos = i
                break

        if etx_pos is None:
            return False

        # Checksum = 2 char hex setelah ETX/ETB
        checksum_start = etx_pos + 1
        if checksum_start + 2 > len(frame):
            return False

        expected_hex = frame[checksum_start : checksum_start + 2].decode("ascii", errors="replace")

        # Hitung checksum: sum bytes dari setelah STX sampai ETX/ETB (inclusive)
        calculated = self.calculate_checksum(frame[stx_pos + 1 : etx_pos + 1])

        return calculated.upper() == expected_hex.upper()

    def calculate_checksum(self, data: bytes) -> str:
        """
        Hitung checksum ASTM: sum semua bytes mod 256, format 2-char hex uppercase.

        Args:
            data: Bytes yang dihitung checksum-nya
                  (dari frame_number sampai ETX/ETB inclusive)

        Returns:
            String 2-char hex, misal '3A'
        """
        checksum = sum(data) % 256
        return f"{checksum:02X}"


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test ASTMParser ===\n")
    parser = ASTMParser()

    # Test 1: calculate_checksum
    test_data = b"1H|\\^&|||Host|||||||P|1|20240101120000\r\x03"
    cs = parser.calculate_checksum(test_data)
    print(f"OK: Checksum calculated: {cs}")
    assert len(cs) == 2, "Checksum harus 2 karakter"
    assert all(c in "0123456789ABCDEF" for c in cs), "Checksum harus hex"

    # Test 2: parse_frame — frame dengan STX/ETX
    frame_data = b"1H|\\^&|||Sysmex|||||||P|1|20240101120000\r"
    cs_bytes = parser.calculate_checksum(frame_data + bytes([ETX]))
    full_frame = bytes([STX]) + frame_data + bytes([ETX]) + cs_bytes.encode() + bytes([CR, LF])
    result = parser.parse_frame(full_frame)
    print(f"OK: Frame parsed: record_type={result['record_type']}, "
          f"frame_number={result['frame_number']}, checksum_valid={result['checksum_valid']}")
    assert result["record_type"] == "H"
    assert result["frame_number"] == 1
    assert result["checksum_valid"] is True

    # Test 3: parse_h_record
    h = parser.parse_h_record("H|\\^&|||Sysmex XN-1000|||||||P|1|20240101120000")
    print(f"OK: H record: sender={h['sender']}, version={h['version']}")
    assert h["record_type"] == "H"
    assert h["sender"] == "Sysmex XN-1000"

    # Test 4: parse_p_record
    p = parser.parse_p_record("P|1||PAT001||Doe^John||19900515|M|||||Dr. Smith")
    print(f"OK: P record: patient_id={p['patient_id']}, name={p['name']}, gender={p['gender']}")
    assert p["patient_id"] == "PAT001"
    assert "John" in p["name"]

    # Test 5: parse_o_record
    o = parser.parse_o_record("O|1|SAMP001||^^^WBC|R||20240101||||||")
    print(f"OK: O record: sample_id={o['sample_id']}, test_code={o['test_code']}")
    assert o["sample_id"] == "SAMP001"
    assert o["test_code"] == "WBC"

    # Test 6: parse_r_record
    r = parser.parse_r_record("R|1|^^^WBC^White Blood Cell|5.2|10^3/uL|4.0-10.0|N||F||||20240101")
    print(f"OK: R record: test={r['test_code']}, value={r['value']}, "
          f"unit={r['unit']}, flag={r['flag']}")
    assert r["test_code"] == "WBC"
    assert r["value"] == "5.2"
    assert r["flag"] == "N"

    # Test 7: parse_l_record
    l = parser.parse_l_record("L|1|N")
    print(f"OK: L record: termination={l['termination_code']}")
    assert l["termination_code"] == "N"

    # Test 8: parse_q_record
    q = parser.parse_q_record("Q|1|^SAMP001|||||||||||O")
    print(f"OK: Q record: sample_id={q['sample_id']}")
    assert q["sample_id"] == "SAMP001"

    # Test 9: parse_message — full message
    frames = [
        b"H|\\^&|||Sysmex|||||||P|1|20240101\r",
        b"P|1||PAT001||Doe^John||19900515|M\r",
        b"O|1|SAMP001||^^^CBC|R\r",
        b"R|1|^^^WBC|5.2|10^3/uL|4.0-10.0|N||F\r",
        b"R|2|^^^RBC|4.8|10^6/uL|3.5-5.5|N||F\r",
        b"L|1|N\r",
    ]
    records = parser.parse_message(frames)
    print(f"OK: Full message parsed: {len(records)} records")
    types = [r["record_type"] for r in records]
    assert types == ["H", "P", "O", "R", "R", "L"], f"Unexpected types: {types}"

    # Test 10: parse_c_record
    c = parser.parse_c_record("C|1|I|Hemolyzed sample|G")
    print(f"OK: C record: text={c['text']}")
    assert c["text"] == "Hemolyzed sample"

    print("\n=== Semua test ASTMParser PASSED ===")
