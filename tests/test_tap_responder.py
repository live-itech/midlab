"""Test responder tapping — semuanya tanpa socket (feed() bebas I/O)."""

import pytest

from services.tap.responder.raw import RawResponder


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
