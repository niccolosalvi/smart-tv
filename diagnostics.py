"""Bounded diagnostic timeline. Never pass raw TV payloads or credentials here."""
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import uuid

request_id = ContextVar('diagnostic_request_id', default=None)

class Timeline:
    def __init__(self, limit=300, path=None):
        self.events = deque(maxlen=limit)
        self.lock = threading.Lock()
        self.sequence = 0
        self.epoch = uuid.uuid4().hex[:12]
        self.path = Path(path) if path else None

    def emit(self, sender, message, level='info', correlation=None):
        with self.lock:
            self.sequence += 1
            item = {'id': self.sequence, 'timestamp': datetime.now(timezone.utc).isoformat(),
                    'sender': sender, 'message': str(message)[:500], 'level': level,
                    'request_id': correlation or request_id.get()}
            self.events.append(item)
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    if self.path.exists() and self.path.stat().st_size > 2_000_000:
                        self.path.replace(self.path.with_suffix('.previous.jsonl'))
                    with self.path.open('a', encoding='utf-8') as output:
                        output.write(json.dumps(item, ensure_ascii=False) + '\n')
                except OSError:
                    pass  # Diagnostics must never prevent a TV command.
            return item

    def read(self, after=0, epoch=None):
        with self.lock:
            reset = epoch != self.epoch
            events = list(self.events) if reset else [e for e in self.events if e['id'] > after]
            return {'epoch': self.epoch, 'cursor': self.sequence, 'events': events,
                    'reset': reset}

timeline = Timeline()

def tv_event(event, response):
    """Expose event names and known errors, never tokens, IDs, URLs or payloads."""
    known = {'ms.channel.connect', 'ms.channel.unauthorized', 'ms.channel.clientConnect',
             'ms.channel.clientDisconnect', 'ms.channel.ready', 'ms.error',
             'ed.apps.launch', 'ed.installedApp.get', 'ed.edenTV.update',
             'ms.remote.imeStart', 'ms.remote.imeEnd', 'ms.voiceApp.hide'}
    name = event if event in known else 'Altro evento WebSocket'
    message = name
    if event == 'ms.channel.connect':
        message += ' · connessione autorizzata (non è ACK di un tasto)'
    elif event == 'ms.error':
        detail = response.get('data', {})
        safe_errors = {'unrecognized method value : ms.remote.control'}
        text = detail.get('message') if isinstance(detail, dict) else None
        message += ' · ' + (text if isinstance(text, str) and text in safe_errors else
                           'errore restituito dalla TV; payload escluso per proteggere i dati')
    timeline.emit('TV', message, 'error' if event in {'ms.error', 'ms.channel.unauthorized'} else 'info')
