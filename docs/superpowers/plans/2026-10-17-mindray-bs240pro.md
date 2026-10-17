# Driver HL7_MINDRAY_BS240PRO_360E_240E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Driver protocol baru untuk Mindray BS-240Pro/BS-360E/BS-240E (HL7 2.3.1 + MLLP): hasil pasien, QC otomatis ke EazyLab (termasuk multi-kontrol per pesan), query dan broadcast.

**Architecture:** Paket `protocols/mindray_bs240pro/` berisi subclass tipis dari driver `protocols/mindray_bs200e/` — hanya parsing QC, identitas model, DSP-26, dan broadcast MSH-15 yang ditulis ulang. TCPSocketService mendapat hook opsional `split_results()` agar satu pesan QC multi-kontrol disimpan sebagai satu baris `tbl_result` per kontrol. `scripts/qc_backfill.py` memakai hook yang sama.

**Tech Stack:** Python 3.10+, pytest (`asyncio_mode = auto`), driver HL7 BS-200E yang ada.

**Spec:** `docs/superpowers/specs/2026-10-17-mindray-bs240pro-design.md`

## Global Constraints

- Nama protokol: `HL7_MINDRAY_BS240PRO_360E_240E` (30 karakter; kolom `protocol` VARCHAR(50), tanpa migrasi DB).
- Paket Python: `protocols/mindray_bs240pro/`; class module: `MindrayBS240ProModule`.
- Model default di MSH-6/QRF-1: `BS-240Pro`.
- Driver BS-200E (`protocols/mindray_bs200e/`) **tidak boleh diubah**.
- Induk diimpor lewat alias modul (`from protocols.mindray_bs200e import module as _bs200e`), bukan nama class langsung — `load_module` memilih class pertama hasil `dir()`.
- Fixture test berasal dari log BS-360E asli, **dianonimkan** (nama pasien/NIK/RM fiktif); kode tes, nilai QC, lot, timestamp dipertahankan.
- Jalankan test dengan `.venv/bin/python -m pytest` (bukan `python3`). Baseline sebelum mulai: 674 passed, 4 failed (`tests/test_tap_transport.py::TestSerialTransport`, pre-existing), 3 skipped.
- Komentar & docstring Bahasa Indonesia, gaya mengikuti driver BS-200E.
- Commit diakhiri baris `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Kerja di branch `feat/mindray-bs240pro` (sudah ada).

## File Structure

| File | Aksi | Tanggung jawab |
|---|---|---|
| `protocols/mindray_bs240pro/__init__.py` | Create | penanda paket |
| `protocols/mindray_bs240pro/constants.py` | Create | re-export konstanta BS-200E + nama/model/posisi QC/pola level |
| `protocols/mindray_bs240pro/parser.py` | Create | `BS240ProParser`: `parse_msh` (model default), `parse_obr_qc` multi-kontrol |
| `protocols/mindray_bs240pro/builder.py` | Create | `BS240ProBuilder`: model default, MSH-15, DSP-26 |
| `protocols/mindray_bs240pro/module.py` | Create | `MindrayBS240ProModule`, `resolve_qc_level`, `split_results`, `format_order` |
| `protocols/base.py` | Modify (registry, ±baris 161) | daftarkan nama protokol |
| `services/tcp_socket/receiver.py` | Modify (`_parse_and_save`, ±baris 492-541) | hook `split_results` |
| `scripts/qc_backfill.py` | Modify (`_collect`, docstring) | hook `split_results` |
| `scripts/mindray_bs240pro_test_sender.py` | Create | simulator alat (mode konek & listen) |
| `tests/test_mindray_bs240pro.py` | Create | seluruh test driver + receiver + backfill |

---

### Task 1: Kerangka paket, registry, dan hasil pasien

**Files:**
- Create: `protocols/mindray_bs240pro/__init__.py`, `constants.py`, `parser.py`, `builder.py`, `module.py`
- Modify: `protocols/base.py` (dict `_PROTOCOL_REGISTRY`)
- Test: `tests/test_mindray_bs240pro.py`

**Interfaces:**
- Consumes: `protocols.mindray_bs200e.module.MindrayBS200EModule`, `protocols.mindray_bs200e.parser.MindrayParser`, `field()`, `protocols.mindray_bs200e.builder.MindrayBuilder`
- Produces:
  - `protocols.mindray_bs240pro.constants`: `PROTOCOL_NAME`, `PROTOCOL_VERSION`, `DEFAULT_MODEL`, `MSH15_LIS_DOWNLOAD`, `MSH15_INDEX`, `QC_LEVEL_FROM_NAME`, `INSTRUMENT_LEVEL_TO_NUMBER`, `SAMPLE_TYPE_LABELS`, `QC_CONTROLS_KEY` + semua konstanta BS-200E
  - `BS240ProParser(MindrayParser)`, `BS240ProBuilder(MindrayBuilder)` dengan `_with_model(context) -> dict`
  - `MindrayBS240ProModule.parse(raw_bytes, instrument) -> dict` dengan `protocol == PROTOCOL_NAME`

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_mindray_bs240pro.py`:

```python
"""
Test driver HL7_MINDRAY_BS240PRO_360E_240E (BS-240Pro / BS-360E / BS-240E).

Fixture diambil dari log komunikasi BS-360E lapangan (7 Okt 2024) dan
DIANONIMKAN: nama pasien, NIK, dan nomor RM diganti nilai fiktif. Kode tes,
nilai QC, lot, dan timestamp dibiarkan asli. Contoh QC multi-kontrol diambil
dari manual "BS-360E & BS-240Pro & BS-240E LIS Interface Manual V1.0" bab 1.3.
"""

import pytest

from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from protocols.mindray_bs240pro.module import MindrayBS240ProModule


PROTOCOL = "HL7_MINDRAY_BS240PRO_360E_240E"

ORU_SAMPLE = (
    b"\x0b"
    b"MSH|^~\\&|||||20241007002452||ORU^R01|1134|P|2.3.1||||0||ASCII|||\r"
    b"PID|570||||PASIEN UJI|||O|||||||||||||||||||||||\r"
    b"OBR|570|2191|1|^|N|20241007000923|20241007000905|20241007000905||1^10"
    b"||||20241007000905|Serum|||||||||||||||||||||||||||||||||\r"
    b"OBX|1|NM|GLU|Glucose (GOD-POD Method)|105.805265|mg/dL|-|N|||F||105.805265|20241007002451|||0||\r"
    b"OBX|2|NM|ALT|Alanine Aminotransferase|6.181722|U/L|-|N|||F||6.181722|20241007002431|||0||\r"
    b"OBX|3|NM|AST|Aspartate Aminotransferase|9.087522|U/L|-|N|||F||9.087522|20241007002441|||0||\r"
    b"OBX|4|NM|CREA|Creatinine (Sarcosine Oxidase Method)|14.931468|mg/dL|-|N|||F||14.931468|20241007002441|||0||\r"
    b"OBX|5|NM|UREA|Urea|98.026045|mg/dL|-|N|||F||98.026045|20241007002202|||0||\r"
    b"\x1c\x0d"
)

INSTRUMENT = {"id": 9, "name": "Mindray BS-240Pro", "protocol": PROTOCOL}


@pytest.fixture
def module():
    return MindrayBS240ProModule()


# ============================================================
# Registry & identitas
# ============================================================

def test_terdaftar_dan_load_module_mengembalikan_class_bs240pro():
    # load_module memilih class pertama hasil dir(); induk BS-200E tidak boleh
    # ikut terpilih.
    assert _PROTOCOL_REGISTRY[PROTOCOL] == "protocols.mindray_bs240pro.module"
    mod = load_module(PROTOCOL)
    assert type(mod) is MindrayBS240ProModule
    assert mod.PROTOCOL_NAME == PROTOCOL


def test_dianggap_keluarga_mllp():
    assert is_mllp_protocol(PROTOCOL) is True


# ============================================================
# Hasil pasien (MSH-16 = 0)
# ============================================================

def test_parse_hasil_pasien(module):
    parsed = module.parse(ORU_SAMPLE, INSTRUMENT)

    assert parsed["protocol"] == PROTOCOL
    assert parsed["instrument_id"] == 9
    assert parsed["patient"]["name"] == "PASIEN UJI"
    assert parsed["specimen"]["sample_id"] == "2191"      # OBR-2 = Lab No
    assert [r["test_code"] for r in parsed["results"]] == ["GLU", "ALT", "AST", "CREA", "UREA"]
    assert parsed["results"][0] == {
        "test_code": "GLU",
        "test_name": "Glucose (GOD-POD Method)",
        "value": "105.805265",
        "unit": "mg/dL",
        "reference_range": "",
        "flag": "N",
        "status": "F",
    }
    assert parsed["parse_errors"] == []
    assert "qc" not in parsed


def test_ack_r01_memakai_model_bs240pro_saat_msh4_kosong(module):
    # Alat di lapangan mengosongkan MSH-3/4; fallback MSH-6 harus BS-240Pro,
    # bukan BS-200E milik driver induk.
    ack = module.build_ack_response(ORU_SAMPLE, INSTRUMENT)
    assert ack.startswith(b"\x0bMSH|^~\\&|||Mindray|BS-240Pro|")
    assert b"MSA|AA|1134|Message accepted|||0|" in ack
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py -v`
Expected: ERROR saat collect — `ModuleNotFoundError: No module named 'protocols.mindray_bs240pro'`

