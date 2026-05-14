# Design: User Roles & Raw Communication Logs

**Date:** 2026-05-14
**Status:** Phase B (raw comm logs) implemented 2026-05-14; Phase A (user roles & auth) pending. See `docs/superpowers/plans/2026-05-14-user-roles-and-comm-logs.md`.
**Scope:** Web Console authentication/authorization + raw TCP frame logging viewer

---

## 1. Motivation

Saat ini Web Console MidLab terbuka tanpa autentikasi — siapa pun yang reach service di port web console bisa start/stop service, CRUD instrument, retry results, dan hot-swap protocol. Untuk operasional lab production, dibutuhkan kontrol siapa yang bisa melakukan aksi destructive vs. siapa yang hanya boleh memantau.

Selain itu, untuk troubleshooting alat (terutama alat baru atau alat yang baru di-onboarding), engineer butuh melihat trafik komunikasi mentah TCP per-byte/per-frame antara MidLab dengan alat — bukan hanya log Python level INFO/WARNING/ERROR yang sudah ada. Saat ini, `tbl_result.raw_data` hanya menyimpan pesan ASTM lengkap setelah EOT; tidak ada visibility ke handshake byte (ENQ/ACK/NAK), frame-frame intermediate, atau trafik dua arah secara realtime.

## 2. Goals

1. Web Console memerlukan login; ada dua role: `admin` (full akses) dan `viewer` (read-only).
2. Aksi mutating tercatat di audit log dengan user, action, target, timestamp.
3. Tiap TCPSocketService menulis file `.comm.log` terpisah berisi bidirectional raw frame realtime dalam format readable (annotated ASCII + hex fallback).
4. Logs viewer existing bisa stream file `.comm.log` via SSE dengan dropdown entry baru per alat.

## 3. Non-Goals

- Password reset via email; 2FA; SSO/LDAP integration.
- Per-instrument permissions (semua admin lihat semua alat).
- Search/filter raw comm log di UI (cuma stream + tail; grep file untuk search).
- Audit log export/CSV.
- Comm log persisted di DB (file only, untuk volume tinggi).

---

## 4. Architecture — User Roles & Auth

### 4.1 Database schema

Tabel baru, dibuat via `scripts/migrate_add_auth.sql`:

```sql
CREATE TABLE tbl_user (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('admin','viewer') NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  force_password_change BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME NULL
);

CREATE TABLE tbl_session (
  token VARCHAR(64) PRIMARY KEY,
  user_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ip_address VARCHAR(45) NULL,
  FOREIGN KEY (user_id) REFERENCES tbl_user(id) ON DELETE CASCADE,
  INDEX idx_expires (expires_at)
);

CREATE TABLE tbl_audit_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  username VARCHAR(64) NOT NULL,   -- denormalized so log stays readable after user delete
  action VARCHAR(64) NOT NULL,
  target VARCHAR(255) NULL,
  metadata JSON NULL,
  ip_address VARCHAR(45) NULL,
  logged_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_logged_at (logged_at),
  INDEX idx_user (user_id),
  INDEX idx_action (action)
);
```

Models di `lib/db.py`: `User`, `Session`, `AuditLog` (mengikuti pola SQLAlchemy yang sudah ada).

### 4.2 `lib/auth.py` (NEW)

Modul tunggal untuk auth concerns:

- `hash_password(plain) -> str` — bcrypt via `passlib.hash.bcrypt`.
- `verify_password(plain, hashed) -> bool`.
- `create_session(user_id, ip) -> token` — generate `secrets.token_urlsafe(48)`, insert `tbl_session` dengan `expires_at = now + 8h`.
- `load_session(token) -> User | None` — query `tbl_session` JOIN `tbl_user`, cek `expires_at > now` dan `user.is_active`, update `last_seen_at`. Return `None` kalau invalid/expired.
- `revoke_session(token)` — delete row.
- `purge_expired_sessions()` — dipanggil periodik (lazy: setiap N login, atau startup) untuk delete row expired.
- `require_role(*roles)` — FastAPI dependency factory. Membaca cookie `midlab_session`, panggil `load_session`, cek `user.role in roles`. Behavior fail:
  - Request `Accept: text/html` → `RedirectResponse('/login?next=<encoded_path>')`.
  - Lainnya → `HTTPException(401)`.
- `audit(user, action, target=None, metadata=None, request=None)` — insert `tbl_audit_log` row, ambil IP dari request.

### 4.3 Endpoints baru di `services/web_console/api.py`

