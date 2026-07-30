# Instalasi MidLab di Server Produksi (systemd)

Panduan deploy MidLab dari git ke server Linux produksi, berjalan sebagai service
`systemd` — **persis** seperti environment development. Semua service dijalankan oleh
venv Python di `/opt/midlab/.venv` dengan user sistem `midlab`.

> Ringkas: `git clone` → `sudo bash scripts/install.sh` → `systemctl enable --now midlab-web-console`.

---

## 1. Prasyarat

| Komponen | Versi / Catatan |
|---|---|
| OS | Linux dengan systemd (Ubuntu/Debian direkomendasikan) |
| Python | 3.10+ (`python3-venv`, `python3-pip`) |
| Database | MySQL 8.0+ atau MariaDB 10.5+ (service unit bernama `mysql.service`) |
| Tools | `git`, `rsync` |
| Akses | `root` / `sudo` |
| Serial (opsional) | Hanya bila memakai Tapping Data lewat RS232 — lihat §9 |

Cek cepat:
```bash
python3 --version
systemctl status mysql        # harus active
git --version
```

### Dependency Python

Semua ada di `requirements.txt` dan dipasang otomatis oleh `install.sh` ke
`/opt/midlab/.venv`. Jangan `pip install` ke Python sistem.

| Paket | Dipakai untuk |
|---|---|
| `fastapi`, `uvicorn[standard]`, `jinja2` | Web console (UI + REST API + SSE) |
| `sqlalchemy`, `pymysql`, `cryptography` | ORM + driver MySQL (`cryptography` wajib untuk auth `caching_sha2_password`) |
| `pyyaml`, `python-dotenv` | Baca `/etc/midlab/config.yaml` |
| `aiohttp` | LisBridgeService → HTTP ke LIS EazyApp |
| `pydantic` | Validasi request/response API |
| `pyserial` | **TapService transport serial (RS232)** — import-nya lazy, jadi service lain tetap jalan meski paket ini absen |

`requirements-dev.txt` (`pytest`, `pytest-asyncio`, `aioresponses`) hanya untuk
menjalankan test — tidak perlu di server produksi.

---

## 2. Ambil kode dari git

Repo: `git@github.com:live-itech/midlab.git` (branch **`main`**).

```bash
sudo mkdir -p /opt
sudo git clone -b main git@github.com:live-itech/midlab.git /opt/midlab
# atau via HTTPS:
# sudo git clone -b main https://github.com/live-itech/midlab.git /opt/midlab
```

Sudah termasuk semua protocol driver (ASTM, HL7, BCI, Cobas C111, ARUMA AR580,
Mindray BS200E, **Mindray BC-5150**, **SD Biosensor STANDARD F2400**) di
`protocols/`.

---

## 3. Jalankan installer

Installer bersifat **idempotent** (aman diulang). Ia membuat user `midlab`,
direktori (`/var/log/midlab`, `/etc/midlab`), venv + dependency, database + user MySQL,
bootstrap schema ORM + migrasi LIS, lalu memasang 5 unit systemd.

```bash
cd /opt/midlab
sudo bash scripts/install.sh
```

Bagaimana tabel dibuat: installer memanggil `Base.metadata.create_all()`, yang
membuat **semua** tabel yang belum ada dari model ORM di `lib/db.py` — termasuk
`tbl_tap_session`. Setelah itu barulah `migrate_lis_api.py` dijalankan untuk
kolom tambahan pada tabel yang sudah ada.

> **Catatan:** `create_all()` hanya membuat tabel yang **belum ada** — ia tidak
> pernah meng-`ALTER` tabel lama. Perubahan kolom pada tabel yang sudah berisi
> data selalu lewat skrip di `scripts/migrate_*`. Semua skrip migrasi di repo
> ini **idempotent**: ia cek `INFORMATION_SCHEMA` dulu dan mencetak "sudah ada"
> bila tidak ada yang perlu diubah, jadi aman dijalankan berulang.

Override kredensial DB saat install (opsional):
```bash
sudo DB_NAME=midlab_db DB_USER=midlab DB_PASS='rahasia' bash scripts/install.sh
# jika perlu root MySQL untuk bootstrap:
sudo DB_ROOT_PASS='rootpass' DB_PASS='rahasia' bash scripts/install.sh
```

Variabel yang didukung: `DB_HOST` (default `127.0.0.1`), `DB_PORT` (`3306`),
`DB_NAME` (`midlab_db`), `DB_USER` (`midlab`), `DB_PASS` (`midlab`), `DB_ROOT_PASS`.

