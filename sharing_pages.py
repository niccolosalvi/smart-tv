"""Render sharing pages for the LAN server and the PC localhost launcher."""
import json
from pathlib import Path

WEB = Path(__file__).parent / 'web'


def render_share(name, config, nonce):
    settings = json.dumps(config).replace('<', '\\u003c')
    return (WEB.joinpath(name + '.html').read_text(encoding='utf-8')
            .replace('__CONFIG__', settings).replace('__CSRF__', nonce))
