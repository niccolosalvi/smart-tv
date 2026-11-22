// Strict ES5 compatibility check + behavioral contracts for web/screen.js.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function stripNonCode(source){
 // Strip comments, strings and regex literals so ES5 keyword checks don't false-trigger.
 let out='',i=0,n=source.length,lastSig='';
 while(i<n){
  const c=source[i],next=source[i+1];
  if(c==='/'&&next==='*'){
   i+=2;while(i<n&&!(source[i]==='*'&&source[i+1]==='/'))i++;i+=2;out+=' ';continue;
  }
  if(c==='/'&&next==='/'){
   i+=2;while(i<n&&source[i]!=='\n')i++;continue;
  }
  if(c==="'"||c==='"'){
   const q=c;i++;while(i<n&&source[i]!==q){if(source[i]==='\\')i+=2;else i++;}i++;out+='""';lastSig='"';continue;
  }
  if(c==='/'&&'(,=:[!&|?;{}return'.split('').some(()=>false)||
     (c==='/'&&(!lastSig||'(=,:!&|?[{};'.includes(lastSig)))){
   i++;while(i<n&&source[i]!=='/'){if(source[i]==='\\')i+=2;else i++;}i++;out+='/r/';lastSig='/';continue;
  }
  out+=c;
  if(!/\s/.test(c))lastSig=c;
  i++;
 }
 return out;
}

