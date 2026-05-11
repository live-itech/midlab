# MidLab — Dokumentasi Struktur Aplikasi

**MidLab** adalah middleware Python multi-service untuk interfacing alat laboratorium klinik
(Sysmex, Roche Cobas, Abbott, Mindray, Vitek, dll) ke Laboratory Information System (LIS)
via TCP Socket + REST API.

**Stack:** Python 3.10+ | FastAPI | asyncio | SQLAlchemy | MySQL | Jinja2
**Total:** ~9.200 baris Python, ~2.700 baris frontend (HTML/CSS/JS)

---

## Arsitektur Umum

```
┌─────────────────┐      TCP/RS232-to-LAN       ┌──────────────────────────┐
│   Alat Lab      │ ◄──────────────────────────► │  TCPSocketService        │
│  (Sysmex, dll)  │                              │  (1 instance per alat)   │
└─────────────────┘                              └────────┬─────────────────┘
                                                          │ parse via ProtocolModule
                                                          ▼
                                                 ┌──────────────────┐
                                                 │     MySQL        │
                                                 │  tbl_instrument  │
                                                 │  tbl_result      │
                                                 │  tbl_order       │
                                                 │  tbl_service_log │
                                                 └──┬──────────┬───┘
                                                    │          │
                                          ┌─────────▼──┐  ┌───▼──────────────┐
                                          │ Result     │  │ OrderReceiver    │
                                          │ Sender     │  │ Service          │
                                          │ (poll+POST)│  │ (REST API ←LIS) │
                                          └─────┬──────┘  └──────────────────┘
                                                │
                                                ▼
                                        ┌──────────────┐
                                        │   LIS        │
                                        │ (REST API)   │
                                        └──────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Web Console (port 8000)                                        │
│  Dashboard | Instruments | Services | Logs | Results | Orders   │
│  + Watchdog (start/stop/restart semua service)                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Struktur Direktori

```
/opt/midlab/
│
├── CLAUDE.md                          # Instruksi AI assistant
├── STRUKTUR.md                        # Dokumentasi ini
├── requirements.txt                   # Python dependencies
│
├── lib/                               # Shared library
│   ├── __init__.py
│   ├── config.py                      #  94 lines — Config loader (YAML singleton)
│   ├── db.py                          # 348 lines — SQLAlchemy models, DBManager, helpers
│   ├── models.py                      # 215 lines — ResultObject & OrderObject dataclass
│   └── utils.py                       # 122 lines — Logger, RotatingFileHandler, helpers
│
├── protocols/                         # Protocol modules (dynamic load via importlib)
│   ├── __init__.py
│   ├── base.py                        # 315 lines — BaseProtocolModule, registry, loader
│   ├── astm/                          # ASTM E1381/E1394 protocol
│   │   ├── __init__.py
│   │   ├── constants.py               # 134 lines — ENQ/ACK/NAK/EOT bytes, delimiters
│   │   ├── parser.py                  # 589 lines — Raw bytes → ResultObject
│   │   ├── builder.py                 # 338 lines — OrderObject → ASTM frames
│   │   └── module.py                  # 535 lines — ASTMModule (implements BaseProtocolModule)
│   ├── hl7/                           # HL7 v2.x protocol (MLLP transport)
│   │   ├── __init__.py
│   │   ├── constants.py               # 202 lines — MSH fields, message types, MLLP chars
│   │   ├── parser.py                  # 740 lines — HL7 message → ResultObject
│   │   ├── builder.py                 # 484 lines — OrderObject → HL7 messages
│   │   └── module.py                  # 573 lines — HL7Module (implements BaseProtocolModule)
│   └── bci/                           # BCI protocol (placeholder)
│       └── __init__.py
│
├── services/                          # Independent services
│   ├── __init__.py
│   │
│   ├── tcp_socket/                    # TCPSocketService — koneksi TCP per alat
│   │   ├── __init__.py
│   │   ├── main.py                    #  88 lines — Entry point (--instrument-id N)
│   │   ├── config.py                  # 242 lines — InstrumentConfig dataclass, DB loader
│   │   ├── service.py                 # 444 lines — Orchestrator: start server/client, spawn workers
│   │   ├── receiver.py                # 517 lines — ResultReceiver: read socket → parse → save DB
│   │   ├── broadcast_worker.py        # 469 lines — BroadcastWorker: poll DB → send order ke alat
│   │   └── query_handler.py           # 483 lines — QueryHandler: handle ENQ → lookup → respond
│   │
│   ├── result_sender/                 # ResultSenderService — kirim result ke LIS
│   │   ├── __init__.py
│   │   ├── main.py                    #  51 lines — Entry point
│   │   └── service.py                 # 287 lines — Poll tbl_result pending → POST ke LIS API
│   │
│   ├── order_receiver/                # OrderReceiverService — terima order dari LIS
│   │   ├── __init__.py
│   │   ├── main.py                    #  54 lines — Entry point (uvicorn)
│   │   └── api.py                     # 260 lines — FastAPI endpoint POST /api/orders
│   │
│   └── web_console/                   # WebConsoleService — dashboard + watchdog
│       ├── __init__.py
│       ├── main.py                    #  59 lines — Entry point (uvicorn)
│       ├── api.py                     # 969 lines — REST API + page routes (Jinja2)
│       ├── watchdog.py                # 519 lines — ServiceWatchdog: start/stop/restart subprocess
│       ├── static/
│       │   ├── css/style.css          # 1098 lines — Dark/light theme, semua komponen
│       │   └── js/app.js             #  383 lines — Fetch wrapper, SSE, toast, modal, helpers
│       └── templates/
│           ├── base.html              #   88 lines — Layout: sidebar, topbar, theme toggle
│           ├── dashboard.html         #  184 lines — Service cards, summary stats, alerts
│           ├── instruments.html       #  291 lines — CRUD tabel + modal add/edit
│           ├── services.html          #  114 lines — Tabel service + auto-restart toggle
│           ├── logs.html              #  161 lines — Live SSE / histori + filter
│           ├── results.html           #  168 lines — Tabel result + pagination + retry
│           └── orders.html            #  181 lines — Tabel order + pagination + retry/cancel
│
├── systemd/                           # Systemd unit files
│   ├── midlab-web-console.service     # Web Console (port 8000)
│   ├── midlab-result-sender.service   # Result Sender
│   ├── midlab-order-receiver.service  # Order Receiver (port 8001)
│   └── midlab-tcp@.service            # Template: midlab-tcp@{instrument_id}.service
│
└── scripts/
    └── install.sh                     # Setup user, dirs, permissions, deps, systemd
