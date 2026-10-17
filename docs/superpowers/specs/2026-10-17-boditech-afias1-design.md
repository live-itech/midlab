# Driver HL7_BODITECH_AFIAS-1 — Design

Tanggal: 2026-10-17
Status: disetujui, siap implementasi — **best-effort**, wajib diverifikasi di alat

## Ringkasan

Driver protocol baru `HL7_BODITECH_AFIAS-1` untuk immunoassay analyzer POCT
Boditech AFIAS-1, mode LIS "HL7" (HL7 v2.6, `OUL^R24`).

Lingkup:

- **Unidirectional** — terima `OUL^R24^OUL_R24` (hasil), balas `ACK^R24^ACK`.
- Framing **MLLP** lewat jalur HL7 yang sudah ada di TCPSocketService.
- Koneksi **server maupun client** — `tbl_instrument.connection` diatur
  TCPSocketService; driver tidak bergantung arah koneksi. Dibuktikan dengan
  test end-to-end socket sungguhan untuk kedua mode.
- Receiver memberi **WARNING** bila data HL7 datang tanpa `<VT>` (sebelumnya
  dibuang diam-diam) — agar framing alat langsung terlihat saat pemasangan.

Di luar lingkup: query/order (alat hanya mengirim hasil), QC (format tidak
diketahui), mode "Boditech/General" dan "POCcelerator", framing HL7 polos.

## Sumber spesifikasi

*AFIAS-1 LIS user manual* (Boditech Med, Technical Sales, Rev.00 2025.06),
16 slide .pptx. Hampir seluruhnya panduan menu alat. Informasi protokol
hanya satu contoh pesan per mode di slide 5:

```
MSH|^~\&||1|AFIAS-6|A60123456789|20200305171553.205+0900||OUL^R24^OUL_R24|1|T|2.6
PID||T45|T45|||||
OBR||T4|G||||||admin
ORC|OK||||||||||||||||A60123456789|5
SPM||T4PKA37F||||||||||||||||20210719
OBX|1|TX|T4||33.26|nmol/L|||||R
```

Fakta lain dari dokumen:

- Mode HL7 tersedia lewat kabel USB-OTG (RS232) atau LAN.
- Mode Network: alat diisi **Server IP + Port** → alat yang konek, MidLab
  menjadi server (`tbl_instrument.connection = 'server'`).
- Mode RS232 lewat converter RS232-to-LAN: converter umumnya bisa diset
  sebagai TCP server, sehingga MidLab yang konek
  (`tbl_instrument.connection = 'client'`). Ditambahkan atas permintaan user
  (17 Okt 2026): driver harus jalan di kedua mode.
- Alat hanya mengirim data ke LIS ("analyzer sends data to LIS server").

### Yang TIDAK dispesifikasi dokumen

1. Framing — MLLP atau HL7 polos.
2. Apakah alat menunggu ACK (MSH-15/16 tidak ada di contoh).
3. Field mana yang memuat barcode sampel vs ID pasien (contoh: `T45` di PID-2 dan PID-3).
4. Format pesan QC (alat punya menu Cal/QC).
5. Arti OBR-3 (`G`), ORC-18 (`5`), dan nilai OBX-11 (`R`).
6. Contoh kemungkinan diketik manual di slide; contoh dokumen AFIAS-1 sendiri
   menulis model `AFIAS-6`.

Diputuskan bersama user: **best-effort dari contoh slide 5**, diverifikasi
dan dikoreksi dengan data asli saat pemasangan pertama.

## Arsitektur

Paket mandiri `protocols/boditech_afias1/` (pola driver SD Biosensor F2400 —
alat POCT, HL7 2.6). Bukan subclass F2400: pesannya berbeda (`OUL^R24` dengan
ORC/SPM vs `ORU^R01` PCD-01).

| File | Isi |
|---|---|
| `constants.py` | nama/versi protocol, segment, event, posisi field, nilai ACK |
| `parser.py` | `field()`, `component()`, `to_iso8601()`, `AfiasParser` (unwrap MLLP, split, parse MSH/PID/OBR/ORC/SPM/OBX, `get_message_type`) |
| `builder.py` | `AfiasBuilder.build_ack_r24(context)` |
| `module.py` | `BoditechAfias1Module(BaseProtocolModule)` |

### Pendaftaran

```python
"HL7_BODITECH_AFIAS-1": "protocols.boditech_afias1.module",
```

