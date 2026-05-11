# MidLab — Linux Lab Instrument Middleware

Python multi-service middleware untuk interfacing alat lab klinik (Sysmex, Roche Cobas,
Abbott, Mindray, Vitek, dll) ke LIS via TCP Socket + REST API. Semua service berdiri
sendiri, komunikasi antar service via MySQL flag-based handoff (bukan direct IPC).

---

## STACK
Python 3.10+ | FastAPI | asyncio | SQLAlchemy | MySQL | RotatingFileHandler
Direktori: `/opt/midlab/` | Log: `/var/log/midlab/` | Config: `/etc/midlab/config.yaml`

---

## STRUKTUR DIREKTORI
```
/opt/midlab/
├── services/tcp_socket/      # TCPSocketService (1 instance per alat)
├── services/result_sender/   # ResultSenderService
├── services/order_receiver/  # OrderReceiverService
├── services/web_console/     # WebConsoleService + Watchdog
├── protocols/base.py         # BaseProtocolModule
├── protocols/astm/           # ASTMModule
├── protocols/hl7/            # HL7Module
└── lib/                      # db.py | config.py | utils.py
```

---

## SERVICES

| Service | Tugas | Log |
|---|---|---|
| TCPSocketService | Koneksi TCP per alat, panggil ProtocolModule, update tbl_order flag | tcp_<id>.log |
| ProtocolModule | Parse raw bytes → ResultObject JSON, simpan ke tbl_result status=pending | (dipanggil oleh TCP) |
| ResultSenderService | Poll tbl_result pending → POST ke LIS REST API, update send_status | result_sender.log |
| OrderReceiverService | Terima order dari LIS via REST API → simpan tbl_order status=pending | order_receiver.log |
| WebConsoleService | Dashboard UI, watchdog start/stop/restart service, CRUD alat, log viewer | webconsole.log |

**Hardware:** RS232 alat → RS232-to-LAN converter → switch → server (TCP)
**Hardware:** Ethernet alat → switch → server (TCP langsung)

---

## DATABASE SCHEMA

```sql
tbl_instrument: id, name, ip_address, port, protocol(ASTM|HL7|BCI),
                mode(unidirectional|bidirectional), bidir_mode(NULL|broadcast|query|broadcast+query),
                broadcast_interval INT DEFAULT 30, connection(server|client), is_active

tbl_result:     id, instrument_id, protocol, raw_data TEXT, result_json JSON,
                send_status(pending|sent|failed), retry_count, sent_at, error_message, received_at

tbl_order:      id, instrument_id, order_json JSON,
                instrument_status(pending|sent|failed), failed_at_service VARCHAR(100),
                retry_count, sent_to_instrument_at, error_message, created_at

tbl_service_log: id, service, level(INFO|WARNING|ERROR), message, logged_at
```

**Flag ownership:**
- `tbl_result.send_status` → OWNED by ResultSenderService
- `tbl_order.instrument_status` → OWNED by TCPSocketService/ProtocolModule
- `failed_at_service` → nilai contoh: `'broadcast_worker'`, `'broadcast_worker_send'`, `'query_handler_send'`

---

## PROTOCOL MODULE CONTRACT

```python
class BaseProtocolModule:
    def parse(self, raw_bytes, instrument) -> dict          # raw → ResultObject, semua mode
    def format_order(self, order, instrument) -> bytes      # build order msg → alat (broadcast)
    def is_enq(self, raw_bytes) -> bool                     # deteksi ENQ/query trigger
    def handle_enq(self, raw_bytes, instrument) -> dict     # return {type, sample_id, patient_id, raw_query}
    def format_query_response(self, order, instrument) -> bytes  # build response untuk query mode
    def format_query_not_found(self, instrument) -> bytes   # NAK/empty response jika order tidak ada
    def handle_ack(self, raw_bytes) -> str                  # return: ACK|NAK|EOT|UNKNOWN
```

**ASTM:** ENQ = 0x05, ACK = 0x06, NAK = 0x15, EOT = 0x04, handshake H/Q record
**HL7:** trigger via MSH message type (QBP^Q22/QRY), response RSP^K22, transport MLLP

