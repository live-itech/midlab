# Tapping Data — Design

Tanggal: 2026-07-17
Status: menunggu review

## Ringkasan

Menu **Tapping Data**: alat untuk menangkap komunikasi mentah dari alat lab yang
**belum punya driver**, sebagai langkah pertama SOP pembuatan driver
(tap → analisis → tulis driver → test).

Lingkup: capture + handshake responder + export. **Tidak ada integrasi LLM.**

## Latar belakang

`PANDUAN-ALAT-BARU.md` bab 2 sudah mendokumentasikan prosedur ini secara manual:

> *"Capture data dari alat menggunakan tool serial monitor atau Wireshark; lihat
> apakah data dimulai dengan byte ENQ (0x05) → kemungkinan ASTM; lihat apakah ada
> string MSH| → kemungkinan HL7."*

Fitur ini memindahkan langkah itu dari Wireshark ke dalam MidLab, dan
mengotomasi heuristik deteksinya.

## Keputusan desain kunci

### 1. Handshake dijawab kode, bukan LLM

Mode pasif murni tidak cukup untuk ASTM/HL7: **alat tidak akan mengirim** bila
handshake-nya menggantung. Dokumen AR580 bab 2.3.1 menetapkan alat kirim ulang
dalam **3 detik** bila ACK tidak diterima, lalu menyerah.

Yang memungkinkan ini tanpa driver: **handshake bersifat agnostik terhadap isi.**

| Protokol | Yang dibutuhkan untuk membalas |
|---|---|
| ASTM | ENQ→ACK, frame→ACK, EOT. Nol parsing. |
| HL7/MLLP | Envelope `<VT>…<FS><CR>` agnostik, **tapi** MSA-2 wajib memantulkan MSH-10 — pecah MSH per `\|`, ambil field 10. Itu saja. |

Karena itu lapisan penjawab adalah **transport responder**, bukan protocol
module. `BaseProtocolModule` mencampur handshake + parse dalam satu kontrak;
tapping hanya butuh separuh pertamanya.

**LLM tidak dipakai di jalur byte** karena: (a) ACK tidak punya satu pun
keputusan — ASTM ACK selalu `0x06`, HL7 ACK selalu `MSA|AA|<echo>`; (b) latensi
API 1–3 detik (P99 lebih buruk) tidak muat di anggaran 3 detik AR580, dan ASTM
butuh ACK **per frame**; (c) lab sering tanpa internet — capture harus tetap
jalan.

### 2. Peran AI: "AI menyusun aturan, kode mengeksekusi"

Analisis capture dan penulisan driver dikerjakan lewat **Claude Code**, di luar
aplikasi — alur yang sudah terbukti: driver `HL7_ARUMA_AR580` dibuat dari PDF
vendor sampai produksi tanpa satu pun panggilan API dari MidLab.

Konsekuensi: **tidak ada API key, tidak ada dependensi `anthropic`, tidak ada
biaya per-token, tidak ada PHI ke API.** Tugas fitur ini adalah menghasilkan
capture yang enak diserahkan ke Claude Code.

### 3. Data yang di-ACK wajib persisten

Bila MidLab membalas ACK, **alat menganggap datanya sudah aman** dan menandai
sampel itu terkirim. Kalau hasil tapping cuma di layar lalu hilang saat tab
ditutup, hasil pasien itu lenyap tanpa jejak.

Karena itu: setiap byte ditulis ke disk **sebelum** ditampilkan; UI wajib memuat
peringatan bahwa data tap **tidak** masuk `tbl_result` dan **tidak** dikirim ke
LIS; dan tap ditolak bila port-nya dipakai service TCP aktif (mencegah dua pihak
meng-ACK alat yang sama).

### 4. Serial hanya di laptop

Topologi produksi tetap `alat → converter serial-to-TCP → server`. Serial dipakai
untuk develop driver di laptop sebelum dipindah ke server. Tapping di server
selalu TCP, dan hanya untuk alat yang service-nya sedang off.

`pyserial 3.5` **sudah terpasang** — tidak ada dependensi baru.

