"""
services/tap/responder/base.py — Kontrak responder tapping.

Responder menjawab handshake di level TRANSPORT, bukan parsing isi. Inilah yang
membuat tapping bisa jalan tanpa driver: ASTM ACK selalu 0x06, HL7 ACK cuma perlu
memantulkan MSH-10. Nol pengetahuan tentang isi pesan.

feed() sengaja sinkron dan bebas I/O — state machine ada di dalamnya, tapi tidak
menyentuh socket, jadi seluruh perilaku responder bisa diuji tanpa jaringan.
"""

from abc import ABC, abstractmethod


class BaseResponder(ABC):
    """Kontrak responder: byte masuk → byte yang harus dibalas."""

    @property
    @abstractmethod
    def NAME(self) -> str:
        """Nama basis protokol: 'ASTM', 'HL7', 'RAW'."""
        ...

    @abstractmethod
    def feed(self, data: bytes) -> list[bytes]:
        """
        Umpankan byte yang diterima dari alat.

        Returns:
            Daftar byte yang harus dikirim balik ke alat. Boleh kosong.
        """
        ...

    @abstractmethod
    def messages(self) -> list[bytes]:
        """Pesan lengkap yang sudah terdeteksi sejauh ini (untuk export per-pesan)."""
        ...
