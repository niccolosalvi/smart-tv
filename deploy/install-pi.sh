#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
service_user="$(id -un)"
service_home="$(getent passwd "$service_user" | cut -d: -f6)"
if [[ "$service_user" == root ]]; then
  echo 'Run this installer as the normal Raspberry user, not root.' >&2
  exit 1
fi
config_file="$service_home/.config/smart-tv/server.env"
if [[ ! -e "$config_file" ]]; then
  : "${TV_HOST:?Set TV_HOST to your television LAN IP before installing}"
  python3 -c 'import ipaddress, sys; ipaddress.ip_address(sys.argv[1])' "$TV_HOST"
fi

python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python >= 3.10 required"'
if ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3-venv
fi

cd -- "$app_root"
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m pip check
mkdir -p reports "$service_home/.local/share/SmartTVLocale" "$service_home/.config/smart-tv"
chmod 700 "$service_home/.local/share/SmartTVLocale" "$service_home/.config/smart-tv"
if [[ ! -e "$config_file" ]]; then
  printf '%s\n' "TV_HOST=$TV_HOST" 'TV_PORT=8002' 'TV_CLIENT_NAME="Smart TV Raspberry"' > "$config_file"
  chmod 600 "$config_file"
fi

service_file="$(mktemp)"
trap 'rm -f -- "$service_file"' EXIT
cat > "$service_file" <<EOF
[Unit]
Description=Smart TV local remote
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$service_user
WorkingDirectory=$app_root
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$config_file
ExecStart=$app_root/.venv/bin/python -u $app_root/server.py --bind 0.0.0.0 --port 8765
Restart=on-failure
RestartSec=3
TimeoutStopSec=10
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 644 "$service_file" /etc/systemd/system/smart-tv.service
sudo systemctl daemon-reload
sudo systemctl enable --now smart-tv.service
sudo systemctl restart smart-tv.service
sudo systemctl --no-pager --full status smart-tv.service
bash "$app_root/deploy/enable-refresh.sh"