for(const file of ['../web/screen.js','../web/audio-receiver.js']){
 const raw=fs.readFileSync(path.join(__dirname,file),'utf8');
 const code=stripNonCode(raw);
 assert.doesNotMatch(code,/\b(const|let|class|async|await)\b/,file+' must remain strict ES5');
 assert.doesNotMatch(code,/=>/,file+' must not use arrow functions');
 assert.doesNotMatch(code,/`/,file+' must not use template literals');
 assert.doesNotMatch(code,/for\s*\([^)]*\bof\b/,file+' must not use for..of');
}

const screenSource=fs.readFileSync(path.join(__dirname,'../web/screen.js'),'utf8');

function sv2Packet({kind,seq,x=0,y=0,w=640,h=360,tiles=null,payload=new Uint8Array([0xff,0xd8,1,2,0xff,0xd9])}){
 const tileBytes=tiles?2+tiles.length*4:0;
 const buf=new ArrayBuffer(32+tileBytes+payload.byteLength);
 const view=new DataView(buf);
 view.setUint32(0,0x53563200);view.setUint8(4,kind);view.setUint8(8,seq);
 view.setUint32(8,seq);view.setUint16(12,640);view.setUint16(14,360);
 view.setUint16(16,x);view.setUint16(18,y);view.setUint16(20,w);view.setUint16(22,h);
 if(tiles){
  view.setUint16(32,tiles.length);
  tiles.forEach(([tx,ty],i)=>{view.setUint16(34+i*4,tx);view.setUint16(36+i*4,ty);});
 }
 new Uint8Array(buf,32+tileBytes).set(payload);
 return buf;
}

function makeHarness({withBitmap=true,userAgent='Mozilla/5.0 Chrome/120'}={}){
 const draws=[],acks=[],sockets=[],pendingBitmaps=[],pendingImages=[],displayWrites=[];
 let contextOptions=null,revoked=0,created=0,storageHandler=null;
 const ctx={
  imageSmoothingEnabled:true,
  clearRect(){},
  drawImage(...args){draws.push(args);}
 };
 function trackStyle(name,initial){
  let val=initial;
  return {get display(){return val;},set display(v){displayWrites.push({name,v});val=v;}};
 }
 const canvas={
  width:0,height:0,style:trackStyle('canvas','none'),
  getContext(kind,opts){contextOptions=opts;return ctx;}
 };
 const panel={style:trackStyle('panel','block')};
 const status={textContent:''};
 const image={
  style:trackStyle('image','none'),
  onload:null,onerror:null,
  set src(url){pendingImages.push({url,img:this});},
  removeAttribute(){}
 };
 const video={
  style:trackStyle('video','none'),
  buffered:{length:0,start:()=>0,end:()=>0},
  readyState:4,currentTime:0,playbackRate:1,paused:false,videoWidth:640,
  play:()=>Promise.resolve(),pause(){},load(){},removeAttribute(){}
 };
 const elements={
  'screen-config':{textContent:JSON.stringify({available:true,ws_url:'ws://127.0.0.1:8767/screen',key:'k'})},
  'frame':image,'screen-canvas':canvas,'screen-video':video,'panel':panel,'status':status,
  'enable-audio':{style:{},onclick:null},'reconnect':{onclick:null},'fullscreen':{onclick:null}
 };
 class FakeSocket{
  constructor(){this.readyState=1;this.sent=[];sockets.push(this);}
  send(d){this.sent.push(d);if(typeof d==='string'){const j=JSON.parse(d);if(j.type==='ack')acks.push(j);}}
  close(){this.readyState=3;}
 }
 let sourceBuffer=null,mediaSource=null;
 class FakeSourceBuffer{
  constructor(){this.updating=false;this.listeners={};this.appended=[];sourceBuffer=this;}
  addEventListener(t,fn){(this.listeners[t]??=[]).push(fn);}
  appendBuffer(buf){this.appended.push(buf);}
  remove(){}
  abort(){}
 }
 class FakeMediaSource{
  static isTypeSupported(){return true;}
  constructor(){this.listeners={};mediaSource=this;}
  addEventListener(t,fn){(this.listeners[t]??=[]).push(fn);}
  addSourceBuffer(){return new FakeSourceBuffer();}
 }
 const win={
  WebSocket:FakeSocket,
  MediaSource:FakeMediaSource,
  Blob,
  URL:{createObjectURL:()=>{created++;return 'blob://'+created;},revokeObjectURL:()=>{revoked++;}},
  addEventListener(t,fn){if(t==='storage')storageHandler=fn;}
 };
 if(withBitmap){
  win.createImageBitmap=()=>{
   let resolve,reject;
   const p=new Promise((res,rej)=>{resolve=res;reject=rej;});
   const bmp={closed:false,close(){this.closed=true;}};
   pendingBitmaps.push({resolve:()=>resolve(bmp),reject,bmp});
   return p;
  };
 }
 const globalScope={
  window:win,
  document:{getElementById:id=>elements[id]||null,addEventListener(){},documentElement:{}},
  navigator:{userAgent},
  Blob,Uint8Array,DataView,Promise,
  URL:win.URL,WebSocket:FakeSocket,MediaSource:FakeMediaSource,
  ScreenAudioReceiver:function(){this.open=function(){};this.close=function(){};},
  setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms);if(t.unref)t.unref();return t;},clearTimeout,Date
 };
 if(withBitmap)globalScope.createImageBitmap=win.createImageBitmap;
 vm.runInContext(screenSource,vm.createContext(globalScope));
 const ws=sockets[0];
 ws.onopen();
 return {
  ws,ctx,canvas,video,panel,draws,acks,displayWrites,pendingBitmaps,pendingImages,
  get contextOptions(){return contextOptions;},
  get revoked(){return revoked;},
  get sourceBuffer(){return sourceBuffer;},
  get mediaSource(){return mediaSource;},
  ready(winSize=4){ws.onmessage({data:JSON.stringify({type:'ready',window:winSize})});},
  configure(codec,width=640,height=360,mime='video/mp4; codecs="avc1.42E01F"'){
   ws.onmessage({data:JSON.stringify({type:'configure',codec,width,height,mime})});
  },
  recv(buf){ws.onmessage({data:buf});}
 };
}

(async()=>{
 // 1. Opaque 2D context, negotiated window, zero-copy tile atlas, cached DOM display writes.
 const h=makeHarness({withBitmap:true});
 assert.equal(h.contextOptions&&h.contextOptions.alpha,false);
 h.ready(4);
 h.configure('jpeg',640,360);
 assert.equal(h.ctx.imageSmoothingEnabled,false);

 // Tile atlas packet (kind 5, 2 tiles at (64,0) and (128,64))
 h.recv(sv2Packet({kind:5,seq:1,x:2,y:64,w:128,h:64,tiles:[[64,0],[128,64]]}));
 assert.equal(h.pendingBitmaps.length,1);
 h.pendingBitmaps.shift().resolve();
 await new Promise(r=>setImmediate(r));
 assert.equal(h.draws.length,2);
 assert.deepEqual(Array.from(h.draws[0].slice(1)),[0,0,64,64,64,0,64,64]);
 assert.deepEqual(Array.from(h.draws[1].slice(1)),[64,0,64,64,128,64,64,64]);
 assert.equal(h.acks.length,1);
 assert.equal(typeof h.acks[0].decodeMs,'number');

 // Steady frame should NOT write canvas.style.display or panel.style.display again!
 const writesBefore=h.displayWrites.length;
 h.recv(sv2Packet({kind:4,seq:2,x:0,y:0,w:640,h:360}));
 h.pendingImages.shift().img.onload();
 await new Promise(r=>setImmediate(r));
 assert.equal(h.displayWrites.length,writesBefore,'Steady frames must skip redundant DOM display writes');

 // 2. Full-frame coalescing while one decode is in flight:
 h.recv(sv2Packet({kind:4,seq:3,x:0,y:0,w:640,h:360}));
 h.recv(sv2Packet({kind:5,seq:4,x:1,y:64,w:64,h:64,tiles:[[0,0]]}));
 h.recv(sv2Packet({kind:4,seq:5,x:0,y:0,w:640,h:360}));
 h.recv(sv2Packet({kind:4,seq:6,x:0,y:0,w:640,h:360}));
 const ackCountBefore=h.acks.length;
 h.pendingImages.shift().img.onload();
 await new Promise(r=>setImmediate(r));
 const newAcks=h.acks.slice(ackCountBefore);
 assert.equal(newAcks.length,3,'ACKed seq 3 plus skipped seq 4 and 5');
 assert.equal(newAcks[0].seq,3);assert.equal(typeof newAcks[0].decodeMs,'number');
 assert.equal(newAcks[1].seq,4);assert.equal(newAcks[1].decodeMs,undefined);
 assert.equal(newAcks[2].seq,5);assert.equal(newAcks[2].decodeMs,undefined);
 assert.equal(h.pendingImages.length,1,'Only full6 should enter decode');
 h.pendingImages.shift().img.onload();
 await new Promise(r=>setImmediate(r));
 assert.equal(h.acks.at(-1).seq,6);
 assert.equal(typeof h.acks.at(-1).decodeMs,'number');

 // 3. Incremental tile updates must NEVER be dropped when no later full frame exists.
 h.recv(sv2Packet({kind:4,seq:7,x:0,y:0,w:640,h:360})); // in flight
 h.recv(sv2Packet({kind:5,seq:8,x:1,y:64,w:64,h:64,tiles:[[0,0]]}));
 h.recv(sv2Packet({kind:5,seq:9,x:1,y:64,w:64,h:64,tiles:[[64,0]]}));
 h.pendingImages.shift().img.onload(); // finish 7
 await new Promise(r=>setImmediate(r));
 assert.equal(h.acks.at(-1).seq,7);
 assert.equal(h.pendingBitmaps.length,1);
 h.pendingBitmaps.shift().resolve(); // finish 8
 await new Promise(r=>setImmediate(r));
 assert.equal(h.acks.at(-1).seq,8);
 assert.equal(typeof h.acks.at(-1).decodeMs,'number');
 assert.equal(h.pendingBitmaps.length,1);
 h.pendingBitmaps.shift().resolve(); // finish 9
 await new Promise(r=>setImmediate(r));
 assert.equal(h.acks.at(-1).seq,9);
 assert.equal(typeof h.acks.at(-1).decodeMs,'number');

 // 4. Queue overflow with a full frame flushes instead of falling back.
 h.recv(sv2Packet({kind:4,seq:10,x:0,y:0,w:640,h:360})); // in flight
 for(let s=11;s<=14;s++)h.recv(sv2Packet({kind:5,seq:s,x:1,y:64,w:64,h:64,tiles:[[0,0]]}));
 h.recv(sv2Packet({kind:4,seq:15,x:0,y:0,w:640,h:360}));
 assert.ok(!h.ws.sent.some(m=>typeof m==='string'&&m.includes('"fallback"')),'Full frame on full queue must coalesce, not fallback');

 // 5. Stale bitmap decode after reset() must still close() the ImageBitmap.
 h.pendingImages.shift().img.onload(); // finish 10 -> triggers bitmap for 15? no, 15 is full frame -> Image
 h.pendingImages.shift().img.onload(); // finish 15
 h.recv(sv2Packet({kind:5,seq:16,x:1,y:64,w:64,h:64,tiles:[[0,0]]}));
 const orphan=h.pendingBitmaps.shift();
 h.configure('jpeg',640,360);
 orphan.resolve();
 await new Promise(r=>setImmediate(r));
 assert.equal(orphan.bmp.closed,true,'Orphan bitmap after reset must be closed');

 // 6. Legacy fallback without createImageBitmap uses Image + ObjectURL cleanly for tiles too.
 const legacy=makeHarness({withBitmap:false});
 legacy.ready(3);legacy.configure('jpeg',640,360);
 legacy.recv(sv2Packet({kind:5,seq:1,x:1,y:64,w:64,h:64,tiles:[[0,0]]}));
 assert.equal(legacy.pendingImages.length,1);
 legacy.pendingImages[0].img.onload();
 assert.equal(legacy.acks.length,1);assert.equal(legacy.acks[0].seq,1);
 assert.equal(legacy.revoked,1);

 // 7. H.264 playEdge() live-edge catch-up and immediate ACK in updateend.
 const avc=makeHarness({withBitmap:true});
 avc.ready(4);avc.configure('h264',640,360,'video/mp4; codecs="avc1.42E01F"');
 avc.mediaSource.listeners.sourceopen[0]();
 avc.recv(sv2Packet({kind:1,seq:1,x:0,y:0,w:0,h:0,payload:new Uint8Array([1,2,3,4])}));
 await new Promise(r=>setTimeout(r,10));
 // Within 380ms buffer -> no jump, playbackRate remains 1, immediate ACK sent
 avc.video.buffered={length:1,start:()=>0,end:()=>2.0};
 avc.video.currentTime=1.70;
 avc.sourceBuffer.listeners.updateend[0]();
 assert.equal(avc.acks.length,1,'ACK must be sent immediately on updateend');
 assert.equal(avc.video.currentTime,1.70);
 assert.equal(avc.video.playbackRate,1);

 // Lag exceeds 380ms -> seeks to end-0.12 without playbackRate jitter
 avc.recv(sv2Packet({kind:2,seq:2,x:0,y:0,w:0,h:0,payload:new Uint8Array([5,6,7,8])}));
 avc.video.buffered={length:1,start:()=>0,end:()=>3.0};
 avc.video.currentTime=2.2;
 avc.sourceBuffer.listeners.updateend[0]();
 assert.ok(Math.abs(avc.video.currentTime-2.88)<0.001);
 assert.equal(avc.video.playbackRate,1);

 console.log('Screen receiver: ES5 strictness, zero-copy views, coalescing, createImageBitmap/Image fallback and H.264 pacing passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
