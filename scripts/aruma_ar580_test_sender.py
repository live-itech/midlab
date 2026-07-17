#!/usr/bin/env python3
"""
scripts/aruma_ar580_test_sender.py — Simulator alat ARUMA AR580.

MidLab dikonfigurasi sebagai TCP SERVER (connection='server'), jadi script ini
berperan sebagai alat: connect ke MidLab lalu kirim ORU^R01 HL7 v2.3.1 di atas
MLLP sesuai "LIS communication protocol instruction" (Genrui) bab 2.3.1.

AR580 adalah rebrand OEM Genrui KT-6610 dan hanya unidirectional: alat kirim
hasil, LIS balas ACK^R01. Tidak ada query/broadcast — ORM^O01 tidak
dispesifikasi dokumen.

Skenario:
  1. RESULT — kirim ORU^R01 CBC+DIFF lengkap (25 parameter), harus dibalas
              ACK^R01 dengan MSA|AA| yang memantulkan MSH-10
  2. BITMAP — ORU^R01 + 4 OBX bertipe ED (histogram/scattergram); MidLab harus
              tetap balas ACK dan melewati bitmapnya
  3. NOACK  — kirim ORU^R01 lalu tunggu tanpa membalas; dipakai memeriksa
              perilaku MidLab saat alat tidak menerima ACK (bab 2.3.1: alat
              kirim ulang dalam 3 detik)

Run:
  python3 scripts/aruma_ar580_test_sender.py
  python3 scripts/aruma_ar580_test_sender.py --host 127.0.0.1 --port 2576
  python3 scripts/aruma_ar580_test_sender.py --scenario result --sample 0706-ZY-190-11
  python3 scripts/aruma_ar580_test_sender.py --scenario bitmap
"""
import argparse
import socket
import sys
from datetime import datetime


VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2576
DEFAULT_SAMPLE = "0706-ZY-190-11"

# Panel CBC+DIFF dokumen bab 2.3.1: (kode, nilai, unit, rentang, flag)
PANEL = [
    ("WBC",    "6.55",  "10^9/L",  "4.00-10.00",  ""),
    ("Neu#",   "3.20",  "10^9/L",  "2.00-7.00",   ""),
    ("Lym#",   "2.10",  "10^9/L",  "0.80-4.00",   ""),
    ("Mon#",   "0.45",  "10^9/L",  "0.12-1.20",   ""),
    ("Eos#",   "0.15",  "10^9/L",  "0.02-0.50",   ""),
    ("Bas#",   "0.05",  "10^9/L",  "0.00-0.10",   ""),
    ("Neu%",   "48.9",  "%",       "50.0-70.0",   "L"),
    ("Lym%",   "32.1",  "%",       "20.0-40.0",   ""),
    ("Mon%",   "6.9",   "%",       "3.0-12.0",    ""),
    ("Eos%",   "2.3",   "%",       "0.5-5.0",     ""),
    ("Bas%",   "0.8",   "%",       "0.0-1.0",     ""),
    ("RBC",    "4.62",  "10^12/L", "3.50-5.50",   ""),
    ("HGB",    "138",   "g/L",     "120-160",     ""),
    ("HCT",    "41.2",  "%",       "40.0-54.0",   ""),
    ("MCV",    "89.2",  "fL",      "80.0-100.0",  ""),
    ("MCH",    "29.9",  "pg",      "27.0-34.0",   ""),
    ("MCHC",   "335",   "g/L",     "320-360",     ""),
    ("RDW-CV", "12.8",  "%",       "11.0-16.0",   ""),
    ("RDW-SD", "41.3",  "fL",      "35.0-56.0",   ""),
    ("PLT",    "256",   "10^9/L",  "100-300",     ""),
    ("MPV",    "9.8",   "fL",      "6.5-12.0",    ""),
    ("PDW",    "15.9",  "",        "9.0-17.0",    ""),
    ("PCT",    "0.251", "%",       "0.108-0.282", ""),
    ("P-LCC",  "62",    "10^9/L",  "30-90",       ""),
    ("P-LCR",  "24.2",  "%",       "11.0-45.0",   ""),
]

BITMAPS = [
    "DIFFScatter_BMP", "WBCScatter_BMP", "RBCHistogram_BMP", "PLTHistogram_BMP",
]


def wrap(segments: list) -> bytes:
    """Bungkus segment (tiap-tiap diakhiri CR) dalam envelope MLLP."""
    body = "".join(f"{s}\r" for s in segments)
    return VT + body.encode("utf-8") + FS + CR


def unwrap(raw: bytes) -> str:
    return raw.replace(VT, b"").replace(FS, b"").decode("utf-8", errors="replace")


