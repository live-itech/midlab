# Dokumen Handover — MidLab Development

**Dari:** Claude (AI Assistant)
**Kepada:** Developer yang melanjutkan
**Tanggal:** 17 April 2026
**Versi App:** 1.0.0

---

## 1. Ringkasan Project

MidLab adalah middleware Python untuk menghubungkan alat laboratorium klinik ke Laboratory
Information System (LIS). Alat berkomunikasi via TCP (ASTM/HL7), MidLab menerima hasil dan
meneruskan ke LIS via REST API, serta menerima order dari LIS dan mengirim ke alat.

**Total codebase:** ~9.200 baris Python + ~2.700 baris frontend (HTML/CSS/JS)

---

## 2. Status Saat Handover

### Apa yang Sudah Selesai (100%)

| Komponen | Status | Keterangan |
|---|---|---|
| `lib/` (config, db, models, utils) | Selesai | Shared library, DB schema, models |
| `protocols/base.py` | Selesai | Abstract base class + dynamic loader |
| `protocols/astm/` | Selesai | Parser, builder, module (uni + bidir) |
| `protocols/hl7/` | Selesai | Parser, builder, module (uni + bidir) |
| `services/tcp_socket/` | Selesai | Service, receiver, broadcast_worker, query_handler |
| `services/result_sender/` | Selesai | Poll DB → POST ke LIS |
| `services/order_receiver/` | Selesai | REST API terima order dari LIS |
| `services/web_console/` | Selesai | API + Watchdog + semua halaman UI |
| Systemd unit files | Selesai | 5 unit files (web-console, tcp@, lis-bridge@, result-sender, order-receiver) + install script |
| Dokumentasi | Selesai | STRUKTUR.md, PANDUAN-ALAT-BARU.md, HANDOVER.md |

### Apa yang Belum Ada / Perlu Dilanjutkan

| Item | Prioritas | Keterangan |
|---|---|---|
| **BCI Protocol Module** | Medium | Placeholder ada (`protocols/bci/`), belum ada implementasi. Perlu spesifikasi dari vendor. |
| **Unit Tests** | High | Belum ada test suite. Semua testing dilakukan manual. Prioritaskan test untuk parser ASTM dan HL7. |
| **Authentication Web Console** | Medium | Belum ada login/session. API key opsional ada di config tapi belum enforce di halaman HTML. |
| **HTTPS/TLS** | Medium | Semua komunikasi masih HTTP plain. Untuk production, gunakan reverse proxy (nginx) dengan SSL. |
| **Database Migration** | Low | Belum ada migration tool (Alembic). Schema dikelola manual via SQL. |
| **Monitoring/Alerting** | Low | Alert hanya tampil di dashboard. Belum ada notifikasi ke email/Telegram. |
| **Backup Strategy** | Medium | Belum ada backup otomatis untuk database. |
| **Rate Limiting** | Low | API endpoint belum ada rate limiting. |
| **Protocols Page** | Low | Route `/protocols` ada tapi mengarah ke halaman instruments. Bisa dibuat halaman sendiri untuk manage protocol modules. |

---

## 3. Arsitektur & Keputusan Desain

### Kenapa Multi-Service (bukan Monolith)?

Setiap service berdiri sendiri karena:
- **Isolasi fault** — jika satu TCP service crash, yang lain tidak terdampak
- **Scalability** — bisa deploy service di server berbeda jika perlu
- **Independent restart** — restart satu alat tidak mengganggu alat lain
- **Simplicity** — setiap service punya satu tanggung jawab jelas

### Kenapa MySQL Flag-Based (bukan Message Queue)?

Komunikasi antar service via kolom status di MySQL (`send_status`, `instrument_status`):
- Tidak perlu dependency tambahan (RabbitMQ, Redis, dll)
- State tersimpan persisten — survive restart
- Mudah di-debug via query SQL langsung
- Cukup untuk throughput lab klinik (ratusan record/hari, bukan jutaan)

**Trade-off:** Polling-based (tiap N detik), bukan event-driven. Untuk volume lab klinik ini acceptable.

### Kenapa Vanilla JS (bukan React/Vue)?

- Zero build step — tidak perlu Node.js, npm, webpack
- Mudah di-deploy — static files saja
- Cukup untuk complexity dashboard ini
- Satu file `app.js` (~380 baris) sudah cover semua kebutuhan

### Kenapa Protocol Module Dynamic Loading?

Protocol di-load via `importlib` berdasarkan nama di database:
- Bisa tambah protocol baru tanpa ubah kode service
- Hot-swap: ganti protocol instrument cukup update DB + restart TCP service
- Setiap protocol module self-contained dalam direktori sendiri

