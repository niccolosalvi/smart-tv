import json
import socket
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from remote import HOST

def main():
    results = []
    for port in (8001, 8002):
        started = time.perf_counter()
        item = {'test': 'tcp', 'host': HOST, 'port': port}
        try:
            with socket.create_connection((item['host'], port), 4):
                pass
            item['ok'] = True
        except Exception as exc:
            item['error'] = str(exc)
        item['elapsed_ms'] = round((time.perf_counter()-started)*1000, 2)
        results.append(item)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://{HOST}:8001/api/v2/', timeout=6) as response:
            data = json.load(response)
        device = data.get('device', {})
        results.append({'test': 'identity', **{key: device.get(key) for key in
            ('name', 'modelName', 'ip', 'TokenAuthSupport', 'OS', 'type')}})
    except Exception as exc:
        results.append({'test': 'identity', 'error': str(exc)})
    report = {'timestamp': datetime.now(timezone.utc).isoformat(), 'results': results}
    Path('reports').mkdir(exist_ok=True)
    Path('reports/network.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
