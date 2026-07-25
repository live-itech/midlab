"""
services/tap/responder/raw.py — Responder pasif.

Untuk protokol di luar ASTM/HL7 yang dioperasikan manual oleh teknisi: MidLab
tidak pernah membalas apa pun sendiri, semua kiriman lewat tombol "kirim manual".
"""

from services.tap.responder.base import BaseResponder


class RawResponder(BaseResponder):
    """Tidak pernah membalas, tidak mengenal batas pesan."""

    NAME = "RAW"

    def feed(self, data: bytes) -> list[bytes]:
        return []

    def messages(self) -> list[bytes]:
        # RAW tidak punya framing, jadi tidak ada konsep "pesan lengkap".
        return []
