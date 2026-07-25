"""
services/tap/transport/base.py — Kontrak transport tapping.

Transport hanya mengurus ALIRAN byte; ia tidak tahu apa-apa soal protokol.
Pemisahan ini yang membuat 3 transport × 3 responder bisa dikombinasikan bebas
dan diuji terpisah.
"""

from abc import ABC, abstractmethod


class BaseTransport(ABC):
    """Kontrak transport: buka, baca, tulis, tutup."""

    #: True bila read() mengembalikan b"" HANYA saat koneksi putus (TCP).
    #: False bila b"" cuma berarti "belum ada data" dan loop harus lanjut
    #: (serial — port tetap terbuka meski alat sedang diam).
    is_stream: bool = True

    @property
    @abstractmethod
    def description(self) -> str:
        """Deskripsi singkat untuk tbl_tap_session.target."""
        ...

    @abstractmethod
    async def open(self) -> None:
        """Siapkan koneksi (listen / connect / buka port serial)."""
        ...

    @abstractmethod
    async def read(self) -> bytes:
        """
        Baca byte berikutnya.

        Returns:
            Byte yang terbaca, atau b"". Arti b"" ditentukan `is_stream`.
        """
        ...

    @abstractmethod
    async def write(self, data: bytes) -> None:
        """Kirim byte ke alat."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Tutup koneksi dan bebaskan resource."""
        ...
