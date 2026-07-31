"""Test TapSession + model tbl_tap_session."""

import asyncio

import pytest

from lib.db import TblTapSession, TblInstrument
from services.tap.recorder import TapRecorder, read_events
from services.tap.session import TapSession
from services.tap.transport.base import BaseTransport
from services.tap.service import (
    TapPortConflict, check_port_free, build_transport, session_log_path,
)
from services.tap.transport.tcp import TcpServerTransport, TcpClientTransport
from services.tap.transport.serial_port import SerialTransport


VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"
ORU = VT + b"MSH|^~\\&|Genrui|KT-6610|||1||ORU^R01|1275|P|2.3.1\r" + FS + CR


class TransportPalsu(BaseTransport):
    """Transport dari daftar chunk; mencatat apa yang ditulis."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.ditulis: list[bytes] = []
        self.tertutup = False

    description = "palsu"

    async def open(self) -> None:
        pass

    async def read(self) -> bytes:
        if not self._chunks:
            return b""          # putus
        await asyncio.sleep(0)
        return self._chunks.pop(0)

    async def write(self, data: bytes) -> None:
        self.ditulis.append(data)

    async def close(self) -> None:
        self.tertutup = True


@pytest.fixture
def rec_path(tmp_path):
    return str(tmp_path / "sesi.jsonl")


class TestTblTapSession:
    def test_bisa_disimpan_dan_dibaca(self, db_session):
        s = TblTapSession(
            name="AR580 commissioning",
            transport="tcp_server",
            target="0.0.0.0:2600",
            protocol_basis="HL7",
            response_mode="uni",
            status="running",
        )
        db_session.add(s)
        db_session.commit()

        row = db_session.query(TblTapSession).one()
        assert row.name == "AR580 commissioning"
        assert row.target == "0.0.0.0:2600"

    def test_counter_default_nol(self, db_session):
        s = TblTapSession(
            name="x", transport="serial", target="/dev/ttyUSB0@9600-8N1",
            protocol_basis="RAW", response_mode="uni", status="running",
        )
        db_session.add(s)
        db_session.commit()
        row = db_session.query(TblTapSession).one()
        assert (row.bytes_rx, row.bytes_tx, row.message_count) == (0, 0, 0)

    def test_detected_protocol_boleh_kosong(self, db_session):
        s = TblTapSession(
            name="x", transport="tcp_client", target="10.0.0.5:9100",
            protocol_basis="AUTO", response_mode="uni", status="running",
        )
        db_session.add(s)
        db_session.commit()
        assert db_session.query(TblTapSession).one().detected_protocol is None


class TestTapSession:
    @pytest.mark.asyncio
    async def test_merekam_rx_dan_membalas_ack(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
        assert b"MSA|AA|1275" in t.ditulis[0]
        arah = [e["dir"] for e in read_events(rec_path)]
        assert "rx" in arah and "tx" in arah

    @pytest.mark.asyncio
    async def test_rekam_sebelum_balas(self, rec_path):
        # PENGAMAN INTI: byte harus di disk sebelum ACK dikirim. Kalau proses
        # mati setelah ACK tapi sebelum tulisan, alat tidak akan kirim ulang
        # dan hasil pasien hilang tanpa jejak.
        rx_saat_write = []

        class TransportPengintai(TransportPalsu):
            async def write(self, data: bytes) -> None:
                # Cek spesifik: event RX (byte alat) harus SUDAH ada di file saat
                # ACK hendak dikirim. Menghitung "ada event apa pun" tidak cukup —
                # event TX/ACK ditulis persis sebelum write() ini, jadi jumlah
                # total selalu >= 1 walau urutannya dibalik.
                ev = read_events(rec_path)
                rx_saat_write.append(any(e["dir"] == "rx" for e in ev))
                await super().write(data)

        t = TransportPengintai([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()

        # Saat write() pertama dipanggil, byte RX sudah tertulis ke file.
        assert rx_saat_write[0] is True, "ACK dikirim sebelum RX terekam"

    @pytest.mark.asyncio
    async def test_menghitung_byte_dan_pesan(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
        assert s.bytes_rx == len(ORU)
        assert s.bytes_tx > 0
        assert s.message_count == 1

    @pytest.mark.asyncio
    async def test_menandai_pesan_lengkap(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
        meta = [e for e in read_events(rec_path) if e["dir"] == "meta"]
        assert meta[0]["event"] == "message_complete"
        assert meta[0]["index"] == 0

    @pytest.mark.asyncio
    async def test_mode_raw_tidak_membalas(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "RAW", rec)
            await s.run()
        assert t.ditulis == []

    @pytest.mark.asyncio
    async def test_auto_mendeteksi_tapi_tetap_pasif(self, rec_path):
        # AUTO hanya MENYARANKAN — responder tidak berganti sendiri di tengah sesi.
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "AUTO", rec)
            await s.run()
        assert s.detected == "HL7"
        assert t.ditulis == []

    @pytest.mark.asyncio
    async def test_kirim_manual_terekam(self, rec_path):
        t = TransportPalsu([])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "RAW", rec)
            await s.send_manual(b"\x05")
        assert t.ditulis == [b"\x05"]
        ev = read_events(rec_path)
        assert ev[0]["dir"] == "tx" and ev[0]["note"] == "manual"

    @pytest.mark.asyncio
    async def test_hint_baud_menyala(self, rec_path):
        sampah = bytes(range(256)) * 2      # tidak membentuk frame HL7
        t = TransportPalsu([sampah])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "ASTM", rec)
            await s.run()
        assert s.baud_hint is True

    @pytest.mark.asyncio
    async def test_hint_baud_mati_saat_ada_pesan(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
        assert s.baud_hint is False

    @pytest.mark.asyncio
    async def test_stop_menghentikan_loop(self, rec_path):
        class TransportTakHabis(TransportPalsu):
            async def read(self) -> bytes:
                await asyncio.sleep(0.01)
                return b"\x00"

        t = TransportTakHabis([])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "RAW", rec)
            tugas = asyncio.create_task(s.run())
            await asyncio.sleep(0.05)
            s.stop()
            await asyncio.wait_for(tugas, timeout=2)
        assert t.tertutup is True

    @pytest.mark.asyncio
    async def test_stop_saat_alat_diam(self, rec_path):
        # KASUS NYATA yang bikin tombol Stop mati: alat sudah connect tapi tidak
        # mengirim apa pun, jadi read() menggantung selamanya. Flag _berhenti
        # hanya dicek di puncak loop — kalau read() tidak pernah balik, flag itu
        # tidak pernah terbaca. Sesi harus tetap bisa dihentikan.
        class TransportBisu(TransportPalsu):
            async def read(self) -> bytes:
                await asyncio.Event().wait()      # tidak pernah selesai

        t = TransportBisu([])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "RAW", rec)
            tugas = asyncio.create_task(s.run())
            await asyncio.sleep(0.05)
            s.stop()
            await asyncio.wait_for(tugas, timeout=2)
        assert t.tertutup is True

    @pytest.mark.asyncio
    async def test_stop_tidak_membuang_byte_yang_sudah_terbaca(self, rec_path):
        # Stop tidak boleh mengorbankan data: byte yang sudah sempat dibaca
        # harus tetap terekam sebelum loop keluar.
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()
            s.stop()
        assert s.bytes_rx == len(ORU)
        assert any(e["dir"] == "rx" for e in read_events(rec_path))

    @pytest.mark.asyncio
    async def test_bidi_menandai_query_tanpa_menjawabnya(self, rec_path):
        # bidi sengaja TIDAK mengarang jawaban: bentuk not-found beda per tipe
        # query, dan respons salah bisa membuat alat mencatat error — mengotori
        # capture yang justru sedang diamati. Yang dikejar formatnya.
        qry = VT + b"MSH|^~\\&|Mindray|BS-200E|||1||QRY^Q02|5|P|2.3.1\r" + FS + CR
        t = TransportPalsu([qry])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec, mode="bidi")
            await s.run()

        assert s.query_count == 1
        meta = [e for e in read_events(rec_path) if e.get("event") == "query_detected"]
        assert len(meta) == 1
        # Tetap di-ACK (supaya alat tidak kirim ulang), tapi tidak dijawab order.
        assert len(t.ditulis) == 1
        assert b"MSA|AA|5" in t.ditulis[0]

    @pytest.mark.asyncio
    async def test_uni_tidak_menandai_query(self, rec_path):
        qry = VT + b"MSH|^~\\&|Mindray|BS-200E|||1||QRY^Q02|5|P|2.3.1\r" + FS + CR
        t = TransportPalsu([qry])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec, mode="uni")
            await s.run()
        assert s.query_count == 0

    @pytest.mark.asyncio
    async def test_bidi_hasil_biasa_bukan_query(self, rec_path):
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec, mode="bidi")
            await s.run()
        assert s.query_count == 0

    @pytest.mark.asyncio
    async def test_on_event_dipanggil(self, rec_path):
        terlihat = []
        t = TransportPalsu([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec, on_event=terlihat.append)
            await s.run()
        assert any(e["dir"] == "rx" for e in terlihat)


class TestPortGuard:
    def test_port_bebas_lolos(self, db_session):
        check_port_free(2600, db_session)      # tidak melempar

    def test_port_dipakai_alat_aktif_ditolak(self, db_session):
        # Bukan cuma soal bind: mencegah DUA pihak meng-ACK alat yang sama.
        db_session.add(TblInstrument(
            name="AR580", ip_address="0.0.0.0", port=2600,
            protocol="HL7_ARUMA_AR580", mode="unidirectional",
            connection="server", is_active=True,
        ))
        db_session.commit()
        with pytest.raises(TapPortConflict, match="AR580"):
            check_port_free(2600, db_session)

    def test_port_dipakai_alat_nonaktif_lolos(self, db_session):
        db_session.add(TblInstrument(
            name="Lama", ip_address="0.0.0.0", port=2600,
            protocol="HL7", mode="unidirectional",
            connection="server", is_active=False,
        ))
        db_session.commit()
        check_port_free(2600, db_session)      # tidak melempar


class TestBuildTransport:
    def test_tcp_server(self):
        t = build_transport("tcp_server", host="0.0.0.0", port=2600)
        assert isinstance(t, TcpServerTransport)
        assert t.description == "tcp-server 0.0.0.0:2600"

    def test_tcp_client(self):
        t = build_transport("tcp_client", host="10.0.0.5", port=9100)
        assert isinstance(t, TcpClientTransport)

    def test_serial(self):
        t = build_transport("serial", port="/dev/ttyUSB0", baudrate=19200)
        assert isinstance(t, SerialTransport)
        assert "19200" in t.description

    def test_transport_tak_dikenal_ditolak(self):
        with pytest.raises(ValueError, match="tidak dikenali"):
            build_transport("carrier_pigeon")


class TestSessionLogPath:
    def test_path_memakai_id(self):
        assert session_log_path(42).endswith("/tap/42.jsonl")
