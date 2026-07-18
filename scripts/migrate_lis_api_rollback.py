"""
scripts/migrate_lis_api_rollback.py — Rollback migrasi LIS bridging.

Usage:
    python3 scripts/migrate_lis_api_rollback.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from lib.db import DBManager


DROP_COLS = [
    "lis_instrument_id",
    "lis_api_key",
    "order_poll_interval",
    "last_lis_sync_at",
    "lis_status_pushed",
    "lis_bridge_enabled",
]


def _column_exists(conn, table, column):
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def main():
    db = DBManager()
    engine = db.engine
    with engine.begin() as conn:
        for col in DROP_COLS:
            if not _column_exists(conn, "tbl_instrument", col):
                print(f"  skip: tbl_instrument.{col} tidak ada")
                continue
            print(f"  DROP COLUMN tbl_instrument.{col}")
            conn.execute(text(f"ALTER TABLE tbl_instrument DROP COLUMN {col}"))
        print("  DROP TABLE IF EXISTS tbl_lis_event_queue")
        conn.execute(text("DROP TABLE IF EXISTS tbl_lis_event_queue"))
    print("OK: rollback selesai.")


if __name__ == "__main__":
    main()
