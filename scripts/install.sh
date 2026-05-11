#!/usr/bin/env bash
# ============================================================
# MidLab Install Script
# Setup user, direktori, permissions, systemd units, dan deps
# Jalankan sebagai root: sudo bash scripts/install.sh
# ============================================================

set -euo pipefail

MIDLAB_DIR="/opt/midlab"
LOG_DIR="/var/log/midlab"
CONFIG_DIR="/etc/midlab"
SYSTEMD_DIR="/etc/systemd/system"
MIDLAB_USER="midlab"
MIDLAB_GROUP="midlab"

# Warna output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Cek root
if [[ $EUID -ne 0 ]]; then
    error "Script ini harus dijalankan sebagai root (sudo)"
fi

info "=== MidLab Installation ==="

# --------------------------------------------------
# 1. Buat user midlab
# --------------------------------------------------
if id "$MIDLAB_USER" &>/dev/null; then
    info "User '$MIDLAB_USER' sudah ada"
else
    info "Membuat user '$MIDLAB_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$MIDLAB_USER"
    info "User '$MIDLAB_USER' dibuat"
fi

# --------------------------------------------------
# 2. Buat direktori yang dibutuhkan
# --------------------------------------------------
info "Membuat direktori..."

mkdir -p "$LOG_DIR"
mkdir -p "$CONFIG_DIR"

# --------------------------------------------------
# 3. Set ownership dan permissions
# --------------------------------------------------
info "Setting permissions..."

chown -R "$MIDLAB_USER":"$MIDLAB_GROUP" "$LOG_DIR"
chmod 755 "$LOG_DIR"

chown -R "$MIDLAB_USER":"$MIDLAB_GROUP" "$MIDLAB_DIR"
chmod 755 "$MIDLAB_DIR"

# Config bisa dibaca user midlab, tulis hanya root
chown root:"$MIDLAB_GROUP" "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"
if [[ -f "$CONFIG_DIR/config.yaml" ]]; then
    chown root:"$MIDLAB_GROUP" "$CONFIG_DIR/config.yaml"
    chmod 640 "$CONFIG_DIR/config.yaml"
fi

# --------------------------------------------------
# 4. Install Python dependencies
# --------------------------------------------------
info "Menginstall Python dependencies..."

if [[ -f "$MIDLAB_DIR/requirements.txt" ]]; then
    pip3 install --quiet -r "$MIDLAB_DIR/requirements.txt"
    info "Dependencies terinstall"
else
    warn "requirements.txt tidak ditemukan di $MIDLAB_DIR"
fi

# --------------------------------------------------
# 5. Copy systemd unit files
# --------------------------------------------------
info "Menginstall systemd unit files..."

UNIT_SRC="$MIDLAB_DIR/systemd"
if [[ -d "$UNIT_SRC" ]]; then
    cp "$UNIT_SRC/midlab-web-console.service" "$SYSTEMD_DIR/"
    cp "$UNIT_SRC/midlab-result-sender.service" "$SYSTEMD_DIR/"
    cp "$UNIT_SRC/midlab-order-receiver.service" "$SYSTEMD_DIR/"
    cp "$UNIT_SRC/midlab-tcp@.service" "$SYSTEMD_DIR/"
    info "Unit files di-copy ke $SYSTEMD_DIR"
else
    error "Direktori $UNIT_SRC tidak ditemukan"
fi

# --------------------------------------------------
# 6. Reload systemd
# --------------------------------------------------
info "Reload systemd daemon..."
systemctl daemon-reload

# --------------------------------------------------
# 7. Tampilkan info
# --------------------------------------------------
echo ""
info "=== Instalasi selesai ==="
echo ""
echo "Langkah selanjutnya:"
echo "  1. Pastikan config.yaml ada di $CONFIG_DIR/config.yaml"
echo "  2. Pastikan MySQL sudah running dan database sudah dibuat"
echo ""
echo "Menjalankan service:"
echo "  sudo systemctl start midlab-web-console"
echo "  sudo systemctl start midlab-result-sender"
echo "  sudo systemctl start midlab-order-receiver"
echo "  sudo systemctl start midlab-tcp@1          # instrument ID 1"
echo ""
echo "Enable auto-start saat boot:"
echo "  sudo systemctl enable midlab-web-console"
echo "  sudo systemctl enable midlab-result-sender"
echo "  sudo systemctl enable midlab-order-receiver"
echo "  sudo systemctl enable midlab-tcp@1"
echo ""
echo "Cek status:"
echo "  sudo systemctl status midlab-web-console"
echo "  sudo journalctl -u midlab-web-console -f"
echo ""
