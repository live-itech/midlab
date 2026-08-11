# Rekap Jumlah Pemeriksaan per Alat + Deduplikasi Kiriman Ulang

**Tanggal:** 2026-08-10 (dilanjutkan 2026-08-11)
**Status:** menunggu review
**Ruang lingkup:** Result Monitor (web console), `tbl_result`, jalur simpan hasil

---

## 1. Masalah

Result Monitor hanya menampilkan baris mentah `tbl_result`. Tidak ada cara
menjawab pertanyaan operasional yang paling sering muncul: **berapa pemeriksaan
yang benar-benar dikerjakan alat X pada rentang tanggal Y?**

Angka `COUNT(*)` polos tidak bisa dipakai karena alat lab kerap mengirim ulang
hasil yang sama — saat operator menekan tombol *transmit* dua kali, saat koneksi
terputus lalu alat mengulang, atau saat alat menyapu ulang daftar hasilnya.
Kiriman ulang itu tersimpan sebagai baris baru dan menggelembungkan hitungan.

Yang dibutuhkan: bedakan **kiriman ulang** dari **pemeriksaan baru**.

- Isi datanya persis sama → satu pemeriksaan, sisanya kiriman ulang.
- Sample ID sama tapi hasilnya berbeda → dua pemeriksaan (sampel diperiksa ulang
  dan menghasilkan nilai lain).

---

## 2. Keputusan yang sudah dikunci

| Pertanyaan | Keputusan |
|---|---|
| Di mana angkanya muncul | Panel ringkasan di atas tabel Result Monitor |
| Apa yang jadi sidik jari | Sample ID + isi hasil tes (`test_code`, `value`, `unit`, `flag`) |
| Kapan sidik jari dihitung | Saat hasil disimpan, tersimpan di kolom baru `tbl_result.result_fingerprint` |
| Angka yang ditampilkan | Total / Unik / Kiriman ulang, per alat, plus baris TOTAL |
| Cakupan filter | Mengikuti seluruh filter aktif termasuk Status |
| Baris cacat (tanpa `sample_id`, gagal parse) | Tetap ikut dihitung apa adanya |
| Bentuk API | Endpoint rekap terpisah, `/api/results` yang lama tidak diubah |

---

## 3. Sidik jari (fingerprint)

Modul baru `lib/fingerprint.py`, satu fungsi murni tanpa ketergantungan ke DB
maupun HTTP:

```python
def result_fingerprint(result_json: dict | None) -> str:
    """SHA-256 hex dari isi klinis sebuah hasil pemeriksaan."""
```

### Aturan normalisasi

1. Kalau `result_json` bukan `dict`, perlakukan sebagai `{}`.
2. `sample_id` = `str(result_json["specimen"]["sample_id"])` yang di-`strip()`.
   Huruf besar/kecil **tidak** diseragamkan — sebagian alat memakai ID
   case-sensitive, menyeragamkannya berisiko menggabungkan sampel berbeda.
3. Tiap item di `results` diperas jadi empat field, masing-masing `str().strip()`:
   `test_code`, `value`, `unit`, `flag`.
4. Tiap tes menjadi list empat elemen dengan urutan tetap
   `[test_code, value, unit, flag]`, dan daftarnya **diurutkan sebagai list**
   (`sorted(rows)`, perbandingan leksikografis elemen per elemen) supaya urutan
   pengiriman alat tidak berpengaruh.
5. Payload di-serialisasi kanonik lalu di-hash:

```python
rows = sorted(
    [str(t.get(k, "")).strip() for k in ("test_code", "value", "unit", "flag")]
    for t in (result_json.get("results") or [])
)
payload = {"sample_id": sample_id, "results": rows}
canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

### Yang sengaja diabaikan

`message_id`, `message_datetime`, `mid_version`, `protocol`, `instrument_id`,
seluruh `patient.*`, `order.*`, `comments`, `parse_errors`, serta
`reference_range` dan `status` pada tiap tes.

Alasannya: semuanya berubah antar kiriman tanpa isi pemeriksaan ikut berubah.
`message_id` di-generate baru tiap parsing, `message_datetime` mencatat jam
transmisi, dan `reference_range` ikut berubah bila nilai rujukan di alat
disetel ulang. Kalau ikut di-hash, kiriman ulang tidak akan pernah terdeteksi.

### Contoh

```
kiriman #1  10:30  sample_id=ABC123  WBC=5.2 10^3/uL N | RBC=4.1 10^6/uL N
kiriman #2  10:35  sample_id=ABC123  RBC=4.1 10^6/uL N | WBC=5.2 10^3/uL N
→ fingerprint sama (urutan tes tidak dihitung) → 1 unik, 1 kiriman ulang

