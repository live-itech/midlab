"""
Test lanjut-tapping: sesi yang sudah 'stopped' bisa di-start lagi.

Kenapa perlu: capture untuk bikin driver jarang selesai dalam satu duduk — alat
dinyalakan lagi besoknya, dan operator ingin melanjutkan capture yang sama
dengan config yang sama. Sebelum ini satu-satunya jalan adalah membuat sesi baru
(config diketik ulang, capture terpecah dua file).

Konfignya tidak disimpan kolom per kolom; ia direkonstruksi dari `target` —
string yang formatnya ditentukan oleh transport.description sendiri.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.tap import service as svc


class TestTransportFromRow:
    """Rekonstruksi transport dari metadata baris sesi."""

    def baris(self, transport, target):
        return MagicMock(transport=transport, target=target)

    def test_tcp_server(self):
        t = svc.transport_from_row(self.baris("tcp_server", "tcp-server 0.0.0.0:2600"))
        assert t.description == "tcp-server 0.0.0.0:2600"

    def test_tcp_client(self):
        t = svc.transport_from_row(
            self.baris("tcp_client", "tcp-client 192.168.1.10:9100")
        )
        assert t.description == "tcp-client 192.168.1.10:9100"

    def test_serial_lengkap_dengan_frame(self):
        t = svc.transport_from_row(
            self.baris("serial", "serial /dev/ttyUSB0@19200-8E1")
        )
        assert t.description == "serial /dev/ttyUSB0@19200-8E1"

    def test_target_tanpa_prefix_tetap_terbaca(self):
        # Docstring kolom di lib/db.py mencontohkan target tanpa prefix
        # ("0.0.0.0:2600"). Baris lama boleh saja berbentuk begitu.
        t = svc.transport_from_row(self.baris("tcp_server", "0.0.0.0:2600"))
        assert t.description == "tcp-server 0.0.0.0:2600"

    def test_serial_tanpa_frame_pakai_default_8n1(self):
        t = svc.transport_from_row(self.baris("serial", "serial /dev/ttyUSB0@9600"))
        assert t.description == "serial /dev/ttyUSB0@9600-8N1"

    def test_target_kacau_error_jelas(self):
        with pytest.raises(ValueError):
            svc.transport_from_row(self.baris("tcp_server", "entah-apa"))

    def test_port_bukan_angka_error_jelas(self):
        with pytest.raises(ValueError):
            svc.transport_from_row(self.baris("tcp_client", "tcp-client alat:abc"))


class TestSimpanHasilLanjutan:
    """Counter sesi lanjutan harus menumpuk, bukan menimpa."""

    def jalankan(self, baseline):
        row = MagicMock(bytes_rx=0, bytes_tx=0, message_count=0)
        tap = MagicMock(bytes_rx=10, bytes_tx=4, message_count=1, detected="HL7")
        with patch("services.tap.service.DBManager") as MockDB:
            sesi = MockDB.return_value.get_session.return_value
            sesi.get.return_value = row
            svc._simpan_hasil(1, tap, "stopped", None, baseline=baseline)
        return row

    def test_tanpa_baseline_nilai_apa_adanya(self):
        row = self.jalankan(None)
        assert (row.bytes_rx, row.bytes_tx, row.message_count) == (10, 4, 1)

    def test_dengan_baseline_ditambahkan(self):
        # Sesi pertama sudah merekam 100 byte RX; lanjutan menambah 10.
        row = self.jalankan({"bytes_rx": 100, "bytes_tx": 20, "message_count": 3})
        assert (row.bytes_rx, row.bytes_tx, row.message_count) == (110, 24, 4)

    def test_tap_none_counter_tidak_disentuh(self):
        # transport.open() gagal → belum ada TapSession sama sekali. Baris tetap
        # harus ditutup dengan status error, tanpa menimpa counter sesi lama.
        row = MagicMock(bytes_rx=100, bytes_tx=20, message_count=3)
        with patch("services.tap.service.DBManager") as MockDB:
            MockDB.return_value.get_session.return_value.get.return_value = row
            svc._simpan_hasil(1, None, "error", "connection refused")
        assert (row.bytes_rx, row.bytes_tx, row.message_count) == (100, 20, 3)
        assert row.status == "error"
        assert row.error_message == "connection refused"


pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient          # noqa: E402

from services.web_console import api               # noqa: E402
from services.web_console.api import app           # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db():
    with patch("services.web_console.api.DBManager") as MockDB:
        MockDB.return_value.get_session.return_value = MagicMock()
        yield MockDB


@pytest.fixture
def port_bebas(monkeypatch):
    """
    Bikin check_port_free() selalu lolos.

    DB-nya MagicMock, jadi query alat aktif mengembalikan objek palsu yang truthy
    dan pengecekan port selalu bilang bentrok — itu artefak mock, bukan perilaku
    yang sedang diuji.
    """
    monkeypatch.setattr(api.tap_service, "check_port_free", lambda port, session: None)


def baris_stopped(**over):
    row = MagicMock(
        id=1, transport="tcp_server", target="tcp-server 0.0.0.0:2600",
        protocol_basis="HL7", detected_protocol="HL7", response_mode="uni",
        status="stopped", bytes_rx=100, bytes_tx=20, message_count=3,
        error_message=None, started_at=None, stopped_at=None,
    )
    # `name` tidak bisa lewat konstruktor MagicMock — itu kwarg milik mock sendiri.
    row.name = "AR580"
    for k, v in over.items():
        setattr(row, k, v)
    return row


class TestStartSession:
    def test_sesi_tak_dikenal_404(self, client, mock_db, monkeypatch):
        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: None)
        mock_db.return_value.get_session.return_value.get.return_value = None
        r = client.post("/api/tap/sessions/999/start")
        assert r.status_code == 404

    def test_sesi_yang_sedang_jalan_409(self, client, mock_db, monkeypatch):
        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: object())
        r = client.post("/api/tap/sessions/1/start")
        assert r.status_code == 409

    def test_sesi_stopped_dijalankan_lagi(self, client, mock_db, monkeypatch,
                                         port_bebas):
        dijalankan = []
        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: None)
        monkeypatch.setattr(
            api._TAP_RUNNER, "start",
            lambda *a, **kw: dijalankan.append((a, kw)),
        )
        row = baris_stopped(error_message="putus")
        mock_db.return_value.get_session.return_value.get.return_value = row

        r = client.post("/api/tap/sessions/1/start")
        assert r.status_code == 200, r.text

        # Baris dipakai ulang — bukan bikin sesi baru.
        assert r.json()["id"] == 1
        assert row.status == "running"
        assert row.stopped_at is None
        assert row.error_message is None

        (args, kwargs) = dijalankan[0]
        assert args[0] == 1
        assert args[1].description == "tcp-server 0.0.0.0:2600"
        assert args[2] == "HL7"
        assert args[3] == "uni"
        # Counter lama jadi baseline supaya angka di UI terus bertambah.
        assert kwargs["baseline"] == {
            "bytes_rx": 100, "bytes_tx": 20, "message_count": 3,
        }

    def test_baris_yatim_running_boleh_di_start(self, client, mock_db, monkeypatch,
                                                port_bebas):
        # Web console pernah restart selagi sesi jalan: status DB 'running' tapi
        # tidak ada task-nya. Baris begini harus tetap bisa dijalankan lagi.
        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: None)
        monkeypatch.setattr(api._TAP_RUNNER, "start", lambda *a, **kw: None)
        row = baris_stopped(status="running")
        mock_db.return_value.get_session.return_value.get.return_value = row

        r = client.post("/api/tap/sessions/1/start")
        assert r.status_code == 200
        assert row.status == "running"

    def test_port_bentrok_alat_aktif_409(self, client, mock_db, monkeypatch):
        # Pengaman yang sama dengan sesi baru: dua pihak tidak boleh meng-ACK
        # alat yang sama.
        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: None)
        monkeypatch.setattr(api._TAP_RUNNER, "start", lambda *a, **kw: None)
        mock_db.return_value.get_session.return_value.get.return_value = baris_stopped()

        def tolak(port, session):
            raise svc.TapPortConflict(f"Port {port} dipakai alat aktif 'AR580'")

        monkeypatch.setattr(api.tap_service, "check_port_free", tolak)
        r = client.post("/api/tap/sessions/1/start")
        assert r.status_code == 409
        assert "2600" in r.json()["detail"]

    def test_target_kacau_400(self, client, mock_db, monkeypatch, port_bebas):
        monkeypatch.setattr(api._TAP_RUNNER, "get", lambda i: None)
        monkeypatch.setattr(api._TAP_RUNNER, "start", lambda *a, **kw: None)
        mock_db.return_value.get_session.return_value.get.return_value = (
            baris_stopped(target="entah-apa")
        )
        r = client.post("/api/tap/sessions/1/start")
        assert r.status_code == 400


class TestLanjutBeneran:
    """Dua kali jalan pada baris yang sama: capture nyambung, angka menumpuk."""

    @pytest.mark.asyncio
    async def test_capture_disambung_bukan_ditimpa(self, tmp_path, monkeypatch):
        import asyncio

        from services.tap.recorder import read_events
        from services.tap.transport.tcp import TcpServerTransport

        path = str(tmp_path / "7.jsonl")
        monkeypatch.setattr(api, "session_log_path", lambda i: path)

        row = MagicMock(bytes_rx=0, bytes_tx=0, message_count=0, detected_protocol=None)
        with patch("services.tap.service.DBManager") as MockDB:
            MockDB.return_value.get_session.return_value.get.return_value = row

            for kirim in (b"halo-", b"lagi!"):
                baseline = {
                    "bytes_rx": row.bytes_rx, "bytes_tx": row.bytes_tx,
                    "message_count": row.message_count,
                }
                transport = TcpServerTransport("127.0.0.1", 0)
                await transport.open()
                api._TAP_RUNNER.start(7, transport, "RAW", "uni", baseline=baseline)

                _, w = await asyncio.open_connection("127.0.0.1", transport.port)
                w.write(kirim)
                await w.drain()
                await asyncio.sleep(0.2)
                w.close()
                # Alat memutus → TapSession.run() selesai sendiri.
                for _ in range(30):
                    if api._TAP_RUNNER.get(7) is None:
                        break
                    await asyncio.sleep(0.05)
                assert api._TAP_RUNNER.get(7) is None, "sesi tidak menutup diri"

        # Satu file, dua sesi: byte lama masih ada, byte baru menyusul.
        hex_semua = "".join(e["hex"] for e in read_events(path) if e["dir"] == "rx")
        assert hex_semua == (b"halo-" + b"lagi!").hex()
        assert row.bytes_rx == 10          # 5 + 5, bukan direset ke 5
        assert row.status == "stopped"

    @pytest.mark.asyncio
    async def test_transport_gagal_dibuka_baris_tidak_nyangkut_running(
        self, tmp_path, monkeypatch
    ):
        # tcp_client ke alat yang mati: dulu exception open() lolos dari task,
        # baris tetap 'running', dan sesi itu tidak bisa di-start maupun di-stop.
        import asyncio

        from services.tap.transport.tcp import TcpClientTransport

        monkeypatch.setattr(api, "session_log_path", lambda i: str(tmp_path / "8.jsonl"))
        row = MagicMock(bytes_rx=100, bytes_tx=20, message_count=3)

        # Port 1 pada localhost: connect ditolak seketika.
        transport = TcpClientTransport("127.0.0.1", 1)
        with patch("services.tap.service.DBManager") as MockDB:
            MockDB.return_value.get_session.return_value.get.return_value = row
            api._TAP_RUNNER.start(8, transport, "RAW", "uni")
            for _ in range(40):
                if api._TAP_RUNNER.get(8) is None and row.status == "error":
                    break
                await asyncio.sleep(0.05)

        assert row.status == "error"
        assert row.error_message
        # Counter sesi sebelumnya tidak ikut hangus.
        assert (row.bytes_rx, row.bytes_tx, row.message_count) == (100, 20, 3)

