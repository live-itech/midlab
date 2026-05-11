# Cobas c-111 Protocol Module — Design Spec

**Tanggal:** 2026-05-11
**Status:** Draft untuk implementasi
**Sumber referensi:** `docs/vendor/cobas_c111/host_interface_manual.pdf` (Roche, Host Interface Manual Version 2.2, untuk Software Version 3.0+)

---

## 1. Scope & Tujuan

### Tujuan
Tambahkan dukungan protocol module **Cobas c-111** ke MidLab agar alat ini bisa di-onboard sebagai instrument unidirectional yang mengirim hasil pemeriksaan ke MidLab → LIS.

### In-scope
- Modul baru `protocols/cobas_c111/` (standalone, **tidak depend** pada `protocols/astm/`).
- Mode operasi: **unidirectional** saja (alat → MidLab).
- Lower-layer ASTM E1381: handshake ENQ → ACK → frame → ACK per frame → EOT; framing STX/ETX/ETB; FN modulus 8; checksum modulus 8.
- Upper-layer ASTM E1394: parse record H, P, O, R, C, L + Roche-specific **M.CR** (Photometric Calibration Result) dan **M.RR** (Photometric Absorbance Data).
- Decode escape sequences: `&F&` → `|`, `&S&` → `^`, `&R&` → `\`, `&E&` → `&`.
- Output: `ResultObject` (skema MidLab existing di `lib/models.py`), disimpan ke `tbl_result` dengan `send_status='pending'`.
- Penambahan minimal pada `lib/models.py`: satu field baru `ResultObject.comments: List[str]` (additive, default kosong, tidak break protocol existing) untuk menampung isi C record. Tidak ada field baru di sub-model lain.
- **QC, Calibration, Patient results** semua diperlakukan seragam — masuk `tbl_result`, format JSON sama, LIS yang membedakan dari konvensi field.
- Test code Roche (GLUC2, CREJ2, ALTL, dll) disimpan **apa adanya** tanpa mapping.
- Database registry: protocol baru terdaftar sebagai `"COBAS_C111"` di kolom `tbl_instrument.protocol`.

### Out-of-scope (akan dijadikan iterasi berikutnya bila dibutuhkan)
- Bidirectional: order broadcast / query mode. Modul tetap implement method bidirectional dari `BaseProtocolModule` tapi `raise NotImplementedError` agar kontrak abstract tetap terpenuhi.
- Reagent disk inventory (event U05/U06), Database init (U07/U08), Photometric calibration upload — semua MSR commands dari host ke alat.
- Test code mapping ke LIS-internal/LOINC.
- Penyimpanan QC / calibration di tabel terpisah.
- Modifikasi `protocols/astm/` (modul existing tidak disentuh).
- Perubahan `services/result_sender/`, `services/order_receiver/`, atau halaman `services/web_console/`.
- Perubahan sub-model `TestResult`, `OrderInfo`, `SpecimenInfo`, `PatientInfo` di `lib/models.py` (hanya `ResultObject.comments` yang ditambah).
- Penyimpanan raw photometric payload (bytes mentah M.RR / detail kalibrasi M.CR di luar field summary) — saat ini drop, parser hanya ekstrak field summary yang dikenal.
- Penamaan TCP service berbasis slug instrument (di-defer; lihat bagian Future Work).

### Berhasil bila (success criteria)
1. Alat c-111 (atau ASTM simulator dari Appendix C manual) bisa connect ke MidLab via RS232-to-LAN converter dan menyelesaikan satu sesi result upload tanpa error di sisi alat (tidak ada alarm komunikasi).
2. Sesi sample (REAL dan BATCH) menghasilkan satu atau lebih row baru di `tbl_result` dengan `result_json` yang valid sesuai skema `ResultObject` MidLab.
3. `ResultSenderService` (existing) berhasil POST `result_json` tersebut ke LIS — tanpa perubahan kode di service tersebut.
4. Unit test parser PASS untuk minimal 3 sample message: single-result, multi-result, dan message berisi M.CR/M.RR.

---

## 2. Arsitektur Internal Modul

### File baru

```
protocols/cobas_c111/
├── __init__.py
├── constants.py    # control bytes (STX/ETX/ETB/ENQ/ACK/EOT/CR/LF),
│                   # delimiters default (|, \, ^, &),
│                   # record type IDs (H, P, O, R, C, L, Q, M),
│                   # Roche-specific markers (M|CR, M|RR)
├── parser.py       # FrameDecoder: split frame, validate checksum,
│                   #               verify FN sequence, decode escape
│                   # RecordParser: split per CR, dispatch by record type,
│                   #               parse H/P/O/R/C/L/M.CR/M.RR
└── module.py       # class CobasC111Module(BaseProtocolModule):
                    #   - parse(), is_enq(), handle_ack() — implementasi penuh
                    #   - method bidirectional → raise NotImplementedError
