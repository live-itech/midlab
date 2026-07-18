# Tapping Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Menu untuk menangkap komunikasi mentah dari alat lab yang belum punya driver, lengkap dengan handshake responder deterministik dan export ke fixture test.

**Architecture:** Dua sumbu bebas — **transport** (TCP server/client, serial) dan **responder** (ASTM, MLLP, RAW) — dirangkai oleh `TapSession`. Responder sinkron dan bebas I/O (teruji tanpa jaringan); transport async. Setiap byte ditulis ke JSONL **sebelum** balasan dikirim. Metadata sesi di `tbl_tap_session`, aliran byte di file.

**Tech Stack:** Python 3.10+, asyncio, FastAPI, SQLAlchemy, MySQL, pyserial 3.5 (sudah terpasang), SSE.

Spec: `docs/superpowers/specs/2026-07-17-tapping-data-design.md`

## Global Constraints

- Komentar dan pesan log boleh/utamakan Bahasa Indonesia (CLAUDE.md rule 7).
- Log format: `[LEVEL] [SERVICE] [INSTRUMENT] pesan`; pakai `lib.utils.get_logger` (CLAUDE.md rule 5, 6).
- Setiap service handle `SIGTERM` gracefully (CLAUDE.md rule 1).
- DB ops dalam `try/except` (CLAUDE.md rule 2).
- UI web console memakai apple style (CLAUDE.md rule 8).
- **Tidak ada dependensi baru.** `pyserial 3.5` sudah ada; `anthropic` TIDAK dipakai.
- **Rekam-sebelum-balas** — urutannya mengikat di `TapSession`, jangan dibalik.
- Test suite dijalankan dengan `python3 -m pytest -q` polos (baseline saat ini: 163 passed, 6 skipped).
- Byte kontrol di-reuse dari `protocols/astm/constants.py` (`ENQ=0x05`, `ACK=0x06`, `NAK=0x15`, `EOT=0x04`, `STX=0x02`, `ETX=0x03`, `ETB=0x17`, `CR=0x0D`, `LF=0x0A`) dan `protocols/hl7/constants.py` (`MLLP_START_BYTE`, `MLLP_END_BYTE`, `MLLP_TRAILER`). Jangan definisikan ulang.

## File Structure

**Dibuat:**

| File | Tanggung jawab |
|---|---|
| `services/tap/__init__.py` | Package marker |
| `services/tap/responder/base.py` | `BaseResponder` ABC |
| `services/tap/responder/raw.py` | `RawResponder` — pasif |
| `services/tap/responder/astm.py` | `AstmResponder` — ENQ/ACK/frame/EOT |
| `services/tap/responder/mllp.py` | `MllpResponder` — MLLP + echo MSH-10 |
| `services/tap/detect.py` | Deteksi protokol + hint baud rate |
| `services/tap/recorder.py` | `TapRecorder` — tulis JSONL |
| `services/tap/export.py` | `to_python_bytes()`, `rx_bytes()` |
| `services/tap/transport/base.py` | `BaseTransport` ABC |
| `services/tap/transport/tcp.py` | `TcpServerTransport`, `TcpClientTransport` |
| `services/tap/transport/serial_port.py` | `SerialTransport` |
| `services/tap/session.py` | `TapSession` — wiring + rekam-sebelum-balas |
| `services/tap/service.py` | `TapService` + CLI + port guard |
| `services/web_console/templates/tap.html` | Halaman UI |
| `scripts/migrate_tap_session.py` | Migrasi `tbl_tap_session` (idempotent) |
| `tests/test_tap_responder.py` | Test responder (tanpa socket) |
| `tests/test_tap_detect.py` | Test deteksi + hint baud |
| `tests/test_tap_recorder.py` | Test JSONL |
| `tests/test_tap_export.py` | Test export |
| `tests/test_tap_transport.py` | Test TCP loopback + serial pty |
| `tests/test_tap_session.py` | Test wiring + urutan rekam-sebelum-balas |
| `tests/test_tap_api.py` | Test endpoint |
| `tests/test_tap_integration.py` | End-to-end pakai `aruma_ar580_test_sender.py` |

**Dimodifikasi:**

| File | Perubahan |
|---|---|
| `lib/db.py` | Tambah model `TblTapSession` |
| `services/web_console/api.py` | Endpoint `/api/tap/*` + route halaman `/tap` |
| `services/web_console/templates/base.html` | Item nav "Tapping" |
| `services/web_console/static/js/app.js` | Logika halaman tap |
| `PANDUAN-ALAT-BARU.md` | Bab tapping data |

---

## Task 1: Responder base + RawResponder

**Files:**
- Create: `services/tap/__init__.py`, `services/tap/responder/__init__.py`, `services/tap/responder/base.py`, `services/tap/responder/raw.py`
- Test: `tests/test_tap_responder.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `BaseResponder.feed(data: bytes) -> list[bytes]` — byte masuk, kembalikan daftar byte yang harus dikirim balik (boleh `[]`)
  - `BaseResponder.messages() -> list[bytes]` — pesan lengkap yang sudah terdeteksi
  - `BaseResponder.NAME: str`
  - `RawResponder()` — tidak pernah membalas

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_responder.py
"""Test responder tapping — semuanya tanpa socket (feed() bebas I/O)."""

import pytest

from services.tap.responder.raw import RawResponder


class TestRawResponder:
    def test_tidak_pernah_membalas(self):
        r = RawResponder()
        assert r.feed(b"\x05") == []
        assert r.feed(b"MSH|^~\\&|X|Y\r") == []
        assert r.feed(b"apa pun") == []

    def test_tidak_mendeteksi_pesan(self):
        # RAW tidak punya konsep batas pesan.
        r = RawResponder()
        r.feed(b"\x0bMSH|abc\r\x1c\x0d")
        assert r.messages() == []

    def test_punya_nama(self):
        assert RawResponder().NAME == "RAW"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_responder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/__init__.py
"""TapService — capture komunikasi mentah alat lab yang belum punya driver."""
```

```python
# services/tap/responder/__init__.py
"""Responder handshake untuk tapping — menjawab di level transport, bukan parsing."""
```

```python
# services/tap/responder/base.py
"""
services/tap/responder/base.py — Kontrak responder tapping.

Responder menjawab handshake di level TRANSPORT, bukan parsing isi. Inilah yang
membuat tapping bisa jalan tanpa driver: ASTM ACK selalu 0x06, HL7 ACK cuma perlu
memantulkan MSH-10. Nol pengetahuan tentang isi pesan.

feed() sengaja sinkron dan bebas I/O — state machine ada di dalamnya, tapi tidak
menyentuh socket, jadi seluruh perilaku responder bisa diuji tanpa jaringan.
"""

from abc import ABC, abstractmethod


class BaseResponder(ABC):
    """Kontrak responder: byte masuk → byte yang harus dibalas."""

    @property
    @abstractmethod
    def NAME(self) -> str:
        """Nama basis protokol: 'ASTM', 'HL7', 'RAW'."""
        ...

    @abstractmethod
    def feed(self, data: bytes) -> list[bytes]:
        """
        Umpankan byte yang diterima dari alat.

        Returns:
            Daftar byte yang harus dikirim balik ke alat. Boleh kosong.
        """
        ...

    @abstractmethod
    def messages(self) -> list[bytes]:
        """Pesan lengkap yang sudah terdeteksi sejauh ini (untuk export per-pesan)."""
        ...
```

```python
# services/tap/responder/raw.py
"""
services/tap/responder/raw.py — Responder pasif.

Untuk protokol di luar ASTM/HL7 yang dioperasikan manual oleh teknisi: MidLab
tidak pernah membalas apa pun sendiri, semua kiriman lewat tombol "kirim manual".
"""

from services.tap.responder.base import BaseResponder


class RawResponder(BaseResponder):
    """Tidak pernah membalas, tidak mengenal batas pesan."""

    NAME = "RAW"

    def feed(self, data: bytes) -> list[bytes]:
        return []

    def messages(self) -> list[bytes]:
        # RAW tidak punya framing, jadi tidak ada konsep "pesan lengkap".
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_responder.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/__init__.py services/tap/responder/ tests/test_tap_responder.py
git commit -m "feat(tap): kontrak BaseResponder + RawResponder pasif"
```

---

## Task 2: AstmResponder

**Files:**
- Create: `services/tap/responder/astm.py`
- Modify: `tests/test_tap_responder.py` (tambah class test)
- Test: `tests/test_tap_responder.py`

**Interfaces:**
- Consumes: `BaseResponder` (Task 1)
- Produces: `AstmResponder()` dengan `NAME = "ASTM"`

**Konteks protokol:** ASTM E1381 — alat kirim `ENQ`(0x05) → LIS balas `ACK`(0x06) → alat kirim frame `STX...ETX/ETB<checksum>CR LF` → LIS balas `ACK` per frame → alat kirim `EOT`(0x04) → sesi selesai. Satu sesi (ENQ..EOT) = satu pesan.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_responder.py — tambahkan di bawah TestRawResponder
from services.tap.responder.astm import AstmResponder

ENQ, ACK, EOT, STX, ETX = b"\x05", b"\x06", b"\x04", b"\x02", b"\x03"


class TestAstmResponder:
    def test_enq_dibalas_ack(self):
        r = AstmResponder()
        assert r.feed(ENQ) == [ACK]

    def test_frame_dibalas_ack(self):
        r = AstmResponder()
        r.feed(ENQ)
        frame = STX + b"1H|\\^&|||Sysmex" + ETX + b"AB\r\n"
        assert r.feed(frame) == [ACK]

    def test_eot_tidak_dibalas(self):
        # EOT mengakhiri sesi; tidak ada balasan.
        r = AstmResponder()
        r.feed(ENQ)
        assert r.feed(EOT) == []

    def test_sesi_lengkap_jadi_satu_pesan(self):
        r = AstmResponder()
        r.feed(ENQ)
        r.feed(STX + b"1H|\\^&|||Sysmex" + ETX + b"AB\r\n")
        r.feed(STX + b"2L|1|N" + ETX + b"CD\r\n")
        r.feed(EOT)
        pesan = r.messages()
        assert len(pesan) == 1
        assert b"Sysmex" in pesan[0]
        assert b"L|1|N" in pesan[0]

    def test_pesan_hanya_muncul_setelah_eot(self):
        r = AstmResponder()
        r.feed(ENQ)
        r.feed(STX + b"1H|\\^&" + ETX + b"AB\r\n")
        assert r.messages() == []  # sesi belum ditutup

    def test_dua_sesi_jadi_dua_pesan(self):
        r = AstmResponder()
        for tag in (b"A", b"B"):
            r.feed(ENQ)
            r.feed(STX + b"1H|" + tag + ETX + b"XX\r\n")
            r.feed(EOT)
        assert len(r.messages()) == 2

    def test_byte_terpecah_antar_chunk(self):
        # TCP tidak menjamin batas frame — satu frame bisa datang terpotong.
        r = AstmResponder()
        r.feed(ENQ)
        assert r.feed(STX + b"1H|\\^&|||Sys") == []   # belum lengkap
        assert r.feed(b"mex" + ETX + b"AB\r\n") == [ACK]

    def test_dua_frame_dalam_satu_chunk(self):
        r = AstmResponder()
        r.feed(ENQ)
        dua = (STX + b"1H|" + ETX + b"AB\r\n") + (STX + b"2L|" + ETX + b"CD\r\n")
        assert r.feed(dua) == [ACK, ACK]

    def test_enq_tanpa_sesi_sebelumnya_tetap_diack(self):
        r = AstmResponder()
        assert r.feed(ENQ) == [ACK]
        assert r.feed(ENQ) == [ACK]

    def test_nama(self):
        assert AstmResponder().NAME == "ASTM"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_responder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.responder.astm'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/responder/astm.py
"""
services/tap/responder/astm.py — Responder handshake ASTM E1381.

Alur: ENQ → ACK, tiap frame → ACK, EOT menutup sesi. Satu sesi (ENQ..EOT) =
satu pesan untuk keperluan export.

Checksum frame TIDAK diverifikasi: tujuan tapping adalah membuat alat mau
mengirim, bukan memvalidasi. Frame rusak tetap di-ACK dan tetap terekam apa
adanya — justru itu yang ingin dilihat operator saat mendiagnosis.
"""

from protocols.astm.constants import ENQ, ACK, EOT, STX, ETX, ETB
from services.tap.responder.base import BaseResponder


ENQ_B = bytes([ENQ])
ACK_B = bytes([ACK])
EOT_B = bytes([EOT])
STX_B = bytes([STX])


class AstmResponder(BaseResponder):
    """Balas ENQ dan tiap frame dengan ACK; kumpulkan sesi jadi pesan."""

    NAME = "ASTM"

    def __init__(self):
        self._buf = bytearray()          # byte yang belum membentuk frame utuh
        self._sesi = bytearray()         # isi frame sesi berjalan
        self._pesan: list[bytes] = []

    def feed(self, data: bytes) -> list[bytes]:
        balasan: list[bytes] = []
        self._buf.extend(data)

        while self._buf:
            b = self._buf[0]

            if b == ENQ:
                self._buf.pop(0)
                self._sesi.clear()
                balasan.append(ACK_B)
                continue

            if b == EOT:
                self._buf.pop(0)
                if self._sesi:
                    self._pesan.append(bytes(self._sesi))
                    self._sesi.clear()
                continue

            if b == STX:
                frame = self._ambil_frame()
                if frame is None:
                    break                # frame belum lengkap, tunggu chunk berikutnya
                self._sesi.extend(frame)
                balasan.append(ACK_B)
                continue

            # Byte di luar framing (mis. sisa CR/LF) — buang, jangan menggantung.
            self._buf.pop(0)

        return balasan

    def _ambil_frame(self) -> bytes | None:
        """
        Ambil satu frame STX..ETX/ETB + checksum + CRLF dari buffer.

        Returns None bila frame belum lengkap (biarkan di buffer).
        """
        for i in range(1, len(self._buf)):
            if self._buf[i] in (ETX, ETB):
                # ETX/ETB + 2 char checksum + CR + LF
                akhir = i + 4
                if len(self._buf) < akhir:
                    return None
                frame = bytes(self._buf[:akhir])
                del self._buf[:akhir]
                return frame
        return None

    def messages(self) -> list[bytes]:
        return list(self._pesan)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_responder.py -q`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/responder/astm.py tests/test_tap_responder.py
git commit -m "feat(tap): AstmResponder — ENQ/frame ACK + sesi jadi pesan"
```

---

## Task 3: MllpResponder

**Files:**
- Create: `services/tap/responder/mllp.py`
- Modify: `tests/test_tap_responder.py`
- Test: `tests/test_tap_responder.py`

**Interfaces:**
- Consumes: `BaseResponder` (Task 1)
- Produces: `MllpResponder()` dengan `NAME = "HL7"`

**Konteks protokol:** Envelope MLLP `<VT>pesan<FS><CR>` agnostik terhadap isi. **Satu-satunya** parsing yang dibutuhkan: MSA-2 wajib memantulkan MSH-10 (field ke-10, dipisah `|`). Alat AR580 kirim ulang dalam 3 detik bila ACK tak diterima.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_responder.py — tambahkan
from services.tap.responder.mllp import MllpResponder

VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"

ORU = (
    VT
    + b"MSH|^~\\&|Genrui|KT-6610|||20170712140022||ORU^R01|1275|P|2.3.1\r"
    + b"OBX|1|NM|^WBC^||0.01|10^9/L|4.00-10.00|L|||F\r"
    + FS + CR
)


class TestMllpResponder:
    def test_ack_memantulkan_control_id(self):
        # Alat kirim ulang dalam 3 detik bila ACK tak diterima (dokumen AR580
        # bab 2.3.1), jadi MSA-2 wajib echo MSH-10 pesan yang diterima.
        r = MllpResponder()
        balasan = r.feed(ORU)
        assert len(balasan) == 1
        assert b"MSA|AA|1275" in balasan[0]

    def test_ack_dibungkus_mllp(self):
        r = MllpResponder()
        ack = r.feed(ORU)[0]
        assert ack.startswith(VT)
        assert ack.endswith(FS + CR)

    def test_ack_bertipe_ack(self):
        r = MllpResponder()
        assert b"ACK" in r.feed(ORU)[0]

    def test_pesan_terkumpul(self):
        r = MllpResponder()
        r.feed(ORU)
        pesan = r.messages()
        assert len(pesan) == 1
        assert pesan[0] == ORU

    def test_byte_terpecah_antar_chunk(self):
        r = MllpResponder()
        assert r.feed(ORU[:20]) == []      # belum ada FS CR
        balasan = r.feed(ORU[20:])
        assert b"MSA|AA|1275" in balasan[0]

    def test_dua_pesan_dalam_satu_chunk(self):
        r = MllpResponder()
        balasan = r.feed(ORU + ORU)
        assert len(balasan) == 2
        assert len(r.messages()) == 2

    def test_pesan_tanpa_msh_tidak_diack(self):
        # Jangan meng-ACK pesan yang tidak bisa diidentifikasi: ACK palsu
        # membuat alat mengira data sudah tersimpan.
        r = MllpResponder()
        assert r.feed(VT + b"bukan hl7 sama sekali" + FS + CR) == []

    def test_pesan_tanpa_msh_tetap_direkam_sebagai_pesan(self):
        r = MllpResponder()
        rusak = VT + b"bukan hl7" + FS + CR
        r.feed(rusak)
        assert r.messages() == [rusak]

    def test_control_id_berbeda_ikut_berubah(self):
        r = MllpResponder()
        lain = ORU.replace(b"|ORU^R01|1275|", b"|ORU^R01|99|")
        assert b"MSA|AA|99" in r.feed(lain)[0]

    def test_pesan_tanpa_wrapper_mllp_diabaikan_sampai_ada_fs(self):
        r = MllpResponder()
        assert r.feed(b"MSH|^~\\&|X|Y\r") == []

    def test_nama(self):
        assert MllpResponder().NAME == "HL7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_responder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.responder.mllp'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/responder/mllp.py
"""
services/tap/responder/mllp.py — Responder handshake HL7 di atas MLLP.

Envelope <VT>pesan<FS><CR> agnostik terhadap isi. Satu-satunya parsing yang
dibutuhkan: MSA-2 wajib memantulkan MSH-10 — pecah MSH per '|', ambil field
ke-10. Itu saja; tidak ada pengetahuan tentang OBX, PID, atau alatnya.

Pesan tanpa MSH tidak di-ACK: ACK palsu membuat alat mengira datanya sudah
tersimpan, padahal MidLab tidak bisa mengidentifikasinya.
"""

from datetime import datetime

from protocols.hl7.constants import MLLP_START_BYTE, MLLP_TRAILER
from services.tap.responder.base import BaseResponder


class MllpResponder(BaseResponder):
    """Balas tiap pesan MLLP dengan ACK yang memantulkan MSH-10."""

    NAME = "HL7"

    def __init__(self, timestamp: str | None = None):
        # timestamp: override untuk unit test agar hasilnya deterministik.
        self._buf = bytearray()
        self._pesan: list[bytes] = []
        self._timestamp = timestamp

    def feed(self, data: bytes) -> list[bytes]:
        balasan: list[bytes] = []
        self._buf.extend(data)

        while True:
            akhir = self._buf.find(MLLP_TRAILER)
            if akhir == -1:
                break
            pesan = bytes(self._buf[: akhir + len(MLLP_TRAILER)])
            del self._buf[: akhir + len(MLLP_TRAILER)]

            self._pesan.append(pesan)
            ack = self._bangun_ack(pesan)
            if ack:
                balasan.append(ack)

        return balasan

    def _bangun_ack(self, pesan: bytes) -> bytes | None:
        teks = pesan.strip(MLLP_START_BYTE).rstrip(MLLP_TRAILER).decode(
            "utf-8", errors="replace"
        )
        msh = next((s for s in teks.split("\r") if s.startswith("MSH")), None)
        if msh is None:
            return None

        f = msh.split("|")
        control_id = f[9] if len(f) > 9 else ""
        stempel = self._timestamp or datetime.now().strftime("%Y%m%d%H%M%S")

        badan = (
            f"MSH|^~\\&|MidLab|TAP|||{stempel}||ACK|{control_id}|P|2.3.1\r"
            f"MSA|AA|{control_id}\r"
        )
        return MLLP_START_BYTE + badan.encode("utf-8") + MLLP_TRAILER

    def messages(self) -> list[bytes]:
        return list(self._pesan)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_responder.py -q`
Expected: PASS (24 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/responder/mllp.py tests/test_tap_responder.py
git commit -m "feat(tap): MllpResponder — ACK dengan echo MSH-10"
```

---

## Task 4: Deteksi protokol + hint baud rate

**Files:**
- Create: `services/tap/detect.py`
- Test: `tests/test_tap_detect.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `detect_protocol(data: bytes) -> str | None` — `"ASTM"` | `"HL7"` | `None`
  - `should_hint_baud(basis: str, bytes_seen: int, messages_found: int) -> bool`
  - `is_query(message: bytes, basis: str) -> bool`
  - `BAUD_HINT_THRESHOLD: int = 256`
  - `build_responder(basis: str) -> BaseResponder`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_detect.py
"""Test deteksi protokol + hint baud rate."""

import pytest

from services.tap.detect import (
    detect_protocol, should_hint_baud, is_query, build_responder,
    BAUD_HINT_THRESHOLD,
)
from services.tap.responder.astm import AstmResponder
from services.tap.responder.mllp import MllpResponder
from services.tap.responder.raw import RawResponder


class TestDetectProtocol:
    def test_enq_pertama_berarti_astm(self):
        # Heuristik dari PANDUAN-ALAT-BARU.md bab 2.
        assert detect_protocol(b"\x05") == "ASTM"

    def test_vt_pertama_berarti_hl7(self):
        assert detect_protocol(b"\x0bMSH|^~\\&|X") == "HL7"

    def test_string_msh_berarti_hl7(self):
        # Tanpa wrapper MLLP — bab 1.3 dokumen AR580 hanya menyebut "Block is
        # HL7 message", jadi pesan telanjang mungkin.
        assert detect_protocol(b"MSH|^~\\&|Genrui|KT-6610") == "HL7"

    def test_data_tak_dikenal_none(self):
        assert detect_protocol(b"\xff\xfe\x00\x01") is None

    def test_kosong_none(self):
        assert detect_protocol(b"") is None

    def test_astm_menang_atas_msh_di_belakang(self):
        # ENQ di byte pertama lebih kuat daripada string MSH di tengah data.
        assert detect_protocol(b"\x05MSH|") == "ASTM"


class TestHintBaud:
    def test_hint_saat_banyak_byte_tanpa_pesan(self):
        # Salah setel baud menghasilkan byte sampah yang mirip masalah protokol.
        assert should_hint_baud("HL7", bytes_seen=300, messages_found=0) is True

    def test_tidak_hint_bila_ada_pesan(self):
        assert should_hint_baud("HL7", bytes_seen=300, messages_found=1) is False

    def test_tidak_hint_bila_byte_masih_sedikit(self):
        assert should_hint_baud("HL7", bytes_seen=10, messages_found=0) is False

    def test_tidak_hint_untuk_raw(self):
        # RAW memang tidak punya konsep pesan — nol pesan itu normal.
        assert should_hint_baud("RAW", bytes_seen=9999, messages_found=0) is False

    def test_ambang_tepat_di_batas(self):
        assert should_hint_baud("ASTM", BAUD_HINT_THRESHOLD - 1, 0) is False
        assert should_hint_baud("ASTM", BAUD_HINT_THRESHOLD, 0) is True


class TestIsQuery:
    def test_hl7_qbp_adalah_query(self):
        m = b"\x0bMSH|^~\\&|A|B|||1||QBP^Q22|5|P|2.5\rQPD|Q22|1|34567\r\x1c\x0d"
        assert is_query(m, "HL7") is True

    def test_hl7_qry_adalah_query(self):
        m = b"\x0bMSH|^~\\&|Mindray|BS-200E|||1||QRY^Q02|5|P|2.3.1\r\x1c\x0d"
        assert is_query(m, "HL7") is True

    def test_hl7_oru_bukan_query(self):
        m = b"\x0bMSH|^~\\&|Genrui|KT-6610|||1||ORU^R01|1275|P|2.3.1\r\x1c\x0d"
        assert is_query(m, "HL7") is False

    def test_astm_record_q_adalah_query(self):
        m = b"\x021H|\\^&\x03AB\r\n\x022Q|1|^12345||ALL||||||||O\x03CD\r\n"
        assert is_query(m, "ASTM") is True

    def test_astm_tanpa_record_q_bukan_query(self):
        m = b"\x021H|\\^&\x03AB\r\n\x022R|1|^^^Na|140|mmol/L\x03CD\r\n"
        assert is_query(m, "ASTM") is False

    def test_raw_tidak_pernah_query(self):
        # RAW tidak punya konsep record/pesan, jadi tidak bisa disimpulkan.
        assert is_query(b"apa pun", "RAW") is False

    def test_pesan_kosong(self):
        assert is_query(b"", "HL7") is False


class TestBuildResponder:
    @pytest.mark.parametrize("basis,kelas", [
        ("ASTM", AstmResponder),
        ("HL7", MllpResponder),
        ("RAW", RawResponder),
    ])
    def test_membangun_responder_sesuai_basis(self, basis, kelas):
        assert isinstance(build_responder(basis), kelas)

    def test_basis_tak_dikenal_ditolak(self):
        with pytest.raises(ValueError, match="tidak dikenali"):
            build_responder("SOAP")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_detect.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.detect'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/detect.py
"""
services/tap/detect.py — Deteksi protokol + hint baud rate.

Heuristik deteksi diambil langsung dari PANDUAN-ALAT-BARU.md bab 2, yang selama
ini dijalankan manual lewat Wireshark:
  - byte pertama 0x05 (ENQ)  → kemungkinan ASTM
  - ada string "MSH|"        → kemungkinan HL7

Deteksi hanya MENYARANKAN; operator yang memutuskan. Responder tidak pernah
berganti sendiri di tengah sesi.
"""

from protocols.astm.constants import ENQ
from protocols.hl7.constants import MLLP_START
from services.tap.responder.base import BaseResponder
from services.tap.responder.astm import AstmResponder
from services.tap.responder.mllp import MllpResponder
from services.tap.responder.raw import RawResponder


# Setelah sekian byte tanpa satu pun pesan valid, curigai setelan baud.
BAUD_HINT_THRESHOLD = 256

_RESPONDERS = {
    "ASTM": AstmResponder,
    "HL7": MllpResponder,
    "RAW": RawResponder,
}


def detect_protocol(data: bytes) -> str | None:
    """
    Tebak basis protokol dari byte awal.

    Returns:
        'ASTM', 'HL7', atau None bila tidak dikenali.
    """
    if not data:
        return None
    if data[0] == ENQ:
        return "ASTM"
    if data[0] == MLLP_START:
        return "HL7"
    if b"MSH|" in data:
        return "HL7"
    return None


def should_hint_baud(basis: str, bytes_seen: int, messages_found: int) -> bool:
    """
    True bila operator perlu diingatkan memeriksa baud rate.

    Salah setel baud menghasilkan byte sampah yang persis mirip masalah
    protokol — jebakan klasik yang memakan waktu berjam-jam. Bila basisnya
    ASTM/HL7 tapi tidak satu pun frame terbentuk setelah cukup banyak byte,
    setelan serial-nya yang lebih mungkin salah, bukan alatnya.

    RAW dikecualikan: ia memang tidak punya konsep pesan.
    """
    if basis == "RAW":
        return False
    return bytes_seen >= BAUD_HINT_THRESHOLD and messages_found == 0


def is_query(message: bytes, basis: str) -> bool:
    """
    True bila pesan ini alat MEMINTA order (bukan mengirim hasil).

    Dipakai mode `bidi` untuk menandai query di capture. MidLab sengaja TIDAK
    menjawabnya: bentuk jawaban berbeda per protokol dan per tipe query, dan
    mengarangnya bisa membuat alat mencatat error — mengotori capture yang justru
    sedang diamati. Yang dikejar adalah format query-nya.

    HL7  : MSH-9 bertipe QBP / QRY
    ASTM : ada record Q dalam sesi
    """
    if not message or basis == "RAW":
        return False

    if basis == "HL7":
        teks = message.decode("utf-8", errors="replace")
        msh = next((s for s in teks.split("\r") if s.startswith("MSH")), None)
        if msh is None:
            return False
        f = msh.split("|")
        tipe = f[8] if len(f) > 8 else ""
        return tipe.startswith("QBP") or tipe.startswith("QRY")

    if basis == "ASTM":
        # Record ASTM: <seq-digit><tipe>|... — cari record bertipe Q.
        teks = message.decode("ascii", errors="replace")
        for baris in teks.replace("\x02", "\n").split("\n"):
            b = baris.strip()
            if len(b) >= 2 and b[0].isdigit() and b[1] == "Q":
                return True
        return False

    return False


def build_responder(basis: str) -> BaseResponder:
    """Buat responder sesuai basis protokol."""
    kelas = _RESPONDERS.get(basis)
    if kelas is None:
        raise ValueError(
            f"Basis protokol '{basis}' tidak dikenali. "
            f"Tersedia: {list(_RESPONDERS)}"
        )
    return kelas()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_detect.py -q`
