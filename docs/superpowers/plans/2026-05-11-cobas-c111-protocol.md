# Cobas c-111 Protocol Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a standalone unidirectional Cobas c-111 ASTM protocol module so MidLab can ingest result messages from this Roche chemistry analyzer.

**Architecture:** New module `protocols/cobas_c111/` with three files (constants, parser, module). Reuses existing receiver's ENQ-ACK-EOT handshake by routing `"COBAS_C111"` to the same lower-layer code path as `"ASTM"`. Output flows into `tbl_result` through the existing pipeline — the only model change is one additive `comments` field on `ResultObject`.

**Tech Stack:** Python 3.10+, asyncio, SQLAlchemy. No new dependencies. Tests follow the project's existing convention: `if __name__ == "__main__":` blocks with `assert` statements (not pytest), runnable as `python3 <file>`.

**Spec:** `docs/superpowers/specs/2026-05-11-cobas-c111-protocol-design.md`
**Vendor manual:** `docs/vendor/cobas_c111/host_interface_manual.pdf`

---

## File Layout

### New files
- `protocols/cobas_c111/__init__.py` — empty package marker
- `protocols/cobas_c111/constants.py` — control bytes, delimiters, record IDs
- `protocols/cobas_c111/parser.py` — `FrameDecoder` + `RecordParser` classes
- `protocols/cobas_c111/module.py` — `CobasC111Module` class

### Modified files
- `lib/models.py` — add `ResultObject.comments` field + reconstruction in `from_dict()`
- `protocols/base.py` — add one entry to `_PROTOCOL_REGISTRY`
- `services/tcp_socket/receiver.py` — extend dispatcher branch (1 line change)

---

## Task 1: Add `comments` field to `ResultObject`

**Files:**
- Modify: `lib/models.py` (class `ResultObject` around line 135-174)

- [ ] **Step 1: Read the current `ResultObject` definition**

Run: `grep -n "class ResultObject" lib/models.py`
Expected output: line number around 136

- [ ] **Step 2: Add `comments` field to the dataclass**

In `lib/models.py`, locate the `ResultObject` class. After the `results` field (around line 149) and before `parse_errors`, insert:

```python
    comments: List[str] = field(default_factory=list)
```

After the edit, the field declaration block should read:
```python
    results: List[TestResult] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)
```

- [ ] **Step 3: Update `ResultObject.from_dict()` to keep `comments` list passthrough**

In the same class, find `from_dict()` around line 156. The existing code already filters by `valid_keys`, so `comments` will be picked up. No code change needed — but verify by reading the method.

- [ ] **Step 4: Add inline test for `comments` to the existing `__main__` block at end of file**

Check if `lib/models.py` already has `if __name__ == "__main__":` block. If yes — append. If no — add at the very end:

```python
if __name__ == "__main__":
    # Test: ResultObject.comments default is empty list
    r = ResultObject(instrument_id=1, protocol="COBAS_C111")
    assert r.comments == [], f"expected [], got {r.comments}"
    print("OK: ResultObject.comments default is []")

    # Test: comments survives to_dict / from_dict roundtrip
    r.comments.append("order: collected by night shift")
    d = r.to_dict()
    assert d["comments"] == ["order: collected by night shift"]
    r2 = ResultObject.from_dict(d)
    assert r2.comments == ["order: collected by night shift"]
    print("OK: comments roundtrip works")

    print("=== ResultObject.comments tests PASSED ===")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 lib/models.py`
Expected: prints OK lines and `=== ResultObject.comments tests PASSED ===` at minimum (also any pre-existing tests pass).

- [ ] **Step 6: Verify existing ASTM and HL7 modules still work**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/astm/module.py && python3 protocols/hl7/module.py`
Expected: both still print their existing PASS messages (the additive `comments` field has not broken anything).

- [ ] **Step 7: Commit**

```bash
git add lib/models.py
git commit -m "models: add additive ResultObject.comments field

Used by the upcoming Cobas c-111 parser to capture ASTM Comment (C)
records. Default empty list keeps existing ASTM/HL7 behaviour unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Scaffold the `cobas_c111` package + constants

**Files:**
- Create: `protocols/cobas_c111/__init__.py`
- Create: `protocols/cobas_c111/constants.py`

- [ ] **Step 1: Create empty package marker**

Create `protocols/cobas_c111/__init__.py` with the single docstring line:

```python
"""protocols.cobas_c111 — Roche Cobas c-111 ASTM (unidirectional) protocol module."""
```

- [ ] **Step 2: Create `constants.py`**

Create `protocols/cobas_c111/constants.py` with the following content (control bytes verified against manual Section 7.1.3 Table 7; delimiter and record IDs verified against Section 7.1.4 + Section 7.2.1 Table 8 + Table 9):

