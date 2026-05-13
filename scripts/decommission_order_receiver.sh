#!/usr/bin/env bash
set -euo pipefail
echo "Disabling and stopping midlab-order-receiver..."
sudo systemctl stop midlab-order-receiver || true
sudo systemctl disable midlab-order-receiver || true
echo "Archiving services/order_receiver/ -> services/_archive/order_receiver/"
mkdir -p services/_archive
git mv services/order_receiver services/_archive/order_receiver
git mv systemd/midlab-order-receiver.service systemd/_archive_midlab-order-receiver.service 2>/dev/null || true
echo "Done."
