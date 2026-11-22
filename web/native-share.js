/* Native TV video/audio playback; the Pi remuxes without decoding the media. */
'use strict';
class ScreenNativeSender{
 constructor(config,onStatus,onError){
  this.config=config;this.onStatus=onStatus;this.onError=onError;
  this.id=null;this.recorder=null;this.stopped=false;this.queued=0;this.chain=Promise.resolve();this.timer=null;
 }
 async request(path,body,keepalive=false){
  const response=await fetch(this.config.server+path,{method:'POST',
   headers:{'X-Remote-Key':this.config.key,'Content-Type':body instanceof Blob?'video/mp4':'application/json'},
   body,keepalive,signal:keepalive?undefined:AbortSignal.timeout(10000)});
  const data=await response.json();if(!response.ok)throw Error(data.error||'Relay video non disponibile');return data;
 }
 async start(stream,options){
  const types=['video/mp4;codecs=avc1.64002A,mp4a.40.2','video/mp4;codecs=avc1.42E02A,mp4a.40.2'];
  if(typeof MediaRecorder==='undefined')throw Error('Il browser non offre la codifica video nativa. Usa Chrome o Edge sul PC.');
  const mime=types.find(type=>MediaRecorder.isTypeSupported(type));
  if(!mime)throw Error('H.264/AAC non disponibile in questo browser. Usa Chrome o Edge sul PC.');
  const result=await this.request('/api/native/start',JSON.stringify(options.receiver==='tizen'?{receiver:'tizen'}:{}));this.id=result.id;
  if(this.stopped){await this.request('/api/native/'+this.id+'/stop','{}');return;}
  const track=stream.getVideoTracks()[0],settings=track.getSettings();
  const w=settings.width||options.width,h=settings.height||Math.round(options.width*9/16),fps=Math.round(settings.frameRate||options.fps);
  this.label=w+'×'+h+' · cattura '+fps+' immagini/s';
  const videoBps=Math.round(Math.max(2500000,Math.min(12000000,w*h*fps*.10)));
  this.recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:videoBps,audioBitsPerSecond:128000,videoKeyFrameIntervalDuration:1000});
  this.recorder.ondataavailable=event=>{
   if(this.stopped||!event.data.size)return;
   if(this.queued+event.data.size>8*1024*1024){this.fail(Error('La rete non tiene il passo del video. Riduci la frequenza o la risoluzione.'));return;}
   this.queued+=event.data.size;
   this.chain=this.chain.then(async()=>{
    try{
     // Recorder delivery boundaries need not match the requested timeslice.
     // Split large keyframes while preserving the original MP4 byte order.
     for(let offset=0;offset<event.data.size&&!this.stopped;offset+=512*1024)
      await this.request('/api/native/'+this.id+'/chunk',event.data.slice(offset,offset+512*1024));
    }
    finally{this.queued-=event.data.size;}
   }).catch(error=>this.fail(error));
  };
  this.recorder.onerror=event=>this.fail(event.error||Error('Codifica video interrotta'));
  this.recorder.onstop=()=>{if(!this.stopped)this.fail(Error('Il codificatore ha terminato il video'));};
  this.recorder.start(250);
  this.onStatus('Video nativo · '+this.label+' · preparazione del buffer…');
  this.timer=setInterval(()=>this.poll(),1000);
 }
 async poll(){
  if(this.stopped||this.polling)return;this.polling=true;
  try{
   const response=await fetch(this.config.server+'/api/native/'+this.id,{headers:{'X-Remote-Key':this.config.key},signal:AbortSignal.timeout(5000)});
   if(!response.ok)throw Error('Stato video nativo non disponibile');
   const data=await response.json();if(this.stopped)return;
   if(data.error||!data.active)throw Error(data.error||'Trasmissione nativa terminata');
   this.onStatus('Video nativo · '+this.label+(data.stream_fps?' · flusso '+data.stream_fps+' immagini/s':'')+' · TV '+data.state);
  }catch(error){if(!this.stopped)this.fail(error);}finally{this.polling=false;}
 }
 fail(error){if(this.stopped)return;this.onError(error);}
 async stop(keepalive=false){
  if(this.stopped)return;this.stopped=true;clearInterval(this.timer);
  if(this.recorder&&this.recorder.state!=='inactive')this.recorder.stop();
  if(this.id)await this.request('/api/native/'+this.id+'/stop','{}',keepalive);
 }
}
