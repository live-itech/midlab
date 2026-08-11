# Rekap Jumlah Pemeriksaan per Alat + Dedup Kiriman Ulang — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Result Monitor bisa menjawab "berapa pemeriksaan yang benar-benar dikerjakan alat X pada rentang tanggal Y", dengan kiriman ulang dari alat dihitung sekali.

**Architecture:** Sidik jari SHA-256 dari isi klinis tiap hasil (`sample_id` + daftar `test_code/value/unit/flag`) disimpan di kolom baru `tbl_result.result_fingerprint` saat hasil masuk. Endpoint rekap baru `GET /api/results/summary` mengagregasi `COUNT(*)` dan `COUNT(DISTINCT fingerprint)` per alat lewat satu query `GROUP BY`, dan panel di atas tabel Result Monitor menampilkannya. `/api/results` yang lama tidak diubah sama sekali.

**Tech Stack:** Python 3.10+, SQLAlchemy ORM, FastAPI + Pydantic, MySQL (produksi) / SQLite in-memory (tes), Jinja2 + JS vanilla di web console, pytest.

**Spec:** `docs/superpowers/specs/2026-08-10-result-count-dedup-design.md`

## Global Constraints

- Komentar dan docstring dalam Bahasa Indonesia (CLAUDE.md aturan 7).
- Test runner: `.venv/bin/python -m pytest` — **bukan** `python3` polos (butuh `httpx`).
- Baseline sebelum mulai: 545 passed / 6 failed (pre-existing) / 3 skipped. Jumlah `failed` tidak boleh bertambah.
- `/api/results`, `ResultResponse`, `lib/models.py`, seluruh ProtocolModule, LisBridgeService, dan ResultSenderService **tidak boleh disentuh**.
- Tanda tangan `save_result()` tidak berubah.
- Kolom baru bernama persis `result_fingerprint`, tipe `CHAR(64)`, nullable.
- Index baru bernama persis `idx_result_instrument_received` pada `(instrument_id, received_at)`.
- Semua DATETIME adalah naive jam dinding lokal lab (Asia/Jakarta) — lihat kontrak waktu di docstring `lib/db.py`.
- Jangan menjalankan migrasi terhadap database produksi selama implementasi. Migrasi hanya ditulis dan diuji sebagai teks/skrip.

---

## File Structure

| File | Status | Tanggung jawab |
|---|---|---|
| `lib/fingerprint.py` | baru | Satu fungsi murni `result_fingerprint()`. Tidak tahu DB maupun HTTP. |
| `tests/test_result_fingerprint.py` | baru | Unit test fungsi di atas, tanpa DB. |
| `lib/db.py` | ubah | Kolom `result_fingerprint` + index di `TblResult`; isi sidik jari di `save_result()`; fungsi query `summarize_results()`. |
| `tests/test_db_result_summary.py` | baru | Agregasi nyata di atas SQLite in-memory. |
| `scripts/migrate_result_fingerprint.py` | baru | Tambah kolom + index + backfill bertahap. Idempoten. |
| `tests/test_migration.py` | ubah | Assert DDL dan penjaga idempotensi skrip migrasi baru. |
| `services/web_console/api.py` | ubah | Model `ResultSummaryRow` + endpoint `GET /api/results/summary`. |
| `tests/test_api_results_summary.py` | baru | Kontrak HTTP endpoint rekap, `DBManager` di-mock. |
| `services/web_console/templates/results.html` | ubah | Card panel ringkasan + `loadSummary()`. |

---

### Task 1: Fungsi sidik jari

**Files:**
- Create: `lib/fingerprint.py`
- Test: `tests/test_result_fingerprint.py`

**Interfaces:**
- Consumes: tidak ada.
- Produces: `lib.fingerprint.result_fingerprint(result_json: dict | None) -> str` — mengembalikan 64 karakter hex lowercase, tidak pernah melempar exception untuk input apa pun.

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_result_fingerprint.py`:

```python
"""Unit test sidik jari isi klinis hasil pemeriksaan — murni, tanpa DB."""
from lib.fingerprint import result_fingerprint


def _payload(sample_id="ABC123", results=None, **extra):
    """Payload minimal berbentuk ResultObject.to_dict()."""
    base = {
        "mid_version": "1.0",
        "instrument_id": 1,
        "protocol": "HL7_MINDRAY_BC5150",
        "message_id": "msg-0001",
        "message_datetime": "2026-08-10T10:30:00+07:00",
        "patient": {"patient_id": "P1", "name": "BUDI"},
        "specimen": {"sample_id": sample_id},
        "order": {"order_id": "", "panel": ""},
        "results": results if results is not None else [
            {"test_code": "WBC", "value": "5.2", "unit": "10^3/uL", "flag": "N",
             "reference_range": "4.0-10.0", "status": "F"},
            {"test_code": "RBC", "value": "4.1", "unit": "10^6/uL", "flag": "N",
             "reference_range": "3.5-5.5", "status": "F"},
        ],
        "comments": [],
        "parse_errors": [],
    }
    base.update(extra)
    return base


def test_payload_identik_menghasilkan_sidik_jari_sama():
    assert result_fingerprint(_payload()) == result_fingerprint(_payload())


def test_urutan_tes_dibalik_tetap_sama():
    a = _payload()
    b = _payload(results=list(reversed(a["results"])))
    assert result_fingerprint(a) == result_fingerprint(b)


def test_metadata_transmisi_diabaikan():
    a = _payload()
    b = _payload(message_id="msg-9999",
                 message_datetime="2026-08-10T10:35:00+07:00")
    assert result_fingerprint(a) == result_fingerprint(b)


