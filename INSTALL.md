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

Cek cepat:
```bash
python3 --version
systemctl status mysql        # harus active
git --version
```

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
**Mindray BS200E**) di `protocols/`.

---

## 3. Jalankan installer

Installer bersifat **idempotent** (aman diulang). Ia membuat user `midlab`,
direktori (`/var/log/midlab`, `/etc/midlab`), venv + dependency, database + user MySQL,
bootstrap schema ORM + migrasi LIS, lalu memasang 5 unit systemd.

```bash
cd /opt/midlab
sudo bash scripts/install.sh
```

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

Untuk menarik perubahan terbaru dari `main` tanpa reinstall penuh:

```bash
cd /opt/midlab
sudo git pull origin main
sudo bash scripts/deploy.sh --restart-all   # sync kode + restart semua service aktif
```

`deploy.sh` hanya menyinkronkan direktori kode (`services/ protocols/ lib/ scripts/`) dan
**tidak menyentuh** `config.yaml`, log, `.venv`, atau `.git`. Opsi:
`--dry-run` (preview), `--no-restart`, `--restart-all`.

Jika ada dependency baru atau unit systemd berubah, jalankan ulang installer:
```bash
sudo bash scripts/install.sh        # idempotent: refresh venv + reinstall unit + daemon-reload
```

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