```

### Boundary tiap unit

- **`constants.py`** — konstanta murni, tidak ada logic. Karena standalone, tidak import dari `protocols/astm/`.
- **`parser.py`** — dipecah dua kelas untuk testability:
  - `FrameDecoder` — Input: bytes mentah (akumulasi frame dari receiver). Output: list of record-string (sudah decoded). Tidak tahu apa pun tentang semantik record ASTM.
  - `RecordParser` — Input: list of record-string. Output: list of dict per record dengan `record_type` + field-spesifik. Tidak tahu apa pun tentang framing.
- **`module.py`** — tipis. Orchestrate `FrameDecoder` → `RecordParser` → assembly ke `ResultObject`. Tidak ada parsing logic di sini.

Pemisahan ini memungkinkan unit-test `RecordParser` tanpa harus mock frame layer, dan sebaliknya.

### Touch points di file existing (minimal)

1. **`protocols/base.py`** — tambah satu entry registry di `_PROTOCOL_REGISTRY`:
   ```python
   "COBAS_C111": "protocols.cobas_c111.module",
   ```

2. **`services/tcp_socket/receiver.py`** — ubah dispatcher (~baris 88) agar `"COBAS_C111"` di-route ke fungsi lower-layer ASTM yang sudah ada (E1381 identik):
   ```python
   if protocol in ("ASTM", "COBAS_C111"):
       return await self._handle_astm_data(writer)
   ```

3. **`lib/models.py`** — tambah satu field di `ResultObject`:
   ```python
   comments: List[str] = field(default_factory=list)
   ```
   Plus tambahkan rekonstruksi di `ResultObject.from_dict()`. Bersifat additive; protocol existing (ASTM, HL7) tetap berfungsi tanpa perubahan (default empty list).

Itu **seluruh modifikasi pada file existing**. Tidak ada perubahan di `services/result_sender/`, `services/order_receiver/`, `services/web_console/`, atau skema DB.

### Kontrak `BaseProtocolModule` untuk unidirectional

| Method | Implementasi |
|---|---|
| `parse()` | Implementasi penuh — ini inti modul |
| `is_enq()` | Return `True` jika byte pertama = ENQ (0x05). Tidak deteksi Q-record (query mode out-of-scope) |
| `handle_ack()` | Implementasi penuh — receiver butuh ini untuk diferensiasi ACK/NAK/EOT |
| `format_order()` | `raise NotImplementedError("Cobas c-111 module is unidirectional")` |
| `handle_enq()` | `raise NotImplementedError(...)` |
| `format_query_response()` | `raise NotImplementedError(...)` |
| `format_query_not_found()` | `raise NotImplementedError(...)` |

Web Console secara natural tidak akan memanggil method bidirectional selama `tbl_instrument.mode = "unidirectional"`.

### Naming

- Protocol name di registry & DB: `"COBAS_C111"` (uppercase, underscore).
- Class: `CobasC111Module`.
- Module path: `protocols.cobas_c111`.

---

## 3. Data Flow & Logic Parsing

### End-to-end flow

```
Cobas c-111
   │ RS-232 → RS232-to-LAN converter → TCP
   ▼
