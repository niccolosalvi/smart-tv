"""Read-only UPnP descriptor checks, no playback or source commands."""
import json
import socket
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from remote import HOST

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results = []
for path in ('/dmr', '/smp_2_'):
    item = {'port': 9197, 'path': path}
    try:
        with opener.open(f'http://{HOST}:9197{path}', timeout=3) as response:
            root = ET.fromstring(response.read())
        services = []
        for service in root.findall('.//{*}service'):
            services.append({k: service.findtext('{*}' + k) for k in
                             ('serviceType', 'controlURL', 'SCPDURL')})
        item['services'] = services
    except Exception as exc:
        item['error_type'] = type(exc).__name__
    results.append(item)
try:
    with opener.open(f'http://{HOST}:9197/AVTransport_1.xml', timeout=3) as response:
        root = ET.fromstring(response.read())
    actions = [a.findtext('{*}name') for a in root.findall('.//{*}action')]
    results.append({'service': 'AVTransport', 'actions': actions})
    for action in ('GetTransportInfo', 'GetMediaInfo', 'GetPositionInfo'):
        if action not in actions:
            continue
        service = 'urn:schemas-upnp-org:service:AVTransport:1'
        body = f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:{action} xmlns:u="{service}"><InstanceID>0</InstanceID></u:{action}></s:Body></s:Envelope>'
        req = urllib.request.Request(f'http://{HOST}:9197/upnp/control/AVTransport1',
            data=body.encode(), headers={'Content-Type': 'text/xml; charset="utf-8"', 'SOAPAction': f'"{service}#{action}"'})
        try:
            with opener.open(req, timeout=3) as response:
                root = ET.fromstring(response.read())
            fields = {e.tag.split('}')[-1]: e.text for e in root.iter() if len(e) == 0}
            results.append({'action': action, 'fields': fields})
        except Exception as exc:
            results.append({'action': action, 'error_type': type(exc).__name__})
except Exception as exc:
    results.append({'service': 'AVTransport', 'error_type': type(exc).__name__})

# Standard SSDP discovery; inspect only replies from the identified TV.
try:
    locations = set()
    message = f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 1\r\nST: ssdp:all\r\n\r\n'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.5)
        sock.sendto(message.encode(), ('239.255.255.250', 1900))
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            try:
                payload, address = sock.recvfrom(65535)
                if address[0] != HOST:
                    continue
                for line in payload.decode(errors='replace').splitlines():
                    if line.lower().startswith('location:'):
                        locations.add(line.split(':', 1)[1].strip())
            except socket.timeout:
                pass
    from urllib.parse import urlsplit
    for location in sorted(locations):
        if urlsplit(location).hostname != HOST:
            continue
        try:
            with opener.open(location, timeout=3) as response:
                root = ET.fromstring(response.read())
            results.append({'descriptor': location, 'services': [
                {k: s.findtext('{*}' + k) for k in ('serviceType','controlURL','SCPDURL')}
                for s in root.findall('.//{*}service')]})
        except Exception as exc:
            results.append({'descriptor': location, 'error_type': type(exc).__name__})
    if not locations:
        results.append({'ssdp': 'No TV discovery replies in 3 seconds'})
except Exception as exc:
    results.append({'ssdp_error_type': type(exc).__name__})
Path('reports').mkdir(exist_ok=True)
Path('reports/playback-discovery.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