```python
"""
protocols/cobas_c111/constants.py — Konstanta protokol ASTM untuk Cobas c-111.

Sumber: Roche Host Interface Manual v2.2 untuk Cobas c 111 SW v3.0+
        Chapter 7 (ASTM Protocol).

Modul ini STANDALONE — tidak import dari protocols/astm/.
"""

# ============================================================
# ASTM E1381 lower-layer control characters (manual 7.1.3 Table 7)
# ============================================================
STX = 0x02   # Start of Text
ETX = 0x03   # End of Text (last frame)
ETB = 0x17   # End of Transmission Block (intermediate frame)
EOT = 0x04   # End of Transmission (session end)
ENQ = 0x05   # Enquiry (session start)
ACK = 0x06   # Acknowledge
NAK = 0x15   # Negative Acknowledge
CR  = 0x0D   # Carriage Return (record separator within frame text)
LF  = 0x0A   # Line Feed (frame trailing)

STX_BYTE = bytes([STX])
ETX_BYTE = bytes([ETX])
ETB_BYTE = bytes([ETB])
EOT_BYTE = bytes([EOT])
ENQ_BYTE = bytes([ENQ])
ACK_BYTE = bytes([ACK])
NAK_BYTE = bytes([NAK])
CR_BYTE  = bytes([CR])

# ============================================================
# Delimiters (manual 7.1.4.3 — recommended defaults, declared in H record)
# ============================================================
FIELD_DELIM     = "|"
REPEAT_DELIM    = "\\"
COMPONENT_DELIM = "^"
ESCAPE_DELIM    = "&"

# Escape sequences (manual 7.1.4.3.5)
# Note: these are SUB-strings that appear in text; decode after field split.
ESC_FIELD     = "&F&"  # → "|"
ESC_COMPONENT = "&S&"  # → "^"
ESC_REPEAT    = "&R&"  # → "\\"
ESC_ESCAPE    = "&E&"  # → "&"

ESCAPE_MAP = {
    ESC_FIELD:     FIELD_DELIM,
    ESC_COMPONENT: COMPONENT_DELIM,
    ESC_REPEAT:    REPEAT_DELIM,
    ESC_ESCAPE:    ESCAPE_DELIM,
}

# ============================================================
# Record type identifiers (manual 7.2.1 Tables 8 & 9)
# ============================================================
REC_HEADER      = "H"
REC_PATIENT     = "P"
REC_ORDER       = "O"
REC_RESULT      = "R"
REC_COMMENT     = "C"
REC_TERMINATOR  = "L"
REC_QUERY       = "Q"
REC_MANUFACTURER = "M"

VALID_RECORDS = {
    REC_HEADER, REC_PATIENT, REC_ORDER, REC_RESULT,
    REC_COMMENT, REC_TERMINATOR, REC_QUERY, REC_MANUFACTURER,
}

# Roche Manufacturer Specific Record subtypes (manual 7.2 Table 9, 7.2.2.10, 7.2.2.11)
M_SUBTYPE_CR = "CR"   # Photometric / ISE Calibration Result
M_SUBTYPE_RR = "RR"   # Photometric Absorbance raw data

# Protocol identity
PROTOCOL_NAME = "COBAS_C111"
PROTOCOL_VERSION = "1.0.0"


if __name__ == "__main__":
    # Sanity checks
    assert STX == 0x02 and ETX == 0x03 and ETB == 0x17
    assert EOT == 0x04 and ENQ == 0x05 and ACK == 0x06
    assert ESCAPE_MAP["&F&"] == "|"
    assert ESCAPE_MAP["&S&"] == "^"
    assert ESCAPE_MAP["&R&"] == "\\"
    assert ESCAPE_MAP["&E&"] == "&"
    assert PROTOCOL_NAME == "COBAS_C111"
    assert {REC_HEADER, REC_RESULT, REC_TERMINATOR}.issubset(VALID_RECORDS)
    print("=== cobas_c111.constants tests PASSED ===")
```

- [ ] **Step 3: Run test to verify**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/constants.py`
Expected: prints `=== cobas_c111.constants tests PASSED ===`

- [ ] **Step 4: Commit**

```bash
git add protocols/cobas_c111/__init__.py protocols/cobas_c111/constants.py
git commit -m "cobas_c111: scaffold package with ASTM constants

Standalone constants — control bytes, delimiters, record IDs, Roche
M-record subtypes. Verified against Cobas c-111 Host Interface Manual
v2.2 chapter 7.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: `FrameDecoder.split_frames()` — split bytes into frame chunks

**Files:**
- Create: `protocols/cobas_c111/parser.py`

- [ ] **Step 1: Create parser.py with skeleton + first method**

Create `protocols/cobas_c111/parser.py`:

```python
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
```

- [ ] **Step 2: Add tests to the `__main__` block at the bottom of parser.py**

