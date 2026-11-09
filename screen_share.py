"""Bounded, RAM-only AVC/image relay. No capture or transcoding here."""
import asyncio
import json
import re
import secrets
import threading
import struct
import time
from contextlib import AsyncExitStack

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from audio_share import AudioRelay

MAX_FRAME = 512 * 1024
WINDOW = 3


class ShareRelay:
    def __init__(self, key, http_port=8765, ack_timeout=5, https_port=8766):
        self.key = key
        self.http_port = http_port
        self.https_port = https_port
        self.ack_timeout = ack_timeout
        self.port = None
        self.secure_port = None
        self.sender = self.viewer = self.ack = None
        self.viewer_info = {}
        self.sender_protocol = self.viewer_protocol = 1
        self.inflight = {}
        self.metrics = {'packets': 0, 'bytes': 0, 'ack_ms': 0, 'buffer_ms': 0}
        self.avc_failed_agents = set()
        self.audio = AudioRelay()
        self.thread = None
        self.error = None
        self.ready = threading.Event()

    def start(self, host='0.0.0.0', port=8767, ssl_context=None, secure_port=8768):
        self.thread = threading.Thread(target=self._run, args=(host, port, ssl_context, secure_port), daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError('Avvio relay schermo scaduto')
        if self.error:
            raise RuntimeError('Relay schermo non disponibile') from self.error

    def _run(self, host, port, ssl_context, secure_port):
        async def run():
            self.loop = asyncio.get_running_loop()
            self.stop_event = asyncio.Event()
            try:
                async with AsyncExitStack() as stack:
                    options = dict(max_size=MAX_FRAME, max_queue=1, compression=None,
                                   write_limit=32768, ping_interval=10, ping_timeout=10,
                                   close_timeout=1)
                    listener = await stack.enter_async_context(serve(self.handle, host, port, **options))
                    self.port = listener.sockets[0].getsockname()[1]
                    if ssl_context is not None:
                        secure = await stack.enter_async_context(serve(
                            self.handle, host, secure_port, ssl=ssl_context, **options))
                        self.secure_port = secure.sockets[0].getsockname()[1]
                    self.ready.set()
                    await self.stop_event.wait()
            except Exception as exc:
                self.error = exc
                self.ready.set()
        asyncio.run(run())

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.stop_event.set)
            self.thread.join(timeout=5)

    def valid_request(self, ws):
        # Match the actual interface, as the HTTP dashboard does. Reject foreign
        # browser origins even if they can reach the LAN WebSocket port.
        address, port = ws.local_address[:2]
        expected_host = f'[{address}]:{port}' if ':' in address else f'{address}:{port}'
        origin = ws.request.headers.get('Origin', '')
        secure = ws.transport.get_extra_info('ssl_object') is not None
        dashboard = (f'https://{address}:{self.https_port}' if secure else
                     f'http://{address}:{self.http_port}')
        local_sender = re.fullmatch(r'http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}', origin)
        return (ws.request.path == '/screen' and
                ws.request.headers.get('Host') == expected_host and
                (origin == dashboard or (not secure and bool(local_sender))))

    def end_session(self):
        if self.thread and self.thread.is_alive():
            asyncio.run_coroutine_threadsafe(self._end_session(), self.loop).result(timeout=5)

    async def _end_session(self):
        await self.audio.end_session()
        sender, viewer = self.sender, self.viewer
        if viewer:
            try:
                await asyncio.wait_for(viewer.send('close'), 1)
            except (ConnectionClosed, asyncio.TimeoutError):
                pass
        for peer in (sender, viewer):
            if peer:
                await peer.close(1000, 'Condivisione terminata')
        self.inflight.clear()

    async def _watch_acks(self, ws):
        while self.sender is ws:
            await asyncio.sleep(.5)
            if self.inflight and time.monotonic()-min(self.inflight.values()) > self.ack_timeout:
                if self.metrics.get('codec') == 'h264':
                    self.inflight.clear()
                    self.metrics['receiver_error'] = 'MSE append timeout'
                    agent = self.viewer_info.get('userAgent')
                    if agent and len(self.avc_failed_agents)<8:
                        self.avc_failed_agents.add(agent)
                    self.viewer_info['avc'] = False
                    await ws.send('{"type":"fallback","reason":"MSE append timeout"}')
                    continue
                if self.viewer:
                    await self.viewer.close(1011, 'Ricevitore non risponde')
                self.inflight.clear()

    async def _sender_v2(self, ws):
        watcher = asyncio.create_task(self._watch_acks(ws))
        try:
            async for message in ws:
                viewer = self.viewer
                if isinstance(message, str):
                    if len(message) > 1024:
                        return await ws.close(1008, 'Controllo troppo grande')
                    try:
                        data = json.loads(message)
                        if data.get('type') != 'configure' or data.get('codec') not in ('h264', 'jpeg'):
                            raise ValueError()
                        if not all(type(data.get(k)) is int for k in ('width', 'height')):
                            raise ValueError()
                        if not (2 <= data['width'] <= 1280 and 2 <= data['height'] <= 720):
                            raise ValueError()
                        if data['codec'] == 'h264' and not re.fullmatch(r'video/mp4; codecs="avc1\.[0-9A-Fa-f]{6}"', data.get('mime', '')):
                            raise ValueError()
                    except (ValueError, TypeError, AttributeError):
                        return await ws.close(1008, 'Configurazione non valida')
                    self.inflight.clear()
                    self.metrics.update(codec=data['codec'], packets=0, bytes=0, ack_ms=0, buffer_ms=0)
                    if viewer and self.viewer_protocol == 2:
                        await asyncio.wait_for(viewer.send(json.dumps(data)), self.ack_timeout)
                    continue
                if len(message) < 32 or message[:4] != b'SV2\0':
                    return await ws.close(1008, 'Pacchetto video non valido')
                _, kind, flags, count, seq, fw, fh, x, y, w, h, _, _ = struct.unpack_from('!4sBBHI6HII', message)
                if count > 60:
                    return await ws.close(1008, 'Numero campioni non valido')
                if kind not in (1, 2, 4, 5, 6, 7) or not (2 <= fw <= 1280 and 2 <= fh <= 720):
                    return await ws.close(1008, 'Formato o dimensioni non validi')
                if kind == 4 and (not w or not h or x+w > fw or y+h > fh or
                                  message[32:34] != b'\xff\xd8' or message[-2:] != b'\xff\xd9'):
                    return await ws.close(1008, 'Rettangolo JPEG non valido')
                if kind == 7 and (not w or not h or x+w > fw or y+h > fh or
                        message[32:40]!=b'\x89PNG\r\n\x1a\n' or message[-12:]!=b'\0\0\0\0IEND\xaeB`\x82'):
                    return await ws.close(1008,'Rettangolo PNG non valido')
                if kind in (5,6):
                    if len(message)<36:
                        return await ws.close(1008,'Atlante non valido')
                    tiles=struct.unpack_from('!H',message,32)[0]
                    offset=34+tiles*4
                    if (not 1<=tiles<=240 or not 1<=x<=8 or y!=64 or w!=x*64 or
                            h!=((tiles+x-1)//x)*64 or len(message)<offset+4 or
                            (kind==5 and (message[offset:offset+2]!=b'\xff\xd8' or message[-2:]!=b'\xff\xd9')) or
                            (kind==6 and (message[offset:offset+8]!=b'\x89PNG\r\n\x1a\n' or message[-12:]!=b'\0\0\0\0IEND\xaeB`\x82'))):
                        return await ws.close(1008,'Atlante immagine non valido')
                    for index in range(tiles):
                        tx,ty=struct.unpack_from('!HH',message,34+index*4)
                        if tx>=fw or ty>=fh or tx%64 or ty%64:
                            return await ws.close(1008,'Coordinate atlante non valide')
                if viewer is None or self.viewer_protocol != 2:
                    self.inflight.clear()
                    await ws.send('{"type":"waiting"}')
                    continue
                if len(self.inflight) >= WINDOW or seq in self.inflight:
                    return await ws.close(1008, 'Finestra di trasmissione superata')
                self.inflight[seq] = time.monotonic()
                self.metrics['packets'] += 1
                self.metrics['bytes'] += len(message)
                try:
                    await asyncio.wait_for(viewer.send(message), self.ack_timeout)
                except (ConnectionClosed, asyncio.TimeoutError):
                    self.inflight.clear()
                    await ws.send('{"type":"waiting"}')
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    async def _viewer_v2(self, ws):
        async for message in ws:
            if message == 'ack' and self.sender_protocol == 1 and self.ack and not self.ack.done():
                self.ack.set_result(True)
                continue
            try:
                if not isinstance(message, str) or len(message) > 1024:
                    raise ValueError()
                data = json.loads(message)
                if data.get('type') == 'ack' and type(data.get('seq')) is int:
                    sent = self.inflight.pop(data['seq'], None)
                    if sent is None:
                        continue  # Delayed ACK from a codec reconfiguration.
                    self.metrics['ack_ms'] = round((time.monotonic()-sent)*1000, 1)
                    buffer_ms = data.get('bufferMs', 0)
                    if type(buffer_ms) not in (int, float) or not 0 <= buffer_ms <= 60000:
                        raise ValueError()
                    self.metrics['buffer_ms'] = round(buffer_ms, 1)
                    for field, maximum in (('videoWidth',1920),('playedSeconds',86400000),('decodedFrames',1e12)):
                        value=data.get(field)
                        if type(value) in (int,float) and 0 <= value <= maximum:
                            self.metrics[field]=round(value,2)
                    if type(data.get('paused')) is bool:
                        self.metrics['paused']=data['paused']
                    reply = {'type': 'ack', 'seq': data['seq'],
                             'bufferMs': self.metrics['buffer_ms'], 'ackMs': self.metrics['ack_ms']}
                elif data.get('type') == 'fallback':
                    self.inflight.clear()
                    agent = self.viewer_info.get('userAgent')
                    if agent and len(self.avc_failed_agents)<8:
                        self.avc_failed_agents.add(agent)
                    self.viewer_info['avc'] = False
                    reply = {'type': 'fallback', 'reason': 'Decoder video del ricevitore non disponibile'}
                elif data.get('type') == 'diagnostic' and isinstance(data.get('stage'), str):
                    self.metrics['receiver_stage'] = data['stage'][:80]
                    self.metrics['receiver_error'] = str(data.get('error', ''))[:160]
                    continue
                else:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                return await ws.close(1008, 'Conferma non valida')
            if self.sender and self.sender_protocol == 2:
                try:
                    await asyncio.wait_for(self.sender.send(json.dumps(reply)), self.ack_timeout)
                except (ConnectionClosed, asyncio.TimeoutError):
                    pass

    async def handle(self, ws):
        role = None
        try:
            if not self.valid_request(ws):
                return await ws.close(1008, 'Origine o indirizzo non consentito')
            try:
                hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if not isinstance(hello, dict):
                    raise ValueError()
                key, role = hello.get('key'), hello.get('role')
                if (not isinstance(key, str) or
                        not secrets.compare_digest(key, self.key) or
                        role not in ('sender', 'viewer', 'audio_sender', 'audio_viewer')):
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError):
                return await ws.close(1008, 'Accesso non consentito')
            if role in ('audio_sender', 'audio_viewer'):
                return await self.audio.handle(ws, hello)
            if getattr(self, role) is not None:
                return await ws.close(1008, 'Un dispositivo di questo tipo è già collegato')
            setattr(self, role, ws)
            protocol = 2 if hello.get('protocol') == 2 else 1
            setattr(self, role+'_protocol', protocol)
            if role == 'viewer':
                caps = hello.get('capabilities', {})
                self.viewer_info = {k: caps[k] for k in ('mse', 'avc', 'webm', 'userAgent')
                                    if isinstance(caps, dict) and k in caps and
                                    isinstance(caps[k], (bool, str)) and len(str(caps[k])) <= 256}
                if self.viewer_info.get('userAgent') in self.avc_failed_agents:
                    self.viewer_info['avc'] = False
            ready = {'type': 'ready'}
            if protocol == 2:
                ready.update(protocol=2, window=WINDOW, capabilities=self.viewer_info,
                             viewer_connected=self.viewer is not None and self.viewer_protocol == 2)
            await ws.send(json.dumps(ready))
            if role == 'viewer' and protocol == 2 and self.sender and self.sender_protocol == 2:
                await self.sender.send(json.dumps({'type': 'viewer', 'capabilities': self.viewer_info}))
            if protocol == 2:
                return await (self._sender_v2(ws) if role == 'sender' else self._viewer_v2(ws))
            async for message in ws:
                if role == 'viewer':
                    if message != 'ack' or self.ack is None or self.ack.done():
                        return await ws.close(1008, 'Conferma non valida')
                    self.ack.set_result(True)
                else:
                    if (not isinstance(message, bytes) or len(message) < 4 or
                            not message.startswith(b'\xff\xd8') or
                            not message.endswith(b'\xff\xd9')):
                        return await ws.close(1008, 'Fotogramma JPEG richiesto')
                    viewer = self.viewer
                    if viewer is None:
                        await ws.send('{"type":"waiting"}')
                        continue
                    self.ack = asyncio.get_running_loop().create_future()
                    try:
                        # One image in flight, including the TV decode/display.
                        # The sender cannot queue frames indefinitely: its socket
                        # receive queue is bounded too.
                        await asyncio.wait_for(viewer.send(message), self.ack_timeout)
                        await asyncio.wait_for(self.ack, self.ack_timeout)
                        await ws.send('{"type":"ack"}')
                    except (asyncio.TimeoutError, ConnectionClosed):
                        await viewer.close(1011, 'TV non risponde')
                        await ws.send('{"type":"waiting"}')
                    finally:
                        self.ack = None
        except ConnectionClosed:
            pass
        finally:
            if role in ('sender', 'viewer') and getattr(self, role) is ws:
                setattr(self, role, None)
                if role == 'viewer':
                    self.inflight.clear()
                    if self.sender and self.sender_protocol == 2:
                        try:
                            await self.sender.send('{"type":"waiting"}')
                        except ConnectionClosed:
                            pass
                if role == 'viewer' and self.ack and not self.ack.done():
                    self.ack.set_exception(asyncio.TimeoutError())
                if role == 'sender' and self.viewer:
                    self.inflight.clear()
                    try:
                        await asyncio.wait_for(self.viewer.send('stop'), self.ack_timeout)
                    except (ConnectionClosed, asyncio.TimeoutError):
                        pass
                if role == 'sender':
                    await self.audio.end_session()
