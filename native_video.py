"""TV-native DLNA transport. Media decoding happens in the TV, outside its browser."""
import urllib.request
import urllib.error
import collections
import queue
import secrets
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


class NativeTV:
    namespace = 'urn:schemas-upnp-org:service:AVTransport:1'

    def __init__(self, host, timeout=5):
        self.base = 'http://'+host+':9197'
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, action, **arguments):
        values = {'InstanceID': 0, **arguments}
        params = ''.join('<{0}>{1}</{0}>'.format(k, escape(str(v))) for k, v in values.items())
        body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                '<u:{0} xmlns:u="{1}">{2}</u:{0}></s:Body></s:Envelope>'.format(action,self.namespace,params))
        request = urllib.request.Request(self.base+'/upnp/control/AVTransport1',data=body.encode(),
            headers={'Content-Type':'text/xml; charset="utf-8"','SOAPAction':'"'+self.namespace+'#'+action+'"'})
        with self.opener.open(request,timeout=self.timeout) as response:
            root = ET.fromstring(response.read(65536))
        return {element.tag.split('}')[-1]: element.text or '' for element in root.iter() if not len(element)}

    def play(self, url, mime='video/mp4'):
        metadata = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="0" parentID="-1" restricted="1"><dc:title>Schermo PC</dc:title>'
            '<upnp:class>object.item.videoItem</upnp:class><res protocolInfo="http-get:*:'+mime+':*">'
            +escape(url)+'</res></item></DIDL-Lite>')
        self.call('SetAVTransportURI',CurrentURI=url,CurrentURIMetaData=metadata)
        # The player can still be switching applications after SetURI. Samsung
        # reports 701 until that transition completes; retry only that fault.
        for attempt in range(10):
            try:
                self.call('Play',Speed=1)
                return
            except urllib.error.HTTPError as exc:
                fault=exc.read(65536)
                try:
                    code=next((e.text for e in ET.fromstring(fault).iter() if e.tag.split('}')[-1]=='errorCode'),None)
                except ET.ParseError:code=None
                if code!='701' or attempt==9:raise
                time.sleep(.5)

    def stop(self):
        return self.call('Stop')

    def status(self):
        return self.call('GetTransportInfo')

    def position(self):
        return self.call('GetPositionInfo')