- [ ] **Step 3: Buat paket**

`protocols/mindray_bs240pro/__init__.py`:

```python
"""Driver HL7 Mindray BS-240Pro / BS-360E / BS-240E."""
```

`protocols/mindray_bs240pro/constants.py`:

```python
"""
protocols/mindray_bs240pro/constants.py — Konstanta HL7 Mindray BS-240Pro / BS-360E / BS-240E

Referensi: "BS-360E & BS-240Pro & BS-240E LIS Interface Manual V1.0" bab 1
(HL7 v2.3.1 + MLLP). Bab HL7 manual ini sama dengan Host Interface Manual v6.0
milik BS-200E, jadi seluruh konstanta BS-200E di-re-export; di sini hanya yang
berbeda.
"""

import re

from protocols.mindray_bs200e.constants import *  # noqa: F401,F403 — re-export


# ============================================================
# Identitas module
# ============================================================

PROTOCOL_NAME = "HL7_MINDRAY_BS240PRO_360E_240E"
PROTOCOL_VERSION = "1.0.0"

# MSH-6 / QRF-1 bila alat tidak mengisi MSH-4 (log BS-360E: kosong).
DEFAULT_MODEL = "BS-240Pro"


# ============================================================
# MSH-15 — Accept Acknowledgment Type
#
# Manual: "When LIS sends sample request information to the chemistry
# analyzer, the 15 field should be set to P." Dipakai DSR^Q03 broadcast
# (tanpa QRY dari alat); balasan query membiarkannya kosong.
# ============================================================

MSH15_LIS_DOWNLOAD = "P"
MSH15_INDEX = 14    # index list hasil split("|") segmen MSH = MSH-15


# ============================================================
# QC (MSH-16 = 2) — level kontrol
#
# Di lapangan OBR-17 selalu "M" walau kontrol bernama "CTRL L1"/"CTRL L2",
# jadi level diambil dari nama kontrol lebih dulu.
# ============================================================

QC_LEVEL_FROM_NAME = re.compile(r"\b(?:L|LV|LVL|LEVEL)\s*[-_]?\s*([123])\b", re.IGNORECASE)

# OBR-17 alat → nomor level EazyLab (sama dengan _QC_LEVEL_MAP ResultPusher)
INSTRUMENT_LEVEL_TO_NUMBER = {"L": "1", "M": "2", "H": "3"}

# Kunci internal di dict hasil parse(): daftar kontrol untuk split_results().
# Tidak pernah disimpan ke tbl_result (dibuang split_results).
QC_CONTROLS_KEY = "_qc_controls"


# ============================================================
# DSP-26 — jenis sampel; format yang terbukti diterima BS-360E di log
# ============================================================

SAMPLE_TYPE_LABELS = {"serum": "Serum", "plasma": "Plasma", "urine": "Urine"}
```

`protocols/mindray_bs240pro/parser.py`:

```python
"""
protocols/mindray_bs240pro/parser.py — Parser HL7 Mindray BS-240Pro / BS-360E / BS-240E

Turunan MindrayParser (BS-200E). Yang berbeda: model default MSH dan layout
OBR pesan QC (lihat parse_obr_qc).
"""

from lib.utils import get_logger
from protocols.mindray_bs200e.parser import MindrayParser, field
from protocols.mindray_bs240pro.constants import DEFAULT_MODEL


logger = get_logger("mindray_bs240pro_parser")


class BS240ProParser(MindrayParser):
    """Parser HL7 BS-240Pro (arah alat → LIS)."""

    def parse_msh(self, fields: list) -> dict:
        """Seperti induk, tapi fallback model = BS-240Pro bila MSH-4 kosong."""
        parsed = super().parse_msh(fields)
        if not field(fields, 4):
            parsed["model"] = DEFAULT_MODEL
        return parsed
```

`protocols/mindray_bs240pro/builder.py`:

```python
"""
protocols/mindray_bs240pro/builder.py — Builder HL7 Mindray BS-240Pro / BS-360E / BS-240E

Turunan MindrayBuilder (BS-200E). Yang berbeda: model default di MSH-6/QRF-1.
"""

from protocols.mindray_bs200e.builder import MindrayBuilder
from protocols.mindray_bs240pro.constants import DEFAULT_MODEL


class BS240ProBuilder(MindrayBuilder):
    """Builder pesan HL7 BS-240Pro (arah LIS → alat)."""

    def _with_model(self, context: dict) -> dict:
        """
        Salinan context dengan `model` terisi. Builder induk jatuh ke model
        BS-200E bila context tidak membawa model (mis. broadcast).
        """
        ctx = dict(context or {})
        if not ctx.get("model"):
            ctx["model"] = DEFAULT_MODEL
        return ctx

    def _build_msh(self, event: str, control_id: str, context: dict,
                   result_type: str = "") -> str:
        return super()._build_msh(event, control_id, self._with_model(context), result_type)

    def _build_qrf(self, context: dict) -> str:
        return super()._build_qrf(self._with_model(context))
```

`protocols/mindray_bs240pro/module.py`:

```python
"""
protocols/mindray_bs240pro/module.py — Protocol Module Mindray BS-240Pro / BS-360E / BS-240E

Implementasi BaseProtocolModule untuk chemistry analyzer Mindray BS-240Pro,
BS-360E, dan BS-240E (HL7 v2.3.1 + MLLP), sesuai "BS-360E & BS-240Pro &
BS-240E LIS Interface Manual V1.0" bab 1.

Turunan driver BS-200E — MLLP, hasil pasien, query (QRY^Q02 → QCK^Q02 +
DSR^Q03), group download, dan ACK dipakai ulang. Yang berbeda:

- QC: OBR-2 sudah kode tes, satu pesan bisa membawa beberapa kontrol
  (V1^V2^…), level diambil dari nama kontrol lebih dulu
- broadcast resmi: DSR^Q03 dengan MSH-15 = P
- model default BS-240Pro di MSH-6/QRF-1, DSP-26 huruf awal kapital

Di-load dynamic via protocols.base.load_module("HL7_MINDRAY_BS240PRO_360E_240E").
"""

from lib.utils import get_logger
# Induk diimpor lewat alias modul, BUKAN `from ... import MindrayBS200EModule`:
# load_module() memilih class pertama hasil dir() yang mewarisi
# BaseProtocolModule, dan "MindrayBS200EModule" < "MindrayBS240ProModule".
from protocols.mindray_bs200e import module as _bs200e
from protocols.mindray_bs240pro.builder import BS240ProBuilder
from protocols.mindray_bs240pro.constants import PROTOCOL_NAME, PROTOCOL_VERSION
from protocols.mindray_bs240pro.parser import BS240ProParser


class MindrayBS240ProModule(_bs200e.MindrayBS200EModule):
    """Protocol module Mindray BS-240Pro / BS-360E / BS-240E."""

    def __init__(self):
        super().__init__()
        self._parser = BS240ProParser()
        self._builder = BS240ProBuilder()
        self._logger = get_logger("mindray_bs240pro")

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """Seperti induk; field `protocol` diganti nama driver ini."""
        parsed = super().parse(raw_bytes, instrument)
        parsed["protocol"] = PROTOCOL_NAME
        return parsed
```

