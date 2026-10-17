#!/usr/bin/env python3
"""
scripts/boditech_afias1_test_sender.py — Simulator alat Boditech AFIAS-1.

Kirim satu OUL^R24 persis contoh slide 5 manual LIS AFIAS-1 (ID sampel bisa
diganti) lalu tampilkan balasan MidLab. Dua mode koneksi, sesuai
tbl_instrument.connection di MidLab:

  --mode connect  MidLab = server (connection='server'): simulator konek ke
                  MidLab, seperti alat di mode LIS Network
  --mode listen   MidLab = client (connection='client'): simulator membuka
                  port dan menunggu MidLab konek, seperti converter
                  RS232-to-LAN yang diset TCP server

  --raw   kirim tanpa envelope MLLP — MidLab harus menulis WARNING
          "Data HL7 tanpa MLLP <VT> dibuang" di tcp_<id>.log dan tidak
          menyimpan hasil

Run:
  python3 scripts/boditech_afias1_test_sender.py --port 5200
  python3 scripts/boditech_afias1_test_sender.py --mode listen --host 0.0.0.0 --port 5200
  python3 scripts/boditech_afias1_test_sender.py --port 5200 --sample-id BC-001
  python3 scripts/boditech_afias1_test_sender.py --port 5200 --raw
"""
import argparse
import socket
import sys
from datetime import datetime


VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5200
DEFAULT_SAMPLE_ID = "T45"


def build_oul(sample_id: str) -> str:
    now = datetime.now().strftime("%Y%m%d%H%M%S") + ".000+0700"
    segments = [
        f"MSH|^~\\&||1|AFIAS-1|A60123456789|{now}||OUL^R24^OUL_R24|1|T|2.6",
        f"PID||{sample_id}|{sample_id}|||||",
        "OBR||T4|G||||||admin",
        "ORC|OK||||||||||||||||A60123456789|5",
        "SPM||T4PKA37F||||||||||||||||20210719",
        "OBX|1|TX|T4||33.26|nmol/L|||||R",
    ]
    return "".join(s + "\r" for s in segments)


def show(label: str, raw: bytes):
    print(f"\n--- {label} ({len(raw)} bytes) ---")
    text = raw.replace(VT, b"").replace(FS, b"").decode("ascii", errors="replace")
    for line in text.split("\r"):
        if line.strip():
            print(f"  {line}")


def open_socket(args) -> socket.socket:
    if args.mode == "connect":
        print(f"==> Connect ke MidLab {args.host}:{args.port} sebagai AFIAS-1")
        return socket.create_connection((args.host, args.port), timeout=10)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(1)
    server.settimeout(60)
    print(f"==> Listen di {args.host}:{args.port}, menunggu MidLab konek (60s)...")
    try:
        conn, addr = server.accept()
    finally:
        server.close()
    print(f"==> MidLab konek dari {addr}")
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulator alat Boditech AFIAS-1")
    ap.add_argument("--mode", choices=["connect", "listen"], default="connect")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="connect: alamat MidLab; listen: alamat bind (0.0.0.0 untuk semua)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--sample-id", default=DEFAULT_SAMPLE_ID)
    ap.add_argument("--raw", action="store_true", help="kirim tanpa envelope MLLP")
    args = ap.parse_args()

    body = build_oul(args.sample_id).encode("ascii")
    msg = body if args.raw else VT + body + FS + CR

    try:
        sock = open_socket(args)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Pastikan service tcp_<id> jalan dan port/connection sesuai tbl_instrument.",
              file=sys.stderr)
        return 1

    try:
        show("TX OUL^R24" + (" (tanpa MLLP)" if args.raw else ""), msg)
        sock.sendall(msg)
        sock.settimeout(10)
        data = b""
        try:
            while FS + CR not in data:
                chunk = sock.recv(65535)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
    finally:
        sock.close()

    if args.raw:
        if data:
            show("RX (tak terduga)", data)
        print("\n==> Mode --raw: periksa WARNING 'tanpa MLLP' di /var/log/midlab/tcp_<id>.log")
        return 0

    if not data:
        print("\n!!! Tidak ada balasan dalam 10 detik")
        return 1
    show("RX", data)
    ok = b"ACK^R24^ACK" in data and b"MSA|AA|1" in data
    print(f"\n==> ACK {'OK' if ok else 'TIDAK SESUAI'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