Expected: PASS (22 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/detect.py tests/test_tap_detect.py
git commit -m "feat(tap): deteksi protokol + hint baud rate + factory responder"
```

---

## Task 5: TapRecorder (JSONL)

**Files:**
- Create: `services/tap/recorder.py`
- Test: `tests/test_tap_recorder.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `TapRecorder(path: str)` — context manager
  - `.write_event(direction: str, data: bytes, note: str = "") -> None` — `direction`: `"rx"` | `"tx"`
  - `.mark_message(index: int) -> None`
  - `.mark_query(index: int) -> None`
  - `.close() -> None`
  - `read_events(path: str) -> list[dict]`
  - `TAP_LOG_DIR: str = "/var/log/midlab/tap"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_recorder.py
"""Test TapRecorder — JSONL, hex, flush-per-event."""

import json

import pytest

from services.tap.recorder import TapRecorder, read_events


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "sesi.jsonl")


class TestTapRecorder:
    def test_menulis_event_rx(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"\x0bMSH|")
        ev = read_events(path)
        assert len(ev) == 1
        assert ev[0]["dir"] == "rx"
        assert ev[0]["hex"] == "0b4d53487c"

    def test_hex_bukan_base64(self, path):
        # Hex supaya bisa dibaca mata dan di-grep langsung.
        with TapRecorder(path) as r:
            r.write_event("tx", b"\x06")
        assert read_events(path)[0]["hex"] == "06"

    def test_event_punya_timestamp_iso(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"x")
        t = read_events(path)[0]["t"]
        assert "T" in t and len(t) >= 19

    def test_note_ikut_tersimpan(self, path):
        with TapRecorder(path) as r:
            r.write_event("tx", b"\x06", note="ACK")
        assert read_events(path)[0]["note"] == "ACK"

    def test_note_kosong_tidak_ditulis(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"x")
        assert "note" not in read_events(path)[0]

    def test_mark_message(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"x")
            r.mark_message(0)
        ev = read_events(path)
        assert ev[1] == {**ev[1], "dir": "meta", "event": "message_complete", "index": 0}

    def test_mark_query(self, path):
        with TapRecorder(path) as r:
            r.mark_query(3)
        ev = read_events(path)
        assert ev[0]["event"] == "query_detected"
        assert ev[0]["index"] == 3
        assert ev[0]["dir"] == "meta"

    def test_event_kosong_tidak_ditulis(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"")
        assert read_events(path) == []

    def test_ter_flush_sebelum_close(self, path):
        # Inti pengaman: byte harus di disk SEBELUM ACK dikirim. Kalau proses
        # mati setelah ACK tapi sebelum tulisan, hasil pasien hilang diam-diam.
        r = TapRecorder(path)
        r.write_event("rx", b"penting")
        # Belum close — tapi harus sudah terbaca dari file.
        assert len(read_events(path)) == 1
        r.close()

    def test_urutan_event_terjaga(self, path):
        with TapRecorder(path) as r:
            for i in range(20):
                r.write_event("rx", bytes([i]))
        ev = read_events(path)
        assert [e["hex"] for e in ev] == [f"{i:02x}" for i in range(20)]

    def test_direction_tidak_valid_ditolak(self, path):
        with TapRecorder(path) as r:
            with pytest.raises(ValueError, match="direction"):
                r.write_event("sideways", b"x")

    def test_direktori_dibuat_otomatis(self, tmp_path):
        p = str(tmp_path / "belum" / "ada" / "sesi.jsonl")
        with TapRecorder(p) as r:
            r.write_event("rx", b"x")
        assert len(read_events(p)) == 1


class TestReadEvents:
    def test_file_tidak_ada_list_kosong(self, tmp_path):
        assert read_events(str(tmp_path / "hilang.jsonl")) == []

    def test_baris_rusak_dilewati(self, path):
        with open(path, "w") as f:
            f.write(json.dumps({"t": "x", "dir": "rx", "hex": "06"}) + "\n")
            f.write("{ bukan json\n")
            f.write(json.dumps({"t": "y", "dir": "tx", "hex": "05"}) + "\n")
        ev = read_events(path)
        assert len(ev) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_recorder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.recorder'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/recorder.py
"""
services/tap/recorder.py — Perekam sesi tapping ke JSONL.

Byte disimpan HEX, bukan base64: bisa dibaca mata dan di-grep langsung.

Setiap event di-flush segera. Ini bukan optimasi yang kelewat — ini pengaman:
bila MidLab meng-ACK, alat menganggap datanya aman dan tidak mengirim ulang.
Kalau proses mati setelah ACK terkirim tapi sebelum tulisan mendarat, hasil
pasien itu lenyap tanpa jejak. flush() bertahan terhadap crash proses; kehilangan
event terakhir saat listrik mati diterima sebagai batas yang wajar.
"""

import json
import os
from datetime import datetime

from lib.utils import get_logger


logger = get_logger("tap_recorder")

TAP_LOG_DIR = "/var/log/midlab/tap"

_ARAH_VALID = ("rx", "tx", "meta")


class TapRecorder:
    """Tulis event sesi tapping ke file JSONL, satu event per baris."""

    def __init__(self, path: str):
        self._path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")

    def __enter__(self) -> "TapRecorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def write_event(self, direction: str, data: bytes, note: str = "") -> None:
        """Rekam byte yang mengalir. Dipanggil SEBELUM balasan dikirim."""
        if direction not in _ARAH_VALID:
            raise ValueError(
                f"direction '{direction}' tidak valid, harus salah satu dari {_ARAH_VALID}"
            )
        if not data:
            return
        row = {"t": self._now(), "dir": direction, "hex": data.hex()}
        if note:
            row["note"] = note
        self._tulis(row)

    def mark_message(self, index: int) -> None:
        """Tandai bahwa satu pesan lengkap sudah terdeteksi."""
        self._tulis({
            "t": self._now(), "dir": "meta",
            "event": "message_complete", "index": index,
        })

    def mark_query(self, index: int) -> None:
        """
        Tandai pesan ke-index sebagai query (alat meminta order).

        Hanya penanda — MidLab sengaja tidak menjawabnya. Lihat detect.is_query().
        """
        self._tulis({
            "t": self._now(), "dir": "meta",
            "event": "query_detected", "index": index,
        })

    def _tulis(self, row: dict) -> None:
        self._f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._f.flush()      # pengaman: di disk sebelum balasan dikirim

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def close(self) -> None:
        try:
            self._f.close()
        except OSError as e:
            logger.warning(f"Gagal menutup recorder {self._path}: {e}")


def read_events(path: str) -> list[dict]:
    """
    Baca semua event dari file JSONL.

    Baris rusak dilewati, bukan melempar: sesi yang terpotong (proses dibunuh
    saat menulis) tetap harus bisa dibaca sampai baris terakhir yang utuh.
    """
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for baris in f:
            baris = baris.strip()
            if not baris:
                continue
            try:
                out.append(json.loads(baris))
            except json.JSONDecodeError:
                logger.warning(f"Baris JSONL rusak dilewati di {path}")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_recorder.py -q`
Expected: PASS (14 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/recorder.py tests/test_tap_recorder.py
git commit -m "feat(tap): TapRecorder — JSONL hex dengan flush per event"
```

---

## Task 6: Export (.bin + Python bytes literal)

**Files:**
- Create: `services/tap/export.py`
- Test: `tests/test_tap_export.py`

**Interfaces:**
- Consumes: `read_events()` (Task 5)
- Produces:
  - `to_python_bytes(data: bytes, indent: str = "    ") -> str`
  - `rx_bytes(events: list[dict]) -> bytes`
  - `messages_from_events(events: list[dict]) -> list[bytes]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_export.py
"""Test export — .bin dan Python bytes literal."""

from services.tap.export import to_python_bytes, rx_bytes, messages_from_events


ORU = (
    b"\x0b"
    b"MSH|^~\\&|Genrui|KT-6610|||20170712140022||ORU^R01|1275|P|2.3.1\r"
    b"OBX|1|NM|^WBC^||0.01|10^9/L|4.00-10.00|L|||F\r"
    b"\x1c\x0d"
)


class TestToPythonBytes:
    def test_hasil_eval_identik_dengan_asli(self):
        # Ini SATU-SATUNYA jaminan yang penting: literal yang di-export, saat
        # ditempel ke test, harus menghasilkan byte yang persis sama.
        assert eval(to_python_bytes(ORU).strip()) == ORU

    def test_eval_identik_untuk_byte_biner_penuh(self):
        semua = bytes(range(256))
        assert eval(to_python_bytes(semua).strip()) == semua

    def test_dipecah_per_segment_cr(self):
        hasil = to_python_bytes(ORU)
        assert hasil.count("\n") >= 2  # MSH, OBX, trailer di baris terpisah

    def test_byte_kontrol_jadi_escape_hex(self):
        assert "\\x0b" in to_python_bytes(ORU)

    def test_backslash_di_escape(self):
        # MSH|^~\& — backslash HL7 harus jadi \\ di literal Python.
        assert eval(to_python_bytes(b"MSH|^~\\&|X\r").strip()) == b"MSH|^~\\&|X\r"

    def test_petik_ganda_di_escape(self):
        assert eval(to_python_bytes(b'ada "petik" di sini').strip()) == b'ada "petik" di sini'

    def test_data_kosong(self):
        assert to_python_bytes(b"") == ""

    def test_indent_dipakai(self):
        assert to_python_bytes(b"abc\r", indent="  ").startswith("  b\"")


class TestRxBytes:
    def test_hanya_mengambil_rx(self):
        ev = [
            {"dir": "rx", "hex": "0b4d5348"},
            {"dir": "tx", "hex": "06"},
            {"dir": "rx", "hex": "1c0d"},
        ]
        assert rx_bytes(ev) == b"\x0bMSH\x1c\x0d"

    def test_meta_diabaikan(self):
        ev = [
            {"dir": "rx", "hex": "05"},
            {"dir": "meta", "event": "message_complete", "index": 0},
        ]
        assert rx_bytes(ev) == b"\x05"

    def test_kosong(self):
        assert rx_bytes([]) == b""


class TestMessagesFromEvents:
    def test_memecah_rx_pada_penanda_pesan(self):
        ev = [
            {"dir": "rx", "hex": "0b41"},                          # \x0bA
            {"dir": "meta", "event": "message_complete", "index": 0},
            {"dir": "rx", "hex": "0b42"},                          # \x0bB
            {"dir": "meta", "event": "message_complete", "index": 1},
        ]
        assert messages_from_events(ev) == [b"\x0bA", b"\x0bB"]

    def test_tanpa_penanda_list_kosong(self):
        # Basis RAW tidak punya batas pesan — export per-pesan tidak tersedia.
        assert messages_from_events([{"dir": "rx", "hex": "0b41"}]) == []

    def test_rx_setelah_penanda_terakhir_diabaikan(self):
        ev = [
            {"dir": "rx", "hex": "0b41"},
            {"dir": "meta", "event": "message_complete", "index": 0},
            {"dir": "rx", "hex": "0b42"},   # pesan belum selesai
        ]
        assert messages_from_events(ev) == [b"\x0bA"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_export.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.export'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/export.py
"""
services/tap/export.py — Export hasil tapping.

Penutup lingkaran ke SOP: hasil tap → fixture test → driver. Test driver di repo
ini semuanya berjangkar pada byte string verbatim (lihat ORU_DOC di
tests/test_aruma_ar580.py). Saat menulis driver AR580, transkripsi manual dari
PDF menghasilkan kesalahan hitung pipa di segment OBR yang baru tertangkap oleh
test — export otomatis menghapus seluruh kelas kesalahan itu.
"""


def _literal(chunk: bytes) -> str:
    """Satu potong byte → satu literal b"..." Python."""
    out = []
    for b in chunk:
        if b == 0x5C:            # backslash
            out.append("\\\\")
        elif b == 0x22:          # petik ganda
            out.append('\\"')
        elif 0x20 <= b < 0x7F:   # ASCII printable
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return 'b"' + "".join(out) + '"'


def to_python_bytes(data: bytes, indent: str = "    ") -> str:
    """
    Format byte jadi literal Python siap tempel ke file test.

    Dipecah setelah tiap CR supaya tiap segment HL7/ASTM berada di barisnya
    sendiri — sama seperti fixture yang sudah ada di repo.
    """
    if not data:
        return ""

    potongan: list[bytes] = []
    cur = bytearray()
    for b in data:
        cur.append(b)
        if b == 0x0D:            # CR — akhir segment
            potongan.append(bytes(cur))
            cur = bytearray()
    if cur:
        potongan.append(bytes(cur))

    return "\n".join(f"{indent}{_literal(p)}" for p in potongan)


def rx_bytes(events: list[dict]) -> bytes:
    """Gabungkan seluruh byte arah RX — yang dikirim ALAT, untuk di-parse ulang."""
    out = bytearray()
    for e in events:
        if e.get("dir") == "rx":
            out.extend(bytes.fromhex(e["hex"]))
    return bytes(out)


def messages_from_events(events: list[dict]) -> list[bytes]:
    """
    Pecah aliran RX jadi pesan-pesan, berdasar penanda `message_complete`.

    Basis RAW tidak menghasilkan penanda apa pun (tidak ada framing), jadi
    fungsi ini mengembalikan list kosong — export per-pesan memang tidak
    tersedia di sana.
    """
    pesan: list[bytes] = []
    cur = bytearray()
    for e in events:
        if e.get("dir") == "rx":
            cur.extend(bytes.fromhex(e["hex"]))
        elif e.get("dir") == "meta" and e.get("event") == "message_complete":
            pesan.append(bytes(cur))
            cur = bytearray()
    return pesan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_export.py -q`
Expected: PASS (14 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/export.py tests/test_tap_export.py
git commit -m "feat(tap): export .bin + Python bytes literal untuk fixture test"
```

---

## Task 7: Transport base + TCP

**Files:**
- Create: `services/tap/transport/__init__.py`, `services/tap/transport/base.py`, `services/tap/transport/tcp.py`
- Test: `tests/test_tap_transport.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `BaseTransport.open() -> None` (async), `.read() -> bytes` (async), `.write(data: bytes) -> None` (async), `.close() -> None` (async)
  - `BaseTransport.description: str` — untuk `tbl_tap_session.target`
  - `BaseTransport.is_stream: bool` — `True` bila `read()` mengembalikan `b""` HANYA saat koneksi putus (TCP); `False` bila `b""` cuma berarti "belum ada data" (serial)
  - `TcpServerTransport(host: str, port: int)` — `is_stream = True`
  - `TcpClientTransport(host: str, port: int)` — `is_stream = True`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_transport.py
"""Test transport tapping — TCP loopback + serial lewat pty."""

import asyncio

import pytest

from services.tap.transport.tcp import TcpServerTransport, TcpClientTransport


class TestTcpServerTransport:
    @pytest.mark.asyncio
    async def test_menerima_koneksi_dan_membaca(self):
        t = TcpServerTransport("127.0.0.1", 0)
        await t.open()

        async def alat():
            r, w = await asyncio.open_connection("127.0.0.1", t.port)
            w.write(b"\x0bMSH|halo")
            await w.drain()
            w.close()

        tugas = asyncio.create_task(alat())
        data = await asyncio.wait_for(t.read(), timeout=5)
        assert data == b"\x0bMSH|halo"
        await tugas
        await t.close()

    @pytest.mark.asyncio
    async def test_menulis_balik_ke_alat(self):
        t = TcpServerTransport("127.0.0.1", 0)
        await t.open()
        diterima = []

        async def alat():
            r, w = await asyncio.open_connection("127.0.0.1", t.port)
            w.write(b"x")
            await w.drain()
            diterima.append(await r.read(10))
            w.close()

        tugas = asyncio.create_task(alat())
        await asyncio.wait_for(t.read(), timeout=5)
        await t.write(b"\x06")
        await asyncio.wait_for(tugas, timeout=5)
        assert diterima == [b"\x06"]
        await t.close()

    @pytest.mark.asyncio
    async def test_read_kosong_saat_alat_putus(self):
        t = TcpServerTransport("127.0.0.1", 0)
        await t.open()

        async def alat():
            r, w = await asyncio.open_connection("127.0.0.1", t.port)
            w.write(b"x")
            await w.drain()
            w.close()

        asyncio.create_task(alat())
        await asyncio.wait_for(t.read(), timeout=5)
        assert await asyncio.wait_for(t.read(), timeout=5) == b""
        await t.close()

    @pytest.mark.asyncio
    async def test_description(self):
        t = TcpServerTransport("0.0.0.0", 2600)
        assert t.description == "tcp-server 0.0.0.0:2600"


class TestTcpClientTransport:
    @pytest.mark.asyncio
    async def test_connect_dan_baca(self):
        pesan = b"\x05"
        server = await asyncio.start_server(
            lambda r, w: (w.write(pesan), w.close()), "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]

        t = TcpClientTransport("127.0.0.1", port)
        await t.open()
        assert await asyncio.wait_for(t.read(), timeout=5) == pesan
        await t.close()
        server.close()

    @pytest.mark.asyncio
    async def test_description(self):
        assert TcpClientTransport("10.0.0.5", 9100).description == "tcp-client 10.0.0.5:9100"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.transport'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/transport/__init__.py
"""Transport tapping — TCP (server/client) dan serial."""
```

```python
# services/tap/transport/base.py
"""
services/tap/transport/base.py — Kontrak transport tapping.

Transport hanya mengurus ALIRAN byte; ia tidak tahu apa-apa soal protokol.
Pemisahan ini yang membuat 3 transport × 3 responder bisa dikombinasikan bebas
dan diuji terpisah.
"""

from abc import ABC, abstractmethod


class BaseTransport(ABC):
    """Kontrak transport: buka, baca, tulis, tutup."""

    #: True bila read() mengembalikan b"" HANYA saat koneksi putus (TCP).
    #: False bila b"" cuma berarti "belum ada data" dan loop harus lanjut
    #: (serial — port tetap terbuka meski alat sedang diam).
    is_stream: bool = True

    @property
    @abstractmethod
    def description(self) -> str:
        """Deskripsi singkat untuk tbl_tap_session.target."""
        ...

    @abstractmethod
    async def open(self) -> None:
        """Siapkan koneksi (listen / connect / buka port serial)."""
        ...

    @abstractmethod
    async def read(self) -> bytes:
        """
        Baca byte berikutnya.

        Returns:
            Byte yang terbaca, atau b"". Arti b"" ditentukan `is_stream`.
        """
        ...

    @abstractmethod
    async def write(self, data: bytes) -> None:
        """Kirim byte ke alat."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Tutup koneksi dan bebaskan resource."""
        ...
```

```python
# services/tap/transport/tcp.py
"""
services/tap/transport/tcp.py — Transport TCP untuk tapping.

Dua arah koneksi, karena alat berbeda-beda: sebagian connect ke MidLab
(server), sebagian menunggu MidLab connect (client).

TcpServerTransport sengaja melayani SATU koneksi: satu sesi tapping = satu alat.
"""

import asyncio

from lib.utils import get_logger
from services.tap.transport.base import BaseTransport


logger = get_logger("tap_transport")

BUF = 65536


class TcpServerTransport(BaseTransport):
    """MidLab listen; alat yang connect. Melayani satu koneksi."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tersambung = asyncio.Event()

    @property
    def port(self) -> int:
        """Port efektif — berguna saat diminta port 0 (test)."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    @property
    def description(self) -> str:
        return f"tcp-server {self._host}:{self._port}"

    async def open(self) -> None:
        self._server = await asyncio.start_server(
            self._on_connect, self._host, self._port
        )
        logger.info(f"[TAP] listen di {self._host}:{self.port}")

    async def _on_connect(self, reader, writer) -> None:
        if self._reader is not None:
            # Sesi tapping = satu alat. Koneksi kedua ditolak, bukan diantre.
            logger.warning("[TAP] koneksi kedua ditolak — sesi sudah terpakai")
            writer.close()
            return
        peer = writer.get_extra_info("peername")
        logger.info(f"[TAP] alat tersambung dari {peer}")
        self._reader, self._writer = reader, writer
        self._tersambung.set()

    async def read(self) -> bytes:
        await self._tersambung.wait()
        try:
            return await self._reader.read(BUF)
        except (ConnectionError, asyncio.IncompleteReadError):
            return b""

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            return
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


class TcpClientTransport(BaseTransport):
    """MidLab yang connect ke alat."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def description(self) -> str:
        return f"tcp-client {self._host}:{self._port}"

    async def open(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )
        logger.info(f"[TAP] tersambung ke {self._host}:{self._port}")

    async def read(self) -> bytes:
        try:
            return await self._reader.read(BUF)
        except (ConnectionError, asyncio.IncompleteReadError):
            return b""

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            return
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_transport.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/transport/ tests/test_tap_transport.py
git commit -m "feat(tap): BaseTransport + TCP server/client"
```

---

## Task 8: SerialTransport (teruji lewat pty)

**Files:**
- Create: `services/tap/transport/serial_port.py`
- Modify: `tests/test_tap_transport.py`
- Test: `tests/test_tap_transport.py`

**Interfaces:**
- Consumes: `BaseTransport` (Task 7)
- Produces: `SerialTransport(port: str, baudrate: int = 9600, bytesize: int = 8, parity: str = "N", stopbits: int = 1, xonxoff: bool = False, rtscts: bool = False)`

**Catatan:** `pyserial` blocking, jadi `read()` dibungkus `asyncio.to_thread`. Diuji tanpa hardware lewat `os.openpty()` — pyserial bisa membuka sisi slave pty seperti port serial biasa.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_transport.py — tambahkan
import os

from services.tap.transport.serial_port import SerialTransport


class TestSerialTransport:
    @pytest.fixture
    def pty_pair(self):
        """(master_fd, slave_path) — pyserial bisa buka slave seperti port biasa."""
        master, slave = os.openpty()
        yield master, os.ttyname(slave)
        os.close(master)
        os.close(slave)

    @pytest.mark.asyncio
    async def test_membaca_dari_port(self, pty_pair):
        master, path = pty_pair
        t = SerialTransport(path, baudrate=9600)
        await t.open()
        os.write(master, b"\x05")
        data = await asyncio.wait_for(t.read(), timeout=5)
        assert data == b"\x05"
        await t.close()

    @pytest.mark.asyncio
    async def test_menulis_ke_port(self, pty_pair):
        master, path = pty_pair
        t = SerialTransport(path, baudrate=9600)
        await t.open()
        await t.write(b"\x06")
        assert os.read(master, 10) == b"\x06"
        await t.close()

    @pytest.mark.asyncio
    async def test_read_kosong_saat_tidak_ada_data(self, pty_pair):
        # Timeout pendek supaya loop tidak menggantung; b"" = "belum ada apa-apa".
        master, path = pty_pair
        t = SerialTransport(path, baudrate=9600)
        await t.open()
        assert await asyncio.wait_for(t.read(), timeout=5) == b""
        await t.close()

    @pytest.mark.asyncio
    async def test_description_memuat_setelan(self, pty_pair):
        _, path = pty_pair
        t = SerialTransport(path, baudrate=19200, bytesize=8, parity="E", stopbits=1)
        assert t.description == f"serial {path}@19200-8E1"

    @pytest.mark.asyncio
    async def test_port_tidak_ada_pesannya_menyebut_dialout(self):
        t = SerialTransport("/dev/tty-tidak-ada", baudrate=9600)
        with pytest.raises(OSError, match="dialout"):
            await t.open()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.transport.serial_port'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/transport/serial_port.py
"""
services/tap/transport/serial_port.py — Transport serial (RS232) untuk tapping.

Dipakai saat develop driver di laptop, sebelum alat dipindah ke topologi produksi
(alat → converter serial-to-TCP → server). Tapping di server selalu TCP.

pyserial bersifat blocking, jadi read/write dibungkus asyncio.to_thread. Port
dibuka dengan timeout pendek supaya read() cepat kembali dengan apa pun yang ada
— b"" berarti "belum ada data", bukan "putus".
"""

import asyncio

import serial

from lib.utils import get_logger
from services.tap.transport.base import BaseTransport


logger = get_logger("tap_transport")

BUF = 4096
READ_TIMEOUT = 0.2      # detik — cukup pendek agar loop tetap responsif


class SerialTransport(BaseTransport):
    """Baca/tulis port serial lewat pyserial."""

    # Port serial tetap terbuka meski alat diam — b"" berarti "belum ada data",
    # bukan "putus". Loop tidak boleh berhenti karenanya.
    is_stream = False

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
        xonxoff: bool = False,
        rtscts: bool = False,
    ):
        self._port = port
        self._baudrate = baudrate
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._xonxoff = xonxoff
        self._rtscts = rtscts
        self._ser: serial.Serial | None = None

    @property
    def description(self) -> str:
        return (
            f"serial {self._port}@{self._baudrate}-"
            f"{self._bytesize}{self._parity}{self._stopbits}"
        )

    async def open(self) -> None:
        try:
            self._ser = await asyncio.to_thread(
                serial.Serial,
                port=self._port,
                baudrate=self._baudrate,
                bytesize=self._bytesize,
                parity=self._parity,
                stopbits=self._stopbits,
                xonxoff=self._xonxoff,
                rtscts=self._rtscts,
                timeout=READ_TIMEOUT,
            )
        except (serial.SerialException, OSError) as e:
            # Kegagalan paling sering bukan kabel, tapi izin: user harus anggota
            # grup dialout. Sebutkan itu, jangan lempar stack trace mentah.
            raise OSError(
                f"Gagal membuka {self._port}: {e}. "
                f"Bila ini soal izin, pastikan user tergabung di grup 'dialout' "
                f"(sudo usermod -aG dialout $USER, lalu login ulang)."
            ) from e
        logger.info(f"[TAP] serial terbuka: {self.description}")

    async def read(self) -> bytes:
        if self._ser is None:
            return b""
        return await asyncio.to_thread(self._ser.read, BUF)

    async def write(self, data: bytes) -> None:
        if self._ser is None:
            return
        await asyncio.to_thread(self._ser.write, data)

    async def close(self) -> None:
        if self._ser is not None:
            await asyncio.to_thread(self._ser.close)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_transport.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/transport/serial_port.py tests/test_tap_transport.py
git commit -m "feat(tap): SerialTransport via pyserial, teruji lewat pty"
```

---

## Task 9: Model DB + migrasi

**Files:**
- Modify: `lib/db.py` (tambah model setelah `TblLisEventQueue`)
- Create: `scripts/migrate_tap_session.py`
- Test: `tests/test_tap_session.py` (bagian model)

**Interfaces:**
- Consumes: `Base` dari `lib/db.py`
- Produces: `TblTapSession` dengan kolom: `id`, `name`, `transport`, `target`, `protocol_basis`, `detected_protocol`, `response_mode`, `status`, `bytes_rx`, `bytes_tx`, `message_count`, `error_message`, `started_at`, `stopped_at`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_session.py
"""Test TapSession + model tbl_tap_session."""

import pytest

from lib.db import TblTapSession


class TestTblTapSession:
    def test_bisa_disimpan_dan_dibaca(self, db_session):
        s = TblTapSession(
            name="AR580 commissioning",
            transport="tcp_server",
            target="0.0.0.0:2600",
            protocol_basis="HL7",
            response_mode="uni",
            status="running",
        )
        db_session.add(s)
        db_session.commit()

        row = db_session.query(TblTapSession).one()
        assert row.name == "AR580 commissioning"
        assert row.target == "0.0.0.0:2600"

    def test_counter_default_nol(self, db_session):
        s = TblTapSession(
            name="x", transport="serial", target="/dev/ttyUSB0@9600-8N1",
            protocol_basis="RAW", response_mode="uni", status="running",
        )
        db_session.add(s)
        db_session.commit()
        row = db_session.query(TblTapSession).one()
        assert (row.bytes_rx, row.bytes_tx, row.message_count) == (0, 0, 0)

    def test_detected_protocol_boleh_kosong(self, db_session):
        s = TblTapSession(
            name="x", transport="tcp_client", target="10.0.0.5:9100",
            protocol_basis="AUTO", response_mode="uni", status="running",
        )
        db_session.add(s)
        db_session.commit()
        assert db_session.query(TblTapSession).one().detected_protocol is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_session.py -q`
Expected: FAIL — `ImportError: cannot import name 'TblTapSession' from 'lib.db'`

- [ ] **Step 3: Write minimal implementation**

Tambahkan ke `lib/db.py`, setelah class `TblLisEventQueue`:

```python
class TblTapSession(Base):
    """
    Metadata sesi tapping data (capture alat yang belum punya driver).

    Hanya METADATA yang di sini. Aliran byte-nya ada di
    /var/log/midlab/tap/<id>.jsonl — menaruh byte stream di kolom MySQL mengulang
    jebakan truncation tbl_result.raw_data TEXT (batas 64KB), dan capture bisa
    jauh lebih besar dari itu.

    Penulis & pembaca: TapService.
    """
    __tablename__ = "tbl_tap_session"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    name           = Column(String(255), nullable=False)
    transport      = Column(
        Enum("tcp_server", "tcp_client", "serial", name="tap_transport_enum"),
        nullable=False,
    )
    # mis. "0.0.0.0:2600" | "10.0.0.5:9100" | "/dev/ttyUSB0@9600-8N1"
    target         = Column(String(255), nullable=False)
    protocol_basis = Column(
        Enum("ASTM", "HL7", "RAW", "AUTO", name="tap_basis_enum"),
        nullable=False,
    )
    # Hasil tebakan AUTO — hanya saran, tidak mengubah responder.
    detected_protocol = Column(String(20), nullable=True)
    response_mode  = Column(
        Enum("uni", "bidi", name="tap_mode_enum"),
        nullable=False, default="uni",
    )
    status         = Column(
        Enum("running", "stopped", "error", name="tap_status_enum"),
        nullable=False, default="running",
    )
    bytes_rx       = Column(Integer, nullable=False, default=0)
    bytes_tx       = Column(Integer, nullable=False, default=0)
    message_count  = Column(Integer, nullable=False, default=0)
    error_message  = Column(Text, nullable=True)
    started_at     = Column(DateTime, default=datetime.utcnow)
    stopped_at     = Column(DateTime, nullable=True)
```

```python
# scripts/migrate_tap_session.py
#!/usr/bin/env python3
"""
scripts/migrate_tap_session.py — Buat tabel tbl_tap_session.

Metadata sesi tapping data. Aliran byte-nya TIDAK di sini — ada di
/var/log/midlab/tap/<id>.jsonl.

Idempotent: aman dijalankan berkali-kali (cek INFORMATION_SCHEMA dulu).

Usage:
    python3 scripts/migrate_tap_session.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text

from lib.db import DBManager


DDL = """
CREATE TABLE tbl_tap_session (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    transport         ENUM('tcp_server','tcp_client','serial') NOT NULL,
    target            VARCHAR(255) NOT NULL,
    protocol_basis    ENUM('ASTM','HL7','RAW','AUTO') NOT NULL,
    detected_protocol VARCHAR(20) NULL,
    response_mode     ENUM('uni','bidi') NOT NULL DEFAULT 'uni',
    status            ENUM('running','stopped','error') NOT NULL DEFAULT 'running',
    bytes_rx          INT NOT NULL DEFAULT 0,
    bytes_tx          INT NOT NULL DEFAULT 0,
    message_count     INT NOT NULL DEFAULT 0,
    error_message     TEXT NULL,
    started_at        DATETIME NULL,
    stopped_at        DATETIME NULL,
    INDEX idx_status (status),
    INDEX idx_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def main() -> int:
    # Pola sama dengan scripts/migrate_result_protocol_width.py:44.
    db = DBManager()
    with db.engine.begin() as conn:
        ada = conn.execute(text(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tbl_tap_session'"
        )).scalar()
        if ada:
            print("OK: tbl_tap_session sudah ada, tidak ada perubahan.")
            return 0
        conn.execute(text(DDL))
        print("OK: tbl_tap_session dibuat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_session.py -q`
Expected: PASS (3 passed)

Verifikasi migrasi bisa di-import tanpa DB:
Run: `python3 -c "import ast; ast.parse(open('scripts/migrate_tap_session.py').read()); print('sintaks OK')"`
Expected: `sintaks OK`

- [ ] **Step 5: Commit**

```bash
git add lib/db.py scripts/migrate_tap_session.py tests/test_tap_session.py
chmod +x scripts/migrate_tap_session.py
git commit -m "feat(tap): model TblTapSession + migrasi idempotent"
```

---

## Task 10: TapSession — wiring + rekam-sebelum-balas

**Files:**
- Create: `services/tap/session.py`
- Modify: `tests/test_tap_session.py`
- Test: `tests/test_tap_session.py`

**Interfaces:**
- Consumes: `BaseTransport` (Task 7), `build_responder`/`detect_protocol`/`should_hint_baud` (Task 4), `TapRecorder` (Task 5)
- Produces:
  - `TapSession(transport, basis, recorder, mode="uni", on_event=None)`
  - `.run() -> None` (async) — loop sampai transport putus atau `stop()`
  - `.stop() -> None`
  - `.send_manual(data: bytes) -> None` (async)
  - `.bytes_rx: int`, `.bytes_tx: int`, `.message_count: int`, `.query_count: int`, `.detected: str | None`, `.baud_hint: bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_session.py — tambahkan
import asyncio

from services.tap.recorder import TapRecorder, read_events
from services.tap.session import TapSession
from services.tap.transport.base import BaseTransport


VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"
ORU = VT + b"MSH|^~\\&|Genrui|KT-6610|||1||ORU^R01|1275|P|2.3.1\r" + FS + CR


class TransportPalsu(BaseTransport):
    """Transport dari daftar chunk; mencatat apa yang ditulis."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.ditulis: list[bytes] = []
        self.tertutup = False

    description = "palsu"

    async def open(self) -> None:
        pass

    async def read(self) -> bytes:
        if not self._chunks:
            return b""          # putus
        await asyncio.sleep(0)
        return self._chunks.pop(0)

    async def write(self, data: bytes) -> None:
        self.ditulis.append(data)

    async def close(self) -> None:
        self.tertutup = True


@pytest.fixture
def rec_path(tmp_path):
    return str(tmp_path / "sesi.jsonl")


class TestTapSession:
    @pytest.mark.asyncio
    async def test_merekam_rx_dan_membalas_ack(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
        assert b"MSA|AA|1275" in t.ditulis[0]
        arah = [e["dir"] for e in read_events(rec_path)]
        assert "rx" in arah and "tx" in arah

    @pytest.mark.asyncio
    async def test_rekam_sebelum_balas(self, rec_path):
        # PENGAMAN INTI: byte harus di disk sebelum ACK dikirim. Kalau proses
        # mati setelah ACK tapi sebelum tulisan, alat tidak akan kirim ulang
        # dan hasil pasien hilang tanpa jejak.
        urutan = []

        class TransportPengintai(TransportPalsu):
            async def write(self, data: bytes) -> None:
                urutan.append(("write", len(read_events(rec_path))))
                await super().write(data)

        t = TransportPengintai([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()

        # Saat write() dipanggil, event RX sudah tertulis ke file.
        assert urutan[0][1] >= 1, "ACK dikirim sebelum RX terekam"

    @pytest.mark.asyncio
    async def test_menghitung_byte_dan_pesan(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
        assert s.bytes_rx == len(ORU)
        assert s.bytes_tx > 0
        assert s.message_count == 1

    @pytest.mark.asyncio
    async def test_menandai_pesan_lengkap(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
        meta = [e for e in read_events(rec_path) if e["dir"] == "meta"]
        assert meta[0]["event"] == "message_complete"
        assert meta[0]["index"] == 0

    @pytest.mark.asyncio
    async def test_mode_raw_tidak_membalas(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "RAW", rec)
            await s.run()
        assert t.ditulis == []

    @pytest.mark.asyncio
    async def test_auto_mendeteksi_tapi_tetap_pasif(self, rec_path):
        # AUTO hanya MENYARANKAN — responder tidak berganti sendiri di tengah sesi.
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "AUTO", rec)
            await s.run()
        assert s.detected == "HL7"
        assert t.ditulis == []

    @pytest.mark.asyncio
    async def test_kirim_manual_terekam(self, rec_path):
        t = TransportPalsu([])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "RAW", rec)
            await s.send_manual(b"\x05")
        assert t.ditulis == [b"\x05"]
        ev = read_events(rec_path)
        assert ev[0]["dir"] == "tx" and ev[0]["note"] == "manual"

    @pytest.mark.asyncio
    async def test_hint_baud_menyala(self, rec_path):
        sampah = bytes(range(256)) * 2      # tidak membentuk frame HL7
        t = TransportPalsu([sampah])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "ASTM", rec)
            await s.run()
        assert s.baud_hint is True

    @pytest.mark.asyncio
    async def test_hint_baud_mati_saat_ada_pesan(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
        assert s.baud_hint is False

    @pytest.mark.asyncio
    async def test_stop_menghentikan_loop(self, rec_path):
        class TransportTakHabis(TransportPalsu):
            async def read(self) -> bytes:
                await asyncio.sleep(0.01)
                return b"\x00"

        t = TransportTakHabis([])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "RAW", rec)
            tugas = asyncio.create_task(s.run())
            await asyncio.sleep(0.05)
            s.stop()
            await asyncio.wait_for(tugas, timeout=2)
        assert t.tertutup is True

    @pytest.mark.asyncio
    async def test_bidi_menandai_query_tanpa_menjawabnya(self, rec_path):
        # bidi sengaja TIDAK mengarang jawaban: bentuk not-found beda per tipe
        # query, dan respons salah bisa membuat alat mencatat error — mengotori
        # capture yang justru sedang diamati. Yang dikejar formatnya.
        qry = VT + b"MSH|^~\\&|Mindray|BS-200E|||1||QRY^Q02|5|P|2.3.1\r" + FS + CR
        t = TransportPalsu([qry])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec, mode="bidi")
            await s.run()

        assert s.query_count == 1
        meta = [e for e in read_events(rec_path) if e.get("event") == "query_detected"]
        assert len(meta) == 1
        # Tetap di-ACK (supaya alat tidak kirim ulang), tapi tidak dijawab order.
        assert len(t.ditulis) == 1
        assert b"MSA|AA|5" in t.ditulis[0]

    @pytest.mark.asyncio
    async def test_uni_tidak_menandai_query(self, rec_path):
        qry = VT + b"MSH|^~\\&|Mindray|BS-200E|||1||QRY^Q02|5|P|2.3.1\r" + FS + CR
        t = TransportPalsu([qry])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec, mode="uni")
            await s.run()
        assert s.query_count == 0

    @pytest.mark.asyncio
    async def test_bidi_hasil_biasa_bukan_query(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec, mode="bidi")
            await s.run()
        assert s.query_count == 0

    @pytest.mark.asyncio
    async def test_on_event_dipanggil(self, rec_path):
        terlihat = []
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec, on_event=terlihat.append)
            await s.run()
        assert any(e["dir"] == "rx" for e in terlihat)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_session.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.session'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/session.py
"""
services/tap/session.py — Merangkai transport ↔ responder ↔ recorder.

URUTAN OPERASINYA MENGIKAT: rekam dulu, baru balas.

Bila MidLab meng-ACK, alat menganggap datanya sudah tersimpan dan menandai
sampel itu terkirim — ia tidak akan mengirimnya lagi. Kalau proses mati setelah
ACK terkirim tapi sebelum byte-nya tertulis, hasil pasien itu lenyap tanpa jejak.
Jangan membalik urutan _rekam() dan transport.write().
"""

import asyncio
from typing import Callable

from lib.utils import get_logger
from services.tap.detect import (
    build_responder, detect_protocol, is_query, should_hint_baud,
)
from services.tap.recorder import TapRecorder
from services.tap.transport.base import BaseTransport


logger = get_logger("tap_session")


class TapSession:
    """Satu sesi tapping: baca byte, rekam, balas handshake, hitung."""

    def __init__(
        self,
        transport: BaseTransport,
        basis: str,
        recorder: TapRecorder,
        mode: str = "uni",
        on_event: Callable[[dict], None] | None = None,
    ):
        self._transport = transport
        self._basis = basis
        self._recorder = recorder
        self._mode = mode
        self._on_event = on_event

        # Basis AUTO tidak pernah membalas: ia hanya menebak dan melapor.
        # Operator yang memutuskan, responder tidak berganti sendiri.
        self._responder = build_responder("RAW" if basis == "AUTO" else basis)

        self._berhenti = asyncio.Event()
        self.bytes_rx = 0
        self.bytes_tx = 0
        self.message_count = 0
        self.query_count = 0
        self.detected: str | None = None
        self.baud_hint = False

    async def run(self) -> None:
        """Loop sampai transport putus (TCP) atau stop() dipanggil."""
        try:
            while not self._berhenti.is_set():
                data = await self._transport.read()
                if not data:
                    # Arti b"" bergantung transport: TCP = putus (keluar);
                    # serial = alat sedang diam (lanjut, port masih terbuka).
                    if self._transport.is_stream:
                        break
                    continue
                await self._proses(data)
        finally:
            await self._transport.close()

    async def _proses(self, data: bytes) -> None:
        # 1. REKAM DULU — sebelum apa pun dikirim balik.
        self._rekam("rx", data)
        self.bytes_rx += len(data)

        if self._basis == "AUTO" and self.detected is None:
            self.detected = detect_protocol(data)
            if self.detected:
                logger.info(f"[TAP] terdeteksi kemungkinan {self.detected}")

        # 2. Baru hitung balasannya.
        balasan = self._responder.feed(data)

        # 3. Kirim, sambil merekam tiap balasan.
        for b in balasan:
            self._rekam("tx", b, note="auto")
            self.bytes_tx += len(b)
            await self._transport.write(b)

        # 4. Tandai pesan lengkap yang baru muncul.
        pesan = self._responder.messages()
        while self.message_count < len(pesan):
            idx = self.message_count
            self._recorder.mark_message(idx)

            # Mode bidi: tandai query, tapi JANGAN dijawab. Menjawab dengan order
            # sungguhan butuh driver yang justru sedang dibuat; mengarang jawaban
            # bisa membuat alat mencatat error dan mengotori capture.
            if self._mode == "bidi" and is_query(pesan[idx], self._basis):
                self._recorder.mark_query(idx)
                self.query_count += 1
                logger.info(f"[TAP] query terdeteksi di pesan #{idx} — tidak dijawab")

            self.message_count += 1

        self.baud_hint = should_hint_baud(
            self._basis, self.bytes_rx, self.message_count
        )

    async def send_manual(self, data: bytes) -> None:
        """Kirim byte yang diketik operator (dipakai basis RAW)."""
        self._rekam("tx", data, note="manual")
        self.bytes_tx += len(data)
        await self._transport.write(data)

    def _rekam(self, arah: str, data: bytes, note: str = "") -> None:
        self._recorder.write_event(arah, data, note=note)
        if self._on_event is not None:
            row = {"dir": arah, "hex": data.hex()}
            if note:
                row["note"] = note
            self._on_event(row)

    def stop(self) -> None:
        """Minta loop berhenti; run() akan menutup transport."""
        self._berhenti.set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_session.py -q`
Expected: PASS (14 passed)

- [ ] **Step 5: Commit**

```bash
git add services/tap/session.py tests/test_tap_session.py
git commit -m "feat(tap): TapSession — wiring dengan urutan rekam-sebelum-balas"
```

---

## Task 11: TapService + CLI + port guard

**Files:**
- Create: `services/tap/service.py`
- Modify: `tests/test_tap_session.py` (tambah `TestPortGuard`)
- Test: `tests/test_tap_session.py`

**Interfaces:**
- Consumes: `TapSession` (Task 10), `TblTapSession` (Task 9), transports (Task 7–8)
- Produces:
  - `TapPortConflict(Exception)`
  - `check_port_free(port: int, session) -> None` — raise `TapPortConflict` bila bentrok
  - `build_transport(transport: str, **params) -> BaseTransport`
  - `session_log_path(session_id: int) -> str`
  - `main() -> int` — entry CLI

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_session.py — tambahkan
from lib.db import TblInstrument
from services.tap.service import (
    TapPortConflict, check_port_free, build_transport, session_log_path,
)
from services.tap.transport.tcp import TcpServerTransport, TcpClientTransport
from services.tap.transport.serial_port import SerialTransport


class TestPortGuard:
    def test_port_bebas_lolos(self, db_session):
        check_port_free(2600, db_session)      # tidak melempar

    def test_port_dipakai_alat_aktif_ditolak(self, db_session):
        # Bukan cuma soal bind: mencegah DUA pihak meng-ACK alat yang sama.
        db_session.add(TblInstrument(
            name="AR580", ip_address="0.0.0.0", port=2600,
            protocol="HL7_ARUMA_AR580", mode="unidirectional",
            connection="server", is_active=True,
        ))
        db_session.commit()
        with pytest.raises(TapPortConflict, match="AR580"):
            check_port_free(2600, db_session)

    def test_port_dipakai_alat_nonaktif_lolos(self, db_session):
        db_session.add(TblInstrument(
            name="Lama", ip_address="0.0.0.0", port=2600,
            protocol="HL7", mode="unidirectional",
            connection="server", is_active=False,
        ))
        db_session.commit()
        check_port_free(2600, db_session)      # tidak melempar


class TestBuildTransport:
    def test_tcp_server(self):
        t = build_transport("tcp_server", host="0.0.0.0", port=2600)
        assert isinstance(t, TcpServerTransport)
        assert t.description == "tcp-server 0.0.0.0:2600"

    def test_tcp_client(self):
        t = build_transport("tcp_client", host="10.0.0.5", port=9100)
        assert isinstance(t, TcpClientTransport)

    def test_serial(self):
        t = build_transport("serial", port="/dev/ttyUSB0", baudrate=19200)
        assert isinstance(t, SerialTransport)
        assert "19200" in t.description

    def test_transport_tak_dikenal_ditolak(self):
        with pytest.raises(ValueError, match="tidak dikenali"):
            build_transport("carrier_pigeon")


class TestSessionLogPath:
    def test_path_memakai_id(self):
        assert session_log_path(42).endswith("/tap/42.jsonl")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_session.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.tap.service'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tap/service.py
#!/usr/bin/env python3
"""
services/tap/service.py — TapService: orkestrasi sesi tapping.

Menangkap komunikasi alat yang BELUM punya driver — langkah pertama SOP
tap → analisis → tulis driver → test.

Data yang di-tap TIDAK masuk tbl_result dan TIDAK dikirim ke LIS. Yang tersimpan
hanya capture mentahnya di /var/log/midlab/tap/<id>.jsonl.

Usage:
    python3 -m services.tap.service --name "AR580" --transport tcp_server \\
        --port 2600 --basis HL7
    python3 -m services.tap.service --name "Sysmex" --transport serial \\
        --serial-port /dev/ttyUSB0 --baudrate 9600 --basis AUTO
"""

import argparse
import asyncio
import os
import signal
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(
    os.path.dirname(__file__)
))))