---

## 4. Konfigurasi

Installer membuat `/etc/midlab/config.yaml` default (mode 640, grup `midlab`).
Review dan sesuaikan kredensial DB serta endpoint LIS:

```bash
sudo nano /etc/midlab/config.yaml
```

Setelah mengubah config, restart service terkait (lihat langkah 6).

---

## 5. Struktur systemd

Lima unit terpasang di `/etc/systemd/system/` — ExecStart menunjuk ke
`/opt/midlab/.venv/bin/python`, user `midlab`, `WorkingDirectory=/opt/midlab`:

| Unit | Peran | Instansiasi |
|---|---|---|
| `midlab-web-console.service` | Dashboard UI + watchdog (port 8000) | tunggal |
| `midlab-tcp@<id>.service` | Koneksi TCP per alat | per-instrument |
| `midlab-lis-bridge@<id>.service` | Bridge ke LIS EazyApp per alat | per-instrument |
| `midlab-result-sender.service` | Kirim hasil ke LIS (legacy/fallback) | tunggal |
| `midlab-order-receiver.service` | Terima order dari LIS (legacy) | tunggal |

`<id>` = `tbl_instrument.id`.

### Proses yang BUKAN unit systemd

Tidak semua service MidLab dijalankan systemd — ini sering membingungkan saat
troubleshooting, karena `systemctl list-units 'midlab-*'` tidak menampilkannya:

| Proses | Siapa yang menjalankan | Catatan |
|---|---|---|
| **TapService** (sesi tapping) | Web console, saat sesi dibuat lewat UI `/tap` atau `POST /api/tap/sessions` | Hidup selama sesi berjalan lalu berhenti sendiri. Bisa juga manual: `python -m services.tap.service --basis HL7 ...` |
| Service alat via **Watchdog** | Web console, tombol start/stop di UI | Di-spawn `subprocess.Popen(..., start_new_session=True)` — **sengaja tidak mati saat web console restart**, agar redeploy tidak memutus koneksi alat yang sedang menerima hasil |

Konsekuensi praktis: `systemctl restart midlab-web-console` **aman** dilakukan
saat jam kerja — ia tidak menjatuhkan koneksi alat yang di-spawn watchdog.
Sebaliknya, proses yang di-spawn watchdog tidak ikut bangkit otomatis saat
server reboot; pakai unit `midlab-tcp@<id>` bila ingin alat auto-start.

Komunikasi antar service **tidak** lewat IPC langsung, melainkan flag di MySQL
(`tbl_result.send_status`, `tbl_order.instrument_status`,
`tbl_lis_event_queue.send_status`). Artinya service boleh restart sendiri-sendiri
tanpa koordinasi — tapi juga berarti **MySQL harus hidup lebih dulu**; semua unit
sudah `After=mysql.service`.

---

## 6. Enable & start service

```bash
# Web console (selalu jalan)
sudo systemctl enable --now midlab-web-console
# Akses UI: http://<server-ip>:8000

# Per alat (ganti 1 dengan id alat di tbl_instrument)
sudo systemctl enable --now midlab-tcp@1
sudo systemctl enable --now midlab-lis-bridge@1     # jika alat pakai EazyApp (lis_bridge_enabled=true)

# Legacy (hanya untuk alat non-EazyApp)
sudo systemctl enable --now midlab-result-sender
sudo systemctl enable --now midlab-order-receiver
```

Semua unit ber-`Restart=always`/`on-failure` — otomatis bangkit ulang bila crash.

---

## 7. Update / redeploy dari git

### Yang di-cover tiap perintah — baca ini dulu

Sumber kesalahan paling sering: mengira `deploy.sh` mengurus segalanya. Tidak.
Ia **hanya menyalin file kode**.

| | `git pull` | `deploy.sh` | `install.sh` |
|---|:--:|:--:|:--:|
| Ambil kode terbaru dari remote | ✅ | ❌ | ❌ |
| Salin kode → `/opt/midlab` (rsync) | — | ✅ | ❌ |
| **Install dependency baru ke `.venv`** | ❌ | ❌ | ✅ |
| **Buat tabel baru / jalankan migrasi DB** | ❌ | ❌ | ✅¹ |
| Pasang/refresh unit systemd | ❌ | ❌ | ✅ |
| Restart service | ❌ | ✅ | ❌ |
| Sentuh `config.yaml` / log / `.git` | ❌ | ❌ | hanya bila belum ada |

