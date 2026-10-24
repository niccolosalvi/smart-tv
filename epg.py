"""Small, cached XMLTV guide. No TV commands, API keys or browser-side trackers."""
import gzip
import io
import json
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

# italy5 labels yesterday's listings as today (audit 2026-10-24); italy3 also
# has timezone errors. Never silently fall back to either feed or shift dates.
# italy8 checked against broadcaster listings for C5, Italia1, TV8 and 20.
SOURCE = 'https://www.open-epg.com/files/italy8.xml.gz'
FAVORITES = [('5', 'Canale 5', 'Canale 5.it'), ('6', 'Italia 1', 'Italia Uno.it'),
             ('8', 'TV8', 'TV8.it'), ('9', 'NOVE', 'NOVE.it'), ('20', '20 Mediaset', 'canale 20.it'),
             ('52', 'DMAX', 'DMAX.it')]
# Provider updates daily and asks clients to download once a day.
INTERVAL = 86400
RETRY_INTERVAL = 21600


def parse_time(value):
    parsed = datetime.strptime(value, '%Y%m%d%H%M%S %z')
    return parsed.timestamp()


def parse_guide(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Unsupported XML declarations')
    root = ET.fromstring(data)
    if root.tag != 'tv':
        raise ValueError('Not XMLTV')
    mapping = {identifier: number for number, _, identifier in FAVORITES}
    result = {number: [] for number, _, _ in FAVORITES}
    for item in root.iter('programme'):
        number = mapping.get(item.get('channel'))
        if number is None:
            continue
        try:
            start, end = parse_time(item.get('start', '')), parse_time(item.get('stop', ''))
            title = (item.findtext('title') or '').strip()[:300]
            if start < end and title:
                result[number].append({'start': start, 'end': end, 'title': title})
        except (ValueError, TypeError):
            continue
    for items in result.values():
        items.sort(key=lambda item: item['start'])
    return result


class Feed(dict):
    """Programs with the provider's file timestamp, separate from download time."""
    def __init__(self, programs, modified_at=None):
        super().__init__(programs)
        self.modified_at = modified_at


def download():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(SOURCE, headers={'User-Agent': 'SmartTVLocale/1.0 (personal EPG)'})
    with opener.open(request, timeout=25) as response:
        compressed = response.read(4 * 1024 * 1024 + 1)
        try:
            modified_at = parsedate_to_datetime(response.headers['Last-Modified']).timestamp()
        except (TypeError, ValueError, KeyError, OverflowError):
            modified_at = None
    if len(compressed) > 4 * 1024 * 1024:
        raise ValueError('Guide download too large')
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        data = stream.read(24 * 1024 * 1024 + 1)
    if len(data) > 24 * 1024 * 1024:
        raise ValueError('Guide too large')
    return Feed(parse_guide(data), modified_at)


class Guide:
    def __init__(self, path, fetch=download, clock=time.time):
        self.path, self.fetch, self.clock = Path(path), fetch, clock
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.updated = 0
        self.source_updated = None
        self.programs = {}
        self.error = None
        self.worker = None
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if saved['source'] != SOURCE:
                raise ValueError('Different source')
            self.updated = float(saved['updated'])
            self.source_updated = saved.get('source_updated')
            self.programs = self.validate(saved['programs'])
            if any(not self.programs[n] for n, _, _ in FAVORITES):
                self.updated = 0  # New favorite: refill cache, keep existing listings.
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.updated, self.programs, self.source_updated = 0, {}, None
        self.next_attempt = self.updated + INTERVAL if self.updated else self.clock()

    @staticmethod
    def validate(programs):
        result = {}
        for number, _, _ in FAVORITES:
            items = programs.get(number, [])
            result[number] = sorted([
                {'start': float(p['start']), 'end': float(p['end']), 'title': p['title'][:300]}
                for p in items if isinstance(p['title'], str) and p['title']
                and 0 < float(p['start']) < float(p['end']) < 4102444800
            ], key=lambda p: p['start'])
        return result

    def update(self):
        try:
            fetched = self.fetch()
            programs = self.validate(fetched)
            source_updated = getattr(fetched, 'modified_at', None)
            now = self.clock()
            # Do not overwrite useful cache with an empty/expired or mis-mapped feed.
            if any(not any(p['end'] > now for p in programs[n]) for n, _, _ in FAVORITES):
                raise ValueError('Incomplete or expired guide')
            saved = {'source': SOURCE, 'updated': now, 'source_updated': source_updated, 'programs': programs}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
            temp.replace(self.path)
            with self.lock:
                self.updated, self.programs, self.error = now, programs, None
                self.source_updated = source_updated
                self.next_attempt = now + INTERVAL
            return True
        except Exception as exc:
            with self.lock:
                self.error = type(exc).__name__
                self.next_attempt = self.clock() + RETRY_INTERVAL
            return False

    def snapshot(self):
        now = self.clock()
        with self.lock:
            channels = []
            for number, name, _ in FAVORITES:
                items = self.programs.get(number, [])
                current = next((p for p in items if p['start'] <= now < p['end']), None)
                upcoming = next((p for p in items if p['start'] > now), None)
                channels.append({'number': number, 'name': name, 'current': current, 'next': upcoming})
            return {'source': 'Open-EPG · Italy 8', 'source_url': SOURCE,
                    'updated_at': self.updated or None, 'downloaded_at': self.updated or None,
                    'source_updated_at': self.source_updated, 'server_time': now,
                    'auto_refresh': self.worker is not None and self.worker.is_alive(),
                    'next_refresh_at': self.next_attempt,
                    'stale': not self.updated or now - (self.source_updated or self.updated) > INTERVAL * 2,
                    'error': self.error, 'channels': channels}

    def start(self):
        if self.worker is not None:
            return
        def run():
            while not self.stop.is_set():
                delay = self.next_attempt - self.clock()
                if delay > 0:
                    self.stop.wait(min(delay, INTERVAL))
                    continue
                self.update()
        self.worker = threading.Thread(target=run, name='epg-refresh', daemon=True)
        self.worker.start()