kiriman #3  11:02  sample_id=ABC123  WBC=6.0 10^3/uL H | RBC=4.1 10^6/uL N
→ fingerprint berbeda → pemeriksaan tersendiri
```

---

## 4. Perubahan skema

```sql
ALTER TABLE tbl_result ADD COLUMN result_fingerprint CHAR(64) NULL;
CREATE INDEX idx_result_instrument_received ON tbl_result (instrument_id, received_at);
```

Kolom **nullable** dengan sengaja: `NULL` berarti "belum dihitung", dan query
rekap sudah menangani kondisi itu (lihat §6). Ini membuat migrasi tidak
mengharuskan seluruh data lama selesai di-backfill sebelum fitur bisa dipakai.

Index dipasang pada `(instrument_id, received_at)` — dua kolom yang dipakai
menyaring; `result_fingerprint` sendiri tidak perlu di-index karena hanya
menjadi argumen `COUNT(DISTINCT)`, bukan predikat pencarian.

`lib/db.py: TblResult` ditambah satu baris kolom yang sama.

### Migrasi: `scripts/migrate_result_fingerprint.py`

Mengikuti pola `scripts/migrate_result_protocol_width.py` — idempoten, aman
dijalankan berkali-kali:

1. Cek `INFORMATION_SCHEMA`; tambah kolom bila belum ada.
2. Cek index; buat bila belum ada.
3. Backfill baris `result_fingerprint IS NULL` secara **bertahap 500 baris per
   commit**, supaya tabel produksi yang sudah besar tidak dikunci lama dan
   memori tidak meledak.
4. Cetak ringkasan: berapa baris terisi, berapa yang tersisa NULL.

---

## 5. Jalur tulis

Satu-satunya pintu masuk hasil adalah `lib/db.py:419 save_result()`. Perubahan
terbatas di situ:

```python
new_result = TblResult(
    ...,
    result_fingerprint=_safe_fingerprint(result_json),
)
```

`_safe_fingerprint()` membungkus pemanggilan dengan `try/except` dan
mengembalikan `None` bila hashing gagal. **Kegagalan menghitung sidik jari tidak
boleh menggagalkan penyimpanan hasil alat** — fitur laporan tidak boleh
menjatuhkan fungsi inti middleware. Baris yang sidik jarinya `None` tetap
terhitung, hanya saja selalu dianggap unik.

Tanda tangan `save_result()` tidak berubah, jadi seluruh pemanggil (semua
ProtocolModule) tidak perlu disentuh.

---

## 6. API rekap

```
GET /api/results/summary
    ?status=&instrument_id=&date_from=&date_to=
```

Parameter filter sama persis dengan `/api/results`, memakai ulang
`_parse_filter_date()` yang sudah ada. `limit`/`offset` sengaja tidak ada —
ringkasan selalu menghitung **seluruh** baris dalam rentang, bukan halaman aktif.

### Query

```sql
SELECT instrument_id,
       COUNT(*) AS total,
       COUNT(DISTINCT COALESCE(result_fingerprint, CONCAT('row:', id))) AS unik
