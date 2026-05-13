# LIS Bridging — EazyApp Integration Design

**Date:** 2026-05-13
**Status:** Approved, ready for implementation plan
**Author:** Brainstormed via superpowers:brainstorming

## Tujuan

Adjust MidLab agar full kompatibel dengan EazyApp LIS Instrument API
(`https://eazy.vespahobby.xyz/api/v1/instrument`). Saat ini MidLab:

- Push results ke single LIS URL pakai single API key (config-level)
- Receive orders via FastAPI endpoint sendiri (LIS push model)

EazyApp pakai **pull model** dengan **Bearer per-alat**, jadi arah aliran data
order terbalik dan auth jadi per-instrument.

Reference: `EazyApp_Instrument_API.postman_collection.json` (Postman v2.1).

## Keputusan Strategis

| Topik | Keputusan |
|---|---|
| Order direction | Replace `OrderReceiverService` dengan `OrderPullerService` (loop `GET /orders/pending` per alat) |
| Mapping parameter | **Tidak di-cache lokal**. Kirim raw `test_code`; biarkan LIS yang map (LIS sudah handle `unmapped_count` di response) |
| Status push triggers | TCP connect/disconnect, service start/stop graceful, error parsing/timeout |
| Logs push | Hanya `WARNING` + `ERROR` (hemat traffic, info penting saja) |
| Source-of-truth instrument config | **MidLab lokal** tetap source-of-truth. `GET /instrument` hanya untuk verifikasi API key valid + ambil `lis_instrument_id` (string) |
| Order poll interval | Konfigurable per-alat di `tbl_instrument`, default 10 detik |
| Arsitektur service | **Konsolidasi `LisBridgeService` per-alat** (1 instance per alat, 4 internal tasks parallel) |

## Arsitektur

```
┌──────────────────────────────────────────────────────────────────────┐
│                          EazyApp LIS                                 │
│              https://eazy.vespahobby.xyz/api/v1/instrument           │
│   Auth: Bearer <api_key_per_alat>                                    │
└──────────────────────────────────────────────────────────────────────┘
        ▲                ▲                ▲                ▲
        │ POST           │ GET            │ POST           │ POST
        │ /results       │ /orders/       │ /status        │ /logs
        │                │  pending       │                │
   ┌────┴────────────────┴────────────────┴────────────────┴──────┐
   │              LisBridgeService (1 instance per alat)          │
   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
   │  │ ResultPusher │  │ OrderPuller  │  │ StatusReporter│       │
   │  │  poll        │  │  poll        │  │  poll         │       │
   │  │  tbl_result  │  │  /orders/    │  │  tbl_lis_     │       │
   │  │  pending     │  │  pending     │  │  event_queue  │       │
   │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
   │         │                 │                 │                │
   │         │                 │  ┌──────────────┴──────────┐     │
   │         │                 │  │ LogPusher (WARN/ERROR)  │     │
   │         │                 │  │ cursor tbl_service_log  │     │
   │         │                 │  └─────────────────────────┘     │
   └─────────┼─────────────────┼──────────────────────────────────┘
             │                 │
   ┌─────────┴─────────────────┴──────────────────────────────────┐
   │                          MySQL                               │
   │   tbl_result, tbl_order, tbl_instrument (+ lis_*),           │
   │   tbl_service_log, tbl_lis_event_queue (baru)                │
   └─────────┬────────────────────────────────────────────────────┘
             │
   ┌─────────┴──────────────────────────────────────────────────────┐
   │             TCPSocketService (existing, 1 per alat)            │
   │  - ResultReceiver: parse raw → tbl_result (pending)            │
   │  - BroadcastWorker: poll tbl_order → kirim ke alat             │
   │  - On connect/disconnect/error → write tbl_lis_event_queue     │
   └────────────────────────────────────────────────────────────────┘
             │
   ┌─────────┴────────────────┐
   │     Alat Lab via TCP     │
   └──────────────────────────┘
```

**Highlights:**

- `OrderReceiverService` dan `ResultSenderService` (existing, global) **dihapus** setelah cutover selesai.
- `LisBridgeService` baru, **1 instance per alat**, supervise 4 asyncio tasks: ResultPusher, OrderPuller, StatusReporter, LogPusher.
- `TCPSocketService` (existing) menambah satu tanggung jawab: tulis event ke `tbl_lis_event_queue` saat connect/disconnect/error — handoff flag-based (sesuai rule CLAUDE.md, bukan in-process IPC).

## Schema Database

### Perubahan `tbl_instrument`

