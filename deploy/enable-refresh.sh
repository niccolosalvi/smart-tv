#!/usr/bin/env bash
set -euo pipefail
deploy_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sudo install -m 644 "$deploy_root/smart-tv-refresh.service" /etc/systemd/system/smart-tv-refresh.service
sudo install -m 644 "$deploy_root/smart-tv-refresh.timer" /etc/systemd/system/smart-tv-refresh.timer
sudo systemctl daemon-reload
sudo systemctl enable --now smart-tv-refresh.timer
sudo systemctl --no-pager list-timers smart-tv-refresh.timer
