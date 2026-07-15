#!/usr/bin/env bash
#
# scripts/deploy.sh — Sync repo dev → /opt/midlab + restart service
#
# Usage:
#   sudo ./scripts/deploy.sh                 # sync semua + restart web_console
#   sudo ./scripts/deploy.sh --restart-all   # sync + restart semua service
#   sudo ./scripts/deploy.sh --dry-run       # preview (tanpa apply)
#   sudo ./scripts/deploy.sh --no-restart    # sync tanpa restart
#
# Yang di-sync:
#   services/, protocols/, lib/, scripts/  (kode)
# Yang TIDAK di-sync:
#   config.yaml (di /etc/midlab/), logs, .venv, .git, __pycache__, tests
#

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROD_DIR="/opt/midlab"
PROD_USER="midlab"
PROD_GROUP="midlab"

DRY_RUN=""
RESTART_MODE="web"   # web | all | none

for arg in "$@"; do
  case "$arg" in
    --dry-run)      DRY_RUN="--dry-run" ;;
    --restart-all)  RESTART_MODE="all" ;;
    --no-restart)   RESTART_MODE="none" ;;
    -h|--help)
      sed -n '2,15p' "$0"; exit 0 ;;
    *)
      echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: harus dijalankan sebagai root (sudo)." >&2
  exit 1
fi

if [[ ! -d "$PROD_DIR" ]]; then
  echo "ERROR: $PROD_DIR tidak ada — jalankan scripts/install.sh dulu." >&2
  exit 1
fi

echo "==> Sync $REPO_DIR → $PROD_DIR ${DRY_RUN:+(DRY RUN)}"

RSYNC_OPTS=(
  -a
  --delete
  --chown="${PROD_USER}:${PROD_GROUP}"
  --exclude=".git/"
  --exclude=".venv/"
  --exclude="__pycache__/"
  --exclude="*.pyc"
  --exclude="*.pyo"
  --exclude=".pytest_cache/"
  --exclude="tests/"
  --exclude="docs/"
  --exclude="*.md"
  --exclude=".env"
  --exclude="*.log"
  --exclude="config.yaml"
  --exclude=".claude/"
  --exclude=".gitignore"
)

if [[ -n "$DRY_RUN" ]]; then
  RSYNC_OPTS+=("$DRY_RUN" "--itemize-changes")
fi

# Sync hanya direktori kode (jangan top-level "/" agar config.yaml dll aman)
for sub in services protocols lib scripts; do
  if [[ -d "$REPO_DIR/$sub" ]]; then
    rsync "${RSYNC_OPTS[@]}" "$REPO_DIR/$sub/" "$PROD_DIR/$sub/"
  fi
done

if [[ -n "$DRY_RUN" ]]; then
  echo "==> Dry-run selesai. Tidak ada perubahan diterapkan."
  exit 0
fi

# Pastikan ownership benar (jaga-jaga)
chown -R "${PROD_USER}:${PROD_GROUP}" "$PROD_DIR/services" "$PROD_DIR/protocols" "$PROD_DIR/lib" "$PROD_DIR/scripts" 2>/dev/null || true

case "$RESTART_MODE" in
  none)
    echo "==> Sync selesai. Restart service di-skip (--no-restart)."
    ;;
  web)
    echo "==> Restart midlab-web-console.service"
    systemctl restart midlab-web-console.service
    sleep 1
    systemctl is-active midlab-web-console.service && echo "    OK"
    ;;
  all)
    echo "==> Restart semua MidLab service yang aktif"
    UNITS=$(systemctl list-units --type=service --state=active --no-legend \
            | awk '{print $1}' | grep -E '^midlab-' || true)
    if [[ -z "$UNITS" ]]; then
      echo "    (tidak ada midlab-* service aktif)"
    else
      while read -r unit; do
        echo "    restart $unit"
        systemctl restart "$unit"
      done <<< "$UNITS"
    fi
    ;;
esac

echo "==> Deploy selesai."
