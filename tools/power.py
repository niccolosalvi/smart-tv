"""Explicit hardware test; never runs on import."""
import argparse
import json
from power_control import device_info, power_off, prepare_wake, wake
from remote import Remote

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'off', 'on', 'probe', 'press'])
    args = parser.parse_args()
    if args.action == 'prepare':
        print(json.dumps(prepare_wake()), flush=True)
    elif args.action == 'off':
        print(json.dumps(power_off(Remote())), flush=True)
    elif args.action == 'on':
        print(json.dumps(wake()), flush=True)
    elif args.action == 'press':
        import time
        import websocket
        remote = Remote()
        try:
            remote.connect()
            time.sleep(1)
            remote.tv.send_key('KEY_POWER', cmd='Press', key_press_delay=0.2)
            remote.tv.send_key('KEY_POWER', cmd='Release', key_press_delay=0.2)
            print('Short Press/Release sent', flush=True)
            remote.tv.connection.settimeout(0.5)
            end = time.monotonic()+2
            while time.monotonic()<end:
                try:
                    raw = remote.tv.connection.recv()
                    if not raw:
                        print('TV closed socket', flush=True)
                        break
                    response = json.loads(raw)
                    data = response.get('data')
                    print(json.dumps({'event':response.get('event'),
                        'message':data.get('message') if isinstance(data,dict) else None}), flush=True)
                except websocket.WebSocketTimeoutException:
                    pass
        finally:
            remote.close()
    else:
        try:
            info = device_info()
            print(json.dumps({'api_reachable': True, 'model': info.get('modelName')}), flush=True)
        except Exception as exc:
            print(json.dumps({'api_reachable': False, 'error_type': type(exc).__name__}), flush=True)
