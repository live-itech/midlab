# MidLab API Reference

> **Audience:** Tim Internal MidLab + Tim LIS / vendor integrator.
> **Versi schema:** `mid_version = "1.0"`
> **Last updated:** 2026-05-13

MidLab menyediakan dua arah API untuk bridging dengan Laboratory Information System (LIS):

| Arah | Endpoint Owner | Tujuan |
|---|---|---|
| **LIS → MidLab** | MidLab Order Receiver (port 8001) | Terima order pemeriksaan dari LIS |
| **MidLab → LIS** | LIS (dikonfigurasi via Web Console) | Kirim hasil pemeriksaan ke LIS |

URL endpoint dan API key bisa dilihat / diubah lewat **Web Console → Settings (LIS API)**:
`http://<midlab-server>:8000/settings`

---

## 1. Order API — LIS kirim order ke MidLab

### 1.1 Base URL

```
POST http://<midlab-server-ip>:8001/api/orders
```

URL otomatis mengikuti IP server tempat MidLab di-deploy. Konfirmasi URL aktual
di halaman Web Console **Settings**. Port default `8001` (configurable di
`config.yaml: order_receiver.port`).

### 1.2 Authentication

| Mode | Konfigurasi MidLab | Header yang dikirim LIS |
|---|---|---|
| Dengan API key | `order_receiver.api_key` di-set di `config.yaml` | `X-API-Key: <key>` (wajib) |
| Tanpa API key | `order_receiver.api_key` kosong | Tidak perlu (development) |

Jika header API key salah/hilang sementara mode auth aktif → `401 Unauthorized`.

### 1.3 `POST /api/orders` — Buat order baru

**Headers:**
```
Content-Type: application/json
X-API-Key: <your-key>           # bila mode auth aktif
```

**Request body (OrderObject):**
```json
{
  "mid_version": "1.0",
  "order_id": "ORD-12345",
  "instrument_id": 1,
  "request_datetime": "2026-05-13T10:30:00+00:00",
  "patient": {
    "patient_id": "PAT001",
    "name": "John Doe",
    "dob": "19900515",
    "gender": "M"
  },
  "specimen": {
    "sample_id": "SAMP001",
    "sample_type": "Blood",
    "priority": "R"
  },
  "tests": [
    { "test_code": "WBC", "test_name": "White Blood Cell" },
    { "test_code": "RBC", "test_name": "Red Blood Cell" }
  ]
}
```

**Field reference:**

| Field | Type | Wajib | Keterangan |
|---|---|:-:|---|
| `mid_version` | string | — | Default `"1.0"`. |
| `order_id` | string | — | ID order di LIS (echo back saat result dikirim). |
| `instrument_id` | int | ✅ | Harus exist di `tbl_instrument`. > 0. |
| `request_datetime` | ISO8601 string | — | Auto-fill ke `now()` jika kosong. |
| `patient.patient_id` | string | — | Tergantung kebutuhan alat. |
| `patient.name` | string | — | Format bebas. |
| `patient.dob` | string | — | `YYYYMMDD` (rekomendasi). |
| `patient.gender` | string | — | `M` / `F` / `U`. |
| `specimen.sample_id` | string | — | Wajib untuk mode query — alat lookup by sample_id. |
| `specimen.sample_type` | string | — | "Blood", "Serum", "Urine", dll. |
| `specimen.priority` | string | — | `R` = Routine, `S` = Stat. |
| `tests[].test_code` | string | — | Kode tes sesuai konvensi alat. |
| `tests[].test_name` | string | — | Nama deskriptif (opsional). |

**Response `201 Created`:**
```json
{ "success": true, "order_id": 42 }
```
`order_id` di response adalah ID di `tbl_order` MidLab (auto-increment). Bisa
dipakai untuk poll status order.

**Response errors:**

