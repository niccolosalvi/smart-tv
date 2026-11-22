'use strict';
const config=JSON.parse(document.getElementById('share-config').textContent);
const $=id=>document.getElementById(id);
function message(text){$('status').textContent=text;}
const target=new URL(config.server),wsUrl=config.ws_url||('ws://'+target.hostname+':'+config.port+'/screen');
$('receiver').href=config.receiver_url||config.server+'/screen';$('receiver').textContent=$('receiver').href;
$('secure-link').href=config.https_url||config.server+'/share';
const homeUrl=target.origin===location.origin?'/':config.server+'/';
$('back').href=homeUrl;
let leaving=false;
if(target.origin!==location.origin){
 // The optional legacy localhost launcher opens the LAN dashboard separately.
 $('back').target='_blank';$('back').rel='noopener';
}
let stream=null,socket=null,reader=null,worker=null,running=false,generation=0;
let windowSize=3,reservations=0,nextSeq=1,codec='',closePromise=null,needClose=false,forceJPEG=false;
let pending=new Map(),statsTimer=null,statsTime=0,frames=0,bytes=0,acks=0,rtt=0,bufferMs=0,captureMode='';
let encoderGeneration=0,avcInitSent=false,latestFrame=null;
let encodedWidth=0,encodedHeight=0;
let openingPromise=null,connectTimer=null;
let audioSender=null;
let auxiliaryAudio=null,pendingAudioSender=null,selectingAudio=false;
let nativeSender=null;
async function returnToRemote(event){
 if(event&&event.preventDefault)event.preventDefault();
 if(leaving)return;
 leaving=true;
 const back=$('back');
 if(back&&back.setAttribute)back.setAttribute('aria-disabled','true');
 $('start').disabled=$('stop').disabled=true;
 // Clear needClose before stop() so returning to TV antenna does not also race
 // a screen:stop command against /api/command's single-command lock.
 needClose=false;
 stop('Ritorno al telecomando e alla TV antenna…');
 if(closePromise)try{await closePromise;}catch(ignored){}
 try{
  await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Remote-Key':config.key},body:JSON.stringify({key:'KEY_TV'}),signal:AbortSignal.timeout(8000)});
 }catch(ignored){}
 location.href=homeUrl;
}
if(target.origin===location.origin)$('back').addEventListener('click',returnToRemote);
const captureAudioOptions={echoCancellation:false,noiseSuppression:false,autoGainControl:false,
 suppressLocalAudioPlayback:true,restrictOwnAudio:false};
