/* Audio has its own socket and credits, independent of large image transfers. */
(function(scope){
 'use strict';
 class ScreenAudioSender {
  constructor(config,options,status){
   this.config=config;this.options=options;this.status=status;this.pending=new Map();this.seq=0;this.jobs=0;this.closed=false;this.ready=false;this.active=false;
   const AC=window.AudioContext||window.webkitAudioContext;
   try{this.context=new AC({sampleRate:48000,latencyHint:'interactive'});this.context.resume().catch(()=>{});}
   catch(_){this.status('Audio non disponibile nel browser del PC.');}
  }
  async start(track,surface='unknown'){
   if(!this.context||this.closed)return;
   this.surface=['monitor','browser','window'].includes(surface)?surface:'unknown';
   if(!track){this.status(this.surface==='monitor'?'Audio non condiviso: con «Schermo intero» attiva «Condividi audio del sistema», oppure scegli audio da una scheda.':'Audio non condiviso: abilitalo nel dialogo del browser oppure scegli una scheda con audio.');this.stop();return;}
   this.lastSignal=performance.now();this.lastLevel=0;this.inputRms=0;
   this.track=track;track.onended=()=>{this.status('La sorgente audio è terminata.');this.stop();};
   try{
    const ac=this.context;await ac.resume();if(this.closed)return;
    if(ac.sampleRate!==48000)throw Error('Frequenza audio del PC non supportata');
    this.epoch=performance.now()*1000-ac.currentTime*1000000;
    this.source=ac.createMediaStreamSource(new MediaStream([track]));
    this.sink=ac.createGain();this.sink.gain.value=0;this.sink.connect(ac.destination);
    if(ac.audioWorklet){
     await ac.audioWorklet.addModule('/assets/audio-capture.js');if(this.closed)return;
     this.capture=new AudioWorkletNode(ac,'screen-audio',{processorOptions:{channels:this.options.channels}});
     this.capture.port.onmessage=event=>this.samples(event.data.channels,event.data.time);
    }else{
     this.capture=ac.createScriptProcessor(2048,2,2);
     this.capture.onaudioprocess=event=>{
      const input=event.inputBuffer,planes=[];
      for(let c=0;c<this.options.channels;c++){
       const a=input.getChannelData(Math.min(c,input.numberOfChannels-1)),copy=new Float32Array(a);
       if(this.options.channels===1&&input.numberOfChannels>1){const b=input.getChannelData(1);for(let i=0;i<copy.length;i++)copy[i]=(copy[i]+b[i])*.5;}
       planes.push(copy);
      }
      this.samples(planes,event.playbackTime-input.duration);
     };
    }
    this.source.connect(this.capture);this.capture.connect(this.sink);
    this.socket=new WebSocket(this.config.ws_url);this.socket.binaryType='arraybuffer';
    this.socket.onopen=()=>this.socket.send(JSON.stringify({role:'audio_sender',key:this.config.key}));
    this.socket.onmessage=event=>this.message(JSON.parse(event.data));
    this.socket.onerror=()=>{this.status('Collegamento audio non riuscito; lo schermo continua.');this.stop();};
    this.socket.onclose=()=>{if(!this.closed){this.status('Collegamento audio chiuso; lo schermo continua.');this.stop();}};
   }catch(error){this.status('Audio non disponibile: '+error.message);this.stop();}
  }
  samples(planes,time){
   const now=performance.now();
   if(!this.closed&&now-this.lastLevel>=500){
    this.lastLevel=now;let power=0,count=0;
    for(const plane of planes)for(let i=0;i<plane.length;i++){power+=plane[i]*plane[i];count++;}
    this.inputRms=Math.min(1,Math.sqrt(power/Math.max(1,count)));
    if(this.inputRms>.0001)this.lastSignal=now;
    if(this.socket&&this.socket.readyState===WebSocket.OPEN)this.socket.send(JSON.stringify({type:'capture',surface:this.surface,inputRms:this.inputRms,muted:this.track.muted}));
   }
   if(!this.ready||!this.active||this.closed||this.jobs>=3||this.pending.size>=3)return;
   // Re-correlate the device clock: AudioContext can pause during device startup
   // while performance.now keeps advancing. A fixed origin would mark live
   // samples as stale forever after a slow Windows audio-device initialization.
   this.epoch=performance.now()*1000-this.context.currentTime*1000000;
   this.jobs++;this.worker.postMessage({type:'samples',channels:planes,pts:this.epoch+time*1000000},planes.map(p=>p.buffer));
  }
  encode(){
   if(this.closed)return;
   if(this.worker)this.worker.terminate();this.pending.clear();this.jobs=0;this.ready=false;
   this.worker=new Worker('/assets/audio-worker.js');
   const activeWorker=this.worker;
   this.worker.onerror=()=>{this.status('Codifica audio non disponibile; lo schermo continua.');this.stop();};
   this.worker.onmessage=event=>{
    if(this.closed||this.worker!==activeWorker)return;const data=event.data;
    if(data.type==='configure'){this.codec=data.codec;this.bitrate=data.bitrate;this.pending.clear();this.socket.send(JSON.stringify(data));this.status('Audio '+(data.codec==='aac'?'AAC':'ADPCM')+' · avvio ricevitore…');}
    else if(data.type==='ready')this.ready=true;
    else if(data.type==='processed')this.jobs=Math.max(0,this.jobs-1);
    else if(data.type==='packet')this.packet(data);
   };
   this.worker.postMessage({type:'start',channels:this.options.channels,bitrate:this.options.bitrate,codec:this.preferIMA?'ima':'aac'});
  }
  message(data){
   if(this.closed)return;
   if(data.type==='ready'||data.type==='viewer'){
    this.active=data.type==='viewer'||data.viewer_connected;
    if(this.active&&data.capabilities.webAudio){this.preferIMA=data.capabilities.aac===false;this.encode();}
    else this.status(this.active?'Il browser della TV non supporta l’audio condiviso.':'Audio in attesa della TV…');
   }else if(data.type==='waiting'){this.active=false;this.ready=false;this.pending.clear();this.status('Audio in attesa della TV…');}
   else if(data.type==='fallback'&&this.worker){this.pending.clear();this.worker.postMessage({type:'fallback'});}
   else if(data.type==='stop')this.stop();
   else if(data.type==='ack'){
    this.pending.delete(data.seq);
    const now=performance.now();
    if(now-(this.lastStatus||0)<1000&&this.lastState===data.state)return;
    this.lastStatus=now;this.lastState=data.state;
    const format=(this.codec==='aac'?'AAC ':'ADPCM ')+this.bitrate/1000+' kb/s';
    const source=this.surface==='monitor'?'Audio di sistema':this.surface==='browser'?'Audio scheda':this.surface==='window'?'Audio finestra':'Audio';
    const level=now-this.lastSignal>3000?' · nessun suono acquisito dal PC':'';
    this.status(data.state==='suspended'?'Sulla TV premi «Attiva audio» con il telecomando.':source+' · '+format+' · '+(this.options.channels===2?'stereo':'mono')+' · buffer '+Math.round(data.bufferMs)+' ms'+level);
   }
  }
  packet(data){
   if(!this.active||this.closed||this.pending.size>=3||this.socket.bufferedAmount>32768)return;
   // Each packet is independent (AAC preroll/ADPCM state), so stale audio can
   // be discarded rather than building a queue behind the live capture.
   if(performance.now()*1000-data.pts>350000)return;
   const buffer=new ArrayBuffer(24+data.data.length),v=new DataView(buffer);
   v.setUint32(0,0x41553100);v.setUint32(4,++this.seq);
   const pts=Math.max(0,Math.round(data.pts));v.setUint32(8,Math.floor(pts/4294967296));v.setUint32(12,pts>>>0);
   v.setUint32(16,data.samples);v.setUint16(20,data.preroll);v.setUint16(22,data.kind);new Uint8Array(buffer,24).set(data.data);
   this.pending.set(this.seq,performance.now());this.socket.send(buffer);
  }
  stop(){
   if(this.closed)return;this.closed=true;this.ready=this.active=false;this.pending.clear();
   if(this.track){this.track.onended=null;this.track.stop();}
   if(this.capture){if(this.capture.port)this.capture.port.onmessage=null;this.capture.onaudioprocess=null;this.capture.disconnect();}
   if(this.source)this.source.disconnect();if(this.sink)this.sink.disconnect();
   if(this.worker)this.worker.terminate();
   if(this.socket){this.socket.onclose=this.socket.onerror=this.socket.onmessage=null;this.socket.close();}
   if(this.context)this.context.close().catch(()=>{});
   if(this.options.onStop)this.options.onStop();
  }
 }
 scope.ScreenAudioSender=ScreenAudioSender;
})(window);
