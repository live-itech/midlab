# Driver HL7_MINDRAY_BS240PRO_360E_240E — Design

Tanggal: 2026-10-17
Status: disetujui, siap implementasi

## Ringkasan

Driver protocol baru `HL7_MINDRAY_BS240PRO_360E_240E` untuk chemistry analyzer Mindray
BS-240Pro (berlaku juga untuk BS-240E dan BS-360E — satu manual yang sama).

Lingkup:

- **Unidirectional** — terima `ORU^R01` (hasil pasien / QC / kalibrasi), balas `ACK^R01`.
- **Bidirectional query** — alat kirim `QRY^Q02`, MidLab balas `QCK^Q02` + `DSR^Q03`,
  alat balas `ACK^Q03`. Termasuk group download dan pembatalan `CAN`.
- **Bidirectional broadcast** — MidLab dorong `DSR^Q03` dengan MSH-15 = `P`
  (resmi di manual ini).
- **QC otomatis** — pesan QC (MSH-16 = 2) dikenali, diurai per kontrol, dan
  dikirim LisBridge ke EazyLab `POST /qc-results` lewat blok `qc` yang sudah ada.
- **Koneksi klien atau server** — diatur kolom `tbl_instrument.connection`,
  ditangani TCPSocketService yang sudah ada (tanpa kode khusus di driver).

Di luar lingkup: antarmuka ASTM (bab 2 manual), perubahan web console,
perubahan apa pun pada driver `HL7_MINDRAY_BS200E`.

## Sumber spesifikasi

1. *BS-360E & BS-240Pro & BS-240E LIS Interface Manual V1.0 (EN)*, 82 halaman.
   Bab 1 = HL7 v2.3.1 + MLLP, bab 2 = ASTM E1394-97 (tidak dipakai).
2. Log komunikasi BS-360E di lapangan (Parung, 7 Okt 2024, middleware lain):
   64 `ORU^R01` (36 pasien, 28 QC), 37 `QRY^Q02` per barcode, 36 `DSR^Q03`,
   1 `QCK` NF. Log ini membuktikan bab HL7 manual berlaku untuk keluarga ini.

Bab HL7 manual ini secara substansi sama dengan Host Interface Manual v6.0 yang
dipakai driver BS-200E. Perbedaan yang relevan dirangkum di bawah.

### Perbedaan terhadap BS-200E

| Aspek | BS-200E (driver lama) | BS-240Pro (manual + log) |
|---|---|---|
| QC OBR-2 | nomor urut tes (`22`) | **kode tes** sama dengan OBX-3 pasien (`GLU`, `TBIL`) |
| QC OBR-3 | nama tes, butuh tabel padanan (`T-Bill`→`T-BIL`) | nama tes (`Glu-G`), tidak dipakai sebagai kode |
| Waktu QC | OBR-6 | OBR-6 mulai, OBR-7 apply, **OBR-8 selesai per tes** |
| Kontrol per pesan | satu | **satu atau lebih**, field 12–15 & 17–20 berformat `V1^V2^…` |
| Flag QC alat | — | OBR-28 (`RGTE,1-3s,2.7s`) |
| Broadcast | di luar spesifikasi (WARNING) | resmi: DSR^Q03 dengan **MSH-15 = `P`** |
| DSP-26 di log | — | `Serum` (huruf awal kapital) |

### Temuan lapangan: level kontrol

Di log, OBR-17 selalu `M` untuk kontrol bernama `CTRL L1` maupun `CTRL L2` (lot
berbeda). Setting level di alat tidak dapat dipercaya, sehingga level diambil
dari nama kontrol lebih dulu (lihat "Level kontrol").

## Arsitektur

Paket baru `protocols/mindray_bs240pro/` berisi subclass tipis dari driver
BS-200E. Hanya bagian yang berbeda yang ditulis ulang; MLLP, ORU pasien,
QRY/QCK/DSR, group download, dan ACK dipakai ulang.

| File | Isi |
|---|---|
| `constants.py` | Re-export konstanta BS-200E + `PROTOCOL_NAME = "HL7_MINDRAY_BS240PRO_360E_240E"`, `DEFAULT_MODEL = "BS-240Pro"`, `MSH15_LIS_DOWNLOAD = "P"`, posisi field QC, pola level |
| `parser.py` | `BS240ProParser(MindrayParser)` — override `parse_obr_qc` (multi-kontrol) |
| `builder.py` | `BS240ProBuilder(MindrayBuilder)` — MSH-15 `P` untuk broadcast, DSP-26 kapital, model default |
| `module.py` | `MindrayBS240ProModule` — override rakitan QC, `format_order`, tambah `split_results` |

