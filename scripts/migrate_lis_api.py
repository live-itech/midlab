"""
scripts/migrate_lis_api.py — Migrasi schema untuk LIS bridging EazyApp.
Idempotent: aman dijalankan berkali-kali (cek INFORMATION_SCHEMA dulu).

Usage:
    python3 scripts/migrate_lis_api.py
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from lib.db import DBManager


# Kolom baru untuk tbl_instrument: (nama_kolom, ALTER TABLE statement)
ADD_COLS_TBL_INSTRUMENT = [
    ("lis_instrument_id",   "ALTER TABLE tbl_instrument ADD COLUMN lis_instrument_id VARCHAR(50) NULL"),
    ("lis_api_key",         "ALTER TABLE tbl_instrument ADD COLUMN lis_api_key VARCHAR(255) NULL"),
    ("order_poll_interval", "ALTER TABLE tbl_instrument ADD COLUMN order_poll_interval INT DEFAULT 10"),
    ("last_lis_sync_at",    "ALTER TABLE tbl_instrument ADD COLUMN last_lis_sync_at DATETIME NULL"),
    ("lis_status_pushed",   "ALTER TABLE tbl_instrument ADD COLUMN lis_status_pushed VARCHAR(20) NULL"),
    ("lis_bridge_enabled",  "ALTER TABLE tbl_instrument ADD COLUMN lis_bridge_enabled BOOLEAN DEFAULT FALSE"),
]

CREATE_EVENT_QUEUE = """
CREATE TABLE IF NOT EXISTS tbl_lis_event_queue (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    instrument_id INT NOT NULL,
    event_type    ENUM('status','log') NOT NULL,
    payload_json  JSON NOT NULL,
    send_status   ENUM('pending','sent','failed','skipped') DEFAULT 'pending',
    retry_count   INT DEFAULT 0,
    error_message TEXT NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    sent_at       DATETIME NULL,
    INDEX idx_inst_status (instrument_id, send_status, id)
)
"""


def _column_exists(conn, table: str, column: str) -> bool:
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
    engine = db.get_engine()
    with engine.begin() as conn:
        for col_name, alter_sql in ADD_COLS_TBL_INSTRUMENT:
            if _column_exists(conn, "tbl_instrument", col_name):
                print(f"  skip: tbl_instrument.{col_name} already exists")
                continue
            print(f"  ADD COLUMN tbl_instrument.{col_name}")
            conn.execute(text(alter_sql))

        print("  CREATE TABLE IF NOT EXISTS tbl_lis_event_queue")
        conn.execute(text(CREATE_EVENT_QUEUE))

    print("OK: migrasi LIS API selesai.")


if __name__ == "__main__":
    main()
