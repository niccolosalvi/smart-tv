# Raspberry Pi deployment

Requires Python 3.10+, Raspberry Pi OS with systemd, and network access to the TV.
Screen sharing additionally uses LAN TCP port 8767 alongside dashboard port 8765.
After updating the checkout, install the updated `requirements.txt` in `.venv`
and restart `smart-tv`; no HDMI connection is used. The TV must keep `/screen`
open in its browser. The HTTPS workflow requires no launcher on the PC.

## HTTPS screen sharing

Set `SMART_TV_TLS_CERT` and `SMART_TV_TLS_KEY` in the existing private
`~/.config/smart-tv/server.env`, pointing to a server certificate and its private
key. The certificate must include the Raspberry's numeric LAN IP in its Subject
Alternative Name and be trusted by each PC browser. Preserve all existing TV
settings. Keep the key outside the repository, mode 600, and its directory mode
700. Restart `smart-tv` after configuration. An invalid TLS configuration prevents
startup; retain a backup of both application files and the environment file.

The existing service also starts HTTPS on 8766 and WSS on 8768. Ports can be
changed with `--https-port` and `--wss-port`; HTTP 8765/WS 8767 remain the TV path.
The PC and TV share one relay. Do not redirect the old TV browser to HTTPS.

For an isolated LAN, a self-signed **server** certificate with `CA:FALSE` and
`extendedKeyUsage=serverAuth` can be generated on the Raspberry with OpenSSL.
Bind it to the actual IP, export only its public certificate for the PC, and
verify its fingerprint before the one-time trust installation. The private key
must remain on the Raspberry. On Windows the certificate can be imported for
the current user; trust configuration is a separate explicit setup step, not
something a web page installs silently. Renew the certificate before expiry and
update trust on the PC when replacing a self-signed certificate.

After setup, open `https://RASPBERRY_IP:8766/`, then **Condividi dal PC** and
**Condividi**. The browser asks which screen/window/tab to capture, then the TV
receiver opens automatically. No Python installation or command on the PC is
required for normal use.

Audio uses a second connection on the same WS/WSS ports, with no extra service
or codec package on the Raspberry. Update `audio_share.py`, the sharing modules,
and all four `web/audio-*.js` files together with the existing web assets. Reload
the PC page and reopen the TV receiver after restarting the service. To test,
enable audio in both the page and the browser's capture dialog. If the receiver
shows **Attiva audio**, select it once with the TV remote to unlock playback.

## Service installation
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
