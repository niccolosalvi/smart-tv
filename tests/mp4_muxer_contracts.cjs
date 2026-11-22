// Validate ScreenMP4 init, single fragment and multi-sample fragmentBatch box structure.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../web/mp4.js'),'utf8');
const context=vm.createContext({Uint8Array,DataView,Math,String,Array,globalThis:{}});
context.globalThis=context;
vm.runInContext(source,context);
const {ScreenMP4}=context;

function parseBoxes(bytes,start=0,end=bytes.length){
 const boxes=[],view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 let off=start;
 while(off<end){
  assert.ok(off+8<=end,'Truncated box header');
  const size=view.getUint32(off);
  assert.ok(size>=8&&off+size<=end,'Invalid box size '+size);
  const type=String.fromCharCode(bytes[off+4],bytes[off+5],bytes[off+6],bytes[off+7]);
  boxes.push({type,offset:off,size,start:off+8,end:off+size});
  off+=size;
 }
 return boxes;
}

// 1. init box structure
const avcC=new Uint8Array([1,0x42,0xe0,0x1f,0xff,0xe1,0,4,0x67,0x42,0xe0,0x1f,1,0,2,0x68,0xce]);
const init=ScreenMP4.init(1280,720,avcC);
const topInit=parseBoxes(init);
assert.deepEqual(topInit.map(b=>b.type),['ftyp','moov']);
const moovChildren=parseBoxes(init,topInit[1].start,topInit[1].end);
assert.deepEqual(moovChildren.map(b=>b.type),['mvhd','trak','mvex']);

// 2. Single fragment compatibility + 64-bit tfdt (> 2^32 ticks)
const s1=new Uint8Array([0,0,0,4,0x65,1,2,3]);
const frag1=ScreenMP4.fragment(7,50000000000,16667,s1,true);
const top1=parseBoxes(frag1);
assert.deepEqual(top1.map(b=>b.type),['moof','mdat']);
const moof1=top1[0],mdat1=top1[1];
const traf1=parseBoxes(frag1,moof1.start,moof1.end).find(b=>b.type==='traf');
const trafKids1=parseBoxes(frag1,traf1.start,traf1.end);
const tfdt1=trafKids1.find(b=>b.type==='tfdt');
assert.equal(frag1[tfdt1.start],1,'Timestamp > 2^32 ticks must use tfdt version 1');
const trun1=trafKids1.find(b=>b.type==='trun');
const v1=new DataView(frag1.buffer,frag1.byteOffset+trun1.start,trun1.size-8);
assert.equal(v1.getUint32(4),1);
assert.equal(v1.getUint32(8),moof1.size+8,'data_offset must point to mdat payload');
assert.deepEqual(Array.from(frag1.slice(mdat1.start,mdat1.end)),Array.from(s1));

// 3. Batch fragment with 3 samples
const samples=[
 {timestamp:100000,duration:16667,data:new Uint8Array([0,0,0,3,0x65,10,11]),key:true},
 {timestamp:116667,duration:16667,data:new Uint8Array([0,0,0,4,0x41,20,21,22]),key:false},
 {timestamp:133334,duration:16666,data:new Uint8Array([0,0,0,2,0x41,30]),key:false}
];
const batch=ScreenMP4.fragmentBatch(12,samples);
const topB=parseBoxes(batch);
assert.deepEqual(topB.map(b=>b.type),['moof','mdat']);
const moofB=topB[0],mdatB=topB[1];
const trafB=parseBoxes(batch,moofB.start,moofB.end).find(b=>b.type==='traf');
const trunB=parseBoxes(batch,trafB.start,trafB.end).find(b=>b.type==='trun');
const vb=new DataView(batch.buffer,batch.byteOffset+trunB.start,trunB.size-8);
assert.equal(vb.getUint32(0)&0xffffff,0x000701);
assert.equal(vb.getUint32(4),3,'trun sample_count must equal 3');
assert.equal(vb.getUint32(8),moofB.size+8,'batch data_offset must equal moof.size + 8');
for(let i=0;i<samples.length;i++){
 const off=12+i*12;
 assert.equal(vb.getUint32(off),Math.round(samples[i].duration*0.09));
 assert.equal(vb.getUint32(off+4),samples[i].data.length);
 assert.equal(vb.getUint32(off+8),samples[i].key?0x02000000:0x01010000);
}
const expectedPayload=Buffer.concat(samples.map(s=>Buffer.from(s.data)));
assert.deepEqual(Buffer.from(batch.slice(mdatB.start,mdatB.end)),expectedPayload);

// 4. Batching produces strictly smaller container overhead than individual fragments.
const separateSize=samples.reduce((sum,s,i)=>sum+ScreenMP4.fragment(12+i,s.timestamp,s.duration,s.data,s.key).byteLength,0);
assert.ok(batch.byteLength<separateSize,`Expected batch (${batch.byteLength}) < separate (${separateSize})`);

console.log('MP4 muxer: init, single fragment, 64-bit tfdt, fragmentBatch and size savings passed.');