---

## 4. File-File Kunci yang Harus Dipahami

Sebelum mulai develop, baca file-file ini secara berurutan:

### Wajib Baca

1. **`CLAUDE.md`** — Spesifikasi lengkap app (contract, schema, rules)
2. **`lib/db.py`** — SQLAlchemy models dan helper functions
3. **`lib/models.py`** — ResultObject dan OrderObject dataclass
4. **`protocols/base.py`** — Abstract class + registry + dynamic loader
5. **`services/tcp_socket/service.py`** — Orchestrator utama TCP service

### Referensi

6. **`protocols/astm/module.py`** — Contoh implementasi protocol lengkap
7. **`services/web_console/api.py`** — Semua REST API endpoint
8. **`services/web_console/watchdog.py`** — Mekanisme start/stop/restart service

---

## 5. Hal-Hal Penting yang Harus Diketahui

### Database

- **Connection pooling** wajib via SQLAlchemy `QueuePool` (sudah di `lib/db.py`)
- **Flag ownership** — JANGAN update `send_status` dari service selain ResultSender.
  Jangan update `instrument_status` dari service selain TCPSocket. Kecuali Web Console
  untuk fitur manual retry/cancel.
- **Config** di `/etc/midlab/config.yaml` — BUKAN di environment variable

### Protocol Module

- `_PROTOCOL_REGISTRY` di `protocols/base.py` adalah satu-satunya tempat mendaftarkan
  protocol baru. Key harus UPPERCASE.
- Module di-cache setelah pertama kali load. Gunakan `clear_module_cache()` jika perlu
  reload saat runtime.
- Method `parse()` TIDAK BOLEH raise exception untuk data yang tidak sempurna.
  Isi `parse_errors` list di ResultObject sebagai gantinya.

### TCP Service

- Satu instance service = satu instrument. JANGAN jalankan satu service untuk multiple instrument.
- `asyncio.Lock` digunakan untuk shared socket antara ResultReceiver dan BroadcastWorker.
  JANGAN akses socket tanpa acquire lock di mode bidirectional.
- Mode `connection=server`: MidLab listen → alat connect. Mode `connection=client`: MidLab
  connect ke alat.

### Web Console

- Watchdog menjalankan service lain sebagai **subprocess** (bukan thread).
  PID di-track di `/var/run/midlab/` atau fallback ke `/opt/midlab/run/`.
- **Starlette 1.0.0** — `TemplateResponse` signature: `(request, name, context)`,
  BUKAN `(name, {"request": request, ...})` seperti versi lama.
- Static files di-mount dari `services/web_console/static/`, bukan dari project root.

### Logging

- Semua log ke `/var/log/midlab/` via `RotatingFileHandler`
- Format: `[LEVEL] [SERVICE] [INSTRUMENT] pesan`
- Helper: `get_logger(service_name, instrument_id=None)` di `lib/utils.py`
- JANGAN gunakan `print()` untuk logging di production code

### SIGTERM Handling

- Setiap service HARUS handle SIGTERM gracefully (cleanup socket, close DB, flush log)
- Sudah diimplementasi di `tcp_socket/service.py` dan `result_sender/service.py`
- Systemd mengirim SIGTERM saat `systemctl stop`

---

## 6. Dependency & Environment

### Python Dependencies (`requirements.txt`)

| Package | Digunakan Oleh | Fungsi |
|---|---|---|
| fastapi | WebConsole, OrderReceiver | REST API framework |
| uvicorn | WebConsole, OrderReceiver | ASGI server |
| sqlalchemy | Semua service | ORM + connection pooling |
| pymysql | Semua service | MySQL driver |
| cryptography | pymysql | Auth plugin MySQL (sha256) |
| pyyaml | Semua service | Config loader |
| aiohttp | ResultSender | Async HTTP client ke LIS |
| pydantic | WebConsole, OrderReceiver | Request/response validation |
| jinja2 | WebConsole | HTML template rendering |
| python-dotenv | Opsional | Load .env file |

### System Requirements

- Python 3.10+ (tested: 3.12)
- MySQL 8.0+
- Linux dengan systemd
- User system: `midlab`

### File & Direktori Penting di Luar Codebase

| Path | Fungsi |
|---|---|
| `/etc/midlab/config.yaml` | Konfigurasi utama semua service |
| `/var/log/midlab/` | Log files |
| `/var/run/midlab/` | PID files watchdog (fallback: `/opt/midlab/run/`) |
| `/etc/systemd/system/midlab-*.service` | Systemd unit files |