Append at end of file:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/parser.py`
Expected: All 4 OK lines + `=== FrameDecoder.split_frames tests PASSED ===`

- [ ] **Step 4: Commit**

```bash
git add protocols/cobas_c111/parser.py
git commit -m "cobas_c111: FrameDecoder.split_frames

Splits an accumulated byte stream from the TCP receiver into individual
ASTM E1381 frames (STX..ETX/ETB + 2-byte checksum). Tolerant to
optional CR/LF trailing each frame and to truncated tails.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: `FrameDecoder.validate_checksum()` + reference test from manual

**Files:**
- Modify: `protocols/cobas_c111/parser.py`

- [ ] **Step 1: Add checksum method to `FrameDecoder` class**

In `parser.py`, inside class `FrameDecoder`, after `split_frames()`, add:

```python
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
```

- [ ] **Step 2: Add tests inside `__main__` block — append after existing FrameDecoder tests**

In the `__main__` block of `parser.py`, add after the previous tests:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/parser.py`
Expected: All previous + new OK lines, ending with `=== FrameDecoder.checksum tests PASSED ===`

- [ ] **Step 4: Commit**

```bash
git add protocols/cobas_c111/parser.py
git commit -m "cobas_c111: FrameDecoder checksum compute/validate

Implements manual 7.1.5 checksum: sum of bytes from FN through
ETX/ETB inclusive, modulus 256, formatted as 2-hex uppercase.
Verified against the manual's worked example on page 37
(STX 1Test ETX → D4).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: `FrameDecoder.decode()` — escape decoding + complete pipeline

**Files:**
- Modify: `protocols/cobas_c111/parser.py`

- [ ] **Step 1: Add escape decoder + pipeline method**

In `parser.py`, inside class `FrameDecoder`, after the checksum methods, add:

```python
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
```

- [ ] **Step 2: Add tests for `decode_escapes` and full `decode()` pipeline**

Append to `__main__` block:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/parser.py`
Expected: All previous + new tests pass.

- [ ] **Step 4: Commit**

```bash
git add protocols/cobas_c111/parser.py
git commit -m "cobas_c111: FrameDecoder.decode end-to-end pipeline

Adds escape decoding (manual 7.1.4.3.5) and the full decode() pipeline:
split frames, validate checksum (errors collected but processing
continues), check FN sequence, concat text, split by CR.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: `RecordParser` — H, P, L records

**Files:**
- Modify: `protocols/cobas_c111/parser.py`

- [ ] **Step 1: Add `RecordParser` class with H, P, L methods**

In `parser.py`, after the `FrameDecoder` class definition (and before `__main__`), add:

```python
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
        """
        fields = self._split_fields(record)
        # Field 5 (index 4) = sender CM: Name^Manufacturer^InstrumentType^SWVersion^ProtoVers^Serial
        sender = fields[4] if len(fields) > 4 else ""
        # Field 11 (index 10) = comment CM: MessageType^Cause (RSUPL^BATCH / RSUPL^REAL)
        purpose = fields[10] if len(fields) > 10 else ""
        # Field 14 (index 13) = datetime YYYYMMDDHHMMSS
        dt = fields[13] if len(fields) > 13 else ""

        return {
            "record_type": REC_HEADER,
            "sender_name":       self._component(sender, 0),
            "sender_manufacturer": self._component(sender, 1),
            "instrument_type":   self._component(sender, 2),
            "sw_version":        self._component(sender, 3),
            "serial_number":     self._component(sender, 5),
            "receiver_id":       fields[9] if len(fields) > 9 else "",
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
```

- [ ] **Step 2: Add tests for parse_h / parse_p / parse_l**

Append to `__main__`:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/parser.py`
Expected: all OK, new section PASSED.

- [ ] **Step 4: Commit**

```bash
git add protocols/cobas_c111/parser.py
git commit -m "cobas_c111: RecordParser H, P, L records

Implements message Header, Patient, and Termination record parsing
per manual sections 7.2.2.2 through 7.2.2.4. Field positions verified
against the manual's worked examples.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: `RecordParser` — O (Test Order) record

**Files:**
- Modify: `protocols/cobas_c111/parser.py`

- [ ] **Step 1: Add `parse_o` method inside `RecordParser`**

Insert after `parse_l` in the `RecordParser` class:

```python
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
```

- [ ] **Step 2: Add tests for `parse_o`**

Append to `__main__` (before the final `print("=== ... PASSED ===")` block of the previous task):

```python
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
    # Sanity-check pipe count BEFORE running the parser, so a bad string
    # is detected as the source of any later failure.
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
```

- [ ] **Step 3: Run tests**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/parser.py`
Expected: all OK lines, new PASSED line.

- [ ] **Step 4: Commit**

```bash
git add protocols/cobas_c111/parser.py
git commit -m "cobas_c111: RecordParser O record

