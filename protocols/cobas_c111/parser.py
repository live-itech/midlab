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
