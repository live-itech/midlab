"""
tests/test_tcp_connection_lifecycle.py — siklus hidup koneksi TCPSocketService

Latar: Aruma AR580 (instrument 2) membuka koneksi baru tiap beberapa detik.
Perilaku itu memunculkan dua masalah di service.py:

1. Semua penutupan dilaporkan "by remote", termasuk koneksi yang MidLab sendiri
   tutup lewat _close_connection() saat koneksi baru masuk — log jadi penuh
   WARNING reset palsu yang menyamarkan reset sungguhan dari alat.
2. _receive_loop membaca self._reader/self._receiver (atribut bersama), jadi
   koneksi lama bisa membaca socket koneksi baru atau mematikan komponennya.

Catatan penting untuk (1): siapa yang menutup TIDAK boleh disimpulkan dari
writer.is_closing(). Diukur dengan socket sungguhan:

    klien tutup baik-baik (FIN) -> writer.is_closing() = False
    klien abort (RST)           -> writer.is_closing() = True

Pada RST dari alat, asyncio menutup transport sisi kita juga. Karena itu
dipakai penanda eksplisit konteks["ditutup_midlab"] yang hanya diset
_close_connection(). test_rst_alat_tidak_tertukar_dengan_penutupan_midlab
mengunci pembedaan itu.
"""

import asyncio
import logging

import pytest

from services.tcp_socket.service import TCPSocketService


# ============================================================
# Test double
# ============================================================

class WriterPalsu:
    """
    StreamWriter minimal.

    is_closing() meniru asyncio: bernilai True baik saat kita menutup MAUPUN
    saat peer mengirim RST. Kalau kode kembali memakainya untuk menebak siapa
    yang menutup, test RST di bawah akan gagal.
    """

    def __init__(self, peer=("192.168.30.11", 56515)):
        self._closing = False
        self._peer = peer
        self.terkirim = []

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass

    def write(self, data):
        self.terkirim.append(data)

    async def drain(self):
        pass

    def get_extra_info(self, key):
        return self._peer


class ReaderPalsu:
    """Memutar daftar potongan; item Exception dilempar, habis = EOF."""

    def __init__(self, potongan=()):
        self._potongan = list(potongan)

    async def read(self, n):
        if not self._potongan:
            return b""
        item = self._potongan.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ReceiverPalsu:
    def __init__(self, nama="r"):
        self.nama = nama
        self.data = []
        self.buffer_direset = False
        self.last_query_data = None

    async def handle_data(self, data, writer):
        self.data.append(data)
        return False

    def reset_buffer(self):
        self.buffer_direset = True


class CommPalsu:
    def rx(self, data):
        pass

    def tx(self, data):
        pass


def konteks_baru():
    return {"ditutup_midlab": False}


@pytest.fixture
def svc():
    """TCPSocketService tanpa DB/socket — hanya bagian yang diuji yang diisi."""
    s = object.__new__(TCPSocketService)
    s._logger = logging.getLogger("test_tcp_lifecycle")
    s._logger.setLevel(logging.INFO)
    s._tag = "[TCP_9] [Uji]"
    s._comm = CommPalsu()
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
# 1 — label penutupan koneksi
# ============================================================

@pytest.mark.asyncio
async def test_koneksi_ditutup_midlab_bukan_warning(svc, caplog):
    # Koneksi yang MidLab tutup sendiri tidak boleh dilaporkan sebagai
    # gangguan dari alat.
    konteks = konteks_baru()
    konteks["ditutup_midlab"] = True
    with caplog.at_level(logging.INFO, logger="test_tcp_lifecycle"):
        await svc._receive_loop(
            ReaderPalsu(), WriterPalsu(), ReceiverPalsu(), konteks
        )

    teks = caplog.text
    assert "ditutup MidLab" in teks
    assert "digantikan koneksi baru" in teks
    assert "reset" not in teks.lower()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_koneksi_ditutup_midlab_saat_service_berhenti(svc, caplog):
    # Urutan nyata SIGTERM: loop sedang menunggu read(), lalu stop() menyetel
    # _running=False dan _close_connection() menandai konteks.
    konteks = konteks_baru()

    class ReaderShutdown:
        async def read(self, n):
            svc._running = False
            konteks["ditutup_midlab"] = True
            return b""

    with caplog.at_level(logging.INFO, logger="test_tcp_lifecycle"):
        await svc._receive_loop(
            ReaderShutdown(), WriterPalsu(), ReceiverPalsu(), konteks
        )

    assert "ditutup MidLab" in caplog.text
    assert "service berhenti" in caplog.text


