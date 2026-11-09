"""Exercise accepted HTTP commands with mocked transport: no TV side effects."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import os
from pathlib import Path
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer

_runtime = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {'LOCALAPPDATA': _runtime.name}):
    import server
from view_state import ViewState


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.transport = Mock()
        self.transport.send.return_value = {'status': 'sent'}
        self.transport.switch_to_pc.return_value = {'status': 'sent'}
        self.transport.open_screen.return_value = {'status': 'sent'}
        self.transport.close_screen.return_value = {'status': 'sent'}
        for name, value in [('remote', self.transport),
                            ('view', ViewState(Path(folder.name)/'view.json'))]:
            p = patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_host_validation_tracks_socket_address_after_network_change(self):
        handler = object.__new__(server.Handler)
        handler.server = Mock(server_port=8765, http_port=8765)
        handler.connection = Mock()
        handler.headers = {'Host': '198.51.100.20:8765'}
        handler.connection.getsockname.return_value = ('198.51.100.20', 8765)
        self.assertTrue(handler.valid_host())
        handler.connection.getsockname.return_value = ('198.51.100.21', 8765)
        self.assertFalse(handler.valid_host())
        handler.headers['Host'] = '198.51.100.21:8765'
        self.assertTrue(handler.valid_host())
        handler.headers['Host'] = 'attacker.example:8765'
        self.assertFalse(handler.valid_host())
        handler.headers['Host'] = 'localhost:8765'
        self.assertFalse(handler.valid_host())
        handler.connection.getsockname.return_value = ('127.0.0.1', 8765)
        self.assertTrue(handler.valid_host())

    def post(self, payload):
        request = urllib.request.Request(self.base + '/api/command',
            data=json.dumps(payload).encode(), headers={
                'Content-Type': 'application/json', 'X-Remote-Key': server.CSRF})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_volume_click_is_one_request_with_two_steps(self):
        code, data = self.post({'volume': -2})
        self.assertEqual(code, 200)
        self.transport.send.assert_called_once_with(['KEY_VOLDOWN']*2, 'volume:-2')
        self.assertEqual(data['status'], 'sent')

    def test_pc_switch_selects_pc_context(self):
        code, data = self.post({'source': 'pc'})
        self.assertEqual(code, 200)
        self.transport.switch_to_pc.assert_called_once_with()
        self.assertEqual(data['context']['source'], 'pc')

    def test_failed_hdmi_switch_does_not_claim_pc(self):
        self.transport.switch_to_pc.side_effect = RuntimeError('ConnectionError')
        code, data = self.post({'source': 'pc'})
        self.assertEqual(code, 502)
        self.assertEqual(data['context']['source'], 'unknown')
        self.transport.switch_to_pc.assert_called_once()

    def test_busy_server_rejects_double_step_without_sending(self):
        with server.busy:
            code, data = self.post({'volume': 2})
        self.assertEqual(code, 409)
        self.transport.send.assert_not_called()

    def test_screen_button_opens_only_server_selected_receiver(self):
        receiver = 'http://198.51.100.20:8765/screen'
        with patch.object(server, 'sharing', Mock(port=8767, viewer=None)), \
                patch.object(server.Handler, 'screen_url', return_value=receiver):
            code, data = self.post({'screen': 'open'})
        self.assertEqual(code, 200)
        self.transport.open_screen.assert_called_once_with(receiver)
        self.assertEqual(data['context']['source'], 'screen')
        self.transport.launch_app.assert_not_called()

    def test_screen_action_refuses_custom_urls_and_unavailable_relay(self):
        for payload in [{'screen': 'http://evil.example'}, {'screen': []},
                        {'screen': 'open', 'url': 'http://evil.example'}]:
            self.assertEqual(self.post(payload)[0], 400)
        with patch.object(server, 'sharing', None):
            self.assertEqual(self.post({'screen': 'open'})[0], 503)
        self.transport.open_screen.assert_not_called()

    def test_stop_closes_relay_and_browser_and_invalidates_source(self):
        relay=Mock(port=8767, viewer=None)
        with patch.object(server,'sharing',relay):
            code,data=self.post({'screen':'stop'})
        self.assertEqual(code,200)
        relay.end_session.assert_called_once()
        self.transport.close_screen.assert_called_once()
        self.assertEqual(data['context']['source'],'unknown')

    def test_screen_failure_invalidates_context_and_does_not_retry(self):
        self.transport.open_screen.side_effect = RuntimeError('ConnectionError')
        with patch.object(server, 'sharing', Mock(port=8767, viewer=None)), \
                patch.object(server.Handler, 'screen_url', return_value='http://198.51.100.20:8765/screen'):
            code, data = self.post({'screen': 'open'})
        self.assertEqual(code, 502)
        self.assertEqual(data['context']['source'], 'unknown')
        self.transport.open_screen.assert_called_once()

    def test_receiver_url_tracks_lan_socket_and_resolves_loopback_via_tv_route(self):
        handler = object.__new__(server.Handler)
        handler.connection = Mock()
        handler.server = Mock(server_port=8765, http_port=8765)
        handler.connection.getsockname.return_value = ('198.51.100.20', 8765)
        self.assertEqual(handler.screen_url(), 'http://198.51.100.20:8765/screen')
        handler.connection.getsockname.return_value = ('127.0.0.1', 8765)
        with patch.object(server.socket, 'socket') as factory:
            route = factory.return_value.__enter__.return_value
            route.getsockname.return_value = ('198.51.100.21', 1234)
            self.assertEqual(handler.screen_url(), 'http://198.51.100.21:8765/screen')
            route.connect.assert_called_once_with((server.HOST, server.PORT))