from lib.db import DBManager, TblInstrument, TblTapSession
from lib.utils import get_logger
from services.tap.recorder import TAP_LOG_DIR, TapRecorder
from services.tap.session import TapSession
from services.tap.transport.base import BaseTransport
from services.tap.transport.serial_port import SerialTransport
from services.tap.transport.tcp import TcpClientTransport, TcpServerTransport


logger = get_logger("tap_service")


class TapPortConflict(Exception):
    """Port sudah dipakai alat aktif — tapping ditolak."""


def check_port_free(port: int, session) -> None:
    """
    Pastikan tidak ada alat aktif yang memakai port ini.

    Bukan sekadar soal bind: kalau service TCP alat itu sedang jalan, dua pihak
    akan meng-ACK alat yang sama, dan hasil yang di-ACK oleh tap TIDAK masuk
    tbl_result — hasil pasien bisa hilang diam-diam.
    """
    row = (
        session.query(TblInstrument)
        .filter(TblInstrument.port == port, TblInstrument.is_active.is_(True))
        .first()
    )
    if row is not None:
        raise TapPortConflict(
            f"Port {port} dipakai alat aktif '{row.name}' (id={row.id}). "
            f"Matikan service TCP-nya dulu, atau pakai port lain — dua pihak "
            f"tidak boleh meng-ACK alat yang sama."
        )


