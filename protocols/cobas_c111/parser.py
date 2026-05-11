"""
protocols/cobas_c111/parser.py — Parser dua-lapis untuk Cobas c-111.

FrameDecoder  : raw bytes → list of record strings (lower layer; framing,
                checksum, escape decoding). Tidak mengerti semantik record.
RecordParser  : list of record strings → list of dict per record. Tidak
                mengerti framing.
"""

from lib.utils import get_logger
from protocols.cobas_c111.constants import (
    STX, ETX, ETB, CR,
    FIELD_DELIM, COMPONENT_DELIM, REPEAT_DELIM,
    ESCAPE_MAP,
    REC_HEADER, REC_PATIENT, REC_ORDER, REC_RESULT,
    REC_COMMENT, REC_TERMINATOR, REC_MANUFACTURER,
    M_SUBTYPE_CR, M_SUBTYPE_RR,
)


logger = get_logger("cobas_c111")


class FrameDecoder:
    """
    Lower-layer parser: bytes accumulated by receiver → record strings.

    Tidak melempar exception ke caller. Setiap kegagalan jadi entry di
    list `errors` yang dikembalikan bersamaan dengan record-record yang
    masih bisa diparse.
    """

    def split_frames(self, raw: bytes) -> list[bytes]:
        """
        Pecah byte stream menjadi list of frame bytes.

        Frame format (manual 7.1.5):
            [STX] FN <text> [ETX|ETB] CH CL [CR] [LF]
        Saat receiver memanggil parse(), CR/LF sudah mungkin sudah
        terpotong jadi kita toleran (max 2 byte setelah checksum).

        Args:
            raw: byte stream konkatenasi dari receiver.

        Returns:
            List of frame bytes, masing-masing STX..checksum (CR/LF di-strip).
        """
        frames = []
        i = 0
        while i < len(raw):
            if raw[i] != STX:
                i += 1
                continue

            # Cari ETX atau ETB setelah STX
            stx_pos = i
            end_marker_pos = None
            for j in range(stx_pos + 1, len(raw)):
                if raw[j] in (ETX, ETB):
                    end_marker_pos = j
                    break
            if end_marker_pos is None:
                # Frame tidak lengkap di tail; abaikan sisa
                break

            # +2 checksum bytes mandatori
            cs_end = end_marker_pos + 3
            if cs_end > len(raw):
                break

            frames.append(bytes(raw[stx_pos:cs_end]))
            # Lompati optional CR/LF setelah checksum
            i = cs_end
            while i < len(raw) and raw[i] in (CR, 0x0A):
                i += 1
        return frames

    def compute_checksum(self, frame: bytes) -> str:
        """
        Hitung checksum 2-hex-char dari satu frame.

        Manual 7.1.5: jumlah ASCII byte dari FN sampai dengan ETX/ETB
        (inklusif), modulus 256, lalu format 2-digit hex uppercase.

        (Manual menulis "modulus 8" — itu salah ketik vendor; lihat
        contoh perhitungan halaman 37 dimana hasil 0x1D4h modulus
        adalah 0xD4 — i.e. modulus 256. Telah diverifikasi terhadap
        contoh [STX]1Test[ETX] → D4.)

        Args:
            frame: frame bytes mulai dari STX, berakhir di ETX/ETB
                   (TANPA dua-byte checksum).

        Returns:
            String 2-hex-char uppercase (mis. "D4"), atau "" bila frame invalid.
        """
        if len(frame) < 3 or frame[0] != STX:
            return ""
        # Cari posisi ETX atau ETB
        end_pos = None
        for i in range(1, len(frame)):
            if frame[i] in (ETX, ETB):
                end_pos = i
                break
        if end_pos is None:
            return ""
        # Jumlahkan dari index 1 (FN) sampai end_pos (inclusive ETX/ETB)
        total = sum(frame[1:end_pos + 1]) & 0xFF
        return f"{total:02X}"

    def validate_checksum(self, frame_with_cs: bytes) -> bool:
        """
        Validasi checksum pada frame yang sudah include 2-byte checksum.

        Args:
            frame_with_cs: bytes seperti yang dihasilkan split_frames() —
                           dari STX sampai dengan 2 byte checksum.

        Returns:
            True bila checksum cocok.
        """
        if len(frame_with_cs) < 3:
            return False
        # Dua karakter terakhir = checksum
        body = frame_with_cs[:-2]
        expected = self.compute_checksum(body)
        actual = frame_with_cs[-2:].decode("ascii", errors="replace").upper()
        return expected == actual

    @staticmethod
    def decode_escapes(text: str) -> str:
        """
        Decode ASTM escape sequences (manual 7.1.4.3.5).
            &F& → |    &S& → ^    &R& → \\    &E& → &
        Escape sequences tidak dikenali → dijatuhkan (manual:
        "skipped and treated as NULL").
        """
        # Iteratif by find — sederhana dan cukup cepat untuk message ASTM
        out = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "&" and i + 2 < len(text) and text[i + 2] == "&":
                seq = text[i:i + 3]
                if seq in ESCAPE_MAP:
                    out.append(ESCAPE_MAP[seq])
                else:
                    # Unknown escape — drop (sesuai manual)
                    logger.info(f"Unknown escape '{seq}' di-drop")
                i += 3
            else:
                out.append(ch)
                i += 1
        return "".join(out)

    def decode(self, raw: bytes) -> tuple[list[str], list[str]]:
        """
        Pipeline lengkap: bytes mentah → (record_strings, errors).

        1. Split per frame.
        2. Validate checksum tiap frame (gagal → tambah error, tetap pakai).
        3. Concat text dari semua frame.
        4. Split per CR (manual 7.1.4.3.6) → list of record strings.
        5. Escape sequences belum di-decode di sini; biarkan RecordParser
           yang decode setelah split per field (manual: decode after field
           split agar tidak mengganggu pemisah).

        Args:
            raw: byte stream yang dikumpulkan receiver antara ENQ dan EOT.

        Returns:
            (record_strings, errors): list of record-string siap parse,
            list of pesan error (untuk dimasukkan ke ResultObject.parse_errors).
        """
        errors: list[str] = []
        frames = self.split_frames(raw)
        if not frames:
            errors.append("Tidak ada frame valid ditemukan di byte stream")
            return [], errors

        text_parts: list[str] = []
        prev_fn: int | None = None

        for idx, frame in enumerate(frames):
            if not self.validate_checksum(frame):
                errors.append(f"Frame #{idx + 1} checksum mismatch")
                logger.warning(f"Frame {idx + 1} checksum invalid")

            # FN sequence sanity (manual 7.1.5: 1..7, lalu 0..7, ...)
            if len(frame) >= 2:
                try:
                    fn = int(chr(frame[1]))
                    if prev_fn is not None:
                        expected = (prev_fn % 8) + 1 if prev_fn < 7 else 0
                        if fn != expected and not (prev_fn == 7 and fn == 0):
                            errors.append(
                                f"Frame #{idx + 1} FN loncat: {prev_fn} → {fn}"
                            )
                    prev_fn = fn
                except (ValueError, IndexError):
                    errors.append(f"Frame #{idx + 1} FN bukan digit")

            # Ekstrak text antara FN dan ETX/ETB
            try:
                end_pos = None
                for j in range(2, len(frame)):
                    if frame[j] in (ETX, ETB):
                        end_pos = j
                        break
                if end_pos is not None:
                    text = frame[2:end_pos].decode("ascii", errors="replace")
                    text_parts.append(text)
            except Exception as e:
                errors.append(f"Frame #{idx + 1} decode gagal: {e}")

        full_text = "".join(text_parts)
        # Split per CR (record separator)
        records = [r for r in full_text.split("\r") if r.strip()]
        return records, errors


