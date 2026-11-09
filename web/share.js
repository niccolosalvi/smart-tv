'use strict';
const config=JSON.parse(document.getElementById('share-config').textContent);
const $=id=>document.getElementById(id),message=text=>$('status').textContent=text;
const target=new URL(config.server),wsUrl=config.ws_url||('ws://'+target.hostname+':'+config.port+'/screen');
$('receiver').href=config.receiver_url||config.server+'/screen';$('receiver').textContent=$('receiver').href;
$('back').href=config.server+'/';$('secure-link').href=config.https_url||config.server+'/share';
let stream=null,socket=null,reader=null,worker=null,running=false,generation=0;
let windowSize=3,reservations=0,nextSeq=1,codec='',closePromise=null,needClose=false,forceJPEG=false;
let pending=new Map(),statsTimer=null,statsTime=0,frames=0,bytes=0,acks=0,rtt=0,bufferMs=0,captureMode='';
let encoderGeneration=0;
let openingPromise=null,connectTimer=null;
let audioSender=null;
const supported=window.isSecureContext&&navigator.mediaDevices&&navigator.mediaDevices.getDisplayMedia&&window.Worker&&window.VideoFrame;
$('open-tv').disabled=!config.available;
async function tvCommand(action,keepalive=false){
 const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Remote-Key':config.key},body:JSON.stringify({screen:action}),keepalive,signal:keepalive?undefined:AbortSignal.timeout(25000)});
 const result=await response.json();if(!response.ok)throw Error(result.error||'Richiesta rifiutata');return result;
}
async function openTV(){
 if(openingPromise)return openingPromise;
 $('open-tv').disabled=true;$('tv-status').textContent='Apertura ricevitore sulla TV…';
 openingPromise=tvCommand('open').then(()=>{$('tv-status').textContent='Apertura richiesta. In attesa del collegamento della TV.';})
  .catch(error=>{$('tv-status').textContent='Apertura non confermata: '+error.message;})
  .finally(()=>{openingPromise=null;$('open-tv').disabled=false;});
 return openingPromise;
}
$('open-tv').onclick=openTV;
if(!config.available)message('Relay non disponibile. Ricarica la pagina.');
else if(!supported){
 $('launch').hidden=false;
 if(window.isSecureContext){$('secure-link').hidden=true;$('secure-note').textContent='Apri il telecomando con Chrome o Edge aggiornato sul PC.';}
 else if(!config.https_url){$('secure-link').hidden=true;$('secure-note').textContent='Per condividere è necessario configurare HTTPS sul servizio.';}
 message('Condivisione non disponibile in questa pagina.');
}else{$('start').disabled=false;message('Premi Condividi e scegli cosa mostrare sulla TV.');}
function destroyWorker(){encoderGeneration++;if(worker){worker.terminate();worker=null;}reservations=0;pending.clear();}
function stop(text,keepalive=false){
 const close=needClose;needClose=false;generation++;running=false;clearInterval(statsTimer);clearTimeout(connectTimer);destroyWorker();
 if(audioSender){audioSender.stop();audioSender=null;}
 if(reader){reader.cancel().catch(()=>{});reader=null;}
 if(stream){stream.getTracks().forEach(track=>{track.onended=null;track.stop();});stream=null;}
 if(socket){socket.onclose=socket.onerror=socket.onmessage=null;socket.close();socket=null;}
 $('preview').srcObject=null;$('preview').hidden=true;$('stop').disabled=true;
 $('width').disabled=$('fps').disabled=$('transport').disabled=false;
 $('audio-enabled').disabled=$('audio-quality').disabled=false;
 message(text||'Condivisione terminata.');
 if(close&&!closePromise){
  $('start').disabled=true;$('tv-status').textContent='Chiusura del ricevitore e del browser sulla TV…';
  closePromise=Promise.resolve(openingPromise).then(()=>tvCommand('stop',keepalive)).then(()=>{$('tv-status').textContent='Uscita dal browser richiesta alla TV.';})
   .catch(error=>{$('tv-status').textContent='Chiusura TV non confermata: '+error.message;})
   .finally(()=>{closePromise=null;$('start').disabled=!supported||!config.available;});
 }else $('start').disabled=!!closePromise||!supported||!config.available;
}
function packet(data){
 if(!socket||socket.readyState!==WebSocket.OPEN)return;
 if(pending.size>=windowSize){stop('Ricevitore congestionato. Riprova con una larghezza inferiore.');return;}
 const content=data.data,buffer=new ArrayBuffer(32+content.byteLength),view=new DataView(buffer);
 new Uint8Array(buffer).set([83,86,50,0]);view.setUint8(4,data.kind);view.setUint8(5,data.key?1:0);
 const count=data.kind===1?0:(data.count||1);view.setUint16(6,count);
 const seq=nextSeq++;view.setUint32(8,seq);view.setUint16(12,data.width);view.setUint16(14,data.height);
 data.rect.forEach((value,index)=>view.setUint16(16+index*2,value));
 new Uint8Array(buffer,32).set(content);
 pending.set(seq,{sent:performance.now(),kind:data.kind,count});bytes+=buffer.byteLength;socket.send(buffer);
}
function beginEncoding(capabilities){
 if(!running)return;destroyWorker();
 const currentGeneration=generation;
 const currentEncoder=encoderGeneration;
 codec=!forceJPEG&&capabilities.avc&&typeof VideoEncoder==='function'?'h264':'jpeg';
 worker=new Worker('/assets/share-worker.js');
 worker.onmessage=event=>{
  if(currentGeneration!==generation||currentEncoder!==encoderGeneration||!running)return;
  const data=event.data;
  if(data.type==='configure'){
   codec=data.codec;pending.clear();socket.send(JSON.stringify(data));
  }else if(data.type==='packet')packet(data);
  else if(data.type==='processed')reservations=Math.max(0,reservations-1);
  else if(data.type==='mode')codec=data.mode;
 };
 worker.onerror=()=>stop('Codifica non disponibile nel browser. Ricarica la pagina.');
 worker.postMessage({type:'start',codec,width:Number($('width').value),fps:Number($('fps').value)});
 message((codec==='h264'?'Video H.264':'Immagini adattive')+' · collegamento alla TV…');
}
function push(frame){
 // AVC may emit an init packet plus media from one output callback. Images
 // produce exactly one packet, so can use the whole bounded network window.
 const credits=codec==='h264'?windowSize-1:windowSize;
 if(!running||!worker||reservations>=2||pending.size+reservations>=credits||socket.bufferedAmount>512*1024){frame.close();return;}
 reservations++;worker.postMessage({type:'frame',frame},[frame]);
}
async function readFrames(epoch){
 if(typeof MediaStreamTrackProcessor==='function'){
  captureMode='';const processor=new MediaStreamTrackProcessor({track:stream.getVideoTracks()[0],maxBufferSize:1});
  const activeReader=processor.readable.getReader();reader=activeReader;
  try{
   while(epoch===generation){const item=await activeReader.read();if(item.done)break;if(epoch!==generation){item.value.close();break;}push(item.value);}
  }catch(error){if(epoch===generation)stop('Acquisizione interrotta: '+error.message);}
  finally{try{activeReader.releaseLock();}catch(ignored){}}
 }else{
  captureMode=' · tieni attiva questa scheda';
  const video=$('preview');
  video.closest('details').open=true;
  function next(now,metadata){
   if(epoch!==generation)return;
   if(video.videoWidth)push(new VideoFrame(video,{timestamp:Math.round((metadata?metadata.mediaTime:now/1000)*1000000)}));
   if(video.requestVideoFrameCallback)video.requestVideoFrameCallback(next);else requestAnimationFrame(next);
  }
  if(video.requestVideoFrameCallback)video.requestVideoFrameCallback(next);else requestAnimationFrame(next);
 }
}
function updateStats(){
 if(!running)return;const now=performance.now(),elapsed=(now-statsTime)/1000;
 if(pending.size&&now-Math.min(...[...pending.values()].map(item=>item.sent))>10000){stop('La TV non risponde. Riprova la condivisione.');return;}
 if(!worker)return;
 message((codec==='h264'?'H.264':'Immagini adattive')+' · '+(frames/elapsed).toFixed(1)+' immagini/s ricevute · '+(bytes*8/elapsed/1000000).toFixed(2)+' Mb/s video · conferma TV '+Math.round(acks?rtt/acks:0)+' ms'+(codec==='h264'?' · buffer '+Math.round(bufferMs)+' ms':'')+captureMode);
 frames=bytes=acks=rtt=0;statsTime=now;
}
$('start').onclick=async()=>{
 const epoch=++generation;$('start').disabled=true;message('Scegli cosa condividere nel browser.');
 try{
  const fps=Number($('fps').value),width=Number($('width').value);
  const wantAudio=$('audio-enabled').checked,voice=$('audio-quality').value==='voice';
  if(wantAudio)audioSender=new ScreenAudioSender({key:config.key,ws_url:wsUrl},{channels:voice?1:2,bitrate:voice?64000:128000},text=>{$('audio-status').textContent=text;});
  else $('audio-status').textContent='Audio disattivato.';
  const captured=await navigator.mediaDevices.getDisplayMedia({video:{frameRate:{ideal:fps,max:fps},width:{ideal:width,max:width},height:{max:720}},audio:wantAudio?{echoCancellation:false,noiseSuppression:false,autoGainControl:false,suppressLocalAudioPlayback:true}:false,systemAudio:wantAudio?'include':'exclude'});
  if(epoch!==generation){captured.getTracks().forEach(track=>track.stop());return;}
  stream=captured;needClose=true;forceJPEG=$('transport').value==='jpeg';
  const track=stream.getVideoTracks()[0];try{track.contentHint='detail';}catch(ignored){}
  track.onended=()=>stop();$('stop').disabled=false;$('width').disabled=$('fps').disabled=$('transport').disabled=true;
  $('audio-enabled').disabled=$('audio-quality').disabled=true;
  if(audioSender)audioSender.start(stream.getAudioTracks()[0]);
  $('preview').hidden=false;$('preview').srcObject=stream;await $('preview').play();if(epoch!==generation)return;
  const response=await fetch('/api/share');const live=await response.json();
  if(epoch!==generation)return;
  if(!live.receiver_connected)await openTV();if(epoch!==generation)return;
  socket=new WebSocket(wsUrl);
  connectTimer=setTimeout(()=>stop('Il collegamento al Raspberry non risponde.'),12000);
  socket.onopen=()=>socket.send(JSON.stringify({role:'sender',key:config.key,protocol:2}));
  socket.onmessage=event=>{
   if(epoch!==generation)return;clearTimeout(connectTimer);const data=JSON.parse(event.data);
   if(data.type==='ready'){
    running=true;windowSize=data.window||3;statsTime=performance.now();frames=bytes=acks=rtt=0;
    statsTimer=setInterval(updateStats,1000);readFrames(epoch);
    if(data.viewer_connected)beginEncoding(data.capabilities||{});else message('In attesa della TV…');
   }else if(data.type==='viewer')beginEncoding(data.capabilities||{});
   else if(data.type==='waiting'){destroyWorker();message('In attesa della TV. Premi Apri sulla TV.');}
   else if(data.type==='fallback'){forceJPEG=true;beginEncoding({});}
   else if(data.type==='ack'){
    const sent=pending.get(data.seq);if(!sent)return;
    const delay=performance.now()-sent.sent;pending.delete(data.seq);acks++;rtt+=delay;bufferMs=data.bufferMs||0;
    frames+=sent.count;if(worker)worker.postMessage({type:'feedback',rtt:delay,bufferMs});
   }
  };
  socket.onerror=()=>stop('Collegamento al Raspberry non riuscito.');
  socket.onclose=event=>stop('Connessione chiusa. '+(event.reason||'Ricarica la pagina e riprova.'));
 }catch(error){if(epoch===generation)stop('Cattura non avviata: '+error.message);}
};
$('stop').onclick=()=>stop();window.addEventListener('pagehide',()=>stop(undefined,true));