```sql
ALTER TABLE tbl_instrument
  ADD COLUMN lis_instrument_id   VARCHAR(50)  NULL,   -- string ID dari LIS (e.g. "INST-TEIF13QE")
  ADD COLUMN lis_api_key         VARCHAR(255) NULL,   -- Bearer token per-alat
  ADD COLUMN order_poll_interval INT          DEFAULT 10,
  ADD COLUMN last_lis_sync_at    DATETIME     NULL,
  ADD COLUMN lis_status_pushed   VARCHAR(20)  NULL,   -- last pushed status (debug aid)
  ADD COLUMN lis_bridge_enabled  BOOLEAN      DEFAULT FALSE;  -- temporary flag for staged cutover; di-drop setelah migrasi selesai
```

`lis_instrument_id` = identifier eksternal untuk payload outbound. `tbl_instrument.id` (int) tetap source-of-truth internal.

### Tabel baru `tbl_lis_event_queue`

```sql
CREATE TABLE tbl_lis_event_queue (
  id            BIGINT      PRIMARY KEY AUTO_INCREMENT,
  instrument_id INT         NOT NULL,
  event_type    ENUM('status','log') NOT NULL,
  payload_json  JSON        NOT NULL,
  send_status   ENUM('pending','sent','failed','skipped') DEFAULT 'pending',
  retry_count   INT         DEFAULT 0,
  error_message TEXT        NULL,
  created_at    DATETIME    DEFAULT CURRENT_TIMESTAMP,
  sent_at       DATETIME    NULL,
  INDEX idx_inst_status (instrument_id, send_status, id)
);
```

Pakai 1 tabel (bukan 2) karena schema identik dan operator lebih mudah monitor 1 backlog.

### Tabel tetap

`tbl_result`, `tbl_order`, `tbl_service_log` — **tidak berubah** secara struktural.
`order_json` / `result_json` sudah MID v1.0 kompatibel.

`tbl_order.failed_at_service` menambah nilai baru: `'order_puller'`.

### Settings (tbl_setting)

| Key | Default | Keterangan |
|---|---|---|
| `lis.base_url` | `https://eazy.vespahobby.xyz` | Global base URL EazyApp |
| `lis.http_timeout` | `30` | Detik |
| `lis.retry_max` | `3` | Max retry per event/result |
| `lis.result_poll_interval` | `5` | Detik antara poll `tbl_result` |
| `lis.status_poll_interval` | `2` | Detik antara poll `tbl_lis_event_queue` event_type=status |
| `lis.log_poll_interval` | `5` | Detik antara poll `tbl_service_log` |

Deprecated keys (tetap dibaca sebagai fallback selama transisi): `lis.api_url`, `lis.api_key`.

## LisBridgeService Internals

### Lifecycle

```python
class LisBridgeService:
    def __init__(self, instrument_id: int):
        self.instrument_id = instrument_id

    async def start(self):
        self._load_config()                       # tbl_instrument + tbl_setting
        await self._verify_with_lis()             # GET /instrument → cache lis_instrument_id
        await self._push_status("online")         # boot signal (direct, bypass queue)
        await asyncio.gather(
            self._supervise(self._result_pusher_loop,   "result_pusher"),
            self._supervise(self._order_puller_loop,    "order_puller"),
            self._supervise(self._status_reporter_loop, "status_reporter"),
            self._supervise(self._log_pusher_loop,      "log_pusher"),
        )

    async def stop(self):
        await self._push_status("offline")        # graceful shutdown
```

`_supervise()` catch exception per-task, log ke `tbl_service_log` level ERROR, restart dengan exponential backoff (1s → 2s → 4s → max 60s). Task lain tidak ikut down.

### ResultPusher

```
loop:
  rows = poll tbl_result WHERE instrument_id=X AND send_status='pending'
                        AND retry_count<retry_max ORDER BY id LIMIT batch_size
  for row in rows:
    body = build_mid_payload(row, instrument)
    response = POST {base}/api/v1/instrument/results
        Headers: Authorization: Bearer <api_key>, Content-Type: application/json
        Body: body
    if 2xx: update send_status='sent', sent_at=now
    elif 422 (validation/unmapped): update send_status='failed' (permanent), error_message=response
    else (5xx/network/timeout):
        retry_count++
        if retry_count >= lis.retry_max: send_status='failed' (exhausted)
        else: keep send_status='pending'  # akan di-retry di cycle berikutnya
        record error_message
  sleep lis.result_poll_interval
```

