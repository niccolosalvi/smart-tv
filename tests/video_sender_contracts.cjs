// Verify the real sender requests the selected capture size/profile and keeps
// the existing capture lifecycle. No browser, TV commands or sockets are used.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/share.js'),'utf8');

async function exercise(profile,width,fps=24,transport='jpeg',avc=false,testBack=false){
 const elements=new Map(),workers=[],sockets=[],fetchRequests=[];let request=null;
 const get=id=>{
  if(!elements.has(id))elements.set(id,{value:'',disabled:false,hidden:false,textContent:'',listeners:{},attrs:{},
   getAttribute(k){return Object.prototype.hasOwnProperty.call(this.attrs,k)?this.attrs[k]:null;},
   setAttribute(k,v){this.attrs[k]=String(v);},
   addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);},
   closest(){return {open:false};},async play(){}});
  return elements.get(id);
 };
 get('share-config').textContent=JSON.stringify({server:'http://127.0.0.1:8765',port:8767,key:'test',available:true});
 get('video-profile').value=profile;get('width').value=String(width);get('fps').value=String(fps);get('transport').value=transport;get('audio-enabled').checked=false;
 const constraints=[];
 const track={contentHint:'',async applyConstraints(value){constraints.push(value);},getSettings:()=>({displaySurface:'browser'}),stop(){this.stopped=true;}};
 const stream={getVideoTracks:()=>[track],getAudioTracks:()=>[],getTracks:()=>[track]};
 class Socket{static OPEN=1;constructor(){this.readyState=1;this.bufferedAmount=0;this.sent=[];sockets.push(this);}send(d){this.sent.push(d);}close(){this.readyState=3;}}
 class Worker{constructor(){this.messages=[];workers.push(this);}postMessage(message){this.messages.push(message);}terminate(){this.terminated=true;}}
 class Processor{constructor(){this.readable={getReader:()=>({read:()=>new Promise(()=>{}),cancel:async()=>{},releaseLock(){}})};}}
 const loc={hash:'',href:'http://127.0.0.1:8765/share',origin:'http://127.0.0.1:8765'};
 const context=vm.createContext({URL,Map,Promise,AbortSignal,VideoEncoder:class{},WebSocket:Socket,Worker,VideoFrame:class{},MediaStreamTrackProcessor:Processor,
  performance:{now:()=>0},setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
  window:{isSecureContext:true,Worker,VideoFrame:class{},addEventListener(){}},
  location:loc,document:{getElementById:get,hidden:false,body:{classList:{toggle(){}}},addEventListener(){}},
  navigator:{mediaDevices:{async getDisplayMedia(options){request=options;return stream;}}},
  fetch:async(url,options)=>{fetchRequests.push({url,options});return {ok:true,json:async()=>({receiver_connected:true,status:'sent'})};}});
 vm.runInContext(source,context);await get('start').onclick();
 assert.equal(request.video.width.max,width);assert.equal(request.video.height.max,width>1280?1080:720);
 assert.equal(request.video.frameRate.max,Math.min(fps,transport==='jpeg'?30:60));
 assert.equal(track.contentHint,profile==='cinema'?'motion':'detail');
 assert.equal(get('video-profile').disabled,true);
 sockets[0].onmessage({data:JSON.stringify({type:'ready',window:3,viewer_connected:true,capabilities:{avc}})});
 assert.equal(workers[0].messages[0].profile,profile);assert.equal(workers[0].messages[0].width,width);
 assert.equal(workers[0].messages[0].fps,Math.min(fps,transport!=='jpeg'&&avc?60:30));
 assert.equal(constraints.at(-1).frameRate.max,workers[0].messages[0].fps);
 if(transport!=='jpeg'&&avc){
  workers[0].onmessage({data:{type:'mode',mode:'jpeg'}});
  assert.equal(constraints.at(-1).frameRate.max,Math.min(fps,30),'Fallback must also reduce capture rate');
 }
 // One worker reservation prevents a backlog of stale input frames; release
 // it only after processing. Busy frames overwrite the 1-slot latestFrame buffer
 // and close superseded frames instead of stalling or dropping the newest frame.
 vm.runInContext('push({id:1,close(){this.closed=true;}})',context);
 vm.runInContext('push({id:2,close(){this.closed=true;}})',context);
 assert.equal(workers[0].messages.filter(m=>m.type==='frame').length,1);
 workers[0].onmessage({data:{type:'processed'}});
 assert.equal(workers[0].messages.filter(m=>m.type==='frame').length,2);
 vm.runInContext('globalThis.__f3={id:3,closed:false,close(){this.closed=true;}};globalThis.__f4={id:4,closed:false,close(){this.closed=true;}};push(globalThis.__f3);push(globalThis.__f4);',context);
 assert.equal(workers[0].messages.filter(m=>m.type==='frame').length,2);
 assert.equal(context.__f3.closed,true,'Overwritten latestFrame must be closed');
 assert.equal(context.__f4.closed,false);
 workers[0].onmessage({data:{type:'processed'}});
 assert.equal(workers[0].messages.filter(m=>m.type==='frame').at(-1).frame.id,4,'Latest frame must dispatch immediately on processed');

 // Zero-copy SV2 packet send reuses 32-byte headroom ArrayBuffer.
 const raw=new ArrayBuffer(40),payloadView=new Uint8Array(raw,32);payloadView.set([1,2,3,4,5,6,7,8]);
 const sentCountBefore=sockets[0].sent.length;
 workers[0].onmessage({data:{type:'packet',kind:4,width:640,height:360,rect:[0,0,640,360],key:true,data:payloadView}});
 assert.equal(sockets[0].sent.length,sentCountBefore+1);
 assert.equal(sockets[0].sent.at(-1),raw,'Zero-copy packet must reuse headroom ArrayBuffer');
 assert.equal(new DataView(raw).getUint32(0),0x53563200);

 // H.264 uses windowSize-1 before init (kind=1), then full windowSize credits after init.
 if(transport!=='jpeg'&&avc){
  sockets[0].onmessage({data:JSON.stringify({type:'ready',window:4,viewer_connected:true,capabilities:{avc:true}})});
  const w=workers.at(-1);
  w.onmessage({data:{type:'configure',codec:'h264',width:1280,height:720,mime:'video/mp4; codecs="avc1.42E029"'}});
  assert.equal(vm.runInContext('pending.size',context),0);
  // Emit 3 packets before init: 4th frame blocked until ack or init; after kind=1 init, credits become 4.
  w.onmessage({data:{type:'packet',kind:1,width:1280,height:720,rect:[0,0,0,0],key:true,data:new Uint8Array([1,2,3])}});
  assert.equal(vm.runInContext('avcInitSent',context),true);
  assert.equal(vm.runInContext('canSendFrame()',context),true);
  for(let k=0;k<3;k++)w.onmessage({data:{type:'packet',kind:2,width:1280,height:720,rect:[0,0,0,0],key:false,data:new Uint8Array([4,5,6])}});
  assert.equal(vm.runInContext('pending.size',context),4,'H.264 after init must allow full windowSize=4 inflight packets');
 }

 if(testBack){
  let prevented=false;
  const clickHandlers=get('back').listeners.click||[];
  assert.equal(clickHandlers.length,1);
  await clickHandlers[0]({preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.equal(track.stopped,true);
  assert.equal(workers[0].terminated,true);
  const cmds=fetchRequests.filter(r=>r.url==='/api/command').map(r=>JSON.parse(r.options.body));
  assert.deepEqual(cmds,[{key:'KEY_TV'}]);
  assert.equal(loc.href,'/');
 }else{
  get('stop').onclick();assert.equal(track.stopped,true);assert.equal(workers[0].terminated,true);assert.equal(get('video-profile').disabled,false);
 }
}
(async()=>{await exercise('cinema',1920);await exercise('balanced',1280);await exercise('cinema',1280,120);await exercise('cinema',1280,90,'auto');await exercise('cinema',1280,120,'auto',true,true);console.log('Video sender: capture bounds, pacing, bounded worker input and cleanup passed.');})()
 .catch(error=>{console.error(error);process.exitCode=1;});