---

## BIDIRECTIONAL MODES

### BROADCAST
MidLab inisiator. BroadcastWorker poll `tbl_order` tiap `broadcast_interval` detik.
```
IDLE → CHECK_DB → (pending order?) → SEND → WAIT_ACK → UPDATE_SENT/FAILED → IDLE
```
- Gunakan `asyncio.Lock` agar tidak collision dengan ResultReceiver
- `failed_at_service = 'broadcast_worker'` atau `'broadcast_worker_send'`

### QUERY
Alat inisiator via ENQ. QueryHandler aktif dalam receive loop.
```
WAIT_ENQ → ENQ_RX → PARSE_SAMPLE_ID → LOOKUP_ORDER → SEND_ACK → SEND_RESPONSE → WAIT_ACK → UPDATE → WAIT_ENQ
                                                     → NOT_FOUND → SEND_NAK → WAIT_ENQ
```
- ASTM: deteksi byte 0x05; HL7: deteksi MSH message type

### TCPSocketService internal structure
```
unidirectional:       ResultReceiver
broadcast:            ResultReceiver + BroadcastWorker + Lock
query:                ResultReceiver + QueryHandler (ENQ detect in recv loop)
broadcast+query:      ResultReceiver + BroadcastWorker + QueryHandler + shared Lock
```

---

## JSON OBJECTS

**ResultObject (tbl_result.result_json):**
```json
{ "mid_version":"1.0", "instrument_id":1, "protocol":"ASTM", "message_id":"uuid",
  "message_datetime":"ISO8601",
  "patient":{"patient_id":"","name":"","dob":"","gender":"","physician":""},
  "specimen":{"sample_id":"","sample_type":"","collected_at":""},
  "order":{"order_id":"","panel":""},
  "results":[{"test_code":"","test_name":"","value":"","unit":"","reference_range":"","flag":"","status":""}],
  "parse_errors":[] }
```

**OrderObject (tbl_order.order_json):**
```json
{ "mid_version":"1.0", "order_id":"", "instrument_id":1, "request_datetime":"ISO8601",
  "patient":{"patient_id":"","name":"","dob":"","gender":""},
  "specimen":{"sample_id":"","sample_type":"","priority":""},
  "tests":[{"test_code":"","test_name":""}] }
```

---

## WEB CONSOLE FITUR
- **Dashboard:** status card per service (PID, uptime), alert result/order gagal
- **Instrument CRUD:** add/edit/delete alat, pilih protocol, set bidir_mode, set broadcast_interval, tombol "Force Broadcast Now", indikator ENQ terakhir, test TCP connection
- **Protocol Mgmt:** list modul tersedia, hot-swap protocol (update DB + restart service)
- **Watchdog:** start/stop/restart per service, toggle auto-restart
- **Log Viewer:** pilih service → stream via SSE, filter level, search teks, mode tail/histori
- **Result Monitor:** tabel tbl_result, filter status/instrument/tanggal, retry manual
- **Order Monitor:** tabel tbl_order, detail failed_at_service, retry/cancel manual

---

## CODING RULES
1. Setiap service handle `SIGTERM` gracefully (cleanup + close socket/DB)
2. DB ops dalam `try/except`, update flag di `finally`
3. MySQL connection pooling wajib
4. Protocol module di-load dynamic via `importlib`
5. Log format: `[LEVEL] [SERVICE] [INSTRUMENT] pesan`
6. `RotatingFileHandler` untuk semua log
7. Komentar boleh Bahasa Indonesia
8. UI/UX web console menggunakan apple styles

---

## BUILD ORDER
1. `lib/` → DB schema + models + config loader
2. `protocols/base.py` → BaseProtocolModule
3. `protocols/astm/` → ASTMModule (unidirectional → bidirectional)
4. `protocols/hl7/` → HL7Module (unidirectional → bidirectional)
5. `services/tcp_socket/` → TCPSocketService (load protocol dynamic, semua mode)
6. `services/result_sender/` → ResultSenderService
7. `services/order_receiver/` → OrderReceiverService
8. `services/web_console/` → Backend API + Frontend UI