def build_transport(transport: str, **params) -> BaseTransport:
    """Buat transport sesuai jenisnya."""
    if transport == "tcp_server":
        return TcpServerTransport(params["host"], params["port"])
    if transport == "tcp_client":
        return TcpClientTransport(params["host"], params["port"])
    if transport == "serial":
        return SerialTransport(
            params["port"],
            baudrate=params.get("baudrate", 9600),
            bytesize=params.get("bytesize", 8),
            parity=params.get("parity", "N"),
            stopbits=params.get("stopbits", 1),
            xonxoff=params.get("xonxoff", False),
            rtscts=params.get("rtscts", False),
        )
    raise ValueError(
        f"Transport '{transport}' tidak dikenali. "
        f"Tersedia: tcp_server, tcp_client, serial"
    )


def session_log_path(session_id: int) -> str:
    """Lokasi file JSONL untuk satu sesi."""
    return os.path.join(TAP_LOG_DIR, f"{session_id}.jsonl")


async def run_session(row_id: int, transport: BaseTransport, basis: str,
                      mode: str) -> None:
    """Jalankan satu sesi tapping sampai selesai, lalu update metadata."""
    path = session_log_path(row_id)
    await transport.open()

    with TapRecorder(path) as rec:
        tap = TapSession(transport, basis, rec, mode=mode)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, tap.stop)

        try:
            await tap.run()
            status, err = "stopped", None
        except Exception as e:
            logger.error(f"[TAP] sesi {row_id} gagal: {e}")
            status, err = "error", str(e)
        finally:
            _simpan_hasil(row_id, tap, status, err)


