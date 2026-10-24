"""LAN-only remote dashboard, using the Python standard HTTP server."""
import argparse
import json
import secrets
import socket
import threading
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from remote import APPS, HOST, PORT, STATE, Remote, channel_keys, volume_keys
from view_state import SOURCES, ViewState
from power_control import power_off, wake
from diagnostics import timeline, request_id
from epg import Guide
from health import Health

PAGE = (Path(__file__).parent / 'web' / 'index.html').read_text(encoding='utf-8')
ASSETS = {f'/assets/{name}.png': (Path(__file__).parent / 'web' / 'assets' / f'{name}.png').read_bytes()
          for name in ('youtube', 'netflix', 'primevideo')}
CSRF = secrets.token_urlsafe(32)  # Browser control key, memory only, never TV token.
timeline.path = STATE / 'diagnostics.jsonl'
timeline.emit('Server', 'Diagnostica avviata · payload e credenziali esclusi')
remote = Remote(trace_events=True)
busy = threading.Lock()
view = ViewState(STATE / 'ui-context.json')
guide = Guide(STATE / 'epg-cache.json')
health = Health()
allowed_hosts = {'localhost', '127.0.0.1', *socket.gethostbyname_ex(socket.gethostname())[2]}
# Raspberry Pi hostname resolution may return loopback only. Find the interface
# selected for the TV route without transmitting any UDP payload.
try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect((HOST, PORT))
        allowed_hosts.add(route.getsockname()[0])
