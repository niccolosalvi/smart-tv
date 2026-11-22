"""Compare real browser image encoding against deterministic reference frames.

Runs locally with Edge/Playwright; never contacts or controls a TV. The receiver
canvas reconstructs full frames and tile atlases before measuring RGB PSNR and
8x8-window luma SSIM. These are decoded-image metrics, not panel measurements.
Only the JPEG/PNG path is exercised, without network/TV acknowledgments or audio.
Encoding timings include worker startup on the first frame and do not measure
end-to-end frame rate; higher fidelity may require more bandwidth and CPU time.
Example: python -m tools.benchmark_video_quality --output reports/video-quality.json
Optional --baseline PATH compares a saved older share-worker.js as well.
"""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = r"""async ({width,height,frames,baseline,baselineProfile,workers}) => {
 const source=document.createElement('canvas');source.width=width;source.height=height;
 const ctx=source.getContext('2d',{willReadFrequently:true});
 const reference=[];
 for(let n=0;n<frames;n++){
  const pixels=ctx.createImageData(width,height),p=pixels.data;
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
   const i=(y*width+x)*4,scene=n%3;
   const texture=((Math.imul(x+n*9,374761393)^Math.imul(y,668265263))>>>24)/255;
   const light=scene===1?.12:1;
   const wave=18*Math.sin((x+n*15)/37)+12*Math.cos(y/29);
   p[i]=Math.max(0,Math.min(255,(45+140*x/width+wave+texture*28)*light));
   p[i+1]=Math.max(0,Math.min(255,(30+150*y/height+wave+texture*28)*light));
   p[i+2]=Math.max(0,Math.min(255,(80+90*(x+y)/(width+height)+texture*(scene===2?90:20))*light));
   p[i+3]=255;
  }
  ctx.putImageData(pixels,0,0);
  ctx.fillStyle='#e4c39b';ctx.beginPath();ctx.ellipse(width*(.25+(n%5)*.07),height*.4,width*.07,height*.15,0,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#10151b';ctx.fillRect(0,height*.87,width,height*.13);
  ctx.fillStyle='white';ctx.textAlign='center';ctx.font=Math.round(height*.038)+'px sans-serif';
  ctx.fillText('Dettagli, movimento e sottotitoli — '+n,width/2,height*.95);
  reference.push(ctx.getImageData(0,0,width,height));
 }
 function metrics(a,b){
  let error=0,ssim=0,windows=0;
  const luma=(p,i)=>(.2126*p[i]+.7152*p[i+1]+.0722*p[i+2]);
  for(let y=0;y<height;y+=8)for(let x=0;x<width;x+=8){
   let sa=0,sb=0,saa=0,sbb=0,sab=0,count=0;
   for(let yy=y;yy<Math.min(y+8,height);yy++)for(let xx=x;xx<Math.min(x+8,width);xx++){
    const i=(yy*width+xx)*4,va=luma(a,i),vb=luma(b,i);
    sa+=va;sb+=vb;saa+=va*va;sbb+=vb*vb;sab+=va*vb;count++;
    for(let c=0;c<3;c++)error+=(a[i+c]-b[i+c])**2;
   }
   const ma=sa/count,mb=sb/count,va=Math.max(0,saa/count-ma*ma),vb=Math.max(0,sbb/count-mb*mb),cov=sab/count-ma*mb;
   ssim+=((2*ma*mb+6.5025)*(2*cov+58.5225))/((ma*ma+mb*mb+6.5025)*(va+vb+58.5225));windows++;
  }
  const mse=error/(width*height*3);
  return {mse,psnr_db:mse?10*Math.log10(65025/mse):null,ssim:ssim/windows};
 }
 async function run(profile,path){
  const workerURL=URL.createObjectURL(new Blob([workers[path]],{type:'application/javascript'}));
  const worker=new Worker(workerURL),receiver=document.createElement('canvas');receiver.width=width;receiver.height=height;
  const out=receiver.getContext('2d',{willReadFrequently:true});let waiting=null,packets=[],configured=null;
  const evaluated=document.createElement('canvas');evaluated.width=width;evaluated.height=height;
  const evaluation=evaluated.getContext('2d',{willReadFrequently:true});
  worker.onerror=e=>{if(waiting)waiting.reject(Error(e.message));};
  worker.onmessage=e=>{
   const d=e.data;
   if(d.type==='configure'){
    configured={width:d.width,height:d.height};receiver.width=d.width;receiver.height=d.height;
   }
   if(d.type==='packet')packets.push(d);
   if(d.type==='processed'&&waiting){clearTimeout(waiting.timer);waiting.resolve();waiting=null;}
  };
  worker.postMessage({type:'start',codec:'jpeg',profile,width,fps:30});
  const samples=[];
  try{
   for(let n=0;n<reference.length;n++){
    ctx.putImageData(reference[n],0,0);packets=[];
    const start=performance.now();
    await new Promise((resolve,reject)=>{
     waiting={resolve,reject,timer:setTimeout(()=>reject(Error('Encoder timeout')),15000)};
     const frame=new VideoFrame(source,{timestamp:n*1000000/30});worker.postMessage({type:'frame',frame},[frame]);
    });
    const encodeMs=performance.now()-start;let bytes=0;
    for(const packet of packets){
     bytes+=packet.data.byteLength;
     let payload=packet.data,tiles=null;
     if(packet.kind===5||packet.kind===6){
      const v=new DataView(payload.buffer,payload.byteOffset,payload.byteLength),count=v.getUint16(0);tiles=[];
      for(let i=0;i<count;i++)tiles.push([v.getUint16(2+i*4),v.getUint16(4+i*4)]);
      payload=payload.slice(2+count*4);
     }
     const png=packet.kind===6||packet.kind===7;
     const image=await createImageBitmap(new Blob([payload],{type:png?'image/png':'image/jpeg'}));
     if(tiles){const columns=packet.rect[0];tiles.forEach(([x,y],i)=>{
      const w=Math.min(64,receiver.width-x),h=Math.min(64,receiver.height-y);out.drawImage(image,(i%columns)*64,Math.floor(i/columns)*64,w,h,x,y,w,h);
     });}else out.drawImage(image,...packet.rect);
     image.close();
    }
    // Compare at source resolution, including upscaling losses when an older
    // encoder is limited to 720p. Preserve the true receiver canvas dimensions.
    evaluation.drawImage(receiver,0,0,width,height);
    const quality=metrics(reference[n].data,evaluation.getImageData(0,0,width,height).data);
    samples.push({...quality,bytes,encode_ms:encodeMs,packets:packets.length});
   }
  }finally{worker.terminate();URL.revokeObjectURL(workerURL);}
  const mean=key=>samples.reduce((sum,s)=>sum+s[key],0)/samples.length;
  const steady=samples.slice(1).map(s=>s.encode_ms).sort((a,b)=>a-b);
  return {profile,resolution:configured,frames:samples.length,psnr_db:mean('mse')?10*Math.log10(65025/mean('mse')):null,mean_ssim:mean('ssim'),
   worst_ssim:Math.min(...samples.map(s=>s.ssim)),mean_encode_ms:mean('encode_ms'),total_payload_bytes:samples.reduce((sum,s)=>sum+s.bytes,0),
   steady_mean_encode_ms:steady.reduce((sum,value)=>sum+value,0)/steady.length,steady_p95_encode_ms:steady[Math.ceil(steady.length*.95)-1],
   frames_without_update:samples.filter(s=>!s.packets).length,samples};
 }
 const runs=[];
 if(baseline)runs.push({...await run(baselineProfile,'/assets/baseline-worker.js'),version:'baseline'});
 runs.push(await run('balanced','/assets/share-worker.js'));
 runs.push(await run('cinema','/assets/share-worker.js'));
 return {scope:'Local Edge JPEG/PNG decoder; synthetic scenes, no network, ACK feedback, audio or TV measurements',width,height,ssim_definition:'Luma SSIM, uniform non-overlapping 8x8 windows, population moments, K1=.01 K2=.03',runs};
}"""