class NativeSession:
    """Bounded MP4 input -> lossless MPEG-TS remux -> one TV HTTP reader."""
    max_chunk = 4*1024*1024
    max_buffer = 48*1024*1024

    def __init__(self, tv, base_url, executable='ffmpeg', launcher=None):
        self.id = secrets.token_urlsafe(24)
        self.tv = tv
        self.launcher = launcher
        self.url = base_url+'/native/'+self.id+'/stream.ts'
        self.condition = threading.Condition()
        self.chunks = collections.deque()
        self.base = self.end = self.input_bytes = self.delivered = 0
        self.closed = False
        self.error = ''
        self.launched = False
        self.transport = 'BUFFERING'
        self.position = '0:00:00'
        self.video_frames = 0
        self.stream_fps = 0.0
        self.media_us = 0
        self.ts_tail = b''
        self.last_pts = None
        self.rate_pts = None
        self.rate_frames = 0
        self.popped = 0
        self.last_input = time.monotonic()
        self.inputs = queue.Queue(maxsize=8)
        self.process = subprocess.Popen([executable,'-hide_banner','-loglevel','error',
            '-probesize','65536','-analyzeduration','1000000','-i','pipe:0',
            '-map','0:v:0','-map','0:a:0?','-c','copy','-bsf:v','h264_mp4toannexb',
            '-mpegts_flags','+resend_headers','-pcr_period','20',
            '-pat_period','0.1','-muxdelay','0','-muxpreload','0',
            '-flush_packets','1','-stats_period','1','-progress','pipe:2','-f','mpegts','pipe:1'],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        for work in (self._write,self._read,self._play,self._progress):
            threading.Thread(target=work,daemon=True).start()

    def feed(self, data):
        if not data or len(data)>self.max_chunk:
            raise ValueError('Blocco video non valido')
        with self.condition:
            if self.closed: raise RuntimeError(self.error or 'Trasmissione terminata')
            self.last_input=time.monotonic()
            self.input_bytes+=len(data)
        try: self.inputs.put_nowait(data)
        except queue.Full:
            self.close('Invio video troppo veloce per il relay')
            raise RuntimeError(self.error)

    def _write(self):
        try:
            while not self.closed:
                try: data=self.inputs.get(timeout=1)
                except queue.Empty:
                    if time.monotonic()-self.last_input>12:
                        self.close('Nessun dato dal PC per 12 secondi')
                    continue
                view=memoryview(data)
                while view and not self.closed:
                    written=self.process.stdin.write(view)
                    if not written: raise OSError('Remuxer chiuso')
                    view=view[written:]
        except (OSError,ValueError):
            if not self.closed:self.close('Flusso video non leggibile dal relay')
        finally:
            try:self.process.stdin.close()
            except OSError:pass

    def _read(self):
        try:
            while not self.closed:
                data=self.process.stdout.read(65536)
                if not data:break
                self._measure_video(data)
                with self.condition:
                    self.chunks.append((self.end,data));self.end+=len(data)
                    while self.end-self.base>self.max_buffer:
                        offset,old=self.chunks.popleft();self.base=offset+len(old);self.popped+=1
                    self.condition.notify_all()
        finally:
            if not self.closed:self.close('Il remuxer ha terminato il flusso')
            self.process.stdout.close()

    def stream(self):
        offset=0;idx=0;seen_popped=0
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.closed or self.end>offset,timeout=2)
                if self.closed:return
                if offset<self.base:raise RuntimeError('Buffer TV superato')
                evicted=self.popped-seen_popped
                if evicted:
                    idx=max(0,idx-evicted)
                    seen_popped=self.popped
                while idx<len(self.chunks) and self.chunks[idx][0]+len(self.chunks[idx][1])<=offset:
                    idx+=1
                data=b''
                if idx<len(self.chunks):
                    start,chunk=self.chunks[idx]
                    if start<=offset<start+len(chunk):
                        data=chunk[offset-start:]
            if data:
                offset+=len(data);self.delivered=max(self.delivered,offset)
                yield data

    def _play(self):
        try:
            with self.condition:
                self.condition.wait_for(lambda:self.closed or self.end>0,timeout=12)
                if self.closed:return
                if not self.end:raise RuntimeError('Nessun video dal codificatore')
                self.condition.wait_for(lambda:self.closed or (self.video_frames>0 and self.end>=524288),timeout=1.5)
            if self.closed:return
            if self.launcher:
                self.launcher(self.url)
                self.transport='APP_REQUESTED'
                return
            self.tv.play(self.url,mime='video/mpeg');self.launched=True
            started=time.monotonic()
            while not self.closed:
                self.transport=self.tv.status().get('CurrentTransportState','UNKNOWN')
                self.position=self.tv.position().get('RelTime','0:00:00')
                if self.transport in ('STOPPED','NO_MEDIA_PRESENT') and time.monotonic()-started>12:
                    raise RuntimeError('Il lettore ha interrotto il video')
                time.sleep(1)
        except RuntimeError as exc:
            if not self.closed:self.close(str(exc))
        except Exception:
            if not self.closed:self.close('Il lettore nativo della TV non risponde')
        finally:
            if self.launched:
                try:
                    # Do not stop a different program selected with the remote.
                    if self.tv.position().get('TrackURI')==self.url:self.tv.stop()
                except Exception:pass

    def _progress(self):
        try:
            for raw in self.process.stderr:
                key,separator,value=raw.decode('ascii',errors='ignore').strip().partition('=')
                if not separator:continue
                try:
                    if key=='out_time_us':
                        current=int(value)
                        self.media_us=current
                except ValueError:pass
        finally:self.process.stderr.close()

    def _measure_video(self, data):
        # FFmpeg progress out_time follows whichever track is ahead (often
        # audio). Count video PES timestamps instead, over >=5 seconds of media.
        buf=self.ts_tail+data if self.ts_tail else data
        complete=len(buf)//188*188
        self.ts_tail=bytes(buf[complete:]) if complete<len(buf) else b''
        for offset in range(0,complete,188):
            if buf[offset]!=0x47 or not buf[offset+1]&0x40:continue
            adaptation=(buf[offset+3]>>4)&3
            if adaptation not in (1,3):continue
            pos=offset+4
            if adaptation==3:pos+=1+buf[pos]
            if pos+14>offset+188:continue
            if buf[pos]!=0 or buf[pos+1]!=0 or buf[pos+2]!=1:continue
            if not 0xe0<=buf[pos+3]<=0xef or not buf[pos+7]&0x80:continue
            pts=((buf[pos+9]&14)<<29)|(buf[pos+10]<<22)|((buf[pos+11]&254)<<14)|(buf[pos+12]<<7)|(buf[pos+13]>>1)
            if pts==self.last_pts:continue
            self.last_pts=pts;self.video_frames+=1
            if self.rate_pts is None:self.rate_pts=pts;self.rate_frames=self.video_frames
            elapsed=(pts-self.rate_pts)%(1<<33)
            if 450000<=elapsed<9000000:
                self.stream_fps=round((self.video_frames-self.rate_frames)*90000/elapsed,1)
                self.rate_pts=pts;self.rate_frames=self.video_frames
            elif elapsed>=9000000:
                self.rate_pts=pts;self.rate_frames=self.video_frames

    def close(self, error=''):
        with self.condition:
            if self.closed:return
            self.closed=True;self.error=error;self.condition.notify_all()
        try:
            self.process.terminate();self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill();self.process.wait(timeout=2)
        except OSError:pass
        while True:
            try:self.inputs.get_nowait()
            except queue.Empty:break

    def snapshot(self):
        return {'active':not self.closed,'error':self.error,'state':self.transport,
                'position':self.position,'input_bytes':self.input_bytes,
                'output_bytes':self.end,'delivered_bytes':self.delivered,
                'video_frames':self.video_frames,'stream_fps':self.stream_fps}


class NativeCast:
    def __init__(self, host):
        self.tv=NativeTV(host)
        self.host=host
        self.lock=threading.Lock()
        self.session=None

    @property
    def available(self):return bool(shutil.which('ffmpeg'))

    def start(self, base_url, launcher=None):
        with self.lock:
            if self.session and not self.session.closed:
                raise RuntimeError('Una trasmissione nativa è già attiva')
            if not self.available:raise RuntimeError('Installa FFmpeg sul Raspberry per il video nativo')
            self.session=NativeSession(self.tv,base_url,launcher=launcher)
            return self.session

    def get(self, identifier):
        with self.lock:
            if not self.session or not secrets.compare_digest(self.session.id,identifier):
                raise ValueError('Sessione video non valida')
            return self.session

    def stop(self):
        with self.lock:
            if self.session:self.session.close()