---

## 7. Cara Development Lokal

### Setup Awal

```bash
# Clone/copy project
cd /opt/midlab

# Install dependencies
pip3 install -r requirements.txt

# Pastikan MySQL running + database ready
mysql -u midlab -pmidlab_secret midlab_db -e "SHOW TABLES;"

# Pastikan config ada
cat /etc/midlab/config.yaml

# Pastikan direktori log writable
ls -la /var/log/midlab/
```

### Jalankan untuk Development

```bash
# Terminal 1: Web Console (ada auto-reload di uvicorn)
python3 services/web_console/main.py

# Terminal 2: Result Sender
python3 services/result_sender/main.py

# Terminal 3: Order Receiver
python3 services/order_receiver/main.py

# Terminal 4: TCP service untuk instrument ID 1
python3 services/tcp_socket/main.py --instrument-id 1
```

### Workflow Ubah Protocol Module

1. Edit file di `protocols/<nama>/`
2. Restart TCP service yang menggunakan protocol itu
3. Kirim test data dari alat atau simulator
4. Cek log: `tail -f /var/log/midlab/tcp_<id>.log`
5. Cek database: `SELECT * FROM tbl_result ORDER BY id DESC LIMIT 5;`

### Workflow Ubah Web Console

1. Edit file di `services/web_console/`
2. Untuk perubahan Python (api.py, watchdog.py): restart service
3. Untuk perubahan frontend (HTML/CSS/JS): refresh browser saja (static files)

---

## 8. Known Issues & Quirks

1. **Starlette/Jinja2 compatibility** — Jinja2 versi 3.1.2 (bawaan Ubuntu) crash
   dengan Starlette 1.0.0 karena dict cache key issue. Harus upgrade ke Jinja2 >=3.1.4.
   Sudah di-fix dengan `pip install --upgrade jinja2`.

2. **Watchdog RUN_DIR** — `/var/run/midlab` butuh permission root untuk create.
   Kode sudah ada fallback ke `/opt/midlab/run/` jika tidak writable.
   Untuk production, `install.sh` akan membuat directory ini.

3. **Static mount path** — Awalnya `main.py` mount static dari project root `/opt/midlab/static`
   (salah). Sudah dipindah ke `api.py` yang mount dari `services/web_console/static/` (benar).

4. **MySQL user auth** — Ubuntu 24.04 MySQL default pakai `caching_sha2_password`.
   pymysql perlu `mysql_native_password` atau install package `cryptography`.
   User MySQL harus dibuat dengan: `IDENTIFIED WITH mysql_native_password BY 'password'`

5. **Protocol BCI** — Hanya placeholder. Jika ada alat BCI, module perlu diimplementasi.
   Registry entry sudah ada di `_PROTOCOL_REGISTRY`.

---

## 9. Roadmap / Saran Pengembangan

### Short-term (1-2 minggu)

- [ ] Buat unit tests untuk ASTM parser dan HL7 parser (prioritas tertinggi)
- [ ] Tambah login/authentication untuk Web Console
- [ ] Buat halaman Protocols terpisah (saat ini redirect ke Instruments)
- [ ] Tambah validasi input yang lebih ketat di OrderReceiver API

### Medium-term (1-2 bulan)

- [ ] Setup nginx reverse proxy + SSL untuk production
- [ ] Implementasi database migration dengan Alembic
- [ ] Tambah notifikasi (email/Telegram) untuk alert critical
- [ ] Buat backup script otomatis untuk MySQL
- [ ] Dashboard chart (grafik result per hari, per instrument)

### Long-term (3+ bulan)

- [ ] Implementasi protocol module tambahan sesuai kebutuhan alat
- [ ] Multi-tenant support (beberapa lab dalam satu instance)
- [ ] Audit trail / activity log untuk compliance
- [ ] REST API versioning (v1, v2)
- [ ] Docker containerization untuk deployment yang lebih mudah

---

## 10. Kontak & Referensi

### Dokumentasi di Codebase

| File | Isi |
|---|---|
| `CLAUDE.md` | Spesifikasi teknis lengkap (contract, schema, rules) |
| `STRUKTUR.md` | Diagram arsitektur + struktur direktori + penjelasan service |
| `PANDUAN-ALAT-BARU.md` | Panduan hardware + software menambah alat baru, contoh konfigurasi per vendor, checklist go-live |
| `HANDOVER.md` | Dokumen ini |

### Referensi Eksternal

