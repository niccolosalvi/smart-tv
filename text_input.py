"""Bounded YouTube search and direct video link input. Queries and links are never logged or persisted."""
import json
import re
import threading
import time
import unicodedata
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

MAX_QUERY = 128
MAX_URL = 512
_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
_YT_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be'}


def validate_query(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_QUERY or
            any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in value)):
        raise ValueError(f'Inserisci da 1 a {MAX_QUERY} caratteri, senza caratteri di controllo')
    return value


def youtube_search_payload(value):
    # q= is YouTube's living-room query parameter. Pass it through DIAL to
    # the installed app; no browser or character-grid navigation is involved.
    return urlencode({'launch': 'search', 'q': validate_query(value)}).encode('ascii')


def extract_youtube_video_id(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_URL or
            any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in value)):
        raise ValueError('Inserisci un link YouTube valido')
    raw = value.strip()
    if re.match(r'^(?:(?:www|m|music)\.)?(?:youtube\.com|youtu\.be)/', raw, re.I):
        raw = 'https://' + raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise ValueError('Inserisci un link YouTube valido') from None
    host = (parsed.hostname or '').lower()
    if parsed.scheme.lower() not in {'http', 'https'} or host not in _YT_HOSTS or parsed.username or parsed.password:
        raise ValueError('Inserisci un link YouTube valido')
    candidate = ''
    if host in {'youtu.be', 'www.youtu.be'}:
        parts = [p for p in parsed.path.split('/') if p]
        candidate = parts[0] if parts else ''
    else:
        qs = parse_qs(parsed.query)
        if qs.get('v'):
            candidate = qs['v'][0]
        else:
            parts = [p for p in parsed.path.split('/') if p]
            if len(parts) >= 2 and parts[0] in {'shorts', 'live', 'embed', 'v'}:
                candidate = parts[1]
    if not _VIDEO_ID.fullmatch(candidate):
        raise ValueError('Link YouTube non valido: ID video non trovato')
    return candidate


def youtube_video_payload(value):
    return urlencode({'v': extract_youtube_video_id(value)}).encode('ascii')


MAX_SEEK_SECONDS = 86400


def validate_youtube_seek(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            (isinstance(value, float) and not value.is_integer())):
        raise ValueError('Minutaggio non valido: inserisci un numero intero di secondi')
    seconds = int(value)
    if not 0 <= seconds <= MAX_SEEK_SECONDS:
        raise ValueError(f'Minutaggio fuori intervallo (0–{MAX_SEEK_SECONDS} secondi)')
    return seconds


def validate_youtube_video_id(value):
    if not isinstance(value, str) or not _VIDEO_ID.fullmatch(value):
        raise ValueError('ID video YouTube non valido')
    return value


def parse_youtube_storyboard(video_id, html):
    validate_youtube_video_id(video_id)
    dur_m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', html)
    title_m = re.search(r'"videoDetails"\s*:\s*\{[^}]*"title"\s*:\s*"((?:\\.|[^"\\])*)"', html)
    title = ''
    if title_m:
        try:
            title = json.loads('"' + title_m.group(1) + '"')
        except ValueError:
            title = ''
    duration = int(dur_m.group(1)) if dur_m else 0
    spec_m = re.search(r'"playerStoryboardSpecRenderer"\s*:\s*\{"spec"\s*:\s*"([^"]+)"', html)
    best = None
    if spec_m:
        spec = spec_m.group(1).replace('\\u0026', '&').replace('\\/', '/')
        parts = spec.split('|')
        base_tpl = parts[0]
        if base_tpl.startswith('https://i.ytimg.com/'):
            for lvl, entry in enumerate(parts[1:]):
                fields = entry.split('#')
                if len(fields) < 8 or '$' not in fields[7]:
                    continue
                try:
                    w, h, count, cols, rows, interval = (int(fields[i]) for i in range(6))
                except ValueError:
                    continue
                if min(w, h, count, cols, rows) <= 0:
                    continue
                name_tpl = fields[6]
                sig_key, sig_val = fields[7].split('$', 1)
                param_name = sig_key if sig_key else 'sigh'
                sep = '&' if '?' in base_tpl else '?'
                url_tpl = (
                    base_tpl.replace('$L', str(lvl)).replace('$N', name_tpl.replace('$M', '{M}'))
                    + f'{sep}{param_name}={sig_val}'
                )
                cand = {
                    'level': lvl,
                    'width': w,
                    'height': h,
                    'count': count,
                    'cols': cols,
                    'rows': rows,
                    'interval_ms': interval,
                    'sheet_url_template': url_tpl,
                }
                if '$M' in name_tpl or best is None:
                    best = cand
    return {
        'video_id': video_id,
        'title': title,
        'duration': duration,
        'poster_url': f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg',
        'storyboard': best,
    }


_STORYBOARD_CACHE = {}
_STORYBOARD_LOCK = threading.Lock()
_STORYBOARD_TTL = 1800.0


def fetch_youtube_storyboard(video_id):
    vid = validate_youtube_video_id(video_id)
    now = time.monotonic()
    with _STORYBOARD_LOCK:
        cached = _STORYBOARD_CACHE.get(vid)
        if cached and now - cached[0] < _STORYBOARD_TTL:
            return cached[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f'https://www.youtube.com/watch?v={vid}',
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'it-IT,it;q=0.9,en;q=0.8',
        },
    )
    with opener.open(req, timeout=5) as response:
        html = response.read(2_000_000).decode('utf-8', 'replace')
    data = parse_youtube_storyboard(vid, html)
    with _STORYBOARD_LOCK:
        if len(_STORYBOARD_CACHE) >= 16:
            oldest = min(_STORYBOARD_CACHE.items(), key=lambda item: item[1][0])[0]
            _STORYBOARD_CACHE.pop(oldest, None)
        _STORYBOARD_CACHE[vid] = (now, data)
    return data
