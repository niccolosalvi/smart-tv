"""LAN-only remote dashboard, using the Python standard HTTP server."""
import argparse
import json
import secrets
import socket
import threading
import re
import time
import uuid
import ipaddress
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from remote import APPS, HOST, PORT, STATE, Remote, channel_keys, volume_keys
from view_state import SOURCES, ViewState
from power_control import power_off, wake
from diagnostics import timeline, request_id
from epg import Guide
from health import Health
from screen_share import ShareRelay
from sharing_pages import render_share
from text_input import validate_query

PAGE = (Path(__file__).parent / 'web' / 'index.html').read_text(encoding='utf-8')
SCRIPT_ASSETS = {f'/assets/{name}.js': (Path(__file__).parent / 'web' / (name+'.js')).read_text(encoding='utf-8')
                 for name in ('screen', 'share', 'share-worker', 'mp4', 'keyboard',
                              'audio-share', 'audio-capture', 'audio-worker', 'audio-receiver')}
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
sharing = None
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
        share_csp = ''
        if urlsplit(self.path).path in ('/share', '/screen'):
            address = self.connection.getsockname()[0]
            secure = self.scheme() == 'https'
            port = (sharing.secure_port if secure else sharing.port) if sharing else 8767
            protocol = 'wss' if secure else 'ws'
            share_csp = f"; connect-src 'self' {protocol}://{address}:{port}; img-src 'self' blob: data:; media-src 'self' blob:"
        # The 2016 TV browser cannot be assumed to understand nonce sources.
        # Its receiver uses a static same-origin script, supported by CSP1 too.
        path = urlsplit(self.path).path
        script_policy = "'self'" if path in ('/share', '/screen') or path in SCRIPT_ASSETS else "'nonce-" + CSRF + "'"
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src " + script_policy + "; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" + share_csp)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            timeline.emit('Server', 'Browser disconnesso prima della consegna della risposta; comando non ritentato', 'warning')

    def valid_host(self):
        try:
            url = urlsplit('http://' + self.headers.get('Host', ''))
            # The network can acquire or change its address after startup. The
            # accepted socket identifies the actual local destination for this
            # request without trusting arbitrary Host names or stale addresses.
            local_address = self.connection.getsockname()[0]
            valid_name = url.hostname == local_address or (
                url.hostname == 'localhost' and local_address == '127.0.0.1')
            return valid_name and url.port == self.server.server_port
        except ValueError:
            return False

    def screen_url(self):
        address = self.connection.getsockname()[0]
        if ipaddress.ip_address(address).is_loopback:
            # A dashboard opened locally on the Raspberry must still give the TV
            # the LAN address. Resolve the current route without sending data.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect((HOST, PORT))
                address = route.getsockname()[0]
        if ipaddress.ip_address(address).is_loopback:
            raise ValueError('Indirizzo LAN del ricevitore non disponibile')
        return f'http://{address}:{getattr(self.server, "http_port", self.server.server_port)}/screen'

    def scheme(self):
        return getattr(self.server, 'scheme', 'http')

    def origin(self):
        return self.scheme() + '://' + self.headers['Host']

    def sharing_config(self):
        secure = self.scheme() == 'https'
        address = self.connection.getsockname()[0]
        http_port = getattr(self.server, 'http_port', self.server.server_port)
        https_port = getattr(self.server, 'https_port', None)
        port = (sharing.secure_port if secure else sharing.port) if sharing else None
        return {'available': port is not None, 'port': port, 'key': CSRF,
                'server': self.origin(),
                'receiver_url': f'http://{address}:{http_port}/screen',
                'ws_url': f'{"wss" if secure else "ws"}://{address}:{port}/screen',
                'https_url': f'https://{address}:{https_port}/share' if https_port else None,
                'receiver_connected': sharing is not None and sharing.viewer is not None,
                'sender_connected': sharing is not None and sharing.sender is not None,
                'receiver_capabilities': sharing.viewer_info if sharing else {},
                'transmission': sharing.metrics.copy() if sharing else {},
                'audio': sharing.audio.snapshot() if sharing else {}}

    def do_GET(self):
        self.correlation = None
        if not self.valid_host():
            return self.reply(403, {'error': 'Host non consentito'})
        if self.path == '/':
            link = self.sharing_config()['https_url'] or '/share'
            return self.reply(200, PAGE.replace('__CSRF__', CSRF).replace('__SHARE_URL__', link), 'text/html')
        if self.path in ASSETS:
            return self.reply(200, ASSETS[self.path], 'image/png')
        if self.path in SCRIPT_ASSETS:
            return self.reply(200, SCRIPT_ASSETS[self.path], 'application/javascript')
        parsed = urlsplit(self.path)
        if parsed.path in ('/share', '/screen', '/api/share'):
            config = self.sharing_config()
            if parsed.path == '/api/share':
                return self.reply(200, config)
            return self.reply(200, render_share(
                'share' if parsed.path == '/share' else 'screen', config, CSRF), 'text/html')
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
        if origin and origin != self.origin():
            return self.reply(403, {'error': 'Origine non consentita'})
        if self.path != '/api/command':
            return self.reply(404, {'error': 'Risorsa non trovata'})
        if self.headers.get_content_type() != 'application/json':
            return self.reply(415, {'error': 'JSON richiesto'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2048:
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
            if 'youtube_search' in data:
                try:
                    validate_query(data['youtube_search'])
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
                keys, action = [], 'youtube:search'
            elif 'screen' in data:
                if data['screen'] not in ('open', 'stop'):
                    raise ValueError('Azione schermo non consentita')
                if data['screen'] == 'open' and (sharing is None or sharing.port is None):
                    return self.reply(503, {'error': 'Condivisione schermo non disponibile'})
                if data['screen'] == 'open':
                    try:
                        receiver_url = self.screen_url()
                    except (OSError, ValueError):
                        return self.reply(503, {'error': 'Indirizzo LAN del ricevitore non disponibile'})
                keys, action = [], 'screen:'+data['screen']
            elif 'power' in data:
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
            if 'youtube_search' in data:
                result = remote.search_youtube(data['youtube_search'])
            elif 'screen' in data:
                if data['screen'] == 'open':
                    if sharing.viewer is not None:
                        result = remote.open_screen(receiver_url, reuse=True)
                    else:
                        result = remote.open_screen(receiver_url)
                else:
                    if sharing:
                        try:
                            sharing.end_session()
                        except (TimeoutError, RuntimeError):
                            timeline.emit('Server', 'Chiusura relay non confermata; procedo con uscita browser', 'warning')
                    result = remote.close_screen()
            elif 'power' in data:
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
    parser.add_argument('--share-port', type=int, default=8767,
                        help='Porta WebSocket per la condivisione schermo')
    parser.add_argument('--https-port', type=int, default=8766)
    parser.add_argument('--wss-port', type=int, default=8768)
    parser.add_argument('--tls-cert', default=os.environ.get('SMART_TV_TLS_CERT'))
    parser.add_argument('--tls-key', default=os.environ.get('SMART_TV_TLS_KEY'))
    args = parser.parse_args()
    tls = None
    if args.tls_cert or args.tls_key:
        if not (args.tls_cert and args.tls_key):
            parser.error('Certificato e chiave TLS sono entrambi richiesti')
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(args.tls_cert, args.tls_key)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    server.http_port = server.server_port
    server.https_port = args.https_port if tls else None
    secure_server = None
    if tls:
        secure_server = ThreadingHTTPServer((args.bind, args.https_port), Handler)
        secure_server.daemon_threads = True
        secure_server.scheme = 'https'
        secure_server.http_port = server.server_port
        secure_server.https_port = secure_server.server_port
        server.https_port = secure_server.server_port
        secure_server.socket = tls.wrap_socket(secure_server.socket, server_side=True)
    sharing = ShareRelay(CSRF, http_port=server.server_port,
                         https_port=secure_server.server_port if secure_server else args.https_port)
    try:
        sharing.start(args.bind, args.share_port, tls, args.wss_port)
    except RuntimeError as exc:
        server.server_close()
        parser.exit(1, f'{exc}: {sharing.error}\n')
    if secure_server:
        threading.Thread(target=secure_server.serve_forever, daemon=True).start()
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
        sharing.stop()
        if secure_server:
            secure_server.shutdown()
            secure_server.server_close()
        server.server_close()
        remote.close()
