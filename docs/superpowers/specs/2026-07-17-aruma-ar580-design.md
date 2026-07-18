# Driver HL7_ARUMA_AR580 — Design

Tanggal: 2026-07-17
Status: disetujui, siap implementasi

## Ringkasan

Driver protocol baru `HL7_ARUMA_AR580` untuk hematology analyzer ARUMA AR580.
Lingkup: **unidirectional** — terima hasil `ORU^R01` dari alat, balas `ACK^R01`.

## Sumber spesifikasi & catatan penting

Dokumen acuan: *"LIS communication protocol instruction"* (Genrui, 19 halaman, 2019).

**Dokumen ini tertulis untuk Genrui KT-6610, bukan AR580.** Buktinya: MSH-3
`KT-6610`, MSH-4 `Genrui`, OBR-24 `HM` (hematology), dan panel OBX berisi CBC+DIFF
(WBC, Neu#/Lym#/Mon#/Eos#/Bas#, RBC, HGB, HCT, MCV, MCH, MCHC, RDW-CV/SD, PLT,
MPV, PDW, PCT, P-LCC, P-LCR) plus histogram/scattergram.

Keputusan: AR580 diperlakukan sebagai **rebrand OEM dari KT-6610**, sehingga
dokumen dianggap berlaku secara substansi. Konsekuensi desain: **parser tidak
memvalidasi MSH-3 secara ketat** — nilai apa pun diterima dan hanya dicatat ke
log. Ini menghindari kegagalan lapangan bila string MSH-3 asli ternyata `AR580`,
`AR-580`, atau tetap `KT-6610`.

### Yang TIDAK dispesifikasi dokumen

- Bab 2.2 menyebut `ORM^O01` (permintaan info sampel) dan `ORR^O02` (response),
  tetapi **tidak ada satu pun definisi field, contoh, atau grammar segment**
  untuk keduanya di seluruh dokumen. Query mode karena itu di luar lingkup.
- Bab 2.3.2 (QC information upload) berisi literal *"Not available, to be added"*.
- Bab 1.3 hanya menyebut *"Block is HL7 message"* tanpa menyatakan MLLP eksplisit,
  meski merujuk ke lower layer protocol HL7 2.3.1.

## Arsitektur

Mengikuti layout `protocols/mindray_bs200e/` — empat file di `protocols/aruma_ar580/`:

| File | Tanggung jawab |
|---|---|
| `constants.py` | Nama/versi protocol, indeks field MSH/PID/OBR/OBX, tabel escape, kode MSA |
| `parser.py` | bytes `ORU^R01` → ResultObject dict |
| `builder.py` | Bangun `ACK^R01` (MSH + MSA) |
| `module.py` | `ArumaAR580Module(BaseProtocolModule)` |

Registry: `HL7_ARUMA_AR580` → `protocols.aruma_ar580.module` di `protocols/base.py`.

**Prefix `HL7` bersifat fungsional, bukan kosmetik**: `is_mllp_protocol()`
(`protocols/base.py:173`) memilih framing MLLP berdasarkan prefix nama ini.

### Tanpa perubahan pada TCPSocketService

Dua kait yang sudah ada membuat driver ini plug-in murni:

1. `is_mllp_protocol()` → framing MLLP otomatis dari prefix nama.
2. `receiver.py:337` → memanggil `build_ack_response()` bila protocol module
   menyediakannya (kait yang sama dipakai HL7_MINDRAY_BS200E).

Perubahan di luar package: **satu baris** di `_PROTOCOL_REGISTRY`.

## Alur data

```
alat AR580 ──MLLP(ORU^R01)──> ResultReceiver
                                  │ parse() → ResultObject → tbl_result (pending)
                                  │ build_ack_response() → MSA|AA|<MSH-10>
              <──MLLP(ACK^R01)────┘
```

Dokumen (bab 2.3.1, catatan) menyatakan alat **mengirim ulang dalam 3 detik**
bila ACK tidak diterima, dan operator bisa mematikannya via setelan *no wait
response*. Karena itu ACK wajib dibangun dari **MSH-10 pesan yang diterima**
(echo), bukan control ID buatan sendiri, dan dikirim di koneksi yang sama.

`build_ack_response()` mengembalikan `b""` bila pesan tak punya MSH — konvensi
yang sama dengan Mindray; receiver sudah menangani kasus ini dengan log, bukan
crash.

## Detail parsing (dari dokumen)

