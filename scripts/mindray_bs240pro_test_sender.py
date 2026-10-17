#!/usr/bin/env python3
"""
scripts/mindray_bs240pro_test_sender.py — Simulator alat Mindray BS-240Pro / BS-360E / BS-240E.

Bicara HL7 v2.3.1 di atas MLLP persis seperti BS-360E di log lapangan
(MSH-3/4 kosong, QRF-1 kosong). Dua mode koneksi, sesuai
tbl_instrument.connection di MidLab:

  --mode connect  MidLab = server (connection='server'): simulator konek ke MidLab
  --mode listen   MidLab = client (connection='client'): simulator membuka port
                  dan menunggu MidLab konek

Skenario:
  1. RESULT — ORU^R01 hasil pasien, harus dibalas ACK^R01
  2. QC     — ORU^R01 MSH-16=2 dengan DUA kontrol (L1/L2) dalam satu pesan,
              harus dibalas ACK^R01; MidLab harus menyimpan 2 baris tbl_result
  3. QUERY  — QRY^Q02 berisi barcode; order ada → QCK^Q02 OK + DSR^Q03
              (simulator membalas ACK^Q03), order tidak ada → QCK^Q02 NF

Run:
  python3 scripts/mindray_bs240pro_test_sender.py --port 2575
  python3 scripts/mindray_bs240pro_test_sender.py --mode listen --port 5100
  python3 scripts/mindray_bs240pro_test_sender.py --scenario query --barcode 241000223710
"""
import argparse
import socket
import sys
from datetime import datetime


VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2575
DEFAULT_BARCODE = "241000223710"


def wrap(segments: list) -> bytes:
    """Bungkus segment (tiap-tiap diakhiri CR) dalam envelope MLLP."""
    body = "".join(f"{s}\r" for s in segments)
    return VT + body.encode("ascii") + FS + CR


def unwrap(raw: bytes) -> str:
    return raw.replace(VT, b"").replace(FS, b"").decode("ascii", errors="replace")


def now() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def msh(event: str, control_id: str, result_type: str = "") -> str:
    """MSH arah alat → LIS; MSH-3/4 kosong seperti BS-360E di lapangan."""
    return (
        f"MSH|^~\\&|||||{now()}||{event}|{control_id}|P|2.3.1"
        f"||||{result_type}||ASCII|||"
    )


def build_oru_sample(barcode: str, control_id: str) -> bytes:
    return wrap([
        msh("ORU^R01", control_id, result_type="0"),
        "PID|1||||PASIEN SIMULATOR|||O|||||||||||||||||||||||",
        f"OBR|1|{barcode}|1|^|N|{now()}|{now()}|{now()}||1^1||||{now()}|Serum"
        f"|||||||||||||||||||||||||||||||||",
        f"OBX|1|NM|GLU|Glucose (GOD-POD Method)|105.805265|mg/dL|-|N|||F||105.805265|{now()}|||0||",
        f"OBX|2|NM|UREA|Urea|23.484954|mg/dL|-|N|||F||23.484954|{now()}|||0||",
    ])


def build_oru_qc(control_id: str) -> bytes:
    """QC GLU dua kontrol dalam satu OBR (field 12-15, 17-20 = V1^V2)."""
    return wrap([
        msh("ORU^R01", control_id, result_type="2"),
        f"OBR|1|GLU|Glu-G|^|Serum|{now()}|{now()}|{now()}|||2|8^9|CTRL L1^CTRL L2"
        f"|059324003^059424001|20261231^20261231||M^M|100.000000^250.000000"
        f"|10.000000^20.000000|93.389457^241.500000|mg/dL|||||||RGTE"
        f"||||||||||||||||||||",
    ])


def build_qry(barcode: str, control_id: str) -> bytes:
    return wrap([
        msh("QRY^Q02", control_id),
        f"QRD|{now()}|R|D|{control_id}|||RD|{barcode}|OTH|||T|",
        "QRF||||||RCT|COR|ALL||",
    ])


