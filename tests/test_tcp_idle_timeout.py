"""
tests/test_tcp_idle_timeout.py — deteksi koneksi setengah mati (half-open)

Latar: Mindray BC-5150 (instrument 3, mode client) mengirim heartbeat <STX>
tiap 3 detik. Pada 7 Sep 2026 aliran itu tersendat bertahap lalu berhenti:

    03:58:43 -> 03:58:56   jeda  13 detik
    04:01:35 -> 04:03:22   jeda 107 detik
    04:05:32 -> 04:14:54   jeda 562 detik, lalu 180 <STX> masuk sekaligus
    04:14:54 -> (restart 8 Sep 14:19)   hening 34 jam

Tidak ada FIN, tidak ada RST, tidak ada satu pun baris reconnect di sepanjang
34 jam itu. Peer berhenti mengirim tanpa menutup koneksi, jadi `reader.read()`
menggantung selamanya dan jalur reconnect di _run_client_mode() tidak pernah
tersentuh. Socket-nya pun masih ESTAB tanpa timer keepalive (dibandingkan
socket MySQL di proses yang sama yang punya `timer:(keepalive,...)`), karena
tidak ada satu pun setsockopt di repo ini.

Perbaikannya dideklarasikan oleh MODUL PROTOKOL, bukan dipasang global:

    IDLE_RX_TIMEOUT        detik tanpa byte masuk sebelum koneksi dianggap mati
    TCP_KEEPALIVE_IDLE     detik idle sebelum kernel mulai mengirim probe
    TCP_KEEPALIVE_INTERVAL jarak antar probe
    TCP_KEEPALIVE_PROBES   jumlah probe gagal sebelum kernel menyerah

Semuanya dibaca lewat getattr() dengan default 0 = mati. Protokol yang tidak
mendeklarasikannya berperilaku persis seperti sebelumnya — dikunci oleh
test_protokol_lain_tidak_terpengaruh dan test_keepalive_tidak_dipasang_*.
"""

import asyncio
import logging
import socket

import pytest

from protocols.base import load_module
from services.tcp_socket.service import TCPSocketService


# ============================================================
# Test double
# ============================================================

class ProtokolPolos:
    """Protokol tanpa deklarasi transport apa pun — mayoritas modul."""
    PROTOCOL_NAME = "POLOS"
    VERSION = "1.0.0"


class ProtokolBerheartbeat:
    """Protokol yang meminta pengawasan idle, seperti BC-5150."""
    PROTOCOL_NAME = "BERHEARTBEAT"
    VERSION = "1.0.0"
    IDLE_RX_TIMEOUT = 0.3
    TCP_KEEPALIVE_IDLE = 30
    TCP_KEEPALIVE_INTERVAL = 10
    TCP_KEEPALIVE_PROBES = 3


class WriterPalsu:
    def __init__(self, sock=None):
        self._closing = False
        self._sock = sock

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass

    def write(self, data):
        pass

    async def drain(self):
        pass

    def get_extra_info(self, key):
        if key == "socket":
            return self._sock
        return ("192.168.30.2", 5100)


class ReaderBisu:
    """Peer yang berhenti mengirim tanpa menutup koneksi — kasus 7 Sep."""

    async def read(self, n):
        await asyncio.Event().wait()   # menggantung selamanya


class ReaderHeartbeat:
    """Mengirim <STX> beberapa kali dengan jeda, lalu bisu selamanya."""

    def __init__(self, jumlah, jeda):
        self._sisa = jumlah
        self._jeda = jeda

    async def read(self, n):
        await asyncio.sleep(self._jeda)
        if self._sisa <= 0:
            await asyncio.Event().wait()
        self._sisa -= 1
        return b"\x02"


class ReceiverPalsu:
    def __init__(self):
        self.data = []
        self.last_query_data = None

    async def handle_data(self, data, writer):
        self.data.append(data)
        return False

    def reset_buffer(self):
        pass


class CommPalsu:
    def rx(self, data):
        pass

    def tx(self, data):
        pass


def konteks_baru():
    return {"ditutup_midlab": False}


