#!/usr/bin/env bash
# ============================================================
# MidLab Install Script
# Setup user, direktori, permissions, venv, systemd units, DB
# Jalankan sebagai root: sudo bash scripts/install.sh
# Idempotent: aman dijalankan berkali-kali.
# ============================================================

set -euo pipefail

MIDLAB_DIR="/opt/midlab"
LOG_DIR="/var/log/midlab"
CONFIG_DIR="/etc/midlab"
SYSTEMD_DIR="/etc/systemd/system"
VENV_DIR="$MIDLAB_DIR/.venv"
MIDLAB_USER="midlab"
MIDLAB_GROUP="midlab"

# Default DB settings (override via env var)
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-midlab_db}"
DB_USER="${DB_USER:-midlab}"
DB_PASS="${DB_PASS:-midlab}"

# Lokasi source (default = direktori parent dari script)
SRC_DIR="${SRC_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"

# Warna output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

if [[ $EUID -ne 0 ]]; then
    error "Script ini harus dijalankan sebagai root (sudo)"
fi

info "=== MidLab Installation ==="
info "Source dir : $SRC_DIR"
info "Install dir: $MIDLAB_DIR"
info "DB         : $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"

# --------------------------------------------------
# 1. User midlab
# --------------------------------------------------
if id "$MIDLAB_USER" &>/dev/null; then
    info "User '$MIDLAB_USER' sudah ada"
else
    info "Membuat user '$MIDLAB_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$MIDLAB_USER"
fi

# --------------------------------------------------
# 2. Direktori + permissions
# --------------------------------------------------
info "Menyiapkan direktori..."
mkdir -p "$LOG_DIR" "$CONFIG_DIR" "$MIDLAB_DIR"

# Copy source ke $MIDLAB_DIR (kalau berbeda dari source)
if [[ "$SRC_DIR" != "$MIDLAB_DIR" ]]; then
    info "Sync source $SRC_DIR -> $MIDLAB_DIR..."
    rsync -a --delete \
        --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
        --exclude 'tests' --exclude 'docs' \
        "$SRC_DIR/" "$MIDLAB_DIR/"
fi

chown -R "$MIDLAB_USER":"$MIDLAB_GROUP" "$LOG_DIR" "$MIDLAB_DIR"
chmod 755 "$LOG_DIR" "$MIDLAB_DIR"

chown root:"$MIDLAB_GROUP" "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"

# Bikin config.yaml default kalau belum ada
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    info "Membuat $CONFIG_DIR/config.yaml default..."
    cat > "$CONFIG_DIR/config.yaml" <<EOF
database:
  host: $DB_HOST
  port: $DB_PORT
  user: $DB_USER
  password: $DB_PASS
  database: $DB_NAME
  pool_size: 10
  pool_recycle: 3600

logging:
  level: INFO
  max_bytes: 10485760
  backup_count: 5

server:
  host: 0.0.0.0
  debug: false

order_receiver:
  port: 8001
  api_key: ""

lis:
  api_url: ""
  api_key: ""
EOF
    chown root:"$MIDLAB_GROUP" "$CONFIG_DIR/config.yaml"
    chmod 640 "$CONFIG_DIR/config.yaml"
else
    info "$CONFIG_DIR/config.yaml sudah ada, tidak diubah"
fi

# --------------------------------------------------
# 3. System packages
# --------------------------------------------------
info "Memastikan paket sistem..."
if command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip rsync || true
    # Coba install salah satu MySQL/MariaDB client (urutan preferensi)
    if ! command -v mysql &>/dev/null && ! command -v mariadb &>/dev/null; then
        apt-get install -y -qq default-mysql-client \
            || apt-get install -y -qq mariadb-client \
            || apt-get install -y -qq mysql-client \
            || warn "Tidak ada mysql/mariadb client yang bisa di-install"
    fi
fi

# Deteksi client yang tersedia (mariadb diutamakan karena unix_socket auth default Debian/Ubuntu)
MYSQL_CLIENT=""
for candidate in mariadb mysql; do
    if command -v "$candidate" &>/dev/null; then
        MYSQL_CLIENT="$candidate"
        break
    fi
done
if [[ -n "$MYSQL_CLIENT" ]]; then
    info "MySQL client: $MYSQL_CLIENT"
else
    warn "MySQL/MariaDB client tidak ditemukan — DB bootstrap akan di-skip"
fi

# --------------------------------------------------
# 4. Python venv + deps
# --------------------------------------------------
if [[ ! -d "$VENV_DIR" ]]; then
    info "Membuat virtualenv $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi
info "Menginstall Python dependencies ke venv..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$MIDLAB_DIR/requirements.txt"
chown -R "$MIDLAB_USER":"$MIDLAB_GROUP" "$VENV_DIR"

# --------------------------------------------------
# 5. Database: bikin DB + user (MariaDB / MySQL)
# --------------------------------------------------
info "Setup database..."
SQL_BOOTSTRAP=$(cat <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$DB_PASS';
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'%';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
)