Extracts sample ID, position, priority, action code, result datetime,
and report type from the Test Order record. Field positions verified
against manual section 7.2.2.5 and example messages.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: `RecordParser` — R (Result) and C (Comment) records

**Files:**
- Modify: `protocols/cobas_c111/parser.py`

- [ ] **Step 1: Add `parse_r` and `parse_c` methods to `RecordParser`**

Insert after `parse_o`:

```python
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
```

- [ ] **Step 2: Add tests**

Append to `__main__`:

```python
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

    # R record — H flag with reference range (page 50 example1)
    r2 = rp.parse_r("4R|1|^^^687|49.2|U/L|20.0\\30.0|H||F||admin")
    # Note: leading "4" here mimics frame FN prefix in the manual example;
    # in our pipeline the frame FN has been stripped already, so realistic
    # input to parse_r would just start with "R|"; we still test that the
    # parser is tolerant when the first character is not literally "R".
    # The split-by-pipe still works, but field[0]="4R" so we skip the
    # record_type assertion here and just assert the data.
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
```

- [ ] **Step 3: Run tests**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/parser.py`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add protocols/cobas_c111/parser.py
git commit -m "cobas_c111: RecordParser R and C records

R parses test code, value, unit, reference range, flag, status, and
operator per manual 7.2.2.7. C parses analyzer-emitted comment records
following R (manual 7.2.2.8), extracting flag code and detail comment.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: `RecordParser` — M.CR and M.RR records

**Files:**
- Modify: `protocols/cobas_c111/parser.py`

- [ ] **Step 1: Add `parse_m` dispatcher + parse_m_cr / parse_m_rr methods**

Insert after `parse_c`:

```python
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
```

- [ ] **Step 2: Add tests for M.CR and M.RR**

Append to `__main__`:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/parser.py`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add protocols/cobas_c111/parser.py
git commit -m "cobas_c111: RecordParser M.CR and M.RR records

Roche manufacturer-specific records. M.CR covers photometric and ISE
calibration results (manual 7.2.2.10); M.RR carries photometric raw
absorbance (manual 7.2.2.11). Per spec, raw curve/absorbance payloads
are dropped — only summary fields preserved.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: `CobasC111Module` — implement `BaseProtocolModule` contract

**Files:**
- Create: `protocols/cobas_c111/module.py`

- [ ] **Step 1: Create `module.py`**

Create `protocols/cobas_c111/module.py`:

