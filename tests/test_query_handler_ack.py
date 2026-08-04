"""
Test penantian ACK pada QueryHandler saat mengirim worklist.

Sebagian alat tidak pernah mengacknowledge balasan worklist — balasan itu
sendiri sudah merupakan jawaban atas pertanyaan alat, jadi tidak ada apa pun
yang menyusul. Menunggu ACK di situ punya dua akibat: order ditandai `failed`
padahal sampai dengan selamat, dan read() yang menunggu bisa ikut menelan
pesan alat berikutnya.
"""

import asyncio

import pytest

from services.tcp_socket.query_handler import QueryHandler


class _Cfg:
    id = 1
    name = "BC-5150"
    protocol = "HL7_MINDRAY_BC5150"

    def to_dict(self):
        return {"id": self.id, "name": self.name, "protocol": self.protocol}


class _Writer:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass


class _Protocol:
    """Protocol minimal; `ack_expected` None = atribut tidak dipasang sama sekali."""

    def __init__(self, ack_expected=None):
        if ack_expected is not None:
            self.ACK_EXPECTED_ON_QUERY_RESPONSE = ack_expected

    def format_query_response(self, order_json, instrument):
        return b"\x0bMSH|worklist\r\x1c\x0d"

    def handle_ack(self, raw):
        return "ACK"


def _handler(protocol):
    handler = QueryHandler(_Cfg(), protocol, None, _Writer(), asyncio.Lock())
    handler._ack_waits = 0

    async def _wait_for_ack(timeout=15):
        handler._ack_waits += 1
        return "TIMEOUT"          # alat memang tidak membalas

    handler._wait_for_ack = _wait_for_ack
    return handler


def test_worklist_tidak_menunggu_ack_bila_alat_tidak_membalas():
    handler = _handler(_Protocol(ack_expected=False))

    success = asyncio.run(handler._send_query_response({}, _Cfg().to_dict()))

    assert success is True         # terkirim = berhasil; tidak ada ACK untuk ditunggu
    assert handler._ack_waits == 0
    assert handler._writer.sent == [b"\x0bMSH|worklist\r\x1c\x0d"]


def test_worklist_tetap_menunggu_ack_secara_default():
    """
    Protokol yang alatnya memang mengacknowledge worklist tidak boleh ikut
    berubah: tanpa atribut, perilakunya harus persis seperti sebelumnya.
    """
    handler = _handler(_Protocol())

    success = asyncio.run(handler._send_query_response({}, _Cfg().to_dict()))

    assert success is False        # ACK ditunggu, tidak datang → gagal
    assert handler._ack_waits == 1


@pytest.mark.parametrize("ack_expected", [True, False])
def test_kegagalan_kirim_tetap_terdeteksi(ack_expected):
    """
    Melewati ACK bukan berarti selalu sukses — koneksi putus saat menulis
    tetap harus dilaporkan gagal supaya order ditandai `failed`.
    """
    handler = _handler(_Protocol(ack_expected=ack_expected))

    def _boom(data):
        raise ConnectionResetError("koneksi putus")

    handler._writer.write = _boom

    assert asyncio.run(handler._send_query_response({}, _Cfg().to_dict())) is False
