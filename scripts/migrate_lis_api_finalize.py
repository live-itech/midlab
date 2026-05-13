"""
scripts/migrate_lis_api_finalize.py — Final cleanup post-cutover.
Run ONLY after semua alat sudah lis_bridge_enabled=true dan stabil >= 1 minggu.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from lib.db import DBManager


def main():
    db = DBManager()
    engine = db.get_engine()
    with engine.begin() as conn:
        not_enabled = conn.execute(
            text(
                "SELECT COUNT(*) FROM tbl_instrument "
                "WHERE lis_bridge_enabled = FALSE AND is_active = TRUE"
            )
        ).scalar()
        if not_enabled and int(not_enabled) > 0:
            print(f"ABORT: ada {not_enabled} alat aktif yang lis_bridge_enabled=false")
            sys.exit(1)

        print("  DROP COLUMN lis_bridge_enabled")
        conn.execute(text("ALTER TABLE tbl_instrument DROP COLUMN lis_bridge_enabled"))

        print("  DELETE deprecated settings (lis.api_url, lis.api_key)")
        conn.execute(text("DELETE FROM tbl_settings WHERE `key` IN ('lis.api_url','lis.api_key')"))

    print("OK: finalization selesai.")


if __name__ == "__main__":
    main()