def buat_service(protokol):
    """TCPSocketService tanpa DB/socket — hanya bagian yang diuji yang diisi."""
    s = object.__new__(TCPSocketService)
    s._logger = logging.getLogger("test_tcp_idle")
    s._logger.setLevel(logging.INFO)
    s._tag = "[TCP_9] [Uji]"
    s._comm = CommPalsu()
    s._protocol = protokol
    s._running = True
    s._connected = True
    s._receiver = None
    s._broadcast_worker = None
    s._query_handler = None
    s._reader = None
    s._writer = None
    s._konteks_koneksi = None
    s._emit_lis_status = lambda *a, **k: None
    return s


# ============================================================
# 1 — idle timeout memutus koneksi setengah mati
# ============================================================

async def jalankan_loop(svc, reader, writer, receiver):
    """
    Jalankan _receive_loop sebagai task terpisah.

    Sengaja TIDAK memakai asyncio.wait_for() untuk membatasi loop: _receive_loop
    menangkap CancelledError lalu keluar normal, jadi wait_for tidak pernah
    melempar TimeoutError dan "loop masih menunggu" jadi tidak bisa dibedakan
    dari "loop sudah selesai". Selesai atau tidaknya diperiksa lewat
    task.done() setelah tidur sebentar.
    """
    task = asyncio.create_task(
        svc._receive_loop(reader, writer, receiver, konteks_baru())
    )
    return task


async def hentikan(task):
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_peer_bisu_memutus_receive_loop(caplog):
    # Inti bug 7 Sep: peer diam tanpa FIN/RST. Tanpa pengawas idle, loop ini
    # tidak akan pernah selesai dan jalur reconnect tidak pernah jalan.
    svc = buat_service(ProtokolBerheartbeat())
    writer = WriterPalsu()

    with caplog.at_level(logging.INFO, logger="test_tcp_idle"):
        task = await jalankan_loop(svc, ReaderBisu(), writer, ReceiverPalsu())
        try:
            await asyncio.sleep(0.6)          # IDLE_RX_TIMEOUT = 0.3
            assert task.done(), "loop masih menggantung di read()"
        finally:
            await hentikan(task)

    assert "Tidak ada data" in caplog.text
    assert [r for r in caplog.records if r.levelno == logging.WARNING]
    # Socket mati harus benar-benar ditutup, bukan sekadar ditinggalkan.
    assert writer.is_closing()


async def test_heartbeat_menunda_idle_timeout(caplog):
    # Selama heartbeat masih datang, koneksi tidak boleh diputus. Batas idle
    # 0.3 detik dengan heartbeat tiap 0.1 detik: 5 heartbeat lewat dulu, baru
    # setelah alat bisu koneksi diputus.
    svc = buat_service(ProtokolBerheartbeat())
    receiver = ReceiverPalsu()

    with caplog.at_level(logging.INFO, logger="test_tcp_idle"):
        task = await jalankan_loop(
            svc, ReaderHeartbeat(jumlah=5, jeda=0.1),
            WriterPalsu(), receiver,
        )
        try:
            await asyncio.sleep(0.45)
            assert not task.done(), "heartbeat tidak menyegarkan batas idle"
            await asyncio.sleep(0.9)
            assert task.done()
        finally:
            await hentikan(task)

    assert receiver.data == [b"\x02"] * 5
    assert "Tidak ada data" in caplog.text


async def test_protokol_lain_tidak_terpengaruh():
    # Regresi lintas-protokol: modul tanpa IDLE_RX_TIMEOUT harus tetap
    # menunggu selamanya seperti perilaku hari ini.
    svc = buat_service(ProtokolPolos())
    writer = WriterPalsu()

    task = await jalankan_loop(svc, ReaderBisu(), writer, ReceiverPalsu())
    try:
        await asyncio.sleep(0.6)
        assert not task.done()
        assert not writer.is_closing()
    finally:
        await hentikan(task)


# ============================================================
# 2 — TCP keepalive dipasang per protokol
# ============================================================