Tambahkan ke `_PROTOCOL_REGISTRY` di `protocols/base.py`, tepat setelah baris `"HL7_MINDRAY_BC5150": ...`:

```python
    "HL7_MINDRAY_BS240PRO_360E_240E": "protocols.mindray_bs240pro.module",
```

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py tests/test_mindray_bs200e.py -v`
Expected: semua PASS (test BS-200E tetap hijau).

- [ ] **Step 5: Commit**

```bash
git add protocols/mindray_bs240pro protocols/base.py tests/test_mindray_bs240pro.py
git commit -m "feat(protocol): kerangka driver HL7_MINDRAY_BS240PRO_360E_240E

Subclass driver BS-200E untuk BS-240Pro/BS-360E/BS-240E: hasil pasien,
ACK^R01, dan model default BS-240Pro.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Parsing QC multi-kontrol, level kontrol, dan `split_results`

**Files:**
- Modify: `protocols/mindray_bs240pro/parser.py`, `protocols/mindray_bs240pro/module.py`
- Test: `tests/test_mindray_bs240pro.py`

**Interfaces:**
- Consumes: Task 1 (`BS240ProParser`, `MindrayBS240ProModule`, konstanta `QC_LEVEL_FROM_NAME`, `INSTRUMENT_LEVEL_TO_NUMBER`, `QC_CONTROLS_KEY`); `_bs200e._strip_lot_prefix`, `_bs200e._hl7_date`; `lib.models.SpecimenInfo`, `TestResult`; `lib.utils.generate_message_id`
- Produces:
  - `BS240ProParser.parse_obr_qc(fields) -> dict` dengan kunci `test_code, test_name, sample_type, start_datetime, finish_datetime, declared_count, unit, instrument_flags, controls` (list dict: `control_no, control_name, lot_number, expiration_date, level, mean_value, standard_deviation, value`)
  - `resolve_qc_level(control_name: str, instrument_level: str) -> tuple[str, bool]` — (level, konflik)
  - `MindrayBS240ProModule.split_results(result_dict: dict) -> list[dict]`
  - Hasil `parse()` pesan QC: kunci `qc` (kontrol pertama) + `_qc_controls` (list entri `{"result": dict, "comments": list, "qc": dict}`)

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan ke `tests/test_mindray_bs240pro.py` (import `logging` di atas file):

```python
import logging

from protocols.mindray_bs240pro.module import resolve_qc_level


# QC tunggal dari log (DBIL, CTRL L1, OBR-17 "M", flag Westgard alat)
ORU_QC = (
    b"\x0b"
    b"MSH|^~\\&|||||20241007031434||ORU^R01|1141|P|2.3.1||||2||ASCII|||\r"
    b"OBR|255|DBIL|D-Bil-V|^|Serum|20241007003327|20241007003250|20241007031212|||1|8"
    b"|CTRL L1|059324003|20261231||M|1.050000|0.160000|0.525642|mg/dL|||||||RGTE,1-3s,2.7s"
    b"||||||||||||||||||||\r"
    b"\x1c\x0d"
)

# Multi-kontrol, contoh manual bab 1.3 (dua kontrol dalam satu OBR)
ORU_QC_MULTI = (
    b"\x0b"
    b"MSH|^~\\&|||||20120508103014||ORU^R01|1|P|2.3.1||||2||ASCII|||\r"
    b"OBR|1|AST|AST|^|Serum|20160509115017|20160509114958|20160509115857|||2|1^2"
    b"|QUAL1^QUAL2|1111^2222|20300101^20300101||L^M|45.000000^55.000000|5.000000^5.000000"
    b"|0.130291^0.137470|mg/dL|||||||RGTE||||||||||||||||||||\r"
    b"\x1c\x0d"
)


# ============================================================
# Level kontrol
# ============================================================

@pytest.mark.parametrize("name, inst, expected", [
    ("CTRL L1", "M", ("1", True)),       # kasus lapangan: nama menang, konflik
    ("CTRL L2", "M", ("2", False)),
    ("Level 3", "H", ("3", False)),
    ("LV3", "", ("3", False)),
    ("QC-L 2", "L", ("2", True)),
    ("KONTROL NORMAL", "M", ("M", False)),   # tidak ada pola → OBR-17
    ("", "h", ("H", False)),
    ("CTRL1", "L", ("L", False)),        # "L" tanpa batas kata bukan pola level
])
def test_resolve_qc_level(name, inst, expected):
    assert resolve_qc_level(name, inst) == expected


# ============================================================
# QC (MSH-16 = 2)
# ============================================================

def test_parse_qc_tunggal(module, caplog):
    with caplog.at_level(logging.WARNING):
        parsed = module.parse(ORU_QC, INSTRUMENT)

    assert parsed["protocol"] == PROTOCOL
    assert parsed["specimen"]["sample_type"] == "qc"
    assert parsed["specimen"]["collected_at"] == "20241007031212"   # OBR-8
    assert parsed["results"] == [{
        "test_code": "DBIL",            # OBR-2 langsung, tanpa tabel padanan
        "test_name": "D-Bil-V",
        "value": "0.525642",
        "unit": "mg/dL",
        "reference_range": "",
        "flag": "",                     # flag alat hanya di komentar
        "status": "qc",
    }]
    assert parsed["qc"] == {
        "lot_number": "059324003",
        "level": "1",                   # dari nama "CTRL L1", bukan OBR-17 "M"
        "control_name": "CTRL L1",
        "expiry_date": "2026-12-31",
        "run_at": "20241007031212",
        "targets": {"DBIL": {"mean": "1.050000", "sd": "0.160000"}},
    }
    assert "instrument_flags: RGTE,1-3s,2.7s" in parsed["comments"]
    assert "control_no: 8" in parsed["comments"]
    assert parsed["parse_errors"] == []
    assert len(parsed["_qc_controls"]) == 1
    assert any("level dari nama = 1, dari alat = M" in r.message for r in caplog.records)


def test_parse_qc_multi_kontrol_qc_utama_kontrol_pertama(module):
    parsed = module.parse(ORU_QC_MULTI, INSTRUMENT)

    assert parsed["qc"]["lot_number"] == "1111"
    assert parsed["qc"]["level"] == "L"          # QUAL1 tanpa pola → OBR-17
    assert parsed["results"][0]["value"] == "0.130291"
    assert len(parsed["_qc_controls"]) == 2
    assert parsed["parse_errors"] == []


def test_split_results_satu_baris_per_kontrol(module):
    parsed = module.parse(ORU_QC_MULTI, INSTRUMENT)
    rows = module.split_results(parsed)

    assert len(rows) == 2
    assert [r["qc"]["lot_number"] for r in rows] == ["1111", "2222"]
    assert [r["qc"]["level"] for r in rows] == ["L", "M"]
    assert [r["qc"]["control_name"] for r in rows] == ["QUAL1", "QUAL2"]
    assert rows[1]["qc"]["targets"] == {"AST": {"mean": "55.000000", "sd": "5.000000"}}
    assert [r["results"][0]["value"] for r in rows] == ["0.130291", "0.137470"]
    assert "control_name: QUAL2" in rows[1]["comments"]
    assert "control_name: QUAL1" not in rows[1]["comments"]
    assert rows[0]["message_id"] != rows[1]["message_id"]
    assert all("_qc_controls" not in r for r in rows)


def test_split_results_qc_tunggal_dan_pasien_satu_baris(module):
    qc_rows = module.split_results(module.parse(ORU_QC, INSTRUMENT))
    assert len(qc_rows) == 1 and "_qc_controls" not in qc_rows[0]
    assert qc_rows[0]["qc"]["lot_number"] == "059324003"

    pasien = module.parse(ORU_SAMPLE, INSTRUMENT)
    assert module.split_results(pasien) == [pasien]


def test_obr11_tidak_cocok_dicatat_parse_errors(module):
    raw = ORU_QC_MULTI.replace(b"|||2|1^2|", b"|||3|1^2|")
    parsed = module.parse(raw, INSTRUMENT)
    assert any("OBR-11" in e for e in parsed["parse_errors"])
    assert len(module.split_results(parsed)) == 2
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py -v`
Expected: ERROR collect — `ImportError: cannot import name 'resolve_qc_level'`