class RecordParser:
    """
    Upper-layer parser: record string → dict.

    Setiap method `parse_<type>` mengambil string record lengkap
    (sudah lewat FrameDecoder, masih dengan escape sequences mentah),
    return dict dengan key `record_type` + field-field spesifik.
    """

    def __init__(self):
        # Default delimiters (manual 7.1.4.3 — bisa di-override dari H record)
        self.field_delim = FIELD_DELIM
        self.component_delim = COMPONENT_DELIM
        self.repeat_delim = REPEAT_DELIM

    def _split_fields(self, record: str) -> list[str]:
        """Split record by field delimiter, decode escapes per field."""
        return [FrameDecoder.decode_escapes(f) for f in record.split(self.field_delim)]

    def _component(self, field: str, index: int) -> str:
        """Ambil komponen ke-`index` (0-based) dari sebuah field. Empty bila tidak ada."""
        parts = field.split(self.component_delim)
        return parts[index] if index < len(parts) else ""

    # ============================================================
    # H — Message Header (manual 7.2.2.2)
    # ============================================================
    def parse_h(self, record: str) -> dict:
        """
        Format c-111 contoh (manual hal 39):
        H|\\^&|||c111^Roche^c111^0.5.4.0509^1^1005||||host|RSUPL^BATCH|P|1|20051021152259

        Catatan field positions: c-111 mengirim 13 field, bukan 14 seperti
        skema generik manual — kemungkinan vendor omit satu field "Others".
        Posisi empirik (0-indexed):
            0=H, 1=delim, 2-3=empty, 4=sender, 5-7=empty,
            8=receiver, 9=comment(purpose), 10=processing_id,
            11=version, 12=datetime.
        """
        fields = self._split_fields(record)
        # Sender CM: Name^Manufacturer^InstrumentType^SWVersion^ProtoVers^Serial
        sender = fields[4] if len(fields) > 4 else ""
        # Comment/Special CM: MessageType^Cause (RSUPL^BATCH / RSUPL^REAL)
        purpose = fields[9] if len(fields) > 9 else ""
        # Date/Time YYYYMMDDHHMMSS
        dt = fields[12] if len(fields) > 12 else ""

        return {
            "record_type": REC_HEADER,
            "sender_name":       self._component(sender, 0),
            "sender_manufacturer": self._component(sender, 1),
            "instrument_type":   self._component(sender, 2),
            "sw_version":        self._component(sender, 3),
            "serial_number":     self._component(sender, 5),
            "receiver_id":       fields[8] if len(fields) > 8 else "",
            "purpose_type":      self._component(purpose, 0),   # RSUPL etc.
            "purpose_cause":     self._component(purpose, 1),   # BATCH / REAL
            "message_datetime":  dt,
        }

    # ============================================================
    # P — Patient (manual 7.2.2.4)
    # ============================================================
    def parse_p(self, record: str) -> dict:
        """
        c-111 biasanya kirim `P|1||` (kosong). Pada NPT mode, field 4
        (index 3) berisi sampleID part — kita simpan apa adanya.
        """
        fields = self._split_fields(record)
        return {
            "record_type": REC_PATIENT,
            "sequence":           fields[1] if len(fields) > 1 else "",
            "laboratory_pat_id":  fields[3] if len(fields) > 3 else "",
        }

    # ============================================================
    # O — Test Order (manual 7.2.2.5)
    # ============================================================
    def parse_o(self, record: str) -> dict:
        """
        Saat alat transmits result, sample_id ada di field 09.4.04
        (Instrument Specimen ID, format `<Sample ID>^^<Position ID>`).
        Action code di field 09.4.12 (N=new result, Q=QC).
        """
        fields = self._split_fields(record)
        spec_id_field = fields[3] if len(fields) > 3 else ""  # 09.4.04
        return {
            "record_type": REC_ORDER,
            "sequence":         fields[1] if len(fields) > 1 else "",
            "specimen_id_host": fields[2] if len(fields) > 2 else "",  # 09.4.03
            "sample_id":        self._component(spec_id_field, 0),
            "position_id":      self._component(spec_id_field, 2),
            "priority":         fields[5] if len(fields) > 5 else "",  # 09.4.06 (R/S)
            "action_code":      fields[11] if len(fields) > 11 else "", # 09.4.12 (N/Q)
            "result_datetime":  fields[22] if len(fields) > 22 else "", # 09.4.23
            "report_type":      fields[25] if len(fields) > 25 else "", # 09.4.26 (F/X/Z/Q)
        }

    # ============================================================
    # R — Result (manual 7.2.2.7)
    # ============================================================
    def parse_r(self, record: str) -> dict:
        """
        Example (manual page 48): R|1|^^^111|1480.00|mmol/l||||F||UnitTest
        Test ID di komponen ke-4 (index 3) dari field 10.1.03.
        """
        fields = self._split_fields(record)
        test_id_field = fields[2] if len(fields) > 2 else ""  # 10.1.03
        return {
            "record_type": REC_RESULT,
            "sequence":           fields[1] if len(fields) > 1 else "",
            "test_code":          self._component(test_id_field, 3),
            "treatment":          self._component(test_id_field, 4),
            "value":              fields[3] if len(fields) > 3 else "",   # 10.1.04
            "unit":               fields[4] if len(fields) > 4 else "",   # 10.1.05
            "reference_range":    fields[5] if len(fields) > 5 else "",   # 10.1.06
            "flag":               fields[6] if len(fields) > 6 else "",   # 10.1.07
            "result_status":      fields[8] if len(fields) > 8 else "",   # 10.1.09 (F/C/X/R/I)
            "operator_id":        fields[10] if len(fields) > 10 else "", # 10.1.11
            "test_started":       fields[11] if len(fields) > 11 else "", # 10.1.12
            "test_completed":     fields[12] if len(fields) > 12 else "", # 10.1.13
        }

    # ============================================================
    # C — Comment (manual 7.2.2.6 setelah O, 7.2.2.8 setelah R)
    # ============================================================
    def parse_c(self, record: str) -> dict:
        """
        c-111 mengirim C records HANYA setelah R (manual 7.2.2.8) untuk
        menyampaikan flag yang lebih detail dari yang muat di 10.1.07.
        Format: C|1|I|<FlagCode>^<FlagComment>|I
        """
        fields = self._split_fields(record)
        text_field = fields[3] if len(fields) > 3 else ""
        return {
            "record_type": REC_COMMENT,
            "sequence":      fields[1] if len(fields) > 1 else "",
            "comment_source": fields[2] if len(fields) > 2 else "",  # L=host, I=analyzer
            "flag_code":     self._component(text_field, 0),
            "flag_comment":  self._component(text_field, 1),
            "comment_type":  fields[4] if len(fields) > 4 else "",   # I or G
        }

    # ============================================================
    # M — Manufacturer Specific Record (manual 7.2.2.10 M.CR, 7.2.2.11 M.RR)
    # ============================================================
    def parse_m(self, record: str) -> dict:
        """
        Dispatcher: M record subtype ada di field 3 (index 2) komponen 0.
            CR^BM^c111^1  → Photometric / ISE Calibration Result
            RR^BM^c111^1  → Photometric Absorbance raw data
        Subtype lain → unknown, simpan raw record di parse_errors lewat caller.
        """
        fields = self._split_fields(record)
        subtype_field = fields[2] if len(fields) > 2 else ""
        subtype = self._component(subtype_field, 0)
        if subtype == M_SUBTYPE_CR:
            return self.parse_m_cr(fields)
        elif subtype == M_SUBTYPE_RR:
            return self.parse_m_rr(fields)
        else:
            return {
                "record_type": REC_MANUFACTURER,
                "subtype": subtype,
                "_unknown": True,
            }

    def parse_m_cr(self, fields: list[str]) -> dict:
        """
        Photometric / ISE Calibration Result (manual 7.2.2.10).
        Per spec, raw payload (curve params, replicates) di-DROP — kita
        hanya ekstrak field summary yang berguna untuk audit/LIS.
        """
        carrier_field = fields[6] if len(fields) > 6 else ""  # M.CR.07 BS^GLUC^1 / EL^NA
        return {
            "record_type": REC_MANUFACTURER,
            "subtype":           M_SUBTYPE_CR,
            "sequence":          fields[1] if len(fields) > 1 else "",
            "test_application_code": self._component(fields[3] if len(fields) > 3 else "", 0),
            "test_short_name":    self._component(fields[3] if len(fields) > 3 else "", 1),
            "lot_number":         fields[4] if len(fields) > 4 else "",
            "unit":               fields[5] if len(fields) > 5 else "",
            "carrier_type":       self._component(carrier_field, 0),  # BS or EL
            "carrier_short_name": self._component(carrier_field, 1),
            "calibration_method": fields[8] if len(fields) > 8 else "",   # M.CR.09 N^M / N^R / I
            "replicates":         fields[9] if len(fields) > 9 else "",   # M.CR.10
            "calibration_completed_at": fields[10] if len(fields) > 10 else "",  # M.CR.11 TS
            "result_state":       self._component(fields[11] if len(fields) > 11 else "", 0),
            "operator":           self._component(fields[11] if len(fields) > 11 else "", 1),
        }

    def parse_m_rr(self, fields: list[str]) -> dict:
        """
        Photometric Absorbance raw data (manual 7.2.2.11).
        Raw value list (puluhan/ratusan ratusan absorbance points) di-DROP
        per spec. Kita hanya simpan summary: subtype, sequence, effective signal.
        """
        return {
            "record_type": REC_MANUFACTURER,
            "subtype":          M_SUBTYPE_RR,
            "sequence":         fields[1] if len(fields) > 1 else "",
            "starting_value":   fields[3] if len(fields) > 3 else "",  # M.RR.04
            "effective_signal": fields[5] if len(fields) > 5 else "",  # M.RR.06
        }

    # ============================================================
    # L — Termination (manual 7.2.2.3)
    # ============================================================
    def parse_l(self, record: str) -> dict:
        """`L|1|N` — termination_code: N = normal, E = system error."""
        fields = self._split_fields(record)
        return {
            "record_type": REC_TERMINATOR,
            "sequence":         fields[1] if len(fields) > 1 else "",
            "termination_code": fields[2] if len(fields) > 2 else "",
        }


