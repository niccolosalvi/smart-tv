"""Independent, bounded audio channel. Forward compressed packets; never transcode."""
import asyncio
import json
import math
import struct
import time

from websockets.exceptions import ConnectionClosed

AUDIO_WINDOW = 3
AUDIO_MAX_PACKET = 16384


def audio_packet(message, config):
    if not isinstance(message, bytes) or not 24 < len(message) <= AUDIO_MAX_PACKET:
        raise ValueError('Pacchetto audio non valido')
    magic, seq, pts, samples, preroll, kind = struct.unpack_from('!4sIQIHH', message)
    rate, channels = config['sampleRate'], config['channels']
    if magic != b'AU1\0' or not 0 < samples <= rate//5 or preroll > 2048 or pts > 2**53:
        raise ValueError('Tempi audio non validi')
    if config['codec'] == 'aac':
        if kind != 1 or samples % 1024 or preroll not in (0, 1024):
            raise ValueError('Campioni AAC non validi')
        offset, count = 24, 0
        while offset < len(message):
            data = message[offset:]
            if len(data) < 7 or data[0] != 255 or data[1] & 0xf6 != 0xf0:
                raise ValueError('ADTS non valido')
            # AAC-LC, 48 kHz, fixed negotiated channel count, one raw block.
            if data[2] >> 6 != 1 or (data[2] >> 2) & 15 != 3 or data[6] & 3:
                raise ValueError('Profilo AAC non valido')
            if ((data[2] & 1) << 2 | data[3] >> 6) != channels:
                raise ValueError('Canali AAC non validi')
            size = (data[3] & 3) << 11 | data[4] << 3 | data[5] >> 5
            if size < 7 or offset+size > len(message):
                raise ValueError('Lunghezza ADTS non valida')
            offset += size
            count += 1
        if count*1024 != samples+preroll:
            raise ValueError('Durata ADTS non valida')
    else:
        if kind != 2 or preroll or len(message) != 24+channels*4+(samples*channels+1)//2:
            raise ValueError('Blocco ADPCM non valido')
        for channel in range(channels):
            if message[24+channel*4+2] > 88 or message[24+channel*4+3]:
                raise ValueError('Stato ADPCM non valido')
    return seq


