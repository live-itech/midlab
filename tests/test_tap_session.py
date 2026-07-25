"""Test TapSession + model tbl_tap_session."""

import asyncio

import pytest

from lib.db import TblTapSession
from services.tap.recorder import TapRecorder, read_events
from services.tap.session import TapSession
from services.tap.transport.base import BaseTransport


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
        urutan = []

        class TransportPengintai(TransportPalsu):
            async def write(self, data: bytes) -> None:
                urutan.append(("write", len(read_events(rec_path))))
                await super().write(data)

        t = TransportPengintai([ORU])
        with TapRecorder(rec_path) as rec:
            s = TapSession(t, "HL7", rec)
            await s.run()

        # Saat write() dipanggil, event RX sudah tertulis ke file.
        assert urutan[0][1] >= 1, "ACK dikirim sebelum RX terekam"

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