## Arsitektur

Dua sumbu yang saling bebas: **transport** (bagaimana byte mengalir) dan
**responder** (apa yang dibalas). 3 × 3, dipisah supaya tiap unit bisa diuji
sendiri.

```
services/tap/
├── service.py           # TapService — orkestrasi sesi
├── session.py           # TapSession — lifecycle + wiring transport↔responder↔recorder
├── recorder.py          # Tulis event ke JSONL (sebelum ditampilkan)
├── detect.py            # Heuristik deteksi protokol + hint baud rate
├── transport/
│   ├── base.py          # BaseTransport: open/read/write/close
│   ├── tcp.py           # TcpServerTransport (listen) + TcpClientTransport (connect)
│   └── serial_port.py   # SerialTransport (pyserial)
└── responder/
    ├── base.py          # BaseResponder: feed(bytes) → list[bytes] untuk dikirim
    ├── astm.py          # ENQ→ACK, frame→ACK, EOT
    ├── mllp.py          # MLLP + ACK^R01 dengan echo MSH-10
    └── raw.py           # pasif — tidak pernah membalas
```

Byte kontrol di-reuse dari `protocols/astm/constants.py` dan
`protocols/hl7/constants.py` — reuse tanpa refactor.

### Kontrak responder

```python
class BaseResponder(ABC):
    @abstractmethod
    def feed(self, data: bytes) -> list[bytes]:
        """Byte masuk → daftar byte yang harus dibalas (boleh kosong)."""

    @abstractmethod
    def messages(self) -> list[bytes]:
        """Pesan lengkap yang sudah terdeteksi (untuk export per-pesan)."""
```

`feed()` sengaja sinkron dan bebas I/O — state machine handshake ada di dalamnya,
tapi tidak menyentuh socket, jadi seluruh perilaku responder bisa diuji tanpa
jaringan sama sekali.

### Mode jawaban

| Mode | Perilaku |
|---|---|
| `uni` | ACK saja. Alat kirim hasil, MidLab mengakui. |
| `bidi` | ACK + **deteksi dan tandai query**, lalu diam. |

**`bidi` sengaja tidak mengarang jawaban.** Menjawab query dengan order sungguhan
butuh driver yang belum ada — itu justru yang sedang dibuat. Tiga alasan memilih
diam ketimbang mengirim "not-found generik":

1. Bentuk not-found berbeda per protokol dan per tipe query (HL7 `QBP^Q22`→`RSP^K22`
   vs `QRY^Q02`→`QCK^Q02`). Menebaknya berarti mengarang protokol.
2. Respons yang salah bisa membuat alat mencatat error dan **mengotori capture** —
   persis yang sedang kita amati.
3. Timeout adalah perilaku normal yang dipahami alat, dan retry-nya justru
   informatif: kita jadi tahu interval retry-nya.

Yang dikejar mode ini adalah **format query-nya**, bukan jawabannya. Begitu
formatnya diketahui, driver bisa ditulis dan mode query yang sesungguhnya
ditangani `QueryHandler` seperti pada Mindray BS-200E.

## Alur data

```
alat ──bytes──> Transport ──> TapSession
                                 │ 1. recorder.write(RX, bytes)   ← disk dulu
                                 │ 2. responder.feed(bytes) → balasan
                                 │ 3. recorder.write(TX, balasan)
                                 │ 4. push ke UI (SSE)
     <──bytes── Transport <──────┘
```

Urutannya mengikat: **rekam sebelum balas**. Kalau proses mati setelah ACK
terkirim tapi sebelum tertulis, data pasien hilang diam-diam.

## Penyimpanan

| Apa | Di mana | Alasan |
|---|---|---|
| Metadata sesi | `tbl_tap_session` | Web console butuh list/filter; barisnya kecil |
| Aliran byte | `/var/log/midlab/tap/<session_id>.jsonl` | Byte stream bisa besar; menaruhnya di kolom MySQL mengulang jebakan truncation `raw_data TEXT` (64KB) |

