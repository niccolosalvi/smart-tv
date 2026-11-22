/* Packaged TV application only: AVPlay is not available in the TV browser. */
(function(){
 'use strict';
 var epoch=0,player=window.webapis&&window.webapis.avplay,status=document.getElementById('status');
 function show(text){status.textContent=text;status.style.display='block';}
 function release(){epoch++;if(!player)return;try{player.stop();}catch(ignored){}try{player.close();}catch(ignored){}}
 function open(url){
  // Accept only the session URL produced by our LAN relay. No remote script,
  // credentials, playback configuration or arbitrary app commands are loaded.
  if(typeof url!=='string'||!/^http:\/\/\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}\/native\/[A-Za-z0-9_-]{32}\/stream\.ts$/.test(url)){
   show('Indirizzo della trasmissione non valido. Riavvia la condivisione dal PC.');return;
  }
  release();var generation=epoch;
  if(!player){show('AVPlay non disponibile: avvia questa applicazione sulla TV.');return;}
  try{
   player.open(url);player.setDisplayRect(0,0,1920,1080);
   player.setDisplayMethod('PLAYER_DISPLAY_MODE_LETTER_BOX');
   // Samsung documents a four-second minimum. Timeout is separate and is
   // allowed to complete initial buffering earlier; actual latency needs QA.
   player.setBufferingParam('PLAYER_BUFFER_FOR_PLAY','PLAYER_BUFFER_SIZE_IN_SECOND',4);
   player.setBufferingParam('PLAYER_BUFFER_FOR_RESUME','PLAYER_BUFFER_SIZE_IN_SECOND',4);
   player.setTimeoutForBuffering(3);
   player.setListener({
    onbufferingstart:function(){if(generation===epoch)show('Preparazione del video…');},
    onbufferingcomplete:function(){if(generation===epoch)status.style.display='none';},
    onstreamcompleted:function(){if(generation===epoch){release();show('Condivisione terminata.');}},
    onerror:function(){if(generation===epoch){release();show('Riproduzione interrotta. Riavvia la condivisione dal PC.');}}
   });
   show('Preparazione del video…');
   player.prepareAsync(function(){if(generation===epoch){player.play();status.style.display='none';}},
    function(){if(generation===epoch){release();show('Video non riproducibile. Riavvia la condivisione dal PC.');}});
  }catch(error){release();show('Configurazione del lettore non disponibile su questa TV.');}
 }
 function appControl(){
  try{
   var request=tizen.application.getCurrentApplication().getRequestedAppControl(),data=request&&request.appControl.data;
   for(var i=0;data&&i<data.length;i++)if(data[i].key==='PAYLOAD'&&data[i].value.length){
    var payload=JSON.parse(data[i].value[0]);if(payload.url)open(payload.url);
   }
  }catch(error){show('Avvia la trasmissione dal PC per collegare il ricevitore.');}
 }
 window.addEventListener('appcontrol',appControl);
 window.addEventListener('load',appControl);
 window.addEventListener('unload',release);
 document.addEventListener('visibilitychange',function(){if(document.hidden)release();});
 document.addEventListener('keydown',function(event){if(event.keyCode===10009){release();try{tizen.application.getCurrentApplication().exit();}catch(ignored){}}});
}());