### Jebakan `load_module`

`protocols.base.load_module` memilih class **pertama hasil `dir()`** (urut
alfabet) yang mewarisi `BaseProtocolModule`. Bila `module.py` mengimpor
`MindrayBS200EModule` sebagai nama langsung, `MindrayBS200EModule` <
`MindrayBS240ProModule` sehingga driver BS-200E yang ter-load. Karena itu induk
diimpor lewat alias modul:

```python
from protocols.mindray_bs200e import module as _bs200e

class MindrayBS240ProModule(_bs200e.MindrayBS200EModule): ...
```

Hal yang sama berlaku untuk parser/builder bila nanti ada loader serupa. Test
memastikan `load_module("HL7_MINDRAY_BS240PRO_360E_240E")` mengembalikan
`MindrayBS240ProModule`.

### Pendaftaran

`protocols/base.py`:

```python
"HL7_MINDRAY_BS240PRO_360E_240E": "protocols.mindray_bs240pro.module",
```

- Panjang nama 30 karakter, di bawah `protocol VARCHAR(50)` → **tanpa migrasi DB**.
- Nama mewakili tiga seri (BS-240Pro, BS-360E, BS-240E); paket Python
  `mindray_bs240pro`.
- `is_mllp_protocol()` → `True` (prefiks `HL7`), framing MLLP otomatis.
- `ResultPusher._normalize_protocol()` → keluarga `HL7` lewat prefiks.
- Muncul otomatis di `/api/protocols` dan dropdown web console.

### Hook `split_results` di TCPSocketService

Satu pesan QC bisa membawa beberapa kontrol, sedangkan kontrak
`/qc-results` (dan `build_qc_payload`) adalah satu baris `tbl_result` = satu
lot+level. Receiver mendapat hook opsional, pola yang sama dengan
`should_store_result`:

```python
# services/tcp_socket/receiver.py — _parse_and_save
result_dict = self._protocol.parse(raw_bytes, instrument_dict)
# should_store_result dicek sekali per pesan (tidak berubah)
split = getattr(self._protocol, "split_results", None)
rows = split(result_dict) if split is not None else [result_dict]
for row in rows:
    save_result(self._config.id, self._config.protocol, raw_hex, row)
```

- Driver tanpa hook → perilaku identik (satu baris).
- Setiap baris menyimpan `raw_data` yang sama (pesan asli utuh).
- Kegagalan simpan satu baris dicatat ERROR dan tidak menghentikan baris lain.
- `scripts/qc_backfill.py` memakai hook yang sama setelah `parse()`, dan
  docstring-nya diperbarui (bukan lagi hanya BS-200E).

## Alur data

```
alat ──ORU^R01 (MSH-16=0)──▶ parse → ResultObject pasien ─▶ tbl_result (1 baris)
     ◀──ACK^R01───────────
alat ──ORU^R01 (MSH-16=2)──▶ parse → split_results → N × ResultObject + blok qc
     ◀──ACK^R01───────────                         ─▶ tbl_result (N baris)
                                   LisBridge.ResultPusher ─▶ POST /qc-results
alat ──QRY^Q02 (barcode)───▶ lookup tbl_order
     ◀──QCK^Q02 OK/NF──────
     ◀──DSR^Q03 (bila OK)──
alat ──ACK^Q03─────────────▶ update tbl_order.instrument_status
MidLab ──DSR^Q03 (MSH-15=P)─▶ alat   (broadcast)
       ◀──ACK^Q03──────────
```

## Detail parsing

### Hasil pasien (MSH-16 = 0)

Diwarisi dari BS-200E tanpa perubahan. Log mengonfirmasi posisi: OBR-2 =
barcode/Lab No (`2191`), OBR-3 = ID sampel internal, OBR-10 = posisi
(`1^10`), OBR-15 = jenis sampel, OBX-3/4 = kode/nama tes, OBX-5 = hasil,
OBX-13 = hasil original, OBX-14 = waktu tes.

