"""
Field `protocol` di kontrak EazyApp adalah KELUARGA protokol ("ASTM"/"HL7"),
bukan nama driver internal — lihat docs/API.md:261. EazyApp juga membatasi
panjangnya maksimal 20 karakter.

Regresi asli: driver HL7_SD_BIOSENSOR_F2400 (22 karakter) diteruskan apa
adanya → HTTP 422 "protocol must not be greater than 20 characters" → 44
result mandek permanen. HL7_MINDRAY_BC5150 (18 karakter) lolos hanya karena
namanya kebetulan pendek, padahal sama-sama menyimpang dari kontrak.

Normalisasi per keluarga (bukan entry-per-driver) supaya driver baru
berikutnya tidak kena masalah yang sama.
"""
from datetime import datetime
from unittest.mock import MagicMock

from lib.db import TblResult
from services.lis_bridge.result_pusher import build_mid_payload

MAX_PROTOCOL_LEN = 20


def _row(**kw):
    defaults = {
        "id": 9,
        "instrument_id": 1,
        "result_json": {"mid_version": "1.0"},
        "received_at": datetime(2026, 5, 13, 8, 0),
        "retry_count": 0,
    }
    defaults.update(kw)
    r = TblResult()
    for k, v in defaults.items():
        setattr(r, k, v)
    return r


def _inst():
    m = MagicMock()
    m.id = 1
    m.lis_instrument_id = "INST-9EGRUXOP"
    return m


def test_driver_hl7_panjang_dinormalkan_ke_hl7():
    """Kasus yang bikin 44 result F2400 gagal 422."""
    row = _row(result_json={"protocol": "HL7_SD_BIOSENSOR_F2400"})
    p = build_mid_payload(row, _inst())
    assert p["protocol"] == "HL7"


def test_driver_hl7_pendek_juga_dinormalkan():
    """BC-5150 lolos limit tapi tetap menyimpang dari kontrak."""
    row = _row(result_json={"protocol": "HL7_MINDRAY_BC5150"})
    p = build_mid_payload(row, _inst())
    assert p["protocol"] == "HL7"


def test_driver_astm_dinormalkan_ke_astm():
    row = _row(result_json={"protocol": "ASTM_ARUMA_AR580"})
    p = build_mid_payload(row, _inst())
    assert p["protocol"] == "ASTM"


def test_hl7_dan_astm_polos_passthrough():
    for proto in ("HL7", "ASTM"):
        row = _row(result_json={"protocol": proto})
        assert build_mid_payload(row, _inst())["protocol"] == proto


def test_cobas_c111_tetap_ke_astm():
    """Mapping eksplisit lama tidak boleh rusak."""
    row = _row(result_json={"protocol": "COBAS_C111"})
    p = build_mid_payload(row, _inst())
    assert p["protocol"] == "ASTM"


def test_protocol_kosong_default_astm():
    row = _row(result_json={})
    p = build_mid_payload(row, _inst())
    assert p["protocol"] == "ASTM"


def test_protocol_tak_dikenal_tidak_pernah_lewat_batas():
    """Jaring pengaman: apa pun namanya, jangan sampai kena 422 lagi."""
    row = _row(result_json={"protocol": "PROTOKOL_VENDOR_SANGAT_PANJANG_SEKALI"})
    p = build_mid_payload(row, _inst())
    assert len(p["protocol"]) <= MAX_PROTOCOL_LEN