- [ ] **Step 3: Implementasi parser**

Tambahkan ke `protocols/mindray_bs240pro/parser.py` — ganti baris import konstanta menjadi:

```python
from protocols.mindray_bs240pro.constants import COMPONENT_SEP, DEFAULT_MODEL
```

lalu tambahkan fungsi modul (di atas class) dan method baru di `BS240ProParser`:

```python
def components(value: str) -> list:
    """Pecah field V1^V2^… menjadi list komponen; field kosong → []."""
    if not value:
        return []
    return [part.strip() for part in value.split(COMPONENT_SEP)]


def _nth(parts: list, index: int) -> str:
    return parts[index] if index < len(parts) else ""
```

```python
    def parse_obr_qc(self, fields: list) -> dict:
        """
        Petakan OBR hasil QC (MSH-16 = 2) versi BS-240Pro.

        Beda dengan BS-200E: OBR-2 = kode tes (sama dengan OBX-3 pasien),
        OBR-6/8 = waktu mulai/selesai, dan field 12–15 & 17–20 berformat
        V1^V2^… — satu komponen per kontrol. Jumlah kontrol mengikuti
        komponen lot (OBR-14) karena lot wajib bagi EazyLab.
        """
        numbers = components(field(fields, 12))
        names = components(field(fields, 13))
        lots = components(field(fields, 14))
        expiries = components(field(fields, 15))
        levels = components(field(fields, 17))
        means = components(field(fields, 18))
        sds = components(field(fields, 19))
        values = components(field(fields, 20))

        controls = [
            {
                "control_no": _nth(numbers, i),
                "control_name": _nth(names, i),
                "lot_number": _nth(lots, i),
                "expiration_date": _nth(expiries, i),
                "level": _nth(levels, i),
                "mean_value": _nth(means, i),
                "standard_deviation": _nth(sds, i),
                "value": _nth(values, i),
            }
            for i in range(max(len(lots), 1))
        ]

        parsed = {
            "test_code": field(fields, 2),
            "test_name": field(fields, 3),
            "sample_type": field(fields, 5),
            "start_datetime": field(fields, 6),
            "finish_datetime": field(fields, 8),
            "declared_count": field(fields, 11),
            "unit": field(fields, 21),
            "instrument_flags": field(fields, 28),
            "controls": controls,
        }
        logger.info(
            f"OBR(QC): test={parsed['test_code']}, {len(controls)} kontrol, "
            f"lot={'/'.join(c['lot_number'] for c in controls)}"
        )
        return parsed
```

- [ ] **Step 4: Implementasi module**

Di `protocols/mindray_bs240pro/module.py`, ganti blok import menjadi:

```python
import copy

from lib.models import SpecimenInfo, TestResult
from lib.utils import generate_message_id, get_logger
# Induk diimpor lewat alias modul, BUKAN `from ... import MindrayBS200EModule`:
# load_module() memilih class pertama hasil dir() yang mewarisi
# BaseProtocolModule, dan "MindrayBS200EModule" < "MindrayBS240ProModule".
from protocols.mindray_bs200e import module as _bs200e
from protocols.mindray_bs240pro.builder import BS240ProBuilder
from protocols.mindray_bs240pro.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION, SEG_OBR,
    QC_LEVEL_FROM_NAME, INSTRUMENT_LEVEL_TO_NUMBER, QC_CONTROLS_KEY,
)
from protocols.mindray_bs240pro.parser import BS240ProParser
```

Tambahkan fungsi modul sebelum class:

```python
def resolve_qc_level(control_name: str, instrument_level: str) -> tuple:
    """
    Tentukan level kontrol QC.

    Nama kontrol ("CTRL L1", "Level 2", "LV3") didahulukan karena di lapangan
    OBR-17 tidak bisa dipercaya (selalu "M"). Tanpa pola di nama, OBR-17
    dipakai apa adanya (L/M/H → dipetakan ResultPusher).

    Returns:
        (level, konflik) — konflik True bila nama dan OBR-17 sama-sama ada
        tapi menunjuk level berbeda.
    """
    raw = (instrument_level or "").strip().upper()
    match = QC_LEVEL_FROM_NAME.search(control_name or "")
    if not match:
        return raw, False
    from_name = match.group(1)
    from_instrument = INSTRUMENT_LEVEL_TO_NUMBER.get(raw, raw)
    return from_name, bool(raw) and from_instrument != from_name
```

Ganti method `parse` dan tambahkan method baru di class:

```python
    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Seperti induk; field `protocol` diganti nama driver ini, dan untuk
        pesan QC daftar seluruh kontrol dipindah dari blok `qc` ke kunci
        `_qc_controls` (dikonsumsi split_results()).
        """
        parsed = super().parse(raw_bytes, instrument)
        parsed["protocol"] = PROTOCOL_NAME
        qc = parsed.get("qc")
        if isinstance(qc, dict) and QC_CONTROLS_KEY in qc:
            parsed[QC_CONTROLS_KEY] = qc.pop(QC_CONTROLS_KEY)
        return parsed

    def _assemble_qc(self, segments: list, result) -> dict | None:
        """
        Rakit ResultObject QC dari OBR (tanpa PID/OBX).

        ResultObject dan blok `qc` diisi kontrol pertama; seluruh kontrol
        dibawa di `qc["_qc_controls"]` agar parse() memindahkannya keluar.
        """
        obr_fields = self._parser.find_segment(segments, SEG_OBR)
        if obr_fields is None:
            result.parse_errors.append("Pesan QC tanpa segment OBR")
            return None

        qc = self._parser.parse_obr_qc(obr_fields)
        controls = qc["controls"]
        declared = qc["declared_count"]
        if declared.isdigit() and int(declared) != len(controls):
            result.parse_errors.append(
                f"OBR-11 menyebut {declared} kontrol, data berisi {len(controls)}"
            )

        # OBR-8 = selesai per tes (waktu ukur sebenarnya); OBR-6 = mulai run.
        run_at = qc["finish_datetime"] or qc["start_datetime"]
        common = [
            f"{label}: {value}"
            for label, value in (
                ("sample_type", qc["sample_type"]),
                ("instrument_flags", qc["instrument_flags"]),
            )
            if value
        ]
        entries = [self._qc_entry(qc, control, run_at, common) for control in controls]

        first = entries[0]
        result.specimen = SpecimenInfo(sample_type="qc", collected_at=run_at)
        result.results.append(TestResult(**first["result"]))
        result.comments.extend(first["comments"])

        block = dict(first["qc"])
        block[QC_CONTROLS_KEY] = entries
        return block

    def _qc_entry(self, qc: dict, control: dict, run_at: str, common: list) -> dict:
        """Satu kontrol → {result, comments, qc} untuk satu baris tbl_result."""
        code = qc["test_code"]
        level, conflict = resolve_qc_level(control["control_name"], control["level"])
        if conflict:
            self._logger.warning(
                f'QC {code} kontrol "{control["control_name"]}": level dari nama = '
                f'{level}, dari alat = {control["level"]} — pakai {level}; '
                f"periksa setting level kontrol di alat"
            )

        comments = list(common)
        for label, value in (
            ("control_no", control["control_no"]),
            ("control_name", control["control_name"]),
            ("lot_number", control["lot_number"]),
            ("expiration_date", control["expiration_date"]),
            ("mean_value", control["mean_value"]),
            ("standard_deviation", control["standard_deviation"]),
        ):
            if value:
                comments.append(f"{label}: {value}")

        return {
            "result": TestResult(
                test_code=code,
                test_name=qc["test_name"],
                value=control["value"],
                unit=qc["unit"],
                flag="",            # flag Westgard alat hanya di komentar
                status="qc",
            ).to_dict(),
            "comments": comments,
            "qc": {
                "lot_number": _bs200e._strip_lot_prefix(control["lot_number"]),
                "level": level,
                "control_name": control["control_name"],
                "expiry_date": _bs200e._hl7_date(control["expiration_date"]),
                "run_at": run_at,
                "targets": {
                    code: {
                        "mean": control["mean_value"],
                        "sd": control["standard_deviation"],
                    },
                },
            },
        }

    def split_results(self, result_dict: dict) -> list:
        """
        Hook TCPSocketService: pecah hasil parse() menjadi baris tbl_result.

        Pesan QC → satu baris per kontrol (blok `qc`, hasil, dan komentar
        masing-masing; message_id baru mulai kontrol kedua). Pesan lain →
        [result_dict] apa adanya. Kunci `_qc_controls` tidak pernah ikut
        tersimpan.
        """
        controls = result_dict.get(QC_CONTROLS_KEY)
        if not controls:
            return [result_dict]

        base = {k: v for k, v in result_dict.items() if k != QC_CONTROLS_KEY}
        rows = []
        for index, entry in enumerate(controls):
            row = copy.deepcopy(base)
            if index > 0:
                row["message_id"] = generate_message_id()
            row["results"] = [dict(entry["result"])]
            row["comments"] = list(entry["comments"])
            row["qc"] = copy.deepcopy(entry["qc"])
            rows.append(row)
        return rows
```