def _simpan_hasil(row_id: int, tap: TapSession, status: str, err: str | None) -> None:
    session = DBManager().get_session()
    try:
        row = session.query(TblTapSession).get(row_id)
        if row is not None:
            row.status = status
            row.error_message = err
            row.bytes_rx = tap.bytes_rx
            row.bytes_tx = tap.bytes_tx
            row.message_count = tap.message_count
            row.detected_protocol = tap.detected
            row.stopped_at = datetime.utcnow()
            session.commit()
    except Exception as e:
        logger.error(f"[TAP] gagal menyimpan hasil sesi {row_id}: {e}")
        session.rollback()
    finally:
        session.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="TapService — capture alat lab")
    ap.add_argument("--name", required=True)
    ap.add_argument("--transport", required=True,
                    choices=["tcp_server", "tcp_client", "serial"])
    ap.add_argument("--basis", required=True, choices=["ASTM", "HL7", "RAW", "AUTO"])
    ap.add_argument("--mode", default="uni", choices=["uni", "bidi"])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, help="port TCP")
    ap.add_argument("--serial-port", help="mis. /dev/ttyUSB0")
    ap.add_argument("--baudrate", type=int, default=9600)
    ap.add_argument("--parity", default="N", choices=["N", "E", "O"])
    args = ap.parse_args()

    session = DBManager().get_session()
    try:
        if args.transport in ("tcp_server", "tcp_client"):
            if args.port is None:
                print("ERROR: --port wajib untuk transport TCP", file=sys.stderr)
                return 2
            if args.transport == "tcp_server":
                check_port_free(args.port, session)
            transport = build_transport(
                args.transport, host=args.host, port=args.port
            )
        else:
            if not args.serial_port:
                print("ERROR: --serial-port wajib untuk transport serial",
                      file=sys.stderr)
                return 2
            transport = build_transport(
                "serial", port=args.serial_port,
                baudrate=args.baudrate, parity=args.parity,
            )

        row = TblTapSession(
            name=args.name, transport=args.transport,
            target=transport.description, protocol_basis=args.basis,
            response_mode=args.mode, status="running",
            started_at=datetime.utcnow(),
        )
        session.add(row)
        session.commit()
        row_id = row.id
    except TapPortConflict as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()

    print(f"Sesi tap #{row_id} — {transport.description}")
    print(f"Capture: {session_log_path(row_id)}")
    print("PERINGATAN: data tap TIDAK masuk tbl_result dan TIDAK dikirim ke LIS.")

    asyncio.run(run_session(row_id, transport, args.basis, args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_session.py -q`
Expected: PASS (22 passed)

Verifikasi CLI hidup:
Run: `python3 -m services.tap.service --help`
Expected: usage text dengan `--transport {tcp_server,tcp_client,serial}`

- [ ] **Step 5: Commit**

```bash
git add services/tap/service.py tests/test_tap_session.py
git commit -m "feat(tap): TapService + CLI + guard port bentrok alat aktif"
```

---

## Task 12: Integrasi end-to-end dengan simulator AR580

**Files:**
- Create: `tests/test_tap_integration.py`
- Test: `tests/test_tap_integration.py`

**Interfaces:**
- Consumes: seluruh Task 1–11
- Produces: —

**Catatan:** Memakai `scripts/aruma_ar580_test_sender.py` sebagai fake instrument — alat yang sudah terbukti, tidak perlu menulis simulator baru.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_integration.py
"""
End-to-end tapping: simulator AR580 → TapSession → capture → export.

Memakai scripts/aruma_ar580_test_sender.py sebagai fake instrument. Alat itu
sudah terbukti (dipakai commissioning driver AR580), jadi tidak perlu menulis
simulator baru.
"""

import asyncio
import os
import subprocess
import sys

import pytest

from services.tap.export import messages_from_events, rx_bytes, to_python_bytes
from services.tap.recorder import TapRecorder, read_events
from services.tap.session import TapSession
from services.tap.transport.tcp import TcpServerTransport


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SENDER = os.path.join(REPO, "scripts", "aruma_ar580_test_sender.py")


@pytest.mark.asyncio
async def test_tap_menangkap_dan_meng_ack_ar580(tmp_path):
    path = str(tmp_path / "sesi.jsonl")
    transport = TcpServerTransport("127.0.0.1", 0)
    await transport.open()
    port = transport.port

    proc = await asyncio.create_subprocess_exec(
        sys.executable, SENDER,
        "--host", "127.0.0.1", "--port", str(port), "--scenario", "result",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        cwd=REPO,
    )

    with TapRecorder(path) as rec:
        tap = TapSession(transport, "HL7", rec)
        tugas = asyncio.create_task(tap.run())
        rc = await asyncio.wait_for(proc.wait(), timeout=30)
        tap.stop()
        await asyncio.wait_for(tugas, timeout=5)

    # Simulator memvalidasi ACK-nya sendiri: exit 0 berarti MSA|AA| cocok.
    assert rc == 0, "simulator melaporkan ACK tidak sah"

    events = read_events(path)
    assert tap.message_count == 1
    assert any(e["dir"] == "tx" for e in events)

    pesan = messages_from_events(events)
    assert len(pesan) == 1
    assert b"ORU^R01" in pesan[0]
    assert b"^WBC^" in pesan[0]
    # Panel CBC+DIFF lengkap: 25 NM + 4 IS metadata.
    assert pesan[0].count(b"OBX|") == 29


@pytest.mark.asyncio
async def test_export_menghasilkan_fixture_yang_identik(tmp_path):
    # Inti nilai fitur ini: hasil tap → tempel ke test → byte-nya persis sama.
    path = str(tmp_path / "sesi.jsonl")
    transport = TcpServerTransport("127.0.0.1", 0)
    await transport.open()
    port = transport.port

    proc = await asyncio.create_subprocess_exec(
        sys.executable, SENDER,
        "--host", "127.0.0.1", "--port", str(port), "--scenario", "result",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        cwd=REPO,
    )

    with TapRecorder(path) as rec:
        tap = TapSession(transport, "HL7", rec)
        tugas = asyncio.create_task(tap.run())
        await asyncio.wait_for(proc.wait(), timeout=30)
        tap.stop()
        await asyncio.wait_for(tugas, timeout=5)

    asli = messages_from_events(read_events(path))[0]
    literal = to_python_bytes(asli)
    assert eval(literal.strip()) == asli


@pytest.mark.asyncio
async def test_bitmap_ed_besar_tetap_utuh_di_capture(tmp_path):
    # Skenario bitmap mengirim 4 × 4096 byte ED. Capture harus utuh — tidak ada
    # batas 64KB seperti tbl_result.raw_data TEXT, karena byte-nya di file.
    path = str(tmp_path / "sesi.jsonl")
    transport = TcpServerTransport("127.0.0.1", 0)
    await transport.open()
    port = transport.port

    proc = await asyncio.create_subprocess_exec(
        sys.executable, SENDER,
        "--host", "127.0.0.1", "--port", str(port), "--scenario", "bitmap",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        cwd=REPO,
    )

    with TapRecorder(path) as rec:
        tap = TapSession(transport, "HL7", rec)
        tugas = asyncio.create_task(tap.run())
        await asyncio.wait_for(proc.wait(), timeout=30)
        tap.stop()
        await asyncio.wait_for(tugas, timeout=5)

    pesan = messages_from_events(read_events(path))[0]
    assert b"DIFFScatter_BMP" in pesan
    assert len(pesan) > 16000, "bitmap terpotong di capture"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_integration.py -q`
Expected: FAIL — sebelum Task 1–11 selesai. Bila sudah selesai, jalankan dan pastikan PASS; kalau gagal, perbaiki implementasi (bukan testnya).

- [ ] **Step 3: Verifikasi tidak ada regresi**

Run: `python3 -m pytest -q`
Expected: PASS — 163 (baseline) + test tap baru, 0 gagal

- [ ] **Step 4: Jalankan mutation check pada pengaman inti**

Balik urutan rekam-sebelum-balas di `services/tap/session.py::_proses` — pindahkan `self._rekam("rx", data)` ke SETELAH loop `await self._transport.write(b)`:

Run: `python3 -m pytest tests/test_tap_session.py::TestTapSession::test_rekam_sebelum_balas -q`
Expected: FAIL — membuktikan test itu benar-benar menjaga urutannya

Kembalikan urutan aslinya, lalu:
Run: `python3 -m pytest tests/test_tap_session.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_tap_integration.py
git commit -m "test(tap): end-to-end dengan simulator AR580 + verifikasi export"
```

---

## Task 13: API endpoint web console

**Files:**
- Modify: `services/web_console/api.py`
- Test: `tests/test_tap_api.py`

**Interfaces:**
- Consumes: `TblTapSession` (Task 9), `read_events`/`session_log_path` (Task 5, 11), export (Task 6), `check_port_free` (Task 11)
- Produces:
  - `GET /api/tap/sessions` → `list[TapSessionResponse]`
  - `POST /api/tap/sessions` → `TapSessionResponse` (409 bila port bentrok)
  - `POST /api/tap/sessions/{id}/stop` → `MessageResponse`
  - `GET /api/tap/sessions/{id}/events` → `list[dict]`
  - `GET /api/tap/sessions/{id}/export/bin` → `application/octet-stream`
  - `GET /api/tap/sessions/{id}/export/python?index=N` → `text/plain`
  - `POST /api/tap/sessions/{id}/send` → `MessageResponse`
  - `_TAP_RUNNER` — instance `_TapRunner` dengan `.start(id, transport, basis, mode)`, `.stop(id)`, `.get(id) -> TapSession | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_api.py
"""
Test endpoint /api/tap/*.

Catatan auth: _verify_api_key() (api.py:171) langsung return bila
web_console.api_key kosong di config. Config test tidak menyetelnya, jadi
request TANPA header tetap 200 — jangan menulis test yang mengharapkan 401.

Catatan DB: endpoint memakai DBManager() yang menunjuk ke MySQL. Ikuti pola
tests/test_api_instrument_lis_fields.py — patch
"services.web_console.api.DBManager", jangan mengandalkan DB sungguhan.
"""

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from services.tap.recorder import TapRecorder
from services.web_console import api
from services.web_console.api import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db():
    """Patch DBManager supaya endpoint tidak menyentuh MySQL sungguhan."""
    with patch("services.web_console.api.DBManager") as MockDB:
        MockDB.return_value.get_session.return_value = MagicMock()
        yield MockDB


@pytest.fixture
def jsonl(tmp_path, monkeypatch):
    """Arahkan session_log_path() ke file sementara."""
    path = tmp_path / "1.jsonl"
    monkeypatch.setattr(api, "session_log_path", lambda i: str(path))
    return path


ORU = (
    b"\x0bMSH|^~\\&|Genrui|KT-6610|||1||ORU^R01|1275|P|2.3.1\r"
    b"OBX|1|NM|^WBC^||0.01|10^9/L\r\x1c\x0d"
)


class TestCreateSession:
    def test_port_bentrok_409(self, client, mock_db, monkeypatch):
        # 409, bukan 400: formatnya benar, yang salah keadaan sekarang.
        from services.tap import service as svc

        def tolak(port, session):
            raise svc.TapPortConflict("Port 2600 dipakai alat aktif 'AR580'")

        monkeypatch.setattr(api.tap_service, "check_port_free", tolak)
        r = client.post("/api/tap/sessions", json={
            "name": "x", "transport": "tcp_server", "port": 2600,
            "protocol_basis": "HL7", "response_mode": "uni",
        })
        assert r.status_code == 409
        assert "AR580" in r.json()["detail"]

    def test_tcp_tanpa_port_400(self, client, mock_db):
        r = client.post("/api/tap/sessions", json={
            "name": "x", "transport": "tcp_server",
            "protocol_basis": "HL7", "response_mode": "uni",
        })
        assert r.status_code == 400

    def test_serial_tanpa_serial_port_400(self, client, mock_db):
        r = client.post("/api/tap/sessions", json={
            "name": "x", "transport": "serial",
            "protocol_basis": "RAW", "response_mode": "uni",
        })
        assert r.status_code == 400


class TestGetEvents:
    def test_mengembalikan_event(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"\x05")
        rows = client.get("/api/tap/sessions/1/events").json()
        assert rows[0]["hex"] == "05"

    def test_sesi_tanpa_file_list_kosong(self, client, jsonl):
        assert client.get("/api/tap/sessions/1/events").json() == []


class TestExportBin:
    def test_hanya_byte_rx(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"\x0bMSH")
            r.write_event("tx", b"\x06")          # TX tidak ikut
        r2 = client.get("/api/tap/sessions/1/export/bin")
        assert r2.status_code == 200
        assert r2.content == b"\x0bMSH"

    def test_header_attachment(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"x")
        r2 = client.get("/api/tap/sessions/1/export/bin")
        assert "tap_1.bin" in r2.headers["content-disposition"]


class TestExportPython:
    def test_literal_bisa_di_eval_jadi_byte_asli(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", ORU)
            r.mark_message(0)
        r2 = client.get("/api/tap/sessions/1/export/python")
        assert r2.status_code == 200
        assert eval(r2.text.strip()) == ORU

    def test_index_di_luar_batas_404(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", ORU)
            r.mark_message(0)
        r2 = client.get("/api/tap/sessions/1/export/python?index=99")
        assert r2.status_code == 404
        assert "total 1" in r2.json()["detail"]

    def test_tanpa_pesan_404_menyarankan_bin(self, client, jsonl):
        # Basis RAW tidak punya batas pesan — arahkan operator ke export .bin.
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"\x00\x01\x02")
        r2 = client.get("/api/tap/sessions/1/export/python")
        assert r2.status_code == 404
        assert ".bin" in r2.json()["detail"]


class TestSendManual:
    def test_sesi_tidak_jalan_404(self, client):
        r = client.post("/api/tap/sessions/999/send", json={"hex": "05"})
        assert r.status_code == 404

    def test_hex_tidak_valid_400(self, client, monkeypatch):
        class TapPalsu:
            async def send_manual(self, data):
                pass

        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: TapPalsu())
        r = client.post("/api/tap/sessions/1/send", json={"hex": "zz"})
        assert r.status_code == 400
        assert "hex" in r.json()["detail"]

    def test_hex_valid_terkirim(self, client, monkeypatch):
        terkirim = []

        class TapPalsu:
            async def send_manual(self, data):
                terkirim.append(data)

        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: TapPalsu())
        r = client.post("/api/tap/sessions/1/send", json={"hex": "0b 05"})
        assert r.status_code == 200
        assert terkirim == [b"\x0b\x05"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_api.py -q`
Expected: FAIL — `404` untuk route yang belum ada (atau ImportError)

- [ ] **Step 3: Write minimal implementation**

Tambahkan ke `services/web_console/api.py`. Import di bagian atas file, bersama import lain:

```python
from services.tap import service as tap_service
from services.tap.export import messages_from_events, rx_bytes, to_python_bytes
from services.tap.recorder import read_events
from services.tap.service import session_log_path
```

Model Pydantic, letakkan bersama model lain:

```python
class TapSessionResponse(BaseModel):
    id: int
    name: str
    transport: str
    target: str
    protocol_basis: str
    detected_protocol: str | None
    response_mode: str
    status: str
    bytes_rx: int
    bytes_tx: int
    message_count: int
    started_at: str | None
    stopped_at: str | None


class TapSessionCreate(BaseModel):
    name: str
    transport: str                 # tcp_server | tcp_client | serial
    protocol_basis: str            # ASTM | HL7 | RAW | AUTO
    response_mode: str = "uni"
    host: str = "0.0.0.0"
    port: int | None = None
    serial_port: str | None = None
    baudrate: int = 9600
    parity: str = "N"
```

Endpoint, letakkan di bagian bawah bersama endpoint lain:

```python
# ============================================================
# [Tap] — capture alat yang belum punya driver
# ============================================================

@app.get("/api/tap/sessions", response_model=list[TapSessionResponse])
async def list_tap_sessions(x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    session = DBManager().get_session()
    try:
        rows = (
            session.query(TblTapSession)
            .order_by(TblTapSession.id.desc())
            .limit(100)
            .all()
        )
        return [
            TapSessionResponse(
                id=r.id, name=r.name, transport=r.transport, target=r.target,
                protocol_basis=r.protocol_basis,
                detected_protocol=r.detected_protocol,
                response_mode=r.response_mode, status=r.status,
                bytes_rx=r.bytes_rx, bytes_tx=r.bytes_tx,
                message_count=r.message_count,
                started_at=r.started_at.isoformat() if r.started_at else None,
                stopped_at=r.stopped_at.isoformat() if r.stopped_at else None,
            )
            for r in rows
        ]
    finally:
        session.close()


@app.post("/api/tap/sessions", response_model=TapSessionResponse)
async def create_tap_session(body: TapSessionCreate, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    session = DBManager().get_session()
    try:
        if body.transport == "tcp_server":
            if body.port is None:
                raise HTTPException(400, "port wajib untuk tcp_server")
            try:
                tap_service.check_port_free(body.port, session)
            except tap_service.TapPortConflict as e:
                # 409: bukan salah format, tapi bentrok dengan keadaan sekarang.
                raise HTTPException(409, str(e))
            transport = tap_service.build_transport(
                "tcp_server", host=body.host, port=body.port
            )
        elif body.transport == "tcp_client":
            if body.port is None:
                raise HTTPException(400, "port wajib untuk tcp_client")
            transport = tap_service.build_transport(
                "tcp_client", host=body.host, port=body.port
            )
        elif body.transport == "serial":
            if not body.serial_port:
                raise HTTPException(400, "serial_port wajib untuk transport serial")
            transport = tap_service.build_transport(
                "serial", port=body.serial_port,
                baudrate=body.baudrate, parity=body.parity,
            )
        else:
            raise HTTPException(400, f"transport '{body.transport}' tidak dikenali")

        row = TblTapSession(
            name=body.name, transport=body.transport,
            target=transport.description, protocol_basis=body.protocol_basis,
            response_mode=body.response_mode, status="running",
            started_at=datetime.utcnow(),
        )
        session.add(row)
        session.commit()

        _TAP_RUNNER.start(row.id, transport, body.protocol_basis, body.response_mode)

        return TapSessionResponse(
            id=row.id, name=row.name, transport=row.transport, target=row.target,
            protocol_basis=row.protocol_basis, detected_protocol=None,
            response_mode=row.response_mode, status=row.status,
            bytes_rx=0, bytes_tx=0, message_count=0,
            started_at=row.started_at.isoformat(), stopped_at=None,
        )
    finally:
        session.close()


@app.post("/api/tap/sessions/{session_id}/stop", response_model=MessageResponse)
async def stop_tap_session(session_id: int, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    _TAP_RUNNER.stop(session_id)
    return MessageResponse(message=f"Sesi tap #{session_id} dihentikan")


@app.get("/api/tap/sessions/{session_id}/events")
async def get_tap_events(session_id: int, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    return read_events(session_log_path(session_id))


@app.get("/api/tap/sessions/{session_id}/export/bin")
async def export_tap_bin(session_id: int, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    data = rx_bytes(read_events(session_log_path(session_id)))
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="tap_{session_id}.bin"'
        },
    )


@app.get("/api/tap/sessions/{session_id}/export/python")
async def export_tap_python(
    session_id: int, index: int = 0, x_api_key: str = Header(None)
):
    _verify_api_key(x_api_key)
    pesan = messages_from_events(read_events(session_log_path(session_id)))
    if not pesan:
        raise HTTPException(
            404,
            "Tidak ada pesan lengkap. Basis RAW tidak punya batas pesan — "
            "pakai export .bin.",
        )
    if index >= len(pesan):
        raise HTTPException(404, f"Pesan #{index} tidak ada (total {len(pesan)})")
    return Response(content=to_python_bytes(pesan[index]), media_type="text/plain")


class TapSendRequest(BaseModel):
    hex: str


@app.post("/api/tap/sessions/{session_id}/send", response_model=MessageResponse)
async def send_tap_manual(
    session_id: int, body: TapSendRequest, x_api_key: str = Header(None)
):
    """Kirim byte yang diketik operator — dipakai basis RAW."""
    _verify_api_key(x_api_key)
    tap = _TAP_RUNNER.get(session_id)
    if tap is None:
        raise HTTPException(404, f"Sesi #{session_id} tidak sedang jalan")
    try:
        data = bytes.fromhex(body.hex)
    except ValueError:
        raise HTTPException(
            400, "hex tidak valid — contoh yang benar: '05' atau '0b 4d 53 48'"
        )
    await tap.send_manual(data)
    return MessageResponse(message=f"{len(data)} byte terkirim")
```

Runner sesi (letakkan di atas endpoint, setelah import):

```python
class _TapRunner:
    """
    Menjalankan sesi tap sebagai task asyncio di dalam proses web console.

    Sesi tapping berumur pendek dan dioperasikan interaktif, jadi tidak dijadikan
    service systemd sendiri seperti tcp_*.
    """

    def __init__(self):
        self._sesi: dict[int, tuple] = {}    # id → (TapSession, asyncio.Task)

    def start(self, row_id, transport, basis, mode) -> None:
        async def jalan():
            path = session_log_path(row_id)
            await transport.open()
            with TapRecorder(path) as rec:
                tap = TapSession(transport, basis, rec, mode=mode)
                self._sesi[row_id] = (tap, asyncio.current_task())
                try:
                    await tap.run()
                    status, err = "stopped", None
                except Exception as e:
                    status, err = "error", str(e)
                finally:
                    tap_service._simpan_hasil(row_id, tap, status, err)
                    self._sesi.pop(row_id, None)

        asyncio.create_task(jalan())

    def stop(self, row_id: int) -> None:
        entri = self._sesi.get(row_id)
        if entri is not None:
            entri[0].stop()

    def get(self, row_id: int):
        entri = self._sesi.get(row_id)
        return entri[0] if entri else None


_TAP_RUNNER = _TapRunner()
```

Route halaman:

```python
@app.get("/tap", response_class=HTMLResponse)
async def tap_page(request: Request):
    return _templates.TemplateResponse(request, "tap.html", {"active_page": "tap"})
```

Pastikan import berikut ada di bagian atas `api.py`. `DBManager` sudah diimpor di
`api.py:36`; tambahkan `TblTapSession` ke daftar `from lib.db import (...)` yang
sudah ada, dan tambahkan sisanya:

```python
from fastapi import Response          # untuk export .bin dan text/plain
from services.tap.recorder import TapRecorder
from services.tap.session import TapSession
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_api.py -q`
Expected: PASS (13 passed)

Run: `python3 -m pytest -q`
Expected: PASS — tanpa regresi

- [ ] **Step 5: Commit**

```bash
git add services/web_console/api.py tests/test_tap_api.py
git commit -m "feat(tap): endpoint /api/tap/* — list, create, stop, events, export, send"
```

---

## Task 14: SSE stream event tap

**Files:**
- Modify: `services/web_console/api.py`
- Test: `tests/test_tap_api.py`

**Interfaces:**
- Consumes: `_TAP_RUNNER` (Task 13)
- Produces: `GET /api/tap/sessions/{id}/stream` → `text/event-stream`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tap_api.py — tambahkan
class TestStream:
    def test_sesi_mati_langsung_kirim_done(self, client, jsonl):
        # Sesi tidak ada di _TAP_RUNNER → stream mengirim event 'done' lalu tutup,
        # bukan menggantung selamanya.
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"\x05")
        with client.stream("GET", "/api/tap/sessions/1/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = "".join(resp.iter_text())
        assert '"hex": "05"' in body or '"hex":"05"' in body
        assert "event: done" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tap_api.py::TestStream -q`
Expected: FAIL — 404, route belum ada

- [ ] **Step 3: Write minimal implementation**

Tambahkan ke `services/web_console/api.py`, mengikuti pola `stream_logs` yang sudah ada (`api.py:1097`):

```python
@app.get("/api/tap/sessions/{session_id}/stream")
async def stream_tap(session_id: int, x_api_key: str = Header(None)):
    """Stream event tap via SSE — mekanisme yang sama dengan log viewer."""
    _verify_api_key(x_api_key)

    async def gen():
        path = session_log_path(session_id)
        terkirim = 0
        while True:
            events = read_events(path)
            for e in events[terkirim:]:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            terkirim = len(events)

            tap = _TAP_RUNNER.get(session_id)
            if tap is None:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tap_api.py -q`
Expected: PASS (14 passed)

- [ ] **Step 5: Commit**

```bash
git add services/web_console/api.py tests/test_tap_api.py
git commit -m "feat(tap): SSE stream event sesi tapping"
```

---

## Task 15: Halaman web console

**Files:**
- Create: `services/web_console/templates/tap.html`
- Modify: `services/web_console/templates/base.html`, `services/web_console/static/js/app.js`

**Interfaces:**
- Consumes: seluruh endpoint Task 13–14
- Produces: halaman `/tap`

- [ ] **Step 1: Tambahkan item nav**

Di `services/web_console/templates/base.html`, tambahkan setelah item nav "Protocols" (ikuti markup item nav yang sudah ada persis):

```html
<a href="/tap" class="nav-item {% if active_page == 'tap' %}active{% endif %}">
  Tapping
</a>
```

- [ ] **Step 2: Buat halaman**

```html
<!-- services/web_console/templates/tap.html -->
{% extends "base.html" %}
{% block title %}Tapping Data{% endblock %}

{% block content %}
<div class="page-header">
  <h1>Tapping Data</h1>
  <button class="btn btn-primary" id="tap-new">Sesi Baru</button>
</div>

<div class="alert alert-warning" role="alert">
  <strong>Perhatian:</strong> data yang di-tap <strong>tidak</strong> masuk
  <code>tbl_result</code> dan <strong>tidak</strong> dikirim ke LIS. Sesi ini hanya
  merekam. Jangan jalankan pada alat yang service TCP-nya sedang aktif.
</div>

<div class="card">
  <table class="table" id="tap-list">
    <thead>
      <tr>
        <th>Nama</th><th>Transport</th><th>Target</th><th>Basis</th>
        <th>Status</th><th>RX</th><th>TX</th><th>Pesan</th><th></th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</div>

<div class="card" id="tap-live" hidden>
  <div class="card-header">
    <h2 id="tap-live-title">Sesi</h2>
    <div>
      <button class="btn" id="tap-stop">Stop</button>
      <button class="btn" id="tap-export-bin">Download .bin</button>
      <button class="btn" id="tap-export-py">Copy sebagai Python bytes</button>
    </div>
  </div>

  <div class="alert alert-warning" id="tap-baud-hint" hidden>
    Byte masuk tapi tidak membentuk frame valid — <strong>cek baud rate</strong>.
  </div>

  <div class="tap-toolbar">
    <label><input type="checkbox" id="tap-show-rx" checked> RX</label>
    <label><input type="checkbox" id="tap-show-tx" checked> TX</label>
    <span id="tap-detected"></span>
  </div>

  <pre class="tap-stream" id="tap-stream"></pre>

  <div class="tap-send">
    <input type="text" id="tap-manual" placeholder="kirim manual (hex, mis. 05 06)">
    <button class="btn" id="tap-send">Kirim</button>
  </div>
</div>

<dialog id="tap-dialog">
  <form method="dialog" id="tap-form">
    <h2>Sesi Tapping Baru</h2>
    <label>Nama <input name="name" required></label>
    <label>Transport
      <select name="transport" id="tap-transport">
        <option value="tcp_server">TCP — MidLab listen (alat connect)</option>
        <option value="tcp_client">TCP — MidLab connect ke alat</option>
        <option value="serial">Serial (RS232)</option>
      </select>
    </label>
    <div class="tap-tcp-fields">
      <label>Host <input name="host" value="0.0.0.0"></label>
      <label>Port <input name="port" type="number" value="2600"></label>
    </div>
    <div class="tap-serial-fields" hidden>
      <label>Port <input name="serial_port" placeholder="/dev/ttyUSB0"></label>
      <label>Baudrate
        <select name="baudrate">
          <option>9600</option><option>19200</option>
          <option>38400</option><option>115200</option>
        </select>
      </label>
      <label>Parity
        <select name="parity"><option>N</option><option>E</option><option>O</option></select>
      </label>
    </div>
    <label>Basis Protokol
      <select name="protocol_basis">
        <option value="AUTO">AUTO — deteksi saja, tidak membalas</option>
        <option value="HL7">HL7 — balas ACK (echo MSH-10)</option>
        <option value="ASTM">ASTM — balas ENQ/frame ACK</option>
        <option value="RAW">RAW — pasif, saya yang operasikan</option>
      </select>
    </label>
    <label>Mode Jawaban
      <select name="response_mode">
        <option value="uni">uni — ACK saja</option>
        <option value="bidi">bidi — ACK + tandai query</option>
      </select>
    </label>
    <menu>
      <button value="cancel">Batal</button>
      <button value="ok" id="tap-create">Mulai</button>
    </menu>
  </form>
</dialog>
{% endblock %}
```

- [ ] **Step 3: Tambahkan logika JS**

Tambahkan ke `services/web_console/static/js/app.js`, mengikuti gaya modul halaman
lain di file itu. **Helper yang dipakai sudah ada sebagai method objek `App`**
(`app.js:6`): `App.api(path, options)` (app.js:100), `App.toast(msg, type)`
(app.js:129), `App.escapeHtml(text)` (app.js:283). Jangan buat helper baru.
`App.api()` tidak mengirim header API key — memang tidak perlu, karena
`_verify_api_key()` langsung return bila `web_console.api_key` kosong di config.

```javascript
// ============================================================
// Tapping Data
// ============================================================

const Tap = {
  sesiAktif: null,
  events: [],

  async muatDaftar() {
    const rows = await App.api('/api/tap/sessions');
    const tbody = document.querySelector('#tap-list tbody');
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${App.escapeHtml(r.name)}</td>
        <td>${App.escapeHtml(r.transport)}</td>
        <td><code>${App.escapeHtml(r.target)}</code></td>
        <td>${App.escapeHtml(r.detected_protocol || r.protocol_basis)}</td>
        <td><span class="badge badge-${App.escapeHtml(r.status)}">${App.escapeHtml(r.status)}</span></td>
        <td>${r.bytes_rx}</td>
        <td>${r.bytes_tx}</td>
        <td>${r.message_count}</td>
        <td><button class="btn btn-sm" data-tap-open="${r.id}">Buka</button></td>
      </tr>
    `).join('');
  },

  // Hex + ASCII berdampingan: byte kontrol (ENQ/ACK/STX) tidak kelihatan di ASCII,
  // jadi hex wajib ada.
  baris(ev, prev) {
    const bytes = ev.hex.match(/../g) || [];
    const hex = bytes.join(' ');
    const ascii = bytes
      .map(h => {
        const b = parseInt(h, 16);
        return b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : '.';
      })
      .join('');
    const delta = prev
      ? `+${(new Date(ev.t) - new Date(prev.t)).toString().padStart(5)}ms`
      : '    0ms';
    const arah = ev.dir === 'rx' ? '←RX' : '→TX';
    const note = ev.note ? ` (${ev.note})` : '';
    return `${delta} ${arah}${note}  ${hex}\n              ${ascii}`;
  },

  render() {
    const showRx = document.getElementById('tap-show-rx').checked;
    const showTx = document.getElementById('tap-show-tx').checked;
    const el = document.getElementById('tap-stream');
    let prev = null;
    const baris = [];
    for (const ev of this.events) {
      if (ev.dir === 'meta') continue;
      if (ev.dir === 'rx' && !showRx) continue;
      if (ev.dir === 'tx' && !showTx) continue;
      baris.push(this.baris(ev, prev));
      prev = ev;
    }
    el.textContent = baris.join('\n');
    el.scrollTop = el.scrollHeight;
  },

  buka(id) {
    this.sesiAktif = id;
    this.events = [];
    document.getElementById('tap-live').hidden = false;
    document.getElementById('tap-live-title').textContent = `Sesi #${id}`;

    const es = new EventSource(`/api/tap/sessions/${id}/stream`);
    es.onmessage = e => {
      this.events.push(JSON.parse(e.data));
      this.render();
    };
    es.addEventListener('done', () => {
      es.close();
      this.muatDaftar();
    });
  },

  async kirimManual() {
    const hex = document.getElementById('tap-manual').value.replace(/\s+/g, '');
    if (!hex) return;
    try {
      await App.api(`/api/tap/sessions/${this.sesiAktif}/send`, {
        method: 'POST', body: { hex },
      });
      document.getElementById('tap-manual').value = '';
    } catch (err) {
      App.toast(err.message, 'error');
    }
  },

  async exportPython() {
    // Bukan lewat App.api(): responsnya text/plain, bukan JSON.
    const r = await fetch(`/api/tap/sessions/${this.sesiAktif}/export/python?index=0`);
    if (!r.ok) {
      App.toast('Tidak ada pesan lengkap — pakai export .bin', 'error');
      return;
    }
    await navigator.clipboard.writeText(await r.text());
    App.toast('Tersalin — siap ditempel ke file test', 'success');
  },

  init() {
    document.getElementById('tap-new').onclick = () =>
      document.getElementById('tap-dialog').showModal();

    document.getElementById('tap-transport').onchange = e => {
      const serial = e.target.value === 'serial';
      document.querySelector('.tap-tcp-fields').hidden = serial;
      document.querySelector('.tap-serial-fields').hidden = !serial;
    };

    document.getElementById('tap-form').onsubmit = async e => {
      if (e.submitter?.value !== 'ok') return;
      const body = Object.fromEntries(new FormData(e.target));
      if (body.port) body.port = parseInt(body.port, 10);
      if (body.baudrate) body.baudrate = parseInt(body.baudrate, 10);
      try {
        const r = await App.api('/api/tap/sessions', { method: 'POST', body });
        await this.muatDaftar();
        this.buka(r.id);
      } catch (err) {
        // Termasuk 409 saat port dipakai alat aktif — pesannya sudah jelas.
        App.toast(err.message, 'error');
      }
    };

    document.getElementById('tap-list').onclick = e => {
      const id = e.target.dataset.tapOpen;
      if (id) this.buka(parseInt(id, 10));
    };

    document.getElementById('tap-stop').onclick = () =>
      App.api(`/api/tap/sessions/${this.sesiAktif}/stop`, { method: 'POST' });
    document.getElementById('tap-export-bin').onclick = () =>
      window.location = `/api/tap/sessions/${this.sesiAktif}/export/bin`;
    document.getElementById('tap-export-py').onclick = () => this.exportPython();
    document.getElementById('tap-send').onclick = () => this.kirimManual();
    document.getElementById('tap-show-rx').onchange = () => this.render();
    document.getElementById('tap-show-tx').onchange = () => this.render();

    this.muatDaftar();
  },
};

if (document.getElementById('tap-list')) Tap.init();
```

- [ ] **Step 4: Verifikasi manual**

Endpoint `/send` yang dipanggil halaman ini sudah dibuat di Task 13 — tidak ada
kode backend baru di sini.

Run: `python3 -m pytest -q`
Expected: PASS — tanpa regresi

Jalankan web console lokal, buka `/tap`, buat sesi `tcp_server` port 2600 basis HL7, lalu di terminal lain:
Run: `python3 scripts/aruma_ar580_test_sender.py --port 2600 --scenario result`
Expected: simulator lapor `result : OK`; halaman menampilkan hex+ASCII, RX dan TX, jumlah pesan 1; tombol "Copy sebagai Python bytes" menghasilkan literal yang valid

Uji juga guard-nya: buat sesi `tcp_server` di port yang dipakai alat aktif.
Expected: toast merah berisi nama alat yang memakai port itu, sesi tidak dibuat

- [ ] **Step 5: Commit**

```bash
git add services/web_console/templates/tap.html services/web_console/templates/base.html services/web_console/static/js/app.js
git commit -m "feat(tap): halaman web console — hex/ASCII live, kirim manual, export"
```

---

## Task 16: Dokumentasi

**Files:**
- Modify: `PANDUAN-ALAT-BARU.md`

- [ ] **Step 1: Tambahkan bab tapping**

Sisipkan di `PANDUAN-ALAT-BARU.md`, tepat setelah bab "## 2. Identifikasi Protocol Alat" (sebelum "## 3. Menambah Alat via Web Console"):

```markdown
---

## 2b. Tapping Data — menangkap komunikasi alat baru

Sebelum menulis driver, tangkap dulu komunikasi aslinya. Menu **Tapping** (`/tap`)
menggantikan langkah Wireshark/serial monitor di bab 2.

> **Data yang di-tap TIDAK masuk `tbl_result` dan TIDAK dikirim ke LIS.**
> Jangan menjalankannya pada alat yang service TCP-nya sedang aktif — MidLab
> menolak port yang dipakai alat aktif, karena dua pihak tidak boleh meng-ACK
> alat yang sama.

### Kenapa MidLab perlu membalas

Mode pasif murni sering tidak menghasilkan apa-apa: **alat tidak akan mengirim**
bila handshake-nya menggantung. AR580, misalnya, kirim ulang tiap 3 detik lalu
menyerah. Karena itu tapping membalas handshake — dan itu bisa dilakukan tanpa
driver, karena handshake tidak bergantung pada isi pesan:

| Basis | Yang dibalas |
|---|---|
| `AUTO` | Tidak membalas — hanya menebak protokolnya lalu melapor |
| `HL7` | ACK tiap pesan MLLP, dengan MSA-2 memantulkan MSH-10 |
| `ASTM` | ACK untuk ENQ dan tiap frame |
| `RAW` | Tidak pernah membalas — Anda yang kirim manual |

Mode jawaban `bidi` **tidak mengarang jawaban query**: ia meng-ACK, menandai
query-nya, lalu diam. Menjawab query dengan order sungguhan butuh driver yang
justru sedang Anda buat — yang dikejar di sini formatnya.

### Langkah

1. Buka `/tap` → **Sesi Baru**
2. Pilih transport:
   - **TCP — MidLab listen**: alat connect ke MidLab (paling umum)
   - **TCP — MidLab connect**: alat menunggu di-connect
   - **Serial**: colok langsung RS232 ke laptop (butuh keanggotaan grup `dialout`)
3. Pilih basis protokol. Mulai dari `AUTO` bila belum tahu — ia akan menebak
   berdasar ENQ (0x05) atau string `MSH|`, lalu Anda ulangi sesi dengan basis
   yang tepat supaya alat mau mengirim.
4. Jalankan pemeriksaan di alat, lalu amati hex + ASCII yang masuk.

### Salah baud rate menyerupai masalah protokol

Kalau byte masuk tapi tidak membentuk frame valid, halaman akan menampilkan
peringatan **"cek baud rate"**. Ini jebakan klasik: setelan serial yang salah
menghasilkan byte sampah yang persis mirip protokol tak dikenal. Cek baudrate,
parity, dan stopbits di alat sebelum menyimpulkan protokolnya aneh.

### Dari capture ke driver

| Tombol | Untuk |
|---|---|
| **Download `.bin`** | Byte RX mentah — arsip dan parse ulang |
| **Copy sebagai Python bytes** | Tempel langsung jadi fixture test |

Tombol kedua yang menutup lingkaran. Test driver di repo ini semuanya berjangkar
pada byte string verbatim (lihat `ORU_DOC` di `tests/test_aruma_ar580.py`). Saat
driver AR580 dibuat, transkripsi manual dari PDF sempat salah menghitung pipa di
segment OBR dan baru tertangkap oleh test — export otomatis menghapus seluruh
kelas kesalahan itu.

Setelah punya capture, serahkan ke Claude Code untuk dianalisis dan ditulis
driver-nya, lalu lanjut ke bab 6 (Membuat Protocol Module Baru).

### Via CLI (tanpa web console)

```bash
python3 -m services.tap.service --name "AR580" --transport tcp_server \
    --port 2600 --basis HL7
python3 -m services.tap.service --name "Sysmex" --transport serial \
    --serial-port /dev/ttyUSB0 --baudrate 9600 --basis AUTO
```

Capture tersimpan di `/var/log/midlab/tap/<id>.jsonl`.
```

- [ ] **Step 2: Tambahkan ke daftar isi**

Di daftar isi `PANDUAN-ALAT-BARU.md` (sekitar baris 8), tambahkan entri
"2b. Tapping Data" setelah entri "2. Identifikasi Protocol Alat", mengikuti
format entri yang sudah ada.

- [ ] **Step 3: Verifikasi**

Run: `python3 -m pytest -q`
Expected: PASS

Run: `grep -c "Tapping Data" PANDUAN-ALAT-BARU.md`
Expected: `2` (daftar isi + judul bab)

- [ ] **Step 4: Commit**

```bash
git add PANDUAN-ALAT-BARU.md
git commit -m "docs: bab Tapping Data di PANDUAN-ALAT-BARU"
```

---

## Verifikasi Akhir

- [ ] **Suite penuh tanpa regresi**

Run: `python3 -m pytest -q`
Expected: 163 (baseline) + ~80 test tap = ~243 passed, 6 skipped, 0 failed

- [ ] **Deploy**

```bash
sudo ./scripts/deploy.sh --dry-run
sudo ./scripts/deploy.sh
sudo -u midlab python3 /opt/midlab/scripts/migrate_tap_session.py
```

Expected: `OK: tbl_tap_session dibuat.`

- [ ] **Asap produksi**

Buka `/tap`, buat sesi `tcp_server` port 2600 basis HL7, jalankan
`python3 scripts/aruma_ar580_test_sender.py --port 2600`, pastikan simulator
lapor `OK` dan halaman menampilkan 1 pesan.
