"""Explicit app launch over the TV REST endpoint, then read its state."""
import argparse
import json
import time
import urllib.request
from remote import APPS, HOST

parser = argparse.ArgumentParser()
parser.add_argument('app', choices=APPS)
args = parser.parse_args()
app = APPS[args.app]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
url = f'http://{HOST}:8001/api/v2/applications/{app["id"]}'
try:
    with opener.open(urllib.request.Request(url, data=b'', method='POST'), timeout=6) as response:
        print(json.dumps({'app': args.app, 'launch_http': response.status}), flush=True)
    time.sleep(3)
    with opener.open(url, timeout=3) as response:
        data = json.load(response)
    print(json.dumps({'app': args.app, **{k: data.get(k) for k in ('name', 'running', 'visible')}}), flush=True)
except Exception as exc:
    print(json.dumps({'error_type': type(exc).__name__}), flush=True)
