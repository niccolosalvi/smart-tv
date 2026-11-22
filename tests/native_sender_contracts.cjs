const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/native-share.js'),'utf8');
function setup(){
 const requests=[],recorders=[],statuses=[],errors=[];
 class Recorder{
  static isTypeSupported(){return true;}
  constructor(stream,options){this.state='inactive';this.options=options;recorders.push(this);}
  start(period){this.period=period;this.state='recording';}
  stop(){this.state='inactive';if(this.onstop)this.onstop();}
 }
 const context=vm.createContext({MediaRecorder:Recorder,Blob,AbortSignal,Error,Promise,
  setInterval:()=>1,clearInterval(){},fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({id:'session',active:true,state:'PLAYING',stream_fps:60})};}});
 vm.runInContext(source+'\nglobalThis.Sender=ScreenNativeSender;',context);
 const sender=new context.Sender({server:'https://pi:8766',key:'private'},s=>statuses.push(s),e=>errors.push(e));
 const stream={getVideoTracks:()=>[{getSettings:()=>({width:1920,height:1080,frameRate:60})}]};
 return {sender,requests,recorders,statuses,errors,stream,context};
}
(async()=>{
 const t=setup();await t.sender.start(t.stream,{width:1920,fps:60});
 assert.equal(t.recorders[0].period,500);assert.equal(t.recorders[0].options.videoBitsPerSecond,8000000);
 assert.equal(t.recorders[0].options.videoKeyFrameIntervalDuration,2000);
 const blob=new Blob(['fragment']);t.recorders[0].ondataavailable({data:blob});t.recorders[0].ondataavailable({data:blob});
 await t.sender.chain;
 assert.equal(t.requests.filter(r=>r.url.endsWith('/chunk')).length,2);assert.equal(t.sender.queued,0);
 assert.equal(await t.requests[1].options.body.text(),'fragment');assert.equal(t.requests[1].options.headers['X-Remote-Key'],'private');
 // Coalesce multiple small fragments queued while an upload is in flight into a single POST.
 const coal=setup();await coal.sender.start(coal.stream,{width:1920,fps:60});
 coal.recorders[0].ondataavailable({data:new Blob(['part1-'])});
 coal.recorders[0].ondataavailable({data:new Blob(['part2-'])});
 coal.recorders[0].ondataavailable({data:new Blob(['part3'])});
 await coal.sender.chain;
 const coalChunks=coal.requests.filter(r=>r.url.endsWith('/chunk'));
 assert.equal(coalChunks.length,2,'In-flight queued fragments must coalesce into one follow-up POST');
 assert.equal(await coalChunks[0].options.body.text(),'part1-');
 assert.equal(await coalChunks[1].options.body.text(),'part2-part3');
 await coal.sender.stop();
 await t.sender.poll();assert.ok(t.statuses.at(-1).includes('flusso 60'));
 await t.sender.stop();assert.equal(t.recorders[0].state,'inactive');assert.ok(t.requests.at(-1).url.endsWith('/stop'));
 t.recorders[0].ondataavailable({data:blob});await t.sender.chain;assert.equal(t.requests.filter(r=>r.url.endsWith('/chunk')).length,2);
 const bad=setup();await bad.sender.start(bad.stream,{width:1920,fps:60});
 bad.recorders[0].ondataavailable({data:new Blob([new Uint8Array(24*1024*1024+1)])});assert.equal(bad.errors.length,1);await bad.sender.stop();
 const large=setup();await large.sender.start(large.stream,{width:1920,fps:60});
 large.recorders[0].ondataavailable({data:new Blob([new Uint8Array(3*1024*1024)])});await large.sender.chain;
 const pieces=large.requests.filter(r=>r.url.endsWith('/chunk'));assert.equal(pieces.length,3);assert.ok(pieces.every(r=>r.options.body.size<=1024*1024));await large.sender.stop();
 const cancelled=setup();let release;
 cancelled.context.fetch=async(url,options)=>{cancelled.requests.push({url,options});if(url.endsWith('/start'))await new Promise(resolve=>release=resolve);return {ok:true,json:async()=>({id:'session'})};};
 const start=cancelled.sender.start(cancelled.stream,{width:1920,fps:60});await cancelled.sender.stop();release();await start;
 assert.equal(cancelled.recorders.length,0);assert.ok(cancelled.requests.at(-1).url.endsWith('/stop'));
 console.log('Native sender: A/V encoding, ordered bounded uploads, metrics, cleanup and cancelled startup passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
