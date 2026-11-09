"""Bounded pipelining, session cleanup and codec negotiation over real sockets."""
import json
import struct
import time
import unittest
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed
from screen_share import ShareRelay, WINDOW


def packet(seq,kind=4,x=0,y=0,w=16,h=16):
    return struct.pack('!4sBBHI6HII',b'SV2\0',kind,1,0,seq,640,360,x,y,w,h,0,0)+b'\xff\xd8data\xff\xd9'


class ProtocolV2Tests(unittest.TestCase):
    def setUp(self):
        self.relay=ShareRelay('key',ack_timeout=.5)
        self.relay.start('127.0.0.1',0)
        self.addCleanup(self.relay.stop)

    def peer(self,role):
        ws=connect(f'ws://127.0.0.1:{self.relay.port}/screen',origin='http://127.0.0.1:8765',proxy=None)
        self.addCleanup(ws.close)
        ws.send(json.dumps({'key':'key','role':role,'protocol':2,'capabilities':{'mse':True,'avc':True}}))
        ready=json.loads(ws.recv(timeout=2));self.assertEqual(ready['type'],'ready')
        return ws,ready

    def test_pipeline_forwards_entire_bounded_window_before_any_ack(self):
        viewer,_=self.peer('viewer');sender,ready=self.peer('sender')
        self.assertEqual(ready['window'],WINDOW)
        self.assertTrue(ready['capabilities']['avc'])
        for seq in range(WINDOW):sender.send(packet(seq))
        for seq in range(WINDOW):self.assertEqual(viewer.recv(timeout=2),packet(seq))
        with self.assertRaises(TimeoutError):sender.recv(timeout=.03)
        viewer.send(json.dumps({'type':'ack','seq':0,'bufferMs':100}))
        self.assertEqual(json.loads(sender.recv(timeout=2))['seq'],0)
        sender.send(packet(WINDOW))
        self.assertEqual(viewer.recv(timeout=2),packet(WINDOW))

    def test_overfull_window_is_rejected_instead_of_accumulating_latency(self):
        viewer,_=self.peer('viewer');sender,_=self.peer('sender')
        for seq in range(WINDOW):sender.send(packet(seq))
        for _ in range(WINDOW):viewer.recv(timeout=2)
        sender.send(packet(WINDOW))
        with self.assertRaises(ConnectionClosed) as error:sender.recv(timeout=2)
        self.assertEqual(error.exception.rcvd.code,1008)

    def test_config_and_fallback_reach_peers_without_holding_old_credits(self):
        viewer,_=self.peer('viewer');sender,_=self.peer('sender')
        config={'type':'configure','codec':'h264','width':640,'height':360,'mime':'video/mp4; codecs="avc1.42E029"'}
        sender.send(json.dumps(config));self.assertEqual(json.loads(viewer.recv(timeout=2)),config)
        sender.send(packet(1,kind=1));viewer.recv(timeout=2)
        viewer.send('{"type":"fallback"}')
        self.assertEqual(json.loads(sender.recv(timeout=2))['type'],'fallback')
        self.assertEqual(self.relay.inflight,{})

    def test_invalid_crop_is_rejected(self):
        self.peer('viewer');sender,_=self.peer('sender')
        sender.send(packet(1,x=635,w=16))
        with self.assertRaises(ConnectionClosed):sender.recv(timeout=2)

    def test_jpeg_and_png_tile_atlases_are_forwarded(self):
        viewer,_=self.peer('viewer');sender,_=self.peer('sender')
        for seq,kind in enumerate((5,6,7)):
            payload=(b'\xff\xd8data\xff\xd9' if kind==5 else
                     b'\x89PNG\r\n\x1a\n'+b'data'+b'\0\0\0\0IEND\xaeB`\x82')
            rect=(1,64,64,64) if kind!=7 else (0,0,64,64)
            if kind!=7:payload=struct.pack('!HHH',1,64,64)+payload
            value=struct.pack('!4sBBHI6HII',b'SV2\0',kind,0,1,seq,640,360,*rect,0,0)+payload
            sender.send(value);self.assertEqual(viewer.recv(timeout=2),value)
            viewer.send(json.dumps({'type':'ack','seq':seq}));sender.recv(timeout=2)

    def test_atlas_outside_frame_is_rejected(self):
        self.peer('viewer');sender,_=self.peer('sender')
        value=struct.pack('!4sBBHI6HII',b'SV2\0',5,0,1,1,640,360,1,64,64,64,0,0)
        sender.send(value+struct.pack('!HHH',1,640,0)+b'\xff\xd8data\xff\xd9')
        with self.assertRaises(ConnectionClosed):sender.recv(timeout=2)

    def test_avc_append_timeout_falls_back_without_disconnecting_viewer(self):
        viewer,_=self.peer('viewer');sender,_=self.peer('sender')
        sender.send(json.dumps({'type':'configure','codec':'h264','width':640,'height':360,'mime':'video/mp4; codecs="avc1.42E01F"'}))
        viewer.recv(timeout=2);sender.send(packet(1,kind=1));viewer.recv(timeout=2)
        self.assertEqual(json.loads(sender.recv(timeout=2))['type'],'fallback')
        self.assertIsNotNone(self.relay.viewer)
        self.assertEqual(self.relay.inflight,{})

    def test_late_viewer_notifies_sender_and_disconnect_clears_credits(self):
        sender,ready=self.peer('sender');self.assertFalse(ready['viewer_connected'])
        viewer,_=self.peer('viewer')
        self.assertEqual(json.loads(sender.recv(timeout=2))['type'],'viewer')
        sender.send(packet(1));viewer.recv(timeout=2);viewer.close()
        self.assertEqual(json.loads(sender.recv(timeout=2))['type'],'waiting')
        self.assertEqual(self.relay.inflight,{})

    def test_end_session_closes_data_sockets_and_signals_managed_tabs(self):
        viewer,_=self.peer('viewer');sender,_=self.peer('sender')
        self.relay.end_session()
        self.assertEqual(viewer.recv(timeout=2),'close')
        with self.assertRaises(ConnectionClosed):sender.recv(timeout=2)
        self.assertIsNone(self.relay.sender)
