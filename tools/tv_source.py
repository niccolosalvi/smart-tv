import json
from remote import Remote

if __name__ == '__main__':
    remote = Remote()
    try:
        print(json.dumps(remote.switch_to_tv()), flush=True)
    finally:
        remote.close()