¹ `create_all()` untuk tabel baru + `migrate_lis_api.py` untuk perubahan kolom. Lihat §3.

**Jalur `git pull` + `deploy.sh` saja tidak menyentuh database sama sekali.**
Kalau update membawa tabel baru, jalankan skrip migrasinya (langkah 3 di bawah)
atau cukup ulangi `install.sh` — keduanya idempotent.

**Kalau update membawa dependency baru dan kamu hanya `git pull` + `deploy.sh`,
paket itu tidak akan terpasang.** Gejalanya menyesatkan: service tetap start
normal (import-nya lazy), lalu gagal jauh kemudian saat fitur yang memakainya
dipakai. Ini persis yang terjadi saat `pyserial` ditambahkan bersama fitur
Tapping Data.

### Dua layout yang mungkin — cek dulu yang mana

`deploy.sh` selalu menyalin **dari direktori tempat skrip itu berada**
(`REPO_DIR` diturunkan dari `BASH_SOURCE`), bukan dari `/opt/midlab`. Jadi
langkah `git pull`-nya bergantung pada layout server:

```bash
ls -d /opt/midlab/.git 2>/dev/null && echo "layout A" || echo "layout B"
```

| | **Layout A** — `/opt/midlab` hasil `git clone` (§2) | **Layout B** — repo terpisah, `/opt/midlab` cuma target rsync |
|---|---|---|
| Ambil kode baru | `cd /opt/midlab && sudo git pull origin main` | `cd <repo> && git pull origin main` |
| Deploy | `sudo bash /opt/midlab/scripts/deploy.sh` | `sudo bash <repo>/scripts/deploy.sh` |

⚠️ **Jangan `git pull` di `/opt/midlab` pada layout B** — di sana tidak ada
`.git`, perintahnya gagal, dan bila dirangkai dengan `&&` seluruh deploy ikut
batal tanpa mengubah apa pun. Gejalanya membingungkan: tidak ada error yang
mencolok, service tetap jalan versi lama.

Pada layout B, `/opt/midlab` juga tidak berisi file `*.md` (di-exclude rsync) —
dokumentasi hanya ada di direktori repo. Itu normal.

### Prosedur update yang aman

Contoh di bawah memakai **layout A**; untuk layout B ganti `cd /opt/midlab &&
sudo git pull` dengan `git pull` di direktori repo, dan tunjuk `deploy.sh` di
sana.

```bash
cd /opt/midlab
sudo git pull origin main

# 1. Preview dulu — rsync memakai --delete. Hot-fix yang pernah ditulis
#    langsung di /opt/midlab akan terhapus.
sudo bash scripts/deploy.sh --dry-run

# 2. Dependency baru? Bandingkan requirements.txt dengan isi venv:
sudo /opt/midlab/.venv/bin/pip install -r /opt/midlab/requirements.txt

# 3. Migrasi DB baru? Semua idempotent, aman dijalankan tiap kali update:
sudo -u midlab /opt/midlab/.venv/bin/python /opt/midlab/scripts/migrate_lis_api.py
sudo -u midlab /opt/midlab/.venv/bin/python /opt/midlab/scripts/migrate_tap_session.py

# 4. Baru sync + restart
sudo bash scripts/deploy.sh                 # restart web console saja (aman jam kerja)
# sudo bash scripts/deploy.sh --restart-all # restart semua unit midlab-* yang aktif
```

Opsi `deploy.sh`: `--dry-run` (preview), `--no-restart`, `--restart-all`.

Bila unit systemd berubah atau ragu, installer aman diulang:
```bash
sudo bash scripts/install.sh        # idempotent: refresh venv + reinstall unit + daemon-reload
```

### Verifikasi setelah update

```bash
systemctl is-active midlab-web-console
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/            # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/tap/sessions  # 200, bukan 404/500
```

`404` di `/api/tap/*` = kode belum tersalin (ulangi `deploy.sh`).
`500` = tabel `tbl_tap_session` belum dibuat (jalankan migrasinya).

---

## 8. Verifikasi & troubleshooting

```bash
# Status service
systemctl status midlab-web-console
systemctl list-units 'midlab-*'

# Log via journald
sudo journalctl -u midlab-web-console -f
sudo journalctl -u midlab-tcp@1 -f

# Log file per service
sudo tail -f /var/log/midlab/webconsole.log
sudo tail -f /var/log/midlab/tcp_1.log
sudo tail -f /var/log/midlab/lis_bridge_1.log
```

