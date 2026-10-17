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
