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
