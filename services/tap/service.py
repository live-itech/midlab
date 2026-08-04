#!/usr/bin/env python3
"""
services/tap/service.py — TapService: orkestrasi sesi tapping.

Menangkap komunikasi alat yang BELUM punya driver — langkah pertama SOP
tap → analisis → tulis driver → test.

Data yang di-tap TIDAK masuk tbl_result dan TIDAK dikirim ke LIS. Yang tersimpan
hanya capture mentahnya di /var/log/midlab/tap/<id>.jsonl.

Usage:
    python3 -m services.tap.service --name "AR580" --transport tcp_server \\
        --port 2600 --basis HL7
    python3 -m services.tap.service --name "Sysmex" --transport serial \\
        --serial-port /dev/ttyUSB0 --baudrate 9600 --basis AUTO
"""

import argparse
import asyncio
import os
import signal
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(
    os.path.dirname(__file__)
))))

from lib.db import DBManager, TblInstrument, TblTapSession
from lib.timeutil import now_naive
from lib.utils import get_logger
from services.tap.recorder import TAP_LOG_DIR, TapRecorder
from services.tap.session import TapSession
from services.tap.transport.base import BaseTransport
from services.tap.transport.serial_port import SerialTransport
from services.tap.transport.tcp import TcpClientTransport, TcpServerTransport


logger = get_logger("tap_service")


class TapPortConflict(Exception):
    """Port sudah dipakai alat aktif — tapping ditolak."""


def check_port_free(port: int, session) -> None:
    """
    Pastikan tidak ada alat aktif yang memakai port ini.

    Bukan sekadar soal bind: kalau service TCP alat itu sedang jalan, dua pihak
    akan meng-ACK alat yang sama, dan hasil yang di-ACK oleh tap TIDAK masuk
    tbl_result — hasil pasien bisa hilang diam-diam.
    """
    row = (
        session.query(TblInstrument)
        .filter(TblInstrument.port == port, TblInstrument.is_active.is_(True))
        .first()
    )
    if row is not None:
        raise TapPortConflict(
            f"Port {port} dipakai alat aktif '{row.name}' (id={row.id}). "
            f"Matikan service TCP-nya dulu, atau pakai port lain — dua pihak "
            f"tidak boleh meng-ACK alat yang sama."
        )


def build_transport(transport: str, **params) -> BaseTransport:
    """Buat transport sesuai jenisnya."""
    if transport == "tcp_server":
        return TcpServerTransport(params["host"], params["port"])
    if transport == "tcp_client":
        return TcpClientTransport(params["host"], params["port"])
    if transport == "serial":
        return SerialTransport(
            params["port"],
            baudrate=params.get("baudrate", 9600),
            bytesize=params.get("bytesize", 8),
            parity=params.get("parity", "N"),
            stopbits=params.get("stopbits", 1),
            xonxoff=params.get("xonxoff", False),
            rtscts=params.get("rtscts", False),
        )
    raise ValueError(
        f"Transport '{transport}' tidak dikenali. "
        f"Tersedia: tcp_server, tcp_client, serial"
    )


def transport_from_row(row) -> BaseTransport:
    """
    Bangun ulang transport dari metadata baris sesi — untuk MELANJUTKAN tapping.

    Konfig tidak disimpan kolom per kolom; yang ada hanya `target`, dan itu
    ditulis oleh transport.description sendiri (mis. "tcp-server 0.0.0.0:2600",
    "serial /dev/ttyUSB0@9600-8N1"). Prefiksnya opsional: jenis transport diambil
    dari kolom `transport`, bukan dari tebakan string.
    """
    target = (row.target or "").strip()
    for prefix in ("tcp-server ", "tcp-client ", "serial "):
        if target.startswith(prefix):
            target = target[len(prefix):].strip()
            break

    if row.transport in ("tcp_server", "tcp_client"):
        host, _, port = target.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(
                f"Target '{row.target}' tidak bisa dibaca sebagai host:port — "
                f"sesi ini tidak bisa dilanjutkan, buat sesi baru."
            )
        return build_transport(row.transport, host=host, port=int(port))

    if row.transport == "serial":
        dev, _, frame = target.partition("@")
        if not dev:
            raise ValueError(
                f"Target '{row.target}' tidak memuat nama port serial — "
                f"sesi ini tidak bisa dilanjutkan, buat sesi baru."
            )
        # frame: "9600-8N1"; bagian setelah '-' boleh tidak ada (default 8N1).
        baud, _, bits = frame.partition("-")
        if not baud.isdigit():
            raise ValueError(
                f"Target '{row.target}' tidak memuat baudrate yang sah — "
                f"sesi ini tidak bisa dilanjutkan, buat sesi baru."
            )
        bytesize, parity, stopbits = 8, "N", 1
        if len(bits) == 3:
            bytesize, parity, stopbits = int(bits[0]), bits[1], int(bits[2])
        return build_transport(
            "serial", port=dev, baudrate=int(baud),
            bytesize=bytesize, parity=parity, stopbits=stopbits,
        )

    raise ValueError(f"Transport '{row.transport}' tidak dikenali")