- [ ] **Step 5: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py tests/test_mindray_bs200e.py -v`
Expected: semua PASS.

- [ ] **Step 6: Commit**

```bash
git add protocols/mindray_bs240pro tests/test_mindray_bs240pro.py
git commit -m "feat(bs240pro): QC multi-kontrol dengan level dari nama kontrol

OBR-2 dipakai langsung sebagai kode tes, field 12-20 dipecah per kontrol,
level diambil dari nama kontrol (CTRL L1) sebelum OBR-17 yang di lapangan
selalu M. split_results() memecah pesan menjadi satu baris per kontrol.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Query dan broadcast

**Files:**
- Modify: `protocols/mindray_bs240pro/builder.py`, `protocols/mindray_bs240pro/module.py`
- Test: `tests/test_mindray_bs240pro.py`

**Interfaces:**
- Consumes: Task 1 (`BS240ProBuilder._with_model`), konstanta `MSH15_LIS_DOWNLOAD`, `MSH15_INDEX`, `SAMPLE_TYPE_LABELS`, `DSP_SAMPLE_TYPE`, `FIELD_SEPARATOR`
- Produces: `MindrayBS240ProModule.format_order(order, instrument) -> bytes` (DSR^Q03, MSH-15 = `P`); context builder menerima kunci `accept_ack_type`

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan ke `tests/test_mindray_bs240pro.py`:

```python
# QRY^Q02 dari log (barcode 241000223710; MSH-3/4 dan QRF-1 kosong)
QRY = (
    b"\x0b"
    b"MSH|^~\\&|||||20241007065739||QRY^Q02|1167|P|2.3.1||||||ASCII|||\r"
    b"QRD|20241007065739|R|D|313|||RD|241000223710|OTH|||T|\r"
    b"QRF||||||RCT|COR|ALL||\r"
    b"\x1c\x0d"
)

ORDER = {
    "order_id": "ORD-240",
    "request_datetime": "2024-10-07T07:08:13",
    "patient": {"patient_id": "RM-0001", "name": "PASIEN QUERY", "dob": "19840515", "gender": "M"},
    "specimen": {"sample_id": "241000223710", "sample_type": "serum", "priority": "R"},
    "tests": [{"test_code": "CREA", "test_name": "Creatinine"}, {"test_code": "UREA", "test_name": "Urea"}],
}


def _msh_fields(payload: bytes, event: bytes) -> list:
    """Field segmen MSH dari pesan `event` di dalam payload (bisa >1 pesan)."""
    for message in payload.split(b"\x0b"):
        if event in message:
            msh = message.split(b"\r")[0].decode()
            return msh.split("|")
    raise AssertionError(f"{event!r} tidak ada di payload")


def test_query_ok_qck_lalu_dsr(module):
    info = module.handle_enq(QRY, INSTRUMENT)
    assert info["type"] == "query"
    assert info["sample_id"] == "241000223710"

    resp = module.format_query_response_full(ORDER, INSTRUMENT, info["_msh"])
    assert resp.index(b"QCK^Q02") < resp.index(b"DSR^Q03")
    assert b"QAK|SR|OK|" in resp
    assert b"DSP|3||PASIEN QUERY|||" in resp
    assert b"DSP|21||241000223710|||" in resp
    assert b"DSP|26||Serum|||" in resp            # kapital, seperti di log
    assert b"DSP|29||CREA^Creatinine^^|||" in resp
    assert b"DSP|30||UREA^Urea^^|||" in resp
    assert b"QRD|20241007065739|R|D|313|||RD|241000223710|OTH|||T|" in resp   # QRD dipantulkan
    dsr_msh = _msh_fields(resp, b"DSR^Q03")
    assert dsr_msh[5] == "BS-240Pro"              # MSH-6
    assert dsr_msh[14] == ""                      # MSH-15 kosong untuk balasan query


def test_query_not_found(module):
    context = module.handle_enq(QRY, INSTRUMENT)["_msh"]
    resp = module.format_query_not_found_full(INSTRUMENT, context)
    assert b"QAK|SR|NF|" in resp
    assert b"DSR^Q03" not in resp


@pytest.mark.parametrize("sample_type, expected", [
    ("plasma", b"DSP|26||Plasma|||"),
    ("URINE", b"DSP|26||Urine|||"),
    ("Whole Blood", b"DSP|26||Whole Blood|||"),   # di luar tabel → apa adanya
    ("", b"DSP|26|||||"),
])
def test_dsp26_jenis_sampel(module, sample_type, expected):
    order = dict(ORDER, specimen=dict(ORDER["specimen"], sample_type=sample_type))
    assert expected in module.format_query_response(order, INSTRUMENT)


def test_broadcast_dsr_msh15_p_tanpa_warning(module, caplog):
    with caplog.at_level(logging.WARNING):
        payload = module.format_order(ORDER, INSTRUMENT)

    assert b"QCK^Q02" not in payload
    msh = _msh_fields(payload, b"DSR^Q03")
    assert msh[14] == "P"                 # MSH-15 = P (manual: LIS kirim info sampel)
    assert msh[5] == "BS-240Pro"
    assert b"|RD|241000223710|OTH|" in payload     # QRD-8 = barcode
    assert b"QRF|BS-240Pro|" in payload
    assert b"DSP|21||241000223710|||" in payload
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


ACK_Q03_AA = (
    b"\x0bMSH|^~\\&|||||20241007070813||ACK^Q03|1|P|2.3.1||||||ASCII|||\r"
    b"MSA|AA|1|Message accepted|||0|\r\x1c\x0d"
)


def test_handle_ack_q03(module):
    assert module.handle_ack(ACK_Q03_AA) == "ACK"
    assert module.handle_ack(ACK_Q03_AA.replace(b"MSA|AA|", b"MSA|AE|")) == "NAK"
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py -v -k "query or dsp26 or broadcast or handle_ack"`
Expected: FAIL — `test_query_ok_qck_lalu_dsr` (`DSP|26||serum|||`, bukan `Serum`), `test_dsp26_jenis_sampel`, `test_broadcast_dsr_msh15_p_tanpa_warning` (MSH-15 kosong & WARNING induk). `test_query_not_found` dan `test_handle_ack_q03` sudah PASS (diwarisi). Bila `test_handle_ack_q03` FAIL, baca `MindrayBS200EModule.handle_ack` dan sesuaikan nilai yang diharapkan dengan kontrak induk (`ACK|NAK|EOT|UNKNOWN`) — jangan ubah driver BS-200E.

