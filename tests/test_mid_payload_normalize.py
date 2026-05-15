"""
build_mid_payload harus menormalkan output parser (mis. Cobas c111) ke
kontrak EazyApp Instrument API:
- datetime ASTM (YYYYMMDD[HHMMSS]) → ISO8601 +07:00
- protocol internal → wire protocol EazyApp
- result status code ASTM → label
- buang pseudo-result kalibrasi/absorbansi
- drop field `comments` (tidak ada di kontrak)
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from lib.db import TblResult
from services.lis_bridge.result_pusher import build_mid_payload


def _row(**kw):
    defaults = {
        "id": 9,
        "instrument_id": 1,
        "result_json": {"mid_version": "1.0"},
        "received_at": datetime(2026, 5, 13, 8, 0, tzinfo=timezone.utc),
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
    m.lis_instrument_id = "INST-COBFPW68"
    return m


def test_astm_datetime_full_to_iso_with_lab_offset():
    row = _row(result_json={"message_datetime": "20051021152259"})
    p = build_mid_payload(row, _inst())
    assert p["message_datetime"] == "2005-10-21T15:22:59+07:00"


def test_astm_date_only_to_iso():
    row = _row(result_json={"message_datetime": "20051021"})
    p = build_mid_payload(row, _inst())
    assert p["message_datetime"] == "2005-10-21T00:00:00+07:00"


def test_already_iso_passthrough():
    row = _row(result_json={"message_datetime": "2026-05-13T09:15:00+07:00"})
    p = build_mid_payload(row, _inst())
    assert p["message_datetime"] == "2026-05-13T09:15:00+07:00"


def test_empty_datetime_falls_back_to_received_at():
    row = _row(result_json={"message_datetime": ""})
    p = build_mid_payload(row, _inst())
    assert p["message_datetime"].startswith("2026-05-13T08:00")


def test_protocol_cobas_mapped_to_astm():
    row = _row(result_json={"protocol": "COBAS_C111"})
    p = build_mid_payload(row, _inst())
    assert p["protocol"] == "ASTM"


def test_protocol_astm_passthrough():
    row = _row(result_json={"protocol": "ASTM"})
    p = build_mid_payload(row, _inst())
    assert p["protocol"] == "ASTM"


def test_specimen_collected_at_normalized():
    row = _row(result_json={"specimen": {"sample_id": "S1",
                                         "collected_at": "20260513081000"}})
    p = build_mid_payload(row, _inst())
    assert p["specimen"]["collected_at"] == "2026-05-13T08:10:00+07:00"


def test_result_status_code_mapped():
    row = _row(result_json={"results": [
        {"test_code": "GLU", "value": "95", "status": "F"},
        {"test_code": "UA", "value": "6.2", "status": "P"},
    ]})
    p = build_mid_payload(row, _inst())
    assert p["results"][0]["status"] == "final"
    assert p["results"][1]["status"] == "preliminary"


def test_non_clinical_results_filtered():
    row = _row(result_json={"results": [
        {"test_code": "GLU", "value": "95", "status": "F"},
        {"test_code": "211", "value": "Rea1.1", "status": "calibration"},
        {"test_code": "RR-2", "value": "0.123", "status": "absorbance_raw"},
    ]})
    p = build_mid_payload(row, _inst())
    codes = [r["test_code"] for r in p["results"]]
    assert codes == ["GLU"]


def test_comments_dropped():
    row = _row(result_json={"comments": ["result[687]: Sol1 F Dev"],
                            "results": []})
    p = build_mid_payload(row, _inst())
    assert "comments" not in p


def test_instrument_id_rewritten_and_message_id_defaulted():
    row = _row(result_json={"instrument_id": 1})
    p = build_mid_payload(row, _inst())
    assert p["instrument_id"] == "INST-COBFPW68"
    assert p["message_id"] == "MSG-1-9"
    assert p["mid_version"] == "1.0"