| Status | Penyebab | Solusi |
|---|---|---|
| `400` | Body invalid (instrument_id ≤ 0, schema validation gagal) | Cek field `detail` di response |
| `401` | API key salah / hilang | Konfirmasi key dengan tim MidLab |
| `404` | `instrument_id` tidak terdaftar | Daftarkan instrument lewat Web Console → Instruments |
| `500` | Database error | Retry; cek log MidLab |

### 1.4 `GET /api/orders/{order_id}` — Cek status order

Cek status order berdasarkan ID yang di-return saat create.

**Response 200:**
```json
{
  "id": 42,
  "instrument_id": 1,
  "instrument_status": "sent",
  "retry_count": 0,
  "failed_at_service": null,
  "error_message": null,
  "created_at": "2026-05-13T10:30:01+00:00",
  "sent_to_instrument_at": "2026-05-13T10:30:45+00:00",
  "order_json": { "...": "..." }
}
```

**Status values (`instrument_status`):**

| Value | Arti |
|---|---|
| `pending` | Tersimpan, menunggu dikirim ke alat |
| `sent` | Sudah terkirim ke alat (alat menerima) |
| `failed` | Gagal kirim — cek `failed_at_service` + `error_message` |

`failed_at_service` contoh values: `broadcast_worker`, `broadcast_worker_send`, `query_handler_send`, `web_console` (manual cancel).

### 1.5 `GET /api/health` — Health check

```json
{ "status": "ok", "service": "order_receiver" }
```

### 1.6 Contoh request (curl)

```bash
curl -X POST 'http://192.168.1.50:8001/api/orders' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: your-secret-key' \
  -d '{
    "mid_version": "1.0",
    "order_id": "ORD-12345",
    "instrument_id": 1,
    "patient": { "patient_id": "PAT001", "name": "John Doe", "dob": "19900515", "gender": "M" },
    "specimen": { "sample_id": "SAMP001", "sample_type": "Blood", "priority": "R" },
    "tests": [{ "test_code": "WBC", "test_name": "White Blood Cell" }]
  }'
```

### 1.7 Idempotency

MidLab **tidak** melakukan dedup berdasarkan `order_id` — setiap POST = 1 row baru di `tbl_order`. Jika LIS retry karena network error, gunakan logic idempotency di sisi LIS (mis. tracking `order_id` yang sudah berhasil di-POST).

---

## 2. Result API — MidLab kirim hasil ke LIS

MidLab POST hasil pemeriksaan ke endpoint LIS yang dikonfigurasi di Web Console
Settings. Tim LIS perlu menyediakan endpoint sesuai kontrak di bawah.

### 2.1 Konfigurasi di MidLab

Web Console → Settings → **Result API**:

| Field | Contoh |
|---|---|
| LIS API URL | `https://lis.hospital.id/api/results` |
| LIS API Key | (opsional) → dikirim sebagai `X-API-Key` |

Perubahan langsung di-apply ke ResultSenderService (auto-reload setiap
`poll_interval` detik). Stored di `tbl_settings` (DB), override
`config.yaml` jika ada.

### 2.2 Kontrak endpoint LIS

LIS harus menyediakan endpoint:
```
POST <lis-api-url>
```
yang menerima:

- Header `Content-Type: application/json`
- Header `X-API-Key: <key>` (bila MidLab mengirim, sesuai setting)
- Body JSON sesuai schema ResultObject di bawah.

**Response yang diharapkan dari LIS:**

| Status LIS | Interpretasi MidLab |
|---|---|
| `2xx` | Result diterima — MidLab tandai `send_status='sent'` |
| `4xx` | Result invalid / ditolak — MidLab tandai `send_status='failed'`, no auto-retry dengan body yang sama. Operator harus fix data lewat Web Console + retry manual |
| `5xx` | LIS error transient — MidLab tandai `failed` dan akan retry pada poll cycle berikutnya sampai `retry_max` (default 3x) |
| Connection error / timeout | Sama dengan `5xx` — retry |

### 2.3 Schema body (ResultObject)