- 20 karakter, di bawah `protocol VARCHAR(50)` → tanpa migrasi DB.
- `is_mllp_protocol()` → True (prefiks `HL7`) → framing MLLP.
- `ResultPusher._normalize_protocol()` → keluarga `HL7`.
- Muncul otomatis di `/api/protocols` dan dropdown web console.

## Pemetaan pesan

| Segment-field | Contoh | Tujuan |
|---|---|---|
| MSH-5 | `AFIAS-6` | komentar `model: …` (tidak divalidasi) |
| MSH-6 | `A60123456789` | komentar `serial: …` |
| MSH-7 | `20200305171553.205+0900` | `message_datetime` = `2020-03-05T17:15:53.205+09:00` (pecahan & offset dipertahankan) |
| MSH-9 | `OUL^R24^OUL_R24` | filter `should_store_result` |
| MSH-10 | `1` | dipantulkan ke ACK |
| MSH-11 | `T` | komentar `processing_id: T`; dipantulkan ke ACK; tidak menolak pesan |
| PID-3, cadangan PID-2 | `T45` | `specimen.sample_id` dan `patient.patient_id` |
| OBR-2 | `T4` | `order.panel` |
| OBR-3 | `G` | komentar `obr3: G` |
| OBR-9 | `admin` | komentar `operator: admin` |
| ORC-18 | `5` | komentar `orc18: 5` |
| SPM-2 | `T4PKA37F` | komentar `cartridge_lot: T4PKA37F` |
| SPM-18 | `20210719` | komentar `cartridge_expiry: 2021-07-19` |
| OBX-3 | `T4` | `results[].test_code` (komponen 1; komponen 2 bila ada → `test_name`, selain itu `test_name` = kode) |
| OBX-5 | `33.26` | `results[].value` |
| OBX-6 | `nmol/L` | `results[].unit` |
| OBX-7 | — | `results[].reference_range` |
| OBX-8 | — | `results[].flag` |
| OBX-11 | `R` | `results[].status` apa adanya |

Aturan:

- Setiap OBX → satu `TestResult`.
- Field yang artinya tidak jelas disimpan sebagai komentar, tidak dibuang,
  agar data asli nanti bisa menjelaskan artinya.
- PID-2 dan PID-3 kosong → `parse_errors` "ID sampel kosong"; tanpa OBX →
  `parse_errors` "Tidak ada OBX". Pesan tetap dikembalikan (dan disimpan).
- Input rusak (kosong, bukan HL7, tanpa MSH) tidak melempar exception —
  dilaporkan lewat `parse_errors`.
- Semua pesan diperlakukan sebagai hasil pasien; tidak ada blok `qc`.

## ACK

Setiap pesan non-ACK yang MSH-nya terbaca (bukan hanya `OUL^R24`) dibalas lewat hook
`build_ack_response`:

```
MSH|^~\&|MidLab||<MSH-5 alat>|<MSH-6 alat>|<jam MidLab YYYYMMDDHHMMSS>||ACK^R24^ACK|<MSH-10 alat>|<MSH-11 alat, default P>|2.6
MSA|AA|<MSH-10 alat>
```

dibungkus MLLP. Bila pesan tidak bisa dibaca, ACK tidak dikirim (`b""`) dan
error dicatat. Bila alat ternyata tidak menunggu ACK, balasan ini tidak
berbahaya.

Pesan ACK dari alat (MSH-9 berawalan `ACK`) tidak dibalas (`b""`, INFO) —
mencegah loop echo bila converter RS232-to-LAN memantulkan ACK MidLab sendiri.

## Filter penyimpanan

`should_store_result(result_dict, raw_bytes)` → True hanya bila MSH-9
berawalan `OUL^R24`; selain itu tidak disimpan: WARNING untuk tipe non-ACK (terlihat di `tcp_<id>.log`
saat commissioning), INFO untuk pesan ACK.

## Bidirectional

Tidak didukung. `ACK_EXPECTED_ON_NOT_FOUND = False`; `is_enq` → False;
`handle_enq` → dict `type="unsupported"`; `format_order`,
`format_query_response`, `format_query_not_found` → `b""` dengan WARNING.
`handle_ack` membaca MSA-1 (`AA` → ACK, `AE`/`AR` → NAK, lainnya UNKNOWN).

