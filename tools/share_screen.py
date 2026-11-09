"""Serve the screen-capture page on PC localhost; no video passes through Python."""
import argparse
import ipaddress
import json
import secrets
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from sharing_pages import render_share, WEB


def server_url(value):
    try:
        url = urlsplit(value)
        ipaddress.IPv4Address(url.hostname)
        if (url.scheme != 'http' or not url.port or url.username or url.password or
                url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise argparse.ArgumentTypeError('Usa http://IP_DEL_RASPBERRY:8765') from None
    return value.rstrip('/')


def make_handler(page, nonce, ws_url, config=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def valid_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_POST(self):
            # This loopback bridge supports exactly one fixed TV action. It
            # avoids enabling cross-origin commands on the Raspberry server.
            if (not self.valid_host() or not config or
                    self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}' or
                    self.headers.get('X-Remote-Key') != config['key']):
                return self.json_reply(403, {'error': 'Richiesta non autorizzata'})
            if self.path != '/api/command':
                return self.json_reply(404, {'error': 'Risorsa non trovata'})
            if self.headers.get_content_type() != 'application/json':
                return self.json_reply(415, {'error': 'JSON richiesto'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                if data not in ({'screen': 'open'}, {'screen': 'stop'}):
                    raise ValueError()
            except (ValueError, TypeError):
                return self.json_reply(400, {'error': 'Solo apertura schermo TV consentita'})
            request = urllib.request.Request(config['server'] + '/api/command',
                data=json.dumps(data).encode(), headers={'Content-Type': 'application/json',
                    'X-Remote-Key': config['key'], 'Origin': config['server']})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(request, timeout=20) as response:
                    self.json_reply(response.status, json.load(response))
            except urllib.error.HTTPError as exc:
                with exc:
                    self.json_reply(exc.code, json.load(exc))
            except (OSError, ValueError):
                self.json_reply(502, {'error': 'Raspberry non raggiungibile o risposta non valida'})

        def json_reply(self, status, value):
            body = json.dumps(value).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # The command may already have been delivered. Never retry.

        def do_GET(self):
            if not self.valid_host():
                self.send_error(403)
                return
            if self.path == '/api/share' and config:
                return self.json_reply(200, config)
            asset = self.path in ['/assets/'+name+'.js' for name in ('share', 'share-worker', 'mp4')]
            if self.path != '/' and not asset:
                self.send_error(404)
                return
            body = (WEB/self.path.rsplit('/',1)[1]).read_bytes() if asset else page.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', ('application/javascript' if asset else 'text/html')+'; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; script-src 'self'; worker-src 'self'; "
                             f"style-src 'unsafe-inline'; img-src data:; media-src blob:; connect-src 'self' {ws_url}; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True, type=server_url)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        # Bypass system proxies: the destination is a user-specified LAN address.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(args.server + '/api/share', timeout=5) as response:
            config = json.load(response)
        if not config.get('available'):
            parser.exit(1, 'Condivisione non disponibile: aggiorna e riavvia il server Raspberry.\n')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Raspberry non raggiungibile: {exc}\n')
    nonce = secrets.token_urlsafe(32)
    config['server'] = args.server
    ws_url = f'ws://{urlsplit(args.server).hostname}:{config["port"]}'
    page = render_share('share', config, nonce)
    http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(page, nonce, ws_url, config))
    http.daemon_threads = True
    local = f'http://127.0.0.1:{http.server_port}/'
    print(f'Apri nel browser del PC: {local}', flush=True)
    print('Premi Apri sulla TV nella pagina per aprire il ricevitore senza digitare indirizzi.', flush=True)
    print('Ctrl+C per chiudere il launcher. Per fermare la cattura usa Interrompi nel browser.', flush=True)
    if not args.no_browser:
        webbrowser.open(local)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.server_close()


if __name__ == '__main__':
    main()
