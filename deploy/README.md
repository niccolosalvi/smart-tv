# Raspberry Pi deployment

Requires Python 3.10+, Raspberry Pi OS with systemd, and network access to the TV.
Run from the repository root as your ordinary Raspberry user, not root:

```sh
TV_HOST=198.51.100.20 bash deploy/install-pi.sh
```

Replace the example with your TV's actual LAN IP. For a fresh install `TV_HOST`
is required; an existing environment file is preserved on subsequent installs.
The installer may install python3-venv if missing. It does not upgrade the OS,
change network addresses or configure router port forwarding.

## Files

- Application and virtual environment: the checkout and its `.venv/`.
- Private service configuration: `~/.config/smart-tv/server.env` (mode 600).
- TV token, wake MAC, diagnostics, guide and view cache:
  `~/.local/share/SmartTVLocale/`.
- System service: `/etc/systemd/system/smart-tv.service`, running as the installer
  user with restart-on-failure. No desktop or interactive login is required.

Authorize the Raspberry as a new client on the TV. Do not copy another device's
TV token. While the TV is on, prepare Wake-on-LAN:

```sh
.venv/bin/python -m tools.power prepare
```

## Management

```sh
sudo systemctl status smart-tv
sudo systemctl restart smart-tv
journalctl -u smart-tv -n 100 --no-pager
.venv/bin/python -m tools.verify_server
```

The daily `smart-tv-refresh.timer` restarts only the server at 11:00 Europe/Rome,
including daylight saving time. It uses `try-restart` to respect a deliberately
stopped server and does not catch up missed refreshes after downtime. This is
optional preventive maintenance, not evidence of a memory leak.

```sh
systemctl list-timers smart-tv-refresh.timer --no-pager
sudo systemctl disable --now smart-tv-refresh.timer
# Re-enable from the checkout:
bash deploy/enable-refresh.sh
```

The guide's automatic 24-hour download cycle is separate from that timer. It
survives service restarts using the cache's download timestamp, catches up when
overdue and retries failures after 6 hours.

To remove autostart without deleting runtime data:

```sh
sudo systemctl disable --now smart-tv-refresh.timer
sudo systemctl disable --now smart-tv.service
```

For a stable dashboard address use a DHCP reservation on your router for the
Raspberry's active network interface. Wi-Fi and Ethernet have different MACs.
No household IPs, interface MACs, Wi-Fi passwords or SSH credentials are shipped.
