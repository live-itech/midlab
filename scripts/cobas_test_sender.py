#!/usr/bin/env python3
"""
scripts/cobas_test_sender.py — Cobas c-111 ASTM test transmitter.

MidLab dikonfigurasi sebagai TCP CLIENT (connection='client'), jadi server-side
harus listen di port alat (default 10001) lalu push ASTM session yang valid.

Script ini:
  1. Listen di 0.0.0.0:<port>
  2. Tunggu MidLab connect
  3. Kirim 1 sesi ASTM lengkap (ENQ → frames → EOT) dengan checksum benar
  4. Tunggu ACK per frame, log respon
  5. Tutup koneksi setelah EOT

Run:
  python3 scripts/cobas_test_sender.py                  # default 0.0.0.0:10001
  python3 scripts/cobas_test_sender.py --port 10001
  python3 scripts/cobas_test_sender.py --once           # send 1x lalu exit
  python3 scripts/cobas_test_sender.py --no-wait-ack    # blast tanpa nunggu ACK

Frame format (ref parser.py + manual Roche 7.1.5):
  STX FN <record_text> ETX <CS hi> <CS lo> CR LF
  CS = sum(FN..ETX inclusive) & 0xFF, encoded as 2-hex uppercase
  FN cycles 1..7, then 0..7, ...
"""

import argparse
import socket
import sys
import time
from datetime import datetime

STX = 0x02
ETX = 0x03
EOT = 0x04
ENQ = 0x05
ACK = 0x06
NAK = 0x15
CR  = 0x0D
LF  = 0x0A


def build_frame(fn: int, text: str) -> bytes:
    """Bangun 1 ASTM frame: STX FN text ETX CS CR LF."""
    body = bytes([fn + 0x30]) + text.encode("ascii") + bytes([ETX])
    cs = sum(body) & 0xFF
    cs_hex = f"{cs:02X}".encode("ascii")
    return bytes([STX]) + body + cs_hex + bytes([CR, LF])


def build_records(sample_id: str = "SAMPLE001") -> list[str]:
    """Bangun list record string (tanpa STX/ETX) untuk 1 sesi result."""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return [
        # H — Header. Delimiters: |\^& sesuai manual 7.1.4.3
        f"H|\\^&|||cobas c111^03-01^123456|||||host|P|1|{ts}\r",
        # P — Patient
        "P|1||PID12345||DOE^JOHN^A||19800101|M\r",
        # O — Order. Field 4 = sample_id; field 5 = test universal id ^^^GLU
        f"O|1|{sample_id}||^^^GLU|N||||||||||||||||||F\r",
        # R — Result. Field 3 = test ^^^GLU; value=95; unit=mg/dL; ref=70_to_110;
        #            flag=N; status=F; result_datetime
        f"R|1|^^^GLU|95|mg/dL|70_to_110||N||F||{ts}\r",
        # R kedua — contoh banyak hasil
        f"R|2|^^^CHOL|180|mg/dL|0_to_200||N||F||{ts}\r",
        # L — Terminator. Field 3 = N (normal end)
        "L|1|N\r",
    ]


def chunk_records_to_frames(records: list[str]) -> list[bytes]:
    """
    Setiap record = 1 frame (ETX terminator). FN cycle 1..7, 0..7.
    Manual mengizinkan multi-record per frame, tapi 1:1 paling aman dipersepsi
    parser MidLab dan paling mudah didebug.
    """
    frames = []
    fn = 1
    for rec in records:
        frames.append(build_frame(fn, rec))
        fn = (fn + 1) if fn < 7 else 0
        if fn == 0 and len(frames) == 1:
            fn = 1  # safeguard, tidak akan tercapai dgn 6 records
    return frames


def send_session(conn: socket.socket, wait_ack: bool, sample_id: str) -> None:
    records = build_records(sample_id)
    frames = chunk_records_to_frames(records)
    print(f"[SENDER] Sesi: {len(records)} record → {len(frames)} frame, sample_id={sample_id}")

    def _read_byte(timeout: float = 5.0) -> int | None:
        conn.settimeout(timeout)
        try:
            b = conn.recv(1)
            if not b:
                return None
            return b[0]
        except socket.timeout:
            return None

    # 1. ENQ
    print("[SENDER] → ENQ")
    conn.sendall(bytes([ENQ]))
    if wait_ack:
        b = _read_byte()
        print(f"[SENDER] ← {hex(b) if b is not None else 'TIMEOUT'} (expect ACK 0x06)")
        if b != ACK:
            print("[SENDER] MidLab tidak ACK ENQ — abort")
            return

    # 2. Frames
    for i, frame in enumerate(frames, 1):
        print(f"[SENDER] → Frame #{i} ({len(frame)} bytes): {frame!r}")
        conn.sendall(frame)
        if wait_ack:
            b = _read_byte()
            label = {ACK: "ACK", NAK: "NAK"}.get(b, hex(b) if b is not None else "TIMEOUT")
            print(f"[SENDER] ← {label}")
            if b == NAK:
                print(f"[SENDER] NAK pada frame #{i}, retry sekali...")
                conn.sendall(frame)
                _read_byte()

    # 3. EOT
    print("[SENDER] → EOT")
    conn.sendall(bytes([EOT]))
    time.sleep(0.5)
    print("[SENDER] Sesi selesai.")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=10001)
    p.add_argument("--once", action="store_true",
                   help="kirim 1 sesi lalu exit (bukan loop)")
    p.add_argument("--no-wait-ack", action="store_true",
                   help="blast tanpa nunggu ACK MidLab")
    p.add_argument("--sample-id", default="SAMPLE001")
    p.add_argument("--delay", type=float, default=0.5,
                   help="delay (detik) antara accept dan kirim sesi")
    args = p.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(1)
    print(f"[SERVER] Listen di {args.host}:{args.port} — menunggu MidLab connect")

    try:
        while True:
            conn, addr = srv.accept()
            print(f"[SERVER] MidLab terhubung: {addr[0]}:{addr[1]}")
            try:
                time.sleep(args.delay)
                send_session(
                    conn,
                    wait_ack=not args.no_wait_ack,
                    sample_id=args.sample_id,
                )
            except (BrokenPipeError, ConnectionResetError) as e:
                print(f"[SERVER] Koneksi putus: {e}")
            finally:
                conn.close()
                print(f"[SERVER] Koneksi {addr[0]} ditutup")
            if args.once:
                break
    except KeyboardInterrupt:
        print("\n[SERVER] Shutdown")
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
