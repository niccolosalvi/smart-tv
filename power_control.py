"""Power controls. Wake configuration is stored outside the checkout."""
import json
import re
import socket
import time
import urllib.request
from remote import HOST, STATE, record
from diagnostics import timeline

MAC_FILE = STATE / 'wake-config.json'

def normalize_mac(value):
    compact = str(value).replace(':', '').replace('-', '')
    if not re.fullmatch(r'[0-9a-fA-F]{12}', compact) or int(compact, 16) == 0:
        raise ValueError('Indirizzo MAC non valido')
    return compact.lower()

def device_info(timeout=2):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://{HOST}:8001/api/v2/', timeout=timeout) as response:
        return json.load(response).get('device', {})

def prepare_wake():
    data = device_info()
    mac = normalize_mac(data.get('wifiMac'))
    MAC_FILE.write_text(json.dumps({'host': HOST, 'mac': mac}), encoding='utf-8')
    return {'configured': True, 'network_type': data.get('networkType')}

def wake_mac():
    try:
        saved = json.loads(MAC_FILE.read_text(encoding='utf-8'))
        if saved.get('host') == HOST:
            return normalize_mac(saved['mac'])
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        pass
    prepare_wake()
    return wake_mac()

def magic_packet(mac):
    return b'\xff' * 6 + bytes.fromhex(normalize_mac(mac)) * 16

def wake():
    started = time.perf_counter()
    try:
        packet = magic_packet(wake_mac())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((HOST, 9))
            local_ip = route.getsockname()[0]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((local_ip, 0))
            for _ in range(3):
                sock.sendto(packet, ('255.255.255.255', 9))
                time.sleep(0.15)
        timeline.emit('Server → TV', 'Wake-on-LAN: 3 pacchetti UDP inviati; nessuna risposta/ACK prevista')
        return record('power:on', started, 'sent',
            note='Pacchetti di risveglio inviati; accensione non confermata')
    except Exception as exc:
        record('power:on', started, 'error', error_type=type(exc).__name__)
        raise RuntimeError(type(exc).__name__) from None

def power_off(remote):
    # Cache the wake address while the TV is still reachable.
    try:
        prepare_wake()
    except Exception:
        pass
    with remote.lock:
        if not remote.tv.is_alive():
            remote.connect()
        started = time.perf_counter()
        try:
            time.sleep(1)
            timeline.emit('Server → TV', 'WS Press KEY_POWER · 200 ms')
            remote.tv.send_key('KEY_POWER', cmd='Press', key_press_delay=0.2)
            timeline.emit('Server → TV', 'WS Release KEY_POWER')
            remote.tv.send_key('KEY_POWER', cmd='Release', key_press_delay=0.2)
            return record('power:off', started, 'sent', key='KEY_POWER',
                method='short_press_release',
                note='Spegnimento richiesto; standby da verificare sullo schermo')
        except Exception as exc:
            record('power:off', started, 'error', error_type=type(exc).__name__)
            raise RuntimeError(type(exc).__name__) from None
        finally:
            remote.close()
