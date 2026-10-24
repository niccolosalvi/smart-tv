"""Read installed app metadata; do not start or stop any app."""
import json
from remote import Remote

remote = Remote(timeout=6)
try:
    remote.connect()
    apps = remote.tv.app_list()
    if apps:
        filtered = [{k: a.get(k) for k in ('name', 'appId', 'app_id', 'id', 'app_type')} for a in apps
                    if any(s in str(a.get('name', '')).lower() for s in ('youtube', 'netflix', 'prime', 'amazon'))]
        print(json.dumps({'apps': filtered}, ensure_ascii=False), flush=True)
    else:
        print('App list not returned', flush=True)
except Exception as exc:
    print(json.dumps({'error_type': type(exc).__name__}), flush=True)
finally:
    remote.close()