```

---

## Services

### 1. TCPSocketService (`services/tcp_socket/`)

Satu instance per alat lab. Mengelola koneksi TCP dan berkomunikasi dengan alat
menggunakan protocol module yang sesuai (ASTM/HL7).

**Menjalankan:**
```bash
python3 services/tcp_socket/main.py --instrument-id 1
# atau via systemd:
systemctl start midlab-tcp@1
```

**Komponen internal berdasarkan mode:**

| Mode | Komponen Aktif |
|---|---|
| `unidirectional` | ResultReceiver |
| `bidirectional` + `broadcast` | ResultReceiver + BroadcastWorker + Lock |
| `bidirectional` + `query` | ResultReceiver + QueryHandler |
| `bidirectional` + `broadcast+query` | ResultReceiver + BroadcastWorker + QueryHandler + Lock |

- **ResultReceiver** — Baca data dari socket, panggil `ProtocolModule.parse()`, simpan ke `tbl_result`
- **BroadcastWorker** — Poll `tbl_order` setiap N detik, kirim ke alat via `format_order()`
- **QueryHandler** — Deteksi ENQ dari alat, lookup order di DB, kirim response

**Koneksi:**
- `connection=server`: MidLab listen, alat connect ke MidLab
- `connection=client`: MidLab connect ke alat (RS232-to-LAN converter)

---

### 2. ResultSenderService (`services/result_sender/`)

Singleton service. Poll `tbl_result` yang `send_status=pending`, kirim ke LIS via HTTP POST,
update status menjadi `sent` atau `failed`.

**Menjalankan:**
```bash
python3 services/result_sender/main.py
# atau:
systemctl start midlab-result-sender
```

**Flow:**
```
loop setiap {poll_interval} detik:
  SELECT * FROM tbl_result WHERE send_status='pending'
  → POST ke lis_api_url
  → update send_status = sent/failed
