// Exercise the real worker's pacing, quality controller and packet boundaries.
// Canvas encoding is substituted; this verifies decisions, not visual fidelity.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/share-worker.js'),'utf8');

function load(options={}){
 const messages=[],encodes=[],videoConfigs=[],encodedCalls=[];let now=0,readbacks=0,draws=0,lastEncoder=null;
 class Canvas{
  constructor(width,height){this.width=width;this.height=height;this.seed=0;}
  getContext(){const canvas=this;return {
   drawImage(input){draws++;canvas.seed=input.seed||0;},clearRect(){canvas.seed=0;},
   getImageData(x,y,width,height){
    readbacks++;
    if(options.pixels)return {data:options.pixels(width,height,canvas.seed)};
    const data=new Uint8ClampedArray(width*height*4);data.fill(canvas.seed);return {data};
   }
  };}
  async convertToBlob({type,quality}){
   encodes.push({type,quality});
   const size=options.size?options.size(type,quality):(type==='image/png'?6000:4000);
   return {size,async arrayBuffer(){return new ArrayBuffer(size);}};
  }
 }
 class Encoder{
  static async isConfigSupported(config){return {supported:true,config};}
  constructor(init){this.encodeQueueSize=0;this.init=init;lastEncoder=this;}
  configure(config){videoConfigs.push(config);}
  encode(frame,opts){encodedCalls.push({frame,opts});}
 }
 class Frame{constructor(canvas,settings){this.timestamp=settings.timestamp;this.duration=settings.duration;}close(){}}
 const context=vm.createContext({OffscreenCanvas:Canvas,VideoEncoder:Encoder,VideoFrame:Frame,Uint8Array,Uint8ClampedArray,Uint16Array,Uint32Array,DataView,ArrayBuffer,
  ScreenMP4:{init:(w,h,d)=>new Uint8Array([0,0,0,8,102,116,121,112]),fragment:(s,ts,dur,d)=>new Uint8Array([0,0,0,8,109,111,111,102]),fragmentBatch:(s,arr)=>new Uint8Array([0,0,0,8,109,111,111,102])},
  performance:{now:()=>now},importScripts(){},self:{postMessage:message=>messages.push(message)}});
 vm.runInContext(source,context);
 const send=data=>context.self.onmessage({data});
 return {messages,encodes,videoConfigs,encodedCalls,send,get readbacks(){return readbacks;},get draws(){return draws;},get encoder(){return lastEncoder;},time:value=>{now=value;},read:expression=>vm.runInContext(expression,context),
  async frame(timestamp=0,width=1280,height=720,seed=1){
   const frame={timestamp,displayWidth:width,displayHeight:height,seed,closed:false,close(){this.closed=true;}};
   await send({type:'frame',frame});assert.equal(frame.closed,true);return frame;
  }};
}