def test_reference_range_dan_status_diabaikan():
    a = _payload()
    b = _payload()
    b["results"][0]["reference_range"] = "4.5-11.0"
    b["results"][0]["status"] = "P"
    assert result_fingerprint(a) == result_fingerprint(b)


def test_nilai_tes_berubah_menghasilkan_sidik_jari_berbeda():
    a = _payload()
    b = _payload()
    b["results"][0]["value"] = "6.0"
    assert result_fingerprint(a) != result_fingerprint(b)


def test_flag_berubah_menghasilkan_sidik_jari_berbeda():
    a = _payload()
    b = _payload()
    b["results"][0]["flag"] = "H"
    assert result_fingerprint(a) != result_fingerprint(b)


def test_sample_id_berbeda_menghasilkan_sidik_jari_berbeda():
    assert result_fingerprint(_payload(sample_id="ABC123")) != \
        result_fingerprint(_payload(sample_id="XYZ999"))


def test_sample_id_case_sensitive():
    """Huruf besar/kecil tidak diseragamkan — sebagian alat pakai ID case-sensitive."""
    assert result_fingerprint(_payload(sample_id="abc123")) != \
        result_fingerprint(_payload(sample_id="ABC123"))


def test_spasi_berlebih_diabaikan():
    a = _payload()
    b = _payload(sample_id="  ABC123 ")
    b["results"][0]["value"] = " 5.2 "
    assert result_fingerprint(a) == result_fingerprint(b)


def test_input_cacat_tidak_melempar_exception():
    for bad in (None, {}, {"specimen": None}, {"results": None},
                {"results": "bukan list"}, "bukan dict", 42):
        fp = result_fingerprint(bad)
        assert len(fp) == 64
        assert fp == fp.lower()


def test_hasil_tanpa_sample_id_dan_tanpa_tes_stabil():
    assert result_fingerprint({}) == result_fingerprint(None)


def test_nilai_numerik_dikonversi_lewat_str():
    """Driver bisa mengirim angka sebagai float; str(5.2) == '5.2'."""
    a = _payload()
    b = _payload()
    b["results"][0]["value"] = 5.2
    assert result_fingerprint(a) == result_fingerprint(b)
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_result_fingerprint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.fingerprint'`

- [ ] **Step 3: Tulis implementasi minimal**

Buat `lib/fingerprint.py`:

```python
"""
lib/fingerprint.py — Sidik jari isi klinis sebuah hasil pemeriksaan.

Dipakai untuk membedakan kiriman ulang dari alat (isi persis sama) dengan
pemeriksaan baru. Sengaja hanya memperhitungkan apa yang operator sebut
"hasilnya": nomor sampel dan daftar tes beserta nilai, satuan, dan flag.

Yang diabaikan: message_id dan message_datetime (di-generate ulang tiap
parsing), patient/order/comments, serta reference_range dan status yang bisa
berubah tanpa isi pemeriksaan ikut berubah. Kalau semua itu ikut di-hash,
kiriman ulang tidak akan pernah terdeteksi.

Fungsi ini murni: tidak menyentuh database maupun jaringan, dan tidak pernah
melempar exception — pemanggil di jalur simpan hasil tidak boleh jatuh gara-gara
sidik jari.
"""

import hashlib
import json

# Field tes yang ikut menentukan identitas sebuah hasil.
_TEST_FIELDS = ("test_code", "value", "unit", "flag")


def _text(value) -> str:
    """Nilai apa pun → teks rapi. None jadi string kosong, bukan 'None'."""
    if value is None:
        return ""
    return str(value).strip()


def result_fingerprint(result_json) -> str:
    """
    SHA-256 hex (64 karakter) dari isi klinis sebuah hasil.

    Urutan tes tidak berpengaruh: daftarnya diurutkan dulu supaya alat yang
    mengirim tes dalam urutan berbeda tetap menghasilkan sidik jari yang sama.
    """
    data = result_json if isinstance(result_json, dict) else {}

    specimen = data.get("specimen")
    if not isinstance(specimen, dict):
        specimen = {}
    sample_id = _text(specimen.get("sample_id"))

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raw_results = []

    rows = sorted(
        [_text(t.get(k)) for k in _TEST_FIELDS]
        for t in raw_results
        if isinstance(t, dict)
    )

    canonical = json.dumps(
        {"sample_id": sample_id, "results": rows},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_result_fingerprint.py -v`
Expected: PASS — 12 tes lulus.

- [ ] **Step 5: Commit**

```bash
git add lib/fingerprint.py tests/test_result_fingerprint.py
git commit -m "feat(lib): sidik jari isi klinis hasil untuk deteksi kiriman ulang"
```

---

### Task 2: Kolom `result_fingerprint` di model + jalur simpan

**Files:**
- Modify: `lib/db.py:83-103` (model `TblResult`), `lib/db.py:419-447` (`save_result`)
- Test: `tests/test_db_result_summary.py` (dibuat di task ini, ditambah lagi di Task 3)

**Interfaces:**
- Consumes: `lib.fingerprint.result_fingerprint(result_json) -> str` (Task 1).
- Produces: kolom ORM `TblResult.result_fingerprint` (`String(64)`, nullable) dan index `idx_result_instrument_received`. `save_result()` mengisi kolom itu otomatis; tanda tangannya tetap `save_result(instrument_id, protocol, raw_data, result_json) -> int | None`.

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_db_result_summary.py`:

```python
"""Test kolom sidik jari pada tbl_result di atas SQLite in-memory."""
from lib.db import TblResult
from lib.fingerprint import result_fingerprint


