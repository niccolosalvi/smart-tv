"""Offline regression checks; do not start servers, event loops or TV sessions."""
import json
import shutil
import struct
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from screen_share import ShareRelay


class Peer:
    def __init__(self, messages=()):
        self.messages=messages
        self.sent=[]
        self.closed=None

    async def send(self, message):
        self.sent.append(message)

    async def close(self, code, reason):
        self.closed=code

    async def __aiter__(self):
        for message in self.messages:
            yield message


def complete(coroutine):
    """All fake I/O completes synchronously; no event loop/socket is needed."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('Unexpected asynchronous operation in fake transport')


async def immediate(awaitable, *args):
    return await awaitable


async def gathered(*args, **kwargs):
    return []


def task(coroutine):
    coroutine.close()
    class Task:
        def cancel(self):
            pass
    return Task()


class VideoQualityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required')
    def test_real_worker_decisions(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('video_worker_contracts.cjs'))],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required')
    def test_sender_capture_and_cleanup(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('video_sender_contracts.cjs'))],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def transmit(self, messages):
        relay=ShareRelay('key');relay.viewer=Peer();relay.viewer_protocol=2
        sender=Peer(messages);relay.sender=sender
        with patch('screen_share.asyncio.create_task',task),patch('screen_share.asyncio.wait_for',immediate), \
                patch('screen_share.asyncio.gather',gathered):
            complete(relay._sender_v2(sender))
        return relay,sender

    def test_full_hd_config_and_more_than_240_tiles(self):
        config={'type':'configure','codec':'jpeg','width':1920,'height':1080}
        tiles=[(x,y) for y in range(0,1080,64) for x in range(0,1920,64)][:241]
        atlas=struct.pack('!H',len(tiles))+b''.join(struct.pack('!HH',*tile) for tile in tiles)+b'\xff\xd8data\xff\xd9'
        packet=struct.pack('!4sBBHI6HII',b'SV2\0',5,0,1,1,1920,1080,8,64,512,1984,0,0)+atlas
        relay,sender=self.transmit([json.dumps(config),packet])
        self.assertIsNone(sender.closed)
        self.assertEqual(json.loads(relay.viewer.sent[0]),config)
        self.assertEqual(relay.viewer.sent[1],packet)

    def test_oversized_frame_dimensions_are_rejected(self):
        for width,height in ((1922,1080),(1920,1082),(True,720)):
            with self.subTest(width=width,height=height):
                relay,sender=self.transmit([json.dumps({'type':'configure','codec':'jpeg','width':width,'height':height})])
                self.assertEqual(sender.closed,1008)
                self.assertEqual(relay.viewer.sent,[])

    def test_decode_measurement_is_forwarded_but_malformed_values_are_rejected(self):
        for value in (21.5,-1,True,float('nan'),float('inf'),60001):
            with self.subTest(value=value):
                relay=ShareRelay('key');relay.sender=Peer();relay.sender_protocol=2;relay.inflight[1]=time.monotonic()
                viewer=Peer([json.dumps({'type':'ack','seq':1,'decodeMs':value})])
                with patch('screen_share.asyncio.wait_for',immediate):
                    complete(relay._viewer_v2(viewer))
                if value==21.5:
                    self.assertEqual(json.loads(relay.sender.sent[0])['decodeMs'],value)
                    self.assertEqual(relay.metrics['decode_ms'],value)
                else:
                    self.assertEqual(viewer.closed,1008)
                    self.assertEqual(relay.sender.sent,[])
