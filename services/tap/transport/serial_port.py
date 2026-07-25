"""
services/tap/transport/serial_port.py — Transport serial (RS232) untuk tapping.

Dipakai saat develop driver di laptop, sebelum alat dipindah ke topologi produksi
(alat → converter serial-to-TCP → server). Tapping di server selalu TCP.

pyserial bersifat blocking, jadi read/write dibungkus asyncio.to_thread. Port
dibuka dengan timeout pendek supaya read() cepat kembali dengan apa pun yang ada
— b"" berarti "belum ada data", bukan "putus".
"""

import asyncio

import serial

from lib.utils import get_logger
from services.tap.transport.base import BaseTransport


logger = get_logger("tap_transport")

BUF = 4096
READ_TIMEOUT = 0.2      # detik — cukup pendek agar loop tetap responsif


class SerialTransport(BaseTransport):
    """Baca/tulis port serial lewat pyserial."""

    # Port serial tetap terbuka meski alat diam — b"" berarti "belum ada data",
    # bukan "putus". Loop tidak boleh berhenti karenanya.
    is_stream = False

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
        xonxoff: bool = False,
        rtscts: bool = False,
    ):
        self._port = port
        self._baudrate = baudrate
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._xonxoff = xonxoff
        self._rtscts = rtscts
        self._ser: serial.Serial | None = None

    @property
    def description(self) -> str:
        return (
            f"serial {self._port}@{self._baudrate}-"
            f"{self._bytesize}{self._parity}{self._stopbits}"
        )

    async def open(self) -> None:
        try:
            self._ser = await asyncio.to_thread(
                serial.Serial,
                port=self._port,
                baudrate=self._baudrate,
                bytesize=self._bytesize,
                parity=self._parity,
                stopbits=self._stopbits,
                xonxoff=self._xonxoff,
                rtscts=self._rtscts,
                timeout=READ_TIMEOUT,
            )
        except (serial.SerialException, OSError) as e:
            # Kegagalan paling sering bukan kabel, tapi izin: user harus anggota
            # grup dialout. Sebutkan itu, jangan lempar stack trace mentah.
            raise OSError(
                f"Gagal membuka {self._port}: {e}. "
                f"Bila ini soal izin, pastikan user tergabung di grup 'dialout' "
                f"(sudo usermod -aG dialout $USER, lalu login ulang)."
            ) from e
        logger.info(f"[TAP] serial terbuka: {self.description}")

    async def read(self) -> bytes:
        if self._ser is None:
            return b""
        return await asyncio.to_thread(self._ser.read, BUF)

    async def write(self, data: bytes) -> None:
        if self._ser is None:
            return
        await asyncio.to_thread(self._ser.write, data)

    async def close(self) -> None:
        if self._ser is not None:
            await asyncio.to_thread(self._ser.close)