```

---

### 3. OrderReceiverService (`services/order_receiver/`)

FastAPI app yang menerima order dari LIS via REST API. Simpan ke `tbl_order` dengan
`instrument_status=pending`. TCPSocketService akan mengambil dan mengirimnya ke alat.

**Menjalankan:**
```bash
python3 services/order_receiver/main.py    # default port 8001
# atau:
systemctl start midlab-order-receiver
```

**Endpoint:**
- `POST /api/orders` — Terima order baru dari LIS

---

### 4. WebConsoleService (`services/web_console/`)

Dashboard web + REST API + Watchdog. Mengelola semua service lain via subprocess.

**Menjalankan:**
```bash
python3 services/web_console/main.py       # default port 8000
# atau:
systemctl start midlab-web-console
```

**Halaman UI:**

| Route | Fungsi |
|---|---|
| `/` | Dashboard: status service, summary result/order, alerts |
| `/instruments` | CRUD alat, test TCP, force broadcast |
| `/services` | Start/stop/restart service, auto-restart toggle |
| `/logs` | Log viewer: live SSE stream atau histori, filter level |
| `/results` | Monitor tbl_result: filter, pagination, retry, view JSON |
| `/orders` | Monitor tbl_order: filter, pagination, retry, cancel, view JSON |

**API Endpoints:**

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/dashboard` | Summary status, counts, alerts |
| GET | `/api/services` | List semua service + status |
| POST | `/api/services/{name}/start` | Start service |
| POST | `/api/services/{name}/stop` | Stop service |
| POST | `/api/services/{name}/restart` | Restart service |
| PUT | `/api/services/{name}/auto-restart` | Toggle auto-restart |
| GET | `/api/instruments` | List instrument |
| POST | `/api/instruments` | Tambah instrument |
| PUT | `/api/instruments/{id}` | Update instrument |
| DELETE | `/api/instruments/{id}` | Hapus instrument |
| POST | `/api/instruments/{id}/test-connection` | Test TCP ke alat |
| POST | `/api/instruments/{id}/force-broadcast` | Reset failed → pending |
| GET | `/api/protocols` | List protocol modules tersedia |
| GET | `/api/logs/{service}` | Ambil log (histori) |
| GET | `/api/logs/{service}/stream` | SSE stream log realtime |
| GET | `/api/results` | List result + filter + pagination |
| POST | `/api/results/{id}/retry` | Retry kirim result |
| GET | `/api/orders` | List order + filter + pagination |
| POST | `/api/orders/{id}/retry` | Retry kirim order |
| POST | `/api/orders/{id}/cancel` | Cancel order |

---

## Protocol Modules

Protocol module di-load secara dynamic via `importlib` berdasarkan kolom `protocol` di `tbl_instrument`.

**Contract (BaseProtocolModule):**

```python
class BaseProtocolModule:
    def parse(self, raw_bytes, instrument) -> dict          # raw → ResultObject
    def format_order(self, order, instrument) -> bytes      # build order → alat
    def is_enq(self, raw_bytes) -> bool                     # deteksi ENQ/query trigger
    def handle_enq(self, raw_bytes, instrument) -> dict     # parse query request
    def format_query_response(self, order, instrument) -> bytes  # build response
    def format_query_not_found(self, instrument) -> bytes   # NAK/empty response
    def handle_ack(self, raw_bytes) -> str                  # return: ACK|NAK|EOT|UNKNOWN
```

