import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from services.web_console.api import app


def test_list_lis_events_returns_array():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB:
        mock_session = MagicMock()
        chain = mock_session.query.return_value
        chain.order_by.return_value.limit.return_value.all.return_value = []
        MockDB.return_value.get_session.return_value = mock_session
        r = client.get("/api/lis-events")
    assert r.status_code == 200
    assert r.json() == []


def test_retry_endpoint_calls_helper():
    client = TestClient(app)
    with patch("lib.db.update_lis_event_status", return_value=True):
        r = client.post("/api/lis-events/5/retry")
    assert r.status_code == 200
