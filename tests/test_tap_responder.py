"""Test responder tapping — semuanya tanpa socket (feed() bebas I/O)."""

import pytest

from services.tap.responder.raw import RawResponder
from services.tap.responder.astm import AstmResponder

ENQ, ACK, EOT, STX, ETX = b"\x05", b"\x06", b"\x04", b"\x02", b"\x03"


class TestRawResponder:
    def test_tidak_pernah_membalas(self):
        r = RawResponder()
        assert r.feed(b"\x05") == []
        assert r.feed(b"MSH|^~\\&|X|Y\r") == []
        assert r.feed(b"apa pun") == []

    def test_tidak_mendeteksi_pesan(self):
        # RAW tidak punya konsep batas pesan.
        r = RawResponder()
        r.feed(b"\x0bMSH|abc\r\x1c\x0d")
        assert r.messages() == []

    def test_punya_nama(self):
        assert RawResponder().NAME == "RAW"


class TestAstmResponder:
    def test_enq_dibalas_ack(self):
        r = AstmResponder()
        assert r.feed(ENQ) == [ACK]

    def test_frame_dibalas_ack(self):
        r = AstmResponder()
        r.feed(ENQ)
        frame = STX + b"1H|\\^&|||Sysmex" + ETX + b"AB\r\n"
        assert r.feed(frame) == [ACK]

    def test_eot_tidak_dibalas(self):
        # EOT mengakhiri sesi; tidak ada balasan.
        r = AstmResponder()
        r.feed(ENQ)
        assert r.feed(EOT) == []

    def test_sesi_lengkap_jadi_satu_pesan(self):
        r = AstmResponder()
        r.feed(ENQ)
        r.feed(STX + b"1H|\\^&|||Sysmex" + ETX + b"AB\r\n")
        r.feed(STX + b"2L|1|N" + ETX + b"CD\r\n")
        r.feed(EOT)
        pesan = r.messages()
        assert len(pesan) == 1
        assert b"Sysmex" in pesan[0]
        assert b"L|1|N" in pesan[0]

    def test_pesan_hanya_muncul_setelah_eot(self):
        r = AstmResponder()
        r.feed(ENQ)
        r.feed(STX + b"1H|\\^&" + ETX + b"AB\r\n")
        assert r.messages() == []  # sesi belum ditutup

    def test_dua_sesi_jadi_dua_pesan(self):
        r = AstmResponder()
        for tag in (b"A", b"B"):
            r.feed(ENQ)
            r.feed(STX + b"1H|" + tag + ETX + b"XX\r\n")
            r.feed(EOT)
        assert len(r.messages()) == 2

    def test_byte_terpecah_antar_chunk(self):
        # TCP tidak menjamin batas frame — satu frame bisa datang terpotong.
        r = AstmResponder()
        r.feed(ENQ)
        assert r.feed(STX + b"1H|\\^&|||Sys") == []   # belum lengkap
        assert r.feed(b"mex" + ETX + b"AB\r\n") == [ACK]

    def test_dua_frame_dalam_satu_chunk(self):
        r = AstmResponder()
        r.feed(ENQ)
        dua = (STX + b"1H|" + ETX + b"AB\r\n") + (STX + b"2L|" + ETX + b"CD\r\n")
        assert r.feed(dua) == [ACK, ACK]

    def test_enq_tanpa_sesi_sebelumnya_tetap_diack(self):
        r = AstmResponder()
        assert r.feed(ENQ) == [ACK]
        assert r.feed(ENQ) == [ACK]

    def test_nama(self):
        assert AstmResponder().NAME == "ASTM"