```sql
tbl_tap_session: id, name, transport(tcp_server|tcp_client|serial),
                 target VARCHAR(255),        -- "0.0.0.0:2600" | "/dev/ttyUSB0@9600-8N1"
                 protocol_basis(ASTM|HL7|RAW|AUTO),
                 detected_protocol VARCHAR(20) NULL,
                 response_mode(uni|bidi),
                 status(running|stopped|error),
                 bytes_rx INT, bytes_tx INT, message_count INT,
                 error_message TEXT, started_at, stopped_at
```

Format JSONL, satu event per baris:

```json
{"t": "2026-07-17T11:02:03.412+07:00", "dir": "rx", "hex": "0b4d5348..."}
{"t": "2026-07-17T11:02:03.413+07:00", "dir": "tx", "hex": "0b4d5348...", "note": "ACK^R01"}
{"t": "2026-07-17T11:02:03.413+07:00", "dir": "meta", "event": "message_complete", "index": 0}
```

Hex, bukan base64: bisa dibaca mata dan di-grep langsung.

## Deteksi protokol & hint baud rate

Mode `AUTO` memakai heuristik yang sudah tertulis di PANDUAN:

| Sinyal | Dugaan |
|---|---|
| Byte pertama `0x05` | ASTM |
| Mengandung `MSH\|` | HL7 |
| Diawali `0x0B` | HL7 (MLLP) |

AUTO hanya **menyarankan** — operator yang memutuskan; responder tidak berganti
sendiri di tengah sesi.

**Hint baud rate** (khusus serial): salah setel baud menghasilkan byte sampah
yang persis mirip masalah protokol — jebakan klasik yang memakan waktu berjam-jam.
Bila basis `ASTM`/`HL7` dan sudah ≥256 byte masuk tanpa satu pun frame valid, UI
menampilkan: *"byte masuk tapi tidak membentuk frame valid — cek baud rate."*

## Web Console

Halaman `/tap`, mengikuti apple style yang sudah ada.

**Daftar sesi** — nama, transport, protokol, status, jumlah byte, waktu.

**Sesi baru** — form: nama, transport (+ parameternya: host/port, atau
port/baud/parity/stopbits/flow control), basis protokol, mode jawaban.

**Tampilan live** — hex + ASCII berdampingan (hex wajib: byte kontrol
ENQ/ACK/STX tidak kelihatan di ASCII), timestamp + **delta** antar event (untuk
melihat pola retry/timeout), penanda arah RX/TX, filter arah, stream via SSE
(mekanisme yang sama dengan log viewer).

**Kirim manual** — input hex, untuk mode `RAW` yang dioperasikan sendiri.

**Peringatan permanen** di halaman: data tap **tidak** masuk `tbl_result`, **tidak**
dikirim ke LIS.

## Export — penutup lingkaran ke driver

Ini yang membuat fitur ini menyambung ke SOP, bukan sekadar penampil byte.

| Tombol | Isi | Untuk |
|---|---|---|
| **Download `.jsonl`** | Seluruh sesi, RX+TX+meta | Arsip, dokumentasi |
| **Download `.bin`** | RX saja, byte mentah | Replay, parse ulang |
| **Copy sebagai Python bytes** | Satu pesan terpilih | **Fixture test** |

Export per-pesan bergantung pada `responder.messages()`, jadi hanya tersedia di
basis `ASTM`/`HL7` yang punya batas pesan. Di basis `RAW` tidak ada konsep pesan —
yang tersedia export seluruh sesi atau rentang event yang dipilih operator.

Yang ketiga bukan gimmick. Test driver di repo ini semuanya berjangkar pada byte
string verbatim (lihat `ORU_DOC` di `tests/test_aruma_ar580.py`). Saat menulis
driver AR580, transkripsi manual dari PDF **menghasilkan kesalahan hitung pipa di
segment OBR** yang baru tertangkap oleh test. Export otomatis menghilangkan
seluruh kelas kesalahan itu.

Format keluarannya persis seperti fixture yang sudah ada:

