/* Minimal fragmented ISO BMFF muxer for one low-delay AVC video track.
 * No B frames, AVCC samples and monotonically increasing microsecond timestamps.
 * Each media fragment is independently appendable after the initialization data.
 */
(function(scope){
 'use strict';
 const bytes=(...values)=>new Uint8Array(values);
 const text=s=>new Uint8Array([...s].map(c=>c.charCodeAt(0)));
 function join(...parts){const out=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));let offset=0;for(const p of parts){out.set(p,offset);offset+=p.length;}return out;}
 function u16(n){return bytes(n>>>8,n&255);}
 function u32(n){return bytes(n>>>24,(n>>>16)&255,(n>>>8)&255,n&255);}
 function u64(n){return join(u32(Math.floor(n/4294967296)),u32(n>>>0));}
 function box(type,...parts){const payload=join(...parts);return join(u32(payload.length+8),text(type),payload);}
 const full=(type,version,flags,...parts)=>box(type,bytes(version,flags>>>16,(flags>>>8)&255,flags&255),...parts);
 const zeros=n=>new Uint8Array(n);
 const matrix=join(u32(0x10000),u32(0),u32(0),u32(0),u32(0x10000),u32(0),u32(0),u32(0),u32(0x40000000));
 function init(width,height,description){
  const ftyp=box('ftyp',text('iso6'),u32(1),text('iso6isomavc1mp41'));
  const mvhd=full('mvhd',0,0,u32(0),u32(0),u32(90000),u32(0),u32(0x10000),u16(0x100),zeros(10),matrix,zeros(24),u32(2));
  const tkhd=full('tkhd',0,7,u32(0),u32(0),u32(1),u32(0),u32(0),zeros(8),u16(0),u16(0),u16(0),u16(0),matrix,u32(width*65536),u32(height*65536));
  const mdhd=full('mdhd',0,0,u32(0),u32(0),u32(90000),u32(0),u16(0x55c4),u16(0));
  const hdlr=full('hdlr',0,0,u32(0),text('vide'),zeros(12),text('Screen video\0'));
  const avc1=box('avc1',zeros(6),u16(1),zeros(16),u16(width),u16(height),u32(0x480000),u32(0x480000),u32(0),u16(1),zeros(32),u16(24),u16(0xffff),box('avcC',new Uint8Array(description)));
  const stbl=box('stbl',full('stsd',0,0,u32(1),avc1),full('stts',0,0,u32(0)),full('stsc',0,0,u32(0)),full('stsz',0,0,u32(0),u32(0)),full('stco',0,0,u32(0)));
  const dinf=box('dinf',full('dref',0,0,u32(1),full('url ',0,1)));
  const minf=box('minf',full('vmhd',0,1,zeros(8)),dinf,stbl);
  const trak=box('trak',tkhd,box('mdia',mdhd,hdlr,minf));
  const mvex=box('mvex',full('trex',0,0,u32(1),u32(1),u32(0),u32(0),u32(0)));
  return join(ftyp,box('moov',mvhd,trak,mvex));
 }
 function fragment(sequence,timestamp,duration,data,key){
  const mfhd=full('mfhd',0,0,u32(sequence));
  const tfhd=full('tfhd',0,0x020000,u32(1));
  const ticks=Math.max(0,Math.round(timestamp*.09));
  const tfdt=ticks<4294967296?full('tfdt',0,0,u32(ticks)):full('tfdt',1,0,u64(ticks));
  function make(offset){return box('moof',mfhd,box('traf',tfhd,tfdt,full('trun',0,0x000701,u32(1),u32(offset),u32(Math.max(1,Math.round(duration*.09))),u32(data.length),u32(key?0x02000000:0x01010000))));}
  let moof=make(0);moof=make(moof.length+8);
  return join(moof,box('mdat',data));
 }
 scope.ScreenMP4={init,fragment};
})(typeof self!=='undefined'?self:globalThis);