class AudioRelay:
    def __init__(self, ack_timeout=2):
        self.sender = self.viewer = None
        self.config = None
        self.capabilities = {}
        self.inflight = {}
        self.metrics = {}
        self.ack_timeout = ack_timeout

    def snapshot(self):
        return {**self.metrics, 'sender_connected': self.sender is not None,
                'receiver_connected': self.viewer is not None}

    async def end_session(self):
        for peer in (self.sender, self.viewer):
            if peer:
                try:
                    await peer.send('{"type":"stop"}')
                    await peer.close(1000, 'Audio terminato')
                except ConnectionClosed:
                    pass
        self.inflight.clear()
        self.config = None

    async def watch(self, ws):
        while self.sender is ws:
            await asyncio.sleep(.25)
            if self.inflight and time.monotonic()-min(self.inflight.values()) > self.ack_timeout:
                self.inflight.clear()
                if self.config and self.config['codec'] == 'aac':
                    await ws.send('{"type":"fallback","reason":"Decoder audio non risponde"}')
                elif self.viewer:
                    await self.viewer.close(1011, 'Ricevitore audio non risponde')

    async def handle(self, ws, hello):
        role = 'sender' if hello['role'] == 'audio_sender' else 'viewer'
        if getattr(self, role) is not None:
            return await ws.close(1008, 'Audio già collegato')
        setattr(self, role, ws)
        watcher = None
        try:
            if role == 'viewer':
                caps = hello.get('capabilities', {})
                self.capabilities = {'webAudio': isinstance(caps, dict) and caps.get('webAudio') is True,
                                     'aac': isinstance(caps, dict) and caps.get('aac', True) is True}
            await ws.send(json.dumps({'type': 'ready', 'window': AUDIO_WINDOW,
                                     'viewer_connected': self.viewer is not None,
                                     'capabilities': self.capabilities}))
            if role == 'viewer' and self.sender:
                await self.sender.send(json.dumps({'type': 'viewer', 'capabilities': self.capabilities}))
            if role == 'sender':
                watcher = asyncio.create_task(self.watch(ws))
            async for message in ws:
                try:
                    if role == 'sender' and isinstance(message, bytes):
                        if not self.config:
                            raise ValueError('Configura prima l’audio')
                        seq = audio_packet(message, self.config)
                        if not self.viewer:
                            await ws.send('{"type":"waiting"}')
                            continue
                        if len(self.inflight) >= AUDIO_WINDOW or seq in self.inflight:
                            raise ValueError('Coda audio superata')
                        self.inflight[seq] = time.monotonic()
                        self.metrics['packets'] = self.metrics.get('packets', 0)+1
                        self.metrics['bytes'] = self.metrics.get('bytes', 0)+len(message)
                        await asyncio.wait_for(self.viewer.send(message), self.ack_timeout)
                        continue
                    if not isinstance(message, str) or len(message) > 1024:
                        raise ValueError('Controllo audio non valido')
                    data = json.loads(message)
                    if role == 'sender' and data.get('type') == 'configure':
                        codec, rate, channels = (data.get(k) for k in ('codec', 'sampleRate', 'channels'))
                        if (codec not in ('aac', 'ima') or type(rate) is not int or
                                rate != (48000 if codec == 'aac' else 24000) or
                                type(channels) is not int or channels not in (1, 2)):
                            raise ValueError('Configurazione audio non valida')
                        self.config = {'type': 'configure', 'codec': codec, 'sampleRate': rate, 'channels': channels}
                        self.inflight.clear()
                        self.metrics = {'codec': codec, 'sample_rate': rate, 'channels': channels,
                                        'packets': 0, 'bytes': 0, 'buffer_ms': 0}
                        if self.viewer:
                            await self.viewer.send(json.dumps(self.config))
                    elif role == 'sender' and data.get('type') == 'capture':
                        level=data.get('inputRms')
                        if (data.get('surface') not in ('monitor','browser','window','unknown') or
                                type(data.get('muted')) is not bool or type(level) not in (int,float) or
                                not math.isfinite(level) or not 0 <= level <= 1):
                            raise ValueError('Livello cattura non valido')
                        self.metrics.update(source=data['surface'],input_rms=round(level,6),track_muted=data['muted'])
                    elif role == 'viewer' and data.get('type') == 'ack':
                        if type(data.get('seq')) is not int:
                            raise ValueError('ACK audio non valido')
                        sent = self.inflight.pop(data['seq'], None)
                        if sent is None:
                            continue
                        reply = {'type': 'ack', 'seq': data['seq']}
                        for key, maximum in [('bufferMs', 2000), ('contextTime', 1e9),
                                             ('playedSamples', 1e15), ('rms', 1)]:
                            value = data.get(key, 0)
                            if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= maximum:
                                raise ValueError('Misura audio non valida')
                            reply[key] = round(value, 5)
                        self.metrics.update(buffer_ms=reply['bufferMs'], context_time=reply['contextTime'],
                                            played_samples=reply['playedSamples'], rms=reply['rms'],
                                            ack_ms=round((time.monotonic()-sent)*1000, 1),
                                            state=str(data.get('state', 'unknown'))[:20])
                        if self.sender:
                            await self.sender.send(json.dumps(reply))
                    elif role == 'viewer' and data.get('type') == 'fallback':
                        self.inflight.clear()
                        self.metrics['error'] = 'AAC non decodificabile; uso ADPCM'
                        if self.sender:
                            await self.sender.send('{"type":"fallback"}')
                    elif role == 'sender' and data.get('type') == 'stop':
                        self.config = None
                        self.inflight.clear()
                        if self.viewer:
                            await self.viewer.send('{"type":"stop"}')
                    else:
                        raise ValueError('Messaggio audio non consentito')
                except (ValueError, TypeError, AttributeError, KeyError, struct.error) as error:
                    self.metrics['error'] = role+': '+str(error)[:120]
                    return await ws.close(1008, 'Audio non valido')
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        finally:
            if watcher:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            if getattr(self, role) is ws:
                setattr(self, role, None)
                if role == 'sender':
                    self.config = None
                self.inflight.clear()
                peer = self.viewer if role == 'sender' else self.sender
                if peer:
                    try:
                        await peer.send('{"type":"stop"}' if role == 'sender' else '{"type":"waiting"}')
                    except ConnectionClosed:
                        pass
