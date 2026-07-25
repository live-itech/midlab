"""Test transport tapping — TCP loopback + serial lewat pty."""

import asyncio

import pytest

from services.tap.transport.tcp import TcpServerTransport, TcpClientTransport


class TestTcpServerTransport:
    @pytest.mark.asyncio
    async def test_menerima_koneksi_dan_membaca(self):
        t = TcpServerTransport("127.0.0.1", 0)
        await t.open()

        async def alat():
            r, w = await asyncio.open_connection("127.0.0.1", t.port)
            w.write(b"\x0bMSH|halo")
            await w.drain()
            w.close()

        tugas = asyncio.create_task(alat())
        data = await asyncio.wait_for(t.read(), timeout=5)
        assert data == b"\x0bMSH|halo"
        await tugas
        await t.close()

    @pytest.mark.asyncio
    async def test_menulis_balik_ke_alat(self):
        t = TcpServerTransport("127.0.0.1", 0)
        await t.open()
        diterima = []

        async def alat():
            r, w = await asyncio.open_connection("127.0.0.1", t.port)
            w.write(b"x")
            await w.drain()
            diterima.append(await r.read(10))
            w.close()

        tugas = asyncio.create_task(alat())
        await asyncio.wait_for(t.read(), timeout=5)
        await t.write(b"\x06")
        await asyncio.wait_for(tugas, timeout=5)
        assert diterima == [b"\x06"]
        await t.close()

    @pytest.mark.asyncio
    async def test_read_kosong_saat_alat_putus(self):
        t = TcpServerTransport("127.0.0.1", 0)
        await t.open()

        async def alat():
            r, w = await asyncio.open_connection("127.0.0.1", t.port)
            w.write(b"x")
            await w.drain()
            w.close()

        asyncio.create_task(alat())
        await asyncio.wait_for(t.read(), timeout=5)
        assert await asyncio.wait_for(t.read(), timeout=5) == b""
        await t.close()

    @pytest.mark.asyncio
    async def test_description(self):
        t = TcpServerTransport("0.0.0.0", 2600)
        assert t.description == "tcp-server 0.0.0.0:2600"


class TestTcpClientTransport:
    @pytest.mark.asyncio
    async def test_connect_dan_baca(self):
        pesan = b"\x05"
        server = await asyncio.start_server(
            lambda r, w: (w.write(pesan), w.close()), "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]

        t = TcpClientTransport("127.0.0.1", port)
        await t.open()
        assert await asyncio.wait_for(t.read(), timeout=5) == pesan
        await t.close()
        server.close()

    @pytest.mark.asyncio
    async def test_description(self):
        assert TcpClientTransport("10.0.0.5", 9100).description == "tcp-client 10.0.0.5:9100"
