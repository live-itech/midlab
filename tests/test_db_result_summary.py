"""Test kolom sidik jari dan rekap tbl_result di atas SQLite in-memory."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import lib.db as db_module
from lib.db import TblResult, summarize_results
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


# ============================================================
# Rekap per alat
# ============================================================

def _add_result(session, instrument_id=1, payload=None, fingerprint="__auto__",
                received_at=None, send_status="pending"):
    """Sisipkan satu baris hasil; fingerprint '__auto__' = hitung dari payload."""
    payload = payload if payload is not None else _payload()
    if fingerprint == "__auto__":
        fingerprint = result_fingerprint(payload)
    session.add(TblResult(
        instrument_id=instrument_id,
        protocol="ASTM",
        raw_data="raw",
        result_json=payload,
        send_status=send_status,
        result_fingerprint=fingerprint,
        received_at=received_at or datetime(2026, 8, 10, 10, 0, 0),
    ))
    session.commit()


def test_kiriman_ulang_dihitung_sekali(db_session):
    for _ in range(3):
        _add_result(db_session)

    rows = summarize_results(db_session)
    assert rows == [{"instrument_id": 1, "total": 3, "unique_count": 1}]


def test_sample_sama_hasil_beda_dihitung_dua(db_session):
    _add_result(db_session, payload=_payload(sample_id="S1", value="5.2"))
    _add_result(db_session, payload=_payload(sample_id="S1", value="6.0"))

    rows = summarize_results(db_session)
    assert rows == [{"instrument_id": 1, "total": 2, "unique_count": 2}]


def test_baris_tanpa_fingerprint_tidak_pernah_menyatu(db_session):
    for _ in range(3):
        _add_result(db_session, fingerprint=None)

    rows = summarize_results(db_session)
    assert rows == [{"instrument_id": 1, "total": 3, "unique_count": 3}]


def test_hitungan_antar_alat_tidak_bocor(db_session):
    _add_result(db_session, instrument_id=1)
    _add_result(db_session, instrument_id=1)
    _add_result(db_session, instrument_id=2)

    rows = summarize_results(db_session)
    assert rows == [
        {"instrument_id": 1, "total": 2, "unique_count": 1},
        {"instrument_id": 2, "total": 1, "unique_count": 1},
    ]


def test_filter_rentang_tanggal(db_session):
    _add_result(db_session, received_at=datetime(2026, 8, 9, 23, 0, 0))
    _add_result(db_session, payload=_payload(value="6.0"),
                received_at=datetime(2026, 8, 10, 8, 0, 0))
    _add_result(db_session, payload=_payload(value="7.0"),
                received_at=datetime(2026, 8, 11, 1, 0, 0))

    rows = summarize_results(
        db_session,
        received_from=datetime(2026, 8, 10, 0, 0, 0),
        received_to=datetime(2026, 8, 10, 23, 59, 59, 999999),
    )
    assert rows == [{"instrument_id": 1, "total": 1, "unique_count": 1}]


def test_filter_status_dan_instrument(db_session):
    _add_result(db_session, instrument_id=1, send_status="sent")
    _add_result(db_session, instrument_id=1, payload=_payload(value="6.0"),
                send_status="failed")
    _add_result(db_session, instrument_id=2, send_status="sent")

    assert summarize_results(db_session, status="sent") == [
        {"instrument_id": 1, "total": 1, "unique_count": 1},
        {"instrument_id": 2, "total": 1, "unique_count": 1},
    ]
    assert summarize_results(db_session, instrument_id=1) == [
        {"instrument_id": 1, "total": 2, "unique_count": 2},
    ]


def test_rentang_kosong_mengembalikan_list_kosong(db_session):
    assert summarize_results(db_session) == []