# Coba urutan koneksi:
# 1. mariadb (socket auth default Debian/Ubuntu)
# 2. mysql -uroot (socket / no password)
# 3. mysql -uroot -p$DB_ROOT_PASS (kalau env DB_ROOT_PASS di-set)
db_bootstrap_done=false
if [[ -n "$MYSQL_CLIENT" ]]; then
    # Method 1: mariadb (socket)
    if command -v mariadb &>/dev/null && mariadb <<<"$SQL_BOOTSTRAP" 2>/dev/null; then
        info "Database '$DB_NAME' + user '$DB_USER' siap (via mariadb socket)"
        db_bootstrap_done=true
    # Method 2: mysql -uroot tanpa password
    elif command -v mysql &>/dev/null && mysql -uroot <<<"$SQL_BOOTSTRAP" 2>/dev/null; then
        info "Database '$DB_NAME' + user '$DB_USER' siap (via mysql -uroot)"
        db_bootstrap_done=true
    # Method 3: mysql -uroot -p (kalau env DB_ROOT_PASS di-set)
    elif [[ -n "${DB_ROOT_PASS:-}" ]] && command -v mysql &>/dev/null \
         && mysql -uroot -p"$DB_ROOT_PASS" <<<"$SQL_BOOTSTRAP" 2>/dev/null; then
        info "Database '$DB_NAME' + user '$DB_USER' siap (via mysql -uroot -p)"
        db_bootstrap_done=true
    fi
fi

if [[ "$db_bootstrap_done" != true ]]; then
    warn "Gagal auto-setup DB. Coba salah satu:"
    warn "  1. Set DB root password lalu rerun: sudo DB_ROOT_PASS='xxx' bash $0"
    warn "  2. Jalankan SQL manual (pakai sudo mysql atau sudo mariadb):"
    echo "$SQL_BOOTSTRAP" | sed 's/^/        /'
fi

# Verifikasi koneksi dari user yang baru dibuat
if [[ -n "$MYSQL_CLIENT" ]] && \
   "$MYSQL_CLIENT" -u"$DB_USER" -p"$DB_PASS" -h"$DB_HOST" -P"$DB_PORT" "$DB_NAME" \
       -e "SELECT 1;" &>/dev/null; then
    info "Koneksi sebagai '$DB_USER' ke '$DB_NAME' verified"
elif [[ "$db_bootstrap_done" == true ]]; then
    warn "DB bootstrap sukses tapi koneksi '$DB_USER' via TCP $DB_HOST:$DB_PORT gagal"
    warn "Cek bind-address di /etc/mysql/mariadb.conf.d/50-server.cnf (harus 127.0.0.1 atau 0.0.0.0)"
fi

# --------------------------------------------------
# 6. Bootstrap ORM schema + migrasi LIS
# --------------------------------------------------
info "Bootstrap ORM schema (Base.metadata.create_all)..."
cd "$MIDLAB_DIR"
if "$VENV_DIR/bin/python" -c "from lib.db import Base, DBManager; Base.metadata.create_all(DBManager().engine); print('ORM ready')" 2>&1; then
    info "Schema ORM dibuat"
else
    warn "Bootstrap ORM gagal — cek koneksi DB di $CONFIG_DIR/config.yaml"
fi

info "Menjalankan migrasi LIS bridging..."
if "$VENV_DIR/bin/python" "$MIDLAB_DIR/scripts/migrate_lis_api.py"; then
    info "Migrasi LIS selesai"
else
    warn "Migrasi LIS gagal — periksa output di atas"
fi

# --------------------------------------------------
# 7. Systemd units
# --------------------------------------------------
info "Menginstall systemd unit files..."
UNIT_SRC="$MIDLAB_DIR/systemd"
if [[ -d "$UNIT_SRC" ]]; then
    for unit in midlab-web-console.service midlab-result-sender.service \
                midlab-order-receiver.service midlab-tcp@.service \
                midlab-lis-bridge@.service; do
        if [[ -f "$UNIT_SRC/$unit" ]]; then
            cp "$UNIT_SRC/$unit" "$SYSTEMD_DIR/"
            info "  + $unit"
        else
            warn "  - $unit tidak ditemukan, skip"
        fi
    done
else
    error "Direktori $UNIT_SRC tidak ditemukan"
fi

# Patch ExecStart agar pakai venv python (kalau belum)
for unit in "$SYSTEMD_DIR"/midlab-*.service; do
    [[ -f "$unit" ]] || continue
    if grep -q "ExecStart=/usr/bin/python3" "$unit"; then
        sed -i "s|ExecStart=/usr/bin/python3|ExecStart=$VENV_DIR/bin/python|" "$unit"
        info "  patched ExecStart → venv di $(basename "$unit")"
    fi
done

info "Reload systemd daemon..."
systemctl daemon-reload

# --------------------------------------------------
# 8. Summary
# --------------------------------------------------
echo ""
info "=== Instalasi selesai ==="
echo ""
echo "Langkah selanjutnya:"
echo "  1. Review config di $CONFIG_DIR/config.yaml"
echo "  2. Start Web Console:"
echo "       sudo systemctl start midlab-web-console"
echo "       sudo systemctl enable midlab-web-console"
echo "  3. Akses UI: http://localhost:8000"
echo "  4. Untuk tiap alat:"
echo "       sudo systemctl start midlab-tcp@<id>"
echo "       sudo systemctl start midlab-lis-bridge@<id>   # kalau pakai EazyApp"
echo ""
echo "Cek status / log:"
echo "  sudo systemctl status midlab-web-console"
echo "  sudo journalctl -u midlab-web-console -f"
echo "  sudo tail -f $LOG_DIR/lis_bridge_<id>.log"
echo ""
echo "Override DB credentials saat install (rerun):"
echo "  sudo DB_NAME=foo DB_USER=bar DB_PASS=baz bash $0"
echo ""