OBX-18 (result flags baru di manual ini) tidak dipetakan — tidak ada di
ResultObject dan tidak terisi di log.

### Hasil QC (MSH-16 = 2)

Contoh dari log:

```
OBR|253|GLU|Glu-G|^|Serum|20241007003327|20241007003250|20241007031132|||1|8|CTRL L1|059324003|20261231||M|100.000000|10.000000|93.389457|mg/dL|||||||RGTE,1-3s|...
```

| Field | Isi | Dipakai sebagai |
|---|---|---|
| OBR-2 | kode tes | `results[].test_code`, kunci `targets` |
| OBR-3 | nama tes | `results[].test_name` |
| OBR-5 | jenis sampel | komentar |
| OBR-6 | waktu mulai run | cadangan `run_at` |
| OBR-8 | waktu selesai per tes | `run_at` dan `specimen.collected_at` |
| OBR-11 | jumlah kontrol | validasi |
| OBR-12 | no. kontrol `^` | komentar |
| OBR-13 | nama kontrol `^` | `qc.control_name`, sumber level |
| OBR-14 | lot `^` | `qc.lot_number` (prefiks "Lot No." dibuang) |
| OBR-15 | kedaluwarsa `^` | `qc.expiry_date` (`YYYY-MM-DD`) |
| OBR-17 | level `^` (L/M/H) | cadangan level |
| OBR-18 | mean `^` | `qc.targets[code].mean` |
| OBR-19 | SD `^` | `qc.targets[code].sd` |
| OBR-20 | hasil `^` | `results[].value` |
| OBR-21 | unit (tunggal) | `results[].unit` |
| OBR-28 | flag QC alat | komentar `instrument_flags: …`; `results[].flag` kosong |

Flag alat tidak dikirim sebagai `flag` karena EazyLab menghitung Westgard
sendiri; flag tetap terlihat di Result Monitor lewat komentar.

**Pemecahan per kontrol.** Field 12–15 dan 17–20 dipecah pada `^`. Jumlah
kontrol = jumlah komponen OBR-14 (lot), karena lot wajib bagi EazyLab. Kontrol
ke-i mengambil komponen ke-i dari tiap field; komponen yang hilang dianggap
kosong. Bila OBR-11 berisi angka yang berbeda dengan jumlah komponen, catat
`parse_errors` dan tetap pakai jumlah komponen.

`parse()` mengembalikan ResultObject untuk kontrol pertama dengan daftar seluruh
kontrol di kunci internal `_qc_controls`. `split_results()` membangun satu
ResultObject per kontrol (`message_id` baru per baris, `results` satu item,
blok `qc` sendiri) dan membuang `_qc_controls`. QC satu kontrol menghasilkan
satu baris (tetap tanpa `_qc_controls`); pesan selain QC dikembalikan sebagai
`[result_dict]` apa adanya. Hasil `parse()` sendiri sudah berisi blok `qc`
kontrol pertama, sehingga pemanggil yang tidak memakai hook tetap mendapat
data QC yang valid (minus kontrol ke-2 dst).

### Level kontrol

1. Cari pola level di nama kontrol (tidak peka huruf):
   `\b(?:L|LV|LVL|LEVEL)\s*[-_]?\s*([123])\b` → `"1"`, `"2"`, `"3"`.
   Contoh cocok: `CTRL L1`, `Level 2`, `LV3`, `QC-L 2`.
2. Bila tidak cocok, pakai OBR-17 apa adanya (`L`/`M`/`H`); ResultPusher
   memetakannya ke `1`/`2`/`3` lewat `_QC_LEVEL_MAP` yang sudah ada.
3. Bila keduanya ada dan berbeda (nama → `1`, OBR-17 `M` → `2`), tulis WARNING:
   `QC <kode> kontrol "<nama>": level dari nama = 1, dari alat = M — pakai 1; periksa setting level kontrol di alat`.

### Kalibrasi (MSH-16 = 1)

Diwarisi dari BS-200E: direkam ke `tbl_result` dengan `sample_type =
"calibration"`, tidak dikirim ke EazyLab.

## Bidirectional

### Query

Diwarisi dari BS-200E: `handle_enq` membaca QRD-8 (barcode) dan QRD-9
(`OTH`/`CAN`), `format_query_response_full` → QCK^Q02 + DSR^Q03,
`format_query_not_found_full` → QCK^Q02 `NF`, group download satu DSR per
order dengan DSC kosong pada pesan terakhir.

