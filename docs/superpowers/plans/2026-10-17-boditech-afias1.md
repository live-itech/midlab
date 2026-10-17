# Driver HL7_BODITECH_AFIAS-1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Driver protocol best-effort untuk Boditech AFIAS-1 (HL7 2.6 `OUL^R24`, unidirectional), jalan dengan connection server maupun client, plus WARNING di receiver bila data HL7 datang tanpa MLLP.

**Architecture:** Paket mandiri `protocols/boditech_afias1/` (constants, parser, builder, module) mengikuti pola driver SD Biosensor F2400, didaftarkan di `protocols/base.py`. `services/tcp_socket/receiver.py::_extract_mllp_message` menulis WARNING untuk byte yang dibuang. Simulator di `scripts/`.

**Tech Stack:** Python 3.10+, pytest (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-10-17-boditech-afias1-design.md`

## Global Constraints

- Nama protokol: `HL7_BODITECH_AFIAS-1` (20 karakter; tanpa migrasi DB). Paket: `protocols/boditech_afias1/`; class: `BoditechAfias1Module`.
- Fixture utama = contoh slide 5 dokumen, dibungkus MLLP (`\x0b` … `\x1c\x0d`), segment dipisah `\r`.
- Unidirectional saja; tidak ada blok `qc`.
- Jangan ubah driver lain (`protocols/*` selain paket baru dan satu baris registry).
- Test memakai instrument id **12** (id 7 dan 9 sudah dipakai test lain untuk cache logger `midlab.tcp_socket.<id>`); test yang membuat `ResultReceiver` membersihkan handler logger `midlab.tcp_socket.12` lewat fixture.
- Jalankan test dengan `.venv/bin/python -m pytest`. Baseline sebelum mulai: 709 passed, 4 failed (`tests/test_tap_transport.py::TestSerialTransport`, pre-existing), 3 skipped.
- Jangan sentuh DB, jangan deploy, jangan push.
- Komentar & docstring Bahasa Indonesia. Commit diakhiri trailer `Co-Authored-By:` model yang mengerjakan.
- Branch kerja: `feat/boditech-afias1` (sudah ada).

## File Structure

| File | Aksi | Tanggung jawab |
|---|---|---|
| `protocols/boditech_afias1/__init__.py` | Create | penanda paket |
| `protocols/boditech_afias1/constants.py` | Create | identitas, segment, event, posisi field |
| `protocols/boditech_afias1/parser.py` | Create | `field`, `component`, `to_iso8601`, `AfiasParser` |
| `protocols/boditech_afias1/builder.py` | Create | `AfiasBuilder.build_ack_r24` |
| `protocols/boditech_afias1/module.py` | Create | `BoditechAfias1Module` |
| `protocols/base.py` | Modify (`_PROTOCOL_REGISTRY`) | daftarkan nama |
| `services/tcp_socket/receiver.py` | Modify (`_extract_mllp_message`) | WARNING byte non-MLLP |
| `scripts/boditech_afias1_test_sender.py` | Create | simulator alat (`--mode connect|listen`) |
| `tests/test_boditech_afias1.py` | Create | seluruh test |

---

### Task 1: Parser, module `parse()`, dan registry

**Files:**
- Create: `protocols/boditech_afias1/__init__.py`, `constants.py`, `parser.py`, `module.py`
- Modify: `protocols/base.py`
- Test: `tests/test_boditech_afias1.py`

**Interfaces:**
- Produces: `BoditechAfias1Module.parse(raw_bytes, instrument) -> dict` (ResultObject dict); `AfiasParser` dengan `unwrap_mllp`, `split_message`, `find_segment`, `find_all`, `parse_msh`, `parse_pid`, `parse_obr`, `parse_orc`, `parse_spm`, `parse_obx`, `get_message_type`; fungsi `field`, `component`, `to_iso8601`; konstanta di `constants.py`.

- [ ] **Step 1: Tulis test yang gagal**

`tests/test_boditech_afias1.py`:

```python
"""
Test driver HL7_BODITECH_AFIAS-1 (Boditech AFIAS-1, HL7 2.6 OUL^R24).

Driver best-effort: satu-satunya sumber adalah contoh pesan di slide 5
"AFIAS-1 LIS user manual" (Boditech, Rev.00 2025.06). Fixture utama adalah
contoh itu, dibungkus MLLP.
"""

import pytest

from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from protocols.boditech_afias1.module import BoditechAfias1Module


PROTOCOL = "HL7_BODITECH_AFIAS-1"

SEGMENTS = [
    "MSH|^~\\&||1|AFIAS-6|A60123456789|20200305171553.205+0900||OUL^R24^OUL_R24|1|T|2.6",
    "PID||T45|T45|||||",
    "OBR||T4|G||||||admin",
    "ORC|OK||||||||||||||||A60123456789|5",
    "SPM||T4PKA37F||||||||||||||||20210719",
    "OBX|1|TX|T4||33.26|nmol/L|||||R",
]


def mllp(segments) -> bytes:
    return b"\x0b" + "".join(s + "\r" for s in segments).encode() + b"\x1c\x0d"


OUL = mllp(SEGMENTS)

INSTRUMENT = {"id": 12, "name": "Boditech AFIAS-1", "protocol": PROTOCOL}


@pytest.fixture
def module():
    return BoditechAfias1Module(now=lambda: "20260101120000")


def _with(index, segment):
    """Contoh slide 5 dengan satu segment diganti (None = dihapus)."""
    segs = list(SEGMENTS)
    if segment is None:
        segs.pop(index)
    else:
        segs[index] = segment
    return mllp(segs)


# ============================================================
# Registry
# ============================================================

def test_terdaftar_dan_load_module_mengembalikan_class_afias():
    assert _PROTOCOL_REGISTRY[PROTOCOL] == "protocols.boditech_afias1.module"
    mod = load_module(PROTOCOL)
    assert type(mod) is BoditechAfias1Module
    assert mod.PROTOCOL_NAME == PROTOCOL


def test_dianggap_keluarga_mllp():
    assert is_mllp_protocol(PROTOCOL) is True


# ============================================================
# parse() — contoh slide 5
# ============================================================

def test_parse_contoh_slide5(module):
    parsed = module.parse(OUL, INSTRUMENT)

    assert parsed["protocol"] == PROTOCOL
    assert parsed["instrument_id"] == 12
    assert parsed["message_datetime"] == "2020-03-05T17:15:53.205+09:00"
    assert parsed["patient"]["patient_id"] == "T45"
    assert parsed["specimen"]["sample_id"] == "T45"
    assert parsed["specimen"]["collected_at"] == "2020-03-05T17:15:53.205+09:00"
    assert parsed["order"]["panel"] == "T4"
    assert parsed["results"] == [{
        "test_code": "T4",
        "test_name": "T4",
        "value": "33.26",
        "unit": "nmol/L",
        "reference_range": "",
        "flag": "",
        "status": "R",
    }]
    for comment in (
        "model: AFIAS-6",
        "serial: A60123456789",
        "processing_id: T",
        "obr3: G",
        "operator: admin",
        "orc18: 5",
        "cartridge_lot: T4PKA37F",
        "cartridge_expiry: 2021-07-19",
    ):
        assert comment in parsed["comments"]
    assert parsed["parse_errors"] == []
    assert "qc" not in parsed


def test_id_sampel_dari_pid2_bila_pid3_kosong(module):
    parsed = module.parse(_with(1, "PID||RM-77||||||"), INSTRUMENT)
    assert parsed["specimen"]["sample_id"] == "RM-77"
    assert parsed["parse_errors"] == []


def test_pid3_didahulukan_dari_pid2(module):
    parsed = module.parse(_with(1, "PID||RM-77|BC-001^^^LAB||||"), INSTRUMENT)
    assert parsed["specimen"]["sample_id"] == "BC-001"
    assert parsed["patient"]["patient_id"] == "BC-001"


def test_id_sampel_kosong_dicatat(module):
    parsed = module.parse(_with(1, "PID||||||||"), INSTRUMENT)
    assert any("ID sampel kosong" in e for e in parsed["parse_errors"])
    assert parsed["results"][0]["value"] == "33.26"


def test_dua_obx_dua_hasil(module):
    segs = SEGMENTS + ["OBX|2|NM|TSH^Thyroid Stimulating Hormone||1.2|uIU/mL|0.4-4.0|N|||F"]
    parsed = module.parse(mllp(segs), INSTRUMENT)
    assert [r["test_code"] for r in parsed["results"]] == ["T4", "TSH"]
    assert parsed["results"][1]["test_name"] == "Thyroid Stimulating Hormone"
    assert parsed["results"][1]["reference_range"] == "0.4-4.0"
    assert parsed["results"][1]["flag"] == "N"


def test_tanpa_obx_dicatat(module):
    parsed = module.parse(_with(5, None), INSTRUMENT)
    assert parsed["results"] == []
    assert any("Tidak ada OBX" in e for e in parsed["parse_errors"])


def test_message_type_lain_dicatat(module):
    raw = OUL.replace(b"OUL^R24^OUL_R24", b"ORU^R01^ORU_R01")
    parsed = module.parse(raw, INSTRUMENT)
    assert any("bukan hasil" in e for e in parsed["parse_errors"])


@pytest.mark.parametrize("raw", [b"", b"sampah", b"\x0bPID||T45\r\x1c\x0d", b"\x0bMSH|rusak\x1c\x0d"])
def test_input_rusak_tidak_melempar(module, raw):
    parsed = module.parse(raw, INSTRUMENT)
    assert parsed["parse_errors"]


def test_pesan_tanpa_mllp_tetap_bisa_diparse(module):
    # Parser sendiri toleran; framing ditentukan receiver.
    raw = "".join(s + "\r" for s in SEGMENTS).encode()
    assert module.parse(raw, INSTRUMENT)["specimen"]["sample_id"] == "T45"
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_boditech_afias1.py -v`
Expected: ERROR collect — `ModuleNotFoundError: No module named 'protocols.boditech_afias1'`

- [ ] **Step 3: Buat paket**

`protocols/boditech_afias1/__init__.py`:

```python
"""Driver HL7 Boditech AFIAS-1."""
```

`protocols/boditech_afias1/constants.py`:

```python
"""
protocols/boditech_afias1/constants.py — Konstanta HL7 Boditech AFIAS-1

Sumber: "AFIAS-1 LIS user manual" (Boditech Med, Rev.00 2025.06), slide 5 —
satu-satunya contoh pesan mode HL7:

    MSH|^~\\&||1|AFIAS-6|A60123456789|20200305171553.205+0900||OUL^R24^OUL_R24|1|T|2.6
    PID||T45|T45|||||
    OBR||T4|G||||||admin
    ORC|OK||||||||||||||||A60123456789|5
    SPM||T4PKA37F||||||||||||||||20210719
    OBX|1|TX|T4||33.26|nmol/L|||||R

Posisi field di bawah dihitung dari contoh itu. Dokumen tidak menjelaskan
artinya — driver ini best-effort dan wajib diverifikasi dengan data asli.
"""

from protocols.hl7.constants import (  # noqa: F401 — re-export
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, COMPONENT_SEP, ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_PID, SEG_OBR, SEG_OBX, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    PROC_PRODUCTION,
)


# ============================================================
# Identitas module
# ============================================================

PROTOCOL_NAME = "HL7_BODITECH_AFIAS-1"
PROTOCOL_VERSION = "1.0.0"

HL7_VERSION = "2.6"
MESSAGE_ENCODING = "utf-8"   # tidak disebut dokumen; decode dengan errors="replace"

SEG_ORC = "ORC"
SEG_SPM = "SPM"


# ============================================================
# Message type (MSH-9)
# ============================================================

EVENT_RESULT_PREFIX = "OUL^R24"   # alat → LIS: hasil
EVENT_ACK_R24 = "ACK^R24^ACK"     # LIS → alat: balasan
ACK_SENDING_APP = "MidLab"        # MSH-3 ACK


# ============================================================
# Posisi field (nomor field HL7)
# ============================================================

MSH_FIELD_MODEL = 5           # "AFIAS-6"
MSH_FIELD_SERIAL = 6          # "A60123456789"
MSH_FIELD_DATETIME = 7        # "20200305171553.205+0900"
MSH_FIELD_MESSAGE_TYPE = 9
MSH_FIELD_CONTROL_ID = 10
MSH_FIELD_PROCESSING_ID = 11  # "T" pada contoh
MSH_FIELD_VERSION = 12

PID_FIELD_PATIENT_ID = 2      # "T45"
PID_FIELD_IDENTIFIER = 3      # "T45" — didahulukan sebagai ID sampel

OBR_FIELD_PLACER_ORDER = 2    # "T4"
OBR_FIELD_FILLER_ORDER = 3    # "G" — arti tidak diketahui
OBR_FIELD_OPERATOR = 9        # "admin"

ORC_FIELD_18 = 18             # "5" — arti tidak diketahui

SPM_FIELD_CARTRIDGE_LOT = 2   # "T4PKA37F"
SPM_FIELD_CARTRIDGE_EXPIRY = 18   # "20210719"

OBX_FIELD_VALUE_TYPE = 2
OBX_FIELD_IDENTIFIER = 3
OBX_FIELD_VALUE = 5
OBX_FIELD_UNITS = 6
OBX_FIELD_REFERENCE_RANGE = 7
OBX_FIELD_ABNORMAL_FLAGS = 8
OBX_FIELD_STATUS = 11         # "R" pada contoh
```

`protocols/boditech_afias1/parser.py`:

```python
"""
protocols/boditech_afias1/parser.py — Parser HL7 Boditech AFIAS-1

Mengurai OUL^R24 sesuai posisi field contoh slide 5 manual LIS AFIAS-1.
Toleran terhadap pesan dengan atau tanpa envelope MLLP; framing ditentukan
receiver.
"""

import re

from lib.utils import get_logger
from protocols.hl7.parser import HL7Parser
from protocols.boditech_afias1.constants import (
    FIELD_SEPARATOR, COMPONENT_SEP, SEGMENT_TERMINATOR, MESSAGE_ENCODING,
    SEG_MSH,
    MSH_FIELD_MODEL, MSH_FIELD_SERIAL, MSH_FIELD_DATETIME,
    MSH_FIELD_MESSAGE_TYPE, MSH_FIELD_CONTROL_ID, MSH_FIELD_PROCESSING_ID,
    MSH_FIELD_VERSION,
    PID_FIELD_PATIENT_ID, PID_FIELD_IDENTIFIER,
    OBR_FIELD_PLACER_ORDER, OBR_FIELD_FILLER_ORDER, OBR_FIELD_OPERATOR,
    ORC_FIELD_18,
    SPM_FIELD_CARTRIDGE_LOT, SPM_FIELD_CARTRIDGE_EXPIRY,
    OBX_FIELD_VALUE_TYPE, OBX_FIELD_IDENTIFIER, OBX_FIELD_VALUE,
    OBX_FIELD_UNITS, OBX_FIELD_REFERENCE_RANGE, OBX_FIELD_ABNORMAL_FLAGS,
    OBX_FIELD_STATUS,
)


logger = get_logger("boditech_afias1_parser")

# `20200305171553.205+0900` / `20200305171553` / `202003051715` / `20200305`
_TS_RE = re.compile(
    r"^(\d{4})(\d{2})(\d{2})"           # tanggal
    r"(?:(\d{2})(\d{2})(?:(\d{2}))?)?"  # jam opsional
    r"(\.\d+)?"                         # pecahan detik opsional
    r"([+-]\d{4})?$"                    # offset zona opsional
)


def field(fields: list, index: int) -> str:
    """Field ke-`index` (nomor field HL7); string kosong bila tidak ada."""
    if 0 <= index < len(fields):
        return fields[index].strip()
    return ""


def component(value: str, index: int = 0) -> str:
    """Komponen ke-`index` sebuah field (dipisah `^`)."""
    if not value:
        return ""
    parts = value.split(COMPONENT_SEP)
    if 0 <= index < len(parts):
        return parts[index].strip()
    return ""


def to_iso8601(value: str) -> str:
    """
    Timestamp HL7 → ISO8601, pecahan detik dan offset zona dipertahankan.

        "20200305171553.205+0900" → "2020-03-05T17:15:53.205+09:00"
        "20210719"                → "2021-07-19"
        "" / tidak dikenal        → dikembalikan apa adanya
    """
    text = (value or "").strip()
    if not text:
        return ""

    m = _TS_RE.match(text)
    if not m:
        logger.warning(f"Format timestamp tidak dikenal: {text!r}")
        return text

    year, month, day, hour, minute, second, frac, offset = m.groups()

    iso = f"{year}-{month}-{day}"
    if hour is not None:
        iso += f"T{hour}:{minute}:{second or '00'}"
        if frac:
            iso += frac
    if offset:
        iso += f"{offset[:3]}:{offset[3:]}"
    return iso


class AfiasParser:
    """Parser HL7 AFIAS-1 (arah alat → LIS)."""

    def __init__(self):
        # HL7Parser dipakai untuk envelope MLLP (identik antar vendor).
        self._hl7 = HL7Parser()

    def unwrap_mllp(self, raw_bytes: bytes) -> bytes:
        """Hapus envelope MLLP `<VT>...<FS><CR>` bila ada."""
        return self._hl7.unwrap_mllp(raw_bytes)

    def split_message(self, hl7_bytes: bytes) -> list:
        """Pesan → list segment; tiap segment list field (index = nomor field)."""
        text = hl7_bytes.decode(MESSAGE_ENCODING, errors="replace")
        segments = []
        for line in text.split(SEGMENT_TERMINATOR):
            line = line.strip("\n").strip()
            if len(line) < 3:
                continue
            segments.append(self.split_segment(line))
        return segments

    def split_segment(self, segment_text: str) -> list:
        if segment_text[:3] == SEG_MSH:
            separator = segment_text[3] if len(segment_text) > 3 else FIELD_SEPARATOR
            return [SEG_MSH, separator] + segment_text[4:].split(separator)
        return segment_text.split(FIELD_SEPARATOR)

    def find_segment(self, segments: list, seg_type: str) -> list | None:
        for fields in segments:
            if fields and fields[0] == seg_type:
                return fields
        return None

    def find_all(self, segments: list, seg_type: str) -> list:
        return [fields for fields in segments if fields and fields[0] == seg_type]

    def parse_msh(self, fields: list) -> dict:
        parsed = {
            "model": field(fields, MSH_FIELD_MODEL),
            "serial": field(fields, MSH_FIELD_SERIAL),
            "datetime": field(fields, MSH_FIELD_DATETIME),
            "message_type": field(fields, MSH_FIELD_MESSAGE_TYPE),
            "control_id": field(fields, MSH_FIELD_CONTROL_ID),
            "processing_id": field(fields, MSH_FIELD_PROCESSING_ID),
            "version_id": field(fields, MSH_FIELD_VERSION),
        }
        logger.info(
            f"MSH: type={parsed['message_type']}, control_id={parsed['control_id']}, "
            f"model={parsed['model'] or '-'}, serial={parsed['serial'] or '-'}"
        )
        return parsed

    def parse_pid(self, fields: list) -> dict:
        """ID sampel: PID-3 (komponen pertama) didahulukan, cadangan PID-2."""
        identifier = component(field(fields, PID_FIELD_IDENTIFIER), 0)
        patient_id = field(fields, PID_FIELD_PATIENT_ID)
        return {"sample_id": identifier or patient_id}

    def parse_obr(self, fields: list) -> dict:
        return {
            "placer_order": field(fields, OBR_FIELD_PLACER_ORDER),
            "filler_order": field(fields, OBR_FIELD_FILLER_ORDER),
            "operator": field(fields, OBR_FIELD_OPERATOR),
        }

    def parse_orc(self, fields: list) -> dict:
        return {"orc18": field(fields, ORC_FIELD_18)}

    def parse_spm(self, fields: list) -> dict:
        return {
            "cartridge_lot": field(fields, SPM_FIELD_CARTRIDGE_LOT),
            "cartridge_expiry": field(fields, SPM_FIELD_CARTRIDGE_EXPIRY),
        }

    def parse_obx(self, fields: list) -> dict:
        identifier = field(fields, OBX_FIELD_IDENTIFIER)
        code = component(identifier, 0)
        parsed = {
            "value_type": field(fields, OBX_FIELD_VALUE_TYPE),
            "test_code": code,
            "test_name": component(identifier, 1) or code,
            "value": field(fields, OBX_FIELD_VALUE),
            "unit": component(field(fields, OBX_FIELD_UNITS), 0),
            "reference_range": field(fields, OBX_FIELD_REFERENCE_RANGE),
            "flag": field(fields, OBX_FIELD_ABNORMAL_FLAGS),
            "status": field(fields, OBX_FIELD_STATUS),
        }
        logger.info(f"OBX: {code}={parsed['value']} {parsed['unit']}")
        return parsed

    def get_message_type(self, raw_bytes: bytes) -> str:
        """MSH-9 dari raw bytes (dengan atau tanpa MLLP); "" bila tak terbaca."""
        try:
            msh = self.find_segment(self.split_message(self.unwrap_mllp(raw_bytes)), SEG_MSH)
            if msh:
                return field(msh, MSH_FIELD_MESSAGE_TYPE)
        except Exception as e:
            logger.warning(f"Gagal baca message type: {e}")
        return ""
```

`protocols/boditech_afias1/module.py`:

```python
"""
protocols/boditech_afias1/module.py — Protocol Module Boditech AFIAS-1

Driver **best-effort** untuk immunoassay analyzer POCT Boditech AFIAS-1, mode
LIS "HL7" (HL7 v2.6, OUL^R24), disusun dari satu-satunya contoh pesan di
slide 5 "AFIAS-1 LIS user manual" (Boditech, Rev.00 2025.06).

## Alur (unidirectional)

    alat  → OUL^R24^OUL_R24   MSH + PID + OBR + ORC + SPM + OBX
    MidLab→ ACK^R24^ACK       MSA|AA|<MSH-10>

Mode Network: alat diisi Server IP + Port, jadi alat yang konek —
tbl_instrument.connection harus 'server'.

## Belum diverifikasi (wajib dicek saat pemasangan pertama)

1. Framing MLLP atau polos — data tanpa <VT> memicu WARNING di receiver
2. Apakah alat menunggu ACK
3. Letak barcode sampel (dipakai PID-3, cadangan PID-2)
4. Format QC — semua pesan diperlakukan sebagai hasil pasien
5. Arti OBR-3, ORC-18, OBX-11 — disimpan sebagai komentar

Di-load dynamic via protocols.base.load_module("HL7_BODITECH_AFIAS-1").
"""

from lib.utils import get_logger
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.boditech_afias1.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION,
    SEG_MSH, SEG_PID, SEG_OBR, SEG_OBX, SEG_ORC, SEG_SPM,
    EVENT_RESULT_PREFIX,
)
from protocols.boditech_afias1.parser import AfiasParser, to_iso8601


class BoditechAfias1Module(BaseProtocolModule):
    """Protocol module Boditech AFIAS-1 — HL7 v2.6 OUL^R24 (best-effort)."""

    # Alat tidak pernah meminta order.
    ACK_EXPECTED_ON_NOT_FOUND = False

    def __init__(self, now=None):
        """
        Args:
            now: callable pengganti jam sistem (`YYYYMMDDHHMMSS`) untuk MSH-7
                 ACK — dipakai unit test.
        """
        self._parser = AfiasParser()
        self._now = now
        self._logger = get_logger("boditech_afias1")

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    # ============================================================
    # parse() — OUL^R24 → ResultObject
    # ============================================================

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        instrument_id = instrument.get("id", 0)
        result = ResultObject(instrument_id=instrument_id, protocol=PROTOCOL_NAME)

        if not raw_bytes:
            result.parse_errors.append("Data kosong")
            return result.to_dict()

        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_bytes))
        except Exception as e:
            result.parse_errors.append(f"Gagal decode pesan: {e}")
            self._logger.error(f"Gagal decode pesan: {e}")
            return result.to_dict()

        msh_fields = self._parser.find_segment(segments, SEG_MSH)
        if msh_fields is None:
            result.parse_errors.append("Segment MSH tidak ditemukan")
            self._logger.warning("Pesan tanpa MSH — diabaikan")
            return result.to_dict()

        try:
            self._assemble(segments, msh_fields, result)
        except Exception as e:
            result.parse_errors.append(f"Parse gagal: {e}")
            self._logger.error(f"Parse gagal: {e}")

        self._logger.info(
            f"Parse selesai: sampel={result.specimen.sample_id or '-'}, "
            f"{len(result.results)} hasil, {len(result.parse_errors)} error"
        )
        return result.to_dict()

    def _assemble(self, segments: list, msh_fields: list, result: ResultObject):
        msh = self._parser.parse_msh(msh_fields)
        if not msh["message_type"].startswith(EVENT_RESULT_PREFIX):
            result.parse_errors.append(f"Message type bukan hasil: {msh['message_type'] or '-'}")

        # MSH-7 satu-satunya timestamp di pesan — dipakai juga sebagai waktu sampel.
        message_time = to_iso8601(msh["datetime"])
        if message_time:
            result.message_datetime = message_time

        comments = [
            ("model", msh["model"]),
            ("serial", msh["serial"]),
            ("processing_id", msh["processing_id"]),
        ]

        pid_fields = self._parser.find_segment(segments, SEG_PID)
        sample_id = self._parser.parse_pid(pid_fields)["sample_id"] if pid_fields else ""
        if not sample_id:
            result.parse_errors.append("ID sampel kosong (PID-2/PID-3)")
        result.patient = PatientInfo(patient_id=sample_id)
        result.specimen = SpecimenInfo(sample_id=sample_id, collected_at=message_time)

        obr_fields = self._parser.find_segment(segments, SEG_OBR)
        if obr_fields:
            obr = self._parser.parse_obr(obr_fields)
            result.order = OrderInfo(panel=obr["placer_order"])
            comments += [("obr3", obr["filler_order"]), ("operator", obr["operator"])]

        orc_fields = self._parser.find_segment(segments, SEG_ORC)
        if orc_fields:
            comments.append(("orc18", self._parser.parse_orc(orc_fields)["orc18"]))

        spm_fields = self._parser.find_segment(segments, SEG_SPM)
        if spm_fields:
            spm = self._parser.parse_spm(spm_fields)
            comments += [
                ("cartridge_lot", spm["cartridge_lot"]),
                ("cartridge_expiry", to_iso8601(spm["cartridge_expiry"])),
            ]

        obx_list = self._parser.find_all(segments, SEG_OBX)
        if not obx_list:
            result.parse_errors.append("Tidak ada OBX (hasil kosong)")
        for obx_fields in obx_list:
            obx = self._parser.parse_obx(obx_fields)
            result.results.append(TestResult(
                test_code=obx["test_code"],
                test_name=obx["test_name"],
                value=obx["value"],
                unit=obx["unit"],
                reference_range=obx["reference_range"],
                flag=obx["flag"],
                status=obx["status"],
            ))

        result.comments.extend(f"{label}: {value}" for label, value in comments if value)

    # ============================================================
    # Bidirectional — tidak didukung (diisi Task 2)
    # ============================================================

    def format_order(self, order: dict, instrument: dict) -> bytes:
        return b""

    def is_enq(self, raw_bytes: bytes) -> bool:
        return False

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        return {"type": "unsupported", "sample_id": "", "patient_id": "",
                "raw_query": (raw_bytes or b"").hex()}

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        return b""

    def format_query_not_found(self, instrument: dict) -> bytes:
        return b""

    def handle_ack(self, raw_bytes: bytes) -> str:
        return "UNKNOWN"
```

Tambahkan ke `_PROTOCOL_REGISTRY` di `protocols/base.py`, tepat setelah baris `"HL7_SD_BIOSENSOR_F2400": ...`:

```python
    "HL7_BODITECH_AFIAS-1": "protocols.boditech_afias1.module",
```

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_boditech_afias1.py -v`
Expected: semua PASS. Bila `test_input_rusak_tidak_melempar[\x0bPID||T45...]` gagal karena `parse_errors` kosong, periksa jalur "Segment MSH tidak ditemukan".

- [ ] **Step 5: Commit**

```bash
git add protocols/boditech_afias1 protocols/base.py tests/test_boditech_afias1.py
git commit -m "feat(protocol): driver HL7_BODITECH_AFIAS-1 — parse OUL^R24

Best-effort dari contoh slide 5 manual LIS AFIAS-1: ID sampel PID-3/PID-2,
hasil per OBX, field yang belum jelas artinya disimpan sebagai komentar.

Co-Authored-By: <model yang mengerjakan> <noreply@anthropic.com>"
```

---

### Task 2: ACK^R24, filter penyimpanan, dan stub bidirectional

**Files:**
- Create: `protocols/boditech_afias1/builder.py`
- Modify: `protocols/boditech_afias1/module.py`
- Test: `tests/test_boditech_afias1.py`

**Interfaces:**
- Consumes: Task 1 (`AfiasParser.parse_msh`, `find_segment`, `split_message`, `unwrap_mllp`, `get_message_type`, `field`; konstanta)
- Produces: `AfiasBuilder(now=None).build_ack_r24(context: dict) -> bytes`; `BoditechAfias1Module.build_ack_response(raw_message, instrument) -> bytes`; `should_store_result(result_dict, raw_bytes) -> bool`; `handle_ack(raw_bytes) -> str` (`ACK|NAK|UNKNOWN`)

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan ke `tests/test_boditech_afias1.py` (import `logging` di blok import atas):

```python
# ============================================================
# ACK^R24
# ============================================================

def test_ack_r24(module):
    ack = module.build_ack_response(OUL, INSTRUMENT)

    assert ack.startswith(b"\x0b") and ack.endswith(b"\x1c\x0d")
    segs = ack[1:-2].decode().split("\r")
    assert segs[0] == (
        "MSH|^~\\&|MidLab||AFIAS-6|A60123456789|20260101120000||ACK^R24^ACK|1|T|2.6"
    )
    assert segs[1] == "MSA|AA|1"


def test_ack_processing_id_default_p(module):
    raw = OUL.replace(b"|OUL^R24^OUL_R24|1|T|2.6", b"|OUL^R24^OUL_R24|1||2.6")
    msh = module.build_ack_response(raw, INSTRUMENT)[1:].decode().split("\r")[0]
    assert msh.split("|")[10] == "P"


def test_ack_pesan_tak_terbaca_kosong(module):
    assert module.build_ack_response(b"\x0bPID||T45\r\x1c\x0d", INSTRUMENT) == b""


# ============================================================
# Filter penyimpanan & bidirectional
# ============================================================

def test_should_store_result_hanya_oul_r24(module):
    assert module.should_store_result({}, OUL) is True
    ack = mllp(["MSH|^~\\&|||||20200305||ACK^R24^ACK|1|P|2.6", "MSA|AA|1"])
    assert module.should_store_result({}, ack) is False
    assert module.should_store_result({}, b"sampah") is False


def test_tidak_ada_query(module):
    assert module.is_enq(OUL) is False
    assert module.handle_enq(OUL, INSTRUMENT)["type"] == "unsupported"


def test_order_dan_query_tidak_didukung(module, caplog):
    with caplog.at_level(logging.WARNING):
        assert module.format_order({"order_id": "X"}, INSTRUMENT) == b""
        assert module.format_query_response({}, INSTRUMENT) == b""
        assert module.format_query_not_found(INSTRUMENT) == b""
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3


@pytest.mark.parametrize("code, expected", [("AA", "ACK"), ("AE", "NAK"), ("AR", "NAK"), ("XX", "UNKNOWN")])
def test_handle_ack(module, code, expected):
    raw = mllp(["MSH|^~\\&|||||20200305||ACK^R24^ACK|1|P|2.6", f"MSA|{code}|1"])
    assert module.handle_ack(raw) == expected


def test_handle_ack_tanpa_msa(module):
    assert module.handle_ack(b"") == "UNKNOWN"
    assert module.handle_ack(OUL) == "UNKNOWN"
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_boditech_afias1.py -v -k "ack or store or query or order"`
Expected: FAIL — `AttributeError: ... has no attribute 'build_ack_response'` / `'should_store_result'`, WARNING count 0, `handle_ack` selalu UNKNOWN. `test_tidak_ada_query` dan `test_handle_ack_tanpa_msa` sudah PASS.

- [ ] **Step 3: Buat builder**

`protocols/boditech_afias1/builder.py`:

```python
"""
protocols/boditech_afias1/builder.py — Builder HL7 Boditech AFIAS-1

Satu-satunya pesan LIS → alat: ACK^R24^ACK atas OUL^R24. Identitas alat
(MSH-5/6) dan processing ID (MSH-11) dipantulkan dari pesan alat.
"""

from datetime import datetime

from lib.utils import get_logger
from protocols.boditech_afias1.constants import (
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, ENCODING_CHARACTERS, SEGMENT_TERMINATOR, MESSAGE_ENCODING,
    HL7_VERSION, PROC_PRODUCTION, ACK_AA,
    EVENT_ACK_R24, ACK_SENDING_APP,
)


logger = get_logger("boditech_afias1_builder")


def _now_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


class AfiasBuilder:
    """Builder pesan HL7 AFIAS-1 (arah LIS → alat)."""

    def __init__(self, now=None):
        """
        Args:
            now: callable pengganti jam sistem (`YYYYMMDDHHMMSS`) — unit test.
        """
        self._now = now or _now_timestamp

    def _wrap_mllp(self, segments: list) -> bytes:
        body = "".join(f"{seg}{SEGMENT_TERMINATOR}" for seg in segments)
        return MLLP_START_BYTE + body.encode(MESSAGE_ENCODING, errors="replace") + MLLP_TRAILER

    def build_ack_r24(self, context: dict) -> bytes:
        """
        ACK^R24^ACK atas OUL^R24.

        Args:
            context: hasil AfiasParser.parse_msh() pesan alat
        """
        context = context or {}
        control_id = context.get("control_id", "")

        msh = FIELD_SEPARATOR.join([
            "MSH",
            ENCODING_CHARACTERS,                          # MSH-2
            ACK_SENDING_APP,                              # MSH-3
            "",                                           # MSH-4
            context.get("model", ""),                     # MSH-5
            context.get("serial", ""),                    # MSH-6
            self._now(),                                  # MSH-7
            "",                                           # MSH-8
            EVENT_ACK_R24,                                # MSH-9
            control_id,                                   # MSH-10
            context.get("processing_id") or PROC_PRODUCTION,  # MSH-11
            HL7_VERSION,                                  # MSH-12
        ])
        msa = FIELD_SEPARATOR.join(["MSA", ACK_AA, control_id])

        logger.info(f"ACK^R24 dibangun untuk control_id={control_id or '-'}")
        return self._wrap_mllp([msh, msa])
```

- [ ] **Step 4: Lengkapi module**

Di `protocols/boditech_afias1/module.py`:

1. Ubah import konstanta menjadi:

```python
from protocols.boditech_afias1.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION,
    SEG_MSH, SEG_PID, SEG_OBR, SEG_OBX, SEG_ORC, SEG_SPM, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    EVENT_RESULT_PREFIX,
)
from protocols.boditech_afias1.parser import AfiasParser, field, to_iso8601
from protocols.boditech_afias1.builder import AfiasBuilder
```

2. Di `__init__`, ganti `self._now = now` dengan `self._builder = AfiasBuilder(now=now)`.

3. Ganti seluruh blok `# Bidirectional — tidak didukung (diisi Task 2)` sampai akhir class dengan:

```python
    # ============================================================
    # Filter & ACK — dipanggil ResultReceiver
    # ============================================================

    def should_store_result(self, result_dict: dict, raw_bytes: bytes) -> bool:
        """Hanya OUL^R24 yang layak masuk tbl_result."""
        message_type = self._parser.get_message_type(raw_bytes)
        if message_type.startswith(EVENT_RESULT_PREFIX):
            return True
        self._logger.info(f"Pesan {message_type or '(tak dikenal)'} tidak disimpan")
        return False

    def build_ack_response(self, raw_message: bytes, instrument: dict) -> bytes:
        """
        ACK^R24^ACK atas pesan alat. Dokumen tidak menyebut apakah alat
        menunggu ACK; bila tidak, balasan ini tidak berbahaya.
        """
        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_message))
            msh_fields = self._parser.find_segment(segments, SEG_MSH)
        except Exception as e:
            self._logger.error(f"Gagal baca pesan untuk ACK: {e}")
            return b""

        if msh_fields is None:
            self._logger.error("Pesan tanpa MSH — ACK tidak dikirim")
            return b""

        context = self._parser.parse_msh(msh_fields)
        if not context.get("control_id"):
            self._logger.warning("MSH-10 kosong — ACK dikirim tanpa control ID")
        return self._builder.build_ack_r24(context)

    # ============================================================
    # Bidirectional — tidak didukung alat ini
    # ============================================================

    def format_order(self, order: dict, instrument: dict) -> bytes:
        self._logger.warning(
            f"AFIAS-1 tidak menerima order dari LIS — order "
            f"{(order or {}).get('order_id', '?')} tidak dikirim"
        )
        return b""

    def is_enq(self, raw_bytes: bytes) -> bool:
        return False

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        return {"type": "unsupported", "sample_id": "", "patient_id": "",
                "raw_query": (raw_bytes or b"").hex()}

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        self._logger.warning("AFIAS-1 tidak mengirim query — tidak ada response")
        return b""

    def format_query_not_found(self, instrument: dict) -> bytes:
        self._logger.warning("AFIAS-1 tidak mengirim query — tidak ada response")
        return b""

    def handle_ack(self, raw_bytes: bytes) -> str:
        """Baca MSA-1: AA → ACK, AE/AR → NAK, selain itu UNKNOWN."""
        if not raw_bytes:
            return "UNKNOWN"
        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_bytes))
        except Exception as e:
            self._logger.warning(f"Gagal decode ACK: {e}")
            return "UNKNOWN"

        msa_fields = self._parser.find_segment(segments, SEG_MSA)
        if msa_fields is None:
            return "UNKNOWN"

        code = field(msa_fields, 1).upper()
        if code == ACK_AA:
            return "ACK"
        if code in (ACK_AE, ACK_AR):
            self._logger.warning(f"Alat membalas {code}")
            return "NAK"
        return "UNKNOWN"
```

- [ ] **Step 5: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_boditech_afias1.py -v`
Expected: semua PASS.

- [ ] **Step 6: Commit**

```bash
git add protocols/boditech_afias1 tests/test_boditech_afias1.py
git commit -m "feat(afias1): ACK^R24, filter OUL^R24, dan stub bidirectional

Co-Authored-By: <model yang mengerjakan> <noreply@anthropic.com>"
```

---

### Task 3: WARNING non-MLLP di receiver + integrasi receiver & ResultPusher

**Files:**
- Modify: `services/tcp_socket/receiver.py` (`ResultReceiver._extract_mllp_message`, ±baris 311-343)
- Test: `tests/test_boditech_afias1.py`

**Interfaces:**
- Consumes: `BoditechAfias1Module` lengkap (Task 1-2); `ResultReceiver(config, protocol)`, `handle_data(data, writer) -> bool`, `_preview(data) -> str`; `save_result` (di-monkeypatch); `services.lis_bridge.result_pusher.build_mid_payload(result_row, instrument) -> dict`
- Produces: `ResultReceiver._warn_dropped(data: bytes)`; perilaku: byte non-MLLP yang dibuang → WARNING

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan ke `tests/test_boditech_afias1.py` (import di blok atas: `from datetime import datetime`, `from unittest.mock import MagicMock`, `from lib.db import TblResult`, `from services.lis_bridge.result_pusher import build_mid_payload`, `from services.tcp_socket.receiver import ResultReceiver`):

```python
# ============================================================
# Integrasi ResultReceiver
# ============================================================

class _FakeConfig:
    id = 12
    name = "Boditech AFIAS-1"
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


@pytest.fixture
def receiver_logger_bersih():
    # ResultReceiver meng-cache handler file di logger midlab.tcp_socket.12;
    # buang setelah test agar tidak bocor ke test lain.
    yield
    lg = logging.getLogger("midlab.tcp_socket.12")
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)


async def test_receiver_pesan_terpotong_disimpan_dan_di_ack_sekali(
    monkeypatch, module, receiver_logger_bersih
):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda inst_id, proto, raw_hex, result: saved.append(result) or len(saved),
    )
    receiver = ResultReceiver(_FakeConfig(), module)
    writer = _FakeWriter()

    potong = len(OUL) // 2
    assert await receiver.handle_data(OUL[:potong], writer) is False
    assert saved == [] and writer.written == b""

    await receiver.handle_data(OUL[potong:], writer)

    assert len(saved) == 1
    assert saved[0]["specimen"]["sample_id"] == "T45"
    assert writer.written.count(b"ACK^R24^ACK") == 1


async def test_receiver_hl7_polos_memicu_warning(
    monkeypatch, module, caplog, receiver_logger_bersih
):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a: saved.append(a) or 1,
    )
    receiver = ResultReceiver(_FakeConfig(), module)
    raw = "".join(s + "\r" for s in SEGMENTS).encode()

    with caplog.at_level(logging.WARNING):
        await receiver.handle_data(raw, _FakeWriter())

    assert saved == []
    pesan = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("tanpa MLLP" in m and f"({len(raw)} byte)" in m and "MSH|" in m for m in pesan)


async def test_receiver_sampah_sebelum_vt_memicu_warning_tapi_pesan_tetap_masuk(
    monkeypatch, module, caplog, receiver_logger_bersih
):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a: saved.append(a) or 1,
    )
    receiver = ResultReceiver(_FakeConfig(), module)

    with caplog.at_level(logging.WARNING):
        await receiver.handle_data(b"NOISE" + OUL, _FakeWriter())

    assert len(saved) == 1
    assert any("tanpa MLLP" in r.getMessage() and "NOISE" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)


async def test_receiver_mllp_normal_tanpa_warning(
    monkeypatch, module, caplog, receiver_logger_bersih
):
    monkeypatch.setattr("services.tcp_socket.receiver.save_result", lambda *a: 1)
    receiver = ResultReceiver(_FakeConfig(), module)

    with caplog.at_level(logging.WARNING):
        # CR/LF sisa di antara pesan bukan data — tidak boleh memicu WARNING.
        await receiver.handle_data(OUL + b"\r\n" + OUL, _FakeWriter())

    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and "tanpa MLLP" in r.getMessage()]


# ============================================================
# Payload ke EazyLab (/results)
# ============================================================

def test_payload_results_dari_hasil_afias(module):
    row = TblResult()
    row.id = 1
    row.result_json = module.parse(OUL, INSTRUMENT)
    row.received_at = datetime(2020, 3, 5, 15, 15, 53)
    inst = MagicMock()
    inst.id = 12
    inst.lis_instrument_id = "INST-AFIAS1"

    payload = build_mid_payload(row, inst)

    assert payload["protocol"] == "HL7"
    assert payload["specimen"]["sample_id"] == "T45"
    assert payload["specimen"]["collected_at"] == "2020-03-05T17:15:53.205+09:00"
    assert payload["message_datetime"] == "2020-03-05T17:15:53.205+09:00"
    assert [(r["test_code"], r["value"], r["status"]) for r in payload["results"]] == [
        ("T4", "33.26", "final")
    ]
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_boditech_afias1.py -v -k "receiver or payload"`
Expected: FAIL `test_receiver_hl7_polos_memicu_warning` dan `test_receiver_sampah_sebelum_vt_memicu_warning_tapi_pesan_tetap_masuk` (tidak ada WARNING). Yang lain PASS. Bila `caplog` tidak menangkap log receiver sama sekali (logger tidak propagate), ganti mekanisme di test dengan `monkeypatch.setattr(receiver._logger, "warning", lambda msg, *a, **k: pesan.append(msg))` dan catat di report.

- [ ] **Step 3: Implementasi**

Di `services/tcp_socket/receiver.py`, ganti seluruh method `_extract_mllp_message` dengan:

```python
    def _extract_mllp_message(self) -> bytes | None:
        """
        Ekstrak satu MLLP message dari buffer.

        Mencari pasangan <VT>...<FS><CR>. Byte di luar envelope dibuang —
        dengan WARNING, karena alat yang mengirim HL7 polos (tanpa MLLP)
        hanya ketahuan dari sini.
        """
        # Cari MLLP_START
        try:
            start = self._buffer.index(MLLP_START)
        except ValueError:
            # Tidak ada VT — buang isi buffer
            self._warn_dropped(bytes(self._buffer))
            self._buffer.clear()
            return None

        if start > 0:
            self._warn_dropped(bytes(self._buffer[:start]))
            del self._buffer[:start]

        # Cari MLLP_END setelah VT
        try:
            end = self._buffer.index(MLLP_END, 1)
        except ValueError:
            # Belum lengkap
            return None

        # Ambil message lengkap termasuk envelope
        # MLLP: <VT>...<FS><CR> — CR mungkin ada atau tidak
        msg_end = end + 1
        if msg_end < len(self._buffer) and self._buffer[msg_end] == 0x0D:
            msg_end += 1

        msg = bytes(self._buffer[:msg_end])
        del self._buffer[:msg_end]
        return msg

    def _warn_dropped(self, data: bytes):
        """WARNING untuk byte non-MLLP yang dibuang; CR/LF/spasi sisa diabaikan."""
        if not data.strip(b"\r\n\t \x00"):
            return
        self._logger.warning(
            f"[{self._inst_name}] Data HL7 tanpa MLLP <VT> dibuang "
            f"({len(data)} byte): {self._preview(data)}"
        )
```

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_boditech_afias1.py tests/test_mindray_bs200e.py tests/test_mindray_bs240pro.py tests/test_mindray_bc5150.py tests/test_sd_biosensor_f2400.py tests/test_aruma_ar580.py -q`
Expected: semua PASS (driver HL7 lain tidak berubah).

- [ ] **Step 5: Commit**

```bash
git add services/tcp_socket/receiver.py tests/test_boditech_afias1.py
git commit -m "feat(tcp): WARNING untuk data HL7 tanpa MLLP yang dibuang receiver

Sebelumnya buffer tanpa <VT> dan sisa sebelum <VT> dibuang diam-diam,
sehingga alat yang mengirim HL7 polos tidak meninggalkan jejak di log.

Co-Authored-By: <model yang mengerjakan> <noreply@anthropic.com>"
```

---

### Task 4: Koneksi server & klien (end-to-end), simulator, dan verifikasi akhir

Syarat tambahan dari user (17 Okt 2026): driver harus jalan dengan `tbl_instrument.connection` = `server` maupun `client`. Arah koneksi diurus TCPSocketService; task ini **membuktikannya** dengan socket localhost sungguhan dan menyesuaikan docstring + simulator.

**Files:**
- Modify: `protocols/boditech_afias1/module.py` (docstring modul saja)
- Create: `scripts/boditech_afias1_test_sender.py`
- Test: `tests/test_boditech_afias1.py`

**Interfaces:**
- Consumes: `BoditechAfias1Module` lengkap; `services.tcp_socket.config.InstrumentConfig(id, name, ip_address, port, protocol, mode, bidir_mode, broadcast_interval, connection, is_active)`; `services.tcp_socket.service.TCPSocketService(config)` dengan `_run_server_mode()`, `_run_client_mode()`, `stop()`, atribut `_running`; `services.tcp_socket.service.enqueue_lis_event` (DB — di-monkeypatch); fixture `receiver_logger_bersih` dan konstanta `OUL`, `PROTOCOL` dari Task 1/3
- Produces: CLI simulator `--mode connect|listen`, `--host`, `--port`, `--sample-id`, `--raw`

- [ ] **Step 1: Tulis test end-to-end**

Tambahkan ke `tests/test_boditech_afias1.py` (import di blok atas: `import asyncio`, `import socket as socketlib`, `from services.tcp_socket.config import InstrumentConfig`, `from services.tcp_socket.service import TCPSocketService`):

```python
# ============================================================
# End-to-end TCPSocketService — connection server & client
#
# Socket localhost sungguhan. start() tidak dipanggil karena memasang
# signal handler SIGTERM/SIGINT pada proses pytest; _run_*_mode() dipanggil
# langsung dengan _running = True, lalu stop().
# ============================================================

def _port_bebas() -> int:
    with socketlib.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _config(connection: str, port: int) -> InstrumentConfig:
    return InstrumentConfig(
        id=12, name="Boditech AFIAS-1", ip_address="127.0.0.1", port=port,
        protocol=PROTOCOL, mode="unidirectional", bidir_mode=None,
        broadcast_interval=30, connection=connection, is_active=True,
    )


async def _baca_satu_pesan(reader) -> bytes:
    data = b""
    while b"\x1c\x0d" not in data:
        chunk = await reader.read(4096)
        if not chunk:
            break
        data += chunk
    return data


@pytest.fixture
def e2e_tersimpan(monkeypatch, receiver_logger_bersih):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda inst_id, proto, raw_hex, result: saved.append(result) or len(saved),
    )
    # Status koneksi ditulis ke tbl_lis_event_queue — jangan sentuh DB.
    monkeypatch.setattr("services.tcp_socket.service.enqueue_lis_event", lambda **k: None)
    return saved


async def test_e2e_connection_server_alat_yang_konek(e2e_tersimpan):
    port = _port_bebas()
    svc = TCPSocketService(_config("server", port))
    svc._running = True
    task = asyncio.create_task(svc._run_server_mode())
    try:
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail("TCPSocketService tidak listen")

        writer.write(OUL)
        await writer.drain()
        ack = await asyncio.wait_for(_baca_satu_pesan(reader), 5)
        writer.close()
    finally:
        await svc.stop()
        await asyncio.wait_for(task, 5)

    assert b"ACK^R24^ACK" in ack and b"MSA|AA|1" in ack
    assert [r["specimen"]["sample_id"] for r in e2e_tersimpan] == ["T45"]


async def test_e2e_connection_client_midlab_yang_konek(e2e_tersimpan):
    # Test berperan sebagai alat / converter RS232-to-LAN yang listen.
    ack_diterima = asyncio.get_running_loop().create_future()

    async def alat(reader, writer):
        writer.write(OUL)
        await writer.drain()
        ack_diterima.set_result(await _baca_satu_pesan(reader))

    server = await asyncio.start_server(alat, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    svc = TCPSocketService(_config("client", port))
    svc._running = True
    task = asyncio.create_task(svc._run_client_mode())
    try:
        ack = await asyncio.wait_for(ack_diterima, 5)
    finally:
        await svc.stop()
        await asyncio.wait_for(task, 10)
        server.close()
        await server.wait_closed()

    assert b"ACK^R24^ACK" in ack and b"MSA|AA|1" in ack
    assert [r["specimen"]["sample_id"] for r in e2e_tersimpan] == ["T45"]


def test_driver_tidak_mengunci_arah_koneksi(module):
    # Deklarasi transport ini mengubah perilaku koneksi TCPSocketService;
    # AFIAS-1 tidak memerlukannya di mode mana pun.
    assert not getattr(module, "PING_BEFORE_RECONNECT", False)
    assert not getattr(module, "IDLE_RX_TIMEOUT", 0)
```

- [ ] **Step 2: Jalankan test**

Run: `.venv/bin/python -m pytest tests/test_boditech_afias1.py -v -k "e2e or arah"`
Expected: PASS (fitur ini sudah didukung TCPSocketService; test mengunci buktinya). Bila sebuah test menggantung atau gagal, **jangan ubah `services/tcp_socket/service.py`** — laporkan BLOCKED dengan traceback dan baris service yang terlibat; kemungkinan besar masalah di test (mis. fixture, urutan stop), bukan di driver.

- [ ] **Step 3: Perbarui docstring module**

Di `protocols/boditech_afias1/module.py`, ganti paragraf:

```
Mode Network: alat diisi Server IP + Port, jadi alat yang konek —
tbl_instrument.connection harus 'server'.
```

dengan:

```
## Koneksi

Driver tidak bergantung arah koneksi — keduanya didukung:

- connection='server' — mode Network alat: alat diisi Server IP + Port dan
  alat yang konek ke MidLab
- connection='client' — mis. lewat converter RS232-to-LAN yang diset sebagai
  TCP server: MidLab yang konek ke IP:port converter
```

- [ ] **Step 4: Tulis simulator**

`scripts/boditech_afias1_test_sender.py`:

```python
#!/usr/bin/env python3
"""
scripts/boditech_afias1_test_sender.py — Simulator alat Boditech AFIAS-1.

Kirim satu OUL^R24 persis contoh slide 5 manual LIS AFIAS-1 (ID sampel bisa
diganti) lalu tampilkan balasan MidLab. Dua mode koneksi, sesuai
tbl_instrument.connection di MidLab:

  --mode connect  MidLab = server (connection='server'): simulator konek ke
                  MidLab, seperti alat di mode LIS Network
  --mode listen   MidLab = client (connection='client'): simulator membuka
                  port dan menunggu MidLab konek, seperti converter
                  RS232-to-LAN yang diset TCP server

  --raw   kirim tanpa envelope MLLP — MidLab harus menulis WARNING
          "Data HL7 tanpa MLLP <VT> dibuang" di tcp_<id>.log dan tidak
          menyimpan hasil

Run:
  python3 scripts/boditech_afias1_test_sender.py --port 5200
  python3 scripts/boditech_afias1_test_sender.py --mode listen --host 0.0.0.0 --port 5200
  python3 scripts/boditech_afias1_test_sender.py --port 5200 --sample-id BC-001
  python3 scripts/boditech_afias1_test_sender.py --port 5200 --raw
"""
import argparse
import socket
import sys
from datetime import datetime


VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5200
DEFAULT_SAMPLE_ID = "T45"


def build_oul(sample_id: str) -> str:
    now = datetime.now().strftime("%Y%m%d%H%M%S") + ".000+0700"
    segments = [
        f"MSH|^~\\&||1|AFIAS-1|A60123456789|{now}||OUL^R24^OUL_R24|1|T|2.6",
        f"PID||{sample_id}|{sample_id}|||||",
        "OBR||T4|G||||||admin",
        "ORC|OK||||||||||||||||A60123456789|5",
        "SPM||T4PKA37F||||||||||||||||20210719",
        "OBX|1|TX|T4||33.26|nmol/L|||||R",
    ]
    return "".join(s + "\r" for s in segments)


def show(label: str, raw: bytes):
    print(f"\n--- {label} ({len(raw)} bytes) ---")
    text = raw.replace(VT, b"").replace(FS, b"").decode("ascii", errors="replace")
    for line in text.split("\r"):
        if line.strip():
            print(f"  {line}")


def open_socket(args) -> socket.socket:
    if args.mode == "connect":
        print(f"==> Connect ke MidLab {args.host}:{args.port} sebagai AFIAS-1")
        return socket.create_connection((args.host, args.port), timeout=10)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(1)
    server.settimeout(60)
    print(f"==> Listen di {args.host}:{args.port}, menunggu MidLab konek (60s)...")
    try:
        conn, addr = server.accept()
    finally:
        server.close()
    print(f"==> MidLab konek dari {addr}")
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulator alat Boditech AFIAS-1")
    ap.add_argument("--mode", choices=["connect", "listen"], default="connect")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="connect: alamat MidLab; listen: alamat bind (0.0.0.0 untuk semua)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--sample-id", default=DEFAULT_SAMPLE_ID)
    ap.add_argument("--raw", action="store_true", help="kirim tanpa envelope MLLP")
    args = ap.parse_args()

    body = build_oul(args.sample_id).encode("ascii")
    msg = body if args.raw else VT + body + FS + CR

    try:
        sock = open_socket(args)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Pastikan service tcp_<id> jalan dan port/connection sesuai tbl_instrument.",
              file=sys.stderr)
        return 1

    try:
        show("TX OUL^R24" + (" (tanpa MLLP)" if args.raw else ""), msg)
        sock.sendall(msg)
        sock.settimeout(10)
        data = b""
        try:
            while FS + CR not in data:
                chunk = sock.recv(65535)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
    finally:
        sock.close()

    if args.raw:
        if data:
            show("RX (tak terduga)", data)
        print("\n==> Mode --raw: periksa WARNING 'tanpa MLLP' di /var/log/midlab/tcp_<id>.log")
        return 0

    if not data:
        print("\n!!! Tidak ada balasan dalam 10 detik")
        return 1
    show("RX", data)
    ok = b"ACK^R24^ACK" in data and b"MSA|AA|1" in data
    print(f"\n==> ACK {'OK' if ok else 'TIDAK SESUAI'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Cek skrip**

Run: `.venv/bin/python scripts/boditech_afias1_test_sender.py --help` → teks bantuan, exit 0.
Run: `.venv/bin/python -m py_compile scripts/boditech_afias1_test_sender.py` → tanpa output.
Jangan jalankan simulator ke port/service mana pun.

- [ ] **Step 6: Full suite**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -8`
Expected: hanya 4 kegagalan pre-existing `tests/test_tap_transport.py::TestSerialTransport`; tidak ada kegagalan lain dan tidak ada test yang menggantung.

- [ ] **Step 7: Commit**

```bash
git add protocols/boditech_afias1/module.py scripts/boditech_afias1_test_sender.py tests/test_boditech_afias1.py
git commit -m "feat(afias1): bukti end-to-end connection server & client + simulator

Test socket localhost sungguhan untuk kedua arah koneksi TCPSocketService,
docstring driver menyebut kedua mode, simulator --mode connect|listen.

Co-Authored-By: <model yang mengerjakan> <noreply@anthropic.com>"
```
