"""Test SQL migration string contains expected DDL + idempotency guard."""
import re
from pathlib import Path


def test_migration_script_has_expected_ddl():
    sql = Path("scripts/migrate_lis_api.py").read_text()
    assert "ADD COLUMN lis_instrument_id" in sql
    assert "ADD COLUMN lis_api_key" in sql
    assert "ADD COLUMN order_poll_interval" in sql
    assert "ADD COLUMN last_lis_sync_at" in sql
    assert "ADD COLUMN lis_status_pushed" in sql
    assert "ADD COLUMN lis_bridge_enabled" in sql
    assert "CREATE TABLE" in sql and "tbl_lis_event_queue" in sql
    assert re.search(r"IF NOT EXISTS\s+tbl_lis_event_queue", sql)


def test_migrasi_fingerprint_punya_ddl_dan_penjaga_idempotensi():
    src = Path("scripts/migrate_result_fingerprint.py").read_text()
    assert "ADD COLUMN result_fingerprint CHAR(64) NULL" in src
    assert "idx_result_instrument_received" in src
    # Idempoten: cek INFORMATION_SCHEMA dulu sebelum ALTER/CREATE INDEX
    assert "information_schema.columns" in src
    assert "information_schema.statistics" in src
    # Backfill bertahap supaya tabel produksi tidak terkunci lama
    assert "BATCH_SIZE" in src
