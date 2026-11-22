const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const calls=[],events={},status={style:{},textContent:''};let prepared,listener,payload;
const player={open:url=>calls.push(['open',url]),close:()=>calls.push(['close']),stop:()=>calls.push(['stop']),
 setDisplayRect(){},setDisplayMethod(){},setBufferingParam:(...args)=>calls.push(['buffer',...args]),
 setTimeoutForBuffering:value=>calls.push(['timeout',value]),setListener:value=>listener=value,
 prepareAsync:success=>{prepared=success;},play:()=>calls.push(['play'])};
const app={getRequestedAppControl:()=>({appControl:{data:[{key:'PAYLOAD',value:[JSON.stringify(payload)]}]}}),exit:()=>calls.push(['exit'])};
const context=vm.createContext({window:{webapis:{avplay:player},addEventListener:(name,fn)=>events[name]=fn},
 tizen:{application:{getCurrentApplication:()=>app}},document:{getElementById:()=>status,addEventListener:(name,fn)=>events[name]=fn,hidden:false}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../tv-receiver/receiver.js'),'utf8'),context);
payload={url:'http://192.0.2.1:8765/native/'+'a'.repeat(32)+'/stream.ts'};events.load();
assert.ok(calls.some(c=>c[0]==='open'&&c[1]===payload.url));
assert.equal(calls.filter(c=>c[0]==='buffer').length,2);assert.ok(calls.filter(c=>c[0]==='buffer').every(c=>c[3]===4));
assert.ok(calls.some(c=>c[0]==='timeout'&&c[1]===3));
prepared();assert.equal(calls.filter(c=>c[0]==='play').length,1);
events.unload();prepared();assert.equal(calls.filter(c=>c[0]==='play').length,1,'Stale prepare callback must not restart playback');
payload={url:'https://example.com/other'};events.appcontrol();assert.equal(calls.filter(c=>c[0]==='open').length,1);
console.log('Tizen receiver: controlled deep link, documented buffer minimum and stale callbacks passed. Hardware/signing not tested.');
