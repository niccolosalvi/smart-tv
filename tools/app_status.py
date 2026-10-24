"""Query app state without launching, closing or installing apps."""
import json
import urllib.request
from remote import APPS, HOST

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for key, app in APPS.items():
    try:
        with opener.open(f'http://{HOST}:8001/api/v2/applications/{app["id"]}', timeout=3) as response:
            data = json.load(response)
        print(json.dumps({'app': key, **{k: data.get(k) for k in ('id', 'name', 'running', 'visible')} }), flush=True)
    except Exception as exc:
        print(json.dumps({'app': key, 'error_type': type(exc).__name__}), flush=True)