def _payload(sample_id="S1", value="5.2"):
    return {
        "specimen": {"sample_id": sample_id},
        "results": [{"test_code": "WBC", "value": value,
                     "unit": "10^3/uL", "flag": "N"}],
    }


def test_kolom_fingerprint_ada_di_model(db_session):
    """Kolom harus lahir lewat create_all() supaya instalasi baru ikut punya."""
    row = TblResult(
        instrument_id=1,
        protocol="HL7_MINDRAY_BC5150",
        raw_data="raw",
        result_json=_payload(),
        send_status="pending",
        result_fingerprint=result_fingerprint(_payload()),
    )
    db_session.add(row)
    db_session.commit()

    stored = db_session.query(TblResult).first()
    assert stored.result_fingerprint == result_fingerprint(_payload())
    assert len(stored.result_fingerprint) == 64


def test_fingerprint_boleh_null(db_session):
    """Baris lama sebelum backfill tetap sah."""
    row = TblResult(
        instrument_id=1,
        protocol="ASTM",
        raw_data="raw",
        result_json=_payload(),
        send_status="pending",
    )
    db_session.add(row)
    db_session.commit()
    assert db_session.query(TblResult).first().result_fingerprint is None
```

Tambahkan juga test bahwa `save_result()` mengisi kolom itu dan tidak jatuh
saat hashing bermasalah. `save_result()` membuat session sendiri lewat
`DBManager`, jadi bagian ini memakai mock:

```python
from unittest.mock import MagicMock, patch

import lib.db as db_module


def test_save_result_mengisi_fingerprint():
    captured = {}

    def _add(obj):
        captured["row"] = obj

    with patch.object(db_module, "DBManager") as MockDB:
        session = MagicMock()
        session.add.side_effect = _add
        MockDB.return_value.get_session.return_value = session
        db_module.save_result(1, "ASTM", "raw", _payload())

    assert captured["row"].result_fingerprint == result_fingerprint(_payload())


def test_save_result_tetap_jalan_saat_hashing_gagal():
    """Fitur laporan tidak boleh menjatuhkan penyimpanan hasil alat."""
    captured = {}

    def _add(obj):
        captured["row"] = obj

    with patch.object(db_module, "DBManager") as MockDB, \
         patch.object(db_module, "result_fingerprint",
                      side_effect=RuntimeError("boom")):
        session = MagicMock()
        session.add.side_effect = _add
        MockDB.return_value.get_session.return_value = session
        db_module.save_result(1, "ASTM", "raw", _payload())

    assert captured["row"].result_fingerprint is None
    assert captured["row"].raw_data == "raw"
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_db_result_summary.py -v`
Expected: FAIL — `TypeError: 'result_fingerprint' is an invalid keyword argument for TblResult`

- [ ] **Step 3: Tulis implementasi minimal**

Di `lib/db.py`, tambahkan import di dekat import `lib` lainnya (setelah `from lib.config import Config`):

```python
from lib.fingerprint import result_fingerprint
```

Pada model `TblResult` (`lib/db.py:83`), tambahkan kolom setelah `received_at`
lalu `__table_args__`:

```python
    received_at = Column(DateTime, default=now_naive)
    # Sidik jari isi klinis, untuk membedakan kiriman ulang alat dari
    # pemeriksaan baru. NULL = belum dihitung (baris lama sebelum backfill);
    # query rekap memperlakukannya sebagai baris unik.
    result_fingerprint = Column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_result_instrument_received", "instrument_id", "received_at"),
    )
```

Di `save_result()` (`lib/db.py:419`), tambahkan helper tepat di atasnya dan
pakai di konstruktor:

```python
def _safe_fingerprint(result_json: dict) -> str | None:
    """
    Sidik jari yang tidak pernah menggagalkan penyimpanan.

    Hasil dari alat lab jauh lebih penting daripada angka rekap. Kalau hashing
    bermasalah, kolomnya dibiarkan NULL dan baris tetap tersimpan.
    """
    try:
        return result_fingerprint(result_json)
    except Exception:
        return None


def save_result(
    instrument_id: int,
    protocol: str,
    raw_data: str,
    result_json: dict,
) -> int | None:
    """
    Simpan hasil baru ke tbl_result dengan status pending.
    Dipanggil oleh ProtocolModule setelah parsing.
    Returns: ID record baru atau None jika gagal.
    """
    db = DBManager()
    session = db.get_session()
    try:
        new_result = TblResult(
            instrument_id=instrument_id,
            protocol=protocol,
            raw_data=raw_data,
            result_json=result_json,
            send_status="pending",
            result_fingerprint=_safe_fingerprint(result_json),
        )
        session.add(new_result)
        session.commit()
        return new_result.id
    except Exception:
        session.rollback()
        return None
    finally:
        session.close()
```

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_db_result_summary.py -v`
Expected: PASS — 4 tes lulus.

- [ ] **Step 5: Pastikan tidak ada yang rusak**

Run: `.venv/bin/python -m pytest -q`
Expected: `failed` tidak lebih dari 6 (baseline pre-existing). Kalau ada
kegagalan baru, perbaiki sebelum lanjut — kemungkinan besar penyebabnya import
melingkar antara `lib/db.py` dan `lib/fingerprint.py` (`lib/fingerprint.py`
tidak boleh mengimpor apa pun dari `lib/db.py`).