- ASTM E1381 Standard: Spesifikasi low-level transport protocol
- ASTM E1394 Standard: Spesifikasi record format (H/P/O/R/L)
- HL7 v2.x: https://www.hl7.org/implement/standards/
- MLLP (Minimal Lower Layer Protocol): transport layer untuk HL7
- FastAPI docs: https://fastapi.tiangolo.com/
- SQLAlchemy docs: https://docs.sqlalchemy.org/

---

## 11. Deployment Production Checklist

Gunakan checklist ini sebelum go-live di environment production.
Untuk langkah instalasi lengkap (deploy dari git → systemd), lihat **[INSTALL.md](INSTALL.md)**.

### Infrastructure

- [ ] MySQL 8.0+ terinstall, database `midlab_db` sudah dibuat dengan schema terbaru
- [ ] User MySQL `midlab` dibuat dengan `mysql_native_password` (bukan `caching_sha2_password`)
- [ ] Python 3.10+ terinstall, semua dependency di `requirements.txt` sudah di-install
- [ ] User system `midlab` sudah dibuat: `sudo useradd -r -s /bin/false midlab`
- [ ] Direktori log writable oleh user `midlab`: `/var/log/midlab/`
- [ ] Direktori run writable oleh user `midlab`: `/var/run/midlab/` atau `/opt/midlab/run/`
- [ ] File config ada dan lengkap: `/etc/midlab/config.yaml`
- [ ] `lis_api_url` di config sudah diisi dengan URL LIS yang benar
- [ ] Script install sudah dijalankan: `sudo bash /opt/midlab/scripts/install.sh`

### Network & Firewall

- [ ] Port 8000 (Web Console) dapat diakses dari client jaringan internal
- [ ] Port 8001 (Order Receiver) dapat diakses dari server LIS
- [ ] Port yang digunakan alat (misal: 9100) terbuka di firewall jika MidLab mode `server`
- [ ] Semua alat lab dapat di-ping dari server MidLab
- [ ] IP address semua alat sudah static (bukan DHCP)

### Services

- [ ] Semua 5 systemd unit file sudah di-copy ke `/etc/systemd/system/` (via `install.sh`)
- [ ] `systemctl daemon-reload` sudah dijalankan
- [ ] `midlab-web-console`, `midlab-result-sender`, `midlab-order-receiver` sudah di-enable
- [ ] Test jalan manual sebelum enable systemd: `python3 services/web_console/main.py`
- [ ] Web Console dapat dibuka di browser: `http://server:8000`
- [ ] Semua instrument sudah ada di `tbl_instrument` dengan IP/port yang benar

### End-to-End Verification

- [ ] Kirim test result dari alat → cek `tbl_result` berisi data → cek LIS menerima
- [ ] Kirim test order dari LIS ke `POST /api/orders` → cek `tbl_order` berisi data
- [ ] Untuk alat bidirectional: pastikan order terkirim ke alat (cek `instrument_status=sent`)
- [ ] Cek log tidak ada ERROR berulang: `tail -f /var/log/midlab/*.log`

### Security (Opsional tapi Disarankan)

- [ ] Setup nginx sebagai reverse proxy untuk Web Console (hindari expose uvicorn langsung)
- [ ] Enable HTTPS via Let's Encrypt atau self-signed certificate
- [ ] Tambah `api_key` di config dan enforce di kode jika Web Console diakses dari luar

---

## 12. Monitoring & Health Check

### Cek Status Service (Manual)

```bash
# Status semua service MidLab
systemctl status midlab-web-console midlab-result-sender midlab-order-receiver

# Status per alat (instrument ID 1, 2, 3)
systemctl status midlab-tcp@1 midlab-tcp@2 midlab-tcp@3

# Cek apakah ada service crash (exit code bukan 0)
journalctl -u midlab-result-sender --since "1 hour ago" | grep -i "error\|failed\|crash"
```

### Cek Health via Web Console

Buka `http://server:8000` — Dashboard menampilkan:
- **Status card** per service: warna hijau = running, merah = stopped
- **Alerts** hasil gagal dikirim ke LIS (send_status=failed)
- **Alerts** order gagal dikirim ke alat (instrument_status=failed)

### Query Health via Database

```sql
-- Backlog result belum terkirim ke LIS
SELECT COUNT(*) FROM tbl_result WHERE send_status='pending';

-- Result gagal terkirim (butuh perhatian)
SELECT id, instrument_id, error_message, received_at
FROM tbl_result WHERE send_status='failed' ORDER BY received_at DESC LIMIT 10;

-- Order gagal ke alat
SELECT id, instrument_id, failed_at_service, error_message, created_at
FROM tbl_order WHERE instrument_status='failed' ORDER BY created_at DESC LIMIT 10;

-- Throughput hari ini
SELECT DATE(received_at) as tgl, COUNT(*) as total,
       SUM(send_status='sent') as sent, SUM(send_status='failed') as failed
FROM tbl_result GROUP BY DATE(received_at) ORDER BY tgl DESC LIMIT 7;
```

