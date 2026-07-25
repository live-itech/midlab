"""
End-to-end tapping: simulator AR580 → TapSession → capture → export.

Memakai scripts/aruma_ar580_test_sender.py sebagai fake instrument. Alat itu
sudah terbukti (dipakai commissioning driver AR580), jadi tidak perlu menulis
simulator baru.
"""

import asyncio
import os
import subprocess
import sys

import pytest

from services.tap.export import messages_from_events, rx_bytes, to_python_bytes
from services.tap.recorder import TapRecorder, read_events
from services.tap.session import TapSession
from services.tap.transport.tcp import TcpServerTransport


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SENDER = os.path.join(REPO, "scripts", "aruma_ar580_test_sender.py")


@pytest.mark.asyncio
async def test_tap_menangkap_dan_meng_ack_ar580(tmp_path):
    path = str(tmp_path / "sesi.jsonl")
    transport = TcpServerTransport("127.0.0.1", 0)
    await transport.open()
    port = transport.port

    proc = await asyncio.create_subprocess_exec(
        sys.executable, SENDER,
        "--host", "127.0.0.1", "--port", str(port), "--scenario", "result",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        cwd=REPO,
    )

    with TapRecorder(path) as rec:
        tap = TapSession(transport, "HL7", rec)
        tugas = asyncio.create_task(tap.run())
        rc = await asyncio.wait_for(proc.wait(), timeout=30)
        tap.stop()
        await asyncio.wait_for(tugas, timeout=5)

    # Simulator memvalidasi ACK-nya sendiri: exit 0 berarti MSA|AA| cocok.
    assert rc == 0, "simulator melaporkan ACK tidak sah"

    events = read_events(path)
    assert tap.message_count == 1
    assert any(e["dir"] == "tx" for e in events)

    pesan = messages_from_events(events)
    assert len(pesan) == 1
    assert b"ORU^R01" in pesan[0]
    assert b"^WBC^" in pesan[0]
    # Panel CBC+DIFF lengkap: 25 NM + 4 IS metadata.
    assert pesan[0].count(b"OBX|") == 29


@pytest.mark.asyncio
async def test_export_menghasilkan_fixture_yang_identik(tmp_path):
    # Inti nilai fitur ini: hasil tap → tempel ke test → byte-nya persis sama.
    path = str(tmp_path / "sesi.jsonl")
    transport = TcpServerTransport("127.0.0.1", 0)
    await transport.open()
    port = transport.port

    proc = await asyncio.create_subprocess_exec(
        sys.executable, SENDER,
        "--host", "127.0.0.1", "--port", str(port), "--scenario", "result",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        cwd=REPO,
    )

    with TapRecorder(path) as rec:
        tap = TapSession(transport, "HL7", rec)
        tugas = asyncio.create_task(tap.run())
        await asyncio.wait_for(proc.wait(), timeout=30)
        tap.stop()
        await asyncio.wait_for(tugas, timeout=5)

    asli = messages_from_events(read_events(path))[0]
    literal = to_python_bytes(asli)
    assert eval(literal.strip()) == asli


@pytest.mark.asyncio
async def test_bitmap_ed_besar_tetap_utuh_di_capture(tmp_path):
    # Skenario bitmap mengirim 4 × 4096 byte ED. Capture harus utuh — tidak ada
    # batas 64KB seperti tbl_result.raw_data TEXT, karena byte-nya di file.
    path = str(tmp_path / "sesi.jsonl")
    transport = TcpServerTransport("127.0.0.1", 0)
    await transport.open()
    port = transport.port

    proc = await asyncio.create_subprocess_exec(
        sys.executable, SENDER,
        "--host", "127.0.0.1", "--port", str(port), "--scenario", "bitmap",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        cwd=REPO,
    )

    with TapRecorder(path) as rec:
        tap = TapSession(transport, "HL7", rec)
        tugas = asyncio.create_task(tap.run())
        await asyncio.wait_for(proc.wait(), timeout=30)
        tap.stop()
        await asyncio.wait_for(tugas, timeout=5)

    pesan = messages_from_events(read_events(path))[0]
    assert b"DIFFScatter_BMP" in pesan
    assert len(pesan) > 16000, "bitmap terpotong di capture"