```python
"""
protocols/cobas_c111/module.py — Cobas c-111 Protocol Module (unidirectional).

Implements BaseProtocolModule. Hanya parse() yang di-implement penuh;
method bidirectional (format_order, query handling, dll) raise
NotImplementedError karena modul ini intentionally unidirectional.

Loaded dynamically oleh protocols.base.load_module("COBAS_C111").
"""

from lib.utils import get_logger
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.cobas_c111.constants import (
    ENQ, ACK, NAK, EOT,
    ENQ_BYTE, ACK_BYTE, NAK_BYTE, EOT_BYTE,
    PROTOCOL_NAME, PROTOCOL_VERSION,
    REC_HEADER, REC_PATIENT, REC_ORDER, REC_RESULT, REC_COMMENT,
    REC_TERMINATOR, REC_MANUFACTURER,
    M_SUBTYPE_CR, M_SUBTYPE_RR,
)
from protocols.cobas_c111.parser import FrameDecoder, RecordParser


_UNIDIRECTIONAL_MSG = "Cobas c-111 module is unidirectional — bidirectional methods not implemented"


class CobasC111Module(BaseProtocolModule):
    """
    Protocol module untuk Roche Cobas c-111 (Host Interface Manual v2.2).

    Unidirectional only. Lower-layer ASTM E1381 handshake reused dari
    services/tcp_socket/receiver.py:_handle_astm_data via dispatcher branch.
    """

    def __init__(self):
        self._decoder = FrameDecoder()
        self._record_parser = RecordParser()
        self._logger = get_logger("cobas_c111")

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    # ============================================================
    # parse() — receiver memanggil ini dengan akumulasi bytes setelah EOT
    # ============================================================
    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        instrument_id = instrument.get("id", 0)
        self._logger.info(
            f"Mulai parse {len(raw_bytes)} bytes dari instrument {instrument_id}"
        )

        result = ResultObject(
            instrument_id=instrument_id,
            protocol=PROTOCOL_NAME,
        )

        records_str, frame_errors = self._decoder.decode(raw_bytes)
        result.parse_errors.extend(frame_errors)

        if not records_str:
            self._logger.warning("Tidak ada record valid setelah decode")
            return result.to_dict()

        last_test_code: str = ""  # untuk attribusi C-after-R

        for rec_str in records_str:
            if not rec_str:
                continue
            rtype = rec_str[0].upper()
            try:
                if rtype == REC_HEADER:
                    h = self._record_parser.parse_h(rec_str)
                    if h.get("message_datetime"):
                        result.message_datetime = h["message_datetime"]
                elif rtype == REC_PATIENT:
                    p = self._record_parser.parse_p(rec_str)
                    result.patient = PatientInfo(
                        patient_id=p.get("laboratory_pat_id", ""),
                    )
                elif rtype == REC_ORDER:
                    o = self._record_parser.parse_o(rec_str)
                    result.specimen = SpecimenInfo(
                        sample_id=o.get("sample_id", ""),
                        sample_type=o.get("action_code", ""),  # N/Q/A/C
                        collected_at=o.get("result_datetime", ""),
                    )
                    result.order = OrderInfo(
                        order_id=o.get("sample_id", ""),
                        panel=o.get("report_type", ""),  # F/X/Z/Q
                    )
                elif rtype == REC_RESULT:
                    r = self._record_parser.parse_r(rec_str)
                    last_test_code = r.get("test_code", "")
                    result.results.append(TestResult(
                        test_code=r.get("test_code", ""),
                        test_name="",
                        value=r.get("value", ""),
                        unit=r.get("unit", ""),
                        reference_range=r.get("reference_range", ""),
                        flag=r.get("flag", ""),
                        status=r.get("result_status", ""),
                    ))
                elif rtype == REC_COMMENT:
                    c = self._record_parser.parse_c(rec_str)
                    code = c.get("flag_code", "")
                    text = c.get("flag_comment", "")
                    body = f"{code}: {text}" if text else code
                    attribution = f"result[{last_test_code}]" if last_test_code else "order"
                    result.comments.append(f"{attribution}: {body}")
                elif rtype == REC_MANUFACTURER:
                    m = self._record_parser.parse_m(rec_str)
                    if m.get("_unknown"):
                        result.parse_errors.append(
                            f"Unknown M subtype: {m.get('subtype', '')}"
                        )
                        self._logger.info(f"M record subtype tak dikenal: {m.get('subtype')}")
                    elif m.get("subtype") == M_SUBTYPE_CR:
                        result.results.append(TestResult(
                            test_code=m.get("test_application_code", ""),
                            test_name=m.get("test_short_name", ""),
                            value=m.get("result_state", ""),
                            unit=m.get("unit", ""),
                            reference_range="",
                            flag=m.get("calibration_method", ""),
                            status="calibration",
                        ))
                        self._logger.info("M.CR calibration result captured (summary only)")
                    elif m.get("subtype") == M_SUBTYPE_RR:
                        result.results.append(TestResult(
                            test_code=f"RR-{m.get('sequence', '')}",
                            test_name="",
                            value=m.get("effective_signal", ""),
                            unit="",
                            reference_range="",
                            flag="",
                            status="absorbance_raw",
                        ))
                        self._logger.info("M.RR absorbance summary captured (raw payload dropped)")
                elif rtype == REC_TERMINATOR:
                    l = self._record_parser.parse_l(rec_str)
                    code = l.get("termination_code", "N")
                    if code and code != "N":
                        result.parse_errors.append(f"termination={code}")
                else:
                    result.parse_errors.append(f"Unknown record type: {rtype}")
                    self._logger.warning(f"Record type tak dikenal: {rtype}")
            except Exception as e:
                result.parse_errors.append(
                    f"Parse failure on '{rec_str[:40]}...': {e}"
                )
                self._logger.warning(f"Parse exception: {e}")

        self._logger.info(
            f"Parse selesai: {len(result.results)} results, "
            f"{len(result.comments)} comments, "
            f"{len(result.parse_errors)} errors"
        )
        return result.to_dict()

    # ============================================================
    # is_enq / handle_ack — receiver butuh ini (lower-layer dispatch)
    # ============================================================
    def is_enq(self, raw_bytes: bytes) -> bool:
        """True bila byte pertama adalah ENQ. Q-record query OUT-OF-SCOPE."""
        return bool(raw_bytes) and raw_bytes[0:1] == ENQ_BYTE

    def handle_ack(self, raw_bytes: bytes) -> str:
        """Return 'ACK', 'NAK', 'EOT', atau 'UNKNOWN'."""
        if not raw_bytes:
            return "UNKNOWN"
        first = raw_bytes[0:1]
        if first == ACK_BYTE:
            return "ACK"
        if first == NAK_BYTE:
            return "NAK"
        if first == EOT_BYTE:
            return "EOT"
        return "UNKNOWN"

    # ============================================================
    # Bidirectional methods — intentionally not implemented
    # ============================================================
    def format_order(self, order: dict, instrument: dict) -> bytes:
        raise NotImplementedError(_UNIDIRECTIONAL_MSG)

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        raise NotImplementedError(_UNIDIRECTIONAL_MSG)

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        raise NotImplementedError(_UNIDIRECTIONAL_MSG)

    def format_query_not_found(self, instrument: dict) -> bytes:
        raise NotImplementedError(_UNIDIRECTIONAL_MSG)


# ============================================================
# Unit tests
# ============================================================
if __name__ == "__main__":
    mod = CobasC111Module()

    # Identity
    assert mod.PROTOCOL_NAME == "COBAS_C111"
    assert mod.VERSION == "1.0.0"
    print(f"OK: {mod.PROTOCOL_NAME} v{mod.VERSION}")

    # is_enq / handle_ack
    assert mod.is_enq(b"\x05") is True
    assert mod.is_enq(b"\x06") is False
    assert mod.is_enq(b"") is False
    assert mod.handle_ack(b"\x06") == "ACK"
    assert mod.handle_ack(b"\x15") == "NAK"
    assert mod.handle_ack(b"\x04") == "EOT"
    assert mod.handle_ack(b"") == "UNKNOWN"
    print("OK: is_enq / handle_ack")

    # Bidirectional methods raise NotImplementedError
    for method_name, args in [
        ("format_order", ({}, {})),
        ("handle_enq", (b"\x05", {})),
        ("format_query_response", ({}, {})),
        ("format_query_not_found", ({},)),
    ]:
        raised = False
        try:
            getattr(mod, method_name)(*args)
        except NotImplementedError as e:
            raised = True
            assert "unidirectional" in str(e).lower()
        assert raised, f"{method_name} should raise NotImplementedError"
    print("OK: all bidirectional methods raise NotImplementedError")

    # End-to-end: build a multi-record message and parse it
    from protocols.cobas_c111.parser import FrameDecoder as _FD
    fd_helper = _FD()

    body_text = (
        "1H|\\^&|||c111^Roche^c111^0.5.4.0509^1^1005||||host|RSUPL^BATCH|P|1|20051021152259\r"
        "P|1||\r"
        "O|1||Sample1^^Pos1|^^^111|R||||||N|||||||||||20051021152259|||F\r"
        "R|1|^^^111|1480.00|mmol/l||||F||UnitTest\r"
        "L|1|N\r"
    )
    # Wrap as single end-frame
    body = b"\x02" + body_text.encode("ascii") + b"\x03"
    cs = fd_helper.compute_checksum(body)
    full = body + cs.encode("ascii") + b"\r\n"

    parsed = mod.parse(full, {"id": 42, "name": "Cobas c-111 #1"})
    assert parsed["instrument_id"] == 42
    assert parsed["protocol"] == "COBAS_C111"
    assert parsed["patient"]["patient_id"] == ""
    assert parsed["specimen"]["sample_id"] == "Sample1"
    assert len(parsed["results"]) == 1
    assert parsed["results"][0]["test_code"] == "111"
    assert parsed["results"][0]["value"] == "1480.00"
    assert parsed["results"][0]["unit"] == "mmol/l"
    assert parsed["results"][0]["status"] == "F"
    assert parsed["parse_errors"] == []
    print("OK: parse() end-to-end H+P+O+R+L")

    # End-to-end: message with M.CR
    body_text2 = (
        "1H|\\^&|||c111^Roche^c111^0.5.4.0509^1^1005||||host|RSUPL^BATCH|P|1|20081021130649\r"
        "P|1||\r"
        "M|1|CR^BM^c111^1|211^Ap211|Rea1.1|mmol/l|BS^Rea1||N^R|2|20081021130649|A^admin\r"
        "L|1|N\r"
    )
    body2 = b"\x02" + body_text2.encode("ascii") + b"\x03"
    cs2 = fd_helper.compute_checksum(body2)
    full2 = body2 + cs2.encode("ascii") + b"\r\n"
    parsed2 = mod.parse(full2, {"id": 42})
    assert len(parsed2["results"]) == 1
    assert parsed2["results"][0]["status"] == "calibration"
    assert parsed2["results"][0]["test_code"] == "211"
    assert parsed2["results"][0]["test_name"] == "Ap211"
    print("OK: parse() captures M.CR as calibration result")

    # End-to-end: message with R + C (comment after result)
    body_text3 = (
        "1H|\\^&|||c111^Roche^c111^0.5.4.0509^1^1005||||host|RSUPL^BATCH|P|1|20081021130649\r"
        "P|1||\r"
        "O|1||Sample2^^|^^^687|R||||||N|||||||||||20081021130649|||F\r"
        "R|1|^^^687|49.2|U/L|20.0\\30.0|H||F||admin\r"
        "C|1|I|Sol1^F Dev|I\r"
        "L|1|N\r"
    )
    body3 = b"\x02" + body_text3.encode("ascii") + b"\x03"
    cs3 = fd_helper.compute_checksum(body3)
    full3 = body3 + cs3.encode("ascii") + b"\r\n"
    parsed3 = mod.parse(full3, {"id": 42})
    assert len(parsed3["results"]) == 1
    assert parsed3["results"][0]["flag"] == "H"
    assert len(parsed3["comments"]) == 1
    assert "result[687]" in parsed3["comments"][0]
    assert "Sol1" in parsed3["comments"][0]
    print("OK: parse() captures C-after-R into comments with test attribution")

    print("=== CobasC111Module tests PASSED ===")
```