Poin yang tidak mengikuti kebiasaan HL7 umum dan wajib dikodekan eksplisit:

- **OBX-3 berbentuk `^WBC^`** — kode test ada di **komponen 2**, komponen 1
  kosong. Membaca komponen 1 (kebiasaan HL7 umum) menghasilkan `test_code`
  kosong untuk semua hasil.
- **PID-5 berbentuk `&LName&&&`** — nama pasien ada di **subkomponen**, bukan
  komponen.
- **PID-3** = Patient ID / Sample No. **PID-8** gender = `F`/`M`/`O`.
- **OBX-8** abnormal flag `L`/`H`/null. **OBX-11** observ result status.
- **OBX-13** field custom vendor untuk status edit: null=unedited, `O`=expired
  reagent, `E`=active editing, `e`=passive editing. Ditangkap karena memberi
  tahu lab apakah nilai pernah diedit tangan.
- **OBX-2 = `IS`** adalah metadata, bukan pengukuran: Blood Mode, Test Mode,
  Ref Group, Age, Remarks, Blood Type, ESR. Dirutekan ke konteks
  specimen/order, tidak dicampur ke `results[]` bersama HGB.
- **OBX-2 = `ED`** (bitmap histogram/scattergram: `DIFFScatter_BMP`,
  `WBCScatter_BMP`, `RBCHistogram_BMP`, `PLTHistogram_BMP`) → **dilewati**,
  hanya jumlahnya dicatat ke log.
- **Escape decoding** pada field ST/TX/FT: `\F\ \S\ \T\ \R\ \E\` dan `\.br\`→CR.
  Field Remarks adalah tempat `|` tanpa escape akan merusak parse.
- **Encoding UTF-8** (contoh MSH-18 = `UTF-8`, meski Tabel 5 menulis "Unicode").
  Decode dengan `errors="replace"` agar alat yang salah setel terdegradasi,
  bukan melempar exception.

### Alasan melewati OBX bertipe ED

`OBX-5` bermaksimum 65536 karakter. Empat bitmap dalam satu pesan akan:
membengkakkan `tbl_result.result_json`, berisiko memotong `raw_data TEXT`
(batas 64KB MySQL) secara diam-diam, dan memperbesar payload push ke EazyApp.
EazyApp membutuhkan angka, bukan gambar. Bisa ditambahkan kemudian bila perlu.

## Method kontrak yang tidak diimplementasi

`BaseProtocolModule` adalah ABC, jadi semua method wajib ada. Untuk lingkup
unidirectional:

| Method | Perilaku |
|---|---|
| `is_enq` | `False` |
| `format_order` | raise `NotImplementedError` — ORM^O01 tidak dispesifikasi |
| `handle_enq` | raise `NotImplementedError` |
| `format_query_response` | raise `NotImplementedError` |
| `format_query_not_found` | `b""` |
| `handle_ack` | parse MSA-1 → `AA`→`ACK`; `AE`/`AR`/`CA`/`CE`/`CR`→`NAK` |

Stub yang melempar exception (bukan diam-diam mengembalikan data kosong)
membuat `bidir_mode` yang salah setel gagal keras di titik kesalahannya,
bukan mengirim payload ngawur ke hematology analyzer.

## Testing

`tests/test_aruma_ar580.py`, mengikuti pola `tests/test_mindray_bs200e.py`.
Ditulis test-first (TDD).

Kasus jangkar: **pesan 34-OBX verbatim dari bab 2.3.1 dokumen**. Bila parser
menghasilkan WBC=0.01, HGB=106, PLT=144 dengan flag L/H yang benar dari byte
string persis itu, driver benar.

Cakupan tambahan: escape decoding; nama `&LName&&&`; penghitungan ED-skip;
echo control ID pada ACK; input malformed/kosong; frame MLLP terpotong;
pemisahan OBX `IS` dari `results[]`; `handle_ack` untuk tiap kode MSA.

## Risiko diketahui

| Risiko | Mitigasi |
|---|---|
| String MSH-3 asli belum diketahui | Parser permisif; MSH-3 dicatat ke log, tidak divalidasi |
| Framing MLLP vs bare tidak pasti (bab 1.3 ambigu) | Frame reader toleran: terima dengan atau tanpa wrapper `<VT>`/`<FS><CR>` |
| Panel OBX AR580 mungkin beda dari KT-6610 | Kode test dibaca dinamis dari OBX-3, bukan whitelist |