### Cek Koneksi TCP ke Alat

```bash
# Dari Web Console: klik "Test" di halaman Instruments
# Dari command line:
nc -zv 192.168.1.100 9100    # ganti IP dan port sesuai alat
```

### Log Patterns yang Perlu Diperhatikan

| Pattern | Artinya | Tindakan |
|---|---|---|
| `Connection refused` berulang | Alat mati atau IP/port salah | Cek alat, cek config |
| `Connection timeout` | Firewall atau routing issue | Cek network |
| `parse error` di tcp_*.log | Data dari alat tidak sesuai format | Capture raw data, periksa parser |
| `HTTP 4xx/5xx` di result_sender.log | LIS menolak data | Cek format ResultObject, cek LIS |
| `SSL error` di result_sender.log | LIS pakai HTTPS, perlu `verify=False` atau cert | Konfigurasi aiohttp SSL context |
| `MySQL connection lost` | Pool habis atau MySQL restart | Pastikan `pool_recycle` < wait_timeout MySQL |

---

## 13. Backup & Recovery

### Yang Harus Di-Backup

1. **Database `midlab_db`** — berisi semua result, order, dan konfigurasi alat
2. **`/etc/midlab/config.yaml`** — konfigurasi service (LIS URL, DB credentials)
3. **`/opt/midlab/`** — codebase (jika tidak pakai version control)

### Backup Database Manual

```bash
# Dump semua tabel
mysqldump -u midlab -pmidlab_secret midlab_db > /backup/midlab_$(date +%Y%m%d).sql

# Restore
mysql -u midlab -pmidlab_secret midlab_db < /backup/midlab_20260417.sql
```

### Backup Database Otomatis (Contoh Cron)

```bash
# Edit crontab untuk user root:
# crontab -e

# Backup setiap hari jam 02:00 pagi, simpan 30 hari terakhir
0 2 * * * mysqldump -u midlab -pmidlab_secret midlab_db | gzip > /backup/midlab_$(date +\%Y\%m\%d).sql.gz && find /backup -name "midlab_*.sql.gz" -mtime +30 -delete
```

### Recovery Skenario Umum

**Service crash (auto-restart):**
- Systemd sudah set `Restart=always` — service akan restart otomatis dalam 5 detik
- Jika tidak auto-restart: `systemctl start midlab-<service>`

**Data result/order pending menumpuk:**
```bash
# Restart result sender untuk flush pending
systemctl restart midlab-result-sender

# Retry manual via Web Console: Results → klik Retry pada row yang failed
```

**MySQL tidak bisa connect:**
```bash
# Cek MySQL running
systemctl status mysql

# Cek credentials di config
cat /etc/midlab/config.yaml | grep -A5 database

# Test connect manual
mysql -u midlab -pmidlab_secret midlab_db -e "SELECT 1;"
```

**TCP service tidak bisa connect ke alat:**
1. Cek kabel fisik dan power alat
2. `nc -zv <ip> <port>` dari server MidLab
3. Cek apakah alat perlu di-reset atau restart layanan komunikasinya
4. Cek firewall: `iptables -L -n | grep <port>` atau `ufw status`

---

*Dokumen ini ditulis berdasarkan state codebase per 17 April 2026.
Update dokumen ini jika ada perubahan arsitektur atau keputusan desain yang signifikan.*

---

## LIS Bridging Migration — 2026-05-13

EazyApp LIS integration sekarang ditangani oleh `LisBridgeService` per-alat
(replace `OrderReceiverService` + `ResultSenderService` untuk alat yang
`lis_bridge_enabled=true`).

- Spec: `docs/superpowers/specs/2026-05-13-lis-bridging-eazyapp-design.md`
- Plan: `docs/superpowers/plans/2026-05-13-lis-bridging-eazyapp.md`
- Migration script: `scripts/migrate_lis_api.py`
- Rollback: `scripts/migrate_lis_api_rollback.py`
- Per-alat enabling via `tbl_instrument.lis_bridge_enabled` (set via Web Console)
- Auth Bearer token per-alat (`tbl_instrument.lis_api_key`)
- Global settings: `lis.base_url`, `lis.http_timeout`, `lis.retry_max`,
  `lis.result_poll_interval`, `lis.status_poll_interval`, `lis.log_poll_interval`
