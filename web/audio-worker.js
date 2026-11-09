/* Native AAC-LC, independent ADTS groups with one preroll frame. The small
 * IMA fallback uses independently decodable 40 ms blocks, never raw PCM. */
'use strict';
let encoder=null,channels=2,mode='aac',parts=[],previous=null,fallback=false;
let imaPredictor=null,imaIndex=null;
let configuredBitrate=0;
const steps=[7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767];
const indices=[-1,-1,-1,-1,2,4,6,8];
function configure(codec){self.postMessage({type:'configure',codec,sampleRate:codec==='aac'?48000:24000,channels,bitrate:codec==='aac'?configuredBitrate:24000*channels*4});}
function useIMA(){
 if(encoder){try{encoder.close();}catch(_){}encoder=null;}
 mode='ima';parts=[];previous=null;imaPredictor=imaIndex=null;configure('ima');
}
function packet(kind,data,pts,samples,preroll){self.postMessage({type:'packet',kind,data,pts,samples,preroll},[data.buffer]);}
async function start(data){
 channels=data.channels;
 if(data.codec==='ima'||typeof AudioEncoder!=='function'){useIMA();return;}
 const config={codec:'mp4a.40.2',sampleRate:48000,numberOfChannels:channels,bitrate:data.bitrate,aac:{format:'adts'}};
 try{
  let support=await AudioEncoder.isConfigSupported(config);
  // Windows AAC supports 96 kb/s mono but may reject 64 kb/s. Prefer this
  // native, full-bandwidth format before using the lower-bandwidth IMA fallback.
  if(!support.supported&&config.bitrate<96000){config.bitrate=96000;support=await AudioEncoder.isConfigSupported(config);}
  if(!support.supported){useIMA();return;}
  configuredBitrate=config.bitrate;
  encoder=new AudioEncoder({output(chunk){
   if(mode!=='aac')return;
   const bytes=new Uint8Array(chunk.byteLength);chunk.copyTo(bytes);parts.push({data:bytes,pts:chunk.timestamp});
   if(parts.length===4){
    const group=previous?[previous,...parts]:parts;
    const content=new Uint8Array(group.reduce((n,p)=>n+p.data.length,0));let offset=0;
    for(const p of group){content.set(p.data,offset);offset+=p.data.length;}
    packet(1,content,parts[0].pts,4096,previous?1024:0);
    previous=parts[parts.length-1];parts=[];
   }
  },error(){useIMA();}});
  encoder.configure(support.config);configure('aac');
 }catch(_){useIMA();}
}
function ima(data){
 const frames=data.channels[0].length/2;
 const out=new Uint8Array(channels*4+Math.ceil(frames*channels/2)),v=new DataView(out.buffer);
 if(!imaPredictor){
  imaPredictor=new Int32Array(channels);imaIndex=new Int32Array(channels);
  for(let c=0;c<channels;c++)imaPredictor[c]=Math.round(Math.max(-1,Math.min(1,data.channels[c][0]))*32767);
 }
 const predictor=imaPredictor,index=imaIndex;
 for(let c=0;c<channels;c++){
  v.setInt16(c*4,predictor[c],true);v.setUint8(c*4+2,index[c]);
 }
 let nibble=0;
 for(let i=0;i<frames;i++)for(let c=0;c<channels;c++){
  // Pair averaging provides a cheap low-pass before the 48 -> 24 kHz reduction.
  const pcm=Math.round(Math.max(-1,Math.min(1,(data.channels[c][i*2]+data.channels[c][i*2+1])*.5))*32767);
  const step=steps[index[c]];let delta=pcm-predictor[c],code=delta<0?8:0;
  delta=Math.abs(delta);let change=step>>3;
  if(delta>=step){code|=4;delta-=step;change+=step;}
  if(delta>=(step>>1)){code|=2;delta-=step>>1;change+=step>>1;}
  if(delta>=(step>>2)){code|=1;change+=step>>2;}
  predictor[c]=Math.max(-32768,Math.min(32767,predictor[c]+(code&8?-change:change)));
  index[c]=Math.max(0,Math.min(88,index[c]+indices[code&7]));
  out[channels*4+(nibble>>1)]|=code<<((nibble&1)*4);nibble++;
 }
 packet(2,out,data.pts,frames,0);
}
self.onmessage=async event=>{
 const data=event.data;
 if(data.type==='start'){await start(data);self.postMessage({type:'ready'});return;}
 if(data.type==='fallback'){if(!fallback){fallback=true;useIMA();}return;}
 if(data.type!=='samples')return;
 try{
  if(mode==='ima')ima(data);
  else if(encoder&&encoder.state==='configured'&&encoder.encodeQueueSize<4){
   const frames=data.channels[0].length,pcm=new Float32Array(frames*channels);
   for(let c=0;c<channels;c++)pcm.set(data.channels[c],c*frames);
   const audio=new AudioData({format:'f32-planar',sampleRate:48000,numberOfFrames:frames,numberOfChannels:channels,timestamp:Math.round(data.pts),data:pcm});
   encoder.encode(audio);audio.close();
  }
 }catch(_){useIMA();}
 finally{self.postMessage({type:'processed'});}
};
