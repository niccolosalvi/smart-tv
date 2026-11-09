"""Audio packets are independently bounded and cannot consume video credits."""
import json
import struct
import unittest

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed
from screen_share import ShareRelay
from audio_share import audio_packet


def adts(channels=2, size=12):
    return bytes([255, 241, (1 << 6) | (3 << 2), channels << 6 | size >> 11,
                  (size >> 3) & 255, (size & 7) << 5 | 31, 252])+bytes(size-7)


def packet(seq=1, codec='aac', channels=2):
    if codec == 'aac':
        return struct.pack('!4sIQIHH', b'AU1\0', seq, 1234000, 4096, 1024, 1)+adts(channels)*5
    return (struct.pack('!4sIQIHH', b'AU1\0', seq, 1234000, 1024, 0, 2)+
            bytes(channels*4+1024*channels//2))


class AudioPacketTests(unittest.TestCase):
    def test_aac_frames_and_fallback_state_match_negotiated_format(self):
        for codec in ('aac', 'ima'):
            for channels in (1, 2):
                config={'codec':codec,'sampleRate':48000 if codec=='aac' else 24000,'channels':channels}
                self.assertEqual(audio_packet(packet(codec=codec,channels=channels),config),1)

    def test_malformed_duration_and_truncated_adts_are_rejected(self):
        config={'codec':'aac','sampleRate':48000,'channels':2}
        for value in (packet()[:-1], packet()[:16]+struct.pack('!I',2048)+packet()[20:],
                      packet().replace(b'\xff\xf1',b'\xff\x00')):
            with self.assertRaises(ValueError):audio_packet(value,config)
        config={'codec':'ima','sampleRate':24000,'channels':2}
        value=bytearray(packet(codec='ima'));value[26]=89
        with self.assertRaises(ValueError):audio_packet(bytes(value),config)


class AudioRelayTests(unittest.TestCase):
    def setUp(self):
        self.relay=ShareRelay('test-key');self.relay.start('127.0.0.1',0)
        self.addCleanup(self.relay.stop)

    def peer(self,role,key='test-key'):
        ws=connect(f'ws://127.0.0.1:{self.relay.port}/screen',origin='http://127.0.0.1:8765',proxy=None)
        self.addCleanup(ws.close);ws.send(json.dumps({'role':role,'key':key,'protocol':2,'capabilities':{'webAudio':True}}))
        if key=='test-key':self.assertEqual(json.loads(ws.recv(timeout=2))['type'],'ready')
        return ws

    def configure(self,sender,viewer,codec='aac'):
        value={'type':'configure','codec':codec,'sampleRate':48000 if codec=='aac' else 24000,'channels':2}
        sender.send(json.dumps(value));self.assertEqual(json.loads(viewer.recv(timeout=2)),value)

    def test_audio_uses_independent_window_and_ack_metrics(self):
        viewer=self.peer('audio_viewer');sender=self.peer('audio_sender');self.configure(sender,viewer)
        for seq in (1,2,3):sender.send(packet(seq))
        for seq in (1,2,3):self.assertEqual(viewer.recv(timeout=2),packet(seq))
        self.assertEqual(self.relay.inflight,{})
        viewer.send('{"type":"ack","seq":1,"bufferMs":120,"contextTime":2,"playedSamples":4096,"rms":0.05,"state":"running"}')
        self.assertEqual(json.loads(sender.recv(timeout=2))['seq'],1)
        self.assertEqual(self.relay.audio.snapshot()['played_samples'],4096)
        self.assertEqual(len(self.relay.audio.inflight),2)

    def test_full_window_is_rejected(self):
        viewer=self.peer('audio_viewer');sender=self.peer('audio_sender');self.configure(sender,viewer)
        for seq in (1,2,3):sender.send(packet(seq));viewer.recv(timeout=2)
        sender.send(packet(4))
        with self.assertRaises(ConnectionClosed):sender.recv(timeout=2)

    def test_bad_key_and_unnegotiated_audio_are_rejected(self):
        attacker=self.peer('audio_sender','bad')
        with self.assertRaises(ConnectionClosed):attacker.recv(timeout=2)
        self.peer('audio_viewer');sender=self.peer('audio_sender');sender.send(packet())
        with self.assertRaises(ConnectionClosed):sender.recv(timeout=2)

    def test_fallback_does_not_reset_video_and_end_session_closes_audio(self):
        viewer=self.peer('audio_viewer');sender=self.peer('audio_sender');self.configure(sender,viewer)
        self.relay.inflight[123]=1
        viewer.send('{"type":"fallback"}');self.assertEqual(json.loads(sender.recv(timeout=2))['type'],'fallback')
        self.assertIn(123,self.relay.inflight)
        self.relay.end_session()
        self.assertEqual(json.loads(viewer.recv(timeout=2))['type'],'stop')
        self.assertIsNone(self.relay.audio.sender);self.assertIsNone(self.relay.audio.viewer)

    def test_bad_audio_ack_does_not_close_video_channel(self):
        viewer=self.peer('audio_viewer');sender=self.peer('audio_sender');self.configure(sender,viewer)
        sender.send(packet());viewer.recv(timeout=2)
        viewer.send('{"type":"ack","seq":1,"rms":NaN}')
        with self.assertRaises(ConnectionClosed):viewer.recv(timeout=2)
        self.assertEqual(json.loads(sender.recv(timeout=2))['type'],'waiting')
        video=self.peer('viewer');self.assertIsNotNone(self.relay.viewer)

    def test_new_sender_cannot_reuse_previous_configuration(self):
        viewer=self.peer('audio_viewer');sender=self.peer('audio_sender');self.configure(sender,viewer)
        sender.close();self.assertEqual(json.loads(viewer.recv(timeout=2))['type'],'stop')
        sender=self.peer('audio_sender');sender.send(packet())
        with self.assertRaises(ConnectionClosed):sender.recv(timeout=2)
