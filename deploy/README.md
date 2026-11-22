# Native TV video update

Deploy `native_video.py`, `remote.py`, `server.py`, `web/native-share.js`,
`web/share.js`, `web/share.html` and `tools/share_screen.py` together.
Install the Raspberry OS package `ffmpeg` with `--no-install-recommends`.
FFmpeg only remuxes H.264/AAC from the PC; it does not encode video on the Pi.
Native playback targets 1920×1080 with 60 requested capture images/s. The TV
must be reachable on its DLNA control port 9197 and must fetch the RAM-only
session stream over dashboard HTTP. Preserve certificates, service environment
and existing device tokens. Check normal pages, assets and /api/share after
restart. Retain an application-file backup for rollback.

The DLNA path was tested on the actual UE49KS7000 but has approximately 10–12
seconds of playback delay. Constant mux bitrate and HLS/seek probes did not
solve that limitation; the constant-bitrate trial is removed. FPS telemetry
uses video PES timestamps, separate from requested capture rate and audio.
The packaged AVPlay receiver source is in `tv-receiver/`. It requires Samsung
signing and Developer Mode, and has not yet been tested on the real TV.
`SMART_TV_TIZEN_RECEIVER=1` is opt-in after that setup. It is disabled by default.

# Raspberry Pi deployment

## Film/video quality update

Deploy `screen_share.py`, `web/share.html`, `web/share.js`,
`web/share-worker.js` and `web/screen.js` together, then restart `smart-tv`
and reload both sharing and receiver pages. The relay must be updated before
selecting Full HD: older relay versions reject dimensions above 1280 × 720.
Film/video quality encodes full JPEG frames directly, avoiding CPU pixel readback,
tile scans and speculative PNG encoding. Desktop mode retains exact damage
tracking and lossless patches. Capture is capped to the negotiated path (30
images/s for JPEG, 60 for H.264); one encoder input is allowed at a time to avoid
stale frames waiting in the worker. Unsupported 90/120 choices are removed.

Film/video quality is selected by default at 1280 px / 30 requested images/s;
Full HD and 24/25 images/s are optional. Real TV decoding throughput and visible
quality must be checked on the target device before claiming an improvement.

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

## iPhone certificate setup

An optional separate HTTPS listener on 8769 and WSS listener on 8770 preserve
the PC certificate and trust configuration. Configure `SMART_TV_IOS_TLS_CERT`,
`SMART_TV_IOS_TLS_KEY`, and `SMART_TV_IOS_PROFILE` together in the private service
environment. The server chain must have a leaf certificate with `CA:FALSE`,
`serverAuth`, and the Raspberry IP in its SAN, signed by a local root CA with
`CA:TRUE` and `keyCertSign`. Keep both private keys on the Raspberry, mode 600.
The profile must contain exactly one `com.apple.security.root` public DER
certificate, inside a `Configuration` payload; do not include private keys.

After restarting, open `http://RASPBERRY_IP:8765/setup/iphone` in Safari. Compare
the displayed SHA-256 fingerprint through a separate trusted channel before
installing. Install the downloaded profile in Settings > General > VPN & Device
Management, then enable full trust under General > About > Certificate Trust
Settings. Open `https://RASPBERRY_IP:8769/share` afterwards. Downloading alone
does not enable trust. Removing the profile revokes this local trust. Renew
the leaf before expiry. Keep these ports on the LAN, without router forwarding.

The extra listeners share the existing relay and keep the TV on HTTP/WS. Each
WSS listener accepts only its own HTTPS origin. Certificate setup enables a
secure context; actual capture and audio availability still depend on iOS and
the browser and require testing on the phone.

While capturing, use **Telecomando** on the sharing page to open the existing
controls inside that document. **Condivisione**, the embedded sharing link and
browser Back switch views without ending the capture or starting another one.
The preview remains rendered in the callback-based capture fallback. The
`/remote-panel` route permits framing only by the same origin; all other pages
retain their framing prohibition and commands retain their key/origin checks.
**Interrompi** is available in both views. Leaving or reloading the capture
document still ends the session intentionally. Switching away from Safari on
iOS may suspend capture, encoding or networking; the internal remote does not
grant background execution. After OS suspension, elapsed background time is
excluded from the sender's local acknowledgment watchdog.

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
