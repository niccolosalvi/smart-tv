"""HTTPS commands and a WSS sender share the HTTP TV receiver's relay."""
import json
import asyncio
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from screen_share import ShareRelay
from websockets.sync.client import connect
from websockets.asyncio.client import connect as async_connect


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for ephemeral TLS test certificate')
class HTTPSSharingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        cert,key=root/'cert.pem',root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-keyout',str(key),'-out',str(cert),'-subj','/CN=localhost',
            '-addext','subjectAltName=IP:127.0.0.1'],check=True,capture_output=True)
        with patch.dict(os.environ,{'LOCALAPPDATA':self.tmp.name}):
            import server
        self.app=server
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert,key)
        self.client_tls=ssl.create_default_context(cafile=str(cert))
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.http.daemon_threads=True
        self.http.scheme='https'
        self.http.http_port=8765
        self.http.https_port=self.http.server_port
        self.http.socket=tls.wrap_socket(self.http.socket,server_side=True)
        self.relay=ShareRelay(server.CSRF,http_port=8765,https_port=self.http.server_port)
        self.relay.start('127.0.0.1',0,tls,0)
        self.addCleanup(self.relay.stop)
        override=patch.object(server,'sharing',self.relay)
        override.start();self.addCleanup(override.stop)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close_http)
        self.base=f'https://127.0.0.1:{self.http.server_port}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=self.client_tls))

    def close_http(self):
        self.http.shutdown();self.http.server_close();self.thread.join()

    def test_https_page_uses_wss_and_tv_remains_on_http(self):
        with self.opener.open(self.base+'/api/share',timeout=3) as r:config=json.load(r)
        self.assertEqual(config['server'],self.base)
        self.assertEqual(config['receiver_url'],'http://127.0.0.1:8765/screen')
        self.assertEqual(config['ws_url'],f'wss://127.0.0.1:{self.relay.secure_port}/screen')
        with self.opener.open(self.base+'/share',timeout=3) as r:
            self.assertIn(f'wss://127.0.0.1:{self.relay.secure_port}',r.headers['Content-Security-Policy'])
        with self.opener.open(self.base,timeout=3) as r:
            self.assertIn(self.base+'/share',r.read().decode())

    def test_https_control_accepts_same_origin_and_targets_http_receiver(self):
        transport=Mock()
        transport.open_screen.return_value={'status':'sent'}
        request=urllib.request.Request(self.base+'/api/command',data=b'{"screen":"open"}',
            headers={'Content-Type':'application/json','Origin':self.base,'X-Remote-Key':self.app.CSRF})
        with patch.object(self.app,'remote',transport), \
                patch.object(self.app.Handler,'screen_url',return_value='http://198.51.100.20:8765/screen'):
            with self.opener.open(request,timeout=3) as r:self.assertEqual(r.status,200)
        transport.open_screen.assert_called_once_with('http://198.51.100.20:8765/screen')

    def test_wss_capture_sender_reaches_plain_ws_tv_receiver(self):
        async def exercise():
            async with async_connect(f'ws://127.0.0.1:{self.relay.port}/screen',origin='http://127.0.0.1:8765',proxy=None) as viewer:
                await viewer.send(json.dumps({'role':'viewer','key':self.app.CSRF}))
                self.assertEqual(json.loads(await asyncio.wait_for(viewer.recv(),2))['type'],'ready')
                async with async_connect(f'wss://127.0.0.1:{self.relay.secure_port}/screen',ssl=self.client_tls,
                                         origin=self.base,proxy=None) as sender:
                    await sender.send(json.dumps({'role':'sender','key':self.app.CSRF}))
                    self.assertEqual(json.loads(await asyncio.wait_for(sender.recv(),2))['type'],'ready')
                    frame=b'\xff\xd8frame\xff\xd9'
                    await sender.send(frame)
                    self.assertEqual(await asyncio.wait_for(viewer.recv(),2),frame)
                    await viewer.send('ack')
                    self.assertEqual(json.loads(await asyncio.wait_for(sender.recv(),2))['type'],'ack')
        asyncio.run(exercise())