def now() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def build_oru(sample_id: str, control_id: str, with_bitmap: bool = False) -> bytes:
    """ORU^R01 arah alat → LIS, sesuai contoh dokumen bab 2.3.1."""
    segments = [
        f"MSH|^~\\&|Genrui|KT-6610|||{now()}||ORU^R01|{control_id}|P|2.3.1"
        f"|||||CHA|UTF-8|||",
        f"PID|1||{sample_id}||&Pasien Uji&&&||19910606|M|||||||||||||||||||||",
        "PV1|1|clinic|internal medicine||||||||||||||||||",
        f"OBR|1|||||{now()}|{now()}|||inspector||||{now()}||||RD||RD"
        f"||||HM|||||||Genrui||||||||",
    ]

    idx = 0
    for idx, (kode, nilai, unit, rentang, flag) in enumerate(PANEL, start=1):
        segments.append(
            f"OBX|{idx}|NM|^{kode}^||{nilai}|{unit}|{rentang}|{flag}|||F||||||||"
        )

    for meta, nilai in [
        ("Blood Mode", "whole blood"),
        ("Test Mode", "CBC+DIFF"),
        ("Ref Group", "man"),
        ("Remarks", "kiriman simulator"),
    ]:
        idx += 1
        segments.append(f"OBX|{idx}|IS|^{meta}^||{nilai}||||||F||||||||")

    if with_bitmap:
        for nama in BITMAPS:
            idx += 1
            # Payload besar untuk memeriksa MidLab benar-benar melewatinya.
            segments.append(f"OBX|{idx}|ED|^{nama}^||{'A' * 4096}||||||F||||||||")

    return wrap(segments)


def show(label: str, raw: bytes):
    print(f"\n--- {label} ---")
    for baris in unwrap(raw).split("\r"):
        if baris.strip():
            # Bitmap dipendekkan agar output terminal terbaca.
            print(baris[:120] + (" …" if len(baris) > 120 else ""))


def receive(sock: socket.socket, label: str, timeout: float = 10) -> bytes:
    sock.settimeout(timeout)
    try:
        data = sock.recv(65536)
    except socket.timeout:
        print(f"\n!! TIMEOUT menunggu {label} ({timeout}s)")
        return b""
    if data:
        show(label, data)
    return data


def _cek_ack(balasan: bytes, control_id: str) -> bool:
    """ACK sah bila MSA|AA| memantulkan MSH-10 yang dikirim (dokumen tabel 11)."""
    if not balasan:
        return False
    teks = unwrap(balasan)
    if f"MSA|AA|{control_id}" not in teks:
        print(f"!! MSA|AA|{control_id} tidak ditemukan di balasan")
        return False
    if "ACK^R01" not in teks:
        print("!! balasan bukan ACK^R01")
        return False
    return True


def scenario_result(sock: socket.socket, sample_id: str) -> bool:
    control_id = "1275"
    pesan = build_oru(sample_id, control_id)
    show("KIRIM ORU^R01 (CBC+DIFF, 25 parameter)", pesan)
    sock.sendall(pesan)
    return _cek_ack(receive(sock, "TERIMA ACK^R01"), control_id)


def scenario_bitmap(sock: socket.socket, sample_id: str) -> bool:
    control_id = "1276"
    pesan = build_oru(sample_id, control_id, with_bitmap=True)
    show("KIRIM ORU^R01 + 4 bitmap ED", pesan)
    sock.sendall(pesan)
    return _cek_ack(receive(sock, "TERIMA ACK^R01"), control_id)


def scenario_noack(sock: socket.socket, sample_id: str) -> bool:
    """Kirim hasil lalu diam — periksa MidLab tidak menggantung koneksi."""
    control_id = "1277"
    pesan = build_oru(sample_id, control_id)
    show("KIRIM ORU^R01 (tanpa membaca balasan segera)", pesan)
    sock.sendall(pesan)
    print("\n… menunggu 5 detik tanpa membaca ACK …")
    import time
    time.sleep(5)
    return _cek_ack(receive(sock, "TERIMA ACK^R01 (tertunda)"), control_id)


SCENARIOS = {
    "result": scenario_result,
    "bitmap": scenario_bitmap,
    "noack": scenario_noack,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulator alat ARUMA AR580")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--sample", default=DEFAULT_SAMPLE)
    ap.add_argument(
        "--scenario",
        choices=list(SCENARIOS) + ["all"],
        default="all",
        help="default: result+bitmap",
    )
    args = ap.parse_args()

    pilihan = ["result", "bitmap"] if args.scenario == "all" else [args.scenario]

    print(f"Connect ke MidLab {args.host}:{args.port} sebagai AR580 …")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=10)
    except OSError as e:
        print(f"!! gagal connect: {e}")
        return 1

    hasil = {}
    try:
        for nama in pilihan:
            hasil[nama] = SCENARIOS[nama](sock, args.sample)
    finally:
        sock.close()

    print("\n=== RINGKASAN ===")
    for nama, ok in hasil.items():
        print(f"  {nama:8s} : {'OK' if ok else 'GAGAL'}")

    return 0 if all(hasil.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