def benchmark(width=1280, height=720, frames=12, baseline=None, baseline_profile='previous'):
    import playwright
    # Run Playwright's Node driver over pipes. All image data stays in memory;
    # file-origin + Blob workers require neither a server nor network sockets.
    mp4 = (ROOT/'web/mp4.js').read_text(encoding='utf-8')
    def worker_source(path):
        return mp4+'\n'+Path(path).read_text(encoding='utf-8').replace("importScripts('/assets/mp4.js');", '')
    workers = {'/assets/share-worker.js': worker_source(ROOT/'web/share-worker.js')}
    if baseline:
        workers['/assets/baseline-worker.js'] = worker_source(baseline)
    driver = Path(playwright.__file__).parent/'driver/package'
    runner = r"""
const fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const {chromium}=require(input.driver);
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage();await page.goto(input.url);
  // evaluate(string, arg) evaluates an expression without invoking an arrow
  // function. Rehydrate our local function so Playwright supplies its argument.
  const benchmark= new Function('return ('+input.script+')')();
  const result=await page.evaluate(benchmark,input.options);
  process.stdout.write(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    with tempfile.TemporaryDirectory(prefix='smart-tv-quality-') as temporary:
        html=Path(temporary)/'benchmark.html'
        html.write_text('<!doctype html><title>Local video quality benchmark</title>',encoding='utf-8')
        payload={'driver':str(driver),'url':html.as_uri(),'script':BENCHMARK,
                 'options':dict(width=width,height=height,frames=frames,baseline=bool(baseline),baselineProfile=baseline_profile,workers=workers)}
        result=subprocess.run([shutil.which('node') or 'node','-e',runner],input=json.dumps(payload),
                              capture_output=True,text=True,encoding='utf-8',timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--width', type=int, choices=(640, 960, 1280, 1920), default=1280)
    parser.add_argument('--frames', type=int, default=12)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--baseline-profile', choices=('previous','balanced','cinema'), default='previous',
                        help='Use the same profile for before/after comparisons of recent workers')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 3 <= args.frames <= 120:
        parser.error('--frames must be between 3 and 120')
    result = benchmark(args.width, args.width*9//16, args.frames, args.baseline, args.baseline_profile)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({**result, 'runs': [{k:v for k,v in r.items() if k != 'samples'} for r in result['runs']]}, indent=2))


if __name__ == '__main__':
    main()
