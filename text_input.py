"""Bounded YouTube search and direct video link input. Queries and links are never logged or persisted."""
import re
import unicodedata
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