- [ ] **Step 2: Run tests**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/cobas_c111/module.py`
Expected: all OK lines + `=== CobasC111Module tests PASSED ===`

- [ ] **Step 3: Commit**

```bash
git add protocols/cobas_c111/module.py
git commit -m "cobas_c111: CobasC111Module implementing BaseProtocolModule

parse() orchestrates FrameDecoder + RecordParser, assembles a
ResultObject, attributes C records to either the order or to the
preceding R test_code, captures M.CR as a calibration TestResult and
M.RR as an absorbance_raw TestResult. Bidirectional methods raise
NotImplementedError per the spec (unidirectional only).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: Wire protocol into registry + receiver dispatcher

**Files:**
- Modify: `protocols/base.py` (line 156-160)
- Modify: `services/tcp_socket/receiver.py` (line 88)

- [ ] **Step 1: Register `COBAS_C111` in protocol registry**

In `protocols/base.py`, locate `_PROTOCOL_REGISTRY` (around line 156). Replace:

```python
_PROTOCOL_REGISTRY = {
    "ASTM": "protocols.astm.module",
    "HL7":  "protocols.hl7.module",
    "BCI":  "protocols.bci.module",
}
```

with:

```python
_PROTOCOL_REGISTRY = {
    "ASTM": "protocols.astm.module",
    "HL7":  "protocols.hl7.module",
    "BCI":  "protocols.bci.module",
    "COBAS_C111": "protocols.cobas_c111.module",
}
```