Penyesuaian builder:

- DSP-26 jenis sampel dikirim dengan huruf awal kapital (`Serum`, `Plasma`,
  `Urine`) — format yang terbukti diterima BS-360E di log. Nilai lain dikirim
  apa adanya (alat menerima jenis sampel dari kamus lokal).
- DSP-29+ tetap `KODE^NAMA^^`. Alat mengenali tes dari kode (manual); log
  middleware lain mengosongkan nama, BS-200E mengisinya — tidak berpengaruh.
- MSH-6 fallback `BS-240Pro` bila alat tidak mengisi MSH-3/4 (log: kosong).

### Broadcast

`format_order()` membangun DSR^Q03 dengan **MSH-15 = `P`** dan tanpa WARNING
"out-of-spec". QRD-8 berisi barcode order. `handle_ack()` (diwarisi) membaca
ACK^Q03: `AA` → BroadcastWorker menandai `sent`, selain itu `failed`.

Belum ada log broadcast dari alat sungguhan; jalur ini hanya diuji terhadap
manual. Saat instalasi, mulai dengan `bidir_mode = query`.

### Koneksi

Tanpa kode di driver. `tbl_instrument.connection`:

- `server` — MidLab listen di `port`, alat konek ke IP:port LIS (umum di Mindray).
- `client` — MidLab konek ke `ip_address:port` alat.

Framing MLLP sudah menangani pesan yang tiba terpecah per segmen (pola di log).

## Testing

`tests/test_mindray_bs240pro.py`, TDD. Fixture diambil dari log BS-360E
dan **dianonimkan** (nama pasien, NIK, nomor RM diganti nilai fiktif) sebelum
masuk git; struktur, kode tes, nilai QC, lot, dan timestamp dipertahankan.

1. ORU pasien (5 tes) → ResultObject: barcode, nama, tes, unit, flag.
2. QC tunggal `GLU / CTRL L1 / M`: kode `GLU`, lot `059324003`, level `1`,
   WARNING mismatch, komentar `instrument_flags`, kedaluwarsa `2026-12-31`,
   `run_at` = OBR-8.
3. QC multi-kontrol (contoh manual `QUAL1^QUAL2`, `L^M`, `45^55`):
   `split_results` → 2 baris dengan lot/level/target masing-masing dan
   `message_id` berbeda; OBR-11 tidak cocok → `parse_errors`.
4. Pola level: `CTRL L1`, `Level 2`, `LV3`, `QC-L 2`, `KONTROL NORMAL`
   (cadangan ke OBR-17), nama kosong.
5. Query QRY barcode → QCK `OK` + DSR (DSP-21 barcode, DSP-26 `Serum`,
   DSP-29+ tes); order tidak ada → QCK `NF`.
6. Broadcast: `format_order` → MSH-15 = `P`, tanpa WARNING.
7. `build_qc_payload` dari baris BS-240Pro → payload `/qc-results` valid
   (level `1`, lot, run_at ISO8601, target mean/sd).
8. Receiver: protocol dengan `split_results` → N baris tersimpan; tanpa
   hook → 1 baris (regresi).
9. `load_module("HL7_MINDRAY_BS240PRO_360E_240E")` → `MindrayBS240ProModule`.
10. `qc_backfill` dengan pesan multi-kontrol → N payload.

Simulator `scripts/mindray_bs240pro_test_sender.py` (pola
`mindray_bs200e_test_sender.py`) untuk uji manual di dev, mode klien dan
server.

## Risiko diketahui

- **Broadcast belum terbukti di alat** — mitigasi: instalasi awal mode `query`.
- **Pola level dari nama** bisa salah tangkap nama yang kebetulan berisi
  `L1`/`L2` tanpa maksud level — WARNING mismatch membuatnya terlihat, dan
  EazyLab tetap memberi `level_mismatch` bila master kontrol berbeda.
- **Ketergantungan pada BS-200E** — perubahan driver BS-200E ikut terbawa;
  dijaga oleh test BS-240Pro berbasis log asli.
- **QC multi-kontrol** hanya diuji dengan contoh manual; log lapangan selalu
  satu kontrol per pesan.
