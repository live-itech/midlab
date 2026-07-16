"""
scripts/migrate_result_protocol_width.py — Lebarkan tbl_result.protocol → VARCHAR(50).

Menyamakan lebar dengan tbl_instrument.protocol (lihat migrate_protocol_to_varchar.sql).
Nama driver spesifik alat lebih panjang dari VARCHAR(10) lama, mis.
'HL7_MINDRAY_BS200E' (18 karakter) dan 'COBAS_C111' (11). Pada MySQL mode
non-strict kelebihannya dipotong diam-diam sehingga tbl_result mencatat protokol
yang salah; pada mode strict INSERT gagal dan hasil alat hilang.

Idempotent: aman dijalankan berkali-kali (cek INFORMATION_SCHEMA dulu).

Usage:
    python3 scripts/migrate_result_protocol_width.py
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from lib.db import DBManager


TABLE = "tbl_result"
COLUMN = "protocol"
TARGET_LENGTH = 50

ALTER_SQL = f"ALTER TABLE {TABLE} MODIFY COLUMN {COLUMN} VARCHAR({TARGET_LENGTH}) NOT NULL"


def _column_length(conn, table: str, column: str) -> int | None:
    """Lebar kolom saat ini; None bila kolom/tabel tidak ada."""
    row = conn.execute(
        text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] if row else None


def main():
    db = DBManager()
    with db.engine.begin() as conn:
        current = _column_length(conn, TABLE, COLUMN)

        if current is None:
            print(f"ERROR: {TABLE}.{COLUMN} tidak ditemukan.", file=sys.stderr)
            return 1

        if current >= TARGET_LENGTH:
            print(f"  skip: {TABLE}.{COLUMN} sudah VARCHAR({current})")
        else:
            print(f"  MODIFY {TABLE}.{COLUMN}: VARCHAR({current}) → VARCHAR({TARGET_LENGTH})")
            conn.execute(text(ALTER_SQL))

        # Laporkan nilai yang mungkin terlanjur terpotong oleh kolom lama.
        rows = conn.execute(
            text(f"SELECT {COLUMN}, COUNT(*) FROM {TABLE} GROUP BY {COLUMN}")
        ).all()
        if rows:
            print("  protokol yang tercatat di tbl_result:")
            for protocol, jumlah in rows:
                catatan = " ← mungkin terpotong" if len(protocol or "") == 10 else ""
                print(f"    {protocol!r}: {jumlah} baris{catatan}")

    print("OK: migrasi lebar tbl_result.protocol selesai.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