FROM tbl_result
WHERE <filter yang sama dengan /api/results>
GROUP BY instrument_id
```

`COALESCE(..., CONCAT('row:', id))` menangani baris yang sidik jarinya masih
`NULL`: tanpa itu `COUNT(DISTINCT)` akan mengabaikan NULL dan hitungan unik
menjadi terlalu kecil. Dengan ini, baris yang belum ter-fingerprint dihitung
sebagai pemeriksaan tersendiri — pilihan yang aman (tidak pernah menggabungkan
data yang belum tentu sama) sekaligus membuat urutan deploy tidak kritis.

Nama alat diambil lewat satu query kecil terpisah ke `tbl_instrument` lalu
digabung di Python. Lebih sederhana daripada `JOIN` yang harus ikut masuk
`GROUP BY`.

### Response

```json
[
  {"instrument_id": 1, "instrument_name": "BC-5150",
   "total": 142, "unique_count": 118, "duplicate_count": 24},
  {"instrument_id": 3, "instrument_name": "Glory 127",
   "total": 51, "unique_count": 51, "duplicate_count": 0}
]
```

`duplicate_count = total - unique_count`, dihitung di server supaya klien tidak
perlu tahu rumusnya. Urutan baris mengikuti nama alat (A→Z) supaya panel tidak
lompat-lompat saat filter diubah. Alat tanpa data pada rentang itu tidak muncul.

Baris TOTAL dijumlahkan di frontend dari baris-baris per alat — **bukan** dari
`COUNT(DISTINCT)` global. Sidik jari yang sama dari dua alat berbeda adalah dua
pemeriksaan yang berbeda, dan penjumlahan per alat menjaga hal itu tetap benar.

---

## 7. Panel di Result Monitor

Card baru tepat di atas card tabel di `results.html`, memakai ulang kelas yang
sudah ada (`card`, `table-wrapper`, `badge`) supaya konsisten dengan gaya Apple
yang dipakai konsol.

```
┌─ Ringkasan Pemeriksaan ─────────────────────────────┐
│  Alat            Total    Unik    Kiriman ulang     │
│  ──────────────────────────────────────────────     │
│  BC-5150           142     118      ⟨24⟩            │
│  BS-200E            38      35      ⟨3⟩             │
│  Glory 127          51      51        0             │
│  ──────────────────────────────────────────────     │
│  TOTAL             231     204       27             │
└─────────────────────────────────────────────────────┘
```

Angka "Kiriman ulang" yang lebih besar dari nol diberi badge kuning agar langsung
terlihat; nol ditulis polos tanpa badge.

### Perilaku

- `loadSummary()` dipanggil berdampingan dengan `loadResults()` setiap kali
  filter berubah — dua request terpisah, berjalan paralel.
- `changePage()` **hanya** memanggil `loadResults()`. Ringkasan tidak berubah
  antar halaman, jadi tidak perlu diambil ulang.
- Saat memuat: "Memuat…". Saat kosong: "Tidak ada data pada rentang ini".
- Bila request ringkasan gagal, panel menampilkan "Ringkasan tidak tersedia" dan
  tabel hasil **tetap termuat normal**. Kegagalan fitur baru tidak boleh
  membuat Result Monitor yang lama ikut mati.

---

## 8. Rencana tes

**`tests/test_result_fingerprint.py`** — murni, tanpa DB:

- dua payload identik → sidik jari sama
- urutan tes dibalik → sidik jari sama
- `message_id` / `message_datetime` berbeda → sidik jari sama
- satu nilai tes berubah → sidik jari berbeda
- `flag` berubah (N→H) → sidik jari berbeda
- `sample_id` berbeda → sidik jari berbeda
- spasi berlebih di sekitar nilai → sidik jari sama
- `None` dan `{}` → mengembalikan hex 64 karakter, tidak melempar exception

**`tests/test_api_results_summary.py`** — `TestClient` + `DBManager` di-mock,
mengikuti pola `tests/test_api_lis_events.py`:

- bentuk response dan `duplicate_count = total - unique_count`
- rentang kosong → `[]`
- `date_from` tak valid → HTTP 400
- filter status diteruskan ke query

**`tests/test_migration.py`** — tambah assert bahwa
`scripts/migrate_result_fingerprint.py` memuat `ADD COLUMN result_fingerprint`
dan penjaga idempotensinya.

Verifikasi akhir: `.venv/bin/python -m pytest`, dibandingkan dengan baseline
545 passed / 6 failed (pre-existing) / 3 skipped.

---

## 9. Konsekuensi yang diterima

- **Pemeriksaan ulang dengan hasil yang sama persis terhitung sebagai kiriman
  ulang.** Bila sampel diperiksa dua kali dan seluruh nilainya identik sampai
  digit terakhir, keduanya menyatu jadi satu. Tidak bisa dibedakan dari data yang
  ada, dan pada praktiknya nilai yang identik sempurna sangat jarang.
- **Baris cacat yang isinya sama-sama kosong menyatu.** Beberapa baris dengan
  `result_json` kosong akan dianggap saling kiriman ulang. Ini konsekuensi
  langsung dari keputusan "tetap ikut dihitung apa adanya", dan jumlahnya
  seharusnya nol pada operasi normal.
- **Alat yang mengirim satu analit per pesan** (mis. CUSTOM_GLORY_127) tetap
  menghasilkan satu baris `tbl_result` per analit, jadi satu sampel berisi
  sepuluh tes terhitung sepuluh. Ini konsisten dengan makna "jumlah data yang
  diterima dari alat", dan dedup tetap bekerja per analit.

---

## 10. Yang sengaja tidak dikerjakan

- `/api/results` dan `ResultResponse` tidak diubah sedikit pun.
- `lib/models.py`, seluruh ProtocolModule, LisBridgeService, dan
  ResultSenderService tidak disentuh.
- Tidak ada penandaan "duplikat dari #123" per baris tabel, tidak ada penyaringan
  duplikat dari tampilan, tidak ada export. Tiga hal itu mudah ditambahkan
  belakangan di atas kolom sidik jari yang sama bila memang dibutuhkan.
- Tidak ada penghapusan otomatis baris duplikat. Data mentah dari alat tetap
  utuh; fitur ini hanya menghitung.

---

## 11. Urutan deploy

1. **Jalankan migrasi lebih dulu**, dari repo dev:
   `python3 scripts/migrate_result_fingerprint.py`
2. Baru `scripts/deploy.sh` untuk kode barunya.

Urutan ini wajib. Model ORM yang baru menyebut kolom `result_fingerprint` pada
setiap `INSERT`; bila kode naik duluan sementara kolomnya belum ada, penyimpanan
hasil alat akan gagal dan data dari alat hilang.

Migrasi aman dijalankan saat service produksi hidup — kolomnya nullable, dan
service lama tidak menulis maupun membaca kolom itu.

**Rollback:** revert kode saja. Kolom `result_fingerprint` boleh ditinggal di
tabel; service versi lama mengabaikannya.