## Peringatan non-MLLP di receiver

`services/tcp_socket/receiver.py`, `_extract_mllp_message`:

- Buffer tanpa `<VT>` saat ini di-`clear()` diam-diam; sisa sebelum `<VT>`
  juga dibuang diam-diam.
- Diubah: bila byte yang dibuang tidak kosong, tulis WARNING ke logger
  receiver: `Data HL7 tanpa MLLP <VT> dibuang (<n> byte): <cuplikan>`
  memakai `self._preview()` yang sudah ada.
- Perilaku framing tidak berubah; berlaku untuk semua driver HL7. Byte
  mentah tetap tercatat di comm log seperti biasa.

## Koneksi server & client

Driver tidak memuat kode arah koneksi; `TCPSocketService._run_server_mode()`
dan `_run_client_mode()` sama-sama meneruskan data ke `ResultReceiver` yang
sama. Yang wajib dijamin: tidak ada deklarasi transport di driver yang
mengunci satu arah (`PING_BEFORE_RECONNECT`, `IDLE_RX_TIMEOUT` tidak
dideklarasikan), dan test end-to-end membuktikan alur OUL → simpan → ACK di
kedua mode dengan socket localhost sungguhan. Docstring module menyebut
kedua mode. Simulator mendukung `--mode connect` (MidLab server) dan
`--mode listen` (MidLab client).

## Testing

`tests/test_boditech_afias1.py`, TDD. Fixture utama: contoh slide 5,
dibungkus MLLP.

1. Registry: `load_module("HL7_BODITECH_AFIAS-1")` → `BoditechAfias1Module`; `is_mllp_protocol` True.
2. Parse contoh: `sample_id`/`patient_id` `T45`; hasil `T4` = `33.26` `nmol/L` status `R`; `message_datetime` `2020-03-05T17:15:53.205+09:00`; komentar model/serial/operator/lot/kedaluwarsa/processing_id/obr3/orc18; `parse_errors == []`.
3. Variasi: PID-3 kosong → PID-2; keduanya kosong → `parse_errors`; dua OBX → dua hasil; tanpa OBX → `parse_errors`; input rusak tidak melempar.
4. ACK: `ACK^R24^ACK`, MSA-2 = MSH-10, MSH-11 `T` dipantulkan, MSH-5/6 identitas alat, envelope MLLP.
5. `should_store_result`: `OUL^R24` True; `ACK^R24` dan lainnya False.
6. Receiver end-to-end (`save_result` di-monkeypatch): pesan terpotong → tersimpan sekali, ACK sekali.
7. WARNING non-MLLP: HL7 polos → WARNING berisi cuplikan, tidak tersimpan; pesan MLLP normal tanpa WARNING.
8. `build_mid_payload` ResultPusher dari hasil parse → `protocol == "HL7"`, `specimen.collected_at` ISO, satu hasil `T4`.
9. `is_enq` False untuk pesan apa pun.
10. End-to-end TCPSocketService, socket localhost sungguhan: `connection='server'` (test berperan alat yang konek) dan `connection='client'` (test berperan alat/converter yang listen) — keduanya: OUL tersimpan sekali, alat menerima `ACK^R24^ACK`, service berhenti bersih.

Simulator `scripts/boditech_afias1_test_sender.py`: kirim contoh slide 5 ke
MidLab dan tampilkan ACK; `--mode connect|listen` untuk MidLab server/client;
`--raw` mengirim tanpa MLLP untuk mencoba WARNING.

## Risiko diketahui

- **Seluruh driver bersandar pada satu contoh** yang mungkin diketik manual;
  salah ketik bisa menggeser pemetaan field.
- **Framing/ACK belum terbukti.** Alat mengirim HL7 polos → data tidak
  tersimpan, tapi WARNING membuatnya langsung terlihat.
- **ID sampel PID-3/PID-2 adalah dugaan.** Bila barcode lab ada di field lain,
  hasil tidak cocok dengan order LIS.
- **QC tidak dikenali** — QC yang dikirim alat masuk sebagai hasil pasien.
- **Mode RS232** lewat converter: framing tidak diketahui.

**Langkah wajib di pemasangan pertama:** rekam lalu lintas alat (TapService
atau comm log `tcp_<id>.comm.log`), cocokkan dengan pemetaan di atas, dan
koreksi driver dengan data asli (termasuk QC bila alat mengirimnya).
