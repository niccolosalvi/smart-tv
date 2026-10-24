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
        for name, value in [('remote', self.transport),
                            ('view', ViewState(Path(folder.name)/'view.json'))]:
            p = patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)

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
