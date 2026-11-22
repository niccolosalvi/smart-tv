"""Independent iPhone TLS trust, fixed-origin WSS and a certificate-only profile."""
import asyncio
import json
import os
import plistlib
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from screen_share import ShareRelay
from ios_setup import load_profile, setup_page


class ProfileTests(unittest.TestCase):
    def test_profile_accepts_only_one_root_certificate(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profile.mobileconfig'
            root={'PayloadType':'com.apple.security.root','PayloadDisplayName':'Local <CA>','PayloadContent':b'public certificate'}
            path.write_bytes(plistlib.dumps({'PayloadType':'Configuration','PayloadContent':[root]}))
            content,name,fingerprint=load_profile(path)
            self.assertEqual(plistlib.loads(content)['PayloadContent'],[root])
            page=setup_page(name,fingerprint,'https://127.0.0.1:8769/share')
            self.assertIn('Local &lt;CA&gt;',page)
            path.write_bytes(plistlib.dumps({'PayloadType':'Configuration','PayloadContent':[root,{'PayloadType':'com.apple.vpn.managed'}]}))
            with self.assertRaises(ValueError):load_profile(path)


@unittest.skipUnless(shutil.which('openssl'),'OpenSSL required for TLS certificate tests')
class IPhoneTLSTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        root=Path(self.folder.name)
        def openssl(*args):subprocess.run(['openssl',*args],check=True,capture_output=True)
        for name in ('pc','ca'):
            openssl('req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','2',
                    '-keyout',str(root/(name+'.key')),'-out',str(root/(name+'.crt')),'-subj','/CN='+name,
                    '-addext','subjectAltName=IP:127.0.0.1','-addext','basicConstraints=critical,CA:TRUE',
                    '-addext','keyUsage=critical,digitalSignature,keyCertSign,cRLSign')
        openssl('req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes',
                '-keyout',str(root/'phone.key'),'-out',str(root/'phone.csr'),'-subj','/CN=phone')
        (root/'leaf.ext').write_text('subjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n')
        openssl('x509','-req','-in',str(root/'phone.csr'),'-CA',str(root/'ca.crt'),'-CAkey',str(root/'ca.key'),
                '-CAcreateserial','-days','1','-out',str(root/'phone.crt'),'-extfile',str(root/'leaf.ext'))
        pc_tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);pc_tls.load_cert_chain(root/'pc.crt',root/'pc.key')
        phone_tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);phone_tls.load_cert_chain(root/'phone.crt',root/'phone.key')
        self.pc_trust=ssl.create_default_context(cafile=str(root/'pc.crt'))
        self.phone_trust=ssl.create_default_context(cafile=str(root/'ca.crt'))
        with patch.dict(os.environ,{'LOCALAPPDATA':str(root)}):import server
        self.app=server
        self.pc=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.phone=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.bootstrap=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        der=ssl.PEM_cert_to_DER_cert((root/'ca.crt').read_text())
        profile_path=root/'iphone.mobileconfig'
        profile_path.write_bytes(plistlib.dumps({'PayloadType':'Configuration','PayloadContent':[
            {'PayloadType':'com.apple.security.root','PayloadDisplayName':'Local CA','PayloadContent':der}]}))
        profile=load_profile(profile_path)
        for http,tls in ((self.pc,pc_tls),(self.phone,phone_tls),(self.bootstrap,None)):
            http.daemon_threads=True;http.http_port=8765;http.ios_https_port=self.phone.server_port;http.ios_profile=profile
            http.https_port=http.server_port if tls else self.pc.server_port
            if tls:http.scheme='https';http.socket=tls.wrap_socket(http.socket,server_side=True)
        self.relay=ShareRelay('key',https_port=self.pc.server_port)
        self.relay.start('127.0.0.1',0,pc_tls,0,phone_tls,0,self.phone.server_port)
        self.addCleanup(self.relay.stop)
        self.phone.wss_port=self.relay.extra_secure_port
        self.override=patch.object(server,'sharing',self.relay);self.override.start();self.addCleanup(self.override.stop)
        self.threads=[]
        for http in (self.pc,self.phone,self.bootstrap):
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();self.threads.append(thread)
        self.addCleanup(self.close_servers)
        self.pc_url=f'https://127.0.0.1:{self.pc.server_port}'
        self.phone_url=f'https://127.0.0.1:{self.phone.server_port}'

    def close_servers(self):
        for http in (self.pc,self.phone,self.bootstrap):http.shutdown();http.server_close()
        for thread in self.threads:thread.join()

    def get(self,url,trust=None,headers=None):
        handlers=[urllib.request.ProxyHandler({})]
        if trust:handlers.append(urllib.request.HTTPSHandler(context=trust))
        return urllib.request.build_opener(*handlers).open(urllib.request.Request(url,headers=headers or {}),timeout=3)

    def test_independent_certificates_keep_pc_trust_and_phone_origin(self):
        for url,trust,port in ((self.pc_url,self.pc_trust,self.relay.secure_port),
                               (self.phone_url,self.phone_trust,self.relay.extra_secure_port)):
            with self.get(url+'/api/share',trust) as response:settings=json.load(response)
            self.assertEqual(settings['https_url'],url+'/share')
            self.assertEqual(settings['ws_url'],f'wss://127.0.0.1:{port}/screen')
            with self.get(url+'/share',trust) as response:self.assertIn(f'wss://127.0.0.1:{port}',response.headers['Content-Security-Policy'])
        with self.assertRaises(urllib.error.URLError):self.get(self.phone_url,self.pc_trust)
        with self.assertRaises(urllib.error.URLError):self.get(self.pc_url,self.phone_trust)

    def test_only_remote_panel_can_be_embedded_by_same_origin(self):
        for path in ('/', '/share', '/remote-panel'):
            with self.get(self.phone_url+path,self.phone_trust) as response:
                panel=path=='/remote-panel'
                self.assertEqual(response.headers['X-Frame-Options'],'SAMEORIGIN' if panel else 'DENY')
                self.assertIn("frame-ancestors "+("'self'" if panel else "'none'"),
                              response.headers['Content-Security-Policy'])
                if panel:self.assertIn('id="view-select"',response.read().decode())

    def test_bootstrap_serves_public_profile_and_selects_iphone_entry(self):
        base=f'http://127.0.0.1:{self.bootstrap.server_port}'
        with self.get(base+'/setup/iphone.mobileconfig') as response:
            self.assertTrue(response.headers['Content-Type'].startswith('application/x-apple-aspen-config'))
            self.assertEqual(len(plistlib.loads(response.read())['PayloadContent']),1)
        with self.get(base+'/setup/iphone') as response:self.assertIn(self.phone_url+'/share',response.read().decode())
        with self.get(base+'/api/share',headers={'User-Agent':'iPhone'}) as response:settings=json.load(response)
        self.assertEqual(settings['https_url'],self.phone_url+'/share')
        with self.get(base+'/api/share',headers={'User-Agent':'Windows'}) as response:settings=json.load(response)
        self.assertEqual(settings['https_url'],self.pc_url+'/share')

    def test_iphone_wss_reaches_tv_and_rejects_other_https_origin(self):
        async def exercise():
            uri=f'wss://127.0.0.1:{self.relay.extra_secure_port}/screen'
            async with connect(uri,ssl=self.phone_trust,origin=self.pc_url,proxy=None) as bad:
                with self.assertRaises(ConnectionClosed):await asyncio.wait_for(bad.recv(),2)
            async with connect(f'ws://127.0.0.1:{self.relay.port}/screen',origin='http://127.0.0.1:8765',proxy=None) as viewer:
                await viewer.send('{"role":"viewer","key":"key"}');await viewer.recv()
                async with connect(uri,ssl=self.phone_trust,origin=self.phone_url,proxy=None) as sender:
                    await sender.send('{"role":"sender","key":"key"}');await sender.recv()
                    frame=b'\xff\xd8phone\xff\xd9';await sender.send(frame)
                    self.assertEqual(await asyncio.wait_for(viewer.recv(),2),frame)
                    await viewer.send('ack');self.assertEqual(json.loads(await sender.recv())['type'],'ack')
        asyncio.run(exercise())
