// Verify the real sender requests the selected capture size/profile and keeps
// the existing capture lifecycle. No browser, TV commands or sockets are used.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/share.js'),'utf8');

async function exercise(profile,width,fps=24,transport='jpeg',avc=false){
 const elements=new Map(),workers=[],sockets=[];let request=null;
 const get=id=>{
  if(!elements.has(id))elements.set(id,{value:'',disabled:false,hidden:false,textContent:'',getAttribute(){return null;},
   addEventListener(){},closest(){return {open:false};},async play(){}});
  return elements.get(id);
 };
 get('share-config').textContent=JSON.stringify({server:'http://127.0.0.1:8765',port:8767,key:'test',available:true});
 get('video-profile').value=profile;get('width').value=String(width);get('fps').value=String(fps);get('transport').value=transport;get('audio-enabled').checked=false;
 const constraints=[];
 const track={contentHint:'',async applyConstraints(value){constraints.push(value);},getSettings:()=>({displaySurface:'browser'}),stop(){this.stopped=true;}};
 const stream={getVideoTracks:()=>[track],getAudioTracks:()=>[],getTracks:()=>[track]};
 class Socket{static OPEN=1;constructor(){this.readyState=1;this.bufferedAmount=0;sockets.push(this);}send(){}close(){this.readyState=3;}}
 class Worker{constructor(){this.messages=[];workers.push(this);}postMessage(message){this.messages.push(message);}terminate(){this.terminated=true;}}
 class Processor{constructor(){this.readable={getReader:()=>({read:()=>new Promise(()=>{}),cancel:async()=>{},releaseLock(){}})};}}
 const context=vm.createContext({URL,Map,Promise,AbortSignal,VideoEncoder:class{},WebSocket:Socket,Worker,VideoFrame:class{},MediaStreamTrackProcessor:Processor,
  performance:{now:()=>0},setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
  window:{isSecureContext:true,Worker,VideoFrame:class{},addEventListener(){}},
  location:{hash:'',origin:'http://127.0.0.1:8765'},document:{getElementById:get,hidden:false,body:{classList:{toggle(){}}},addEventListener(){}},
  navigator:{mediaDevices:{async getDisplayMedia(options){request=options;return stream;}}},
  fetch:async()=>({ok:true,json:async()=>({receiver_connected:true,status:'sent'})})});
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
 // it only after processing. Busy frames are closed without being transferred.
 vm.runInContext('push({close(){}})',context);
 vm.runInContext('push({close(){}})',context);
 assert.equal(workers[0].messages.filter(m=>m.type==='frame').length,1);
 workers[0].onmessage({data:{type:'processed'}});
 vm.runInContext('push({close(){}})',context);
 assert.equal(workers[0].messages.filter(m=>m.type==='frame').length,2);
 get('stop').onclick();assert.equal(track.stopped,true);assert.equal(workers[0].terminated,true);assert.equal(get('video-profile').disabled,false);
}
(async()=>{await exercise('cinema',1920);await exercise('balanced',1280);await exercise('cinema',1280,120);await exercise('cinema',1280,90,'auto');await exercise('cinema',1280,120,'auto',true);console.log('Video sender: capture bounds, pacing, bounded worker input and cleanup passed.');})()
 .catch(error=>{console.error(error);process.exitCode=1;});