function clearLatest(){if(latestFrame){latestFrame.close();latestFrame=null;}}
const preview=$('preview');
function syncPreview(){
 if(!preview)return;
 const needVideo=!!stream&&typeof MediaStreamTrackProcessor!=='function';
 if(needVideo){
  preview.hidden=false;
  if(preview.srcObject!==stream){preview.srcObject=stream;if(preview.play)preview.play().catch(()=>{});}
 }else{
  if(preview.pause)try{preview.pause();}catch(ignored){}
  preview.srcObject=null;preview.hidden=true;
 }
}
let volumePending=false;
async function sendVolumeCommand(payload){
 if(volumePending||target.origin!==location.origin)return;
 volumePending=true;
 const buttons=document.querySelectorAll?document.querySelectorAll('.volume-row button'):[];
 buttons.forEach(b=>{b.disabled=true;});
 try{
  const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Remote-Key':config.key},body:JSON.stringify(payload),signal:AbortSignal.timeout(10000)});
  const data=await response.json();
  if(!response.ok)throw Error(data.error||'Comando rifiutato');
 }catch(error){
  $('tv-status').textContent='Volume TV: '+error.message;
 }finally{
  volumePending=false;
  buttons.forEach(b=>{b.disabled=false;});
 }
}
if(document.addEventListener)document.addEventListener('click',event=>{
 const btn=event.target&&event.target.closest?event.target.closest('button[data-volume],button[data-key]'):null;
 if(!btn)return;
 if(btn.dataset.volume)sendVolumeCommand({volume:Number(btn.dataset.volume)});
 else if(btn.dataset.key)sendVolumeCommand({key:btn.dataset.key});
});
function audioOptions(onStop){const voice=$('audio-quality').value==='voice';return {channels:voice?1:2,bitrate:voice?64000:128000,onStop};}
function audioStatus(text){$('audio-status').textContent=text;}
function stopTracks(captured){if(captured)captured.getTracks().forEach(track=>track.stop());}
const supported=window.isSecureContext&&navigator.mediaDevices&&navigator.mediaDevices.getDisplayMedia&&window.Worker&&window.VideoFrame;
if((!config.native_available||target.origin!==location.origin)&&$('transport').value==='native')$('transport').value='auto';
for(const option of ($('transport').options||[]))if(option.value==='native')option.disabled=!config.native_available||target.origin!==location.origin;
for(const option of ($('transport').options||[]))if(option.value==='native-app')option.disabled=!config.native_available||!config.tizen_receiver_available||target.origin!==location.origin;
const MODE_HINTS={
 native:'Usa il lettore della TV con audio sincronizzato (~10–12 s di buffer per film/video).',
 'native-app':'Usa l’app Tizen dedicata (richiede installazione e flag SMART_TV_TIZEN_RECEIVER=1).',
 auto:'Sceglie H.264 se supportato o immagini adattive su Samsung 2016; latenza bassa.',
 jpeg:'Invia solo le aree modificate in JPEG/PNG (fino a 30 fps); ideale per desktop e testo.'
};
const MODE_LABELS={native:'Video nativo TV','native-app':'Ricevitore Tizen',auto:'Automatica',jpeg:'Compatibile'};
const useNativeDefault=!!(config.native_available&&target.origin===location.origin);
function syncModeUI(){
 const mode=$('transport').value;
 $('mode-hint').textContent=MODE_HINTS[mode]||'';
 $('video-summary').textContent=(MODE_LABELS[mode]||mode)+' · '+$('width').value+' px · '+$('fps').value+' fps';
 $('audio-summary').textContent=$('audio-enabled').checked?('Attivo · '+($('audio-quality').value==='voice'?'Voce mono':'Stereo')):'Disattivato';
 const cinemaMatch=mode===(useNativeDefault?'native':'auto')&&$('video-profile').value==='cinema'&&$('width').value==='1920'&&$('fps').value==='60';
 const desktopMatch=mode==='auto'&&$('video-profile').value==='balanced'&&$('width').value==='1280'&&$('fps').value==='30';
 for(const [id,active] of [['preset-cinema',cinemaMatch],['preset-desktop',desktopMatch]]){
  const btn=$(id);if(!btn)continue;
  if(btn.classList)btn.classList.toggle('active',active);
  if(btn.setAttribute)btn.setAttribute('aria-pressed',active?'true':'false');
 }
}
function applyPreset(kind){
 if(running)return;
 if(kind==='cinema'){
  $('transport').value=useNativeDefault?'native':'auto';
  $('video-profile').value='cinema';$('width').value='1920';$('fps').value='60';
 }else{
  $('transport').value='auto';
  $('video-profile').value='balanced';$('width').value='1280';$('fps').value='30';
 }
 syncModeUI();
}
$('preset-cinema').onclick=()=>applyPreset('cinema');
$('preset-desktop').onclick=()=>applyPreset('desktop');
for(const id of ['transport','video-profile','width','fps','audio-enabled','audio-quality'])$(id).addEventListener('change',syncModeUI);
syncModeUI();
async function tvCommand(action,keepalive=false){
 const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Remote-Key':config.key},body:JSON.stringify({screen:action}),keepalive,signal:keepalive?undefined:AbortSignal.timeout(25000)});
 const result=await response.json();if(!response.ok)throw Error(result.error||'Richiesta rifiutata');return result;
}
async function openTV(){
 if(openingPromise)return openingPromise;
 $('tv-status').textContent='Apertura ricevitore sulla TV…';
 openingPromise=tvCommand('open').then(()=>{$('tv-status').textContent='Apertura richiesta. In attesa del collegamento della TV.';})
  .catch(error=>{$('tv-status').textContent='Apertura non confermata: '+error.message;})
  .finally(()=>{openingPromise=null;});
 return openingPromise;
}
if(!config.available)message('Relay non disponibile. Ricarica la pagina.');
else if(!supported){
 $('launch').hidden=false;
 if(window.isSecureContext){$('secure-link').hidden=true;$('secure-note').textContent='Apri il telecomando con Chrome o Edge aggiornato sul PC.';}
 else if(!config.https_url){$('secure-link').hidden=true;$('secure-note').textContent='Per condividere è necessario configurare HTTPS sul servizio.';}
 message('Condivisione non disponibile in questa pagina.');
}else{$('start').disabled=false;message('Premi Condividi e scegli cosa mostrare sulla TV.');}
function destroyWorker(){encoderGeneration++;avcInitSent=false;clearLatest();if(worker){worker.terminate();worker=null;}reservations=0;pending.clear();}
function stop(text,keepalive=false){
 const close=needClose;needClose=false;generation++;running=false;clearInterval(statsTimer);clearTimeout(connectTimer);destroyWorker();
 if(nativeSender){
  const active=nativeSender;nativeSender=null;
  closePromise=active.stop(keepalive).catch(error=>{$('tv-status').textContent='Chiusura video: '+error.message;})
   .finally(()=>{closePromise=null;$('start').disabled=leaving||!supported||!config.available;});
 }
 if(audioSender){audioSender.stop();audioSender=null;}
 if(pendingAudioSender){pendingAudioSender.stop();pendingAudioSender=null;}
 stopTracks(auxiliaryAudio);auxiliaryAudio=null;$('select-audio').disabled=true;
 if(reader){reader.cancel().catch(()=>{});reader=null;}
 if(stream){stream.getTracks().forEach(track=>{track.onended=null;track.stop();});stream=null;}
 if(socket){socket.onclose=socket.onerror=socket.onmessage=null;socket.close();socket=null;}
 if($('preview').pause)try{$('preview').pause();}catch(ignored){}
 $('preview').srcObject=null;$('preview').hidden=true;$('stop').disabled=true;
 $('width').disabled=$('fps').disabled=$('transport').disabled=$('video-profile').disabled=false;
 $('preset-cinema').disabled=$('preset-desktop').disabled=false;
 $('audio-enabled').disabled=$('audio-quality').disabled=false;
 if(document.body&&document.body.classList)document.body.classList.toggle('is-sharing',false);
 message(text||'Condivisione terminata.');
 if(close&&!closePromise){
  $('start').disabled=true;$('tv-status').textContent='Chiusura del ricevitore e del browser sulla TV…';
  closePromise=Promise.resolve(openingPromise).then(()=>tvCommand('stop',keepalive)).then(()=>{$('tv-status').textContent='Uscita dal browser richiesta alla TV.';})
   .catch(error=>{$('tv-status').textContent='Chiusura TV non confermata: '+error.message;})
   .finally(()=>{closePromise=null;$('start').disabled=leaving||!supported||!config.available;});
 }else $('start').disabled=leaving||!!closePromise||!supported||!config.available;
}
function packet(data){
 if(!socket||socket.readyState!==WebSocket.OPEN)return;
 if(pending.size>=windowSize){stop('Ricevitore congestionato. Riprova con una larghezza inferiore.');return;}
 const content=data.data;
 const zeroCopy=content.byteOffset===32&&content.buffer.byteLength===32+content.byteLength;
 const buffer=zeroCopy?content.buffer:new ArrayBuffer(32+content.byteLength);
 const view=new DataView(buffer);
 view.setUint32(0,0x53563200);view.setUint8(4,data.kind);view.setUint8(5,data.key?1:0);
 const count=data.kind===1?0:(data.count||1);view.setUint16(6,count);
 const seq=nextSeq++;view.setUint32(8,seq);view.setUint16(12,data.width);view.setUint16(14,data.height);
 data.rect.forEach((value,index)=>view.setUint16(16+index*2,value));
 if(!zeroCopy)new Uint8Array(buffer,32).set(content);
 if(data.kind===1)avcInitSent=true;
 pending.set(seq,{sent:performance.now(),kind:data.kind,count});bytes+=buffer.byteLength;socket.send(buffer);
}
function effectiveFPS(mode){return Math.min(Number($('fps').value),mode==='h264'?60:30);}
function paceCapture(){
 const track=stream&&stream.getVideoTracks()[0];
 if(track&&track.applyConstraints)track.applyConstraints({frameRate:{ideal:effectiveFPS(codec),max:effectiveFPS(codec)}}).catch(()=>{});
}
function canSendFrame(){
 const credits=codec==='h264'&&!avcInitSent?Math.max(1,windowSize-1):windowSize;
 return !!(running&&worker&&reservations<1&&pending.size+reservations<credits&&socket&&socket.readyState===WebSocket.OPEN&&socket.bufferedAmount<=512*1024);
}
function drainLatest(){
 if(latestFrame&&canSendFrame()){
  const next=latestFrame;latestFrame=null;
  reservations++;worker.postMessage({type:'frame',frame:next},[next]);
 }
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
   avcInitSent=false;encodedWidth=data.width;encodedHeight=data.height;
   codec=data.codec;pending.clear();socket.send(JSON.stringify(data));
  }else if(data.type==='packet')packet(data);
  else if(data.type==='processed'){reservations=Math.max(0,reservations-1);drainLatest();}
  else if(data.type==='mode'){codec=data.mode;paceCapture();}
 };
 worker.onerror=()=>stop('Codifica non disponibile nel browser. Ricarica la pagina.');
 paceCapture();
 worker.postMessage({type:'start',codec,width:Number($('width').value),fps:effectiveFPS(codec),profile:$('video-profile').value});
 message((codec==='h264'?'Video H.264':'Immagini adattive')+' · collegamento alla TV…');
}
function push(frame){
 if(!running||!worker){frame.close();return;}
 if(canSendFrame()){
  clearLatest();reservations++;worker.postMessage({type:'frame',frame},[frame]);
 }else{
  clearLatest();latestFrame=frame;
 }
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
  function next(now,metadata){
   if(epoch!==generation)return;
   if(video.videoWidth)push(new VideoFrame(video,{timestamp:Math.round((metadata?metadata.mediaTime:now/1000)*1000000)}));
   if(video.requestVideoFrameCallback)video.requestVideoFrameCallback(next);else requestAnimationFrame(next);
  }
  if(video.requestVideoFrameCallback)video.requestVideoFrameCallback(next);else requestAnimationFrame(next);
 }
}
function updateStats(){
 if(!running||document.hidden)return;const now=performance.now(),elapsed=Math.max(.001,(now-statsTime)/1000);
 if(pending.size&&now-Math.min(...[...pending.values()].map(item=>item.sent))>10000){stop('La TV non risponde. Riprova la condivisione.');return;}
 if(!worker)return;
 message((codec==='h264'?'H.264':'Immagini adattive')+' · '+encodedWidth+'×'+encodedHeight+' · '+(frames/elapsed).toFixed(1)+' immagini/s ricevute · '+(bytes*8/elapsed/1000000).toFixed(2)+' Mb/s video · conferma TV '+Math.round(acks?rtt/acks:0)+' ms'+(codec==='h264'?' · buffer '+Math.round(bufferMs)+' ms':'')+captureMode);
 frames=bytes=acks=rtt=0;statsTime=now;
}
$('start').onclick=async()=>{
 const epoch=++generation;$('start').disabled=true;message('Scegli cosa condividere nel browser.');
 try{
  const nativeMode=$('transport').value==='native'||$('transport').value==='native-app';
  const fps=effectiveFPS($('transport').value==='jpeg'?'jpeg':'h264'),width=Number($('width').value);
  const wantAudio=$('audio-enabled').checked;
  if(wantAudio&&!nativeMode)audioSender=new ScreenAudioSender({key:config.key,ws_url:wsUrl},audioOptions(),audioStatus);
  else $('audio-status').textContent='Audio disattivato.';
  // Include all system/browser audio while requesting TV-only playback. With
  // restrictOwnAudio=false, Chromium/Windows can select loopbackWithMute.
  // Tab capture uses the same suppression request without excluding other apps
  // from a monitor capture. The browser owns mute/restore for the capture lifetime.
  const captured=await navigator.mediaDevices.getDisplayMedia({video:{frameRate:{ideal:fps,max:fps},width:{ideal:width,max:width},height:{max:width>1280?1080:720}},
   audio:wantAudio?{...captureAudioOptions}:false,systemAudio:wantAudio?'include':'exclude',
   windowAudio:wantAudio?'system':'exclude',audioSelection:wantAudio?'preferred':undefined});
  if(epoch!==generation){captured.getTracks().forEach(track=>track.stop());return;}
  stream=captured;needClose=!nativeMode;forceJPEG=$('transport').value==='jpeg';
  const track=stream.getVideoTracks()[0];try{track.contentHint=$('video-profile').value==='cinema'?'motion':'detail';}catch(ignored){}
  track.onended=()=>stop();$('stop').disabled=false;$('width').disabled=$('fps').disabled=$('transport').disabled=$('video-profile').disabled=true;
  $('preset-cinema').disabled=$('preset-desktop').disabled=true;
  $('audio-enabled').disabled=$('audio-quality').disabled=true;
  if(document.body&&document.body.classList)document.body.classList.toggle('is-sharing',true);
  $('select-audio').disabled=false;
  if(audioSender)audioSender.start(stream.getAudioTracks()[0],track.getSettings().displaySurface);
  if(typeof MediaStreamTrackProcessor!=='function'){
   $('preview').hidden=false;$('preview').srcObject=stream;await $('preview').play();if(epoch!==generation)return;
  }else syncPreview();
  if(nativeMode){
   needClose=false;running=true;$('select-audio').disabled=true;
   $('tv-status').textContent='Avvio del lettore video nativo della TV; il browser della TV non è necessario.';
   audioStatus(stream.getAudioTracks().length?'Audio AAC sincronizzato nel video nativo.':'Nessuna traccia audio selezionata nel browser.');
   nativeSender=new ScreenNativeSender(config,message,error=>{if(epoch===generation)stop('Video nativo: '+error.message);});
   await nativeSender.start(stream,{width,fps,receiver:$('transport').value==='native-app'?'tizen':'dlna'});
   return;
  }
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
   else if(data.type==='waiting'){destroyWorker();message('In attesa della TV…');}
   else if(data.type==='fallback'){forceJPEG=true;beginEncoding({});}
   else if(data.type==='ack'){
    const sent=pending.get(data.seq);if(!sent)return;
    const delay=performance.now()-sent.sent;pending.delete(data.seq);acks++;rtt+=delay;bufferMs=data.bufferMs||0;
    frames+=sent.count;if(worker)worker.postMessage({type:'feedback',rtt:delay,bufferMs,decodeMs:data.decodeMs});
    drainLatest();
   }
  };
  socket.onerror=()=>stop('Collegamento al Raspberry non riuscito.');
  socket.onclose=event=>stop('Connessione chiusa. '+(event.reason||'Ricarica la pagina e riprova.'));
 }catch(error){if(epoch===generation)stop('Cattura non avviata: '+error.message);}
};
$('select-audio').onclick=async()=>{
 if(!stream||selectingAudio)return;
 const epoch=generation;selectingAudio=true;$('select-audio').disabled=true;
 let extra=null;
 // Prepare the audio context inside this click's user activation, before the
 // native picker consumes it. Do not interrupt the old source on cancellation.
 const replacement=new ScreenAudioSender({key:config.key,ws_url:wsUrl},audioOptions(()=>stopTracks(extra)),audioStatus);
 pendingAudioSender=replacement;
 try{
  extra=await navigator.mediaDevices.getDisplayMedia({
   video:{displaySurface:'browser',width:{ideal:16,max:320},height:{ideal:16,max:180},frameRate:{ideal:1,max:1}},
   audio:{...captureAudioOptions},systemAudio:'include',windowAudio:'system',audioSelection:'preferred'});
  if(epoch!==generation){replacement.stop();stopTracks(extra);return;}
  const sound=extra.getAudioTracks()[0];
  if(!sound){replacement.stop();stopTracks(extra);audioStatus('Audio non incluso: riprova e attiva «Condividi audio» nel dialogo del browser.');return;}
  const old=audioSender,oldSocket=old&&old.socket;
  if(old)old.stop();
  if(oldSocket&&oldSocket.readyState!==WebSocket.CLOSED)await new Promise(resolve=>{
   oldSocket.addEventListener('close',resolve,{once:true});setTimeout(resolve,1200);
  });
  if(epoch!==generation){replacement.stop();stopTracks(extra);return;}
  stopTracks(auxiliaryAudio);auxiliaryAudio=extra;audioSender=replacement;
  $('audio-enabled').checked=true;syncModeUI();
  const picture=extra.getVideoTracks()[0];
  // Keep the auxiliary display session alive at one tiny frame/s. Stopping its
  // video track can also end audio on some browsers. It is never sent to the TV.
  if(picture){picture.enabled=false;picture.onended=()=>replacement.stop();}
  await replacement.start(sound,picture?picture.getSettings().displaySurface:'unknown');
 }catch(error){
  replacement.stop();stopTracks(extra);
  if(epoch===generation)audioStatus(error.name==='NotAllowedError'?'Scelta audio annullata. La condivisione dello schermo continua.':'Audio non selezionato: '+error.message);
 }finally{
  if(pendingAudioSender===replacement)pendingAudioSender=null;
  selectingAudio=false;$('select-audio').disabled=!stream;
 }
};
$('stop').onclick=()=>stop();window.addEventListener('pagehide',()=>stop(undefined,true));
document.addEventListener('visibilitychange',()=>{
 if(document.hidden||!running)return;
 // Time spent suspended by the OS isn't a TV acknowledgment timeout.
 const now=performance.now();for(const packet of pending.values())packet.sent=now;
 statsTime=now;frames=bytes=acks=rtt=0;
});
