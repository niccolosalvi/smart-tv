"""Read firmware metadata and open settings. Does not start an update."""
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from remote import HOST, Remote

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(f'http://{HOST}:8001/api/v2/', timeout=5) as response:
    device = json.load(response).get('device', {})
report = {
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'model': device.get('modelName'),
    'installed_firmware_api': device.get('firmwareVersion'),
    'status': 'Awaiting installed version from TV settings; update not started',
}
remote = Remote()
try:
    report['settings_command'] = remote.send(['KEY_MENU'])
finally:
    remote.close()
Path('reports').mkdir(exist_ok=True)
Path('reports/firmware-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