(async()=>{
 const cinema=load();await cinema.send({type:'start',codec:'jpeg',profile:'cinema',width:1920,fps:24});
 await cinema.frame(0,1920,1080);
 assert.equal(cinema.messages.find(m=>m.type==='configure').width,1920);
 assert.equal(cinema.messages.find(m=>m.type==='configure').height,1080);
 assert.equal(cinema.encodes.find(e=>e.type==='image/jpeg').quality,.9);
 assert.equal(cinema.readbacks,0,'Film encoding must avoid synchronous pixel readback');
 assert.equal(cinema.encodes.length,1,'Film encoding must not also try PNG');
 await cinema.frame(50000,1920,1080,2);
 assert.equal(cinema.readbacks,0);assert.equal(cinema.messages.filter(m=>m.type==='packet').length,2);
 // One expensive ACK must not lower quality once for every queued image.
 for(let i=0;i<100;i++)await cinema.send({type:'feedback',rtt:350,decodeMs:60});
 assert.ok(cinema.read('quality')>=.879);
 for(let i=1;i<50;i++){cinema.time(i*501);await cinema.send({type:'feedback',rtt:350,decodeMs:60});}
 assert.equal(cinema.read('quality'),.78);
 for(let i=50;i<100;i++){cinema.time(i*501);await cinema.send({type:'feedback',rtt:20,decodeMs:5});}
 assert.equal(cinema.read('quality'),.94);
 const desktop=load();await desktop.send({type:'start',codec:'jpeg',width:1280,fps:30});
 await desktop.frame(0,1920,1080);
 assert.equal(desktop.messages.find(m=>m.type==='configure').width,1280);
 assert.equal(desktop.messages.find(m=>m.type==='configure').height,720);
 assert.equal(desktop.encodes.find(e=>e.type==='image/jpeg').quality,.72);
 assert.ok(desktop.readbacks>0,'Desktop must retain precise damage tracking');
 // Queued round-trip time alone, within the three-image pipeline budget,
 // must not progressively destroy JPEG quality.
 for(let i=0;i<20;i++){desktop.time(i*501);await desktop.send({type:'feedback',rtt:190,decodeMs:10});}
 assert.equal(desktop.read('quality'),.72);
 // A high-entropy frame is retried and sent in the same input operation.
 const retry=load({size:(type,q)=>type==='image/png'||q>.8?600000:4000});
 await retry.send({type:'start',codec:'jpeg',profile:'cinema',width:1920,fps:30});await retry.frame(0,1920,1080);
 assert.equal(retry.messages.filter(m=>m.type==='packet').length,1);
 assert.ok(retry.encodes.filter(e=>e.type==='image/jpeg').length>=2);
 assert.ok(retry.messages.find(m=>m.type==='packet').data.length<=512*1024-32);
 // If even minimum quality exceeds the cap, do not advance the damage
 // baseline or emit an invalid packet. The next input must be a full refresh.
 const overflow=load({size:()=>600000});
 await overflow.send({type:'start',codec:'jpeg',profile:'cinema',width:1920,fps:30});await overflow.frame(0,1920,1080);
 assert.equal(overflow.messages.filter(m=>m.type==='packet').length,0);
 assert.equal(overflow.read('oldPixels'),null);assert.equal(overflow.read('first'),true);
 assert.ok(overflow.encodes.length<12);
 // Identical source frames do not consume network credits.
 desktop.time(1);
 await desktop.frame(100000,1920,1080);
 assert.equal(desktop.messages.filter(m=>m.type==='packet').length,1);
 desktop.time(2501);await desktop.frame(200000,1920,1080);
 assert.equal(desktop.messages.filter(m=>m.type==='packet').length,2);
 for(const [fps,level] of [[30,'28'],[60,'2A']]){
  const avc=load();await avc.send({type:'start',codec:'h264',profile:'cinema',width:1920,fps});const input=await avc.frame(0,1920,1080);
  assert.equal(avc.videoConfigs[0].codec,'avc1.42E0'+level);
  assert.equal(avc.videoConfigs[0].width,1920);assert.equal(avc.videoConfigs[0].height,1080);
  assert.ok(avc.videoConfigs[0].bitrate>6000000&&avc.videoConfigs[0].bitrate<=12000000);
  assert.equal(avc.draws,0,'Native-size H.264 encode must skip OffscreenCanvas blit');
  assert.equal(avc.encodedCalls[0].frame,input,'Native-size H.264 encode must pass input VideoFrame directly');
  // Emit output with duration and confirm 32-byte SV2 headroom and processed ack.
  avc.encoder.init.output(
   {byteLength:16,timestamp:0,duration:33333,type:'key',copyTo(dst){dst.fill(7);}},
   {decoderConfig:{codec:'avc1.42E0'+level,description:new Uint8Array([1,2,3])}}
  );
  const initPkt=avc.messages.find(m=>m.type==='packet'&&m.kind===1);
  assert.equal(initPkt.data.byteOffset,32);assert.equal(initPkt.data.buffer.byteLength,32+initPkt.data.byteLength);
  assert.ok(avc.messages.some(m=>m.type==='processed'));
 }
 // Phase-locked 30fps pacing drops intermediate 60Hz frames cleanly.
 const paced=load();await paced.send({type:'start',codec:'jpeg',profile:'cinema',width:1280,fps:30});
 for(let i=0;i<5;i++)await paced.frame(Math.round(i*16667),1280,720,i+1);
 assert.equal(paced.messages.filter(m=>m.type==='packet').length,3,'60Hz source at 30fps must emit 3 of 5 frames');
 const pkt0=paced.messages.find(m=>m.type==='packet');
 assert.equal(pkt0.data.byteOffset,32,'JPEG packet must preserve 32-byte SV2 headroom');
 assert.equal(pkt0.data.buffer.byteLength,32+pkt0.data.byteLength);
 // Dynamic resize reconfigures worker cleanly.
 const resized=load();await resized.send({type:'start',codec:'jpeg',profile:'cinema',width:1280,fps:30});
 await resized.frame(0,1280,720,1);
 await resized.frame(40000,960,540,2);
 const cfgs=resized.messages.filter(m=>m.type==='configure');
 assert.equal(cfgs.length,2);assert.equal(cfgs[1].width,960);assert.equal(cfgs[1].height,540);
 // Smart PNG skip: high-entropy >12 dirty tiles skips PNG, small flat tiles attempt PNG.
 const highEntropy=load({pixels:(w,h,seed)=>{
  const d=new Uint8ClampedArray(w*h*4);
  if(seed===1)return d;
  for(let y=0;y<320;y++)for(let x=0;x<w;x++){
   const i=(y*w+x)*4;d[i]=(x*17+seed)%256;d[i+1]=(y*31)%256;d[i+2]=((x^y)*13)%256;d[i+3]=255;
  }
  return d;
 }});
 await highEntropy.send({type:'start',codec:'jpeg',profile:'balanced',width:1280,fps:30});
 await highEntropy.frame(0,1280,720,1);
 highEntropy.encodes.length=0;
 await highEntropy.frame(40000,1280,720,2);
 assert.equal(highEntropy.encodes.filter(e=>e.type==='image/png').length,0,'High-entropy >12 tile updates must skip PNG trial');
 console.log('Video worker: Full HD, legacy HD, quality bounds, hysteresis, payload retries and unchanged-frame suppression passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
