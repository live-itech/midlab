#!/usr/bin/env python3
"""
scripts/mindray_bs200e_test_sender.py — Simulator alat Mindray BS-200E.

MidLab dikonfigurasi sebagai TCP SERVER (connection='server'), jadi script ini
berperan sebagai alat: connect ke MidLab lalu bicara HL7 v2.3.1 di atas MLLP
sesuai Host Interface Manual v6.0.

Skenario yang dijalankan:
  1. RESULT  — kirim ORU^R01 (hasil sampel), harus dibalas ACK^R01
  2. QC      — kirim ORU^R01 dengan MSH-16=2, harus dibalas ACK^R01
  3. QUERY   — kirim QRY^Q02 berisi barcode:
                 - order ada    → QCK^Q02 (QAK OK) + DSR^Q03, dibalas ACK^Q03
                 - order kosong → QCK^Q02 (QAK NF), tanpa DSR

Run:
  python3 scripts/mindray_bs200e_test_sender.py                      # semua skenario
  python3 scripts/mindray_bs200e_test_sender.py --host 127.0.0.1 --port 2575
  python3 scripts/mindray_bs200e_test_sender.py --scenario result
  python3 scripts/mindray_bs200e_test_sender.py --scenario query --barcode 34567743
"""
import argparse
import socket
import sys
from datetime import datetime


VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2575
DEFAULT_BARCODE = "34567743"


def wrap(segments: list) -> bytes:
    """Bungkus segment (tiap-tiap diakhiri CR) dalam envelope MLLP."""
    body = "".join(f"{s}\r" for s in segments)
    return VT + body.encode("ascii") + FS + CR


def unwrap(raw: bytes) -> str:
    return raw.replace(VT, b"").replace(FS, b"").decode("ascii", errors="replace")


def now() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def msh(event: str, control_id: str, result_type: str = "") -> str:
    """MSH arah alat → LIS: MSH-3/4 = Manufacturer/Model."""
    return (
        f"MSH|^~\\&|Mindray|BS-200E|||{now()}||{event}|{control_id}|P|2.3.1"
        f"||||{result_type}||ASCII|||"
    )


def build_oru_sample(barcode: str, control_id: str) -> bytes:
    """ORU^R01 hasil sampel — satu pesan = satu tes (manual bab 2)."""
    return wrap([
        msh("ORU^R01", control_id, result_type="0"),
        "PID|1|854||12|Tommy||19830719145307|F|A||||||||||||||||||||||",
        f"OBR|1|{barcode}|2|Mindray^BS-200E|Y||{now()}||||||diabetes||serum|"
        f"Dr Ratna||||Dr Bagus|Interna|||||||||||||||||||||||",
        "OBX|1|NM|2|Glucose|5.400000|mmol/L|3.9-6.1|N|||F|||||||",
    ])


def build_oru_qc(control_id: str) -> bytes:
    """ORU^R01 hasil QC (MSH-16=2) — seluruh data ada di OBR, tanpa OBX."""
    return wrap([
        msh("ORU^R01", control_id, result_type="2"),
        f"OBR|1|1|test1|Mindray^BS-200E||{now()}|||||||QUAL1|1111|20080720000000||H"
        f"|5.000000|2.000000|0.11029|g/ml|||||||||||||||||||||||||||",
    ])


def build_qry(barcode: str, control_id: str) -> bytes:
    """QRY^Q02 — minta order untuk satu barcode."""
    return wrap([
        msh("QRY^Q02", control_id),
        f"QRD|{now()}|R|D|1|||RD|{barcode}|OTH|||T|",
        f"QRF|BS-200E|{now()[:8]}000000|{now()}|||RCT|COR|ALL||",
    ])


def build_ack_q03(control_id: str) -> bytes:
    """ACK^Q03 — balasan alat setelah menerima DSR."""
    return wrap([
        msh("ACK^Q03", control_id),
        f"MSA|AA|{control_id}|Message accepted|||0|",
        "ERR|0|",
    ])


def show(label: str, raw: bytes):
    print(f"\n--- {label} ({len(raw)} bytes) ---")
    for line in unwrap(raw).split("\r"):
        if line.strip():
            print(f"  {line}")


def receive(sock: socket.socket, label: str, timeout: float = 10) -> bytes:
    sock.settimeout(timeout)
    try:
        data = sock.recv(65535)
    except socket.timeout:
        print(f"\n!!! TIMEOUT menunggu {label} ({timeout}s)")
        return b""
    if not data:
        print(f"\n!!! Koneksi ditutup saat menunggu {label}")
        return b""
    show(label, data)
    return data


def scenario_result(sock: socket.socket, barcode: str) -> bool:
    msg = build_oru_sample(barcode, "1")
    show("TX ORU^R01 (hasil sampel)", msg)
    sock.sendall(msg)

    reply = receive(sock, "RX ACK^R01")
    ok = b"ACK^R01" in reply and b"MSA|AA|1|" in reply
    print(f"\n[{'OK' if ok else 'GAGAL'}] hasil sampel di-ACK oleh MidLab")
    return ok


def scenario_qc(sock: socket.socket) -> bool:
    msg = build_oru_qc("2")
    show("TX ORU^R01 (hasil QC, MSH-16=2)", msg)
    sock.sendall(msg)

    reply = receive(sock, "RX ACK^R01")
    ok = b"ACK^R01" in reply and b"MSA|AA|2|" in reply
    print(f"\n[{'OK' if ok else 'GAGAL'}] hasil QC di-ACK oleh MidLab")
    return ok


def scenario_query(sock: socket.socket, barcode: str) -> bool:
    msg = build_qry(barcode, "3")
    show(f"TX QRY^Q02 (minta order barcode {barcode})", msg)
    sock.sendall(msg)

    reply = receive(sock, "RX QCK^Q02 (+ DSR^Q03 bila order ada)")
    if not reply:
        return False

    if b"QAK|SR|NF|" in reply:
        print("\n[OK] MidLab balas QAK NF — tidak ada order untuk barcode ini.")
        print("     (buat order dulu via POST /api/orders untuk menguji jalur DSR)")
        return True

    if b"QAK|SR|OK|" not in reply or b"DSR^Q03" not in reply:
        print("\n[GAGAL] response query tidak sesuai: QCK OK + DSR^Q03 tidak lengkap")
        return False

    tests = [
        line for line in unwrap(reply).split("\r")
        if line.startswith("DSP|") and int(line.split("|")[1]) >= 29 and line.split("|")[3]
    ]
    print(f"\n[OK] MidLab balas QCK OK + DSR^Q03 dengan {len(tests)} tes:")
    for line in tests:
        print(f"     {line.split('|')[3]}")

    ack = build_ack_q03("3")
    show("TX ACK^Q03 (alat konfirmasi DSR diterima)", ack)
    sock.sendall(ack)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulator alat Mindray BS-200E")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--barcode", default=DEFAULT_BARCODE)
    ap.add_argument(
        "--scenario",
        choices=["all", "result", "qc", "query"],
        default="all",
    )
    args = ap.parse_args()

    print(f"==> Connect ke MidLab {args.host}:{args.port} sebagai Mindray BS-200E")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=10)
    except OSError as e:
        print(f"ERROR: gagal connect: {e}", file=sys.stderr)
        print("Pastikan service tcp_<id> jalan dan port sesuai tbl_instrument.",
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
