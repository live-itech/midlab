#!/usr/bin/env python3
"""
scripts/migrate_timezone_local.py — Geser timestamp lama UTC → waktu lokal lab.

Sebelum perubahan zona waktu, semua kolom DATETIME diisi `datetime.now(timezone.utc)`
sehingga yang tersimpan adalah jam dinding UTC. Setelah perubahan, kolom yang sama
diisi jam dinding lokal lab (lihat lib/timeutil.py). Tanpa migrasi, baris lama
tampil mundur 7 jam di console dan riwayat jadi terputus di titik cutover.

Script ini menambahkan offset zona lab (default +7 jam) ke baris-baris lama.

PENGAMAN
--------
1. Dry-run adalah default. Perlu `--apply` untuk benar-benar menulis.
2. Backup mysqldump otomatis dibuat sebelum menulis (matikan dengan --skip-backup).
3. Idempotent: menulis penanda ke tbl_settings dan menolak jalan dua kali
   (paksa dengan --force kalau memang perlu, mis. setelah restore backup).
4. Batas ID: hanya baris yang sudah ada SAAT script mulai yang digeser. Baris
   yang ditulis service (yang sudah pakai jam lokal) selama/ sesudah migrasi
   tidak ikut tergeser, jadi aman walau service lupa dihentikan.

PEMAKAIAN
---------
    # lihat rencananya dulu
    python scripts/migrate_timezone_local.py

    # jalankan
    sudo systemctl stop 'midlab-*'
    python scripts/migrate_timezone_local.py --apply
    sudo systemctl start 'midlab-*'
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from lib import timeutil
from lib.config import Config
from lib.db import DBManager


# Penanda idempotensi di tbl_settings.
MARKER_KEY = "timezone.local_migration_applied_at"

# Tabel → (kolom PK integer untuk batas aman, daftar kolom DATETIME).
# tbl_settings ditangani terpisah: PK-nya string, dan value cursor perlu diparse.
TABLES = {
    "tbl_result":          ("id", ["received_at", "sent_at"]),
    "tbl_order":           ("id", ["created_at", "sent_to_instrument_at"]),
    "tbl_service_log":     ("id", ["logged_at"]),
    "tbl_lis_event_queue": ("id", ["created_at", "sent_at"]),
    "tbl_instrument":      ("id", ["last_lis_sync_at"]),
}


def lab_offset_hours() -> float:
    """Offset zona lab terhadap UTC, dalam jam (Asia/Jakarta → 7.0)."""
    return timeutil.now().utcoffset().total_seconds() / 3600.0


def table_exists(session, table: str) -> bool:
    return bool(session.execute(text(f"SHOW TABLES LIKE '{table}'")).fetchall())


def read_marker(session) -> str | None:
    if not table_exists(session, "tbl_settings"):
        return None
    row = session.execute(
        text("SELECT value FROM tbl_settings WHERE `key` = :k"), {"k": MARKER_KEY}
    ).fetchone()
    return row[0] if row else None


def backup(cfg: Config, out_dir: str) -> str:
    """mysqldump seluruh database sebelum menulis apa pun."""
    os.makedirs(out_dir, exist_ok=True)
    dbname = cfg.get("database.database", "midlab_db")
    path = os.path.join(
        out_dir, f"{dbname}-pre-tz-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sql"
    )
    cmd = [
        "mysqldump",
        f"--host={cfg.get('database.host', '127.0.0.1')}",
        f"--port={cfg.get('database.port', 3306)}",
        f"--user={cfg.get('database.user', 'midlab')}",
        f"--password={cfg.get('database.password', '')}",
        "--single-transaction",
        "--routines",
        dbname,
    ]
    with open(path, "w") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        os.unlink(path)
        raise RuntimeError(f"mysqldump gagal: {proc.stderr.strip()}")
    return path


def plan(session) -> tuple[dict, list]:
    """
    Kumpulkan batas ID dan jumlah baris terdampak per tabel.

    Batas ID diambil SEBELUM update: baris yang lahir setelah titik ini sudah
    memakai jam lokal dan tidak boleh digeser lagi.
    """
    bounds, report = {}, []
    for table, (pk, cols) in TABLES.items():
        if not table_exists(session, table):
            report.append((table, None, 0, "tabel tidak ada — dilewati"))
            continue
        max_id = session.execute(text(f"SELECT MAX({pk}) FROM {table}")).scalar()
        if max_id is None:
            report.append((table, None, 0, "kosong — dilewati"))
            continue
        where = " OR ".join(f"{c} IS NOT NULL" for c in cols)
        n = session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {pk} <= :m AND ({where})"),
            {"m": max_id},
        ).scalar()
        bounds[table] = max_id
        report.append((table, max_id, n, ", ".join(cols)))
    return bounds, report


def shift_tables(session, bounds: dict, hours: float) -> None:
    """Geser kolom DATETIME dengan INTERVAL, hanya sampai batas ID."""
    for table, max_id in bounds.items():
        pk, cols = TABLES[table]
        # NULL tetap NULL: DATE_ADD(NULL, ...) = NULL, jadi aman tanpa CASE.
        assigns = ", ".join(
            f"{c} = DATE_ADD({c}, INTERVAL :mins MINUTE)" for c in cols
        )
        session.execute(
            text(f"UPDATE {table} SET {assigns} WHERE {pk} <= :m"),
            {"mins": int(round(hours * 60)), "m": max_id},
        )


def shift_log_cursors(session, hours: float) -> int:
    """
    Geser juga cursor log yang disimpan sebagai string ISO di tbl_settings.

    Cursor ini dibandingkan dengan tbl_service_log.logged_at. Kalau logged_at
    digeser +7 jam tapi cursornya tidak, LogPusher akan mengirim ulang sampai
    7 jam log lama ke EazyApp.
    """
    if not table_exists(session, "tbl_settings"):
        return 0
    rows = session.execute(
        text("SELECT `key`, value FROM tbl_settings WHERE `key` LIKE 'lis.log_cursor.%'")
    ).fetchall()
    n = 0
    for key, value in rows:
        if not value:
            continue
        try:
            shifted = datetime.fromisoformat(value) + timedelta(hours=hours)
        except ValueError:
            print(f"  ! cursor {key} tidak bisa diparse ({value!r}) — dilewati")
            continue
        session.execute(
            text("UPDATE tbl_settings SET value = :v WHERE `key` = :k"),
            {"v": shifted.isoformat(), "k": key},
        )
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="benar-benar tulis perubahan (default: dry-run)")
    ap.add_argument("--force", action="store_true",
                    help="jalankan walau penanda migrasi sudah ada")
    ap.add_argument("--skip-backup", action="store_true",
                    help="lewati mysqldump (tidak disarankan)")
    ap.add_argument("--backup-dir", default="/var/backups/midlab",
                    help="lokasi file backup (default: /var/backups/midlab)")
    args = ap.parse_args()

    cfg = Config()
    hours = lab_offset_hours()
    session = DBManager().get_session()

    try:
        marker = read_marker(session)
        if marker and not args.force:
            print(f"Migrasi sudah pernah dijalankan pada {marker}.")
            print("Tidak melakukan apa-apa. Pakai --force kalau memang perlu ulang.")
            return 0

        bounds, report = plan(session)

        print(f"Zona lab   : {timeutil.get_lab_tz()} (offset +{hours:g} jam)")
        print(f"Mode       : {'APPLY' if args.apply else 'DRY-RUN'}")
        print()
        print(f"{'Tabel':<22} {'batas id':>9} {'baris':>7}  kolom")
        print("-" * 78)
        for table, max_id, n, note in report:
            print(f"{table:<22} {str(max_id or '-'):>9} {n:>7}  {note}")
        print()

        if not bounds:
            print("Tidak ada yang perlu digeser.")
            return 0

        if not args.apply:
            print("Dry-run selesai. Jalankan ulang dengan --apply untuk menulis.")
            return 0

        if not args.skip_backup:
            path = backup(cfg, args.backup_dir)
            print(f"Backup   : {path}")

        shift_tables(session, bounds, hours)
        n_cursor = shift_log_cursors(session, hours)
        if n_cursor:
            print(f"Cursor log digeser: {n_cursor}")

        session.execute(
            text("INSERT INTO tbl_settings (`key`, value, updated_at) "
                 "VALUES (:k, :v, :t) ON DUPLICATE KEY UPDATE value = :v, updated_at = :t"),
            {"k": MARKER_KEY, "v": timeutil.isoformat(), "t": timeutil.now_naive()},
        )
        session.commit()
        print("Selesai. Timestamp lama sekarang dalam waktu lokal lab.")
        return 0

    except Exception as e:
        session.rollback()
        print(f"GAGAL: {e}", file=sys.stderr)
        print("Tidak ada perubahan yang di-commit.", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
