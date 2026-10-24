"""Local TV transport. Secrets live outside the checkout; logs contain no payloads."""
import json
import logging
import os
import threading
import time
import urllib.request
import websocket
from datetime import datetime, timezone
from pathlib import Path

from samsungtvws import SamsungTVWS
from diagnostics import timeline, tv_event

logging.getLogger('samsungtvws').setLevel(logging.CRITICAL)
logging.getLogger('websocket').setLevel(logging.CRITICAL)
ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'SmartTVLocale'
STATE.mkdir(parents=True, exist_ok=True)
if os.name != 'nt':
    STATE.chmod(0o700)
# No household address is shipped. Set TV_HOST to the television's LAN IP.
HOST = os.environ.get('TV_HOST', '127.0.0.1')
PORT = int(os.environ.get('TV_PORT', '8002'))
# IDs read from this UE49KS7000 via ed.installedApp.get.
APPS = {
    'youtube': {'name': 'YouTube', 'id': '111299001912'},
    'netflix': {'name': 'Netflix', 'id': '11101200001'},
    'primevideo': {'name': 'Prime Video', 'id': '3201512006785'},
}
ALLOWED = {f'KEY_{d}' for d in range(10)} | {
    'KEY_VOLUP', 'KEY_VOLDOWN', 'KEY_MUTE', 'KEY_CHUP', 'KEY_CHDOWN',
    'KEY_UP', 'KEY_DOWN', 'KEY_LEFT', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_RETURN',
    'KEY_EXIT', 'KEY_HOME', 'KEY_MENU', 'KEY_SOURCE', 'KEY_GUIDE', 'KEY_INFO',
    'KEY_CH_LIST', 'KEY_PLAY', 'KEY_PAUSE', 'KEY_STOP', 'KEY_REWIND', 'KEY_FF', 'KEY_TV',
    'KEY_HDMI1'}

def record(action, started, status, **extra):
    item = dict(timestamp=datetime.now(timezone.utc).isoformat(), action=action,
                elapsed_ms=round((time.perf_counter()-started)*1000, 2), status=status, **extra)
    (ROOT / 'reports').mkdir(exist_ok=True)
    with (ROOT / 'reports/commands.jsonl').open('a', encoding='utf-8') as output:
        output.write(json.dumps(item) + '\n')
    timeline.emit('Server', f'{action} → {status} · {item["elapsed_ms"]} ms' +
                  (' · ' + extra['error_type'] if 'error_type' in extra else ''),
                  'error' if status == 'error' else 'info')
    return item

class ObservedTV(SamsungTVWS):
    def _websocket_event(self, event, response):
        super()._websocket_event(event, response)
        tv_event(event, response)

