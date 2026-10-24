"""Single harmless key, then inspect TV events without exposing identity/tokens."""
import json
import sys
import time
import websocket
from remote import Remote, record

tv = Remote(timeout=8)
tv.tv.port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
try:
    print(json.dumps(tv.connect()), flush=True)
    print(json.dumps(tv.send(['KEY_VOLUP'])), flush=True)
    end = time.monotonic() + 3
    tv.tv.connection.settimeout(1)
    while time.monotonic() < end:
        try:
            raw = tv.tv.connection.recv()
            if not raw:
                print('Connection closed by TV', flush=True)
                break
            data = json.loads(raw)
            # Only known protocol fields, never the complete response.
            safe = {'event': data.get('event'), 'message': data.get('data', {}).get('message') if isinstance(data.get('data'), dict) else None}
            print(json.dumps(safe), flush=True)
            record('tv_event', time.perf_counter(), 'received', **safe)
        except websocket.WebSocketTimeoutException:
            pass
except Exception as exc:
    print(json.dumps({'error_type': type(exc).__name__}), flush=True)
finally:
    tv.close()
