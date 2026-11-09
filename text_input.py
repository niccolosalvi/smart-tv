"""Bounded YouTube search input. Queries are never logged or persisted."""
import unicodedata
from urllib.parse import urlencode

MAX_QUERY = 128


def validate_query(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_QUERY or
            any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in value)):
        raise ValueError(f'Inserisci da 1 a {MAX_QUERY} caratteri, senza caratteri di controllo')
    return value


def youtube_search_payload(value):
    # q= is YouTube's living-room query parameter. Pass it through DIAL to
    # the installed app; no browser or character-grid navigation is involved.
    return urlencode({'launch': 'search', 'q': validate_query(value)}).encode('ascii')
