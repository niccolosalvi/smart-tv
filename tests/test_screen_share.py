"""Real loopback sockets: authentication, flow control and viewer recovery."""
import argparse
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed

from screen_share import MAX_FRAME, ShareRelay
from tools.share_screen import make_handler, server_url

JPEG = b'\xff\xd8test-image\xff\xd9'


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.relay = ShareRelay('test-key', ack_timeout=0.3)
        self.relay.start('127.0.0.1', 0)
        self.addCleanup(self.relay.stop)
        self.url = f'ws://127.0.0.1:{self.relay.port}/screen'

    def peer(self, role, key='test-key', origin='http://127.0.0.1:8765'):
        peer = connect(self.url, origin=origin, proxy=None, compression=None, close_timeout=1)
        self.addCleanup(peer.close)
        peer.send(json.dumps({'key': key, 'role': role}))
        return peer

    def ready_peer(self, role):
        peer = self.peer(role)
        self.assertEqual(json.loads(peer.recv(timeout=1)), {'type': 'ready'})
        return peer

    def test_binary_frame_waits_for_display_ack_and_stop_clears_receiver(self):
        viewer, sender = self.ready_peer('viewer'), self.ready_peer('sender')
        sender.send(JPEG)
        self.assertEqual(viewer.recv(timeout=1), JPEG)
        with self.assertRaises(TimeoutError):
            sender.recv(timeout=0.05)
        viewer.send('ack')
        self.assertEqual(json.loads(sender.recv(timeout=1))['type'], 'ack')
        sender.close()
        self.assertEqual(viewer.recv(timeout=1), 'stop')

    def test_wrong_key_and_foreign_origin_are_rejected(self):
        for key, origin in [('wrong', 'http://127.0.0.1:8765'),
                            ('test-key', 'http://evil.example')]:
            with self.subTest(key=key, origin=origin):
                with self.assertRaises(ConnectionClosed) as result:
                    peer = self.peer('sender', key, origin)
                    peer.recv(timeout=1)
                self.assertEqual(result.exception.rcvd.code, 1008)

    def test_localhost_capture_page_is_accepted(self):
        peer = self.peer('sender', origin='http://127.0.0.1:42123')
        self.assertEqual(json.loads(peer.recv(timeout=1))['type'], 'ready')

    def test_second_sender_or_viewer_is_rejected(self):
        for role in ('sender', 'viewer'):
            self.ready_peer(role)
            duplicate = self.peer(role)
            with self.assertRaises(ConnectionClosed):
                duplicate.recv(timeout=1)

    def test_absent_viewer_and_slow_viewer_do_not_stall_sender(self):
        sender = self.ready_peer('sender')
        sender.send(JPEG)
        self.assertEqual(json.loads(sender.recv(timeout=1))['type'], 'waiting')
        viewer = self.ready_peer('viewer')
        sender.send(JPEG)
        self.assertEqual(viewer.recv(timeout=1), JPEG)
        # Deliberately no ACK; the relay drops the slow receiver.
        self.assertEqual(json.loads(sender.recv(timeout=2))['type'], 'waiting')
        with self.assertRaises(ConnectionClosed):
            viewer.recv(timeout=1)
        replacement = self.ready_peer('viewer')
        sender.send(JPEG)
        self.assertEqual(replacement.recv(timeout=1), JPEG)
        replacement.send('ack')
        self.assertEqual(json.loads(sender.recv(timeout=1))['type'], 'ack')

    def test_viewer_disconnect_releases_pending_frame(self):
        sender, viewer = self.ready_peer('sender'), self.ready_peer('viewer')
        sender.send(JPEG)
        viewer.recv(timeout=1)
        start = time.monotonic()
        viewer.close()
        self.assertEqual(json.loads(sender.recv(timeout=1))['type'], 'waiting')
        self.assertLess(time.monotonic()-start, 0.3)

    def test_invalid_or_oversized_frame_closes_sender(self):
        for data, expected in [(b'not-jpeg', 1008),
                               (b'\xff\xd8' + b'x'*MAX_FRAME + b'\xff\xd9', 1009)]:
            with self.subTest(expected=expected):
                sender = self.ready_peer('sender')
                sender.send(data)
                with self.assertRaises(ConnectionClosed) as result:
                    sender.recv(timeout=1)
                self.assertEqual(result.exception.rcvd.code, expected)

    def test_launcher_rejects_non_lan_url_shapes(self):
        self.assertEqual(server_url('http://192.168.1.20:8765/'), 'http://192.168.1.20:8765')
        for url in ['https://192.168.1.20:8765', 'http://evil.example:8765',
                    'http://user:pass@127.0.0.1:8765', 'http://127.0.0.1:8765/path']:
            with self.subTest(url=url), self.assertRaises(argparse.ArgumentTypeError):
                server_url(url)


class SharingHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'LOCALAPPDATA': cls.runtime.name}):
            import server
        cls.app = server
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.http.daemon_threads = True
        cls.relay = ShareRelay(server.CSRF, cls.http.server_port)
        cls.relay.start('127.0.0.1', 0)
        cls.override = patch.object(server, 'sharing', cls.relay)
        cls.override.start()
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.relay.stop()
        cls.override.stop()
        cls.runtime.cleanup()

    def test_config_and_pages_publish_active_port_and_allow_required_resources(self):
        with self.opener.open(self.base+'/api/share', timeout=2) as response:
            config = json.load(response)
        self.assertTrue(config['available'])
        self.assertEqual(config['port'], self.relay.port)
        for path in ['/share', '/screen']:
            with self.subTest(path=path), self.opener.open(self.base+path, timeout=2) as response:
                page = response.read().decode()
                self.assertIn(json.dumps(config), page)
                self.assertNotIn('__CONFIG__', page)
                self.assertNotIn('__CSRF__', page)
                csp = response.headers['Content-Security-Policy']
                self.assertIn(f'ws://127.0.0.1:{self.relay.port}', csp)
                self.assertIn("img-src 'self' blob: data:", csp)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_receiver_works_with_same_origin_script_policy_without_nonce_support(self):
        with self.opener.open(self.base+'/screen', timeout=2) as response:
            page = response.read().decode()
            self.assertIn('<script src="/assets/screen.js"></script>', page)
            self.assertIn('type="application/json" id="screen-config"', page)
            self.assertIn("script-src 'self';", response.headers['Content-Security-Policy'])
            self.assertNotIn('nonce-', response.headers['Content-Security-Policy'])
        with self.opener.open(self.base+'/assets/screen.js', timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), 'application/javascript')
            script = response.read().decode()
            self.assertIn("getElementById('screen-config')", script)
            self.assertNotIn(self.app.CSRF, script)

    def test_launcher_serves_only_loopback_host_and_root(self):
        http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler('test-page', 'nonce', 'ws://127.0.0.1:8767'))
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{http.server_port}'
            with self.opener.open(base, timeout=2) as response:
                self.assertEqual(response.read(), b'test-page')
            for path, headers, status in [('/', {'Host': 'foreign.example'}, 403), ('/unknown', {}, 404)]:
                request = urllib.request.Request(base+path, headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as result:
                    self.opener.open(request, timeout=2)
                self.assertEqual(result.exception.code, status)
                result.exception.close()
        finally:
            http.shutdown()
            http.server_close()
            thread.join()

    def test_launcher_forwards_only_authenticated_screen_action(self):
        config = {'server': self.base, 'key': self.app.CSRF}
        http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler('', 'nonce', 'ws://127.0.0.1:8767', config))
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        transport = Mock()
        transport.open_screen.return_value = {'status': 'sent'}
        base = f'http://127.0.0.1:{http.server_port}'

        def post(data, headers=None):
            request = urllib.request.Request(base+'/api/command',
                data=json.dumps(data).encode(), headers={'Content-Type': 'application/json',
                    'Origin': base, 'X-Remote-Key': config['key'], **(headers or {})})
            try:
                response = self.opener.open(request, timeout=3)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return response.status, json.load(response)

        receiver = 'http://198.51.100.20:8765/screen'
        try:
            with patch.object(self.app, 'remote', transport), \
                    patch.object(self.app.Handler, 'screen_url', return_value=receiver):
                self.assertEqual(post({'screen': 'open'}, {'Origin': 'http://evil.example'})[0], 403)
                self.assertEqual(post({'screen': 'open'}, {'X-Remote-Key': 'wrong'})[0], 403)
                self.assertEqual(post({'power': 'off'})[0], 400)
                self.assertEqual(post({'screen': 'open', 'url': 'http://evil.example'})[0], 400)
                transport.open_screen.assert_not_called()
                status, result = post({'screen': 'open'})
                self.assertEqual(status, 200)
                self.assertEqual(result['status'], 'sent')
                transport.open_screen.assert_called_once_with(receiver)
                transport.open_screen.side_effect = RuntimeError('ConnectionError')
                self.assertEqual(post({'screen': 'open'})[0], 502)
                self.assertEqual(transport.open_screen.call_count, 2)  # No implicit retry.
        finally:
            http.shutdown()
            http.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
