"""
Test skema TblTapSession — khususnya kesetaraan dua jalur pembuatan tabel.

Tabel ini bisa lahir lewat dua jalur yang berbeda:
  - install.sh  → Base.metadata.create_all()  (server baru)
  - deploy.sh   → scripts/migrate_tap_session.py  (server yang di-update)

Keduanya HARUS menghasilkan tabel yang sama. Pernah tidak: index hanya
dideklarasikan di DDL skrip migrasi, sehingga server yang diinstall lewat
install.sh mendapat tabel tanpa index — dan tidak bisa ditambal belakangan
karena skrip migrasinya no-op begitu tabelnya sudah ada.
"""
import re
from pathlib import Path

from sqlalchemy import create_engine, inspect

from lib.db import Base, TblTapSession


DDL_PATH = Path(__file__).resolve().parent.parent / "scripts" / "migrate_tap_session.py"

# Index yang dijanjikan DDL migrasi.
INDEX_DIHARAPKAN = {"idx_status": ["status"], "idx_started": ["started_at"]}


def test_kolom_lengkap():
    cols = TblTapSession.__table__.columns.keys()
    for c in ("id", "name", "transport", "target", "protocol_basis",
              "detected_protocol", "response_mode", "status",
              "bytes_rx", "bytes_tx", "message_count", "error_message",
              "started_at", "stopped_at"):
        assert c in cols, f"kolom hilang: {c}"


def test_create_all_menghasilkan_index_yang_sama_dengan_ddl_migrasi():
    """Jalur install.sh harus setara dengan jalur skrip migrasi."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    hasil = {
        i["name"]: list(i["column_names"])
        for i in inspect(engine).get_indexes("tbl_tap_session")
    }
    assert hasil == INDEX_DIHARAPKAN, (
        f"create_all() menghasilkan index {hasil}, "
        f"seharusnya {INDEX_DIHARAPKAN} — server yang diinstall lewat "
        f"install.sh akan berbeda dari yang lewat migrate_tap_session.py"
    )


def test_ddl_migrasi_masih_mendeklarasikan_index_yang_sama():
    """
    Jaga agar DDL skrip migrasi tidak berubah sendiri lepas dari model ORM.
    Dibaca sebagai teks: skrip itu menembak MySQL, tidak bisa dijalankan di sini.
    """
    ddl = DDL_PATH.read_text(encoding="utf-8")
    ditemukan = dict(re.findall(r"INDEX\s+(\w+)\s*\((\w+)\)", ddl))
    assert ditemukan == {k: v[0] for k, v in INDEX_DIHARAPKAN.items()}, (
        f"DDL di {DDL_PATH.name} mendeklarasikan index {ditemukan}, "
        f"tidak lagi sinkron dengan model ORM"
    )