- [ ] **Step 2: Verify registry loads the module**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 -c "from protocols.base import load_module; m = load_module('COBAS_C111'); print(m.PROTOCOL_NAME, m.VERSION)"`
Expected: `COBAS_C111 1.0.0`

- [ ] **Step 3: Extend receiver dispatcher**

In `services/tcp_socket/receiver.py`, locate the dispatcher around line 86-94:

```python
        protocol = self._config.protocol.upper()

        if protocol == "ASTM":
            return await self._handle_astm_data(writer)
        elif protocol == "HL7":
            return await self._handle_hl7_data(writer)
        else:
            # Protocol lain: coba parse langsung
            return await self._handle_generic_data(writer)
```

Replace the `if protocol == "ASTM":` branch so it covers both ASTM and Cobas c-111 (same E1381 lower layer):

```python
        protocol = self._config.protocol.upper()

        if protocol in ("ASTM", "COBAS_C111"):
            return await self._handle_astm_data(writer)
        elif protocol == "HL7":
            return await self._handle_hl7_data(writer)
        else:
            # Protocol lain: coba parse langsung
            return await self._handle_generic_data(writer)
```

- [ ] **Step 4: Verify base.py self-test still passes**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 protocols/base.py`
Expected: existing test suite still passes (the `_PROTOCOL_REGISTRY` now has 4 entries; that assertion in the existing test uses `list(_PROTOCOL_REGISTRY.keys())` and just prints it — confirm output mentions COBAS_C111).

- [ ] **Step 5: Verify all four protocol modules still load**

Run: `cd /home/setya/Documents/ME/liveit/midlab && python3 -c "
from protocols.base import load_module
for p in ['ASTM', 'HL7', 'COBAS_C111']:
    m = load_module(p)
    print(f'{p}: {m.PROTOCOL_NAME} v{m.VERSION}')
"`
Expected: three lines printed, one per protocol.

- [ ] **Step 6: Commit**

```bash
git add protocols/base.py services/tcp_socket/receiver.py
git commit -m "cobas_c111: register protocol and route via receiver dispatcher

- protocols/base.py: add COBAS_C111 entry to _PROTOCOL_REGISTRY so
  load_module('COBAS_C111') returns the new module.
- services/tcp_socket/receiver.py: treat COBAS_C111 the same as ASTM
  for lower-layer handshake (E1381 is identical).

Web Console /api/protocols and the instrument dropdown will pick up
the new option automatically via the registry.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 12: Full-suite smoke run + manual verification checklist

**Files:** none — verification only.

- [ ] **Step 1: Run every module's self-test in dependency order**

```bash
cd /home/setya/Documents/ME/liveit/midlab && \
  python3 lib/models.py && \
  python3 protocols/cobas_c111/constants.py && \
  python3 protocols/cobas_c111/parser.py && \
  python3 protocols/cobas_c111/module.py && \
  python3 protocols/base.py && \
  python3 protocols/astm/module.py