| Method & Path | Auth | Body | Behavior |
|---|---|---|---|
| `GET /login` | public | — | render `login.html` |
| `POST /login` | public | `username, password, next?` | verify; set cookie `midlab_session=<token>; HttpOnly; SameSite=Lax; Max-Age=28800`; audit `login.success`/`login.fail`; redirect ke `next` atau `/services`; rate-limit 5 fail/menit/IP (in-memory dict, OK karena single web_console process) |
| `POST /logout` | any role | — | revoke session, clear cookie, redirect ke `/login` |
| `GET /users` | admin | — | render `users.html` (list semua user) |
| `POST /api/users` | admin | `username, password, role` | create user; audit `user.create` |
| `PUT /api/users/{id}` | admin | `role?, is_active?, password?` | update user; audit `user.update` |
| `DELETE /api/users/{id}` | admin | — | delete user (cascade sessions); audit `user.delete`; tolak kalau itu user sendiri |
| `GET /audit` | admin | — | render `audit.html` (paginated list, filter user/action/date range) |
| `GET /api/audit` | admin | query params | JSON paginated |

Semua endpoint mutating yang sudah ada (`POST /api/services/.../start|stop|restart`, instrument CRUD, retry/cancel result, protocol hot-swap) ditambahi `Depends(require_role('admin'))` dan panggil `audit(...)` setelah aksi sukses. Endpoint read (logs, results, orders, instruments list) ditambahi `Depends(require_role('admin','viewer'))`.

### 4.4 Bootstrap admin

Di `services/web_console/main.py` startup hook:
1. Cek `SELECT COUNT(*) FROM tbl_user`. Kalau 0:
2. Baca `MIDLAB_ADMIN_USER` (default `admin`) & `MIDLAB_ADMIN_PASS` (default `admin`) dari env.
3. Insert user role `admin` dengan `force_password_change=TRUE` kalau pass default.
4. Log warning ke service log: "Bootstrap admin created — force_password_change set".

`scripts/install.sh` ditambah:
- Prompt user untuk admin username & password (echo off), kalau interactive.
- Export jadi env var di systemd unit / `.env`.

Login handler: kalau `user.force_password_change=TRUE`, setelah verify password redirect ke `/change-password` (bukan ke `next`); semua endpoint lain redirect ke sini juga sampai password diubah.

### 4.5 Template changes

- **NEW** `login.html`, `users.html`, `audit.html`, `change_password.html` — apple-style consistent dengan template existing.
- **MODIFIED** `services.html`, `logs.html`, `protocols.html`, semua page existing — tambah header bar: username + role badge + logout button.
- Defense in depth: tombol `start/stop/restart`, "Add Instrument", "Hot-swap" di-render `disabled` (atau di-hide) kalau current user `role='viewer'`. Backend tetap enforce dengan `require_role('admin')`.

### 4.6 Role enforcement matrix

| Endpoint group | admin | viewer |
|---|---|---|
| `GET /services`, `/logs`, `/results`, `/orders`, `/instruments` (HTML & API) | ✅ | ✅ |
| `POST /api/services/*/start|stop|restart` | ✅ | ❌ |
| `POST/PUT/DELETE /api/instruments/*` | ✅ | ❌ |
| `POST /api/protocols/*/hot-swap` | ✅ | ❌ |
| `POST /api/results/*/retry`, `POST /api/orders/*/cancel` | ✅ | ❌ |
| `POST/PUT/DELETE /api/users/*`, `GET /audit` | ✅ | ❌ |

### 4.7 Audited actions

- `login.success`, `login.fail`, `logout`, `password.change`
- `service.start`, `service.stop`, `service.restart`
- `instrument.create`, `instrument.update`, `instrument.delete`, `instrument.protocol_swap`
- `result.retry`, `order.cancel`, `order.retry`
- `user.create`, `user.update`, `user.delete`

Tidak diaudit: GET endpoints, SSE log stream (high volume, low security value).

---

## 5. Architecture — Raw Communication Logs

### 5.1 `lib/comm_logger.py` (NEW)

```python
class CommLogger:
    """Per-instrument bidirectional raw byte logger to .comm.log file."""
    _cache: dict[int, "CommLogger"] = {}

    @classmethod
    def for_instrument(cls, instrument_id: int) -> "CommLogger": ...

    def rx(self, data: bytes) -> None: ...   # logs '← RX <decoded>'
    def tx(self, data: bytes) -> None: ...   # logs '→ TX <decoded>'
```

