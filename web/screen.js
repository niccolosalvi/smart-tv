/* ES5 receiver for Samsung 2016: AVC/MSE first, JPEG rectangles as fallback. */
(function(){
 var config=JSON.parse(document.getElementById('screen-config').textContent);
 var audio=new ScreenAudioReceiver(config);
 var socket=null,image=document.getElementById('frame'),canvas=document.getElementById('screen-canvas');
 var video=document.getElementById('screen-video'),panel=document.getElementById('panel'),status=document.getElementById('status');
 var URLApi=window.URL||window.webkitURL,MS=window.MediaSource||window.WebKitMediaSource;
 var media=null,source=null,mediaURL=null,imageURL=null,timer=null;
 var queue=[],current=null,epoch=0,codec=null,lastTrim=0,closed=false,ctx=canvas.getContext('2d'),mseTimer=null,firstAppend=false,lastVideoStats=0;
 function accepts(mime){try{return !!(MS&&MS.isTypeSupported&&MS.isTypeSupported(mime));}catch(ignored){return false;}}
 function caps(){
  // This browser advertises AVC support, but real append/decode probes on the
  // target UE49KS7000 stall. Avoid a black startup on this known broken MSE.
  var oldSamsung=/Tizen 2\.4\.0.*SamsungBrowser\/1\.1/.test(navigator.userAgent);
  return {mse:!!MS,avc:!oldSamsung&&accepts('video/mp4; codecs="avc1.42E029"'),webm:accepts('video/webm; codecs="vp8"'),userAgent:navigator.userAgent.slice(0,256)};
 }
 function revokeImage(){if(imageURL){URLApi.revokeObjectURL(imageURL);imageURL=null;}}
 function reset(){
  epoch++;queue=[];current=null;codec=null;clearTimeout(mseTimer);firstAppend=false;
  image.onload=image.onerror=null;image.removeAttribute('src');image.style.display='none';revokeImage();
  video.onerror=null;try{video.pause();}catch(ignored){}video.removeAttribute('src');video.load();video.style.display='none';
  if(source){source.onupdateend=source.onerror=null;try{source.abort();}catch(ignored){}}
  if(media)media.onsourceopen=null;
  if(mediaURL){URLApi.revokeObjectURL(mediaURL);mediaURL=null;}
  source=media=null;canvas.style.display='none';ctx.clearRect(0,0,canvas.width,canvas.height);
 }
 function show(text){panel.style.display='block';status.textContent=text;}
 function send(data){if(socket&&socket.readyState===1)socket.send(typeof data==='string'?data:JSON.stringify(data));}
 function closeManaged(broadcast){
  if(closed)return;closed=true;
  if(broadcast){try{localStorage.setItem('smart-tv-close-receivers',String(Date.now()));}catch(ignored){}}
  reset();audio.close();if(socket){socket.onclose=null;socket.close();}show('Condivisione terminata. Chiusura browser richiesta.');
  try{window.close();}catch(ignored){}
 }
 window.addEventListener('storage',function(event){if(event.key==='smart-tv-close-receivers')closeManaged(false);});
 function diagnostic(stage,error){send({type:'diagnostic',stage:stage,error:error?String(error.message||error):''});}
 function fallback(error){diagnostic('fallback',error);reset();show('Passaggio alla modalità compatibile.');send({type:'fallback'});}
 function bufferMs(){try{return video.buffered.length?Math.max(0,(video.buffered.end(video.buffered.length-1)-video.currentTime)*1000):0;}catch(ignored){return 0;}}
 function acknowledge(item){
  if(!item)return;if(item.legacy){send('ack');return;}
  var result={type:'ack',seq:item.seq,bufferMs:codec==='h264'?Math.min(60000,Math.round(bufferMs())):0};
  if(codec==='h264'&&Date.now()-lastVideoStats>1000){
   lastVideoStats=Date.now();result.videoWidth=video.videoWidth||0;result.playedSeconds=video.currentTime||0;result.paused=video.paused;
   try{var quality=video.getVideoPlaybackQuality?video.getVideoPlaybackQuality():null;
    if(quality)result.decodedFrames=Math.max(0,quality.totalVideoFrames-quality.droppedVideoFrames);
    else if(typeof video.webkitDecodedFrameCount==='number')result.decodedFrames=video.webkitDecodedFrameCount;
   }catch(ignored){}
  }
  send(result);
 }
 function playEdge(){
  try{if(!video.buffered.length)return;
   var end=video.buffered.end(video.buffered.length-1),begin=video.buffered.start(video.buffered.length-1);
   if(video.readyState<3&&end-begin<.15)return;
   if((video.currentTime>0&&end-video.currentTime>.35)||video.currentTime<begin)video.currentTime=Math.max(begin,end-.15);
   if(video.paused){var promise=video.play();if(promise&&promise.catch)promise.catch(function(){fallback('Autoplay non consentito');});}
  }catch(ignored){}
 }
 function pump(){
  if(codec==='h264'){
   if(!source||source.updating||current||!queue.length)return;
   try{if(video.currentTime-lastTrim>2&&video.currentTime>4){lastTrim=video.currentTime;source.remove(0,video.currentTime-2);return;}
    current=queue.shift();if(!firstAppend)diagnostic('append-start');source.appendBuffer(current.payload);
   }catch(error){fallback();}
  }else if(codec==='jpeg'||codec==='legacy'){
   if(current||!queue.length)return;
   current=queue.shift();var item=current,generation=epoch;
   revokeImage();imageURL=URLApi.createObjectURL(new Blob([item.payload],{type:item.mime||'image/jpeg'}));
   image.onload=function(){if(generation!==epoch)return;
    if(item.legacy)image.style.display='block';else{
     if(item.tiles){for(var i=0;i<item.tiles.length;i++){var tx=item.tiles[i][0],ty=item.tiles[i][1],tw=Math.min(64,canvas.width-tx),th=Math.min(64,canvas.height-ty);ctx.drawImage(image,(i%item.columns)*64,Math.floor(i/item.columns)*64,tw,th,tx,ty,tw,th);}}
     else ctx.drawImage(image,item.x,item.y,item.w,item.h);
     canvas.style.display='block';
    }
    panel.style.display='none';revokeImage();acknowledge(item);current=null;pump();
   };
   image.onerror=function(){show('Immagine non riproducibile.');if(socket)socket.close(1000,'Decoder JPEG non disponibile');};image.src=imageURL;
  }
 }
 function configure(data){
  reset();codec=data.codec;canvas.width=data.width;canvas.height=data.height;
  if(codec==='h264'){
   if(!accepts(data.mime)){fallback();return;}
   try{media=new MS();var generation=epoch;diagnostic('media-created');
    var opened=function(){if(generation!==epoch||source)return;diagnostic('source-open');
     try{source=media.addSourceBuffer(data.mime);source.mode='segments';
      var updated=function(){var done=current;
       setTimeout(function(){if(generation!==epoch||source.updating||current!==done)return;current=null;
        if(done){if(!firstAppend){firstAppend=true;clearTimeout(mseTimer);diagnostic('append-done');}acknowledge(done);}playEdge();pump();},0);
      };
      source.addEventListener('updateend',updated);source.addEventListener('webkitupdateend',updated);
      source.addEventListener('error',function(){if(generation===epoch)fallback('SourceBuffer error');});setTimeout(pump,0);
     }catch(error){fallback(error);}
    };
    media.addEventListener('sourceopen',opened);media.addEventListener('webkitsourceopen',opened);
    video.onerror=function(){if(generation===epoch)fallback('Video error '+(video.error?video.error.code:''));};video.muted=true;video.autoplay=false;video.preload='auto';
    mediaURL=URLApi.createObjectURL(media);video.src=mediaURL;video.style.display='block';panel.style.display='none';
    mseTimer=setTimeout(function(){if(generation===epoch&&!firstAppend)fallback('MSE initialization timeout');},3500);
   }catch(error){fallback(error);}
  }
 }
 function receive(buffer){
  var bytes=new Uint8Array(buffer),item;
  if(bytes[0]===255&&bytes[1]===216){if(codec!=='legacy'){reset();codec='legacy';}item={legacy:true,payload:buffer};}
  else{if(buffer.byteLength<32)return;
   var view=new DataView(buffer),kind=view.getUint8(4);
   item={seq:view.getUint32(8),x:view.getUint16(16),y:view.getUint16(18),w:view.getUint16(20),h:view.getUint16(22),payload:buffer.slice(32)};
   if(kind===6||kind===7)item.mime='image/png';
   if(kind===5||kind===6){var count=view.getUint16(32);item.tiles=[];item.columns=item.x;
    for(var n=0;n<count;n++)item.tiles.push([view.getUint16(34+n*4),view.getUint16(36+n*4)]);
    item.payload=buffer.slice(34+count*4);}
   if((kind===1||kind===2)&&codec!=='h264')return;if(kind>=4&&codec!=='jpeg')return;
  }
  if(queue.length>=3){fallback();return;}queue.push(item);pump();
 }
 function connect(){
  closed=false;reset();audio.close();clearTimeout(timer);
  if(socket){socket.onclose=socket.onerror=socket.onmessage=null;socket.close();}
  show('Connessione al Raspberry…');
  if(!config.available){show('Relay non disponibile. Ricarica la pagina.');return;}
  if(!window.WebSocket||!window.Blob||!URLApi||!URLApi.createObjectURL){show('Browser non compatibile.');return;}
  try{socket=new WebSocket(config.ws_url);socket.binaryType='arraybuffer';var connection=socket;
   timer=setTimeout(function(){if(socket===connection){show('Connessione scaduta. Premi Ricollega.');connection.close();}},8000);
   connection.onopen=function(){send({role:'viewer',key:config.key,protocol:2,capabilities:caps()});};
   connection.onmessage=function(event){clearTimeout(timer);
    if(typeof event.data!=='string'){receive(event.data);return;}
    if(event.data==='close'){closeManaged(true);return;}
    if(event.data==='stop'){reset();audio.close();show('Condivisione terminata sul PC.');return;}
    try{var data=JSON.parse(event.data);if(data.type==='configure')configure(data);else if(data.type==='ready'){audio.open();show('TV collegata. Avvia la condivisione sul PC.');}}
    catch(error){show('Messaggio ricevitore non valido.');}
   };
   connection.onclose=function(event){clearTimeout(timer);reset();audio.close();show('Connessione chiusa. '+(event.reason||'Ricarica la pagina o premi Ricollega.'));};
   connection.onerror=function(){show('Collegamento WebSocket non riuscito.');};
  }catch(error){show('WebSocket non disponibile: '+error.message);}
 }
 window.onerror=function(){show('Errore del ricevitore. Riapri la pagina dalla dashboard.');};
 document.getElementById('reconnect').onclick=connect;
 document.getElementById('fullscreen').onclick=function(){var root=document.documentElement,request=root.requestFullscreen||root.webkitRequestFullscreen;if(request)request.call(root);};
 window.onpagehide=function(){reset();audio.close();if(socket)socket.close();};connect();
}());