except OSError:
    pass

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, data, content_type='application/json'):
        correlation = getattr(self, 'correlation', None)
        elapsed = None
        if correlation:
            elapsed = round((time.perf_counter()-self.request_started)*1000, 2)
            if isinstance(data, dict):
                data = {**data, 'request_id': correlation, 'server_elapsed_ms': elapsed}
            timeline.emit('Server → Browser', f'HTTP {status} · {elapsed} ms' +
                          (' · errore/rifiuto' if status >= 400 else ' · risposta completata'),
                          'error' if status >= 400 else 'info')
        body = data if isinstance(data, bytes) else data.encode('utf-8') if isinstance(data, str) else json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if correlation:
            self.send_header('X-Request-ID', correlation)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'nonce-" + CSRF + "'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            timeline.emit('Server', 'Browser disconnesso prima della consegna della risposta; comando non ritentato', 'warning')

    def valid_host(self):
        try:
            url = urlsplit('http://' + self.headers.get('Host', ''))
            return url.hostname in allowed_hosts and url.port == self.server.server_port
        except ValueError:
            return False

    def do_GET(self):
        self.correlation = None
        if not self.valid_host():
            return self.reply(403, {'error': 'Host non consentito'})
        if self.path == '/':
            return self.reply(200, PAGE.replace('__CSRF__', CSRF), 'text/html')
        if self.path in ASSETS:
            return self.reply(200, ASSETS[self.path], 'image/png')
        parsed = urlsplit(self.path)
        if parsed.path == '/api/health':
            return self.reply(200, health.snapshot(busy.locked()))
        if parsed.path == '/api/epg':
            return self.reply(200, guide.snapshot())
        if parsed.path == '/api/logs':
            if self.headers.get('X-Remote-Key') != CSRF:
                return self.reply(403, {'error': 'Richiesta non autorizzata'})
            query = parse_qs(parsed.query)
            try:
                after = max(0, int(query.get('after', ['0'])[0]))
            except ValueError:
                return self.reply(400, {'error': 'Cursore non valido'})
            return self.reply(200, timeline.read(after, query.get('epoch', [None])[0]))
        if self.path == '/api/status':
            return self.reply(200, {'host': HOST, 'port': PORT, 'connected': remote.tv.is_alive(),
                'context': view.get(),
                'note': 'Connessione socket; lo stato di volume e canale non viene letto dalla TV'})
        self.reply(404, {'error': 'Risorsa non trovata'})

    def do_POST(self):
        self.request_started = time.perf_counter()
        proposed = self.headers.get('X-Request-ID', '')
        self.correlation = proposed if re.fullmatch(r'[a-zA-Z0-9-]{8,64}', proposed) else uuid.uuid4().hex[:16]
        token = request_id.set(self.correlation)
        try:
            timeline.emit('Browser → Server', 'POST ricevuto')
            self._handle_post()
        finally:
            request_id.reset(token)

    def _handle_post(self):
        if not self.valid_host() or self.headers.get('X-Remote-Key') != CSRF:
            return self.reply(403, {'error': 'Richiesta non autorizzata'})
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.reply(403, {'error': 'Origine non consentita'})
        if self.path != '/api/command':
            return self.reply(404, {'error': 'Risorsa non trovata'})
        if self.headers.get_content_type() != 'application/json':
            return self.reply(415, {'error': 'JSON richiesto'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024:
                raise ValueError('Dimensione richiesta non valida')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Oggetto JSON richiesto')
            if len(data) != 1:
                raise ValueError('Un solo comando per richiesta')
            if 'context' in data:
                source = data['context']
                if not isinstance(source, str) or source not in SOURCES:
                    raise ValueError('Vista non valida')
                if not busy.acquire(blocking=False):
                    timeline.emit('Server', 'Richiesta rifiutata: un altro comando è in corso', 'warning')
                    return self.reply(409, {'error': 'Comando in corso, riprova tra poco'})
                try:
                    timeline.emit('Server', f'Scelta vista {source}: nessun comando alla TV')
                    return self.reply(200, {'status': 'view_selected', 'context': view.select(source)})
                finally:
                    busy.release()
            if 'power' in data:
                if not isinstance(data['power'], str) or data['power'] not in {'on', 'off'}:
                    raise ValueError('Alimentazione non valida')
                keys, action = [], 'power:' + data['power']
            elif 'volume' in data:
                keys = volume_keys(data['volume'])
                action = 'volume:' + str(data['volume'])
            elif 'source' in data:
                if data['source'] != 'pc':
                    raise ValueError('Sorgente non consentita')
                keys, action = [], 'source:pc:hdmi1'
            elif 'app' in data:
                app = data['app']
                if not isinstance(app, str) or app not in APPS or len(data) != 1:
                    raise ValueError('App non consentita')
                keys, action = [], 'app:' + app
            elif 'channel' in data:
                keys = channel_keys(data['channel'])
                action = 'channel:' + str(data['channel'])
            else:
                key = data.get('key')
                if not isinstance(key, str):
                    raise ValueError('Tasto richiesto')
                keys, action = [key], key
            from remote import ALLOWED
            if any(key not in ALLOWED for key in keys):
                raise ValueError('Tasto non consentito')
        except (ValueError, TypeError):
            timeline.emit('Server', 'Richiesta rifiutata dalla validazione', 'warning')
            return self.reply(400, {'error': 'Comando o numero canale non valido'})
        timeline.emit('Server', f'Comando validato: {action}')
        if not busy.acquire(blocking=False):
            timeline.emit('Server', 'Comando rifiutato: un altro invio è in corso', 'warning')
            return self.reply(409, {'error': 'Comando in corso, riprova tra poco'})
        try:
            if 'power' in data:
                result = wake() if data['power'] == 'on' else power_off(remote)
            elif 'source' in data:
                result = remote.switch_to_pc()
            elif 'app' in data:
                result = remote.launch_app(app)
            elif data.get('key') == 'KEY_TV':
                result = remote.switch_to_tv()
            else:
                result = remote.send(keys, action)
            result['context'] = view.after_command(data)
            self.reply(200, result)
        except RuntimeError as exc:
            self.reply(502, {'error': str(exc), 'context': view.after_failure(data),
                'hint': 'Verifica TV accesa e autorizzazione Smart TV Locale'})
        finally:
            busy.release()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    guide.start()
    print(f'Dashboard: http://127.0.0.1:{args.port}', flush=True)
    for address in sorted(allowed_hosts - {'localhost', '127.0.0.1'}):
        print(f'Smartphone sulla stessa LAN: http://{address}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        guide.stop.set()
        server.server_close()
        remote.close()
