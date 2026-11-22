/* Encoding runs off the UI thread. VideoFrames are transferred, never copied
 * through JSON. Low-delay AVC batches at most 2 frames at >=45fps; network credits bound queues. */
'use strict';
importScripts('/assets/mp4.js');
let mode='jpeg', options, encoder=null, previous=null, baseTimestamp=null;
let canvas=null, ctx=null, patch=null, patchCtx=null, oldPixels=null, tileFlags=null;
let width=0,height=0,nextInput=-Infinity,lastKey=-Infinity,sequence=0,quality=.72;
let busy=false, configured=false, first=true, jpegKeyTime=0, avcDescription=null, stopped=false;
let fragments=[],fragmentKeys=false;
let cinema=false,lastFeedback=-Infinity,preProbe=null,preProbeKey='';
const colorSeen=new Uint16Array(32768);
let colorStamp=0;
const MAX_IMAGE_BYTES=512*1024-32;
const post=data=>self.postMessage(data,data.data?[data.data.buffer]:[]);
function withHeadroom(bytes){
 const raw=new ArrayBuffer(32+bytes.byteLength),view=new Uint8Array(raw,32);
 view.set(bytes);
 return view;
}
function size(frame){
 const scale=Math.min(1,options.width/frame.displayWidth,(options.width>1280?1080:720)/frame.displayHeight);
 return [Math.max(2,Math.floor(frame.displayWidth*scale/2)*2),Math.max(2,Math.floor(frame.displayHeight*scale/2)*2)];
}
function ensureCanvas(readFrequently){
 if(!canvas){
  canvas=new OffscreenCanvas(width,height);
  ctx=canvas.getContext('2d',{willReadFrequently:readFrequently,alpha:false})||canvas.getContext('2d',{willReadFrequently:readFrequently});
 }
}
function configure(codec,mime){post({type:'configure',codec,width,height,mime:mime||''});configured=true;}
function emit(kind,data,rect,key=false,count=1){post({type:'packet',kind,data,width,height,rect:rect||[0,0,width,height],key,count});}
function resetToJPEG(reason){
 if(encoder){try{encoder.close();}catch(ignored){}encoder=null;}
 previous=null;fragments=[];fragmentKeys=false;mode='jpeg';configured=false;first=true;oldPixels=null;tileFlags=null;
 post({type:'mode',mode:'jpeg',reason:String(reason).slice(0,100)});
}
function failToJPEG(reason){
 resetToJPEG(reason);
 post({type:'processed'});
}
function buildAvcConfig(w,h,rate){
 const level=w>1280||h>720?(rate<=30?'28':'2A'):(rate<=30?'1F':'29');
 return {codec:'avc1.42E0'+level,width:w,height:h,framerate:rate,
  bitrate:Math.round(Math.max(600000,Math.min(cinema?12000000:6000000,w*h*rate*(cinema?.20:.12)))),
  latencyMode:'realtime',hardwareAcceleration:'prefer-hardware',avc:{format:'avc'}};
}
async function probeAvc(cfg){
 let support=await VideoEncoder.isConfigSupported(cfg);
 if(!support.supported){delete cfg.hardwareAcceleration;support=await VideoEncoder.isConfigSupported(cfg);}
 return support;
}
async function setup(frame){
 [width,height]=size(frame);
 if(mode==='h264'&&typeof VideoEncoder==='function'){
  const rate=Math.min(options.fps,60);
  const cfg=buildAvcConfig(width,height,rate);
  const key=width+'x'+height+'@'+rate+':'+cinema;
  const support=(preProbe&&preProbeKey===key)?await preProbe:await probeAvc(cfg);
  preProbe=null;preProbeKey='';
  if(!support.supported){resetToJPEG('H.264 non disponibile sul PC');return;}
  encoder=new VideoEncoder({
   output(chunk,metadata){
    if(stopped||mode!=='h264')return;
    try{
     if(metadata&&metadata.decoderConfig&&metadata.decoderConfig.description){
      const desc=new Uint8Array(metadata.decoderConfig.description);
      const changed=avcDescription&&(desc.length!==avcDescription.length||!desc.every((v,i)=>v===avcDescription[i]));
      if(changed){failToJPEG('Configurazione AVC variata');return;}
      if(!avcDescription){
       avcDescription=desc.slice();
       const codec=metadata.decoderConfig.codec||cfg.codec;
       configure('h264','video/mp4; codecs="'+codec+'"');
       emit(1,withHeadroom(ScreenMP4.init(width,height,avcDescription)));
      }
     }
     const data=new Uint8Array(chunk.byteLength);chunk.copyTo(data);
     if(baseTimestamp===null)baseTimestamp=chunk.timestamp;
     const ts=Math.max(0,chunk.timestamp-baseTimestamp),key=chunk.type==='key';
     const rawDur=typeof chunk.duration==='number'&&chunk.duration>0?chunk.duration:0;
     const pushSample=(sampleData,sampleTs,sampleDur,sampleKey)=>{
      fragments.push({data:sampleData,timestamp:sampleTs,duration:Math.max(1000,Math.min(500000,Math.round(sampleDur))),key:sampleKey});
      fragmentKeys=fragmentKeys||sampleKey;
     };
     if(previous){
      pushSample(previous.data,previous.timestamp,ts-previous.timestamp,previous.key);
      previous=null;
     }
     if(rawDur)pushSample(data,ts,rawDur,key);
     else previous={data,timestamp:ts,key};
     const group=rate>=45?2:1;
     if(fragments.length>=group){
      const mp4=fragments.length===1
       ?ScreenMP4.fragment(++sequence,fragments[0].timestamp,fragments[0].duration,fragments[0].data,fragments[0].key)
       :ScreenMP4.fragmentBatch(++sequence,fragments);
      emit(2,withHeadroom(mp4),null,fragmentKeys,fragments.length);
      fragments=[];fragmentKeys=false;
     }
     post({type:'processed'});
    }catch(error){failToJPEG(error.message);}
   },error(error){if(!stopped)failToJPEG(error.message);}
  });
  encoder.configure(support.config);
  post({type:'mode',mode:'h264',bitrate:cfg.bitrate});
 }else mode='jpeg';
}
function dirtyTiles(pixels,force){
 // Exact scanline pixel comparison with early abort when >45% of tiles differ.
 const current=new Uint32Array(pixels.buffer,pixels.byteOffset,pixels.byteLength>>2);
 if(force||!oldPixels)return {tiles:null,current};
 const columns=(width+63)>>6,rows=(height+63)>>6;
 const totalTiles=columns*rows,limit=Math.floor(totalTiles*.45);
 if(!tileFlags||tileFlags.length!==totalTiles)tileFlags=new Uint8Array(totalTiles);
 else tileFlags.fill(0);
 let dirtyCount=0;
 for(let tr=0;tr<rows;tr++){
  const ty=tr<<6,bottom=Math.min(height,ty+64),rowBase=tr*columns;
  for(let y=ty;y<bottom;y++){
   const yOff=y*width;
   for(let tc=0;tc<columns;tc++){
    const idx=rowBase+tc;
    if(tileFlags[idx])continue;
    const tx=tc<<6,right=Math.min(width,tx+64);
    for(let x=tx;x<right;x++){
     const p=yOff+x;
     if(current[p]!==oldPixels[p]){
      tileFlags[idx]=1;
      if(++dirtyCount>limit)return {tiles:null,current};
      break;
     }
    }
   }
  }
 }
 const changed=[];
 for(let tr=0;tr<rows;tr++){
  const rowBase=tr*columns;
  for(let tc=0;tc<columns;tc++)if(tileFlags[rowBase+tc])changed.push([tc<<6,tr<<6]);
 }
 return {tiles:changed,current};
}
function countUniqueColors(current,tiles){
 if(++colorStamp===65535){colorSeen.fill(0);colorStamp=1;}
 const stamp=colorStamp;
 let unique=0;
 if(!tiles){
  const stepX=Math.max(1,Math.floor(width/64)),stepY=Math.max(1,Math.floor(height/36));
  for(let y=0;y<height;y+=stepY){
   const row=y*width;
   for(let x=0;x<width;x+=stepX){
    const p=current[row+x];
    const key=((p&0xF8)<<7)|((p>>6)&0x3E0)|((p>>19)&0x1F);
    if(colorSeen[key]!==stamp){
     colorSeen[key]=stamp;
     if(++unique>=320)return unique;
    }
   }
  }
  return unique;
 }
 const stride=Math.max(1,Math.floor(Math.sqrt(tiles.length*4096/2304)));
 for(let t=0;t<tiles.length;t++){
  const tx=tiles[t][0],ty=tiles[t][1];
  const right=Math.min(width,tx+64),bottom=Math.min(height,ty+64);
  for(let y=ty;y<bottom;y+=stride){
   const row=y*width;
   for(let x=tx;x<right;x+=stride){
    const p=current[row+x];
    const key=((p&0xF8)<<7)|((p>>6)&0x3E0)|((p>>19)&0x1F);
    if(colorSeen[key]!==stamp){
     colorSeen[key]=stamp;
     if(++unique>=320)return unique;
    }
   }
  }
 }
 return unique;
}
async function jpeg(frame){
 const now=performance.now();
 ensureCanvas(!cinema);
 ctx.drawImage(frame,0,0,width,height);
 const key=first||now-jpegKeyTime>2000;
 // Film frames are sent whole without pixel readback or PNG trial.
 // Desktop profile keeps exact damage tracking and lossless UI patches.
 let damage=null,tiles=null;
 if(!cinema){
  damage=dirtyTiles(ctx.getImageData(0,0,width,height).data,key);
  tiles=damage.tiles;
 }
 if(tiles&&tiles.length===0){post({type:'processed'});return;}
 if(!configured)configure('jpeg');
 const columns=tiles?Math.min(8,tiles.length):0;
 const rect=tiles?[columns,64,columns*64,Math.ceil(tiles.length/columns)*64]:[0,0,width,height];
 const w=rect[2],h=rect[3];
 if(tiles){
  if(!patch){
   patch=new OffscreenCanvas(w,h);
   patchCtx=patch.getContext('2d',{alpha:false})||patch.getContext('2d');
  }else{
   if(patch.width!==w)patch.width=w;
   if(patch.height!==h)patch.height=h;
  }
  patchCtx.clearRect(0,0,w,h);
  for(let i=0;i<tiles.length;i++){
   const tx=tiles[i][0],ty=tiles[i][1];
   const tw=Math.min(64,width-tx),th=Math.min(64,height-ty);
   patchCtx.drawImage(canvas,tx,ty,tw,th,(i%columns)*64,Math.floor(i/columns)*64,tw,th);
  }
 }
 const surface=tiles?patch:canvas;
 let blob=await surface.convertToBlob({type:'image/jpeg',quality}),png=false;
 const uniqueColors=damage?countUniqueColors(damage.current,tiles):320;
 if(!cinema&&(uniqueColors<320||(tiles&&tiles.length<=12))){
  const lossless=await surface.convertToBlob({type:'image/png'});
  if(lossless.size<blob.size*.95){blob=lossless;png=true;}
 }
 // Retry this very frame before abandoning it. A scene cut must not leave
 // the damage baseline ahead of what was actually sent to the receiver.
 const headerBytes=tiles?2+tiles.length*4:0;
 let actualQuality=quality;
 while(blob.size+headerBytes>MAX_IMAGE_BYTES&&actualQuality>.4){
  const step=Math.max(.08,Math.min(.20,((blob.size+headerBytes)/MAX_IMAGE_BYTES-1)*.35));
  actualQuality=Math.max(.4,actualQuality-step);
  blob=await surface.convertToBlob({type:'image/jpeg',quality:actualQuality});png=false;
 }
 if(blob.size+headerBytes>MAX_IMAGE_BYTES){first=true;post({type:'processed'});return;}
 quality=Math.min(quality,Math.max(cinema?.78:.4,actualQuality));
 const blobBuf=await blob.arrayBuffer();
 const raw=new ArrayBuffer(32+headerBytes+blobBuf.byteLength);
 const data=new Uint8Array(raw,32);
 if(tiles){
  const view=new DataView(raw,32);
  view.setUint16(0,tiles.length);
  for(let i=0;i<tiles.length;i++){
   view.setUint16(2+i*4,tiles[i][0]);
   view.setUint16(4+i*4,tiles[i][1]);
  }
 }
 data.set(new Uint8Array(blobBuf),headerBytes);
 emit(tiles?(png?6:5):(png?7:4),data,rect,key||first);
 if(damage)oldPixels=damage.current;first=false;if(key)jpegKeyTime=now;
 post({type:'processed'});
}
self.onmessage=async event=>{
 const data=event.data;
 if(data.type==='start'){
  options=data;mode=data.codec;cinema=data.profile==='cinema';quality=cinema?.90:.72;
  if(mode==='h264'&&typeof VideoEncoder==='function'){
   const rate=Math.min(options.fps,60),w=Math.max(2,Math.floor(options.width/2)*2);
   const h=Math.max(2,Math.floor(Math.min(w>1280?1080:720,w*9/16)/2)*2);
   preProbeKey=w+'x'+h+'@'+rate+':'+cinema;
   preProbe=probeAvc(buildAvcConfig(w,h,rate)).catch(()=>({supported:false}));
  }
  return;
 }
 if(data.type==='feedback'){
  const now=performance.now();if(now-lastFeedback<500)return;lastFeedback=now;
  const period=1000/Math.min(options.fps,30);
  const slow=data.rtt>Math.max(250,period*6)||(data.decodeMs>period*1.3);
  const fast=data.rtt<Math.max(100,period*3)&&!(data.decodeMs>period);
  if(slow)quality=Math.max(cinema?.78:.4,quality-.02);
  else if(fast)quality=Math.min(cinema?.94:.82,quality+.005);
  return;
 }
 if(data.type!=='frame')return;
 const frame=data.frame;
 if(busy||stopped){frame.close();post({type:'processed'});return;}
 const rate=Math.min(options.fps,mode==='h264'?60:30);
 const period=1000000/rate;
 const tol=Math.min(4000,period*.25);
 if(frame.timestamp<nextInput-tol){frame.close();post({type:'processed'});return;}
 busy=true;
 try{
  const [nextW,nextH]=size(frame);
  if(width&&(nextW!==width||nextH!==height)){
   if(encoder){try{encoder.close();}catch(ignored){}encoder=null;}
   width=0;height=0;canvas=ctx=patch=patchCtx=oldPixels=tileFlags=null;
   configured=false;first=true;previous=null;fragments=[];fragmentKeys=false;
   avcDescription=null;baseTimestamp=null;sequence=0;
  }
  if(!width)await setup(frame);
  if(stopped)return;
  const advanceInput=()=>{(nextInput===-Infinity||frame.timestamp-nextInput>period)?(nextInput=frame.timestamp+period*.75):(nextInput+=period);};
  if(mode==='h264'&&encoder){
   if(encoder.encodeQueueSize>1){post({type:'processed'});return;}
   advanceInput();
   const key=frame.timestamp-lastKey>1000000;
   if(frame.displayWidth===width&&frame.displayHeight===height){
    encoder.encode(frame,{keyFrame:key});
   }else{
    ensureCanvas(false);
    ctx.drawImage(frame,0,0,width,height);
    const scaled=new VideoFrame(canvas,{timestamp:frame.timestamp,duration:frame.duration||Math.round(period)});
    encoder.encode(scaled,{keyFrame:key});scaled.close();
   }
   if(key)lastKey=frame.timestamp;
  }else{
   advanceInput();
   await jpeg(frame);
  }
 }catch(error){failToJPEG(error.message);}
 finally{frame.close();busy=false;}
};
