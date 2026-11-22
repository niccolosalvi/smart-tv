"""Public, certificate-only iPhone setup page; private keys are never served."""
import hashlib
import html
import plistlib
from pathlib import Path


def load_profile(path):
    content = Path(path).read_bytes()
    if len(content) > 32768:
        raise ValueError('Profilo iPhone troppo grande')
    profile = plistlib.loads(content)
    payloads = profile.get('PayloadContent', [])
    if (profile.get('PayloadType') != 'Configuration' or len(payloads) != 1 or
            payloads[0].get('PayloadType') != 'com.apple.security.root' or
            not isinstance(payloads[0].get('PayloadContent'), bytes)):
        raise ValueError('Il profilo deve contenere solo un certificato radice')
    cert = payloads[0]['PayloadContent']
    fingerprint = ':'.join(f'{b:02X}' for b in hashlib.sha256(cert).digest())
    return content, str(payloads[0].get('PayloadDisplayName', 'Smart TV Locale iPhone CA')), fingerprint


def setup_page(name, fingerprint, secure_url):
    return '''<!doctype html><html lang="it"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Configura iPhone · Smart TV Locale</title>
<style>body{font:18px system-ui;max-width:680px;margin:24px auto;padding:0 20px;line-height:1.6}code{overflow-wrap:anywhere}li{margin:16px 0}</style>
<h1>Accesso sicuro da iPhone</h1>
<p>Questo profilo contiene solo il certificato radice <strong>'''+html.escape(name)+'''</strong>.
Non configura VPN, gestione remota, password o altri servizi.</p>
<p>Confronta questa impronta SHA-256 con quella fornita nella chat:</p><code>'''+html.escape(fingerprint)+'''</code>
<ol><li>Apri in Safari <a href="/setup/iphone.mobileconfig">Scarica il profilo del certificato</a> e consenti il download.</li>
<li>Apri Impostazioni → Generali → VPN e gestione dispositivo, scegli il profilo scaricato e premi Installa. Conferma sul tuo iPhone.</li>
<li>Vai in Impostazioni → Generali → Info → Impostazioni attendibilità certificati e abilita la fiducia completa per <strong>'''+html.escape(name)+'''</strong>.</li>
<li>Torna in Safari e apri <a href="'''+html.escape(secure_url, quote=True)+'''">la condivisione sicura per iPhone</a>.</li></ol>
<p>Il profilo è locale e non firmato: iOS chiede conferma durante l’installazione. Puoi rimuoverlo da VPN e gestione dispositivo.
La chiave privata rimane sul Raspberry.</p></html>'''