def buat_socket_pair():
    """
    Sepasang socket TCP sungguhan di loopback.

    socket.socketpair() tidak bisa dipakai: di Linux ia menghasilkan AF_UNIX,
    dan setsockopt(IPPROTO_TCP, TCP_KEEPIDLE) di sana gagal ENOPROTOOPT. Opsi
    yang sedang diuji hanya ada artinya di socket TCP.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    klien = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    klien.connect(listener.getsockname())
    server, _ = listener.accept()
    listener.close()
    return klien, server


async def test_keepalive_dipasang_untuk_protokol_yang_meminta():
    # Kernel harus punya cara sendiri mendeteksi peer mati, tidak bergantung
    # pada trafik aplikasi. Default sistem 7200 detik terlalu lama untuk alat.
    svc = buat_service(ProtokolBerheartbeat())
    sock, lawan = buat_socket_pair()
    try:
        svc._apply_socket_options(WriterPalsu(sock))

        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == 30
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) == 10
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT) == 3
    finally:
        sock.close()
        lawan.close()


async def test_keepalive_tidak_dipasang_untuk_protokol_lain():
    # Regresi lintas-protokol: modul yang tidak meminta apa-apa harus
    # meninggalkan socket persis seperti default sistem.
    svc = buat_service(ProtokolPolos())
    sock, lawan = buat_socket_pair()
    try:
        svc._apply_socket_options(WriterPalsu(sock))

        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 0
    finally:
        sock.close()
        lawan.close()


async def test_socket_tidak_tersedia_tidak_menjatuhkan_service():
    # writer.get_extra_info("socket") bisa mengembalikan None (mis. transport
    # non-socket). Itu tidak boleh menghentikan koneksi.
    svc = buat_service(ProtokolBerheartbeat())
    svc._apply_socket_options(WriterPalsu(None))


# ============================================================
# 3 — BC-5150 memang yang mendeklarasikan
# ============================================================

def test_bc5150_mendeklarasikan_pengawasan_idle():
    modul = load_module("HL7_MINDRAY_BC5150")

    # Heartbeat alat tiap 3 detik; 90 detik = 30 heartbeat terlewat.
    assert modul.IDLE_RX_TIMEOUT == 90
    assert modul.TCP_KEEPALIVE_IDLE == 30
    assert modul.TCP_KEEPALIVE_INTERVAL == 10
    assert modul.TCP_KEEPALIVE_PROBES == 3


@pytest.mark.parametrize(
    "nama",
    ["HL7_MINDRAY_BS200E", "ASTM", "HL7"],
)
def test_protokol_lain_tidak_ikut_mendeklarasikan(nama):
    # Perbaikan ini sengaja dibatasi ke BC-5150 saja.
    modul = load_module(nama)

    assert getattr(modul, "IDLE_RX_TIMEOUT", 0) == 0
    assert getattr(modul, "TCP_KEEPALIVE_IDLE", 0) == 0


# ============================================================
# 4 — socket sungguhan, bukan reader palsu
# ============================================================

async def test_idle_timeout_pada_stream_asyncio_sungguhan(caplog):
    """
    Test di atas memakai reader palsu. Yang ini memakai asyncio.StreamReader
    asli di atas socket TCP loopback: server menerima koneksi lalu diam tanpa
    menutup — persis kelakuan peer BC-5150 pada 7 Sep. Membuktikan
    asyncio.wait_for() memang membatalkan read() sungguhan, bukan hanya
    coroutine buatan test.
    """
    svc = buat_service(ProtokolBerheartbeat())      # IDLE_RX_TIMEOUT = 0.3

    async def diam(reader_srv, writer_srv):
        # Menunggu tanpa pernah mengirim sepatah byte pun. Sengaja membaca
        # (bukan menunggu Event selamanya) supaya handler ini ikut selesai
        # begitu sisi klien menutup — kalau tidak, server.wait_closed() di
        # blok finally menggantung dan test-nya yang macet, bukan kodenya.
        await reader_srv.read()
        writer_srv.close()

    server = await asyncio.start_server(diam, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    try:
        with caplog.at_level(logging.INFO, logger="test_tcp_idle"):
            task = asyncio.create_task(
                svc._receive_loop(reader, writer, ReceiverPalsu(), konteks_baru())
            )
            try:
                await asyncio.sleep(0.6)
                assert task.done(), "read() sungguhan tidak ikut dibatalkan"
            finally:
                await hentikan(task)

        assert "Tidak ada data" in caplog.text
        assert writer.is_closing()
    finally:
        writer.close()
        server.close()
        await server.wait_closed()


async def test_timeout_dari_handler_bukan_idle(caplog):
    """
    "Tidak ada data" hanya boleh berarti socket yang bisu.

    Kalau handler di jalur dispatch melempar TimeoutError karena urusannya
    sendiri (QueryHandler dan BroadcastWorker sama-sama menunggu ACK 15 detik),
    itu bukan koneksi mati dan tidak boleh diberi label idle. Keduanya memang
    menangkap timeout-nya sendiri hari ini; test ini menjaga agar penempatan
    except di receive loop tidak diam-diam mengklaim kasus yang bukan miliknya.
    """
    svc = buat_service(ProtokolBerheartbeat())

    class ReceiverTimeout:
        last_query_data = None

        async def handle_data(self, data, writer):
            raise asyncio.TimeoutError()

        def reset_buffer(self):
            pass

    with caplog.at_level(logging.INFO, logger="test_tcp_idle"):
        task = await jalankan_loop(
            svc, ReaderHeartbeat(jumlah=1, jeda=0.01),
            WriterPalsu(), ReceiverTimeout(),
        )
        try:
            await asyncio.sleep(0.15)
            assert task.done()
        finally:
            await hentikan(task)

    assert "Tidak ada data" not in caplog.text
    assert "Error dalam receive loop" in caplog.text


# ============================================================
# 5 — pemeriksaan ping sebelum sambung ulang
# ============================================================

class ProtokolPing(ProtokolBerheartbeat):
    """Protokol yang minta alat di-ping dulu sebelum disambung ulang."""
    PING_BEFORE_RECONNECT = True


class KonfigPalsu:
    id = 9
    name = "Uji"
    ip_address = "192.168.30.2"
    port = 5100


def buat_service_ping(protokol, ip="192.168.30.2"):
    svc = buat_service(protokol)
    konfig = KonfigPalsu()
    konfig.ip_address = ip
    svc._config = konfig
    svc._shutdown_event = asyncio.Event()
    return svc


async def test_ping_ke_loopback_balas():
    # Memakai ping sungguhan, bukan mock: yang diuji justru pemetaan exit code
    # perintah ping ke True/False.
    svc = buat_service_ping(ProtokolPing(), ip="127.0.0.1")

    assert await svc._ping_alat() is True


async def test_ping_ke_alamat_mati_tidak_balas():
    # 192.0.2.0/24 adalah blok TEST-NET-1 (RFC 5737) yang dijamin tidak
    # dirutekan ke mana pun, jadi tidak akan pernah membalas.
    svc = buat_service_ping(ProtokolPing(), ip="192.0.2.1")

    assert await svc._ping_alat(timeout=1) is False


async def test_ping_tidak_tersedia_mengembalikan_none(monkeypatch):
    # Kalau binary ping tidak ada, hasilnya harus "tidak tahu" (None), bukan
    # "tidak balas" (False) — beda artinya jauh untuk orang yang baca log.
    from services.tcp_socket import service as modul

    monkeypatch.setattr(modul, "PING_BINARY", "ping-yang-tidak-ada")
    svc = buat_service_ping(ProtokolPing(), ip="127.0.0.1")

    assert await svc._ping_alat() is None


async def test_gate_lanjut_saat_ping_balas(caplog):
    # Alat membalas ping = jalur jaringan sehat. Kendalanya di sisi aplikasi
    # atau alat, dan sambung ulang layak dicoba sekarang juga.
    svc = buat_service_ping(ProtokolPing())
    svc._ping_alat = lambda *a, **k: _hasil(True)

    with caplog.at_level(logging.INFO, logger="test_tcp_idle"):
        await asyncio.wait_for(svc._tunggu_alat_bisa_dihubungi(), timeout=5)

    assert "BALAS" in caplog.text
    assert "jaringan ke alat sehat" in caplog.text


async def test_gate_menunda_selama_ping_tidak_balas(caplog, monkeypatch):
    # Alat tidak membalas ping = masalahnya di bawah TCP: kabel, converter,
    # switch, atau alat mati. Sambung ulang ditahan sampai ping berhasil.
    from services.tcp_socket import service as modul

    monkeypatch.setattr(modul, "PING_RETRY_DELAY", 0.05)
    svc = buat_service_ping(ProtokolPing())
    jawaban = [False, False, True]

    def ping_bertahap(*a, **k):
        return _hasil(jawaban.pop(0))

    svc._ping_alat = ping_bertahap

    with caplog.at_level(logging.INFO, logger="test_tcp_idle"):
        await asyncio.wait_for(svc._tunggu_alat_bisa_dihubungi(), timeout=5)

    assert jawaban == []                       # ketiga ping benar-benar dipakai
    assert "TIDAK BALAS" in caplog.text
    assert "kabel" in caplog.text              # arahkan teknisi ke sisi fisik
    assert "BALAS" in caplog.text
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


async def test_gate_lanjut_saat_ping_tidak_tersedia(caplog):
    # Ping tidak tersedia tidak boleh mengunci alat selamanya tanpa koneksi.
    svc = buat_service_ping(ProtokolPing())
    svc._ping_alat = lambda *a, **k: _hasil(None)

    with caplog.at_level(logging.INFO, logger="test_tcp_idle"):
        await asyncio.wait_for(svc._tunggu_alat_bisa_dihubungi(), timeout=5)

    assert "tidak tersedia" in caplog.text


async def test_gate_tidak_aktif_untuk_protokol_lain():
    # Regresi lintas-protokol: tanpa PING_BEFORE_RECONNECT, tidak ada ping
    # sama sekali dan tidak ada penundaan sama sekali.
    svc = buat_service_ping(ProtokolPolos())
    dipanggil = []

    def jangan_dipanggil(*a, **k):
        dipanggil.append(1)
        return _hasil(True)

    svc._ping_alat = jangan_dipanggil

    await asyncio.wait_for(svc._tunggu_alat_bisa_dihubungi(), timeout=1)

    assert dipanggil == []


async def _hasil(nilai):
    return nilai


def test_bc5150_meminta_pemeriksaan_ping():
    modul = load_module("HL7_MINDRAY_BC5150")

    assert modul.PING_BEFORE_RECONNECT is True


@pytest.mark.parametrize("nama", ["HL7_MINDRAY_BS200E", "ASTM", "HL7"])
def test_protokol_lain_tidak_meminta_ping(nama):
    modul = load_module(nama)

    assert getattr(modul, "PING_BEFORE_RECONNECT", False) is False


async def test_gate_dipasang_di_jalur_sambung_ulang(monkeypatch, caplog):
    """
    Gate harus benar-benar terpanggil oleh _run_client_mode, bukan sekadar ada.

    Ini bagian yang paling gampang salah diam-diam: metodenya lulus semua tes
    sendiri tapi tidak pernah dipanggil siapa pun. Koneksi dibuat selalu gagal
    supaya loop langsung masuk jalur sambung ulang.
    """
    from services.tcp_socket import service as modul

    monkeypatch.setattr(modul, "RECONNECT_DELAY", 0.05)

    async def selalu_gagal(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(modul.asyncio, "open_connection", selalu_gagal)

    svc = buat_service_ping(ProtokolPing())
    dipanggil = []

    async def gate_palsu():
        dipanggil.append(1)
        svc._running = False
        svc._shutdown_event.set()

    svc._tunggu_alat_bisa_dihubungi = gate_palsu

    with caplog.at_level(logging.INFO, logger="test_tcp_idle"):
        await asyncio.wait_for(svc._run_client_mode(), timeout=5)

    assert dipanggil, "_run_client_mode tidak memanggil pemeriksaan ping"


async def test_jeda_ping_naik_bertahap_dan_dibatasi():
    """
    Gangguan fisik bisa berjam-jam. Ping tiap 10 detik selama 34 jam seperti
    insiden 7 Sep berarti ~12.000 baris WARNING, dan LogPusher mengirim setiap
    WARN/ERROR ke LIS. Jeda dinaikkan bertahap supaya log tetap terbaca.
    """
    svc = buat_service_ping(ProtokolPing())

    jeda = [svc._jeda_ping_berikutnya(n) for n in range(1, 8)]

    assert jeda == [10, 20, 40, 80, 160, 300, 300]


async def test_status_lis_hanya_sekali_walau_ping_gagal_berkali(monkeypatch):
    """
    Status alat tidak berubah selama ia tetap tak terjangkau, jadi satu event
    sudah cukup. Tanpa ini tbl_lis_event_queue terisi ribuan baris identik
    selama satu gangguan.
    """
    from services.tcp_socket import service as modul

    monkeypatch.setattr(modul, "PING_RETRY_DELAY", 0.01)
    monkeypatch.setattr(modul, "PING_RETRY_DELAY_MAX", 0.02)

    svc = buat_service_ping(ProtokolPing())
    event = []
    svc._emit_lis_status = lambda *a, **k: event.append(a)
    jawaban = [False, False, False, False, True]
    svc._ping_alat = lambda *a, **k: _hasil(jawaban.pop(0))

    await asyncio.wait_for(svc._tunggu_alat_bisa_dihubungi(), timeout=5)

    assert jawaban == []
    assert len(event) == 1, f"status dikirim {len(event)} kali, harusnya sekali"
