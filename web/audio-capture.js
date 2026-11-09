/* Only the realtime capture thread touches this processor. No encoding here. */
class ScreenAudioCapture extends AudioWorkletProcessor {
 constructor(options){super();this.channels=options.processorOptions.channels;this.offset=0;this.buffers=this.allocate();}
 allocate(){return Array.from({length:this.channels},()=>new Float32Array(2048));}
 process(inputs){
  const input=inputs[0];if(!input||!input.length)return true;
  for(let i=0;i<input[0].length;i++){
   for(let c=0;c<this.channels;c++)this.buffers[c][this.offset]=this.channels===1&&input.length>1?(input[0][i]+input[1][i])*.5:input[Math.min(c,input.length-1)][i];
   if(++this.offset===2048){
    this.port.postMessage({channels:this.buffers,time:(currentFrame+i+1-2048)/sampleRate},this.buffers.map(p=>p.buffer));
    this.buffers=this.allocate();this.offset=0;
   }
  }
  return true;
 }
}
registerProcessor('screen-audio',ScreenAudioCapture);