```json
{
  "mid_version": "1.0",
  "instrument_id": 1,
  "protocol": "ASTM",
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "message_datetime": "2026-05-13T10:35:22+00:00",
  "patient": {
    "patient_id": "PAT001",
    "name": "John Doe",
    "dob": "19900515",
    "gender": "M",
    "physician": "Dr. Smith"
  },
  "specimen": {
    "sample_id": "SAMP001",
    "sample_type": "Blood",
    "collected_at": "20260513093000"
  },
  "order": {
    "order_id": "ORD-12345",
    "panel": "CBC"
  },
  "results": [
    {
      "test_code": "WBC",
      "test_name": "White Blood Cell",
      "value": "5.2",
      "unit": "10^3/uL",
      "reference_range": "4.0-10.0",
      "flag": "N",
      "status": "F"
    },
    {
      "test_code": "RBC",
      "test_name": "Red Blood Cell",
      "value": "4.8",
      "unit": "10^6/uL",
      "reference_range": "3.5-5.5",
      "flag": "N",
      "status": "F"
    }
  ],
  "comments": [
    "result[WBC]: Sol1: F Dev"
  ],
  "parse_errors": []
}
```

**Field reference:**

| Field | Type | Keterangan |
|---|---|---|
| `mid_version` | string | Versi schema, `"1.0"`. |
| `instrument_id` | int | ID alat di MidLab (`tbl_instrument.id`). |
| `protocol` | string | `"ASTM"`, `"HL7"`, `"COBAS_C111"`. |
| `message_id` | string (UUID v4) | Unik per result. |
| `message_datetime` | ISO8601 | Saat hasil di-parse oleh MidLab. |
| `patient.*` | object | Bisa ada field kosong (alat tidak kirim semua). |
| `specimen.sample_id` | string | Primary key untuk match dengan order. |
| `order.order_id` | string | Echo back dari `OrderObject.order_id` jika alat menyertakan. |
| `order.panel` | string | Panel/grup tes (ASTM Order record field 5). |
| `results[]` | array | Bisa 0 / 1 / banyak tes per result. |
| `results[].value` | string | **Selalu string** — untuk dukung non-numeric ("POS", "ERR", ">10000"). |
| `results[].unit` | string | mis. `"mg/dL"`, `"10^3/uL"`. |
| `results[].reference_range` | string | mis. `"4.0-10.0"`, `"<200"`. |
| `results[].flag` | string | `N` (normal), `H` (high), `L` (low), `A` (abnormal), `<`, `>`. Vendor-specific. |
| `results[].status` | string | `F` (final), `P` (preliminary), `C` (correction). |
| `comments[]` | array of string | C records ASTM, NTE HL7. Prefix `"order: "` / `"result[<code>]: "` untuk attribusi. |
| `parse_errors[]` | array of string | Berisi pesan kalau parser MidLab tidak bisa proses semua data (mis. checksum frame fail). Kosong = parsing bersih. |

### 2.4 Retry & error handling

- **Poll interval default:** 5 detik (`result_sender.poll_interval` di `config.yaml`).
- **Batch size default:** 50 result per cycle.
- **Retry max default:** 3x (`result_sender.retry_max`). Setelah melewati limit, result di-skip otomatis dan logged WARNING. Operator harus retry manual via Web Console → Results → tombol Retry.
- **Pesan error** tersimpan di `tbl_result.error_message` — bisa dilihat di Web Console.

### 2.5 Contoh endpoint LIS (referensi Python/FastAPI)

```python
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI()

EXPECTED_API_KEY = "your-secret"

class TestResult(BaseModel):
    test_code: str
    test_name: str = ""
    value: str
    unit: str = ""
    reference_range: str = ""
    flag: str = ""
    status: str = ""

class ResultPayload(BaseModel):
    mid_version: str
    instrument_id: int
    protocol: str
    message_id: str
    patient: dict
    specimen: dict
    order: dict
    results: List[TestResult]

@app.post("/api/results")
async def receive_result(
    body: ResultPayload,
    x_api_key: Optional[str] = Header(None),
):
    if x_api_key != EXPECTED_API_KEY:
        raise HTTPException(401, "invalid api key")

    # TODO: simpan ke DB LIS, match dengan order
    save_to_lis(body)

    return {"success": True}
```