**Modul tersedia:**

| Protocol | Handshake | Transport | Bidirectional |
|---|---|---|---|
| ASTM | ENQ(0x05)/ACK(0x06)/NAK(0x15)/EOT(0x04) | Raw TCP | broadcast + query |
| HL7 | MSH message type | MLLP (0x0B..0x1C0x0D) | broadcast + query |
| BCI | — | — | Placeholder |

---

## Database

**Server:** MySQL (config di `/etc/midlab/config.yaml`)
**Database:** `midlab_db`

### Tabel

**tbl_instrument** — Daftar alat lab
| Kolom | Tipe | Keterangan |
|---|---|---|
| id | INT PK AUTO_INCREMENT | |
| name | VARCHAR(255) | Nama alat |
| ip_address | VARCHAR(45) | IP alat / converter |
| port | INT | TCP port |
| protocol | VARCHAR(10) | ASTM, HL7, BCI |
| mode | VARCHAR(20) | unidirectional / bidirectional |
| bidir_mode | VARCHAR(50) | NULL, broadcast, query, broadcast+query |
| broadcast_interval | INT | Default 30 detik |
| connection | VARCHAR(10) | server / client |
| is_active | BOOLEAN | Aktif/nonaktif |

**tbl_result** — Hasil pengukuran dari alat
| Kolom | Tipe | Keterangan |
|---|---|---|
| id | INT PK AUTO_INCREMENT | |
| instrument_id | INT | FK ke tbl_instrument |
| protocol | VARCHAR(10) | Protocol saat parsing |
| raw_data | TEXT | Raw bytes dari alat |
| result_json | JSON | ResultObject (parsed) |
| send_status | VARCHAR(10) | pending / sent / failed |
| retry_count | INT | Jumlah retry |
| sent_at | DATETIME | Waktu terkirim ke LIS |
| error_message | TEXT | Pesan error jika gagal |
| received_at | DATETIME | Waktu diterima dari alat |

**tbl_order** — Order dari LIS untuk alat
| Kolom | Tipe | Keterangan |
|---|---|---|
| id | INT PK AUTO_INCREMENT | |
| instrument_id | INT | FK ke tbl_instrument |
| order_json | JSON | OrderObject |
| instrument_status | VARCHAR(10) | pending / sent / failed |
| failed_at_service | VARCHAR(100) | Komponen yang gagal |
| retry_count | INT | Jumlah retry |
| sent_to_instrument_at | DATETIME | Waktu terkirim ke alat |
| error_message | TEXT | Pesan error |
| created_at | DATETIME | Waktu order masuk |

**tbl_service_log** — Log service (via DB)
| Kolom | Tipe | Keterangan |
|---|---|---|
| id | INT PK AUTO_INCREMENT | |
| service | VARCHAR(100) | Nama service |
| level | VARCHAR(10) | INFO / WARNING / ERROR |
| message | TEXT | Isi log |
| logged_at | DATETIME | Timestamp |

**Flag ownership:**
- `tbl_result.send_status` → dimiliki oleh **ResultSenderService**
- `tbl_order.instrument_status` → dimiliki oleh **TCPSocketService**

---

## Konfigurasi

**File:** `/etc/midlab/config.yaml`

```yaml
database:
  host: "127.0.0.1"
  port: 3306
  user: "midlab"
  password: "midlab_secret"
  database: "midlab_db"
  pool_size: 10
  pool_recycle: 3600

result_sender:
  poll_interval: 5              # detik antara polling
  lis_api_url: "http://..."     # endpoint LIS
  lis_api_key: ""               # API key LIS (opsional)
  retry_max: 3                  # max retry per result

order_receiver:
  port: 8001                    # port REST API

web_console:
  port: 8000                    # port web dashboard
  api_key: ""                   # API key (opsional)

logging:
  level: "INFO"
  max_bytes: 10485760           # 10 MB per file
  backup_count: 5               # rotasi file
```

