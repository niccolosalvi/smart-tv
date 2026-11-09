// Execute the real inline controller with a minimal DOM, without network or TV access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const script = html.match(/<script nonce="__CSRF__">([\s\S]*?)<\/script>/)[1];

class Element {
 constructor() {
  this.children=[];this.dataset={};this.attrs={};this.events={};this.value='';
  this.hidden=false;this.scrollHeight=0;this.scrollTop=0;this.clientHeight=0;
  this.classList={toggle:()=>{}};
 }
 set textContent(value){this.text=String(value);this.children=[];}
 get textContent(){return this.text||'';}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(key,value){this.attrs[key]=value;}
 removeAttribute(key){delete this.attrs[key];}
 addEventListener(type,handler){(this.events[type]??=[]).push(handler);}
 get firstElementChild(){return this.children[0];}
}

function load(saved) {
 const ids=new Map();
 for(const match of html.matchAll(/\bid="([^"]+)"/g))ids.set(match[1],new Element());
 const events={};
 const storage=new Map(saved?[['salotto_favorites',saved]]:[]);
 const requests=[];
 const document={hidden:true,
  querySelector:selector=>ids.get(selector.slice(1))||null,
  getElementById:id=>ids.get(id)||null,
  querySelectorAll:()=>[],
  createElement:()=>new Element(),
  addEventListener:(type,handler)=>(events[type]??=[]).push(handler)
 };
 const context=vm.createContext({document,
  localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
  location:{origin:'http://example.test',host:'example.test',reload:()=>{}},
  navigator:{},setInterval:()=>{},AbortSignal,performance,URL,console,
  fetch:async(url,options)=>{
   requests.push({url,options});
   return {ok:false,status:503,json:async()=>({error:'Server non disponibile'})};
  }
 });
 vm.runInContext(script,context);
 return {ids,storage,requests,events,context,run:code=>vm.runInContext(code,context)};
}

(async()=>{
 const app=load();
 assert.equal(app.ids.get('channel-directory').children.length,45);
 assert.equal(app.ids.get('favorites').children.length,6);
 // All shared contexts must show the intended panels without touching the TV.
 const visible={
  tv:['channels-panel'],
  youtube:['text-panel','navigation-panel','media-panel','app-volume'],
  netflix:['app-search-unavailable','navigation-panel','media-panel','app-volume'],
  primevideo:['app-search-unavailable','navigation-panel','media-panel','app-volume'],
  pc:['app-volume'],
  screen:['navigation-panel','app-volume','screen-panel'],
  unknown:['text-panel','navigation-panel','media-panel','channels-panel','screen-panel']
 };
 const panels=['text-panel','navigation-panel','media-panel','app-volume',
  'channels-panel','screen-panel','app-search-unavailable'];
 let revision=1;
 for(const [source,expected] of Object.entries(visible)){
  app.run(`renderContext({source:${JSON.stringify(source)},basis:'manual',revision:${revision++}})`);
  for(const id of panels)assert.equal(!app.ids.get(id).hidden,expected.includes(id),source+': '+id);
 }
 assert.equal(app.requests.length,0);
 app.run("toggleFavorite('1')");
 assert.ok(JSON.parse(app.storage.get('salotto_favorites')).includes('1'));
 assert.equal(load(app.storage.get('salotto_favorites')).ids.get('favorites').children.length,7);
 assert.equal(load('{broken').ids.get('favorites').children.length,6);
 assert.equal(load('["1","1","invalid"]').ids.get('favorites').children.length,1);
 app.run("activeCat='ragazzi';renderDirectory()");
 assert.equal(app.ids.get('channel-directory').children.length,7);
 app.run("activeCat='all';channelFilter='Rai Movie';renderDirectory()");
 assert.equal(app.ids.get('channel-directory').children.length,1);
 app.run("channelFilter='99999';renderDirectory()");
 assert.equal(app.ids.get('channel-directory').children.length,0);
 // Exercise the original command path: credential header, one request, no retry.
 app.run('document.hidden=false');
 await app.run("command({channel:'20'})");
 const commandRequests=app.requests.filter(request=>request.url==='/api/command');
 assert.equal(commandRequests.length,1);
 assert.equal(commandRequests[0].options.headers['X-Remote-Key'],'__CSRF__');
 assert.ok(commandRequests[0].options.headers['X-Request-ID']);
 assert.deepEqual(JSON.parse(commandRequests[0].options.body),{channel:'20'});
 assert.match(app.ids.get('status').textContent,/Operazione non confermata/);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(app.run('logFetching'),false);
 assert.equal(app.run('logOffline'),true);
 assert.match(app.ids.get('log-health').textContent,/Log non raggiungibile/);
 app.run('pending=true');
 await app.run("command({power:'off'})");
 assert.equal(app.requests.filter(request=>request.url==='/api/command').length,1);
 console.log('Frontend: contexts, catalogue, favourites, filtering, authenticated commands and logs passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