### 2.6 Cara test kontrak

Web Console → Settings → tombol **Test Connection** akan POST dummy payload
`{"mid_version":"1.0","_probe":true,"instrument_id":0}` ke URL LIS dengan
header sesuai konfigurasi. Status `2xx` atau `4xx` dianggap sukses
(reachable + bisa parse), `5xx` / connection error = gagal.

---

## 3. Settings API (internal)

Endpoint Web Console untuk manage konfigurasi LIS bridging. Biasanya tidak
dipakai langsung dari LIS — tapi bisa berguna untuk automation deployment.

### `GET /api/settings`

```json
{
  "order_api_url": "http://192.168.1.50:8001/api/orders",
  "order_api_key_set": true,
  "lis_api_url": "https://lis.hospital.id/api/results",
  "lis_api_key_masked": "********abcd",
  "lis_api_key_set": true,
  "local_ip": "192.168.1.50"
}
```

### `PUT /api/settings`

```json
{ "lis_api_url": "https://...", "lis_api_key": "new-key" }
```

Field `null` = jangan diubah. String kosong `""` = clear override (kembali pakai `config.yaml`).

### `POST /api/settings/test-lis`

Body opsional `{ "lis_api_url": "...", "lis_api_key": "..." }` — kalau kosong, pakai value tersimpan. Return 200 + message jika reachable.

---

## 4. Catatan Keamanan

| Item | Rekomendasi Production |
|---|---|
| Order API key (`order_receiver.api_key`) | Wajib set — random 32+ char |
| Result API key | Wajib set bila LIS punya validasi key |
| Transport | Pasang nginx reverse proxy + Let's Encrypt TLS di depan port 8000 & 8001 |
| Firewall | Port 8001 hanya buka untuk IP server LIS |
| Rotasi key | Update di Web Console Settings — auto-apply tanpa restart |
| Audit log | Akses API key di log Web Console (`/var/log/midlab/webconsole.log`) |

---

## 5. Versioning

Field `mid_version` di setiap payload menunjukkan versi schema. Saat ini `"1.0"`.

| Versi | Status | Endpoint |
|---|---|---|
| 1.0 | **Current** | `/api/orders`, `/api/results` |
| 2.0 | Planned | `/v2/api/...` (saat dibutuhkan breaking change) |

Perubahan additive (field baru di payload) tidak bump versi. Breaking change
(rename / remove / change semantics) → bump major, endpoint v1 di-maintain
minimal 6 bulan setelah v2 launch.

---

## 6. Quick Reference

```
┌──── LIS ────┐                          ┌──── MidLab ────────┐
│             │  POST  /api/orders  →   │  Order Receiver    │
│             │                          │  port 8001          │
│             │                          │                     │
│             │  ←  POST  <lis-url>      │  Result Sender      │
│             │                          │  (polls tbl_result) │
└─────────────┘                          └─────────────────────┘
```

| | Order API | Result API |
|---|---|---|
| **Owner** | MidLab | LIS |
| **Endpoint** | `<midlab-ip>:8001/api/orders` | `<lis-url>` (di Settings) |
| **Method** | `POST` | `POST` |
| **Auth** | `X-API-Key` (opsional) | `X-API-Key` (opsional) |
| **Body** | OrderObject | ResultObject |
| **Sukses** | `201 Created` | `2xx` |
| **Retry** | LIS handle sendiri | MidLab retry 3x |

---

*Untuk pertanyaan teknis: hubungi tim internal MidLab.
Untuk update dokumentasi: edit file `docs/API.md` di repo MidLab.*