- [ ] **Step 3: Implementasi builder**

Di `protocols/mindray_bs240pro/builder.py`, ganti import konstanta menjadi:

```python
from protocols.mindray_bs240pro.constants import (
    DEFAULT_MODEL, FIELD_SEPARATOR, MSH15_INDEX,
    DSP_SAMPLE_TYPE, SAMPLE_TYPE_LABELS,
)
```

Ganti `_build_msh` dan tambahkan `_build_dsp_lines`:

```python
    def _build_msh(self, event: str, control_id: str, context: dict,
                   result_type: str = "") -> str:
        """
        MSH induk + model default BS-240Pro. Bila context membawa
        `accept_ack_type`, nilainya ditulis ke MSH-15 (broadcast: "P").
        """
        ctx = self._with_model(context)
        msh = super()._build_msh(event, control_id, ctx, result_type)
        accept = ctx.get("accept_ack_type")
        if accept:
            fields = msh.split(FIELD_SEPARATOR)
            fields[MSH15_INDEX] = accept
            msh = FIELD_SEPARATOR.join(fields)
        return msh

    def _build_dsp_lines(self, order: dict) -> list:
        """
        DSP induk, dengan DSP-26 (jenis sampel) berhuruf awal kapital —
        format yang terbukti diterima BS-360E di log. Jenis di luar tabel
        dikirim apa adanya (kamus jenis sampel lokal alat).
        """
        segments = super()._build_dsp_lines(order)
        raw = ((order.get("specimen") or {}).get("sample_type") or "").strip()
        label = SAMPLE_TYPE_LABELS.get(raw.lower(), raw)
        segments[DSP_SAMPLE_TYPE - 1] = self._build_dsp(DSP_SAMPLE_TYPE, label)
        return segments
```

- [ ] **Step 4: Implementasi broadcast di module**

Tambahkan `MSH15_LIS_DOWNLOAD` ke import konstanta `module.py`, lalu method di class:

```python
    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        DSR^Q03 untuk broadcast (MidLab mendorong order tanpa QRY dari alat).

        Resmi di manual BS-240Pro: MSH-15 = P menandai info sampel yang
        dikirim LIS atas inisiatif sendiri. Alat membalas ACK^Q03.
        """
        self._logger.info(
            f"Broadcast DSR^Q03 order {order.get('order_id', '?')} / barcode "
            f"{(order.get('specimen') or {}).get('sample_id', '?')}"
        )
        return self._builder.build_dsr_q03(order, {"accept_ack_type": MSH15_LIS_DOWNLOAD})
```

- [ ] **Step 5: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py tests/test_mindray_bs200e.py -v`
Expected: semua PASS.

- [ ] **Step 6: Commit**

```bash
git add protocols/mindray_bs240pro tests/test_mindray_bs240pro.py
git commit -m "feat(bs240pro): broadcast DSR^Q03 MSH-15=P dan DSP-26 kapital

Broadcast resmi di manual BS-240Pro (MSH-15 = P), jenis sampel dikirim
Serum/Plasma/Urine seperti yang diterima BS-360E di log.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Hook `split_results` di receiver

**Files:**
- Modify: `services/tcp_socket/receiver.py` (`ResultReceiver._parse_and_save`)
- Test: `tests/test_mindray_bs240pro.py`

**Interfaces:**
- Consumes: `MindrayBS240ProModule.split_results` (Task 2); `save_result(instrument_id, protocol, raw_hex, result_dict) -> int | None` (sudah ada, mengembalikan `None` bila gagal)
- Produces: receiver menyimpan N baris bila protocol punya `split_results`; perilaku lama untuk protocol tanpa hook

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan ke `tests/test_mindray_bs240pro.py`:

```python
from protocols.mindray_bs200e.module import MindrayBS200EModule
from services.tcp_socket.receiver import ResultReceiver


class _FakeConfig:
    id = 9
    name = "Mindray BS-240Pro"
    protocol = PROTOCOL

    def to_dict(self):
        return dict(INSTRUMENT)


class _FakeWriter:
    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data

    async def drain(self):
        pass


async def test_receiver_simpan_satu_baris_per_kontrol(monkeypatch, module):
    saved = []

    def _fake_save(instrument_id, protocol, raw_hex, result_dict):
        saved.append((raw_hex, result_dict))
        return len(saved)

    monkeypatch.setattr("services.tcp_socket.receiver.save_result", _fake_save)
    receiver = ResultReceiver(_FakeConfig(), module)
    writer = _FakeWriter()

    assert await receiver.handle_data(ORU_QC_MULTI, writer) is False

    assert [r["qc"]["lot_number"] for _, r in saved] == ["1111", "2222"]
    assert saved[0][0] == saved[1][0] == ORU_QC_MULTI.hex()   # raw sama di tiap baris
    assert all("_qc_controls" not in r for _, r in saved)
    assert writer.written.count(b"ACK^R01") == 1               # satu pesan, satu ACK


async def test_receiver_satu_baris_gagal_tidak_menghentikan_lainnya(monkeypatch, module):
    calls = []

    def _fake_save(instrument_id, protocol, raw_hex, result_dict):
        calls.append(result_dict["qc"]["lot_number"])
        return None if len(calls) == 1 else 77

    monkeypatch.setattr("services.tcp_socket.receiver.save_result", _fake_save)
    receiver = ResultReceiver(_FakeConfig(), module)

    await receiver.handle_data(ORU_QC_MULTI, _FakeWriter())

    assert calls == ["1111", "2222"]


async def test_receiver_tanpa_hook_tetap_satu_baris(monkeypatch):
    # Driver tanpa split_results (BS-200E) tidak berubah perilakunya.
    bs200e = MindrayBS200EModule()
    assert not hasattr(bs200e, "split_results")
    calls = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a: calls.append(a) or 1,
    )

    class _Cfg200E(_FakeConfig):
        protocol = "HL7_MINDRAY_BS200E"

    receiver = ResultReceiver(_Cfg200E(), bs200e)
    await receiver.handle_data(ORU_SAMPLE, _FakeWriter())

    assert len(calls) == 1
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py -v -k receiver`
Expected: FAIL `test_receiver_simpan_satu_baris_per_kontrol` (hanya 1 baris tersimpan, berisi `_qc_controls`) dan `test_receiver_satu_baris_gagal_tidak_menghentikan_lainnya` (`calls == ["1111"]`). `test_receiver_tanpa_hook_tetap_satu_baris` PASS.

- [ ] **Step 3: Implementasi hook**

Di `services/tcp_socket/receiver.py`, dalam `_parse_and_save`, ganti blok mulai komentar `# Simpan ke database` sampai akhir blok `if result_id: ... else: ...` (sebelum `except Exception as e:`) dengan:

```python
            # Protocol module boleh menyediakan split_results() untuk memecah
            # satu pesan menjadi beberapa baris tbl_result — dipakai
            # HL7_MINDRAY_BS240PRO_360E_240E yang mengirim beberapa kontrol QC
            # dalam satu pesan, sedangkan /qc-results menerima satu lot+level
            # per baris. Default: satu pesan = satu baris.
            split = getattr(self._protocol, "split_results", None)
            rows = split(result_dict) if split is not None else [result_dict]

            raw_hex = raw_bytes.hex()
            loop = asyncio.get_event_loop()
            for row in rows:
                # Gagal simpan satu baris tidak boleh menghentikan baris lain.
                try:
                    result_id = await loop.run_in_executor(
                        None,
                        save_result,
                        self._config.id,
                        self._config.protocol,
                        raw_hex,
                        row,
                    )
                except Exception as e:
                    result_id = None
                    self._logger.error(f"[{self._inst_name}] Error simpan result: {e}")

                if result_id:
                    self._total_results += 1
                    self._logger.info(
                        f"[{self._inst_name}] Result saved: id={result_id}, "
                        f"total={self._total_results}"
                    )
                else:
                    self._logger.error(
                        f"[{self._inst_name}] Gagal simpan result ke database"
                    )
```

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py tests/test_mindray_bs200e.py tests/test_mindray_bc5150.py tests/test_glory_127.py tests/test_sd_biosensor_f2400.py -v`
Expected: semua PASS (driver lain tidak berubah).

- [ ] **Step 5: Commit**

```bash
git add services/tcp_socket/receiver.py tests/test_mindray_bs240pro.py
git commit -m "feat(tcp): hook split_results untuk pesan berisi beberapa hasil

