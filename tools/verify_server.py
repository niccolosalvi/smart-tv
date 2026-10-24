"""Read-only health and rejected-input checks. Never sends a valid TV command."""
import json
import re
import urllib.error
import urllib.request
import uuid
import os
from pathlib import Path

base = os.environ.get('SMART_TV_BASE_URL', 'http://127.0.0.1:8765')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(base, timeout=5) as response:
    page = response.read().decode()
    assert response.status == 200 and 'Salotto' in page
    key = re.search("const controlKey='([^']+)'", page)[1]
with opener.open(base + '/api/status', timeout=5) as response:
    status = json.load(response)
    assert isinstance(status['host'], str) and 0 < status['port'] < 65536
with opener.open(base + '/api/health', timeout=7) as response:
    health = json.load(response)
    assert health['status'] == 'ok' and health['session']
    assert health['server_uptime_seconds'] >= 0
    assert 'service' in health and 'refresh_timer' in health
checks = [('missing-control-key', {}, {'key': 'KEY_INFO'}, 403),
          ('invalid-key', {'X-Remote-Key': key}, {'key': 'KEY_FACTORY'}, 400),
          ('invalid-channel', {'X-Remote-Key': key}, {'channel': 'abc'}, 400),
          ('invalid-app', {'X-Remote-Key': key}, {'app': 'arbitrary'}, 400),
          ('invalid-app-type', {'X-Remote-Key': key}, {'app': []}, 400),
          ('invalid-context', {'X-Remote-Key': key}, {'context': 'invalid'}, 400),
          ('invalid-context-type', {'X-Remote-Key': key}, {'context': []}, 400),
          ('invalid-power', {'X-Remote-Key': key}, {'power': 'restart'}, 400),
          ('invalid-power-type', {'X-Remote-Key': key}, {'power': []}, 400),
          ('invalid-volume-step', {'X-Remote-Key': key}, {'volume': 100}, 400),
          ('invalid-volume-bool', {'X-Remote-Key': key}, {'volume': True}, 400),
          ('invalid-volume-type', {'X-Remote-Key': key}, {'volume': '2'}, 400),
          ('invalid-source', {'X-Remote-Key': key}, {'source': 'hdmi4'}, 400),
          ('invalid-source-type', {'X-Remote-Key': key}, {'source': []}, 400),
          ('mixed-actions', {'X-Remote-Key': key}, {'context': 'tv', 'key': 'KEY_INFO'}, 400),
          ('foreign-origin', {'X-Remote-Key': key, 'Origin': 'https://example.com'}, {'key': 'KEY_INFO'}, 403)]
results = []
for name in ('youtube', 'netflix', 'primevideo'):
    with opener.open(base + '/assets/' + name + '.png', timeout=5) as response:
        assert response.read().startswith(b'\x89PNG')
for name, headers, payload, expected in checks:
    correlation = str(uuid.uuid4())
    request = urllib.request.Request(base + '/api/command', data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'X-Request-ID': correlation, **headers})
    try:
        with opener.open(request, timeout=5) as response:
            actual = response.status
    except urllib.error.HTTPError as exc:
        actual = exc.code
        assert exc.headers.get('X-Request-ID') == correlation
        assert json.load(exc)['request_id'] == correlation
    assert actual == expected, (name, actual, expected)
    results.append({'test': name, 'status': actual, 'passed': True})
Path('reports').mkdir(exist_ok=True)
Path('reports/server-checks.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
try:
    opener.open(base + '/api/logs', timeout=5)
    raise AssertionError('Log endpoint requires the browser key')
except urllib.error.HTTPError as exc:
    assert exc.code == 403
request = urllib.request.Request(base + '/api/logs', headers={'X-Remote-Key': key})
with opener.open(request, timeout=5) as response:
    logs = json.load(response)
assert len(logs['events']) <= 300 and logs['epoch']
assert key not in json.dumps(logs)
print(f'HTTP/status/log API OK; {len(checks)} input checks and request IDs passed. No TV commands sent.')