```python
b"\x0b"
b"MSH|^~\\&|Genrui|KT-6610|||20170712140022||ORU^R01|1275|P|2.3.1|||||CHA|UTF-8|||\r"
...
b"\x1c\x0d"
```

## Pengaman

| Pengaman | Perilaku |
|---|---|
| Port bentrok | Tolak start bila port dipakai service TCP aktif (cek `tbl_instrument` + proses anak watchdog). Bukan cuma soal bind — mencegah dua pihak meng-ACK alat yang sama. |
| Rekam-sebelum-balas | Urutan operasi mengikat; ACK tidak boleh mendahului tulisan ke disk |
| Peringatan UI | Data tap tidak masuk `tbl_result`/LIS |
| Izin serial | Bila `/dev/tty*` tidak bisa dibuka, pesan menyebut grup `dialout` — bukan stack trace |

## Testing

`tests/test_tap_*.py`, ditulis test-first (TDD).

**Responder — tanpa socket sama sekali** (`feed()` murni):
- ASTM: ENQ→ACK, frame→ACK, EOT, urutan handshake lengkap
- MLLP: ACK^R01 memantulkan MSH-10; pesan tanpa MSH tidak di-ACK
- RAW: tidak pernah membalas apa pun
- Deteksi pesan lengkap (EOT / `<FS><CR>`)

**Transport:**
- TCP: loopback round-trip
- Serial: **`os.openpty()`** — pyserial bisa membuka sisi slave pty, jadi
  SerialTransport teruji tanpa hardware

**Recorder & export:**
- Round-trip JSONL
- Byte literal hasil export, saat di-`eval`, **identik dengan byte aslinya**

**Integrasi — memakai simulator yang sudah ada:**

```bash
python3 scripts/aruma_ar580_test_sender.py --port <port-tap>
```

Sesi tap harus: menangkap 34 OBX, membalas `MSA|AA|1275`, dan meng-export byte
literal yang identik dengan yang dikirim simulator. Ini menguji jalur penuh dengan
alat yang sudah terbukti, tanpa menulis fake instrument baru.

**Pengaman:**
- Tap ditolak saat port dipakai service aktif
- Recorder menulis sebelum responder membalas (urutan diverifikasi, bukan diasumsikan)

## Fase

| Fase | Isi | Bisa dipakai? |
|---|---|---|
| 1 | Transport + responder + recorder + service | Ya — via CLI |
| 2 | Halaman web console + SSE + export | Ya — alur penuh |

Fase 1 sudah berguna sendiri (`python3 -m services.tap.service --port 2600
--basis HL7`), jadi bisa diuji ke alat sungguhan sebelum UI selesai.

## Di luar lingkup (sengaja)

- **Integrasi LLM di dalam aplikasi** — analisis lewat Claude Code. Kalau nanti
  alur manualnya terbukti kurang, integrasi bisa ditambah (~$0,15 per analisis
  dengan `claude-opus-4-8`). Jangan dibangun sebelum terbukti perlu.
- **Menjawab query bidi dengan order sungguhan** — butuh driver; itu justru yang
  sedang dibuat.
- **Refactor `BaseProtocolModule`** memisahkan framing dari parsing. Pemisahan itu
  benar dan tapping membuktikannya, tapi driver yang ada sudah jalan — jangan
  diutak-atik tanpa alasan.
- **Replay capture ke driver kandidat.** Berguna (iterasi driver di meja tanpa
  alat), tapi bukan bagian dari tapping. Fitur sendiri, setelah ini terbukti.

## Risiko diketahui

| Risiko | Mitigasi |
|---|---|
| Salah setel baud menyerupai masalah protokol | Hint eksplisit setelah 256 byte tanpa frame valid |
| Hasil pasien hilang saat tapping alat live | Rekam-sebelum-balas + peringatan UI + guard port |
| JSONL membengkak pada sesi panjang | Ukuran ditampilkan di UI; rotasi diserahkan ke operator (sesi tapping berumur pendek) |
| `bidi` tidak bisa menjawab query dengan benar | Disengaja dan didokumentasikan — yang dikejar formatnya, bukan jawabannya |
