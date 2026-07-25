#!/usr/bin/env python3
"""
scripts/migrate_tap_session.py — Buat tabel tbl_tap_session.

Metadata sesi tapping data. Aliran byte-nya TIDAK di sini — ada di
/var/log/midlab/tap/<id>.jsonl.

Idempotent: aman dijalankan berkali-kali (cek INFORMATION_SCHEMA dulu).

Usage:
    python3 scripts/migrate_tap_session.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text

from lib.db import DBManager


DDL = """
CREATE TABLE tbl_tap_session (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    transport         ENUM('tcp_server','tcp_client','serial') NOT NULL,
    target            VARCHAR(255) NOT NULL,
    protocol_basis    ENUM('ASTM','HL7','RAW','AUTO') NOT NULL,
    detected_protocol VARCHAR(20) NULL,
    response_mode     ENUM('uni','bidi') NOT NULL DEFAULT 'uni',
    status            ENUM('running','stopped','error') NOT NULL DEFAULT 'running',
    bytes_rx          INT NOT NULL DEFAULT 0,
    bytes_tx          INT NOT NULL DEFAULT 0,
    message_count     INT NOT NULL DEFAULT 0,
    error_message     TEXT NULL,
    started_at        DATETIME NULL,
    stopped_at        DATETIME NULL,
    INDEX idx_status (status),
    INDEX idx_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def main() -> int:
    # Pola sama dengan scripts/migrate_result_protocol_width.py:44.
    db = DBManager()
    with db.engine.begin() as conn:
        ada = conn.execute(text(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tbl_tap_session'"
        )).scalar()
        if ada:
            print("OK: tbl_tap_session sudah ada, tidak ada perubahan.")
            return 0
        conn.execute(text(DDL))
        print("OK: tbl_tap_session dibuat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