- [ ] **Step 6: Commit**

```bash
git add lib/db.py tests/test_db_result_summary.py
git commit -m "feat(db): kolom result_fingerprint diisi otomatis saat hasil disimpan"
```

---

### Task 3: Query agregasi `summarize_results()`

**Files:**
- Modify: `lib/db.py` (tambah fungsi setelah `save_result()`)
- Test: `tests/test_db_result_summary.py`

**Interfaces:**
- Consumes: model `TblResult` dengan kolom `result_fingerprint` (Task 2).
- Produces:

```python
def summarize_results(
    session,
    status: str | None = None,
    instrument_id: int | None = None,
    received_from=None,   # datetime naive lokal
    received_to=None,     # datetime naive lokal
) -> list[dict]:
    """[{"instrument_id": int, "total": int, "unique_count": int}, ...]"""
```

  Diurutkan naik berdasarkan `instrument_id`. Alat tanpa data tidak muncul.

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan ke `tests/test_db_result_summary.py`:

```python
from datetime import datetime

from lib.db import summarize_results


def _add_result(session, instrument_id=1, payload=None, fingerprint="__auto__",
                received_at=None, send_status="pending"):
    """Sisipkan satu baris hasil; fingerprint '__auto__' = hitung dari payload."""
    payload = payload if payload is not None else _payload()
    if fingerprint == "__auto__":
        fingerprint = result_fingerprint(payload)
    session.add(TblResult(
        instrument_id=instrument_id,
        protocol="ASTM",
        raw_data="raw",
        result_json=payload,
        send_status=send_status,
        result_fingerprint=fingerprint,
        received_at=received_at or datetime(2026, 8, 10, 10, 0, 0),
    ))
    session.commit()


def test_kiriman_ulang_dihitung_sekali(db_session):
    for _ in range(3):
        _add_result(db_session)

    rows = summarize_results(db_session)
    assert rows == [{"instrument_id": 1, "total": 3, "unique_count": 1}]


def test_sample_sama_hasil_beda_dihitung_dua(db_session):
    _add_result(db_session, payload=_payload(sample_id="S1", value="5.2"))
    _add_result(db_session, payload=_payload(sample_id="S1", value="6.0"))

    rows = summarize_results(db_session)
    assert rows == [{"instrument_id": 1, "total": 2, "unique_count": 2}]


def test_baris_tanpa_fingerprint_tidak_pernah_menyatu(db_session):
    for _ in range(3):
        _add_result(db_session, fingerprint=None)

    rows = summarize_results(db_session)
    assert rows == [{"instrument_id": 1, "total": 3, "unique_count": 3}]


def test_hitungan_antar_alat_tidak_bocor(db_session):
    _add_result(db_session, instrument_id=1)
    _add_result(db_session, instrument_id=1)
    _add_result(db_session, instrument_id=2)

    rows = summarize_results(db_session)
    assert rows == [
        {"instrument_id": 1, "total": 2, "unique_count": 1},
        {"instrument_id": 2, "total": 1, "unique_count": 1},
    ]


def test_filter_rentang_tanggal(db_session):
    _add_result(db_session, received_at=datetime(2026, 8, 9, 23, 0, 0))
    _add_result(db_session, payload=_payload(value="6.0"),
                received_at=datetime(2026, 8, 10, 8, 0, 0))
    _add_result(db_session, payload=_payload(value="7.0"),
                received_at=datetime(2026, 8, 11, 1, 0, 0))

    rows = summarize_results(
        db_session,
        received_from=datetime(2026, 8, 10, 0, 0, 0),
        received_to=datetime(2026, 8, 10, 23, 59, 59, 999999),
    )
    assert rows == [{"instrument_id": 1, "total": 1, "unique_count": 1}]


def test_filter_status_dan_instrument(db_session):
    _add_result(db_session, instrument_id=1, send_status="sent")
    _add_result(db_session, instrument_id=1, payload=_payload(value="6.0"),
                send_status="failed")
    _add_result(db_session, instrument_id=2, send_status="sent")

    assert summarize_results(db_session, status="sent") == [
        {"instrument_id": 1, "total": 1, "unique_count": 1},
        {"instrument_id": 2, "total": 1, "unique_count": 1},
    ]
    assert summarize_results(db_session, instrument_id=1) == [
        {"instrument_id": 1, "total": 2, "unique_count": 2},
    ]


def test_rentang_kosong_mengembalikan_list_kosong(db_session):
    assert summarize_results(db_session) == []
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_db_result_summary.py -v`
Expected: FAIL — `ImportError: cannot import name 'summarize_results' from 'lib.db'`

- [ ] **Step 3: Tulis implementasi minimal**

Di `lib/db.py`, lebarkan import SQLAlchemy yang sudah ada di bagian atas file
dengan tiga nama tambahan (`cast`, `distinct`, `func`):

```python
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Integer,
    String,
    Text,
    JSON,
    Boolean,
    DateTime,
    Enum,
    Index,
    cast,
    distinct,
    func,
)
```

Lalu tambahkan fungsi tepat setelah `save_result()`:

```python
def summarize_results(
    session,
    status: str | None = None,
    instrument_id: int | None = None,
    received_from=None,
    received_to=None,
) -> list[dict]:
    """
    Rekap jumlah hasil per alat: total baris dan jumlah pemeriksaan unik.

    Unik dihitung dari sidik jari isi klinis, jadi kiriman ulang dari alat
    (isi persis sama) menyusut jadi satu. Baris yang sidik jarinya masih NULL
    diwakili oleh id-nya sendiri sehingga selalu terhitung unik — lebih aman
    menghitung lebih daripada menggabungkan data yang belum tentu sama. `id`
    yang dicor jadi teks tidak mungkin bertabrakan dengan sidik jari asli yang
    selalu 64 karakter heksadesimal.

    Session sengaja diterima dari pemanggil (bukan dibuat sendiri) supaya
    agregasinya bisa diuji langsung di atas SQLite in-memory.
    """
    identity = func.coalesce(
        TblResult.result_fingerprint, cast(TblResult.id, String)
    )

    q = session.query(
        TblResult.instrument_id,
        func.count(TblResult.id),
        func.count(distinct(identity)),
    )

    if status:
        q = q.filter(TblResult.send_status == status)
    if instrument_id:
        q = q.filter(TblResult.instrument_id == instrument_id)
    if received_from is not None:
        q = q.filter(TblResult.received_at >= received_from)
    if received_to is not None:
        q = q.filter(TblResult.received_at <= received_to)

    rows = (
        q.group_by(TblResult.instrument_id)
        .order_by(TblResult.instrument_id)
        .all()
    )

    return [
        {"instrument_id": inst_id, "total": total, "unique_count": uniq}
        for inst_id, total, uniq in rows
    ]
```

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_db_result_summary.py -v`
Expected: PASS — 11 tes lulus.

- [ ] **Step 5: Commit**

```bash
git add lib/db.py tests/test_db_result_summary.py
git commit -m "feat(db): summarize_results() hitung total dan pemeriksaan unik per alat"
```

---

### Task 4: Skrip migrasi + backfill

**Files:**
- Create: `scripts/migrate_result_fingerprint.py`
- Modify: `tests/test_migration.py`

**Interfaces:**
- Consumes: `lib.db.DBManager`, `lib.fingerprint.result_fingerprint` (Task 1).
- Produces: skrip CLI `python3 scripts/migrate_result_fingerprint.py`, keluar dengan kode 0 saat sukses. Tidak diimpor oleh kode lain.

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan ke `tests/test_migration.py`:

```python
def test_migrasi_fingerprint_punya_ddl_dan_penjaga_idempotensi():
    src = Path("scripts/migrate_result_fingerprint.py").read_text()
    assert "ADD COLUMN result_fingerprint CHAR(64) NULL" in src
    assert "idx_result_instrument_received" in src
    # Idempoten: cek INFORMATION_SCHEMA dulu sebelum ALTER/CREATE INDEX
    assert "information_schema.columns" in src
    assert "information_schema.statistics" in src
    # Backfill bertahap supaya tabel produksi tidak terkunci lama
    assert "BATCH_SIZE" in src
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_migration.py -v`
Expected: FAIL — `FileNotFoundError: scripts/migrate_result_fingerprint.py`

- [ ] **Step 3: Tulis implementasi minimal**

Buat `scripts/migrate_result_fingerprint.py`:

```python
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