def build_ack_q03(control_id: str) -> bytes:
    return wrap([
        msh("ACK^Q03", control_id),
        f"MSA|AA|{control_id}|Message accepted|||0|",
    ])


def show(label: str, raw: bytes):
    print(f"\n--- {label} ({len(raw)} bytes) ---")
    for line in unwrap(raw).split("\r"):
        if line.strip():
            print(f"  {line}")


def receive(sock: socket.socket, label: str, timeout: float = 10) -> bytes:
    """Baca sampai minimal satu pesan MLLP utuh (FS CR) tiba."""
    sock.settimeout(timeout)
    data = b""
    try:
        while FS + CR not in data:
            chunk = sock.recv(65535)
            if not chunk:
                print(f"\n!!! Koneksi ditutup saat menunggu {label}")
                return data
            data += chunk
    except socket.timeout:
        print(f"\n!!! TIMEOUT menunggu {label} ({timeout}s)")
        return data
    show(label, data)
    return data


def scenario_result(sock: socket.socket, barcode: str) -> bool:
    msg = build_oru_sample(barcode, "1")
    show("TX ORU^R01 (hasil pasien)", msg)
    sock.sendall(msg)
    return b"MSA|AA|1|" in receive(sock, "RX ACK^R01")


def scenario_qc(sock: socket.socket) -> bool:
    msg = build_oru_qc("2")
    show("TX ORU^R01 (QC dua kontrol)", msg)
    sock.sendall(msg)
    ok = b"MSA|AA|2|" in receive(sock, "RX ACK^R01")
    print("  → cek Result Monitor: harus ada 2 baris QC (lot 059324003 L1, 059424001 L2)")
    return ok


def scenario_query(sock: socket.socket, barcode: str) -> bool:
    msg = build_qry(barcode, "3")
    show(f"TX QRY^Q02 (barcode {barcode})", msg)
    sock.sendall(msg)
    data = receive(sock, "RX QCK^Q02 (+DSR^Q03)")
    if b"QAK|SR|NF|" in data:
        print("  → order tidak ada di MidLab (QAK NF) — buat order pending dulu untuk uji DSR")
        return True
    if b"DSR^Q03" not in data:
        data += receive(sock, "RX DSR^Q03")
    if b"DSR^Q03" not in data:
        return False
    sock.sendall(build_ack_q03("3"))
    print("  → ACK^Q03 dikirim; order harus berstatus sent di Order Monitor")
    return True


def open_socket(args) -> socket.socket:
    if args.mode == "connect":
        print(f"==> Connect ke MidLab {args.host}:{args.port} sebagai BS-240Pro")
        return socket.create_connection((args.host, args.port), timeout=10)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(1)
    print(f"==> Listen di {args.host}:{args.port}, menunggu MidLab konek (60s)...")
    server.settimeout(60)
    try:
        conn, addr = server.accept()
    finally:
        server.close()
    print(f"==> MidLab konek dari {addr}")
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulator alat Mindray BS-240Pro")
    ap.add_argument("--mode", choices=["connect", "listen"], default="connect")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="connect: alamat MidLab; listen: alamat bind (0.0.0.0 untuk semua)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--barcode", default=DEFAULT_BARCODE)
    ap.add_argument("--scenario", choices=["all", "result", "qc", "query"], default="all")
    args = ap.parse_args()

    try:
        sock = open_socket(args)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Pastikan service tcp_<id> jalan dan port/mode sesuai tbl_instrument.",
              file=sys.stderr)
        return 1

    hasil = []
    try:
        if args.scenario in ("all", "result"):
            hasil.append(("result", scenario_result(sock, args.barcode)))
        if args.scenario in ("all", "qc"):
            hasil.append(("qc", scenario_qc(sock)))
        if args.scenario in ("all", "query"):
            hasil.append(("query", scenario_query(sock, args.barcode)))
    finally:
        sock.close()

    print("\n" + "=" * 46)
    for nama, ok in hasil:
        print(f"  {nama:8s} : {'OK' if ok else 'GAGAL'}")
    print("=" * 46)
    return 0 if all(ok for _, ok in hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
