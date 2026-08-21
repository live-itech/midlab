"""Kontrak endpoint kirim-ulang result ke LIS (POST /api/results/{id}/retry)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from services.web_console.api import app


def _row(send_status: str, retry_count: int = 0):
    row = MagicMock()
    row.send_status = send_status
    row.retry_count = retry_count
    return row


def _post_retry(row, result_id: int = 7):
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.filter.return_value.first.return_value = row
        return client.post(f"/api/results/{result_id}/retry"), session


def test_result_sent_bisa_dikirim_ulang():
    """Bukan cuma 'failed' — result yang sudah sampai LIS pun bisa diulang."""
    row = _row("sent", retry_count=2)
    resp, session = _post_retry(row)

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert row.send_status == "pending"
    session.commit.assert_called_once()


def test_kirim_ulang_result_sent_mengembalikan_jatah_retry():
    row = _row("sent", retry_count=2)
    _post_retry(row)

    assert row.retry_count == 0


def test_retry_result_failed_tidak_menyentuh_retry_count():
    """Perilaku retry lama harus persis sama seperti sebelum fitur kirim ulang."""
    row = _row("failed", retry_count=3)
    resp, _ = _post_retry(row)

    assert resp.status_code == 200
    assert row.send_status == "pending"
    assert row.retry_count == 3


def test_retry_result_pending_tetap_pending():
    row = _row("pending", retry_count=1)
    resp, _ = _post_retry(row)

    assert resp.status_code == 200
    assert row.send_status == "pending"
    assert row.retry_count == 1


def test_error_message_mencatat_status_sebelumnya():
    row = _row("sent")
    _post_retry(row)

    assert "sent" in row.error_message


def test_result_tidak_ada_404():
    resp, _ = _post_retry(None, result_id=999)

    assert resp.status_code == 404
