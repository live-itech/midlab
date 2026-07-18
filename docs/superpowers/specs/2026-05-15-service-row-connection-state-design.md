# Services Menu — Row Coloring by Instrument Connection State

**Status:** Approved 2026-05-15, ready to implement.

## Goal

Di menu **Services** web console, baris untuk service `tcp_<id>` muncul **merah**
saat MidLab tidak bisa konek ke alat (client mode) atau gagal bind listener
(server mode). Pengguna lab bisa scan satu kali tanpa perlu buka log file.

## Non-goals

- Tidak menambah polling aktif ke IP alat (sudah ada endpoint
  `POST /api/instruments/<id>/test-connection` untuk one-shot probe manual).
- Tidak menyentuh dashboard summary atau LIS bridge state — itu pipeline
  terpisah (`instruments_lis`).
- Tidak schema migration tambahan — data state dibaca dari tabel yang sudah ada
  (`tbl_lis_event_queue`).

## Data Source

`tbl_lis_event_queue` row dengan `event_type='status'` dan
`payload_json = {"status": "online"|"offline"|"error", "error_message": "..."}`.

Pemilik tulis: `services/tcp_socket/service.py:_emit_lis_status()`.
Saat ini sudah dipanggil di transisi connect / disconnect / client-mode error.
Spec ini menambahkan satu call site lagi (server-mode bind failure).

## State Mapping

| `connection_state` (derived) | Row | Badge |
|---|---|---|
| `online` | normal | hijau **Connected** |
| `offline` | tint merah | merah **Disconnected** |
| `error` | tint merah | merah **Error** (tooltip = `error_message`) |
| `unknown` (no event yet) | normal | hijau **Running** (perilaku lama) |
| Process not running | normal | merah **Stopped** (perilaku lama) |

Rule derivation: ambil event `event_type='status'` terbaru per instrument
(ORDER BY id DESC, LIMIT 1) → `payload.status` jadi `connection_state`.
Kalau tidak ada event → `unknown`.

## Components

### 1. `services/tcp_socket/service.py`

Tambah satu call site di `_run_server_mode` OSError handler:

```python
except OSError as e:
    self._logger.error(f"{self._tag} Gagal bind ...: {e}")
    self._emit_lis_status("error", str(e))   # NEW
    if not self._running:
        break
    await asyncio.sleep(RECONNECT_DELAY)
```

Konsekuensi: setiap bind retry yang gagal akan insert satu row ke
`tbl_lis_event_queue`. Acceptable karena bind retry interval = 5s.

### 2. `lib/db.py` — helper baru

```python
def get_latest_status_per_instrument() -> dict[int, dict]:
    """
    Return {instrument_id: {"status": str, "error_message": str|None, "at": datetime}}
    dari event_type='status' terbaru per instrument di tbl_lis_event_queue.
    Empty dict jika error.
    """
```

Implementasi: subquery max(id) per instrument_id, atau loop di Python untuk
sederhana (tabel kecil per instrument). MySQL dialect — pakai correlated
subquery atau window function. Untuk simplicity dan kompatibilitas SQLite
(test env), pakai loop di Python:

```python
session.query(TblInstrument.id).all()  # all ids
for iid in ids:
    latest = session.query(TblLisEventQueue).filter(
        instrument_id=iid, event_type='status'
    ).order_by(id.desc()).first()
```

Cukup efisien karena jumlah instrument ≤ ~20.

### 3. `services/web_console/api.py`

**ServiceStatusResponse** tambah dua field:
```python
connection_state: Optional[str] = None  # online|offline|error|unknown
connection_error: Optional[str] = None
```

**list_services** — setelah loop watchdog statuses:
```python
state_map = get_latest_status_per_instrument()
for r in out:
    if r.name.startswith("tcp_") and not "__comm" in r.name and r.instrument_id:
        ev = state_map.get(r.instrument_id)
        if ev:
            r.connection_state = ev["status"]
            r.connection_error = ev.get("error_message")
        else:
            r.connection_state = "unknown"
```

Note: untuk virtual `__comm` row, connection_state tetap None (tidak relevan).

### 4. `services/web_console/templates/services.html`

```javascript
const isVirtual = s.name.includes('__comm');
const isDisconnected = !isVirtual
    && (s.connection_state === 'offline' || s.connection_state === 'error');
const rowClass = isDisconnected ? 'row-danger' : '';

// status badge:
let statusBadge;
if (isVirtual) statusBadge = '<span class="badge badge-blue">Virtual</span>';
else if (!s.running) statusBadge = '<span class="badge badge-red">Stopped</span>';
else if (s.connection_state === 'offline') statusBadge = '<span class="badge badge-red" title="${error}">Disconnected</span>';
else if (s.connection_state === 'error') statusBadge = '<span class="badge badge-red" title="${error}">Error</span>';
else if (s.connection_state === 'online') statusBadge = '<span class="badge badge-green">Connected</span>';
else statusBadge = '<span class="badge badge-green">Running</span>';  // unknown fallback
```

CSS class baru `.row-danger td { background: rgba(220,38,38,0.08); }` — ditempel
di `<style>` inline di services.html supaya tidak menyentuh base.html / file CSS
global.

## Data Flow

1. `tcp_socket` connect / disconnect / bind error → `_emit_lis_status()` →
   INSERT `tbl_lis_event_queue`.
2. Web Console UI polls `/api/services` setiap 10 detik (sudah ada
   `App.startAutoRefresh(loadServices, 10000)`).
3. `list_services` query satu kali per request: `get_latest_status_per_instrument()`.
4. Frontend render row: merah jika `connection_state` ∈ `{offline, error}`.

## Error Handling

- DB query gagal di helper → return `{}` → semua row `connection_state=unknown`
  → fallback ke perilaku lama. Tidak ada false-positive merah.
- Helper try/except sekitar query; logger.warning jika gagal.

## Testing

- Unit: `tests/test_db_latest_status.py` — pakai SQLite in-memory:
  - empty queue → `{}`
  - single event per instrument → mapping benar
  - multiple events per instrument → ambil yang `id` terbesar
  - filter `event_type='status'` (ignore `event_type='log'`)
- Integration test untuk tcp_socket bind error skip (env permission /etc/midlab/config.yaml
  diketahui memblokir tests pre-existing).

## Out of Scope (sengaja tidak)

- Tidak menambah indikator untuk LIS bridge connection (sudah punya UI sendiri di dashboard).
- Tidak mengubah polling cadence (tetap 10s).
- Tidak menambah notification/sound untuk transisi merah (bisa di iterasi
  berikutnya kalau diperlukan).

## File Manifest

- `services/tcp_socket/service.py` — +1 baris
- `lib/db.py` — +1 helper (~20 baris)
- `services/web_console/api.py` — +2 field di response model, +~10 baris di list_services
- `services/web_console/templates/services.html` — ubah render badge + tambah class + CSS (~30 baris diff)
- `tests/test_db_latest_status.py` — file baru (~50 baris)

Total estimasi: ~110 baris diff.
