import io
import json
import queue
import shutil
import subprocess
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

from native_video import NativeCast, NativeSession, NativeTV


class NativeVideoTests(unittest.TestCase):
    def session(self):
        process=Mock(stdin=io.BytesIO(),stdout=io.BytesIO(),stderr=io.BytesIO())
        with patch('native_video.subprocess.Popen',return_value=process) as popen,patch('native_video.threading.Thread'):
            session=NativeSession(Mock(),'http://192.0.2.1:8765')
        args=popen.call_args.args[0]
        self.assertEqual(args[args.index('-c')+1],'copy')
        self.assertNotIn('libx264',args)
        return session,process

    def test_input_and_output_buffers_are_bounded(self):
        session,process=self.session()
        with self.assertRaises(ValueError):session.feed(b'')
        with self.assertRaises(ValueError):session.feed(b'x'*(session.max_chunk+1))
        for _ in range(8):session.feed(b'valid')
        with self.assertRaises(RuntimeError):session.feed(b'overflow')
        self.assertTrue(session.closed)
        process.terminate.assert_called_once()
        self.assertTrue(session.inputs.empty())
        session.close();process.terminate.assert_called_once()

    def test_stream_keeps_bytes_order_and_detects_slow_reader(self):
        session,_=self.session()
        session.chunks.extend([(0,b'abc'),(3,b'def')]);session.end=6
        stream=session.stream()
        self.assertEqual(next(stream),b'abc');self.assertEqual(next(stream),b'def')
        session.close()
        with self.assertRaises(StopIteration):next(stream)
        session,_=self.session();session.base=100;session.end=200
        with self.assertRaises(RuntimeError):next(session.stream())
        session.close()

    def test_manager_rejects_overlapping_sessions_and_wrong_identifiers(self):
        cast=NativeCast('192.0.2.2')
        with patch('native_video.shutil.which',return_value='/usr/bin/ffmpeg'),patch('native_video.NativeSession') as factory:
            factory.return_value.closed=False;factory.return_value.id='private-session'
            session=cast.start('http://192.0.2.1:8765')
            with self.assertRaises(RuntimeError):cast.start('http://192.0.2.1:8765')
            with self.assertRaises(ValueError):cast.get('wrong-session')
            self.assertIs(cast.get('private-session'),session)
            cast.stop();session.close.assert_called_once()

    def test_stream_rate_uses_video_timestamps_not_audio_progress(self):
        session,_=self.session()
        def packet(pts,stream=0xe0):
            stamp=bytes([0x21|((pts>>29)&14),(pts>>22)&255,((pts>>14)&254)|1,(pts>>7)&255,((pts<<1)&254)|1])
            pes=b'\x00\x00\x01'+bytes([stream])+b'\x00\x00\x80\x80\x05'+stamp
            data=b'\x47\x41\x00\x10'+pes
            return data+b'\xff'*(188-len(data))
        data=b''.join(packet(n*1500)+packet(n*1500,0xc0) for n in range(361))
        for offset in range(0,len(data),497):session._measure_video(data[offset:offset+497])
        self.assertEqual(session.video_frames,361)
        self.assertEqual(session.stream_fps,60.0)
        session.close()

    def test_soap_escapes_nested_metadata_and_does_not_add_credentials(self):
        tv=NativeTV('192.0.2.2');tv.opener=MagicMock()
        response=tv.opener.open.return_value.__enter__.return_value
        response.read.return_value=b'<Envelope><Body><Result>OK</Result></Body></Envelope>'
        tv.play('http://192.0.2.1/live?a=1&b=2',mime='video/mpeg')
        request=tv.opener.open.call_args_list[0].args[0]
        root=ET.fromstring(request.data)
        uri=next(e.text for e in root.iter() if e.tag=='CurrentURI')
        self.assertEqual(uri,'http://192.0.2.1/live?a=1&b=2')
        metadata=next(e.text for e in root.iter() if e.tag=='CurrentURIMetaData')
        ET.fromstring(metadata)

    def test_native_http_auth_size_and_tv_restrictions(self):
        import server
        handler=object.__new__(server.Handler)
        handler.reply=Mock();handler.path='/api/native/start'
        handler.headers={'Content-Length':'2'};handler.rfile=io.BytesIO(b'{}')
        handler.connection=Mock();handler.connection.getsockname.return_value=('192.0.2.1',8766)
        handler.server=SimpleNamespace(http_port=8765,server_port=8766)
        handler.valid_host=lambda:True
        with patch.object(server,'native_cast') as cast:
            handler._handle_post();self.assertEqual(handler.reply.call_args.args[0],403)
            cast.start.assert_not_called()
            handler.headers={'Content-Length':'2','X-Remote-Key':server.CSRF,'Origin':'https://foreign.example'}
            handler.origin=lambda:'https://192.0.2.1:8766'
            handler._handle_post();self.assertEqual(handler.reply.call_args.args[0],403)
            cast.start.assert_not_called()
            handler.headers={'Content-Length':'2','X-Remote-Key':server.CSRF}
            cast.start.return_value.id='session'
            handler._handle_post();cast.start.assert_called_once_with('http://192.0.2.1:8765')
            handler.rfile=io.BytesIO(b'{"receiver":"tizen"}')
            handler.headers={'Content-Length':'20'}
            with patch.dict('os.environ',{'SMART_TV_TIZEN_RECEIVER':'0'}):handler.native_post()
            self.assertEqual(handler.reply.call_args.args[0],409)
            handler.headers={'Content-Length':str(NativeSession.max_chunk+1)}
            handler.native_post();self.assertEqual(handler.reply.call_args.args[0],400)
            handler.path='/native/session/stream.ts';handler.client_address=('192.0.2.99',1234)
            cast.host='192.0.2.2'
            handler.native_stream();self.assertEqual(handler.reply.call_args.args[0],403)

    def test_tizen_launch_accepts_only_our_native_session_url(self):
        from remote import Remote
        remote=object.__new__(Remote);remote.tv=Mock();remote.lock=threading.Lock()
        remote.tv.is_alive.return_value=True
        url='http://192.0.2.1:8765/native/'+'a'*32+'/stream.ts'
        remote.open_native_receiver(url)
        app,method,payload=remote.tv.run_app.call_args.args
        self.assertEqual(app,'SmartTVRx1.Receiver');self.assertEqual(method,'DEEP_LINK')
        self.assertEqual(json.loads(payload),{'url':url})
        for invalid in ('https://example.com/video','http://127.0.0.1:8765/native/'+'a'*32+'/stream.ts',url+'?code=x',url.replace('/native/','/other/')):
            with self.assertRaises(ValueError):remote.open_native_receiver(invalid)
        self.assertEqual(remote.tv.run_app.call_count,1)

    @unittest.skipUnless(shutil.which('node'),'Node required')
    def test_browser_native_lifecycle(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('native_sender_contracts.cjs'))],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'),'Node required')
    def test_tizen_receiver_buffering_and_lifecycle(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('tizen_receiver_contracts.cjs'))],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