class Remote:
    def __init__(self, timeout=12, trace_events=False):
        self.lock = threading.Lock()
        self.trace_events = trace_events
        self._closing = set()
        self.tv = (ObservedTV if trace_events else SamsungTVWS)(host=HOST, port=PORT, timeout=timeout,
            name=os.environ.get('TV_CLIENT_NAME', 'Smart TV Locale'),
            token_file=str(STATE / 'tv-token'), key_press_delay=0.35)

    def connect(self):
        start = time.perf_counter()
        try:
            if self.tv.connection is not None and not self.tv.is_alive():
                self.close()
            timeline.emit('Server', f'Apertura WebSocket porta {self.tv.port}')
            self.tv.open()
            if getattr(self, 'trace_events', False):
                connection = self.tv.connection
                threading.Thread(target=self._read_events, args=(connection,), daemon=True).start()
            token = STATE / 'tv-token'
            if token.exists() and os.name != 'nt':
                token.chmod(0o600)
            return record('connect', start, 'authorized', port=self.tv.port)
        except Exception as exc:
            # Exception text may contain a response or token. Store only its type.
            record('connect', start, 'error', error_type=type(exc).__name__, port=self.tv.port)
            raise RuntimeError(type(exc).__name__) from None

    def _read_events(self, connection):
        label = hex(id(connection))[-6:]
        try:
            while connection.connected:
                try:
                    raw = connection.recv()
                    if not raw:
                        break
                    response = json.loads(raw)
                    if isinstance(response, dict):
                        self.tv._websocket_event(response.get('event'), response)
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception as exc:
                    if id(connection) not in self._closing:
                        timeline.emit('TV', f'Socket {label}: ricezione terminata · {type(exc).__name__}', 'warning')
                    break
        finally:
            expected = id(connection) in self._closing
            timeline.emit('TV', f'Socket {label}: ' + ('chiusura richiesta dal server' if expected else
                          'collegamento chiuso; il prossimo invio aprirà una nuova connessione'),
                          'info' if expected else 'warning')
            self._closing.discard(id(connection))
            if self.tv.connection is connection:
                connection.connected = False

    def send(self, keys, action=None, pauses=None):
        if not keys or any(key not in ALLOWED for key in keys):
            raise ValueError('Comando non consentito')
        with self.lock:
            if not self.tv.is_alive():
                self.connect()
            else:
                timeline.emit('Server', 'Riutilizzo WebSocket esistente')
            start = time.perf_counter()
            try:
                for index, key in enumerate(keys):
                    timeline.emit('Server → TV', f'WS Click {key}')
                    self.tv.send_key(key, key_press_delay=pauses[index] if pauses else None)
                timeline.emit('Server', 'Scrittura WebSocket completata; nessun ACK di esecuzione previsto per i tasti')
                return record(action or ','.join(keys), start, 'sent',
                    note='Invio WebSocket e pausa inclusa; effetto sullo schermo non confermato')
            except Exception as exc:
                self.tv.close()
                record(action or ','.join(keys), start, 'error', error_type=type(exc).__name__)
                raise RuntimeError(type(exc).__name__) from None

    def switch_to_tv(self):
        # Some Tizen apps ignore KEY_TV while foregrounded. Allow time to exit
        # the app before selecting TV, then dismiss any leftover overlay.
        return self.send(['KEY_EXIT', 'KEY_TV', 'KEY_EXIT'], 'source:tv',
                         pauses=[2.0, 0.35, 0.35])

    def switch_to_pc(self):
        return self.send(['KEY_EXIT', 'KEY_HDMI1', 'KEY_EXIT'], 'source:pc:hdmi1',
                         pauses=[2.0, 0.35, 0.35])

    def launch_app(self, app):
        if not isinstance(app, str) or app not in APPS:
            raise ValueError('App non consentita')
        with self.lock:
            started = time.perf_counter()
            try:
                # REST launch was verified on this model. WS deep-link launch
                # opened YouTube but did not bring Netflix to the foreground.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                url = f'http://{HOST}:8001/api/v2/applications/{APPS[app]["id"]}'
                request = urllib.request.Request(url, data=b'', method='POST')
                timeline.emit('Server → TV', f'REST POST apertura {APPS[app]["name"]}')
                with opener.open(request, timeout=6) as response:
                    http_status = response.status
                timeline.emit('TV', f'REST apertura {APPS[app]["name"]}: HTTP {http_status}')
                state = {}
                for _ in range(2):
                    time.sleep(1)
                    try:
                        with opener.open(url, timeout=3) as response:
                            data = json.load(response)
                        state = {k: data.get(k) if isinstance(data.get(k), bool) else None
                                 for k in ('running', 'visible')}
                        timeline.emit('TV', f'Stato {APPS[app]["name"]}: running={state["running"]}, visible={state["visible"]} (dichiarato dalla TV)')
                    except Exception as exc:
                        state = {'state_error_type': type(exc).__name__}
                        break
                    if state.get('visible') is True:
                        break
                return record('app:' + app, started, 'sent', transport='rest',
                    http_status=http_status, app_name=APPS[app]['name'], **state,
                    note='Stato dichiarato dalla TV; riproduzione e accesso account non verificati')
            except Exception as exc:
                record('app:' + app, started, 'error', error_type=type(exc).__name__)
                raise RuntimeError(type(exc).__name__) from None

    def close(self):
        if getattr(self, 'trace_events', False) and self.tv.connection is not None:
            self._closing.add(id(self.tv.connection))
        self.tv.close()

def channel_keys(channel):
    value = str(channel)
    if not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 4 or int(value) == 0:
        raise ValueError('Inserisci un canale da 1 a 9999')
    return [f'KEY_{digit}' for digit in value] + ['KEY_ENTER']


def volume_keys(step):
    if type(step) is not int or step not in {-2, 2}:
        raise ValueError('Volume: solo passi di due')
    return ['KEY_VOLUP' if step > 0 else 'KEY_VOLDOWN'] * 2
