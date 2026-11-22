"""Regression, protocol, HTTP keep-alive and optional E2E performance gate tests."""
import http.client
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from native_video import NativeSession
from screen_share import ShareRelay, WINDOW


def _ts_packet(pts, stream_id=0xE0, adapt_bytes=b''):
    stamp = bytes([
        0x21 | ((pts >> 29) & 14),
        (pts >> 22) & 255,
        ((pts >> 14) & 254) | 1,
        (pts >> 7) & 255,
        ((pts << 1) & 254) | 1,
    ])
    pes = b'\x00\x00\x01' + bytes([stream_id]) + b'\x00\x00\x80\x80\x05' + stamp
    if adapt_bytes:
        hdr = bytes([0x47, 0x41, 0x00, 0x30, len(adapt_bytes)]) + adapt_bytes + pes
    else:
        hdr = b'\x47\x41\x00\x10' + pes
    return hdr + b'\xff' * (188 - len(hdr))


class ScreenShareOptimizationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required')
    def test_screen_receiver_es5_and_behavior_contracts(self):
        result = subprocess.run(
            ['node', str(Path(__file__).with_name('screen_receiver_contracts.cjs'))],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required')
    def test_mp4_muxer_contracts(self):
        result = subprocess.run(
            ['node', str(Path(__file__).with_name('mp4_muxer_contracts.cjs'))],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_relay_sets_tcp_nodelay_and_negotiates_window(self):
        self.assertEqual(WINDOW, 3)
        relay = ShareRelay('k', ack_timeout=1.0)
        relay.start('127.0.0.1', 0)
        self.addCleanup(relay.stop)
        url = f'ws://127.0.0.1:{relay.port}/screen'
        viewer = connect(url, origin='http://127.0.0.1:8765', proxy=None)
        self.addCleanup(viewer.close)
        viewer.send(json.dumps({'key': 'k', 'role': 'viewer', 'protocol': 2, 'capabilities': {'avc': True}}))
        v_ready = json.loads(viewer.recv(timeout=2))
        self.assertEqual(v_ready['window'], WINDOW)

        sender = connect(url, origin='http://127.0.0.1:8765', proxy=None)
        self.addCleanup(sender.close)
        sender.send(json.dumps({'key': 'k', 'role': 'sender', 'protocol': 2}))
        s_ready = json.loads(sender.recv(timeout=2))
        self.assertEqual(s_ready['window'], WINDOW)

        sock = relay.sender.transport.get_extra_info('socket')
        self.assertNotEqual(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY), 0)

    def test_precompiled_struct_tile_validation(self):
        relay = ShareRelay('k', ack_timeout=1.0)
        relay.start('127.0.0.1', 0)
        self.addCleanup(relay.stop)
        url = f'ws://127.0.0.1:{relay.port}/screen'
        viewer = connect(url, origin='http://127.0.0.1:8765', proxy=None)
        self.addCleanup(viewer.close)
        viewer.send(json.dumps({'key': 'k', 'role': 'viewer', 'protocol': 2, 'capabilities': {}}))
        viewer.recv(timeout=2)

        sender = connect(url, origin='http://127.0.0.1:8765', proxy=None)
        self.addCleanup(sender.close)
        sender.send(json.dumps({'key': 'k', 'role': 'sender', 'protocol': 2}))
        sender.recv(timeout=2)

        valid_tiles = [(0, 0), (128, 64)]
        atlas = (
            struct.pack('!H', len(valid_tiles))
            + b''.join(struct.pack('!HH', tx, ty) for tx, ty in valid_tiles)
            + b'\xff\xd8ok\xff\xd9'
        )
        valid_pkt = struct.pack('!4sBBHI6HII', b'SV2\0', 5, 0, 1, 1, 640, 360, 2, 64, 128, 64, 0, 0) + atlas
        sender.send(valid_pkt)
        self.assertEqual(viewer.recv(timeout=2), valid_pkt)
        viewer.send(json.dumps({'type': 'ack', 'seq': 1}))
        sender.recv(timeout=2)

        for seq, bad_tile in ((2, (65, 0)), (3, (640, 0))):
            bad_atlas = struct.pack('!HHH', 1, *bad_tile) + b'\xff\xd8bad\xff\xd9'
            bad_pkt = struct.pack('!4sBBHI6HII', b'SV2\0', 5, 0, 1, seq, 640, 360, 1, 64, 64, 64, 0, 0) + bad_atlas
            s2 = connect(url, origin='http://127.0.0.1:8765', proxy=None)
            self.addCleanup(s2.close)
            # Only one sender allowed; replace first sender cleanly
            sender.close()
            for _ in range(20):
                if relay.sender is None:
                    break
                threading.Event().wait(0.02)
            s2.send(json.dumps({'key': 'k', 'role': 'sender', 'protocol': 2}))
            s2.recv(timeout=2)
            s2.send(bad_pkt)
            with self.assertRaises(ConnectionClosed) as exc:
                s2.recv(timeout=2)
            self.assertEqual(exc.exception.rcvd.code, 1008)
            sender = s2

    def _fake_session(self):
        proc = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO())
        with patch('native_video.subprocess.Popen', return_value=proc) as popen, \
                patch('native_video.threading.Thread'):
            sess = NativeSession(Mock(), 'http://192.0.2.1:8765')
        return sess, popen.call_args.args[0]

    def test_ffmpeg_args_include_low_latency_flags(self):
        sess, args = self._fake_session()
        self.assertIn('-fflags', args)
        self.assertIn('+nobuffer', args)
        self.assertIn('-max_delay', args)
        self.assertEqual(args[args.index('-c') + 1], 'copy')
        self.assertNotIn('libx264', args)
        sess.close()

    def test_measure_video_handles_splits_adaptation_and_33bit_pts_wrap(self):
        sess, _ = self._fake_session()
        start_pts = (1 << 33) - (180 * 1500)
        parts = []
        for n in range(361):
            pts = (start_pts + n * 1500) % (1 << 33)
            # Include adaptation fields, duplicate PTS and interleaved audio packets.
            parts.append(_ts_packet(pts, 0xE0, adapt_bytes=b'\x00\x01\x02'))
            parts.append(_ts_packet(pts, 0xE0))
            parts.append(_ts_packet(pts, 0xC0))
        stream_bytes = b''.join(parts)
        for off in range(0, len(stream_bytes), 317):
            sess._measure_video(stream_bytes[off:off + 317])
        self.assertEqual(sess.video_frames, 361)
        self.assertEqual(sess.stream_fps, 60.0)
        sess.close()

    def test_stream_cursor_survives_eviction_and_detects_slow_reader(self):
        sess, _ = self._fake_session()
        chunks = [f'chunk-{i:02d}-'.encode() * 4 for i in range(16)]
        offset = 0
        for ch in chunks:
            sess.chunks.append((offset, ch))
            offset += len(ch)
        sess.end = offset

        gen = sess.stream()
        received = [next(gen) for _ in range(5)]
        # Evict first 3 chunks (indices 0,1,2) while reader is already at index 5.
        for _ in range(3):
            ev_off, ev_data = sess.chunks.popleft()
            sess.base = ev_off + len(ev_data)
            sess.popped += 1
        received.extend(next(gen) for _ in range(11))
        self.assertEqual(b''.join(received), b''.join(chunks))

        # Slow reader behind evicted base raises RuntimeError.
        sess2, _ = self._fake_session()
        sess2.chunks.append((100, b'late'))
        sess2.base = 100
        sess2.end = 104
        with self.assertRaises(RuntimeError):
            next(sess2.stream())
        sess.close()
        sess2.close()

    def test_http11_keepalive_reuses_single_tcp_connection_and_head_closes(self):
        with tempfile.TemporaryDirectory() as runtime, \
                patch.dict(os.environ, {'LOCALAPPDATA': runtime, 'TV_HOST': '127.0.0.1'}):
            import server
            from http.server import ThreadingHTTPServer
            srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            srv.daemon_threads = True
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()

            class CountingConn(http.client.HTTPConnection):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.connects = 0

                def connect(self):
                    self.connects += 1
                    super().connect()

            fake_sess = Mock(closed=False)
            try:
                with patch.object(server.native_cast, 'get', return_value=fake_sess), \
                        patch.object(server.native_cast, 'host', '127.0.0.1'):
                    conn = CountingConn('127.0.0.1', srv.server_port, timeout=5)
                    headers = {
                        'Host': f'127.0.0.1:{srv.server_port}',
                        'Origin': f'http://127.0.0.1:{srv.server_port}',
                        'X-Remote-Key': server.CSRF,
                        'Content-Type': 'video/mp4',
                    }
                    for _ in range(10):
                        conn.request('POST', '/api/native/sess/chunk', body=b'abc123', headers=headers)
                        resp = conn.getresponse()
                        body = json.loads(resp.read())
                        self.assertEqual(resp.status, 200)
                        self.assertTrue(body['accepted'])
                    self.assertEqual(conn.connects, 1)
                    conn.close()

                    conn2 = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=5)
                    conn2.request('HEAD', '/native/sess/stream.ts', headers={'Host': f'127.0.0.1:{srv.server_port}'})
                    head_resp = conn2.getresponse()
                    head_resp.read()
                    self.assertEqual(head_resp.status, 200)
                    self.assertEqual(head_resp.getheader('Content-Length'), '0')
                    self.assertEqual(head_resp.getheader('Connection'), 'close')
                    conn2.close()
            finally:
                srv.shutdown()
                srv.server_close()
                t.join()

    @unittest.skipUnless(os.environ.get('SMART_TV_PERF_TESTS') == '1', 'Optional Playwright/Edge performance gate')
    def test_end_to_end_performance_gates(self):
        from tools.benchmark_screen_share import run_suite_a, run_suite_b, run_suite_c

        suite_a = run_suite_a(frames=12)
        self.assertGreaterEqual(suite_a['desktop_static']['frames_without_update'], 10)
        self.assertGreaterEqual(suite_a['desktop_cursor']['mean_ssim'], 0.99)
        self.assertGreaterEqual(suite_a['desktop_scroll_mixed']['mean_ssim'], 0.95)
        self.assertGreaterEqual(suite_a['cinema_motion_cinema']['psnr_db'], 34.0)

        suite_b = run_suite_b()
        for name, metrics in suite_b.items():
            with self.subTest(scenario=name):
                self.assertEqual(metrics['errors'], [])
                self.assertFalse(metrics['disconnected'])
        self.assertGreaterEqual(suite_b['e2e_jpeg_cinema_720p30']['delivered_fps'], 20.0)
        self.assertGreaterEqual(suite_b['e2e_h264_mse_720p60']['decoded_fps'], 30.0)

        suite_c = run_suite_c()
        self.assertEqual(suite_c['http_chunk_upload_keepalive']['tcp_connections_opened'], 1)
        self.assertGreater(suite_c['relay_throughput_v2']['packets_per_sec'], 500.0)