ADD_COLUMN_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} CHAR(64) NULL"
CREATE_INDEX_SQL = (
    f"CREATE INDEX {INDEX} ON {TABLE} (instrument_id, received_at)"
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

            for row_id, result_json in rows:
                payload = result_json
                if isinstance(payload, (str, bytes)):
                    # Driver MySQL lama mengembalikan kolom JSON sebagai teks.
                    import json as _json
                    try:
                        payload = _json.loads(payload)
                    except Exception:
                        payload = None
                try:
                    fp = result_fingerprint(payload)
                except Exception:
                    # Baris rusak dibiarkan NULL; query rekap tetap
                    # menghitungnya sebagai baris unik.
                    continue

                conn.execute(
                    text(f"UPDATE {TABLE} SET {COLUMN} = :fp WHERE id = :id"),
                    {"fp": fp, "id": row_id},
                )
                total += 1

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
```

Catatan untuk implementer: `DBManager` mengekspos engine lewat properti
`db.engine` (`lib/db.py:297`), sama seperti yang dipakai
`scripts/migrate_result_protocol_width.py`.

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_migration.py -v`
Expected: PASS

- [ ] **Step 5: Periksa skrip bisa diimpor tanpa efek samping**

Run: `.venv/bin/python -c "import ast,pathlib; ast.parse(pathlib.Path('scripts/migrate_result_fingerprint.py').read_text()); print('sintaks OK')"`
Expected: `sintaks OK`

Jangan menjalankan `main()` — itu menyentuh database.

- [ ] **Step 6: Commit**

```bash
git add scripts/migrate_result_fingerprint.py tests/test_migration.py
git commit -m "feat(scripts): migrasi kolom result_fingerprint + backfill bertahap"
```

---

### Task 5: Endpoint `GET /api/results/summary`

**Files:**
- Modify: `services/web_console/api.py` — model baru di dekat `ResultResponse` (`api.py:305`), endpoint baru setelah `list_results()` (`api.py:1272`)
- Test: `tests/test_api_results_summary.py`

**Interfaces:**
- Consumes: `lib.db.summarize_results(session, ...)` (Task 3), `_parse_filter_date()` yang sudah ada di `api.py:1197`.
- Produces: `GET /api/results/summary?status=&instrument_id=&date_from=&date_to=` → JSON array `[{instrument_id, instrument_name, total, unique_count, duplicate_count}]`, diurutkan berdasarkan nama alat (A→Z).

- [ ] **Step 1: Tulis test yang gagal**

Buat `tests/test_api_results_summary.py`:

```python
"""Kontrak HTTP endpoint rekap pemeriksaan per alat."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from services.web_console.api import app


def _mock_instruments(*pairs):
    """pairs: (id, name) → objek dengan atribut id & name."""
    out = []
    for inst_id, name in pairs:
        obj = MagicMock()
        obj.id = inst_id
        obj.name = name
        out.append(obj)
    return out


def test_summary_menghitung_duplikat_dan_menamai_alat():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = _mock_instruments(
            (1, "BC-5150"), (3, "Glory 127")
        )
        mock_sum.return_value = [
            {"instrument_id": 1, "total": 142, "unique_count": 118},
            {"instrument_id": 3, "total": 51, "unique_count": 51},
        ]
        r = client.get("/api/results/summary")

    assert r.status_code == 200
    assert r.json() == [
        {"instrument_id": 1, "instrument_name": "BC-5150",
         "total": 142, "unique_count": 118, "duplicate_count": 24},
        {"instrument_id": 3, "instrument_name": "Glory 127",
         "total": 51, "unique_count": 51, "duplicate_count": 0},
    ]


def test_summary_urut_nama_alat():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = _mock_instruments(
            (1, "Zeta"), (2, "Alpha")
        )
        mock_sum.return_value = [
            {"instrument_id": 1, "total": 5, "unique_count": 5},
            {"instrument_id": 2, "total": 3, "unique_count": 3},
        ]
        r = client.get("/api/results/summary")

    names = [row["instrument_name"] for row in r.json()]
    assert names == ["Alpha", "Zeta"]


def test_summary_alat_tanpa_nama_pakai_placeholder():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = []
        mock_sum.return_value = [
            {"instrument_id": 9, "total": 2, "unique_count": 1},
        ]
        r = client.get("/api/results/summary")

    assert r.json()[0]["instrument_name"] == "#9"


def test_summary_rentang_kosong():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = []
        mock_sum.return_value = []
        r = client.get("/api/results/summary")

    assert r.status_code == 200
    assert r.json() == []


def test_summary_tanggal_tidak_valid_ditolak():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB:
        MockDB.return_value.get_session.return_value = MagicMock()
        r = client.get("/api/results/summary?date_from=bukan-tanggal")

    assert r.status_code == 400


def test_summary_meneruskan_seluruh_filter():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB, \
         patch("services.web_console.api.summarize_results") as mock_sum:
        session = MagicMock()
        MockDB.return_value.get_session.return_value = session
        session.query.return_value.all.return_value = []
        mock_sum.return_value = []
        client.get("/api/results/summary"
                   "?status=failed&instrument_id=2"
                   "&date_from=2026-08-10&date_to=2026-08-10")

    kwargs = mock_sum.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["instrument_id"] == 2
    # date_to tanggal polos digeser ke akhir hari oleh _parse_filter_date
    assert kwargs["received_to"].hour == 23
    assert kwargs["received_to"].minute == 59
    assert kwargs["received_from"].hour == 0
```

- [ ] **Step 2: Jalankan test, pastikan gagal**

Run: `.venv/bin/python -m pytest tests/test_api_results_summary.py -v`
Expected: FAIL — HTTP 404, karena rutenya belum ada.

- [ ] **Step 3: Tulis implementasi minimal**

Di `services/web_console/api.py`, tambahkan `summarize_results` ke daftar
import dari `lib.db` (blok di `api.py:37`), dengan menjaga urutan alfabet yang
sudah dipakai di sana — sisipkan di antara `set_setting` dan
`update_result_status`:

```python
    set_setting,
    summarize_results,
    update_result_status,
```

Tambahkan model tepat setelah `ResultResponse` (`api.py:315`):

```python
class ResultSummaryRow(BaseModel):
    instrument_id: int
    instrument_name: str
    total: int
    unique_count: int
    duplicate_count: int
```

Tambahkan endpoint tepat setelah `list_results()` (sebelum
`retry_result()` di `api.py:1274`):

```python
@app.get("/api/results/summary", response_model=list[ResultSummaryRow])
async def results_summary(
    status: str = Query(default=None, description="Filter: pending, sent, failed"),
    instrument_id: int = Query(default=None),
    date_from: str = Query(default=None, description="ISO8601 date"),
    date_to: str = Query(default=None, description="ISO8601 date"),
    x_api_key: str = Header(None),
):
    """
    Rekap jumlah pemeriksaan per alat pada rentang filter yang sama dengan
    /api/results.

    Sengaja tanpa limit/offset: ringkasan menghitung seluruh baris dalam
    rentang, bukan halaman yang sedang dibuka.
    """
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        rows = summarize_results(
            session,
            status=status,
            instrument_id=instrument_id,
            received_from=(
                _parse_filter_date(date_from, "date_from") if date_from else None
            ),
            received_to=(
                _parse_filter_date(date_to, "date_to", end_of_day=True)
                if date_to
                else None
            ),
        )

        names = {
            inst.id: inst.name for inst in session.query(TblInstrument).all()
        }

        out = [
            ResultSummaryRow(
                instrument_id=r["instrument_id"],
                instrument_name=names.get(
                    r["instrument_id"], f"#{r['instrument_id']}"
                ),
                total=r["total"],
                unique_count=r["unique_count"],
                duplicate_count=r["total"] - r["unique_count"],
            )
            for r in rows
        ]
        out.sort(key=lambda row: row.instrument_name.lower())
        return out
    finally:
        session.close()
```

**Penting soal urutan rute:** letakkan `/api/results/summary` **sebelum** rute
apa pun yang berbentuk `/api/results/{result_id}`. FastAPI mencocokkan rute
sesuai urutan pendaftaran; kalau terbalik, `summary` akan tertangkap sebagai
`result_id` dan gagal validasi integer.

- [ ] **Step 4: Jalankan test, pastikan lulus**

Run: `.venv/bin/python -m pytest tests/test_api_results_summary.py -v`
Expected: PASS — 6 tes lulus.

- [ ] **Step 5: Pastikan endpoint lama tidak berubah**

Run: `.venv/bin/python -m pytest tests/ -q -k "api or result"`
Expected: tidak ada kegagalan baru dibanding baseline.

- [ ] **Step 6: Commit**

```bash
git add services/web_console/api.py tests/test_api_results_summary.py
git commit -m "feat(web): endpoint rekap jumlah pemeriksaan per alat"
```

---

### Task 6: Panel ringkasan di Result Monitor

**Files:**
- Modify: `services/web_console/templates/results.html`

**Interfaces:**
- Consumes: `GET /api/results/summary` (Task 5); helper yang sudah ada di web console: `App.api(path)`, `App.buildQuery(params)`, `App.escapeHtml(s)`, `App.toast(msg, level)`.
- Produces: tidak ada — ini lapisan paling luar.

- [ ] **Step 1: Tambahkan card panel di atas card tabel**

Di `services/web_console/templates/results.html`, sisipkan tepat setelah
`<div class="page-header">…</div>` (setelah baris 9) dan **sebelum**
`<div class="card">` yang memuat tabel:

```html
<div class="card" style="margin-bottom:16px">
  <div class="card-header">
    <h2 class="card-title" style="margin:0">Ringkasan Pemeriksaan</h2>
  </div>
  <div class="card-body no-padding">
    <div class="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Alat</th>
            <th class="text-right">Total</th>
            <th class="text-right">Unik</th>
            <th class="text-right">Kiriman ulang</th>
          </tr>
        </thead>
        <tbody id="summary-tbody">
          <tr><td colspan="4" class="text-center text-muted" style="padding:16px">Memuat...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
```

Catatan: kelas `text-right` mungkin belum ada di `static/css/style.css`. Cek
dulu dengan `grep -n "text-right" services/web_console/static/css/style.css`.
Kalau tidak ada, tambahkan satu aturan di bagian utilitas file itu:

```css
.text-right { text-align: right; }
```

- [ ] **Step 2: Tambahkan `loadSummary()` di blok script**

Di dalam IIFE pada `{% block scripts %}`, tambahkan tepat setelah fungsi
`loadResults` selesai didefinisikan (setelah baris `};` yang menutupnya):

```javascript
  function summaryFilters() {
    const params = {};
    const status = document.getElementById('filter-status').value;
    const instId = document.getElementById('filter-instrument').value;
    const from = document.getElementById('filter-from').value;
    const to = document.getElementById('filter-to').value;
    if (status) params.status = status;
    if (instId) params.instrument_id = instId;
    if (from) params.date_from = from;
    if (to) params.date_to = to;
    return params;
  }

  function summaryMessage(text) {
    document.getElementById('summary-tbody').innerHTML =
      `<tr><td colspan="4" class="text-center text-muted" style="padding:16px">${text}</td></tr>`;
  }

  window.loadSummary = async function() {
    try {
      const rows = await App.api('/api/results/summary' + App.buildQuery(summaryFilters()));
      renderSummary(rows);
    } catch (err) {
      // Kegagalan panel tidak boleh ikut mematikan tabel hasil di bawahnya.
      console.error(err);
      summaryMessage('Ringkasan tidak tersedia');
    }
  };

  function renderSummary(rows) {
    if (!rows || rows.length === 0) {
      summaryMessage('Tidak ada data pada rentang ini');
      return;
    }

    const sum = (key) => rows.reduce((acc, r) => acc + r[key], 0);
    const dupCell = (n) => n > 0
      ? `<span class="badge badge-yellow">${n}</span>`
      : '0';

    const body = rows.map(r => `<tr>
      <td>${App.escapeHtml(r.instrument_name)}</td>
      <td class="text-right">${r.total}</td>
      <td class="text-right">${r.unique_count}</td>
      <td class="text-right">${dupCell(r.duplicate_count)}</td>
    </tr>`).join('');

    // TOTAL dijumlah per alat, bukan hitung-ulang lintas alat: sidik jari yang
    // sama dari dua alat berbeda tetap dua pemeriksaan yang berbeda.
    const footer = `<tr style="font-weight:600">
      <td>TOTAL</td>
      <td class="text-right">${sum('total')}</td>
      <td class="text-right">${sum('unique_count')}</td>
      <td class="text-right">${sum('duplicate_count')}</td>
    </tr>`;

    document.getElementById('summary-tbody').innerHTML = body + footer;
  }
```

- [ ] **Step 3: Panggil `loadSummary()` di tempat yang tepat**

Ubah handler filter di HTML — keempat kontrol filter (baris 14, 20, 23, 24)
saat ini memanggil `loadResults()`. Ganti menjadi `applyFilters()` pada
keempatnya, lalu tambahkan fungsi itu di blok script:

```javascript
  window.applyFilters = function() {
    currentPage = 0;   // filter berubah → kembali ke halaman pertama
    loadResults();
    loadSummary();
  };
```

`changePage()` **tidak** diubah: pindah halaman tidak mengubah ringkasan, jadi
tidak perlu diambil ulang.

Terakhir, ganti dua baris pemanggilan di akhir IIFE:

```javascript
  loadInstruments();
  loadResults();
  loadSummary();
```

- [ ] **Step 4: Verifikasi di konsol dev**

Jalankan web console dev di port 8001 (jangan sentuh produksi di :8000):

```bash
.venv/bin/python -m uvicorn services.web_console.api:app --host 127.0.0.1 --port 8001
```

`services/web_console/main.py` membaca host/port dari `config.yaml` dan tidak
punya flag CLI, jadi untuk pemeriksaan visual ini uvicorn dipanggil langsung.
Konsekuensinya zona waktu lab tidak dipasang lebih dulu seperti pada `main.py`
— tidak masalah di sini karena panel yang diperiksa hanya berisi angka, bukan
jam.

Buka `http://127.0.0.1:8001/results` dan pastikan:
- panel muncul di atas tabel dengan angka per alat dan baris TOTAL
- mengubah filter tanggal/alat/status mengubah angka panel **dan** isi tabel
- pindah halaman (« ») mengubah tabel tapi **tidak** mengubah panel
- tidak ada error di console browser

Kalau database dev belum punya kolom `result_fingerprint`, jalankan migrasinya
dulu terhadap **database dev**, bukan produksi.

Matikan server dev setelah selesai.

- [ ] **Step 5: Cache-bust aset kalau perlu**

Perubahan ada di template, bukan di `app.js`/`style.css`, jadi biasanya tidak
perlu. Tapi kalau Step 1 menambahkan aturan `.text-right` ke `style.css`,
naikkan parameter versi pada `<link>`/`<script>` di
`services/web_console/templates/base.html` — pola yang sama dipakai commit
`c051b6f`.

- [ ] **Step 6: Commit**

```bash
git add services/web_console/templates/results.html services/web_console/static/css/style.css services/web_console/templates/base.html
git commit -m "feat(web): panel ringkasan jumlah pemeriksaan di Result Monitor"
```

---

### Task 7: Verifikasi menyeluruh + dokumentasi deploy

**Files:**
- Modify: `INSTALL.md` (bagian migrasi/upgrade)

**Interfaces:**
- Consumes: seluruh task sebelumnya.
- Produces: tidak ada.

- [ ] **Step 1: Jalankan seluruh test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: jumlah `failed` tetap 6 (pre-existing), `passed` bertambah sekitar 21
dari test baru. Kalau ada kegagalan baru, perbaiki dulu — jangan lanjut.

- [ ] **Step 2: Pastikan endpoint lama benar-benar utuh**

Run: `git diff main --stat` lalu `git diff main -- services/web_console/api.py`
Expected: pada `api.py` hanya ada penambahan (import `summarize_results`, model
`ResultSummaryRow`, fungsi `results_summary`). Tidak ada baris `list_results()`
atau `ResultResponse` yang berubah. Kalau ada, kembalikan.

- [ ] **Step 3: Catat urutan migrasi di INSTALL.md**

Di INSTALL.md §7 "Prosedur update yang aman" (sekitar baris 239-241) sudah ada
langkah 3 yang menjalankan migrasi sebelum langkah 4 `deploy.sh`. Tambahkan
satu baris di daftar itu:

```bash
# 3. Migrasi DB baru? Semua idempotent, aman dijalankan tiap kali update:
sudo -u midlab /opt/midlab/.venv/bin/python /opt/midlab/scripts/migrate_lis_api.py
sudo -u midlab /opt/midlab/.venv/bin/python /opt/midlab/scripts/migrate_tap_session.py
sudo -u midlab /opt/midlab/.venv/bin/python /opt/midlab/scripts/migrate_result_fingerprint.py
```

Lalu tambahkan catatan tepat di bawah blok itu:

> `migrate_result_fingerprint.py` menambah kolom `tbl_result.result_fingerprint`
> untuk rekap jumlah pemeriksaan di Result Monitor. Urutan langkah 3 sebelum
> langkah 4 wajib dipatuhi: model ORM baru menyebut kolom ini pada setiap
> INSERT, jadi kalau kode sudah aktif sementara kolomnya belum ada, hasil dari
> alat gagal tersimpan. Backfill dicicil 500 baris per transaksi; baris yang
> belum ter-backfill tetap ikut terhitung, hanya saja selalu dianggap
> pemeriksaan tersendiri.

Jangan menambah bab baru — cukup menumpang pada daftar yang sudah ada.

- [ ] **Step 4: Commit**

```bash
git add INSTALL.md
git commit -m "docs: urutan migrasi result_fingerprint sebelum deploy"
```

- [ ] **Step 5: Ringkas hasil ke pengguna**

Laporkan: jumlah test sebelum dan sesudah, daftar file yang berubah, dan
pengingat bahwa **migrasi harus dijalankan sebelum `scripts/deploy.sh`** saat
naik ke produksi. Jangan menjalankan deploy ke produksi tanpa diminta.

---

## Catatan yang berlaku di semua task

- **Jangan** menjalankan `scripts/deploy.sh` atau menyentuh `/opt/midlab`.
  `/opt/midlab` bukan git checkout; deploy dijalankan terpisah oleh pengguna.
- **Jangan** menjalankan migrasi terhadap database produksi.
- `/var/log/midlab` milik user `midlab` — jangan mengubah kepemilikannya.
- Kalau sebuah test gagal karena alasan yang sudah ada sebelum plan ini
  dijalankan (6 kegagalan baseline), catat dan lanjut; jangan mencoba
  memperbaikinya sebagai bagian dari plan ini.