Protocol module boleh memecah satu pesan menjadi beberapa baris
tbl_result; dipakai BS-240Pro untuk QC multi-kontrol. Gagal simpan satu
baris tidak menghentikan baris lain. Driver tanpa hook tidak berubah.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `qc_backfill` memakai hook dan payload `/qc-results`

**Files:**
- Modify: `scripts/qc_backfill.py` (`_collect`, docstring modul)
- Test: `tests/test_mindray_bs240pro.py`

**Interfaces:**
- Consumes: `split_results` (Task 2); `services.lis_bridge.result_pusher.build_qc_payload(result_row, instrument) -> dict`; `scripts/qc_backfill.collect_payloads(instrument, module, date_prefix) -> list[dict]`, `raw_messages(instrument_id)`
- Produces: backfill menghasilkan satu payload per kontrol

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan ke `tests/test_mindray_bs240pro.py`:

```python
import importlib.util
import pathlib
import sys
from datetime import datetime
from unittest.mock import MagicMock

from lib.db import TblResult
from services.lis_bridge.result_pusher import build_qc_payload


def _inst240():
    m = MagicMock()
    m.id = 9
    m.name = "Mindray BS-240Pro"
    m.lis_instrument_id = "INST-BS240PRO"
    return m


def _row(result_json):
    row = TblResult()
    row.id = 1
    row.result_json = result_json
    row.received_at = datetime(2024, 10, 7, 3, 25, 7)
    return row


def test_payload_qc_results_dari_baris_bs240pro(module):
    row_json = module.split_results(module.parse(ORU_QC, INSTRUMENT))[0]
    payload = build_qc_payload(_row(row_json), _inst240())

    assert payload["protocol"] == "HL7"
    assert payload["qc"]["lot_number"] == "059324003"
    assert payload["qc"]["level"] == "1"
    assert payload["qc"]["control_name"] == "CTRL L1"
    assert payload["qc"]["expiry_date"] == "2026-12-31"
    assert payload["run_at"].startswith("2024-10-07T03:12:12")
    assert payload["message_id"] == "MID9-059324003-L1-20241007T031212"
    assert payload["results"] == [{
        "test_code": "DBIL",
        "test_name": "D-Bil-V",
        "value": "0.525642",
        "unit": "mg/dL",
        "flag": "",
        "instrument_target_mean": 1.05,
        "instrument_target_sd": 0.16,
    }]


def _load_qc_backfill(monkeypatch):
    # Skrip menyisipkan /opt/midlab ke sys.path saat di-import; kembalikan
    # sys.path setelah test agar import berikutnya tidak lari ke produksi.
    monkeypatch.setattr(sys, "path", list(sys.path))
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "qc_backfill.py"
    spec = importlib.util.spec_from_file_location("qc_backfill_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_qc_backfill_satu_payload_per_kontrol(monkeypatch, module):
    backfill = _load_qc_backfill(monkeypatch)
    monkeypatch.setattr(backfill, "raw_messages", lambda _id: iter([ORU_QC_MULTI, ORU_SAMPLE]))

    payloads = backfill.collect_payloads(_inst240(), module, "2016")

    assert sorted(p["qc"]["lot_number"] for p in payloads) == ["1111", "2222"]
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py -v -k "payload or backfill"`
Expected: `test_payload_qc_results_dari_baris_bs240pro` PASS (kontrak pusher sudah cocok); `test_qc_backfill_satu_payload_per_kontrol` FAIL — hanya `["1111"]`. Bila test payload FAIL pada `run_at`/`message_id`, periksa `_to_iso8601` dan `timeutil` — sesuaikan nilai harapan dengan offset lab yang dihasilkan, jangan ubah ResultPusher.

- [ ] **Step 3: Implementasi**

Di `scripts/qc_backfill.py`, ganti fungsi `_collect` dengan:

```python
def _collect(instrument, module, date_prefix: str, found: dict):
    # Driver yang mengirim beberapa kontrol per pesan (BS-240Pro) memecahnya
    # lewat split_results() — jalur yang sama dengan TCPSocketService.
    split = getattr(module, "split_results", None)
    for raw in raw_messages(instrument.id):
        parsed = module.parse(raw, {"id": instrument.id, "name": instrument.name})
        for result_json in (split(parsed) if split is not None else [parsed]):
            qc = result_json.get("qc")
            if not isinstance(qc, dict) or not str(qc.get("run_at", "")).startswith(date_prefix):
                continue
            row = TblResult()
            row.id = 0
            row.result_json = result_json
            row.received_at = datetime.now()
            try:
                payload = build_qc_payload(row, instrument)
            except QcPayloadError as e:
                print(f"  lewati: {e}")
                continue
            key = (payload["message_id"], payload["results"][0]["test_code"])
            found.setdefault(key, payload)
```

Dan di docstring modul, ganti paragraf "Batasan" menjadi:

```
Batasan: hanya driver HL7/MLLP yang menghasilkan blok `qc` (saat ini
HL7_MINDRAY_BS200E dan HL7_MINDRAY_BS240PRO_360E_240E) dan hanya selama comm
log masih tersimpan (rotasi).
```

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_mindray_bs240pro.py tests/test_qc_eazylab.py -v`
Expected: semua PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/qc_backfill.py tests/test_mindray_bs240pro.py
git commit -m "feat(scripts): qc_backfill memecah QC multi-kontrol lewat split_results

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Simulator alat dan verifikasi akhir

**Files:**
- Create: `scripts/mindray_bs240pro_test_sender.py`

**Interfaces:**
- Consumes: TCPSocketService yang berjalan dengan alat ber-protocol `HL7_MINDRAY_BS240PRO_360E_240E`
- Produces: skrip CLI `--mode connect|listen`, `--scenario all|result|qc|query`

- [ ] **Step 1: Tulis simulator**

`scripts/mindray_bs240pro_test_sender.py`:

```python
#!/usr/bin/env python3
"""
scripts/mindray_bs240pro_test_sender.py — Simulator alat Mindray BS-240Pro / BS-360E / BS-240E.

Bicara HL7 v2.3.1 di atas MLLP persis seperti BS-360E di log lapangan
(MSH-3/4 kosong, QRF-1 kosong). Dua mode koneksi, sesuai
tbl_instrument.connection di MidLab:

  --mode connect  MidLab = server (connection='server'): simulator konek ke MidLab
  --mode listen   MidLab = client (connection='client'): simulator membuka port
                  dan menunggu MidLab konek

Skenario:
  1. RESULT — ORU^R01 hasil pasien, harus dibalas ACK^R01
  2. QC     — ORU^R01 MSH-16=2 dengan DUA kontrol (L1/L2) dalam satu pesan,
              harus dibalas ACK^R01; MidLab harus menyimpan 2 baris tbl_result
  3. QUERY  — QRY^Q02 berisi barcode; order ada → QCK^Q02 OK + DSR^Q03
              (simulator membalas ACK^Q03), order tidak ada → QCK^Q02 NF

Run:
  python3 scripts/mindray_bs240pro_test_sender.py --port 2575
  python3 scripts/mindray_bs240pro_test_sender.py --mode listen --port 5100
  python3 scripts/mindray_bs240pro_test_sender.py --scenario query --barcode 241000223710
"""
import argparse
import socket
import sys
from datetime import datetime


VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2575
DEFAULT_BARCODE = "241000223710"


def wrap(segments: list) -> bytes:
    """Bungkus segment (tiap-tiap diakhiri CR) dalam envelope MLLP."""
    body = "".join(f"{s}\r" for s in segments)
    return VT + body.encode("ascii") + FS + CR


def unwrap(raw: bytes) -> str:
    return raw.replace(VT, b"").replace(FS, b"").decode("ascii", errors="replace")


def now() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def msh(event: str, control_id: str, result_type: str = "") -> str:
    """MSH arah alat → LIS; MSH-3/4 kosong seperti BS-360E di lapangan."""
    return (
        f"MSH|^~\\&|||||{now()}||{event}|{control_id}|P|2.3.1"
        f"||||{result_type}||ASCII|||"
    )


def build_oru_sample(barcode: str, control_id: str) -> bytes:
    return wrap([
        msh("ORU^R01", control_id, result_type="0"),
        "PID|1||||PASIEN SIMULATOR|||O|||||||||||||||||||||||",
        f"OBR|1|{barcode}|1|^|N|{now()}|{now()}|{now()}||1^1||||{now()}|Serum"
        f"|||||||||||||||||||||||||||||||||",
        f"OBX|1|NM|GLU|Glucose (GOD-POD Method)|105.805265|mg/dL|-|N|||F||105.805265|{now()}|||0||",
        f"OBX|2|NM|UREA|Urea|23.484954|mg/dL|-|N|||F||23.484954|{now()}|||0||",
    ])


def build_oru_qc(control_id: str) -> bytes:
    """QC GLU dua kontrol dalam satu OBR (field 12-15, 17-20 = V1^V2)."""
    return wrap([
        msh("ORU^R01", control_id, result_type="2"),
        f"OBR|1|GLU|Glu-G|^|Serum|{now()}|{now()}|{now()}|||2|8^9|CTRL L1^CTRL L2"
        f"|059324003^059424001|20261231^20261231||M^M|100.000000^250.000000"
        f"|10.000000^20.000000|93.389457^241.500000|mg/dL|||||||RGTE"
        f"||||||||||||||||||||",
    ])


def build_qry(barcode: str, control_id: str) -> bytes:
    return wrap([
        msh("QRY^Q02", control_id),
        f"QRD|{now()}|R|D|{control_id}|||RD|{barcode}|OTH|||T|",
        "QRF||||||RCT|COR|ALL||",
    ])


def build_ack_q03(control_id: str) -> bytes:
    return wrap([
        msh("ACK^Q03", control_id),
        f"MSA|AA|{control_id}|Message accepted|||0|",
    ])


def show(label: str, raw: bytes):
    print(f"\n--- {label} ({len(raw)} bytes) ---")
    for line in unwrap(raw).split("\r"):
        if line.strip():
            print(f"  {line}")


def receive(sock: socket.socket, label: str, timeout: float = 10) -> bytes:
    """Baca sampai minimal satu pesan MLLP utuh (FS CR) tiba."""
    sock.settimeout(timeout)
    data = b""
    try:
        while FS + CR not in data:
            chunk = sock.recv(65535)
            if not chunk:
                print(f"\n!!! Koneksi ditutup saat menunggu {label}")
                return data
            data += chunk
    except socket.timeout:
        print(f"\n!!! TIMEOUT menunggu {label} ({timeout}s)")
        return data
    show(label, data)
    return data


def scenario_result(sock: socket.socket, barcode: str) -> bool:
    msg = build_oru_sample(barcode, "1")
    show("TX ORU^R01 (hasil pasien)", msg)
    sock.sendall(msg)
    return b"MSA|AA|1|" in receive(sock, "RX ACK^R01")


def scenario_qc(sock: socket.socket) -> bool:
    msg = build_oru_qc("2")
    show("TX ORU^R01 (QC dua kontrol)", msg)
    sock.sendall(msg)
    ok = b"MSA|AA|2|" in receive(sock, "RX ACK^R01")
    print("  → cek Result Monitor: harus ada 2 baris QC (lot 059324003 L1, 059424001 L2)")
    return ok


def scenario_query(sock: socket.socket, barcode: str) -> bool:
    msg = build_qry(barcode, "3")
    show(f"TX QRY^Q02 (barcode {barcode})", msg)
    sock.sendall(msg)
    data = receive(sock, "RX QCK^Q02 (+DSR^Q03)")
    if b"QAK|SR|NF|" in data:
        print("  → order tidak ada di MidLab (QAK NF) — buat order pending dulu untuk uji DSR")
        return True
    if b"DSR^Q03" not in data:
        data += receive(sock, "RX DSR^Q03")
    if b"DSR^Q03" not in data:
        return False
    sock.sendall(build_ack_q03("3"))
    print("  → ACK^Q03 dikirim; order harus berstatus sent di Order Monitor")
    return True


def open_socket(args) -> socket.socket:
    if args.mode == "connect":
        print(f"==> Connect ke MidLab {args.host}:{args.port} sebagai BS-240Pro")
        return socket.create_connection((args.host, args.port), timeout=10)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(1)
    print(f"==> Listen di {args.host}:{args.port}, menunggu MidLab konek (60s)...")
    server.settimeout(60)
    try:
        conn, addr = server.accept()
    finally:
        server.close()
    print(f"==> MidLab konek dari {addr}")
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulator alat Mindray BS-240Pro")
    ap.add_argument("--mode", choices=["connect", "listen"], default="connect")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="connect: alamat MidLab; listen: alamat bind (0.0.0.0 untuk semua)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--barcode", default=DEFAULT_BARCODE)
    ap.add_argument("--scenario", choices=["all", "result", "qc", "query"], default="all")
    args = ap.parse_args()

    try:
        sock = open_socket(args)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Pastikan service tcp_<id> jalan dan port/mode sesuai tbl_instrument.",
              file=sys.stderr)
        return 1

    hasil = []
    try:
        if args.scenario in ("all", "result"):
            hasil.append(("result", scenario_result(sock, args.barcode)))
        if args.scenario in ("all", "qc"):
            hasil.append(("qc", scenario_qc(sock)))
        if args.scenario in ("all", "query"):
            hasil.append(("query", scenario_query(sock, args.barcode)))
    finally:
        sock.close()

    print("\n" + "=" * 46)
    for nama, ok in hasil:
        print(f"  {nama:8s} : {'OK' if ok else 'GAGAL'}")
    print("=" * 46)
    return 0 if all(ok for _, ok in hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Cek skrip bisa dijalankan**

Run: `.venv/bin/python scripts/mindray_bs240pro_test_sender.py --help`
Expected: teks bantuan argparse, exit 0.

Run: `.venv/bin/python -m py_compile scripts/mindray_bs240pro_test_sender.py`
Expected: tanpa output.

- [ ] **Step 3: Jalankan seluruh test suite**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -8`
Expected: hanya 4 kegagalan pre-existing `tests/test_tap_transport.py::TestSerialTransport`; jumlah passed = 674 + jumlah test baru; tidak ada kegagalan lain.

- [ ] **Step 4: Commit**

```bash
git add scripts/mindray_bs240pro_test_sender.py
git commit -m "feat(scripts): simulator alat Mindray BS-240Pro (mode connect & listen)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Catatan untuk eksekutor

- **Jangan menyentuh DB dev.** Memori proyek: config dev jatuh ke `midlab_db` yang berisi data alat sungguhan. Seluruh test di rencana ini memakai monkeypatch `save_result` dan tidak perlu DB. Uji simulator terhadap service yang berjalan hanya dengan persetujuan user.
- **Jangan deploy.** Deploy ke `/opt/midlab` lewat `scripts/deploy.sh` dilakukan user sendiri (butuh sudo).
- Bila test induk BS-200E gagal setelah perubahan, perbaikannya di subclass BS-240Pro — driver BS-200E tidak diubah.
