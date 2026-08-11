"""Kontrak HTTP endpoint rekap pemeriksaan per alat."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from services.web_console.api import app


def _mock_instruments(*pairs):
    """pairs: (id, name) → objek dengan atribut id & name."""
    out = []
    for inst_id, name in pairs:
        obj = MagicMock()
        obj.id = inst_id
        obj.name = name
        out.append(obj)
    return out


def test_summary_menghitung_duplikat_dan_menamai_alat():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = _mock_instruments(
            (1, "BC-5150"), (3, "Glory 127")
        )
        mock_sum.return_value = [
            {"instrument_id": 1, "total": 142, "unique_count": 118},
            {"instrument_id": 3, "total": 51, "unique_count": 51},
        ]
        r = client.get("/api/results/summary")

    assert r.status_code == 200
    assert r.json() == [
        {"instrument_id": 1, "instrument_name": "BC-5150",
         "total": 142, "unique_count": 118, "duplicate_count": 24},
        {"instrument_id": 3, "instrument_name": "Glory 127",
         "total": 51, "unique_count": 51, "duplicate_count": 0},
    ]


def test_summary_urut_nama_alat():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = _mock_instruments(
            (1, "Zeta"), (2, "Alpha")
        )
        mock_sum.return_value = [
            {"instrument_id": 1, "total": 5, "unique_count": 5},
            {"instrument_id": 2, "total": 3, "unique_count": 3},
        ]
        r = client.get("/api/results/summary")

    names = [row["instrument_name"] for row in r.json()]
    assert names == ["Alpha", "Zeta"]


def test_summary_alat_tanpa_nama_pakai_placeholder():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = []
        mock_sum.return_value = [
            {"instrument_id": 9, "total": 2, "unique_count": 1},
        ]
        r = client.get("/api/results/summary")

    assert r.json()[0]["instrument_name"] == "#9"


def test_summary_rentang_kosong():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = []
        mock_sum.return_value = []
        r = client.get("/api/results/summary")

    assert r.status_code == 200
    assert r.json() == []


def test_summary_tanggal_tidak_valid_ditolak():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB:
        MockDB.return_value.get_session.return_value = MagicMock()
        r = client.get("/api/results/summary?date_from=bukan-tanggal")

    assert r.status_code == 400


def test_summary_meneruskan_seluruh_filter():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = []
        mock_sum.return_value = []
        client.get("/api/results/summary"
                   "?status=failed&instrument_id=2"
                   "&date_from=2026-08-10&date_to=2026-08-10")

    kwargs = mock_sum.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["instrument_id"] == 2
    # date_to tanggal polos digeser ke akhir hari oleh _parse_filter_date
    assert kwargs["received_to"].hour == 23
    assert kwargs["received_to"].minute == 59
    assert kwargs["received_from"].hour == 0


def test_endpoint_results_lama_tidak_tertangkap_rute_summary():
    """/api/results/{id}/retry harus tetap jalan setelah rute summary ditambah."""
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.filter.return_value.first.return_value = None
        r = client.post("/api/results/5/retry")

    assert r.status_code == 404
