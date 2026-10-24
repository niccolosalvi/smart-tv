import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from diagnostics import Timeline, request_id, tv_event
from remote import Remote
import websocket
from unittest.mock import Mock

class DiagnosticTests(unittest.TestCase):
    def test_bounded_history_and_incremental_cursor(self):
        log = Timeline(limit=2)
        for index in range(3):
            log.emit('Server', str(index))
        snapshot = log.read()
        self.assertEqual([e['id'] for e in snapshot['events']], [2, 3])
        self.assertEqual(log.read(2, snapshot['epoch'])['events'][0]['id'], 3)
        self.assertTrue(log.read(99, 'previous-server')['reset'])

    def test_request_ids_are_separate_across_threads(self):
        log = Timeline()
        def worker(value):
            token = request_id.set(value)
            try:
                log.emit('Server', value)
            finally:
                request_id.reset(token)
        threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(all(e['message'] == e['request_id'] for e in log.read()['events']))

    def test_tv_payload_and_unknown_errors_never_leak_secrets(self):
        log = Timeline()
        with patch('diagnostics.timeline', log):
            tv_event('ms.channel.connect', {'data': {'token': 'SECRET'}})
            tv_event('ms.error', {'data': {'message': 'URL?token=SECRET'}})
        self.assertNotIn('SECRET', json.dumps(log.read()))

    def test_persistence_and_rotation_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'diagnostics.jsonl'
            path.write_text('x'*2_000_001)
            log = Timeline(path=path)
            log.emit('Server', 'new event')
            self.assertLess(path.stat().st_size, 1000)
            self.assertTrue(path.with_suffix('.previous.jsonl').exists())

    def test_storage_failure_does_not_interrupt_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / 'file'
            blocker.write_text('not a directory')
            log = Timeline(path=blocker / 'diagnostics.jsonl')
            self.assertEqual(log.emit('Server', 'event')['id'], 1)

    def test_receiver_survives_idle_timeout_and_marks_closed_socket(self):
        remote = Remote.__new__(Remote)
        remote.tv = Mock()
        connection = Mock(connected=True)
        connection.recv.side_effect = [websocket.WebSocketTimeoutException(),
                                      json.dumps({'event': 'ms.error'}), '']
        remote.tv.connection = connection
        remote._closing = set()
        with patch('remote.timeline', Timeline()):
            remote._read_events(connection)
        remote.tv._websocket_event.assert_called_once()
        self.assertFalse(connection.connected)

    def test_old_receiver_cannot_invalidate_new_connection(self):
        remote = Remote.__new__(Remote)
        remote.tv = Mock()
        current = Mock(connected=True)
        old = Mock(connected=True)
        old.recv.return_value = ''
        remote.tv.connection = current
        remote._closing = {id(old)}
        with patch('remote.timeline', Timeline()):
            remote._read_events(old)
        self.assertTrue(current.connected)

if __name__ == '__main__':
    unittest.main()
