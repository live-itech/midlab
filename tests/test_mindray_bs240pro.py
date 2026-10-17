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
