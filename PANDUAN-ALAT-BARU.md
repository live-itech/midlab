# Panduan Menambahkan Alat Baru & Protocol Module

Dokumen ini menjelaskan langkah lengkap menambahkan alat lab baru ke MidLab,
mulai dari persiapan hardware hingga software berjalan.

---

## Daftar Isi

1. [Persiapan Hardware](#1-persiapan-hardware)
2. [Identifikasi Protocol Alat](#2-identifikasi-protocol-alat)
3. [Menambah Alat via Web Console](#3-menambah-alat-via-web-console)
4. [Menambah Alat via Database](#4-menambah-alat-via-database)
5. [Menjalankan Service TCP](#5-menjalankan-service-tcp)
6. [Membuat Protocol Module Baru](#6-membuat-protocol-module-baru)
7. [Testing & Troubleshooting](#7-testing--troubleshooting)
8. [Contoh Konfigurasi Alat Umum](#8-contoh-konfigurasi-alat-umum)
9. [Verifikasi End-to-End](#9-verifikasi-end-to-end)
10. [Checklist Go-Live](#10-checklist-go-live)

---

## 1. Persiapan Hardware

### Skenario A: Alat dengan port RS232 (Serial)

Mayoritas alat lab lama menggunakan port RS232 (DB9). Karena MidLab berkomunikasi
via TCP, diperlukan **RS232-to-LAN Converter** (contoh: Moxa NPort, USR-TCP232).

```
┌──────────┐   Kabel RS232    ┌──────────────────┐   Kabel LAN    ┌──────────┐
│ Alat Lab ├──────────────────►│ RS232-to-LAN     ├───────────────►│ Switch   │
│ (DB9)    │  (null modem/     │ Converter        │  (RJ45)        │ Network  │
└──────────┘   straight)       │ (Moxa/USR/dll)   │                └────┬─────┘
                               └──────────────────┘                     │
                                                                        │
                                                                 ┌──────▼──────┐
                                                                 │ MidLab      │
                                                                 │ Server      │
                                                                 └─────────────┘
```

**Langkah setup:**

1. **Cek spesifikasi serial alat** — buka manual alat, catat:
   - Baud rate (biasanya 9600 atau 115200)
   - Data bits (biasanya 8)
   - Parity (biasanya None)
   - Stop bits (biasanya 1)
   - Flow control (None / RTS/CTS)
   - Tipe kabel: null modem atau straight-through

2. **Konfigurasi converter** — masuk ke web interface converter:
   - Set parameter serial sesuai spesifikasi alat
   - Set mode: **TCP Server** (converter listen, MidLab connect sebagai client)
     ATAU **TCP Client** (converter connect ke MidLab yang listen sebagai server)
   - Catat **IP address** dan **TCP port** converter
   - Pastikan converter dan MidLab server di network yang sama

3. **Test koneksi fisik:**
   ```bash
   # Dari server MidLab, test apakah port converter bisa diakses
   nc -zv 192.168.1.100 9100
   # atau
   telnet 192.168.1.100 9100
   ```

**Tips kabel RS232:**
- Alat DTE (Data Terminal Equipment) ke converter DCE → kabel straight
- Alat DTE ke converter DTE → kabel null modem (cross)
- Jika tidak yakin, coba keduanya — tidak akan merusak hardware

### Skenario B: Alat dengan port Ethernet (RJ45)

Alat lab modern biasanya sudah punya port Ethernet dan mendukung TCP langsung.

```
┌──────────┐   Kabel LAN    ┌──────────┐         ┌─────────────┐
│ Alat Lab ├────────────────►│ Switch   ├────────►│ MidLab      │
│ (RJ45)   │  (RJ45)        │ Network  │         │ Server      │
└──────────┘                 └──────────┘         └─────────────┘
```

**Langkah setup:**

1. **Konfigurasi network alat** — via menu alat:
   - Set IP address alat (static, contoh: 192.168.1.50)
   - Set port TCP (tergantung alat, contoh: 9100, 15000)
   - Set mode: Server atau Client
   - Aktifkan fitur LIS/Host Communication

2. **Test koneksi:**
   ```bash
   nc -zv 192.168.1.50 9100
   ```

### Catatan Penting Hardware

| Item | Keterangan |
|---|---|
| **IP Address** | Gunakan IP static, JANGAN DHCP — agar tidak berubah |
| **Firewall** | Pastikan port TCP tidak diblokir firewall server |
| **Network** | Alat dan server MidLab harus di subnet yang sama (atau ada routing) |
| **Kabel** | Gunakan kabel berkualitas, hindari kabel terlalu panjang (>100m untuk Ethernet) |
| **Converter** | Satu converter untuk satu alat — jangan share |

### Menentukan Mode `connection` di MidLab

| Siapa yang Listen | Siapa yang Connect | Setting MidLab |
|---|---|---|
| MidLab (server) | Alat/Converter connect ke MidLab | `connection=server` |
| Alat/Converter (server) | MidLab connect ke alat | `connection=client` |

**Kapan pakai `server`:**
- Converter di-set sebagai TCP Client → MidLab harus jadi server
- Alat Ethernet dikonfigurasi untuk connect ke host tertentu

**Kapan pakai `client`:**
- Converter di-set sebagai TCP Server → MidLab connect ke converter
- Alat Ethernet listen di port tertentu, menunggu koneksi masuk

---

## 2. Identifikasi Protocol Alat

Sebelum menambahkan alat, tentukan protocol komunikasinya:

| Protocol | Ciri-ciri | Contoh Alat |
|---|---|---|
| **ASTM** (E1381/E1394) | Handshake ENQ/ACK, record H/P/O/R/L/Q, delimiter `\|` | Sysmex XN, Roche Cobas, Beckman AU |
| **HL7** (v2.x) | Pesan MSH/PID/OBR/OBX, transport MLLP (0x0B...0x1C0x0D) | Abbott Architect, Siemens Atellica |
| **Proprietary** | Format khusus vendor | Perlu buat module baru |

**Cara mengidentifikasi:**
1. Baca manual alat bagian "LIS Communication" atau "Host Interface"
2. Capture data dari alat menggunakan tool serial monitor atau Wireshark
3. Lihat apakah data dimulai dengan byte ENQ (0x05) → kemungkinan ASTM
4. Lihat apakah ada string "MSH|" → kemungkinan HL7

---

## 3. Menambah Alat via Web Console

Cara paling mudah — buka Web Console (`http://server:8000`):

1. Buka menu **Instruments**
2. Klik **Tambah Instrument**
3. Isi form:

| Field | Contoh | Keterangan |
|---|---|---|
| Nama | Sysmex XN-1000 | Nama bebas untuk identifikasi |
| IP Address | 192.168.1.100 | IP alat atau converter |
| Port | 9100 | TCP port alat atau converter |
| Protocol | ASTM | Pilih dari dropdown (ASTM/HL7) |
| Connection | client | Lihat tabel di atas |
| Mode | bidirectional | unidirectional jika hanya terima hasil |
| Bidir Mode | broadcast | broadcast / query / broadcast+query |
| Broadcast Interval | 30 | Detik antara polling order (jika broadcast) |
| Aktif | ON | Toggle untuk aktif/nonaktif |

4. Klik **Simpan**
5. Klik **Test** untuk verifikasi koneksi TCP
6. Pergi ke menu **Services**, cari `tcp_<ID>`, klik **Start**

---

## 4. Menambah Alat via Database

Alternatif jika Web Console tidak tersedia:

```sql
INSERT INTO tbl_instrument
  (name, ip_address, port, protocol, mode, bidir_mode,
   broadcast_interval, connection, is_active)
VALUES
  ('Sysmex XN-1000', '192.168.1.100', 9100, 'ASTM',
   'bidirectional', 'broadcast', 30, 'client', TRUE);
```

Catat ID yang dihasilkan (`LAST_INSERT_ID()`), gunakan untuk start service.

---

## 5. Menjalankan Service TCP

### Via Web Console
Buka menu Services → klik **Start** pada `tcp_<ID>`

### Via Command Line
```bash
# Foreground (untuk testing/debug)
python3 services/tcp_socket/main.py --instrument-id 1

# Background via systemd
sudo systemctl start midlab-tcp@1
sudo systemctl enable midlab-tcp@1    # auto-start saat boot

# Cek status
sudo systemctl status midlab-tcp@1

# Lihat log
tail -f /var/log/midlab/tcp_1.log
# atau
sudo journalctl -u midlab-tcp@1 -f
```

### Menjalankan Multiple Alat

Setiap alat = 1 instance service terpisah:

```bash
sudo systemctl start midlab-tcp@1    # Sysmex XN-1000
sudo systemctl start midlab-tcp@2    # Roche Cobas 6000
sudo systemctl start midlab-tcp@3    # Abbott Architect
```

---

## 6. Membuat Protocol Module Baru

Jika alat menggunakan protocol yang belum ada (bukan ASTM/HL7), ikuti langkah:

### Step 1: Buat Direktori Module

```bash
mkdir -p /opt/midlab/protocols/myprotocol/
touch /opt/midlab/protocols/myprotocol/__init__.py
```

### Step 2: Buat Module Class

Buat file `/opt/midlab/protocols/myprotocol/module.py`:

```python
"""
protocols/myprotocol/module.py — MyProtocol Module untuk MidLab
"""

from lib.utils import get_logger, generate_message_id, format_datetime
from lib.models import ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult
from protocols.base import BaseProtocolModule


class MyProtocolModule(BaseProtocolModule):

    def __init__(self):
        self._logger = get_logger("myprotocol_module")

    @property
    def PROTOCOL_NAME(self) -> str:
        return "MYPROTOCOL"

    @property
    def VERSION(self) -> str:
        return "1.0.0"

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Parse raw bytes dari alat → ResultObject dict.

        Ini adalah method UTAMA yang wajib diimplementasi.
        Dipanggil setiap kali ResultReceiver menerima data dari socket.

        Returns:
            Dict dengan format ResultObject (lihat lib/models.py)
        """
        self._logger.info(f"Parsing {len(raw_bytes)} bytes")

        # TODO: Parse raw_bytes sesuai format protocol alat
        # Contoh: extract sample_id, patient info, test results
        decoded = raw_bytes.decode('ascii', errors='replace')

        result = ResultObject(
            instrument_id=instrument.get('id', 0),
            protocol=self.PROTOCOL_NAME,
            patient=PatientInfo(
                patient_id="",     # extract dari data
                name="",
            ),
            specimen=SpecimenInfo(
                sample_id="",      # extract dari data
            ),
            order=OrderInfo(),
            results=[
                TestResult(
                    test_code="",   # extract dari data
                    test_name="",
                    value="",
                    unit="",
                    reference_range="",
                    flag="",
                    status="F",
                ),
            ],
        )

        return result.to_dict()

    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        Format OrderObject → bytes untuk dikirim ke alat (broadcast mode).
        Jika alat tidak mendukung bidirectional, bisa return b'' atau raise.
        """
        # TODO: Build pesan order sesuai format protocol alat
        raise NotImplementedError("Broadcast belum didukung")

    def is_enq(self, raw_bytes: bytes) -> bool:
        """
        Deteksi apakah raw_bytes adalah query trigger dari alat.
        Return False jika protocol tidak mendukung query mode.
        """
        return False

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Parse query request dari alat, extract sample_id.
        """
        return {
            "type": "enq",
            "sample_id": "",
            "patient_id": "",
            "raw_query": raw_bytes.decode('ascii', errors='replace'),
        }

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        """Build response untuk query mode jika order ditemukan."""
        raise NotImplementedError("Query belum didukung")

    def format_query_not_found(self, instrument: dict) -> bytes:
        """Response jika order tidak ditemukan untuk query."""
        return b''

    def handle_ack(self, raw_bytes: bytes) -> str:
        """
        Identifikasi jenis acknowledgement dari alat.
        Return salah satu: 'ACK', 'NAK', 'EOT', 'UNKNOWN'
        """
        return "UNKNOWN"
```

### Step 3: Daftarkan di Registry

Edit `/opt/midlab/protocols/base.py`, tambahkan entry di `_PROTOCOL_REGISTRY`:

```python
_PROTOCOL_REGISTRY = {
    "ASTM": "protocols.astm.module",
    "HL7":  "protocols.hl7.module",
    "BCI":  "protocols.bci.module",
    "MYPROTOCOL": "protocols.myprotocol.module",   # ← tambahkan
}
```

### Step 4: Test Module

```bash
python3 -c "
from protocols.base import load_module
mod = load_module('MYPROTOCOL')
print(f'Loaded: {mod.PROTOCOL_NAME} v{mod.VERSION}')
print(f'parse test: {mod.parse(b\"test data\", {\"id\": 1})}')
"
```

### Step 5: Gunakan

Sekarang bisa membuat instrument dengan `protocol=MYPROTOCOL` di database atau Web Console.

### Tips Development Protocol Module

1. **Capture data dulu** — jalankan TCP listener sederhana untuk capture raw data dari alat:
   ```bash
   nc -l -p 9100 | xxd > capture.hex
   ```

2. **Pahami frame structure** — setiap protocol punya cara framing:
   - ASTM: STX...ETX/ETB + checksum
   - HL7: 0x0B...0x1C 0x0D (MLLP)
   - Proprietary: baca manual

3. **Pisahkan parser dan builder** — lihat contoh ASTM (`parser.py` + `builder.py`)

4. **Test dengan data riil** — simpan beberapa contoh raw data, buat unit test

5. **Handle error gracefully** — isi field `parse_errors` di ResultObject jika ada field yang gagal di-parse, jangan raise exception

---

## 7. Testing & Troubleshooting

### Test Koneksi TCP
```bash
# Dari Web Console: klik tombol "Test" di halaman Instruments
# Dari command line:
nc -zv 192.168.1.100 9100
```

### Simulasi Alat (untuk testing tanpa alat fisik)

**Simulasi alat ASTM sederhana:**
```bash
# Terminal 1: Jalankan MidLab TCP service
python3 services/tcp_socket/main.py --instrument-id 1

# Terminal 2: Simulasi alat mengirim data ASTM
python3 -c "
import socket
s = socket.socket()
s.connect(('127.0.0.1', 9100))
# Kirim ENQ
s.send(b'\x05')
# Tunggu ACK
ack = s.recv(1)
print(f'Got: {ack}')
# Kirim data ASTM frame (contoh)
s.send(b'\x021H|\^&|||Host^MidLab|||||||P|E 1394-97|20240101120000\r\x033B\r\n')
s.send(b'\x04')  # EOT
s.close()
"
```

### Cek Log
```bash
# Log per instrument
tail -f /var/log/midlab/tcp_1.log

# Log result sender
tail -f /var/log/midlab/result_sender.log

# Via Web Console: menu Logs → pilih service → mode Live
```

### Cek Database
```bash
mysql -u midlab -pmidlab_secret midlab_db

-- Cek result terbaru
SELECT id, instrument_id, send_status, received_at FROM tbl_result ORDER BY id DESC LIMIT 10;

-- Cek order pending
SELECT id, instrument_id, instrument_status FROM tbl_order WHERE instrument_status='pending';
```

### Common Issues

| Masalah | Kemungkinan Penyebab | Solusi |
|---|---|---|
| Connection refused | Port tidak open, alat mati, IP salah | Cek `nc -zv IP PORT`, cek kabel |
| Connection timeout | Firewall blokir, subnet berbeda | Cek firewall, cek routing |
| Data masuk tapi parse error | Format data tidak sesuai module | Capture raw data, periksa parser |
| Result tidak terkirim ke LIS | LIS URL salah, LIS down | Cek config `lis_api_url`, test manual |
| Order tidak sampai ke alat | Service TCP belum start, mode salah | Cek service status, cek bidir_mode |
| Duplicate result | Alat kirim ulang data | Normal — ResultSender handle idempotency |

### Capture Raw Data untuk Debug Parser

Jika parser menghasilkan error atau field kosong, capture dulu raw data asli dari alat:

```bash
# Jika MidLab mode server (listen), matikan service dulu lalu pakai nc
sudo systemctl stop midlab-tcp@1

# Listen di port yang sama, simpan ke file hex
nc -l -p 9100 | tee /tmp/capture.bin | xxd > /tmp/capture.hex

# Lihat hasilnya
cat /tmp/capture.hex

# Setelah dapat data, nyalakan service kembali
sudo systemctl start midlab-tcp@1
```

Kemudian gunakan data tersebut untuk membuat unit test parser:

```python
# Test parser manual
from protocols.astm.module import ASTMModule
mod = ASTMModule()
with open('/tmp/capture.bin', 'rb') as f:
    raw = f.read()
result = mod.parse(raw, {"id": 1, "name": "Test Alat"})
import json
print(json.dumps(result, indent=2))
```

---

## 8. Contoh Konfigurasi Alat Umum

Konfigurasi yang sudah diketahui berhasil untuk alat-alat umum di lab klinik.
Selalu verifikasi dengan manual alat — tiap firmware versi bisa berbeda.

### Sysmex XN Series (XN-1000, XN-2000, XN-3000)

| Parameter | Nilai |
|---|---|
| Protocol | ASTM |
| Mode | bidirectional |
| Bidir Mode | broadcast+query |
| Connection | client (MidLab connect ke Sysmex) |
| Port default | 9100 (cek menu Setup → Communication → LIS) |
| Baud rate (jika RS232) | 9600 atau 19200 |
| Broadcast Interval | 30 detik |

**Catatan:** Sysmex XN mendukung query mode — alat akan kirim ENQ untuk setiap sampel yang masuk. Gunakan mode `broadcast+query` agar order yang di-push dari LIS juga ter-deliver.

**Konfigurasi di alat:**
1. Menu → Setup → Communication → LIS Settings
2. Interface: LAN
3. Transfer Mode: Bidirectional
4. Server IP: IP server MidLab
5. Port: 9100 (atau sesuaikan)

---

### Roche Cobas Series (c111, c311, c501, 6000)

| Parameter | Nilai |
|---|---|
| Protocol | ASTM |
| Mode | bidirectional (atau unidirectional jika hanya hasil) |
| Bidir Mode | query |
| Connection | client |
| Port default | 12000 atau 14000 (cek manual per seri) |

**Catatan:** Cobas umumnya pakai query mode — alat kirim ENQ dengan QIdentifier. Pastikan `handle_enq` di ASTMModule bisa parse format Q-record Roche yang kadang menyertakan range (`*` untuk semua order).

**Konfigurasi di alat (Cobas c501 contoh):**
1. Settings → System → LIS Connection
2. Connection Type: TCP/IP
3. Role: Client
4. Host IP: IP MidLab server
5. Port: 12000

---

### Abbott Architect Series (c4000, c8000, i1000SR)

| Parameter | Nilai |
|---|---|
| Protocol | HL7 |
| Mode | bidirectional |
| Bidir Mode | query |
| Connection | server (MidLab listen, Abbott connect) |
| Port | bebas, set di MidLab misalnya 2575 (standard HL7 MLLP) |
| Transport | MLLP |

**Catatan:** Abbott Architect mengirim QBP^Q22 (query by parameter) untuk setiap sampel. MidLab harus respond dengan RSP^K22. Pastikan HL7Module menangani message type ini.

**Konfigurasi di alat:**
1. System → Host Communication → Connection Setup
2. Connection Type: Network (TCP/IP)
3. Connection Mode: Client (Abbott connect ke MidLab)
4. Host Address: IP MidLab server
5. Port: 2575

---

### Mindray BS Series (BS-200E, BS-220E, BS-120/130/200/220/330/350)

| Parameter | Nilai |
|---|---|
| Protocol | HL7_MINDRAY_BS200E |
| Mode | bidirectional (unidirectional jika hanya terima hasil) |
| Bidir Mode | **query** |
| Connection | server (MidLab listen, alat connect) |
| Port | bebas, misal 2575 |
| Transport | MLLP (HL7 v2.3.1) |

**Catatan:** driver ini mengikuti Mindray Host Interface Manual v6.0. Alat selalu
menjadi inisiator download order — alat kirim `QRY^Q02` berisi barcode, MidLab
balas `QCK^Q02` + `DSR^Q03`, alat balas `ACK^Q03`. Karena itu **pakai
`bidir_mode=query`**, bukan broadcast: manual tidak mendefinisikan order yang
didorong tanpa query, jadi `broadcast` bersifat best-effort dan order akan
ditandai `failed` bila firmware mengabaikannya.

Hasil dikirim alat sebagai `ORU^R01` — **satu pesan per tes**, jadi sampel dengan
5 tes menghasilkan 5 baris `tbl_result` dengan barcode sama. Penggabungan
dilakukan di sisi LIS via barcode (`specimen.sample_id`). QC (`MSH-16=2`) ikut
masuk dengan `status=qc`.

Dua cara download order didukung:
- **Per barcode** — alat kirim QRY berisi barcode, dibalas satu `DSR^Q03`.
- **Group download** — alat kirim QRY tanpa barcode ("semua sampel hari ini"),
  MidLab mengirim **semua order pending** alat itu, satu `DSR^Q03` per order,
  maksimal 100 order per batch (sisanya menyusul di group query berikutnya).
  Alat boleh membatalkan di tengah (QRD-9 = `CAN`); order yang belum sempat
  di-ACK tetap `pending` dan otomatis terkirim lagi di group query berikutnya.

Rentang waktu di QRF (mode "semua sampel" vs "sampel terbaru") tidak dipakai
sebagai filter — MidLab memakai flag `pending` sebagai penanda "belum dikirim
ke alat", dan order otomatis jadi `sent` setelah terkirim, sehingga group query
berikutnya hanya membawa order baru.

**Konfigurasi di alat:**
1. Setup → Communication (atau LIS/Host) → aktifkan Host Communication
2. Protocol/Transfer: HL7, Transfer Mode: Bidirectional (untuk query mode)
3. Host IP: IP server MidLab, Port: sesuai `tbl_instrument.port`
4. Pastikan **test number alat = test_code di LIS**. Bila berbeda, samakan lewat
   file `ItemID.ini` di folder software alat — tanpa itu order tidak dikenali
   dan hasil tidak bisa dipetakan.

---

### ARUMA AR580 (hematology 5-part)

| Parameter | Nilai |
|---|---|
| Protocol | HL7_ARUMA_AR580 |
| Mode | **unidirectional** |
| Bidir Mode | — (kosongkan) |
| Connection | server (MidLab listen, alat connect) |
| Port | bebas, misal 2576 |
| Transport | MLLP (HL7 v2.3.1) |

**Catatan penting:** dokumen acuan (*"LIS communication protocol instruction"*,
Genrui) sebenarnya tertulis untuk **Genrui KT-6610** — MSH-3 `KT-6610`, MSH-4
`Genrui`, OBR-24 `HM`. AR580 diperlakukan sebagai rebrand OEM-nya. Karena itu
parser **tidak memvalidasi MSH-3**: sending application apa pun diterima dan
hanya dicatat ke log, supaya integrasi tidak gagal bila firmware ternyata
mengirim string lain.

**Wajib `mode=unidirectional`.** Alat kirim `ORU^R01`, MidLab balas `ACK^R01`
berisi `MSA|AA|<MSH-10>`. Bab 2.2 dokumen menyebut `ORM^O01`/`ORR^O02` untuk
download order, tetapi **tidak menspesifikasikannya sama sekali** (tidak ada
definisi field, contoh, maupun grammar segment), dan bab 2.3.2 (QC upload) masih
kosong. Query/broadcast karena itu tidak diimplementasi — menyetel `bidir_mode`
akan menyebabkan `NotImplementedError` yang disengaja, bukan diam-diam mengirim
pesan tebakan ke alat.

Alat **mengirim ulang hasil dalam 3 detik** bila ACK tidak diterima (bab 2.3.1).
Perilaku ini bisa dimatikan di alat lewat setelan *no wait response*.

Satu `ORU^R01` memuat seluruh panel CBC+DIFF (25 parameter: WBC, Neu#/Lym#/Mon#/
Eos#/Bas#, Neu%…Bas%, RBC, HGB, HCT, MCV, MCH, MCHC, RDW-CV/SD, PLT, MPV, PDW,
PCT, P-LCC, P-LCR) — berbeda dari Mindray BS yang satu pesan per tes.

Hal yang ditangani khusus driver ini:
- **Histogram/scattergram** (OBX bertipe `ED`: `DIFFScatter_BMP`,
  `WBCScatter_BMP`, `RBCHistogram_BMP`, `PLTHistogram_BMP`) **dilewati** —
  bitmap bisa puluhan KB dan akan memotong `raw_data TEXT` (batas 64KB) serta
  membengkakkan payload ke EazyApp. Yang dicatat hanya jumlahnya di log.
- **Metadata** (Blood Mode, Test Mode, Ref Group, Age, Remarks, Blood Type)
  tidak masuk `results[]`: Blood Mode → `specimen.sample_type`, Test Mode →
  `order.panel`, sisanya → `comments`. ESR tetap diperlakukan sebagai hasil.
- **OBX-13** (status edit: `O`=reagen kedaluwarsa, `E`=edit aktif, `e`=edit
  pasif) digabung ke `status`, mis. `F/E` — supaya lab tahu nilai pernah diedit.

**Konfigurasi di alat:**
1. Setup → Communication (atau LIS/Host) → aktifkan Host Communication
2. Protocol: HL7, Transfer Mode: **Unidirectional**
3. Host IP: IP server MidLab, Port: sesuai `tbl_instrument.port`
4. Character encoding: **UTF-8**

**Uji tanpa alat:**
```bash
python3 scripts/aruma_ar580_test_sender.py --host <ip-midlab> --port 2576
```

---

### Mindray BC Series (BC-6800, BC-6800Plus)

| Parameter | Nilai |
|---|---|
| Protocol | ASTM |
| Mode | bidirectional |
| Bidir Mode | broadcast |
| Connection | client |
| Port default | 9100 atau 9200 |

---

### Alat dengan RS232-to-LAN Converter (Moxa NPort)

Konfigurasi **Moxa NPort 5110/5150** via web interface (`http://192.168.x.x`):

| Setting Moxa | Nilai yang Disarankan |
|---|---|
| Operation Mode | **TCP Server** (MidLab connect sebagai client) |
| Local TCP Port | 4001 (atau bebas) |
| Serial Interface | RS-232 |
| Baud Rate | Sesuai alat (misal 9600) |
| Data Bits | 8 |
| Stop Bits | 1 |
| Parity | None |
| Flow Control | None |
| Max Connection | 1 |

Jika Moxa di-set sebagai **TCP Client** (Moxa yang connect ke MidLab):
- Set Destination IP: IP MidLab server
- Set Destination Port: port yang MidLab listen
- Di MidLab: `connection=server`

---

## 9. Verifikasi End-to-End

Setelah menambah alat baru, lakukan verifikasi penuh sebelum go-live production.

### Step 1: Verifikasi Koneksi TCP

```bash
# Cek koneksi dari server ke alat/converter
nc -zv <IP_ALAT> <PORT>

# Atau via Web Console: Instruments → klik "Test" pada alat
```

Hasil yang diharapkan: `Connection to <IP> <PORT> port [tcp/*] succeeded!`

### Step 2: Verifikasi Penerimaan Data (Alat → MidLab)

1. **Start service** untuk alat baru: `systemctl start midlab-tcp@<ID>`
2. **Jalankan sampel test** pada alat (atau gunakan fungsi "QC Run" jika ada)
3. **Monitor log** secara realtime:
   ```bash
   tail -f /var/log/midlab/tcp_<ID>.log
   ```
4. **Cek database** — result harus masuk:
   ```sql
   SELECT id, instrument_id, send_status, received_at
   FROM tbl_result
   WHERE instrument_id = <ID>
   ORDER BY received_at DESC
   LIMIT 5;
   ```
5. **Cek result_json** — pastikan field terisi dengan benar:
   ```sql
   SELECT result_json FROM tbl_result WHERE instrument_id = <ID> ORDER BY id DESC LIMIT 1;
   ```

### Step 3: Verifikasi Pengiriman ke LIS

1. **Tunggu ResultSender** mem-poll result baru (sesuai `poll_interval` di config, default 5 detik)
2. **Cek status** berubah dari `pending` → `sent`:
   ```sql
   SELECT send_status, sent_at, error_message
   FROM tbl_result
   WHERE instrument_id = <ID>
   ORDER BY id DESC LIMIT 5;
   ```
3. **Cek di LIS** bahwa result sudah masuk dan data benar
4. Jika `send_status=failed`, cek `error_message` untuk diagnosis

### Step 4: Verifikasi Pengiriman Order (Untuk Alat Bidirectional)

**Mode Broadcast:**
1. Kirim test order dari LIS ke `POST http://server:8001/api/orders`
2. Pastikan order masuk ke `tbl_order` dengan `instrument_status=pending`
3. Tunggu BroadcastWorker mem-poll (sesuai `broadcast_interval`)
4. Cek `instrument_status=sent` di database
5. Konfirmasi alat menerima order (biasanya muncul di antrian worklist alat)

**Mode Query:**
1. Masukkan sampel ke alat
2. Alat akan kirim ENQ/query otomatis
3. Cek log — harus ada `ENQ received` atau `Query received`
4. Alat harus menerima response dan menampilkan worklist untuk sampel tersebut

### Step 5: Verifikasi Full Round-Trip

Lakukan tes dengan sampel yang memiliki order di LIS:
1. LIS kirim order → `tbl_order` pending
2. Order terkirim ke alat (broadcast atau query) → `tbl_order` sent
3. Alat proses sampel, kirim hasil → `tbl_result` pending
4. ResultSender kirim ke LIS → `tbl_result` sent
5. LIS menerima hasil → result terhubung ke order yang benar

Jika semua step berhasil, alat siap production.

---

## 10. Checklist Go-Live

Gunakan checklist ini sebelum mengaktifkan alat baru di production.

### Hardware & Network

- [ ] IP address alat sudah static (bukan DHCP)
- [ ] Koneksi TCP berhasil: `nc -zv <IP> <PORT>` sukses
- [ ] Jika pakai RS232-to-LAN converter: serial parameter sudah sesuai manual alat
- [ ] Kabel sudah dirapikan dan dilabel
- [ ] Tidak ada timeout atau packet loss ke IP alat: `ping -c 10 <IP>`

### Konfigurasi MidLab

- [ ] Record di `tbl_instrument` sudah benar (IP, port, protocol, mode, bidir_mode)
- [ ] `broadcast_interval` sudah disesuaikan dengan kebutuhan alat
- [ ] Protocol module yang digunakan sudah ditest dengan data dari alat ini
- [ ] Service TCP sudah start dan tidak ada error di log

### Testing Data

- [ ] Minimal 1 hasil real dari alat sudah masuk ke `tbl_result`
- [ ] `result_json` terisi dengan benar: sample_id, test_code, value, unit
- [ ] `parse_errors` di result_json kosong atau acceptable
- [ ] Result sudah terkirim ke LIS (`send_status=sent`)
- [ ] LIS mengkonfirmasi data diterima dan benar

### Untuk Alat Bidirectional

- [ ] Test order sudah berhasil terkirim ke alat
- [ ] Alat menampilkan worklist yang benar setelah menerima order
- [ ] Test full round-trip (order → alat → hasil → LIS) sudah dilakukan
- [ ] Tidak ada order stuck di `instrument_status=failed`

### Monitoring

- [ ] Log tidak ada ERROR berulang: `tail -100 /var/log/midlab/tcp_<ID>.log | grep ERROR`
- [ ] Dashboard Web Console menampilkan service `tcp_<ID>` berwarna hijau (running)
- [ ] Systemd service di-enable untuk auto-start: `systemctl enable midlab-tcp@<ID>`
- [ ] Catat nama alat, IP, port, dan mode ke dokumentasi internal/inventory

### Sign-off

- [ ] Teknisi lab konfirmasi output alat sudah benar
- [ ] Operator LIS konfirmasi result sudah muncul di LIS dengan benar
- [ ] Supervisor/PIC menyetujui alat aktif

---

## Setup LIS Bridging (EazyApp)

Khusus alat yang bridging-nya pakai EazyApp LIS Instrument API.

1. **Buat instrument di EazyApp** → menu **Integrasi Alat → Tambah Alat**.
   Catat `instrument_id` dan **copy API Key** unik per-alat (format `inst_xxx...`).
2. **Set global LIS Base URL** di Web Console → **Settings → LIS Bridging**:
   - `LIS Base URL`: `https://eazy.vespahobby.xyz` (atau URL deployment EazyApp)
   - HTTP timeout / retry / poll intervals sesuai kebutuhan
3. **Set per-alat API key** di Web Console → **Instruments → Edit alat**:
   - Paste API key di field `LIS API Key (Bearer)`
   - Klik **Verify with LIS** → harus return success dan auto-fill `LIS Instrument ID`
   - Set `Order Poll Interval` (default 10 detik)
   - Centang **Enable LIS Bridging** untuk activate cutover dari ResultSender lama
4. **Simpan**, lalu start service:
   - Via Watchdog API/Web Console: start `lis_bridge_<id>` (template systemd:
     `midlab-lis-bridge@<id>.service`)
5. **Verifikasi**:
   - Cek **Dashboard → LIS Bridges**: status alat = `running`, backlog = 0
   - Cek log `/var/log/midlab/lis_bridge_<id>.log` untuk verifikasi handshake
   - Test push: trigger result dari alat → cek di EazyApp UI bahwa data masuk
   - Test pull: buat order pending di EazyApp → cek `tbl_order` MidLab terisi