```

Expected: each command finishes with its `=== ... PASSED ===` line and exit code 0. The ASTM module run at the end confirms our additive `comments` field hasn't broken anything upstream.

- [ ] **Step 2: Confirm `/api/protocols` would surface the new option**

Run: `cd /home/setya/Documents/ME/liveit/midlab && grep -n "_PROTOCOL_REGISTRY\|/api/protocols" services/web_console/api.py protocols/base.py | head -20`

Verify: the registry contains COBAS_C111 and Web Console's `/api/protocols` endpoint reads from it (it should — that's why no Web Console code change is needed). If the endpoint hardcodes a list instead of reading the registry, this would need a follow-up — but at the time of writing the spec was based on the registry being the single source of truth.

- [ ] **Step 3: Print a "Done" checklist for the operator**

Once the previous steps pass, you're done with implementation. Operator next steps (NOT part of this plan, but worth communicating):

1. In Web Console → Instruments → Add: choose `COBAS_C111`, `unidirectional`, set IP/port matching the RS232-to-LAN converter, `connection=server` or `client` per converter setting.
2. Restart TCP socket service for that instrument (`systemctl restart midlab-tcp@<id>`).
3. Run ASTM Simulator from manual Appendix C, or trigger a result upload from the actual c-111 unit.
4. Watch `tcp_<id>.log` for "ENQ diterima ... EOT diterima ... Parse selesai: N results".
5. Verify a new row appears in `tbl_result` with `send_status='pending'` and a JSON body matching the parsed result.

- [ ] **Step 4: No commit needed for this task.**

---

## Self-Review

After completing all 12 tasks, walk through this list with fresh eyes. **This is a checklist you run; if something is off, fix inline.**

**1. Spec coverage** — for each requirement in the spec, point to a task:

| Spec requirement | Task |
|---|---|
| Standalone module `protocols/cobas_c111/` | Tasks 2-10 |
| Lower-layer E1381 handshake (ENQ/ACK/EOT) | Task 11 (reuse existing receiver) |
| Upper-layer E1394 parse of H/P/O/R/C/L | Tasks 6, 7, 8 |
| Roche M.CR and M.RR parse | Task 9 |
| Escape sequence decoding (&F&/&S&/&R&/&E&) | Task 5 |
| Checksum validation | Task 4 |
| `ResultObject.comments` field added | Task 1 |
| C records → ResultObject.comments[] with attribution | Task 10 |
| M.CR / M.RR drop raw payload, summary only | Task 9 |
| Test code stored as-is (no mapping) | Task 8 (no mapping logic added) |
| Flag stored as-is (no priority interpretation) | Task 8 (flag kept verbatim) |
| `is_enq` returns True only on ENQ byte (no Q-record) | Task 10 |
| Bidirectional methods raise NotImplementedError | Task 10 |
| `protocols/base.py` registry entry | Task 11 |
| `services/tcp_socket/receiver.py` dispatcher branch | Task 11 |
| QC/calibration uniform with patient results | Task 10 (no filter in parse loop) |
| No changes to result_sender, order_receiver, web_console | None of the tasks modify those |
| Manual integration verification | Task 12 step 3 (documentation, not code) |

If anything in the spec lacks a task — add a task before declaring the plan complete.

**2. Placeholder scan** — search the plan for "TBD", "TODO", "implement later", "Add appropriate error handling", "Similar to Task N". None should appear in this document. (The doc was authored to avoid these.)

**3. Type / identifier consistency** — verify across tasks:
- Class name `CobasC111Module` used everywhere (Tasks 10, 11).
- Registry key `"COBAS_C111"` (uppercase underscore) — Tasks 2, 10, 11.
- Module path `protocols.cobas_c111.module` — Task 11.
- `FrameDecoder.split_frames()`, `compute_checksum()`, `validate_checksum()`, `decode_escapes()`, `decode()` — names introduced in Tasks 3-5 are used consistently in Tasks 6-10.
- `RecordParser.parse_h/p/o/r/c/l/m`, plus `parse_m_cr`, `parse_m_rr` — Tasks 6-9 then consumed in Task 10.
- `_split_fields` and `_component` helpers — defined in Task 6, used by 7/8/9.

**4. No work duplicated** — `lib/models.py` only touched in Task 1. `protocols/base.py` and `receiver.py` only touched in Task 11. Tasks 3-9 only touch `protocols/cobas_c111/parser.py`. Task 10 only creates `module.py`.

If you find an issue, fix it in the plan inline before starting execution.
