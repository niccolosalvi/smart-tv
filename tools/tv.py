import argparse
import json
import time
from remote import Remote, channel_keys

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['connect', 'volume', 'channels', 'info'])
args = parser.parse_args()
tv = Remote(timeout=45)
try:
    print(json.dumps(tv.connect()), flush=True)
    if args.action == 'volume':
        print(json.dumps(tv.send(['KEY_VOLUP'])), flush=True)
    if args.action == 'info':
        time.sleep(1)
        print(json.dumps(tv.send(['KEY_INFO'])), flush=True)
        time.sleep(2)
    if args.action == 'channels':
        print(json.dumps(tv.send(['KEY_CHUP'])), flush=True)
        time.sleep(2)
        print(json.dumps(tv.send(channel_keys(1), 'channel:1')), flush=True)
except Exception as exc:
    print(json.dumps({'error_type': str(exc)}), flush=True)
    raise SystemExit(1)
finally:
    tv.close()
