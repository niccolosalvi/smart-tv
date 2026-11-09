/* Encoding runs off the UI thread. VideoFrames are transferred, never copied
 * through JSON. AVC packets group at most three frames; network credits bound queues. */
'use strict';
importScripts('/assets/mp4.js');
let mode='jpeg', options, encoder=null, encoding=false, previous=null, baseTimestamp=null;
let canvas=null, ctx=null, sample=null, sampleCtx=null, oldPixels=null, patch=null;
let width=0,height=0,nextInput=-Infinity,lastKey=-Infinity,sequence=0,quality=.72;
let busy=false, configured=false, first=true, jpegKeyTime=0, avcDescription=null, stopped=false;
let fragments=[],fragmentKeys=false;
const post=data=>self.postMessage(data,data.data?[data.data.buffer]:[]);
function size(frame){
 const scale=Math.min(1,options.width/frame.displayWidth,720/frame.displayHeight);
 return [Math.max(2,Math.floor(frame.displayWidth*scale/2)*2),Math.max(2,Math.floor(frame.displayHeight*scale/2)*2)];
}
function configure(codec,mime){post({type:'configure',codec,width,height,mime:mime||''});configured=true;}
function emit(kind,data,rect,key=false,count=1){post({type:'packet',kind,data,width,height,rect:rect||[0,0,width,height],key,count});}
function failToJPEG(reason){
 if(encoder){try{encoder.close();}catch(ignored){}encoder=null;}
 previous=null;fragments=[];mode='jpeg';configured=false;first=true;oldPixels=null;
 post({type:'mode',mode:'jpeg',reason:String(reason).slice(0,100)});
 post({type:'processed'});encoding=false;
}
async function setup(frame){
 [width,height]=size(frame);
 canvas=new OffscreenCanvas(width,height);ctx=canvas.getContext('2d',{willReadFrequently:mode!=='h264'});
 sample=new OffscreenCanvas(64,36);sampleCtx=sample.getContext('2d',{willReadFrequently:true});
 patch=new OffscreenCanvas(width,height);
 if(mode==='h264'&&typeof VideoEncoder==='function'){
  const cfg={codec:options.fps<=30?'avc1.42E01F':'avc1.42E029',width,height,framerate:Math.min(options.fps,60),
   bitrate:Math.round(Math.max(600000,Math.min(6000000,width*height*Math.min(options.fps,60)*.12))),
   latencyMode:'realtime',hardwareAcceleration:'prefer-hardware',avc:{format:'avc'}};
  let support=await VideoEncoder.isConfigSupported(cfg);
  if(!support.supported){delete cfg.hardwareAcceleration;support=await VideoEncoder.isConfigSupported(cfg);}
  if(!support.supported){failToJPEG('H.264 non disponibile sul PC');return;}
  encoder=new VideoEncoder({
   output(chunk,metadata){
    if(stopped||mode!=='h264')return;
    try{
     if(metadata.decoderConfig&&metadata.decoderConfig.description){
      const desc=new Uint8Array(metadata.decoderConfig.description);
      const changed=avcDescription&&(!desc.every((v,i)=>v===avcDescription[i])||desc.length!==avcDescription.length);
      if(changed){failToJPEG('Configurazione AVC variata');return;}
      if(!avcDescription){
       avcDescription=desc.slice();
       const codec=metadata.decoderConfig.codec||cfg.codec;
       configure('h264','video/mp4; codecs="'+codec+'"');
       emit(1,ScreenMP4.init(width,height,avcDescription));
      }
     }
     const data=new Uint8Array(chunk.byteLength);chunk.copyTo(data);
     if(baseTimestamp===null)baseTimestamp=chunk.timestamp;
     const current={data,timestamp:chunk.timestamp-baseTimestamp,key:chunk.type==='key'};
     if(previous){
      fragments.push(ScreenMP4.fragment(++sequence,previous.timestamp,current.timestamp-previous.timestamp,previous.data,previous.key));
      fragmentKeys=fragmentKeys||previous.key;
      const group=Math.max(1,Math.min(3,Math.round(Math.min(options.fps,60)/10)));
      if(fragments.length>=group){
       const combined=new Uint8Array(fragments.reduce((n,p)=>n+p.length,0));let offset=0;
       for(const part of fragments){combined.set(part,offset);offset+=part.length;}
       emit(2,combined,null,fragmentKeys,fragments.length);fragments=[];fragmentKeys=false;
      }
     }
     previous=current;
     post({type:'processed'});encoding=false;
    }catch(error){failToJPEG(error.message);}
   },error(error){if(!stopped)failToJPEG(error.message);}
  });
  encoder.configure(support.config);
  post({type:'mode',mode:'h264',bitrate:cfg.bitrate});
 }else mode='jpeg';
}
function dirtyTiles(pixels,force){
 // Exact pixel comparisons avoid missing small text/caret changes in a
 // downscaled damage map. One retained RGBA frame, only on the image path.
 const current=new Uint32Array(pixels.buffer,pixels.byteOffset,pixels.byteLength/4);
 if(force||!oldPixels){oldPixels=current;return null;}
 const columns=Math.ceil(width/64),rows=Math.ceil(height/64),changed=[];
 for(let ty=0;ty<height;ty+=64)for(let tx=0;tx<width;tx+=64){
  let dirty=false;const right=Math.min(width,tx+64),bottom=Math.min(height,ty+64);
  for(let y=ty;y<bottom&&!dirty;y++)for(let x=tx;x<right;x++){
   const i=y*width+x;if(current[i]!==oldPixels[i]){dirty=true;break;}
  }
  if(dirty)changed.push([tx,ty]);
 }
 oldPixels=current;
 if(changed.length>columns*rows*.45)return null;
 return changed;
}
async function jpeg(frame){
 const now=performance.now();
 ctx.drawImage(frame,0,0,width,height);
 sampleCtx.drawImage(canvas,0,0,64,36);
 const key=first||now-jpegKeyTime>2000;
 const pixels=sampleCtx.getImageData(0,0,64,36).data;
 const tiles=dirtyTiles(ctx.getImageData(0,0,width,height).data,key);
 if(tiles&&tiles.length===0){post({type:'processed'});return;}
 if(!configured)configure('jpeg');
 const columns=tiles?Math.min(8,tiles.length):0;
 const rect=tiles?[columns,64,columns*64,Math.ceil(tiles.length/columns)*64]:[0,0,width,height];
 const [x,y,w,h]=rect;
 if(patch.width!==w)patch.width=w;if(patch.height!==h)patch.height=h;
 const patchCtx=patch.getContext('2d');
 if(tiles){patchCtx.clearRect(0,0,w,h);tiles.forEach(([tx,ty],i)=>{const tw=Math.min(64,width-tx),th=Math.min(64,height-ty);patchCtx.drawImage(canvas,tx,ty,tw,th,(i%columns)*64,Math.floor(i/columns)*64,tw,th);});}
 else patchCtx.drawImage(canvas,0,0,width,height);
 let blob=await patch.convertToBlob({type:'image/jpeg',quality}),png=false;
 const colors=new Set();for(let i=0;i<pixels.length;i+=4)colors.add(((pixels[i]>>3)<<10)|((pixels[i+1]>>3)<<5)|(pixels[i+2]>>3));
 if(tiles||colors.size<320){
  const lossless=await patch.convertToBlob({type:'image/png'});
  if(lossless.size<blob.size*.95){blob=lossless;png=true;}
 }
 let data=new Uint8Array(await blob.arrayBuffer());
 if(tiles){const packed=new Uint8Array(2+tiles.length*4+data.length),view=new DataView(packed.buffer);view.setUint16(0,tiles.length);
  tiles.forEach(([tx,ty],i)=>{view.setUint16(2+i*4,tx);view.setUint16(4+i*4,ty);});packed.set(data,2+tiles.length*4);data=packed;}
 if(data.length>512*1024-32){quality=Math.max(.4,quality-.1);first=true;post({type:'processed'});return;}
 emit(tiles?(png?6:5):(png?7:4),data,rect,key||first);first=false;if(key)jpegKeyTime=now;
 post({type:'processed'});
}
self.onmessage=async event=>{
 const data=event.data;
 if(data.type==='start'){options=data;mode=data.codec;return;}
 if(data.type==='feedback'){
  if(data.rtt>180)quality=Math.max(.4,quality-.025);
  else if(data.rtt<70)quality=Math.min(.82,quality+.005);
  return;
 }
 if(data.type!=='frame')return;
 const frame=data.frame;
 if(busy||stopped){frame.close();post({type:'processed'});return;}
 const rate=Math.min(options.fps,mode==='h264'?60:30);
 if(frame.timestamp<nextInput-1000){frame.close();post({type:'processed'});return;}
 nextInput=Math.max(nextInput+1000000/rate,frame.timestamp+100000/rate);busy=true;
 try{
  if(!canvas)await setup(frame);
  if(stopped)return;
  if(mode==='h264'&&encoder){
   if(encoder.encodeQueueSize>1){post({type:'processed'});return;}
   const key=frame.timestamp-lastKey>1000000;
   ctx.drawImage(frame,0,0,width,height);
   const scaled=new VideoFrame(canvas,{timestamp:frame.timestamp});
   // GPU-scaled surface; no getImageData/RGBA readback on the AVC path.
   encoding=true;encoder.encode(scaled,{keyFrame:key});scaled.close();
   if(key)lastKey=frame.timestamp;
  }else await jpeg(frame);
 }catch(error){failToJPEG(error.message);}
 finally{frame.close();busy=false;}
};