- **Service gagal start** → cek `journalctl -u <unit> -n 50`. Umumnya koneksi DB
  (`/etc/midlab/config.yaml`) atau MySQL belum jalan.
- **DB bootstrap gagal saat install** → jalankan SQL manual yang dicetak installer, atau
  rerun dengan `DB_ROOT_PASS`.
- **Koneksi DB via TCP gagal** → periksa `bind-address` di konfigurasi MySQL/MariaDB
  (`127.0.0.1` atau `0.0.0.0`).

---

## 9. Tapping Data (opsional)

Fitur perekam komunikasi mentah alat, dipakai saat membuat driver untuk alat yang
belum didukung. Tidak wajib untuk operasi normal — lewati bila tidak dipakai.

**Prasyarat:**

1. **Tabel DB** — `tbl_tap_session`. Sudah dibuat `install.sh`; bila server hanya
   di-update lewat `deploy.sh`, jalankan `scripts/migrate_tap_session.py`
2. **`pyserial`** — sudah di `requirements.txt`; pastikan benar-benar terpasang:
   ```bash
   /opt/midlab/.venv/bin/python -c "import serial; print(serial.__version__)"
   ```
3. **Direktori capture** — `/var/log/midlab/tap/`

### Di mana data tapping disimpan

Hanya **metadata** sesi yang masuk MySQL (`tbl_tap_session`: nama, transport,
target, jumlah byte, jumlah pesan). **Aliran byte-nya ada di file**
`/var/log/midlab/tap/<session_id>.jsonl` — satu event per baris, hex, di-`flush()`
tiap event agar selamat bila proses mati.

Byte sengaja tidak ditaruh di kolom MySQL: `tbl_result.raw_data` bertipe `TEXT`
(batas 64KB) dan capture bisa jauh lebih besar.

Direktori `tap/` dibuat otomatis. Yang perlu diperhatikan hanya **izin tulis**,
karena `TapRecorder` memakai fallback berjenjang:

| Kondisi | Direktori terpakai | Akibat |
|---|---|---|
| `/var/log/midlab` writable (produksi, user `midlab`) | `/var/log/midlab/tap` | ✅ permanen |
| Tidak writable (mis. dev sebagai user biasa) | `/tmp/midlab/tap` | ⚠️ **hilang saat reboot** |

Cek mana yang aktif. **Jalankan sebagai user `midlab`** — hasilnya bergantung
pada siapa yang menjalankan, jadi mengeceknya sebagai user lain bisa menyesatkan:

```bash
cd /opt/midlab && sudo -u midlab /opt/midlab/.venv/bin/python -c \
  "from services.tap.recorder import TAP_LOG_DIR; print(TAP_LOG_DIR)"
```

(`cd /opt/midlab` perlu agar `import services.tap` ketemu.)

Bila jatuh ke `/tmp` padahal ingin permanen, buat subdir yang bisa ditulis
keduanya — **jangan** `chown` seluruh `/var/log/midlab`, itu merebut log
produksi milik user `midlab`:

```bash
sudo install -d -o <user-dev> -g midlab -m 2775 /var/log/midlab/tap
```

setgid (`2775`) membuat file baru mewarisi grup `midlab`, sehingga produksi dan
dev sama-sama bisa akses.

### Serial / RS232

Akses `/dev/ttyUSB*` butuh keanggotaan grup `dialout`:

```bash
sudo usermod -aG dialout midlab      # user service
sudo usermod -aG dialout $USER       # bila menjalankan CLI manual
# wajib logout-login (atau restart service) agar grup aktif
```

Tanpa ini, sesi serial gagal dengan pesan yang menyebut `dialout` — bukan stack
trace mentah (`services/tap/transport/serial_port.py`).

### Catatan operasional

- Sesi tapping **tidak** masuk `tbl_result` dan **tidak** dikirim ke LIS — murni merekam.
- Jangan jalankan tapping pada port alat yang service TCP-nya sedang aktif; API
  menolaknya dengan `409`.
- Basis `AUTO` memakai responder pasif — ia **tidak pernah membalas**. Bila alat
  menunggu ACK untuk lanjut, sesi akan mandek di pesan pertama; pilih basis
  `ASTM`/`HL7` yang sesuai. `AUTO`/`RAW` juga tidak menghasilkan penanda pesan,
  jadi export per-pesan tidak tersedia (hanya `.bin` mentah).