Internal:
- Lazy-init `logging.Logger` per instrument dengan `RotatingFileHandler('/var/log/midlab/tcp_<id>.comm.log', maxBytes=50*1024*1024, backupCount=5)`.
- Formatter: `%(asctime)s.%(msecs)03d [tcp_%(instrument_id)s] %(message)s`.
- `propagate=False` supaya nggak nyebar ke root logger.
- Singleton per `instrument_id` di `_cache` dict.
- Error pada write → catch, log warning sekali ke service log biasa, set internal flag biar nggak log warning berulang.

### 5.2 Decoder `_decode_for_log(data: bytes) -> str`

Mapping:
| Byte | Render |
|---|---|
| 0x02 | `<STX>` |
| 0x03 | `<ETX>` |
| 0x04 | `<EOT>` |
| 0x05 | `<ENQ>` |
| 0x06 | `<ACK>` |
| 0x15 | `<NAK>` |
| 0x17 | `<ETB>` |
| 0x0D | `<CR>` |
| 0x0A | `<LF>` |
| 0x20–0x7E | char as-is |
| lain | `\xNN` (lowercase hex) |

Contoh: `b'\x021H|\\^&|||COBAS\r\x03D2\r\n'` → `<STX>1H|\^&|||COBAS<CR><ETX>D2<CR><LF>`.

### 5.3 Integrasi di `services/tcp_socket/`

Wrapper tipis di sekitar setiap I/O call:

```python
data = await reader.read(4096)
if data:
    comm_logger.rx(data)
    # ... existing frame decoder logic
```

```python
comm_logger.tx(payload)
writer.write(payload)
await writer.drain()
```

Berlaku untuk:
- Server mode receive loop (semua byte yang diterima dari alat).
- Client mode receive loop.
- Setiap `writer.write` untuk ACK/NAK/EOT/ENQ handshake, broadcast order payload, query response payload.

Tidak ada perubahan struktur control flow — hanya tambah dua baris di tiap titik I/O.

### 5.4 Web Console integration

Backend di `services/web_console/api.py`:

- Service registry enrichment: untuk tiap instrument aktif, expose entry virtual dengan id `tcp_<id>__comm`, `display_name = "{instrument.name} — Communication"`.
- `GET /api/logs/{service}` & `/api/logs/{service}/stream`: kalau `service.endswith('__comm')`, resolve file path ke `/var/log/midlab/tcp_<base_id>.comm.log` (strip `__comm`, baca `.comm.log`). Selain itu pakai resolver existing.

Frontend `logs.html`:
- Dropdown menampilkan display_name. Tidak perlu perubahan logic — sudah pakai display_name dari API.

### 5.5 Performance

Trafik ASTM normal per alat: ~puluhan frame/menit. Sync file write via `RotatingFileHandler` sudah aman. Tidak perlu `QueueHandler`/async untuk volume ini.

Kalau di masa depan ada alat high-throughput (mis. streaming continuous data), swap ke `QueueHandler + QueueListener` di same module tanpa ubah call site.

### 5.6 Retention

50 MB × 5 file × N alat. Untuk N=10 alat: 2.5 GB max disk usage. Nggak perlu cron cleanup tambahan — rotation handle.

---

## 6. Data flow examples

### 6.1 Login & authorized action

```
Browser → GET /services (no cookie)
        → 302 /login?next=/services
        → POST /login {admin, P@ss123}
        → bcrypt verify OK
        → INSERT tbl_session (token, user_id, expires=now+8h)
        → INSERT tbl_audit_log (login.success)
        → Set-Cookie: midlab_session=<token>; Max-Age=28800
        → 302 /services
Browser → POST /api/services/tcp_3/stop (with cookie)
        → load_session(cookie) → User(role=admin) → OK
        → watchdog.stop_service('tcp_3') (via run_in_executor)
        → INSERT tbl_audit_log (service.stop, target=tcp_3, metadata={pid:1234})
        → 200 {ok}
```

### 6.2 ASTM session dengan comm logging

```
Alat → b'\x05'                                       (ENQ)
  → reader.read → comm_logger.rx → file: '... [tcp_3] ← RX <ENQ>'
  → FrameDecoder.handle_enq
  → writer: b'\x06'                                  (ACK)
  → comm_logger.tx → file: '... [tcp_3] → TX <ACK>'
Alat → b'\x021H|\\^&|||COBAS\r\x03D2\r\n'            (Header frame)
  → comm_logger.rx → file: '... ← RX <STX>1H|\^&|||COBAS<CR><ETX>D2<CR><LF>'
  → FrameDecoder.process_frame
  → comm_logger.tx (ACK)
... (lebih banyak frame) ...
Alat → b'\x04'                                       (EOT)
  → comm_logger.rx → file: '... ← RX <EOT>'
  → ProtocolModule.parse(accumulated) → INSERT tbl_result row
```

