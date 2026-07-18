#!/usr/bin/env bash
set -euo pipefail
echo "Disabling and stopping midlab-result-sender..."
sudo systemctl stop midlab-result-sender || true
sudo systemctl disable midlab-result-sender || true
echo "Archiving services/result_sender/ -> services/_archive/result_sender/"
mkdir -p services/_archive
git mv services/result_sender services/_archive/result_sender
git mv systemd/midlab-result-sender.service systemd/_archive_midlab-result-sender.service 2>/dev/null || true
echo "Done. Commit the archive moves manually."
