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
