"""Test kolom sidik jari pada tbl_result di atas SQLite in-memory."""
from unittest.mock import MagicMock, patch

import lib.db as db_module
from lib.db import TblResult
from lib.fingerprint import result_fingerprint


def _payload(sample_id="S1", value="5.2"):
    return {
        "specimen": {"sample_id": sample_id},
        "results": [{"test_code": "WBC", "value": value,
                     "unit": "10^3/uL", "flag": "N"}],
    }


def test_kolom_fingerprint_ada_di_model(db_session):
    """Kolom harus lahir lewat create_all() supaya instalasi baru ikut punya."""
    row = TblResult(
        instrument_id=1,
        protocol="HL7_MINDRAY_BC5150",
        raw_data="raw",
        result_json=_payload(),
        send_status="pending",
        result_fingerprint=result_fingerprint(_payload()),
    )
    db_session.add(row)
    db_session.commit()

    stored = db_session.query(TblResult).first()
    assert stored.result_fingerprint == result_fingerprint(_payload())
    assert len(stored.result_fingerprint) == 64


def test_fingerprint_boleh_null(db_session):
    """Baris lama sebelum backfill tetap sah."""
    row = TblResult(
        instrument_id=1,
        protocol="ASTM",
        raw_data="raw",
        result_json=_payload(),
        send_status="pending",
    )
    db_session.add(row)
    db_session.commit()
    assert db_session.query(TblResult).first().result_fingerprint is None


def test_save_result_mengisi_fingerprint():
    captured = {}

    def _add(obj):
        captured["row"] = obj

    with patch.object(db_module, "DBManager") as MockDB:
        session = MagicMock()
        session.add.side_effect = _add
        MockDB.return_value.get_session.return_value = session
        db_module.save_result(1, "ASTM", "raw", _payload())

    assert captured["row"].result_fingerprint == result_fingerprint(_payload())


def test_save_result_tetap_jalan_saat_hashing_gagal():
    """Fitur laporan tidak boleh menjatuhkan penyimpanan hasil alat."""
    captured = {}

    def _add(obj):
        captured["row"] = obj

    with patch.object(db_module, "DBManager") as MockDB, \
         patch.object(db_module, "result_fingerprint",
                      side_effect=RuntimeError("boom")):
        session = MagicMock()
        session.add.side_effect = _add
        MockDB.return_value.get_session.return_value = session
        db_module.save_result(1, "ASTM", "raw", _payload())

    assert captured["row"].result_fingerprint is None
    assert captured["row"].raw_data == "raw"
