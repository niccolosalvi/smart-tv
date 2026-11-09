/* ES5/Web Audio receiver for the TV. AAC never goes through the TV's broken MSE.
 * Bounded decode queue, short jitter buffer and independent fallback blocks. */
(function(scope){
 'use strict';
 var steps=[7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767];
 var indices=[-1,-1,-1,-1,2,4,6,8];
 function ScreenAudioReceiver(config){
  var self=this;
  this.config=config;this.socket=null;this.context=null;this.queue=[];this.sources=[];this.generation=0;this.nextTime=0;this.played=0;
  this.button=document.getElementById('enable-audio');
  this.button.onclick=function(){self.resume();};
  document.addEventListener('keydown',function(){if(self.context)self.resume();});
 }
 ScreenAudioReceiver.prototype.send=function(value){if(this.socket&&this.socket.readyState===1)this.socket.send(JSON.stringify(value));};
 ScreenAudioReceiver.prototype.resume=function(){
  var self=this,ac=this.context;if(!ac)return;
  try{var p=ac.resume?ac.resume():null;
   if(p&&p.then)p.then(function(){self.button.style.display='none';},function(){});
   var b=ac.createBuffer(1,1,ac.sampleRate),s=ac.createBufferSource();s.buffer=b;s.connect(ac.destination);if(s.start)s.start(0);else s.noteOn(0);
   this.button.style.display=ac.state==='suspended'?'block':'none';
  }catch(ignored){}
 };
 ScreenAudioReceiver.prototype.reset=function(){
  this.generation++;this.queue=[];this.decoding=false;this.nextTime=0;this.played=0;clearTimeout(this.decodeTimer);
  for(var i=0;i<this.sources.length;i++){try{this.sources[i].onended=null;this.sources[i].stop(0);this.sources[i].disconnect();}catch(ignored){}}
  this.sources=[];this.lastPts=undefined;this.button.style.display='none';
 };
 ScreenAudioReceiver.prototype.close=function(){
  this.reset();if(this.socket){this.socket.onclose=this.socket.onmessage=this.socket.onerror=null;this.socket.close();this.socket=null;}
  if(this.context){try{if(this.context.close)this.context.close();else this.context.suspend();}catch(ignored){}this.context=null;}
 };
 ScreenAudioReceiver.prototype.fallback=function(){this.reset();this.send({type:'fallback'});};
 ScreenAudioReceiver.prototype.open=function(){
  this.close();var self=this,AC=scope.AudioContext||scope.webkitAudioContext;
  if(!AC)return;
  try{this.socket=new WebSocket(this.config.ws_url);this.socket.binaryType='arraybuffer';
   this.socket.onopen=function(){self.send({role:'audio_viewer',key:self.config.key,capabilities:{webAudio:true,
    aac:!/Tizen 2\.4\.0.*SamsungBrowser\/1\.1/.test(navigator.userAgent)}});};
   this.socket.onmessage=function(event){
    if(typeof event.data==='string'){
     var data=JSON.parse(event.data);
     if(data.type==='configure'){
      self.reset();self.codec=data.codec;self.rate=data.sampleRate;self.channels=data.channels;
      try{if(!self.context)self.context=new AC();self.resume();}catch(ignored){self.fallback();}
     }else if(data.type==='stop')self.reset();
    }else{
     if(self.queue.length>=3){self.fallback();return;}
     self.queue.push(event.data);self.pump();
    }
   };
   this.socket.onclose=function(){self.reset();};
  }catch(ignored){}
 };
 ScreenAudioReceiver.prototype.ima=function(raw,samples){
  var view=new DataView(raw,24),ac=this.context,output=ac.createBuffer(this.channels,samples,this.rate);
  var predictors=[],indexes=[],planes=[];
  for(var c=0;c<this.channels;c++){predictors[c]=view.getInt16(c*4,true);indexes[c]=view.getUint8(c*4+2);planes[c]=output.getChannelData(c);}
  var bytes=new Uint8Array(raw,24+this.channels*4),nibble=0;
  for(var i=0;i<samples;i++)for(var channel=0;channel<this.channels;channel++){
   var code=(bytes[nibble>>1]>>((nibble&1)*4))&15;nibble++;
   var step=steps[indexes[channel]],change=step>>3;
   if(code&4)change+=step;if(code&2)change+=step>>1;if(code&1)change+=step>>2;
   predictors[channel]=Math.max(-32768,Math.min(32767,predictors[channel]+(code&8?-change:change)));
   indexes[channel]=Math.max(0,Math.min(88,indexes[channel]+indices[code&7]));
   planes[channel][i]=predictors[channel]/32768;
  }
  return output;
 };
 ScreenAudioReceiver.prototype.ack=function(seq,rms){
  var ac=this.context;
  this.send({type:'ack',seq:seq,bufferMs:Math.max(0,Math.min(2000,(this.nextTime-ac.currentTime)*1000)),
   contextTime:ac.currentTime,playedSamples:this.played,rms:rms||0,state:ac.state||'running'});
 };
 ScreenAudioReceiver.prototype.schedule=function(buffer,item){
  var self=this,ac=this.context,skip=item.preroll/this.rate,duration=item.samples/this.rate;
  if(buffer.duration+1/this.rate<skip+duration){this.fallback();return;}
  if(ac.state==='suspended'){
   this.button.style.display='block';this.ack(item.seq,0);return;
  }
  var now=ac.currentTime;
  // Discontinuities or congestion discard stale audio. Never keep seconds of
  // queued sound; the next independent packet can resume at the live edge.
  if(this.lastPts!==undefined&&Math.abs(item.pts-this.lastPts)>150000)this.nextTime=0;
  this.lastPts=item.pts+duration*1000000;
  if(this.nextTime<now+.005)this.nextTime=now+.06;
  if(this.nextTime-now>.20){this.ack(item.seq,0);return;}
  var source=ac.createBufferSource();source.buffer=buffer;source.connect(ac.destination);
  var plane=buffer.getChannelData(0),sum=0,start=Math.round(skip*buffer.sampleRate),count=Math.min(plane.length-start,Math.round(duration*buffer.sampleRate));
  for(var i=start;i<start+count;i++)sum+=plane[i]*plane[i];
  var rms=Math.sqrt(sum/Math.max(1,count)),epoch=this.generation;
  this.sources.push(source);
  source.onended=function(){
   source.disconnect();var index=self.sources.indexOf(source);if(index>=0)self.sources.splice(index,1);
   if(epoch===self.generation)self.played+=item.samples;
  };
  if(source.start)source.start(this.nextTime,skip,duration);else source.noteGrainOn(this.nextTime,skip,duration);
  this.nextTime+=duration;this.ack(item.seq,rms);
 };
 ScreenAudioReceiver.prototype.pump=function(){
  if(this.decoding||!this.queue.length||!this.context)return;
  var self=this,raw=this.queue.shift(),v=new DataView(raw),generation=this.generation;
  if(v.byteLength<24||v.getUint32(0)!==0x41553100)return;
  var item={seq:v.getUint32(4),pts:v.getUint32(8)*4294967296+v.getUint32(12),samples:v.getUint32(16),preroll:v.getUint16(20)};
  this.decoding=true;
  function done(buffer){
   if(generation!==self.generation)return;clearTimeout(self.decodeTimer);
   try{self.schedule(buffer,item);}catch(ignored){self.fallback();}
   self.decoding=false;self.pump();
  }
  if(this.codec==='ima'){try{done(this.ima(raw,item.samples));}catch(ignored){this.reset();}return;}
  this.decodeTimer=setTimeout(function(){if(generation===self.generation)self.fallback();},1500);
  // Old WebKit supports the callback form, not the Promise returned by modern engines.
  try{this.context.decodeAudioData(raw.slice(24),done,function(){if(generation===self.generation)self.fallback();});}
  catch(ignored){this.fallback();}
 };
 scope.ScreenAudioReceiver=ScreenAudioReceiver;
})(window);
