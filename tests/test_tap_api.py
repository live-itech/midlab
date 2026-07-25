"""
Test endpoint /api/tap/*.

Catatan auth: _verify_api_key() (api.py:171) langsung return bila
web_console.api_key kosong di config. Config test tidak menyetelnya, jadi
request TANPA header tetap 200 — jangan menulis test yang mengharapkan 401.

Catatan DB: endpoint memakai DBManager() yang menunjuk ke MySQL. Ikuti pola
tests/test_api_instrument_lis_fields.py — patch
"services.web_console.api.DBManager", jangan mengandalkan DB sungguhan.
"""

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from services.tap.recorder import TapRecorder
from services.web_console import api
from services.web_console.api import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db():
    """Patch DBManager supaya endpoint tidak menyentuh MySQL sungguhan."""
    with patch("services.web_console.api.DBManager") as MockDB:
        MockDB.return_value.get_session.return_value = MagicMock()
        yield MockDB


@pytest.fixture
def jsonl(tmp_path, monkeypatch):
    """Arahkan session_log_path() ke file sementara."""
    path = tmp_path / "1.jsonl"
    monkeypatch.setattr(api, "session_log_path", lambda i: str(path))
    return path


ORU = (
    b"\x0bMSH|^~\\&|Genrui|KT-6610|||1||ORU^R01|1275|P|2.3.1\r"
    b"OBX|1|NM|^WBC^||0.01|10^9/L\r\x1c\x0d"
)


class TestCreateSession:
    def test_port_bentrok_409(self, client, mock_db, monkeypatch):
        # 409, bukan 400: formatnya benar, yang salah keadaan sekarang.
        from services.tap import service as svc

        def tolak(port, session):
            raise svc.TapPortConflict("Port 2600 dipakai alat aktif 'AR580'")

        monkeypatch.setattr(api.tap_service, "check_port_free", tolak)
        r = client.post("/api/tap/sessions", json={
            "name": "x", "transport": "tcp_server", "port": 2600,
            "protocol_basis": "HL7", "response_mode": "uni",
        })
        assert r.status_code == 409
        assert "AR580" in r.json()["detail"]

    def test_tcp_tanpa_port_400(self, client, mock_db):
        r = client.post("/api/tap/sessions", json={
            "name": "x", "transport": "tcp_server",
            "protocol_basis": "HL7", "response_mode": "uni",
        })
        assert r.status_code == 400

    def test_serial_tanpa_serial_port_400(self, client, mock_db):
        r = client.post("/api/tap/sessions", json={
            "name": "x", "transport": "serial",
            "protocol_basis": "RAW", "response_mode": "uni",
        })
        assert r.status_code == 400


class TestGetEvents:
    def test_mengembalikan_event(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"\x05")
        rows = client.get("/api/tap/sessions/1/events").json()
        assert rows[0]["hex"] == "05"

    def test_sesi_tanpa_file_list_kosong(self, client, jsonl):
        assert client.get("/api/tap/sessions/1/events").json() == []


class TestExportBin:
    def test_hanya_byte_rx(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"\x0bMSH")
            r.write_event("tx", b"\x06")          # TX tidak ikut
        r2 = client.get("/api/tap/sessions/1/export/bin")
        assert r2.status_code == 200
        assert r2.content == b"\x0bMSH"

    def test_header_attachment(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"x")
        r2 = client.get("/api/tap/sessions/1/export/bin")
        assert "tap_1.bin" in r2.headers["content-disposition"]


class TestExportPython:
    def test_literal_bisa_di_eval_jadi_byte_asli(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", ORU)
            r.mark_message(0)
        r2 = client.get("/api/tap/sessions/1/export/python")
        assert r2.status_code == 200
        assert eval(r2.text.strip()) == ORU

    def test_index_di_luar_batas_404(self, client, jsonl):
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", ORU)
            r.mark_message(0)
        r2 = client.get("/api/tap/sessions/1/export/python?index=99")
        assert r2.status_code == 404
        assert "total 1" in r2.json()["detail"]

    def test_tanpa_pesan_404_menyarankan_bin(self, client, jsonl):
        # Basis RAW tidak punya batas pesan — arahkan operator ke export .bin.
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"\x00\x01\x02")
        r2 = client.get("/api/tap/sessions/1/export/python")
        assert r2.status_code == 404
        assert ".bin" in r2.json()["detail"]


class TestSendManual:
    def test_sesi_tidak_jalan_404(self, client):
        r = client.post("/api/tap/sessions/999/send", json={"hex": "05"})
        assert r.status_code == 404

    def test_hex_tidak_valid_400(self, client, monkeypatch):
        class TapPalsu:
            async def send_manual(self, data):
                pass

        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: TapPalsu())
        r = client.post("/api/tap/sessions/1/send", json={"hex": "zz"})
        assert r.status_code == 400
        assert "hex" in r.json()["detail"]

    def test_hex_valid_terkirim(self, client, monkeypatch):
        terkirim = []

        class TapPalsu:
            async def send_manual(self, data):
                terkirim.append(data)

        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: TapPalsu())
        r = client.post("/api/tap/sessions/1/send", json={"hex": "0b 05"})
        assert r.status_code == 200
        assert terkirim == [b"\x0b\x05"]


class TestStream:
    def test_sesi_mati_langsung_kirim_done(self, client, jsonl):
        # Sesi tidak ada di _TAP_RUNNER → stream mengirim event 'done' lalu tutup,
        # bukan menggantung selamanya.
        with TapRecorder(str(jsonl)) as r:
            r.write_event("rx", b"\x05")
        with client.stream("GET", "/api/tap/sessions/1/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = "".join(resp.iter_text())
        assert '"hex": "05"' in body or '"hex":"05"' in body
        assert "event: done" in body
