"""
Test driver HL7_MINDRAY_BS240PRO_360E_240E (BS-240Pro / BS-360E / BS-240E).

Fixture diambil dari log komunikasi BS-360E lapangan (7 Okt 2024) dan
DIANONIMKAN: nama pasien, NIK, dan nomor RM diganti nilai fiktif. Kode tes,
nilai QC, lot, dan timestamp dibiarkan asli. Contoh QC multi-kontrol diambil
dari manual "BS-360E & BS-240Pro & BS-240E LIS Interface Manual V1.0" bab 1.3.
"""

import logging

import pytest

from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from protocols.mindray_bs240pro.module import MindrayBS240ProModule, resolve_qc_level


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
