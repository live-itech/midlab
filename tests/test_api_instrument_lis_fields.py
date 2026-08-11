import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from services.web_console.api import app


def test_create_instrument_with_lis_fields_round_trips():
    client = TestClient(app)
    captured = {}

    def fake_add(row):
        captured["row"] = row

    def fake_commit():
        if "row" in captured:
            captured["row"].id = 99

    # `watchdog` baru diisi oleh handler startup FastAPI. TestClient tanpa
    # context manager tidak menjalankan lifespan, jadi tanpa patch ini nilainya
    # None dan endpoint gagal saat mendaftarkan service (500, bukan 201).
    # Di-mock, bukan dijalankan beneran: startup asli akan menyalakan service
    # alat sungguhan di mesin ini.
    with patch("services.web_console.api.DBManager") as MockDB, \
            patch("services.web_console.api.watchdog", MagicMock()):
        mock_session = MagicMock()
        mock_session.add.side_effect = fake_add
        mock_session.commit.side_effect = fake_commit
        MockDB.return_value.get_session.return_value = mock_session

        r = client.post("/api/instruments", json={
            "name": "X", "ip_address": "1.1.1.1", "port": 5000,
            "protocol": "ASTM", "mode": "unidirectional",
            "connection": "server",
            "lis_api_key": "inst_xxx",
            "order_poll_interval": 15,
            "lis_bridge_enabled": True,
        })
    assert r.status_code == 201
    body = r.json()
    assert body["order_poll_interval"] == 15
    assert body["lis_bridge_enabled"] is True
    assert "lis_api_key" not in body