TCPSocketService.receiver._handle_astm_data()       [existing, reused]
   │ ENQ → kirim ACK
   │ STX..ETX/ETB frame → kirim ACK per frame, akumulasi
   │ EOT → frames terkumpul → panggil module.parse(combined_bytes)
   ▼
CobasC111Module.parse()                              [baru]
   │
   ├─► FrameDecoder.decode(combined_bytes)
   │     • split per frame (STX..ETX/ETB)
   │     • validate checksum (sum FN..ETX/ETB mod 256, lalu mod 8 — sesuai 7.1.5)
   │     • verify FN sequence (1..7 lalu 0..7 ...)
   │     • strip framing → concat text → split per CR → list of record-string
   │     • decode escape (&F&, &S&, &R&, &E&)
   │
   ├─► RecordParser.parse(record_strings)
   │     • dispatch per record letter (H/P/O/R/C/L/M)
   │     • untuk M: cek subtype (M|CR... atau M|RR...) → parse khusus
   │
   └─► assemble_result(records, instrument_id) → ResultObject.to_dict()
   ▼
save_result() di lib/db.py                           [existing, reused]
   → tbl_result (send_status='pending')
   ▼
ResultSenderService                                  [existing, no change]
   → POST result_json ke LIS
```

### Mapping record ASTM → field ResultObject

| Record c-111 | Tujuan di ResultObject |
|---|---|
| **H** (Header) | `mid_version`, `instrument_id`, `protocol="COBAS_C111"`, `message_id`, `message_datetime` (dari field timestamp H) |
| **P** (Patient) | `patient.{patient_id, name, dob, gender, physician}`. Untuk QC: field biasanya kosong/khusus — tetap diisi apa adanya, LIS yang membedakan |
| **O** (Order/Test Request) | `specimen.{sample_id, sample_type, collected_at}` + `order.{order_id, panel}` |
| **C** (Comment, setelah O) | Append ke `ResultObject.comments[]` dengan prefix `"order: "` (contoh: `"order: collected by night shift"`) untuk membedakan konteks |
| **R** (Result) | Tambah satu `TestResult{test_code, test_name, value, unit, reference_range, flag, status}` ke `results[]`. `flag` = char dari Appendix A manual |
| **C** (Comment, setelah R) | Append ke `ResultObject.comments[]` dengan prefix `"result[<test_code>]: "` agar bisa di-attribute ke test mana |
| **L** (Termination) | Marker akhir message — tidak ada field target langsung. Bila `termination_code` non-`N`, dicatat di `parse_errors[]` sebagai info (mis. `"termination=Q (aborted)"`) |
| **M\|CR** (Photometric Calibration) | Entry di `results[]` dengan `test_code` = ID kalibrasi/reagent, `value` = nilai kalibrasi summary, `status` = `"calibration"`. Raw payload (koefisien, absorbance per kuvet, dll) **tidak disimpan** — di-drop dengan log INFO |
| **M\|RR** (Photometric Absorbance) | Entry di `results[]` dengan `test_code` = identifier rawdata, `value` = nilai/summary, `status` = `"absorbance_raw"`. Raw payload **tidak disimpan** — di-drop dengan log INFO |

**Penanganan QC/Calibration** (sesuai keputusan): parser tidak melakukan filter. Semua record yang punya nilai/payload masuk `results[]`. LIS membedakan via konvensi field (mis. `patient_id` kosong / status string / flag tertentu).

### Edge cases

1. **Multi-frame message** — text di-split menjadi beberapa frame (max 240 char/frame). FrameDecoder concat text dulu sebelum split per CR.
2. **Frame checksum salah** — Receiver existing belum validate checksum (verified di code: `_extract_astm_frame` hanya ekstrak, tidak validate). FrameDecoder kita harus validate. Bila gagal: tambah ke `parse_errors[]`, **tetap coba parse** records (better partial than none).
3. **FN sequence loncat** — log WARNING + `parse_errors[]`, lanjut parse.
4. **NULL value (`||`)** — string kosong, set `""` agar konsisten dengan ResultObject existing.
5. **Field `""`** — manual 7.1.4.3.7: "delete instruction". Hampir tidak relevan untuk unidirectional. Bila ketemu: log warning, treat sebagai empty string.
6. **Escape sequences di tengah text** — decode SETELAH split per field, agar tidak mengganggu pemisahan field.
7. **Record M dengan subtype tak dikenal** — log + simpan di `parse_errors[]`, jangan crash.
8. **Whitespace trailing/leading** — trim default.
9. **Escape sequence tak dikenal** (bukan F/S/R/E) — manual: "skipped and treated as NULL". Drop sequence, log INFO.

### Test code, unit, flag

- **Test code**: nilai dari kolom Universal Test ID di R record, **disimpan apa adanya**. Tidak ada lookup table.
- **Unit**: nilai dari kolom Units, apa adanya.
- **Reference range**: nilai dari kolom Reference Ranges, apa adanya.
- **Flag**: char dari Appendix A (Data/Calibration/QC/Derived/Treatment flags) **disimpan apa adanya** sebagai string. Tidak ada interpretasi flag-priority di parser; ditangguhkan kalau LIS minta.

---

## 4. Error Handling, Logging, Testing

### Error handling strategy

**Prinsip:** Parser **tidak boleh raise exception ke receiver**. Semua kegagalan jadi entry di `parse_errors[]` di ResultObject. Manual sec 6.5 menyebut "results must be sent manually" jika komunikasi gagal — tidak ada auto-resend dari sisi alat — jadi MidLab harus berusaha keras menyimpan apa yang bisa diparse.

**Lapisan 1 — Lower layer (receiver, sudah ada)**: frame tidak lengkap → tunggu data berikutnya. Tidak ada perubahan.

**Lapisan 2 — FrameDecoder (baru)**:
| Kondisi | Aksi |
|---|---|
| Checksum mismatch satu frame | Log WARNING, tambah ke `parse_errors[]`, tetap proses text frame |
| FN sequence loncat | Log WARNING + `parse_errors[]`, lanjut |
| Escape sequence tak dikenal | Drop sequence, log INFO (sesuai manual 7.1.4.3.5) |
| Bytes tanpa STX awal | Log WARNING, skip sampai STX berikut |

**Lapisan 3 — RecordParser (baru)**:
| Kondisi | Aksi |
|---|---|
| Record type tak dikenal | Log WARNING + `parse_errors[]`, skip record, lanjut |
| Field count kurang dari minimum | Log WARNING + `parse_errors[]`, isi field yang ada, sisanya kosong |
| Numeric field non-numeric (mis. `value="ERR"`) | Simpan sebagai string apa adanya (skema `value` di TestResult sudah string) |

**Lapisan 4 — DB save**: sudah di-handle `save_result()` existing — `try/except` dengan flag update di `finally` (coding rule #2 CLAUDE.md).

### Logging

Mengikuti coding rule #5 CLAUDE.md: format `[LEVEL] [SERVICE] [INSTRUMENT] message`. Logger name `"cobas_c111"` untuk modul. Receiver tetap pakai `"tcp_socket"` dengan instrument id.

Event log penting:
- **INFO**: ENQ diterima, EOT diterima, frame count, record count, hasil parsing (N results)
- **WARNING**: checksum fail, FN sequence skip, record type tak dikenal, M-subtype tak dikenal
- **ERROR**: hanya untuk yang benar-benar exceptional (mis. byte tidak bisa di-decode sebagai ASCII)

Output rotasi otomatis pakai `RotatingFileHandler` (sudah disetup di `lib/utils.py`). File log: `tcp_<instrument_id>.log` — tidak ada file log terpisah untuk modul. Modul dipanggil dari receiver, log-nya bergabung di `tcp_N.log`.

### Testing strategy

**Unit test (in-file, pola existing — block `if __name__ == "__main__"` di akhir setiap modul):**

1. **`parser.py`** — minimal 6 test:
   - `FrameDecoder.decode()` happy path: 1 end frame, valid checksum
   - `FrameDecoder.decode()` multi-frame: 2 intermediate + 1 end frame
   - `FrameDecoder.decode()` checksum fail: harus return records + `parse_errors`
   - Escape decode: `"abc&F&def"` → `"abc|def"` (dan empat varian)
   - `RecordParser.parse_r()` happy path: result lengkap dengan flag
   - `RecordParser.parse_m_cr()` & `parse_m_rr()`: photometric records

2. **`module.py`** — minimal 3 test:
   - `parse()` end-to-end dengan sample message lengkap (H, P, O, R, R, L)
   - `parse()` dengan M.CR record: muncul di `results[]` dengan status `"calibration"`
   - Method bidirectional → `try/except NotImplementedError` + `assert` (mengikuti pola existing — tidak pakai pytest)

**Sample data:** karena belum ada raw capture dari alat real, sample message di unit test ditulis manual berdasarkan struktur record di manual Section 7.2 + Appendix B 9.9 (Result Upload Analyzer → Host) saat fase implementasi.

**Manual integration test (di-luar scope spec ini, dicatat sebagai langkah verifikasi):**
1. Pakai **ASTM Simulator** (Appendix C manual) untuk inject message ke MidLab via TCP loopback.
2. Verify row baru muncul di `tbl_result` dengan `result_json` benar dan `send_status='pending'`.
3. Bila tersedia alat real: capture via Wireshark, replay dengan `socat`/`netcat`.

### Web Console implications

`/api/protocols` endpoint akan otomatis menampilkan `"COBAS_C111"` setelah registry update — tidak butuh perubahan kode. Halaman Instruments mendapat opsi protocol baru di dropdown saat add/edit instrument.

Setup operator di Web Console:
- protocol: `COBAS_C111`
- mode: `unidirectional`
- bidir_mode: `NULL`
- connection: tergantung konfigurasi RS232-to-LAN converter (server atau client) — operator pilih sesuai setting fisik

Tidak ada migration DB.

---

## 5. Future Work (tidak di-scope sekarang)

Dicatat di sini supaya tidak hilang:

- **Penamaan TCP service berbasis slug instrument** — saat ini service per alat dinamai `midlab-tcp@<id>` dan file log `tcp_<id>.log`. Operator ingin nama yang reflektif identitas alat (mis. `midlab-tcp@cobas-c111`). Memerlukan: kolom `slug` di `tbl_instrument`, perubahan `services/tcp_socket/main.py`, watchdog, Web Console UI, file log naming. **Sengaja di-defer** karena lingkup cross-cutting yang besar dan tidak berkaitan langsung dengan parsing protocol. Akan ditangani sebagai spec terpisah bila dibutuhkan.
- **Bidirectional Cobas c-111**: query mode dan broadcast (host download order). Memerlukan implementasi `format_order`, `handle_enq`, `format_query_response`, `format_query_not_found` — dan logic di QueryHandler / BroadcastWorker untuk c-111.
- **Test code mapping** ke LIS internal / LOINC — bisa diaktifkan via tabel `tbl_test_code_map` baru bila dibutuhkan.
- **MSR commands**: Reagent disk inventory, Database init — biasanya tidak dibutuhkan jika MidLab hanya men-forward result.
- **Flag priority interpretation** sesuai Appendix A — bila LIS membutuhkan single canonical flag per result.