@pytest.mark.asyncio
async def test_alat_memutus_dengan_fin(svc, caplog):
    # EOF tanpa penanda = alat yang menutup baik-baik.
    with caplog.at_level(logging.INFO, logger="test_tcp_lifecycle"):
        await svc._receive_loop(
            ReaderPalsu(), WriterPalsu(), ReceiverPalsu(), konteks_baru()
        )

    assert "ditutup alat (FIN)" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_alat_mereset_koneksi_tetap_warning(svc, caplog):
    # RST dari alat adalah gangguan sungguhan — harus tetap WARNING agar
    # terlihat di log viewer.
    reader = ReaderPalsu([ConnectionResetError()])
    with caplog.at_level(logging.INFO, logger="test_tcp_lifecycle"):
        await svc._receive_loop(
            reader, WriterPalsu(), ReceiverPalsu(), konteks_baru()
        )

    assert "di-reset alat (RST)" in caplog.text
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_rst_alat_tidak_tertukar_dengan_penutupan_midlab(svc, caplog):
    # Regresi. Pada RST, asyncio menutup transport sisi kita juga sehingga
    # writer.is_closing() True — sama seperti saat MidLab yang menutup.
    # Menyimpulkan pelakunya dari is_closing() membuat SETIAP reset alat
    # dilaporkan "ditutup MidLab (digantikan koneksi baru)", padahal tidak ada
    # koneksi pengganti sama sekali.
    writer = WriterPalsu()

    class ReaderReset:
        async def read(self, n):
            writer.close()          # transport ikut closing, seperti asyncio
            raise ConnectionResetError()

    with caplog.at_level(logging.INFO, logger="test_tcp_lifecycle"):
        await svc._receive_loop(
            ReaderReset(), writer, ReceiverPalsu(), konteks_baru()
        )

    assert "di-reset alat (RST)" in caplog.text
    assert "ditutup MidLab" not in caplog.text


@pytest.mark.asyncio
async def test_close_connection_menandai_konteks(svc):
    # Penanda harus berasal dari _close_connection(), bukan ditebak.
    konteks = konteks_baru()
    svc._konteks_koneksi = konteks
    svc._writer = WriterPalsu()

    await svc._close_connection()

    assert konteks["ditutup_midlab"] is True
    assert svc._konteks_koneksi is None, "konteks lama harus dilepas"


# ============================================================
# 2 — state per-koneksi
# ============================================================

@pytest.mark.asyncio
async def test_receive_loop_tidak_menyentuh_atribut_bersama(svc):
    # self._reader/_writer/_receiver sengaja dibiarkan None: kalau loop masih
    # membaca atribut instance, test ini gagal dengan AttributeError.
    svc._reader = None
    svc._writer = None
    svc._receiver = None
    receiver = ReceiverPalsu()
    reader = ReaderPalsu([b"\x0bHALO\x1c\r"])

    await svc._receive_loop(reader, WriterPalsu(), receiver, konteks_baru())

    assert receiver.data == [b"\x0bHALO\x1c\r"]


@pytest.mark.asyncio
async def test_koneksi_lama_tidak_mematikan_komponen_koneksi_baru(svc):
    # Skenario AR580: koneksi lama baru selesai cleanup setelah koneksi
    # penerusnya siap. Cleanup itu tidak boleh menyentuh state bersama.
    receiver_lama = ReceiverPalsu("lama")
    receiver_baru = ReceiverPalsu("baru")
    svc._receiver = receiver_baru
    svc._connected = True

    dipanggil = []
    svc._stop_components = lambda: dipanggil.append(True)  # noqa: E731

    await svc._selesaikan_koneksi(("192.168.30.11", 56515), receiver_lama)

    assert svc._receiver is receiver_baru, "receiver koneksi baru ikut terhapus"
    assert svc._connected is True, "koneksi aktif ditandai offline oleh koneksi lama"
    assert dipanggil == [], "_stop_components() koneksi baru ikut dijalankan"
    assert receiver_lama.buffer_direset, "buffer koneksi lama harus dilepas"


@pytest.mark.asyncio
async def test_koneksi_aktif_tetap_dibersihkan_normal(svc):
    # Kebalikannya: kalau koneksi ini memang yang aktif, cleanup harus jalan.
    receiver = ReceiverPalsu()
    svc._receiver = receiver
    svc._connected = True

    dipanggil = []

    async def stop_palsu():
        dipanggil.append(True)

    svc._stop_components = stop_palsu

    await svc._selesaikan_koneksi(("192.168.30.11", 56515), receiver)

    assert svc._connected is False
    assert dipanggil == [True]


# ============================================================
# 3 — format log punya timestamp
# ============================================================

def test_format_log_service_punya_timestamp(tmp_path, monkeypatch):
    # Tanpa timestamp, log service tidak bisa dikorelasikan dengan *.comm.log.
    import lib.utils as utils

    monkeypatch.setattr(utils, "LOG_DIR", str(tmp_path))
    logger = utils.get_logger("uji_ts", instrument_id=9)
    logger.info("pesan uji")
    for h in logger.handlers:
        h.flush()

    isi = (tmp_path / "tcp_9.log").read_text()
    import re
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \[INFO\] \[UJI_TS\] \[9\] pesan uji",
        isi.strip(),
    ), f"format tak sesuai: {isi!r}"