---

## Systemd Services

| Unit File | Perintah | Port |
|---|---|---|
| `midlab-web-console.service` | `systemctl start midlab-web-console` | 8000 |
| `midlab-result-sender.service` | `systemctl start midlab-result-sender` | — |
| `midlab-order-receiver.service` | `systemctl start midlab-order-receiver` | 8001 |
| `midlab-tcp@.service` | `systemctl start midlab-tcp@{ID}` | — |

Semua service: `User=midlab`, `Restart=always`, `RestartSec=5`

**Contoh menjalankan untuk instrument ID 1, 2, 3:**
```bash
systemctl start midlab-tcp@1
systemctl start midlab-tcp@2
systemctl start midlab-tcp@3
```

**Enable auto-start saat boot:**
```bash
systemctl enable midlab-web-console midlab-result-sender midlab-order-receiver
systemctl enable midlab-tcp@1 midlab-tcp@2
```

---

## Instalasi

```bash
# Jalankan sebagai root
sudo bash /opt/midlab/scripts/install.sh
```

Script `install.sh` akan:
1. Membuat system user `midlab`
2. Membuat direktori `/var/log/midlab/` dan `/etc/midlab/`
3. Set permission yang sesuai
4. Install Python dependencies dari `requirements.txt`
5. Copy systemd unit files ke `/etc/systemd/system/`
6. Jalankan `systemctl daemon-reload`

---

## Log Files

**Direktori:** `/var/log/midlab/`
**Format:** `[LEVEL] [SERVICE] [INSTRUMENT] pesan`
**Rotasi:** RotatingFileHandler, max 10MB, 5 backup

| Service | File Log |
|---|---|
| TCPSocket instrument 1 | `tcp_1.log` |
| TCPSocket instrument 2 | `tcp_2.log` |
| ResultSender | `result_sender.log` |
| OrderReceiver | `order_receiver.log` |
| WebConsole | `webconsole.log` |

---

## Data Flow

### Alat → LIS (Result)
```
Alat mengirim data via TCP
  → TCPSocketService menerima raw bytes
    → ProtocolModule.parse() → ResultObject JSON
      → Simpan ke tbl_result (send_status=pending)
        → ResultSenderService poll pending
          → POST ke LIS REST API
            → Update send_status=sent
```

### LIS → Alat (Order)

**Mode Broadcast:**
```
LIS POST order ke OrderReceiverService
  → Simpan ke tbl_order (instrument_status=pending)
    → BroadcastWorker poll pending setiap N detik
      → ProtocolModule.format_order() → bytes
        → Kirim via TCP ke alat
          → Update instrument_status=sent
```

**Mode Query:**
```
Alat kirim ENQ/query ke MidLab
  → QueryHandler deteksi ENQ
    → ProtocolModule.handle_enq() → extract sample_id
      → Lookup tbl_order by sample_id
        → ProtocolModule.format_query_response() → bytes
          → Kirim response ke alat
            → Update instrument_status=sent
```

---

## Hardware Setup

```
┌──────────┐   RS232   ┌─────────────────┐   Ethernet   ┌──────────┐
│ Alat Lab ├───────────►│ RS232-to-LAN    ├─────────────►│ Switch   │
│          │           │ Converter       │              │          │
└──────────┘           └─────────────────┘              └────┬─────┘
                                                             │
┌──────────┐   Ethernet                                      │
│ Alat Lab ├─────────────────────────────────────────────────►│
│ (w/ LAN) │                                                 │
└──────────┘                                                 │
                                                             │
                                                      ┌──────▼──────┐
                                                      │ MidLab      │
                                                      │ Server      │
                                                      │ (TCP Socket)│
                                                      └─────────────┘
```
