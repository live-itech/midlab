# Runbook Deploy — LIS Bridging EazyApp (PR #1)

Runbook untuk merilis fitur **LisBridgeService (EazyApp)** ke server production.
Disusun agar bisa dieksekusi manual **atau** diorkestrasi oleh Claude di server.

> **Prinsip:** tiap langkah punya **perintah** → **verifikasi (gate)** → lanjut hanya jika gate PASS.
> Kalau gate FAIL, lihat bagian **Rollback** di akhir.

Ringkasan path production:
| Item | Lokasi |
|---|---|
| Kode | `/opt/midlab/` |
| Config (secrets) | `/etc/midlab/config.yaml` |
| Log | `/var/log/midlab/` |
| Virtualenv | `/opt/midlab/.venv` |
| systemd units | `/etc/systemd/system/midlab-*.service` |
| User/group | `midlab:midlab` |

Konvensi: ganti `<id>` dengan `tbl_instrument.id` alat EazyApp (mis. `1`).
Perintah `mysql` bisa diganti `mariadb` sesuai client yang terpasang.

---

## 0. Pre-flight (WAJIB sebelum apa pun)

- [ ] **Konfirmasi ini server production yang benar** (`hostname`, `ip a`).
- [ ] **Server sudah pernah di-install** MidLab (`/opt/midlab` dan `/etc/midlab/config.yaml` ada).
      Jika **fresh box**, lompat ke [Lampiran A — Fresh Install](#lampiran-a--fresh-install) dulu.
- [ ] **Backup DB** (rollback andalan utama):
  ```bash
  sudo mysqldump midlab_db > /root/midlab_db.pre-eazyapp.$(date +%Y%m%d-%H%M%S).sql
  ```
  **Gate:** file dump > 0 byte (`ls -lh /root/midlab_db.pre-eazyapp.*.sql`).
- [ ] **Catat state service saat ini** (untuk pembanding sesudahnya):
  ```bash
  systemctl list-units 'midlab-*' --no-legend
  ```
- [ ] **Siapkan kredensial EazyApp**: `api_url` LIS, `api_key` global, serta per-alat
      `lis_instrument_id` + `lis_api_key`. Tanpa ini, bridge tidak bisa auth.

---

## 1. Ambil kode terbaru ke server

Repo adalah source of truth **kode saja** (config & DB tidak ikut).

```bash
cd /path/ke/checkout-midlab     # lokasi git checkout di server (BUKAN /opt/midlab)
git fetch origin
git checkout feat/lis-bridging-eazyapp   # atau 'main' jika PR #1 sudah di-merge
git pull --ff-only
git log -1 --oneline
```

- [ ] **Gate:** commit teratas sesuai HEAD PR #1 (mengandung `scripts: cobas test sender + deploy helper` / `protocol as varchar`).

> Jika PR #1 sudah di-merge ke `main`, pakai `main`. Selama belum merge, pakai branch `feat/lis-bridging-eazyapp`.

---

## 2. Update dependency Python (kalau ada yang baru)

`deploy.sh` **tidak** menjalankan `pip`. Requirements PR ini menambah a.l. `aiohttp`.

```bash
sudo /opt/midlab/.venv/bin/pip install -r /opt/midlab/requirements.txt
```
> Catatan: perlu file `requirements.txt` sudah ada di `/opt/midlab`. Kalau belum,
> jalankan Step 3 (sync kode) dulu, atau salin `requirements.txt` manual.

- [ ] **Gate:** `sudo /opt/midlab/.venv/bin/python -c "import aiohttp, fastapi, sqlalchemy; print('deps ok')"` → `deps ok`.

---

## 3. Sync kode → /opt/midlab

Pakai helper deploy, **tanpa restart dulu** (`--no-restart`) supaya bisa migrasi DB dulu.

```bash
sudo ./scripts/deploy.sh --dry-run        # review perubahan
sudo ./scripts/deploy.sh --no-restart     # apply kode, belum restart
```
`deploy.sh` menyinkronkan `services/ protocols/ lib/ scripts/`, meng-exclude
`config.yaml`, `tests/`, `docs/`, `*.md`, logs.

- [ ] **Gate:** `deploy.sh` selesai tanpa error; `ls -l /opt/midlab/services/lis_bridge/` menampilkan file `result_pusher.py`, `order_puller.py`, `status_reporter.py`, `log_pusher.py`, `main.py`.

---

## 4. Migrasi DATABASE (inti PR ini)

`deploy.sh` **tidak** menyentuh DB. Jalankan **dua** migrasi berikut secara berurutan.
Keduanya idempotent (aman diulang).

### 4a. protocol → VARCHAR(50) (WAJIB — tidak dijalankan install.sh)
```bash
mysql -u midlab -p midlab_db < /opt/midlab/scripts/migrate_protocol_to_varchar.sql
```
- [ ] **Gate:**
  ```bash
  mysql -u midlab -p midlab_db -e "SHOW COLUMNS FROM tbl_instrument LIKE 'protocol';"
  ```
  → tipe kolom = `varchar(50)` (bukan `enum(...)`).

### 4b. Kolom LIS + tbl_lis_event_queue
```bash
cd /opt/midlab && sudo -u midlab /opt/midlab/.venv/bin/python scripts/migrate_lis_api.py
```
- [ ] **Gate:** output berisi `skip:`/`added:` per kolom tanpa error, lalu:
  ```bash
  mysql -u midlab -p midlab_db -e "SHOW TABLES LIKE 'tbl_lis_event_queue'; SHOW COLUMNS FROM tbl_instrument LIKE 'lis_bridge_enabled';"
  ```
  → tabel `tbl_lis_event_queue` ada; kolom `lis_bridge_enabled` ada.

---

## 5. Install / refresh systemd unit `lis-bridge@`

`deploy.sh` **tidak** menyalin `systemd/`. Lakukan manual (kecuali kamu menjalankan
`install.sh`, yang sudah menangani ini + patch venv).

```bash
sudo cp /opt/midlab/systemd/midlab-lis-bridge@.service /etc/systemd/system/
# Unit di repo hard-code /usr/bin/python3 → arahkan ke venv:
sudo sed -i 's|ExecStart=/usr/bin/python3|ExecStart=/opt/midlab/.venv/bin/python|' \
     /etc/systemd/system/midlab-lis-bridge@.service
sudo systemctl daemon-reload
```
- [ ] **Gate:** `grep ExecStart /etc/systemd/system/midlab-lis-bridge@.service`
      → menunjuk `/opt/midlab/.venv/bin/python -m services.lis_bridge.main --instrument-id %i`.

---

## 6. Konfigurasi LIS (config + per-alat)

### 6a. Global config
Pastikan `/etc/midlab/config.yaml` punya kredensial LIS (bagian `lis:` / `result_sender.lis_api_url` sesuai versi config yang dipakai). Edit sebagai root:
```bash
sudo nano /etc/midlab/config.yaml
sudo systemctl restart midlab-web-console   # reload config bila perlu (lihat Step 7)
```
- [ ] **Gate:** `api_url` EazyApp terisi; `api_key` global terisi bila skema global dipakai.

### 6b. Per-alat (via Web Console — direkomendasikan)
Di UI (`http://<server>:8000`) → Instrument → alat EazyApp:
- [ ] Set `lis_instrument_id` + `lis_api_key`.
- [ ] Klik **Verify with LIS** → harus sukses (memanggil `POST /api/instruments/<id>/verify-lis`).
- [ ] **JANGAN** aktifkan `lis_bridge_enabled` dulu bila mau cutover bertahap.

Alternatif via SQL (kalau tanpa UI):
```sql
UPDATE tbl_instrument
SET lis_instrument_id='<LIS_ID>', lis_api_key='<KEY>', order_poll_interval=10
WHERE id=<id>;
```

---

## 7. Restart service inti

```bash
sudo systemctl restart midlab-web-console
sleep 1 && systemctl is-active midlab-web-console
```
- [ ] **Gate:** `active`, dan UI `http://<server>:8000` terbuka, halaman Instrument & `/lis-events` render tanpa error.
- [ ] **Gate:** `sudo journalctl -u midlab-web-console -n 50 --no-pager` tidak ada traceback (mis. schema mismatch `protocol`).

---

## 8. Cutover per-alat (bertahap, satu alat dulu)

Aktifkan bridge untuk **satu** alat sebagai kanari.

```bash
# 8a. Aktifkan flag (UI toggle 'LIS Bridge Enabled', atau SQL):
mysql -u midlab -p midlab_db -e "UPDATE tbl_instrument SET lis_bridge_enabled=TRUE WHERE id=<id>;"

# 8b. Start service bridge alat tsb:
sudo systemctl start  midlab-lis-bridge@<id>
sudo systemctl enable midlab-lis-bridge@<id>
```
Verifikasi:
- [ ] **Gate service:** `systemctl is-active midlab-lis-bridge@<id>` → `active`.
- [ ] **Gate log:** `sudo tail -f /var/log/midlab/lis_bridge_<id>.log` → ada log
      `verify` sukses + OrderPuller polling + (bila ada result pending) ResultPusher push.
- [ ] **Gate result flow:** result baru untuk alat ini pindah `send_status` `pending → sent`:
  ```bash
  mysql -u midlab -p midlab_db -e \
    "SELECT send_status, COUNT(*) FROM tbl_result WHERE instrument_id=<id> GROUP BY send_status;"
  ```
- [ ] **Gate legacy gating:** `result_sender` men-skip alat ini (tidak ada double-send).
      Cek `grep -i 'skip' /var/log/midlab/result_sender.log`.
- [ ] **Gate event queue:** status connect/disconnect terkirim:
  ```bash
  mysql -u midlab -p midlab_db -e \
    "SELECT event_type, send_status, COUNT(*) FROM tbl_lis_event_queue WHERE instrument_id=<id> GROUP BY 1,2;"
  ```

Bila kanari sehat (pantau ≥ beberapa siklus poll), ulangi Step 8 untuk alat berikutnya.

---

## 9. Post-deploy

- [ ] Enable service inti agar auto-start: `sudo systemctl enable midlab-web-console`.
- [ ] Pantau `/var/log/midlab/lis_bridge_<id>.log` & `/lis-events` selama window observasi.
- [ ] Setelah semua alat EazyApp stabil di bridge, jadwalkan decommission legacy
      (`result_sender`/`order_receiver`) — **jangan** sekaligus; itu langkah terpisah.
- [ ] Update PR/issue: tandai deploy selesai + commit hash yang dirilis.

---

## Rollback

Skala eskalasi dari paling ringan:

1. **Satu alat bermasalah** → matikan bridge alat itu, balik ke legacy:
   ```bash
   sudo systemctl stop midlab-lis-bridge@<id>
   sudo systemctl disable midlab-lis-bridge@<id>
   mysql -u midlab -p midlab_db -e "UPDATE tbl_instrument SET lis_bridge_enabled=FALSE WHERE id=<id>;"
   sudo systemctl restart midlab-result-sender   # legacy ambil alih lagi
   ```
2. **Kode bermasalah** → checkout commit sebelumnya lalu re-sync:
   ```bash
   cd /path/ke/checkout-midlab && git checkout <commit-lama> && sudo ./scripts/deploy.sh --restart-all
   ```
3. **Skema/DB rusak** → restore dump pre-deploy:
   ```bash
   mysql midlab_db < /root/midlab_db.pre-eazyapp.<timestamp>.sql
   ```
   Catatan: migrasi bersifat additive (ADD COLUMN / CREATE TABLE IF NOT EXISTS),
   umumnya tidak destruktif; kolom `protocol` VARCHAR kompatibel mundur dengan nilai enum lama.

---

## Lampiran A — Fresh Install

Untuk box yang belum pernah ada MidLab, `install.sh` melakukan hampir semuanya
(user, dir, venv+deps, DB bootstrap, ORM schema, `migrate_lis_api.py`, copy semua
systemd unit + patch venv):

```bash
sudo bash scripts/install.sh
# atau override kredensial DB:
sudo DB_NAME=midlab_db DB_USER=midlab DB_PASS='***' bash scripts/install.sh
```
Setelah itu **tetap** kerjakan:
- [ ] Step 4a — `migrate_protocol_to_varchar.sql` (install.sh **tidak** menjalankannya).
- [ ] Step 6 — isi kredensial LIS di `/etc/midlab/config.yaml` + per-alat.
- [ ] Step 7–8 — start web console lalu cutover per alat.

---

## Lampiran B — Catatan untuk orkestrasi oleh Claude di server

- Jalankan **satu langkah per waktu**; jangan lanjut sebelum gate PASS. Tampilkan output perintah verifikasi apa adanya.
- Perintah butuh **sudo/root** dan seringkali **interaktif** (password `mysql -p`, `nano`). Untuk non-interaktif, siapkan `~/.my.cnf` atau env, dan konfirmasi ke operator sebelum menyentuh DB/production.
- **Selalu** konfirmasi ke operator sebelum: menjalankan migrasi DB, mengubah `config.yaml`, meng-enable `lis_bridge_enabled`, atau restart service. Ini aksi berdampak ke production.
- **Jangan pernah** menampilkan/nge-log isi `config.yaml` atau `lis_api_key` ke output.
- Kerjakan cutover **per alat** (kanari) — jangan mass-enable semua alat sekaligus.
- Jika gate FAIL, hentikan dan jalankan Rollback level yang sesuai; jangan lanjut ke langkah berikutnya.
