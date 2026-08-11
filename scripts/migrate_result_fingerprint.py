"""
scripts/migrate_result_fingerprint.py — Tambah tbl_result.result_fingerprint.

Kolom sidik jari isi klinis, dipakai Result Monitor untuk membedakan kiriman
ulang dari alat (isi persis sama) dengan pemeriksaan baru. Lihat
docs/superpowers/specs/2026-08-10-result-count-dedup-design.md.

Jalankan skrip ini SEBELUM men-deploy kode baru: model ORM yang baru menyebut
kolom ini pada setiap INSERT, jadi bila kode naik duluan sementara kolomnya
belum ada, penyimpanan hasil dari alat akan gagal.

Aman dijalankan saat service produksi hidup — kolomnya nullable dan service
versi lama tidak menyentuhnya.

Idempotent: aman dijalankan berkali-kali (cek INFORMATION_SCHEMA dulu).

Usage:
    python3 scripts/migrate_result_fingerprint.py
"""
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text

from lib.db import DBManager
from lib.fingerprint import result_fingerprint


TABLE = "tbl_result"
COLUMN = "result_fingerprint"
INDEX = "idx_result_instrument_received"

# Backfill dicicil supaya tabel produksi yang sudah besar tidak terkunci lama
# dan memori tidak menampung seluruh result_json sekaligus.
BATCH_SIZE = 500

# DDL ditulis literal (bukan f-string) supaya mudah di-grep dan bisa dikunci tes.
ADD_COLUMN_SQL = (
    "ALTER TABLE tbl_result ADD COLUMN result_fingerprint CHAR(64) NULL"
)
CREATE_INDEX_SQL = (
    "CREATE INDEX idx_result_instrument_received "
    "ON tbl_result (instrument_id, received_at)"
)


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t "
            "AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def _index_exists(conn, table: str, index: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t "
            "AND index_name = :i"
        ),
        {"t": table, "i": index},
    ).first()
    return row is not None


def _payload_of(result_json):
    """Kolom JSON bisa datang sebagai dict atau teks, tergantung driver MySQL."""
    if isinstance(result_json, (str, bytes)):
        try:
            return json.loads(result_json)
        except Exception:
            return None
    return result_json


def _backfill(engine) -> int:
    """Isi sidik jari baris lama, satu batch per transaksi. Return jumlah baris."""
    total = 0
    while True:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"SELECT id, result_json FROM {TABLE} "
                    f"WHERE {COLUMN} IS NULL LIMIT {BATCH_SIZE}"
                )
            ).fetchall()

            if not rows:
                return total

            terisi_batch = 0
            for row_id, result_json in rows:
                try:
                    fp = result_fingerprint(_payload_of(result_json))
                except Exception:
                    # Baris rusak dibiarkan NULL; query rekap tetap
                    # menghitungnya sebagai baris unik.
                    continue

                conn.execute(
                    text(f"UPDATE {TABLE} SET {COLUMN} = :fp WHERE id = :id"),
                    {"fp": fp, "id": row_id},
                )
                terisi_batch += 1
                total += 1

            if terisi_batch == 0:
                # Seluruh batch gagal di-hash; tanpa ini loop tidak pernah
                # berhenti karena barisnya tetap NULL dan terpilih lagi.
                print(f"  backfill berhenti: {len(rows)} baris tidak bisa di-hash")
                return total

        print(f"  backfill: {total} baris terisi...")


def main():
    db = DBManager()

    with db.engine.begin() as conn:
        if _column_exists(conn, TABLE, COLUMN):
            print(f"  skip: {TABLE}.{COLUMN} sudah ada")
        else:
            print(f"  ADD COLUMN {TABLE}.{COLUMN} CHAR(64) NULL")
            conn.execute(text(ADD_COLUMN_SQL))

        if _index_exists(conn, TABLE, INDEX):
            print(f"  skip: index {INDEX} sudah ada")
        else:
            print(f"  CREATE INDEX {INDEX} ON {TABLE} (instrument_id, received_at)")
            conn.execute(text(CREATE_INDEX_SQL))

    filled = _backfill(db.engine)
    print(f"  backfill selesai: {filled} baris")

    with db.engine.begin() as conn:
        sisa = conn.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} IS NULL")
        ).scalar()
    print(f"  tersisa NULL: {sisa} baris (dihitung sebagai baris unik)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