if __name__ == "__main__":
    # ============================================================
    # FrameDecoder.split_frames tests
    # ============================================================
    fd = FrameDecoder()

    # Test 1: example from manual 7.1.5 ([STX]1Test[ETX]D4[CR][LF])
    sample = b"\x021Test\x03D4\r\n"
    frames = fd.split_frames(sample)
    assert len(frames) == 1, f"expected 1 frame, got {len(frames)}"
    assert frames[0] == b"\x021Test\x03D4", f"got {frames[0]!r}"
    print("OK: split_frames() single end frame")

    # Test 2: two intermediate frames + one end frame
    multi = (
        b"\x021H|\\^&\x17XX\r\n"      # intermediate (ETB=0x17)
        b"\x022P|1\x17YY\r\n"          # intermediate
        b"\x023L|1|N\x03ZZ\r\n"        # end frame (ETX)
    )
    frames = fd.split_frames(multi)
    assert len(frames) == 3, f"expected 3 frames, got {len(frames)}"
    assert frames[0].startswith(b"\x021H") and frames[0].endswith(b"XX")
    assert frames[1].startswith(b"\x022P") and frames[1].endswith(b"YY")
    assert frames[2].startswith(b"\x023L") and frames[2].endswith(b"ZZ")
    print("OK: split_frames() multi-frame intermediate + end")

    # Test 3: empty/garbage input returns []
    assert fd.split_frames(b"") == []
    assert fd.split_frames(b"\x06\x06\x06") == []  # ACK bytes, no STX
    print("OK: split_frames() empty/garbage → []")

    # Test 4: truncated frame at end is dropped (not partially returned)
    truncated = b"\x021Test\x03D4\r\n\x022Inco"  # 2nd frame has no ETX
    frames = fd.split_frames(truncated)
    assert len(frames) == 1, f"expected 1, got {len(frames)}"
    print("OK: split_frames() truncated tail dropped")

    print("=== FrameDecoder.split_frames tests PASSED ===")

    # ============================================================
    # FrameDecoder.compute_checksum / validate_checksum tests
    # ============================================================

    # Test: reference example from manual page 37
    # "[STX]1Test[ETX]" should produce checksum "D4"
    body = b"\x021Test\x03"
    cs = fd.compute_checksum(body)
    assert cs == "D4", f"expected 'D4', got {cs!r}"
    print(f"OK: compute_checksum reference example → {cs}")

    # Test: validate happy path
    full_frame = body + b"D4"
    assert fd.validate_checksum(full_frame) is True
    print("OK: validate_checksum() valid frame")

    # Test: validate failure path
    bad_frame = body + b"FF"
    assert fd.validate_checksum(bad_frame) is False
    print("OK: validate_checksum() rejects wrong checksum")

    # Test: empty/short input
    assert fd.compute_checksum(b"") == ""
    assert fd.compute_checksum(b"\x02") == ""
    assert fd.validate_checksum(b"\x02") is False
    print("OK: validate_checksum() handles short input")

    print("=== FrameDecoder.checksum tests PASSED ===")

    # ============================================================
    # decode_escapes tests
    # ============================================================
    assert FrameDecoder.decode_escapes("abc&F&def") == "abc|def"
    assert FrameDecoder.decode_escapes("&S&start") == "^start"
    assert FrameDecoder.decode_escapes("end&R&") == "end\\"
    assert FrameDecoder.decode_escapes("&E&") == "&"
    assert FrameDecoder.decode_escapes("plain") == "plain"
    # Unknown escape dropped
    assert FrameDecoder.decode_escapes("a&Z&b") == "ab"
    print("OK: decode_escapes() all four sequences + unknown")

    # ============================================================
    # FrameDecoder.decode() full pipeline tests
    # ============================================================

    # Build a valid 1-frame message containing H + L records using checksum helper.
    body = b"\x021H|\\^&|||c111^Roche^c111\rL|1|N\r\x03"
    cs = fd.compute_checksum(body)
    full = body + cs.encode("ascii") + b"\r\n"

    records, errors = fd.decode(full)
    assert errors == [], f"unexpected errors: {errors}"
    assert len(records) == 2, f"expected 2 records, got {len(records)}: {records}"
    assert records[0].startswith("H|"), records[0]
    assert records[1].startswith("L|"), records[1]
    print(f"OK: decode() 1-frame message → {len(records)} records, no errors")

    # Decode with broken checksum → still returns records + error noted
    broken = body + b"FF" + b"\r\n"
    records, errors = fd.decode(broken)
    assert len(records) == 2
    assert any("checksum" in e for e in errors), errors
    print("OK: decode() broken checksum keeps records + reports error")

    # Empty input → empty + error
    records, errors = fd.decode(b"")
    assert records == []
    assert errors and "Tidak ada frame" in errors[0]
    print("OK: decode() empty input handled")

    print("=== FrameDecoder.decode tests PASSED ===")

    # ============================================================
    # RecordParser tests — H, P, L
    # ============================================================
    rp = RecordParser()

    # H record — example from manual page 39
    h_rec = "H|\\^&|||c111^Roche^c111^0.5.4.0509^1^1005||||host|RSUPL^BATCH|P|1|20051021152259"
    h = rp.parse_h(h_rec)
    assert h["record_type"] == "H"
    assert h["sender_name"] == "c111"
    assert h["sender_manufacturer"] == "Roche"
    assert h["instrument_type"] == "c111"
    assert h["sw_version"] == "0.5.4.0509"
    assert h["serial_number"] == "1005"
    assert h["receiver_id"] == "host"
    assert h["purpose_type"] == "RSUPL"
    assert h["purpose_cause"] == "BATCH"
    assert h["message_datetime"] == "20051021152259"
    print("OK: parse_h() manual example parsed correctly")

    # P record — empty patient
    p = rp.parse_p("P|1||")
    assert p["record_type"] == "P"
    assert p["sequence"] == "1"
    assert p["laboratory_pat_id"] == ""
    # P record — NPT with sample id part
    p2 = rp.parse_p("P|1||SAMP123")
    assert p2["laboratory_pat_id"] == "SAMP123"
    print("OK: parse_p() handles empty and NPT")

    # L record — normal termination
    l = rp.parse_l("L|1|N")
    assert l["record_type"] == "L"
    assert l["termination_code"] == "N"
    # L record — error termination
    l2 = rp.parse_l("L|1|E")
    assert l2["termination_code"] == "E"
    print("OK: parse_l() normal + error")

    print("=== RecordParser H/P/L tests PASSED ===")

    # ============================================================
    # RecordParser tests — O
    # ============================================================

    # O record — analyzer transmits result for a patient sample
    # Layout (0-indexed): O(0) 1(1) ""(2) Sample1^^Pos1(3) ^^^111(4) R(5)
    #                     empty 6..10  N(11)  empty 12..21  date(22)
    #                     empty 23,24  F(25)
    o_rec = (
        "O|1||Sample1^^Pos1|^^^111|R||||||N|||||||||||20051021152259|||F"
    )
    # Sanity-check pipe count BEFORE running the parser
    assert o_rec.split("|")[11] == "N", "test string broken: N must land at idx 11"
    assert o_rec.split("|")[22] == "20051021152259", "test string broken: date must land at idx 22"
    assert o_rec.split("|")[25] == "F", "test string broken: F must land at idx 25"

    o = rp.parse_o(o_rec)
    assert o["record_type"] == "O"
    assert o["sequence"] == "1"
    assert o["sample_id"] == "Sample1"
    assert o["position_id"] == "Pos1"
    assert o["priority"] == "R"
    assert o["action_code"] == "N"
    assert o["result_datetime"] == "20051021152259"
    assert o["report_type"] == "F"
    print("OK: parse_o() analyzer-transmit example")

    # O record — QC result (priority empty, action_code = Q at idx 11)
    o_qc = "O|1||QC001^^|^^^111|||||||Q||||||||||||||"
    assert o_qc.split("|")[11] == "Q", "test string broken: Q must land at idx 11"
    oq = rp.parse_o(o_qc)
    assert oq["action_code"] == "Q"
    assert oq["sample_id"] == "QC001"
    print("OK: parse_o() QC action code")

    print("=== RecordParser O tests PASSED ===")

    # ============================================================
    # RecordParser tests — R, C
    # ============================================================

    # R record — manual example page 48
    r_rec = "R|1|^^^111|1480.00|mmol/l||||F||UnitTest"
    r = rp.parse_r(r_rec)
    assert r["record_type"] == "R"
    assert r["test_code"] == "111"
    assert r["value"] == "1480.00"
    assert r["unit"] == "mmol/l"
    assert r["result_status"] == "F"
    assert r["operator_id"] == "UnitTest"
    print("OK: parse_r() manual example")

    # R record — H flag with reference range
    r2 = rp.parse_r("R|1|^^^687|49.2|U/L|20.0\\30.0|H||F||admin")
    assert r2["value"] == "49.2"
    assert r2["flag"] == "H"
    assert r2["reference_range"] == "20.0\\30.0"
    print("OK: parse_r() H flag + reference range")

    # R record — below-range with "<" flag
    r3 = rp.parse_r("R|1|^^^687|-0.1|U/L||<||F||admin")
    assert r3["flag"] == "<"
    assert r3["value"] == "-0.1"
    print("OK: parse_r() below-range flag")

    # C record (after R)
    c = rp.parse_c("C|1|I|43|I")
    assert c["record_type"] == "C"
    assert c["comment_source"] == "I"
    assert c["flag_code"] == "43"
    assert c["comment_type"] == "I"
    print("OK: parse_c() analyzer flag comment")

    # C record with flag + comment text
    c2 = rp.parse_c("C|1|I|Sol1^F Dev|I")
    assert c2["flag_code"] == "Sol1"
    assert c2["flag_comment"] == "F Dev"
    print("OK: parse_c() flag with comment text")

    print("=== RecordParser R/C tests PASSED ===")

    # ============================================================
    # RecordParser tests — M.CR, M.RR
    # ============================================================

    # M.CR — manual example page 53 (truncated curve params for brevity)
    m_cr = (
        "2M|1|CR^BM^c111^1|211^Ap211|Rea1.1|mmol/l|BS^Rea1||N^R|2|"
        "20051221083459|A^$SYS$||1.650000E-01^-3.909952E-05|SD^^^St1.1|"
        "2110^0.0825^0.055^0.11^0^0\\0^0.165^0.11"
    )
    parsed = rp.parse_m(m_cr)
    assert parsed["record_type"] == "M"
    assert parsed["subtype"] == "CR"
    assert parsed["test_application_code"] == "211"
    assert parsed["test_short_name"] == "Ap211"
    assert parsed["lot_number"] == "Rea1.1"
    assert parsed["unit"] == "mmol/l"
    assert parsed["carrier_type"] == "BS"
    assert parsed["calibration_method"] == "N^R"
    assert parsed["result_state"] == "A"
    assert parsed["operator"] == "$SYS$"
    print("OK: parse_m_cr() manual example")

    # M.CR — ISE calibration variant (manual page 55 Example Sodium)
    m_cr_ise = (
        "2M|1|CR^BM^c111^1|989^NA-I|GSS_ONLY|mmol/L|EL^NA|^NA^0\\^REF^0|I^R|1|"
        "20081021130649|A^admin||4.914673E+01^5.640128E-03|SD^^^21461500"
    )
    parsed_ise = rp.parse_m(m_cr_ise)
    assert parsed_ise["subtype"] == "CR"
    assert parsed_ise["carrier_type"] == "EL"  # Electrode
    assert parsed_ise["test_application_code"] == "989"
    print("OK: parse_m_cr() ISE variant carrier=EL")

    # M.RR — manual example page 56
    m_rr = "M|5|RR^BM^c111^1|10|10\\0\\87\\109\\131\\153\\200|0.055000"
    parsed_rr = rp.parse_m(m_rr)
    assert parsed_rr["record_type"] == "M"
    assert parsed_rr["subtype"] == "RR"
    assert parsed_rr["sequence"] == "5"
    assert parsed_rr["starting_value"] == "10"
    assert parsed_rr["effective_signal"] == "0.055000"
    print("OK: parse_m_rr() manual example")

    # Unknown M subtype
    parsed_unk = rp.parse_m("M|1|XX^BM^c111^1|foo")
    assert parsed_unk["subtype"] == "XX"
    assert parsed_unk["_unknown"] is True
    print("OK: parse_m() unknown subtype flagged")

    print("=== RecordParser M.CR/M.RR tests PASSED ===")
