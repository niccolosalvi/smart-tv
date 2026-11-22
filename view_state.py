"""Shared UI context, inferred from requests, never presented as TV telemetry."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

SOURCES = {'unknown', 'tv', 'youtube', 'netflix', 'primevideo', 'pc', 'screen'}
APP_VIEWS = {'youtube', 'netflix', 'primevideo'}
NAVIGATION = {'KEY_HOME', 'KEY_SOURCE', 'KEY_RETURN', 'KEY_EXIT'}

class ViewState:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.state = {'source': 'unknown', 'basis': 'unknown', 'channel_requested': None,
                      'updated_at': None, 'revision': 0, 'persisted': False}
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if saved.get('source') in SOURCES:
                channel = saved.get('channel_requested')
                if not isinstance(channel, str) or not channel.isascii() or not channel.isdigit() or len(channel) > 4:
                    channel = None
                self.state.update(source=saved['source'], basis='restored', channel_requested=channel,
                                  updated_at=saved.get('updated_at'), persisted=True)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def get(self):
        with self.lock:
            return self.state.copy()

    def select(self, source, basis='manual', channel=None):
        if not isinstance(source, str) or source not in SOURCES:
            raise ValueError('Vista non valida')
        with self.lock:
            self.state.update(source=source, basis=basis, channel_requested=channel,
                updated_at=datetime.now(timezone.utc).isoformat(), revision=self.state['revision']+1)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.state), encoding='utf-8')
                temporary.replace(self.path)
                self.state['persisted'] = True
            except OSError:
                self.state['persisted'] = False
            return self.get()

    def after_command(self, payload):
        if 'youtube_search' in payload or 'youtube_url' in payload or 'youtube_seek' in payload:
            return self.select('youtube', 'last_request')
        if payload.get('screen') == 'stop':
            return self.select('unknown', 'unconfirmed')
        if payload.get('screen') == 'open':
            return self.select('screen', 'last_request')
        if payload.get('power') == 'on':
            # This user's TV always starts on the antenna input.
            return self.select('tv', 'power_on_default')
        if 'power' in payload:
            return self.select('unknown', 'power_request')
        if 'app' in payload:
            return self.select(payload['app'], 'last_request')
        if payload.get('source') == 'pc' or payload.get('key') == 'KEY_HDMI1':
            return self.select('pc', 'last_request')
        if 'channel' in payload:
            return self.select('tv', 'last_request', str(payload['channel']))
        if payload.get('key') == 'KEY_TV':
            return self.select('tv', 'last_request')
        if payload.get('key') in {'KEY_CHUP', 'KEY_CHDOWN'}:
            return self.select('tv', 'last_request')
        if payload.get('key') in NAVIGATION:
            with self.lock:
                if payload.get('key') == 'KEY_RETURN' and self.state['source'] in APP_VIEWS:
                    return self.get()
                return self.select('unknown', 'unconfirmed')
        return self.get()

    def after_failure(self, payload):
        if payload.get('key') == 'KEY_RETURN':
            with self.lock:
                if self.state['source'] in APP_VIEWS:
                    return self.get()
                return self.select('unknown', 'unconfirmed')
        if 'youtube_search' in payload or 'youtube_url' in payload or 'screen' in payload or 'power' in payload or 'app' in payload or 'source' in payload or 'channel' in payload or payload.get('key') in NAVIGATION | {'KEY_TV', 'KEY_HDMI1', 'KEY_CHUP', 'KEY_CHDOWN'}:
            return self.select('unknown', 'unconfirmed')
        return self.get()

    def sync_live(self, tv_online, active_app=None, screen_active=False):
        with self.lock:
            current = self.state['source']
            basis = self.state['basis']
            channel = self.state['channel_requested']
            if screen_active:
                if current != 'screen' or basis != 'tv_verified':
                    return self.select('screen', 'tv_verified')
                return self.get()
            if not tv_online:
                return self.get()
            if active_app in APP_VIEWS:
                if current != active_app or basis != 'tv_verified':
                    return self.select(active_app, 'tv_verified')
                return self.get()
            # TV is ON, no streaming app is visible in foreground, and screen sharing is not active.
            if basis == 'manual' and current not in APP_VIEWS | {'screen'}:
                return self.get()
            if current in APP_VIEWS | {'screen', 'unknown'}:
                return self.select('tv', 'tv_verified', channel if current == 'tv' else None)
            if basis != 'tv_verified':
                return self.select(current, 'tv_verified', channel if current == 'tv' else None)
            return self.get()