- **422** = klien error, tidak counted sebagai retry; mark `failed` permanen. Operator retry manual via Web Console (button reset → pending).
- **5xx/network/timeout** = transient. Tetap `pending` selama `retry_count<retry_max` → otomatis di-retry. Setelah exhausted → `failed`. Operator bisa reset manual untuk lanjut retry.
- Pattern yang sama berlaku untuk StatusReporter dan LogPusher.
- Sebelum kirim: `payload["instrument_id"]` di-rewrite dari int internal → `lis_instrument_id` string.

### OrderPuller

```
loop:
  response = GET {base}/api/v1/instrument/orders/pending
      Headers: Authorization: Bearer <api_key>
  if 2xx:
    for order in response.data:
      if NOT exists in tbl_order WHERE JSON_EXTRACT(order_json, '$.order_id') = order.order_id
                                 AND instrument_id = X:
        INSERT tbl_order (instrument_id=X, order_json=order, instrument_status='pending')
  sleep tbl_instrument.order_poll_interval (default 10)
```

- **Dedup** pakai LIS `order_id` sebagai natural key.
- LIS side: order tetap "pending" sampai MidLab POST results → otomatis transition ke `sample_received`. Tidak perlu ack terpisah.
- Order baru otomatis di-pick oleh `TCPSocketService.BroadcastWorker` existing.

### StatusReporter

```
loop:
  events = poll tbl_lis_event_queue WHERE instrument_id=X AND event_type='status' 
                                    AND send_status='pending' ORDER BY id LIMIT batch
  for ev in events:
    POST {base}/api/v1/instrument/status
        Body: ev.payload_json   # e.g. {"status":"online"} or {"status":"error","error_message":"..."}
    if 2xx: mark sent
    else: retry_count++, mark failed
  sleep lis.status_poll_interval
```

Penulis event ke queue:
- `TCPSocketService` saat connect → `{event_type:'status', payload:{status:'online'}}`
- `TCPSocketService` saat disconnect/timeout → `{event_type:'status', payload:{status:'error', error_message:'<reason>'}}`
- `LisBridgeService.start/stop` → langsung POST tanpa queue (boot/shutdown event, tidak boleh di-buffer)

### LogPusher

```
loop:
  cursor = read tbl_setting key 'lis.log_cursor.<instrument_id>' (default epoch)
  logs = poll tbl_service_log WHERE level IN ('WARNING','ERROR')
                              AND logged_at > cursor
                              AND service matches this instrument
                              ORDER BY logged_at LIMIT batch
  for log in logs:
    POST {base}/api/v1/instrument/logs
        Body: { "level":<warn|error>, "message":<msg>, "logged_at":<iso>, 
                "context":{service, instrument_id} }
    if 2xx: cursor = log.logged_at
  persist cursor to tbl_setting
  sleep lis.log_poll_interval
```

Cursor-based — `tbl_service_log` tetap append-only, tidak di-mutate.

Filter "log matches this instrument": `service` field matching `tcp_<id>`, `lis_bridge_<id>`, atau message berisi `[INSTRUMENT <id>]` (sesuai log format CLAUDE.md).

### MID payload builder

```python
def build_mid_payload(result_row, instrument):
    payload = dict(result_row.result_json)
    payload["instrument_id"] = instrument.lis_instrument_id   # int → LIS string
    payload["mid_version"]   = "1.0"
    payload.setdefault("message_id",       f"MSG-{instrument.id}-{result_row.id}")
    payload.setdefault("message_datetime", result_row.received_at.isoformat())
    return payload
```

Lookup strategy LIS (`order_id` → `sample_id` → `patient_id`) **tidak di-handle MidLab** — itu logic LIS side.

## Web Console Changes

### Instrument CRUD form

Tambah 3 field:
- **LIS Instrument ID** (read-only after first sync; di-isi otomatis lewat tombol "Verify with LIS")
- **LIS API Key** (password-masked input)
- **Order Poll Interval** (number, detik, default 10)

Tombol **"Verify with LIS"**: call `GET /instrument` dengan api_key yang baru diinput → tampilkan `name`/`vendor`/`model` dari LIS sebagai konfirmasi sebelum save.

### `/settings` page

Tambah field global:
- LIS Base URL
- HTTP timeout
- Retry max

Hapus field lama (deprecate UI selama 1-2 release): `lis.api_url`, `lis.api_key`.

### Dashboard cards

Tiap instrument card tambah indikator:
- Bridge status (running/degraded/offline)
- Last status pushed (timestamp + value)
- Last order pulled (timestamp + count today)
- Last result pushed (timestamp)
- Event queue backlog count