def session_log_path(session_id: int) -> str:
    """Lokasi file JSONL untuk satu sesi."""
    return os.path.join(TAP_LOG_DIR, f"{session_id}.jsonl")


async def run_session(row_id: int, transport: BaseTransport, basis: str,
                      mode: str) -> None:
    """Jalankan satu sesi tapping sampai selesai, lalu update metadata."""
    path = session_log_path(row_id)
    await transport.open()

    with TapRecorder(path) as rec:
        tap = TapSession(transport, basis, rec, mode=mode)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, tap.stop)

        try:
            await tap.run()
            status, err = "stopped", None
        except Exception as e:
            logger.error(f"[TAP] sesi {row_id} gagal: {e}")
            status, err = "error", str(e)
        finally:
            _simpan_hasil(row_id, tap, status, err)


def _simpan_hasil(row_id: int, tap: TapSession | None, status: str,
                  err: str | None, baseline: dict | None = None) -> None:
    """
    Tutup baris sesi dan simpan hitungannya.

    `baseline` = hitungan sesi sebelumnya saat tapping dilanjutkan; counter
    TapSession selalu mulai dari nol tiap kali dijalankan, jadi tanpa ini angka
    di UI mundur setiap kali sesi di-start ulang.

    `tap` boleh None: kalau transport.open() sendiri yang gagal, sesi belum
    sempat ada. Barisnya tetap harus ditutup — kalau tidak ia menyangkut
    'running' selamanya — tapi counter lamanya jangan ditimpa nol.
    """
    base = baseline or {}
    session = DBManager().get_session()
    try:
        row = session.get(TblTapSession, row_id)
        if row is not None:
            row.status = status
            row.error_message = err
            if tap is not None:
                row.bytes_rx = base.get("bytes_rx", 0) + tap.bytes_rx
                row.bytes_tx = base.get("bytes_tx", 0) + tap.bytes_tx
                row.message_count = base.get("message_count", 0) + tap.message_count
                row.detected_protocol = tap.detected or row.detected_protocol
            row.stopped_at = now_naive()
            session.commit()
    except Exception as e:
        logger.error(f"[TAP] gagal menyimpan hasil sesi {row_id}: {e}")
        session.rollback()
    finally:
        session.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="TapService — capture alat lab")
    ap.add_argument("--name", required=True)
    ap.add_argument("--transport", required=True,
                    choices=["tcp_server", "tcp_client", "serial"])
    ap.add_argument("--basis", required=True, choices=["ASTM", "HL7", "RAW", "AUTO"])
    ap.add_argument("--mode", default="uni", choices=["uni", "bidi"])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, help="port TCP")
    ap.add_argument("--serial-port", help="mis. /dev/ttyUSB0")
    ap.add_argument("--baudrate", type=int, default=9600)
    ap.add_argument("--parity", default="N", choices=["N", "E", "O"])
    args = ap.parse_args()

    session = DBManager().get_session()
    try:
        if args.transport in ("tcp_server", "tcp_client"):
            if args.port is None:
                print("ERROR: --port wajib untuk transport TCP", file=sys.stderr)
                return 2
            if args.transport == "tcp_server":
                check_port_free(args.port, session)
            transport = build_transport(
                args.transport, host=args.host, port=args.port
            )
        else:
            if not args.serial_port:
                print("ERROR: --serial-port wajib untuk transport serial",
                      file=sys.stderr)
                return 2
            transport = build_transport(
                "serial", port=args.serial_port,
                baudrate=args.baudrate, parity=args.parity,
            )

        row = TblTapSession(
            name=args.name, transport=args.transport,
            target=transport.description, protocol_basis=args.basis,
            response_mode=args.mode, status="running",
            started_at=now_naive(),
        )
        session.add(row)
        session.commit()
        row_id = row.id
    except TapPortConflict as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()

    print(f"Sesi tap #{row_id} — {transport.description}")
    print(f"Capture: {session_log_path(row_id)}")
    print("PERINGATAN: data tap TIDAK masuk tbl_result dan TIDAK dikirim ke LIS.")

    asyncio.run(run_session(row_id, transport, args.basis, args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
