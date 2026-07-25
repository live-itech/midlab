"""
services/tap/transport/tcp.py — Transport TCP untuk tapping.

Dua arah koneksi, karena alat berbeda-beda: sebagian connect ke MidLab
(server), sebagian menunggu MidLab connect (client).

TcpServerTransport sengaja melayani SATU koneksi: satu sesi tapping = satu alat.
"""

import asyncio

from lib.utils import get_logger
from services.tap.transport.base import BaseTransport


logger = get_logger("tap_transport")

BUF = 65536


class TcpServerTransport(BaseTransport):
    """MidLab listen; alat yang connect. Melayani satu koneksi."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tersambung = asyncio.Event()

    @property
    def port(self) -> int:
        """Port efektif — berguna saat diminta port 0 (test)."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    @property
    def description(self) -> str:
        return f"tcp-server {self._host}:{self._port}"

    async def open(self) -> None:
        self._server = await asyncio.start_server(
            self._on_connect, self._host, self._port
        )
        logger.info(f"[TAP] listen di {self._host}:{self.port}")

    async def _on_connect(self, reader, writer) -> None:
        if self._reader is not None:
            # Sesi tapping = satu alat. Koneksi kedua ditolak, bukan diantre.
            logger.warning("[TAP] koneksi kedua ditolak — sesi sudah terpakai")
            writer.close()
            return
        peer = writer.get_extra_info("peername")
        logger.info(f"[TAP] alat tersambung dari {peer}")
        self._reader, self._writer = reader, writer
        self._tersambung.set()

    async def read(self) -> bytes:
        await self._tersambung.wait()
        try:
            return await self._reader.read(BUF)
        except (ConnectionError, asyncio.IncompleteReadError):
            return b""

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            return
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


class TcpClientTransport(BaseTransport):
    """MidLab yang connect ke alat."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def description(self) -> str:
        return f"tcp-client {self._host}:{self._port}"

    async def open(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )
        logger.info(f"[TAP] tersambung ke {self._host}:{self._port}")

    async def read(self) -> bytes:
        try:
            return await self._reader.read(BUF)
        except (ConnectionError, asyncio.IncompleteReadError):
            return b""

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            return
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