### Halaman baru `/lis-events`

Tabel monitor `tbl_lis_event_queue`:
- Columns: timestamp | instrument | event_type | payload preview | status | retry | error
- Filter: alat, status, type, tanggal
- Actions: Retry (reset pending), Skip (mark skipped)

### Watchdog

Service list berubah dari:
- `tcp_socket_<id>` × N, `result_sender`, `order_receiver`, `web_console`

Menjadi:
- `tcp_socket_<id>` × N, `lis_bridge_<id>` × N, `web_console`

## Rollout

**Tahap 1** — Schema + libraries (no behavior change)
- Migration SQL untuk `tbl_instrument` + `tbl_lis_event_queue`
- `lib/lis_client.py` — aiohttp wrapper dengan auth, retry, timeout
- Helper functions di `lib/db.py`

**Tahap 2** — LisBridgeService dibangun parallel (`lis_bridge_enabled=false` default)
- Service baru di `services/lis_bridge/`
- ResultSenderService tetap aktif untuk alat dengan flag false
- Manual testing per alat

**Tahap 3** — Per-alat cutover
- Operator isi `lis_api_key` + `lis_instrument_id` via Web Console
- Toggle `lis_bridge_enabled=true` per alat
- ResultSenderService skip alat yang sudah enabled
- Validasi 1-2 alat dulu, baru rollout sisanya

**Tahap 4** — Decommission service lama
- Setelah semua alat stabil >= 1 minggu:
  - Remove systemd units `result_sender`, `order_receiver`
  - Drop kolom `lis_bridge_enabled` (jadi mandatory)
  - Drop setting deprecated
  - Pindahkan `services/result_sender/` & `services/order_receiver/` ke archive

**Tahap 5** — TCP event hook
- Modify `TCPSocketService` untuk tulis ke `tbl_lis_event_queue` saat connect/disconnect/error
- StatusReporter mulai dapat input real-time

### Rollback

- Toggle off `lis_bridge_enabled` → service lama ambil alih
- Stop `lis_bridge_<id>` di Watchdog
- Migration scripts dengan `down()` SQL: `scripts/migrate_lis_api.py` & `_rollback.py`
- `tbl_lis_event_queue` aman di-drop kapan saja

### Testing

- **Unit**: mock httpx untuk 200/401/422/5xx/timeout di `lib/lis_client.py`
- **Integration**: spin LisBridgeService dengan sandbox EazyApp pakai dev api_key dari Postman variable
- **Manual**: 1 alat live (Cobas c-111 / Sysmex) sebelum rollout massal

## Konfigurasi Migrasi

| Lama | Baru |
|---|---|
| `config.yaml: lis.api_url` | `tbl_setting: lis.base_url` (global) |
| `config.yaml: lis.api_key` | `tbl_instrument.lis_api_key` (per-alat) |
| `config.yaml: result_sender.poll_interval` | `tbl_setting: lis.result_poll_interval` |
| `config.yaml: result_sender.retry_max` | `tbl_setting: lis.retry_max` |
| `config.yaml: order_receiver.api_key` | *(hapus)* |
| `config.yaml: order_receiver.port` | *(hapus)* |

## Out of Scope

- Mapping table cache (LIS handle mapping; MidLab kirim raw)
- Heartbeat periodik (status push event-based saja)
- Push log level INFO (cuma WARN/ERROR)
- Adaptive poll interval (fixed per-alat)
- Tetap support LIS lain di luar EazyApp (kalau ada kebutuhan, buat protocol-style adapter terpisah)

## Risk & Open Items

- **API key leak**: Bearer per-alat disimpan plain di `tbl_instrument.lis_api_key`. Mitigasi minimum: log scrubbing (jangan log header), password-mask di UI. Encryption-at-rest = future work.
- **LIS downtime**: ResultPusher backoff transient errors; OrderPuller skip cycle (tidak error fatal). Queue (`tbl_lis_event_queue`) memastikan no event loss.
- **Order dedup race**: Kalau 2 LisBridge instance accidentally jalan untuk alat sama (mis. dari ops manual), bisa double-insert order. Mitigasi: unique index pada `(instrument_id, order_json->>'$.order_id')` — perlu dicheck MySQL version dukung functional index (8.0+).
- **`lis_instrument_id` belum di-set**: Kalau operator belum verify, LisBridge gagal startup. Mitigasi: Watchdog tampilkan error spesifik "lis_instrument_id NULL — verify alat dulu".