Logs viewer realtime saat user pilih `tcp_3__comm`:
```
22:48:01.123 [tcp_3] ← RX <ENQ>
22:48:01.125 [tcp_3] → TX <ACK>
22:48:01.340 [tcp_3] ← RX <STX>1H|\^&|||COBAS<CR><ETX>D2<CR><LF>
22:48:01.342 [tcp_3] → TX <ACK>
22:48:01.500 [tcp_3] ← RX <EOT>
```

---

## 7. Files touched

**Auth & roles:**
- `lib/db.py` — add `User`, `Session`, `AuditLog` SQLAlchemy models
- `lib/auth.py` — **NEW** module
- `services/web_console/api.py` — login/logout/users/audit endpoints; wrap existing mutating endpoints dengan `require_role('admin')`; add audit calls
- `services/web_console/main.py` — bootstrap admin on startup
- `services/web_console/templates/login.html` — **NEW**
- `services/web_console/templates/users.html` — **NEW**
- `services/web_console/templates/audit.html` — **NEW**
- `services/web_console/templates/change_password.html` — **NEW**
- `services/web_console/templates/*.html` (existing) — add header bar, role-aware UI hiding
- `services/web_console/static/` — minor CSS for login/header
- `scripts/install.sh` — prompt admin credentials
- `scripts/migrate_add_auth.sql` — **NEW**

**Comm log:**
- `lib/comm_logger.py` — **NEW** module
- `services/tcp_socket/` — instrumentasi rx/tx call sites (server mode & client mode)
- `services/web_console/api.py` — service registry enrichment untuk `__comm` virtual entries; log file resolver handle `__comm` suffix

---

## 8. Error handling

| Scenario | Behavior |
|---|---|
| Login fail (wrong pass) | 401 + `audit(login.fail)` + rate-limit counter increment |
| Rate-limit exceeded (>5 fail/min/IP) | 429 + `audit(login.fail, metadata={rate_limited:true})` |
| Session expired mid-request (HTML) | 302 `/login?next=<path>` |
| Session expired mid-request (API/JSON) | 401 |
| User soft-deleted (`is_active=FALSE`) | Treat as expired; revoke sessions immediately on update |
| Comm log file write error | try/except; log warning ke service log; set flag agar warning nggak berulang; socket service tetap jalan |
| Comm log dir nggak writable saat init | log warning; `CommLogger.rx/tx` jadi no-op |
| `tbl_user` race pada bootstrap (multiple workers) | Single web_console process — nggak terjadi. (Documented assumption.) |

---

## 9. Testing strategy

**Unit (`tests/unit/`):**
- `test_auth.py` — hash/verify roundtrip; session token entropy; session expiry; `require_role` allow/deny matrix; audit helper writes row.
- `test_comm_logger.py` — `_decode_for_log` semua control char; printable ASCII; non-printable → `\xNN`; mixed sequence; bytes kosong; bytes panjang.

**Integration (`tests/integration/`):**
- `test_auth_flow.py` — TestClient login success/fail, cookie set, accessing protected endpoint, viewer denied admin endpoint, session expiry simulated.
- `test_audit_log.py` — service stop → audit row inserted dengan field benar.

**Manual smoke:**
- Bootstrap install, login default admin, force password change.
- Create viewer user; login as viewer; verify tombol start/stop disabled & API 401.
- Jalankan `cobas_test_sender.py`, buka `/logs` dropdown `Cobas C111 — Communication`, verify bidirectional trace lengkap & format readable.

---

## 10. Open questions / future considerations

- **Session storage**: pakai DB row sekarang (`tbl_session`). Kalau ada banyak concurrent users dan latency issue, migrate ke in-memory cache (redis) — saat ini overkill.
- **Audit log retention**: belum ada cleanup. Kalau growth jadi masalah, tambah cron purge > 90 hari nanti.
- **Comm log search**: kalau dibutuhkan, tambah endpoint grep di backend nanti.
- **API token (non-cookie)**: kalau ke depan ada integrasi script eksternal, tambah token-based auth selain cookie session — out of scope sekarang.
