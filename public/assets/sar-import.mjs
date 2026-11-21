// Read exactly one encrypted workspace from an Actions artifact ZIP; no dependency or extraction to disk.
export async function readWorkspaceFile(file){
  const MAX=24*1024*1024;if(file.size>MAX)throw new Error('File too large');
  const bytes=new Uint8Array(await file.arrayBuffer());
  if(bytes[0]!==0x50||bytes[1]!==0x4b)return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  const d=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),u16=p=>d.getUint16(p,true),u32=p=>d.getUint32(p,true);
  let end=-1;for(let p=bytes.length-22;p>=Math.max(0,bytes.length-65557);p--)if(u32(p)===0x06054b50){end=p;break;}
  if(end<0||u16(end+4)!==0||u16(end+6)!==0||u16(end+8)!==1||u16(end+10)!==1)throw new Error('Expected a single SAR artifact');
  const c=u32(end+16);if(c+46>bytes.length||u32(c)!==0x02014b50)throw new Error('Invalid ZIP directory');
  const flags=u16(c+8),method=u16(c+10),crc=u32(c+16),packed=u32(c+20),size=u32(c+24),nameLen=u16(c+28),local=u32(c+42);
  const name=new TextDecoder('utf-8',{fatal:true}).decode(bytes.slice(c+46,c+46+nameLen));
  if(name!=='workbench.enc.json'||size>MAX||flags&1||![0,8].includes(method)||local+30>bytes.length||u32(local)!==0x04034b50)throw new Error('Unexpected ZIP contents');
  const offset=local+30+u16(local+26)+u16(local+28);if(offset+packed>c)throw new Error('Invalid ZIP data range');
  const data=bytes.slice(offset,offset+packed);let decoded=data;
  if(method===8){
    const reader=new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader(),chunks=[];let total=0;
    while(true){const {done,value}=await reader.read();if(done)break;total+=value.length;if(total>MAX){await reader.cancel();throw new Error('ZIP contents too large');}chunks.push(value);}
    decoded=new Uint8Array(total);let pos=0;for(const chunk of chunks){decoded.set(chunk,pos);pos+=chunk.length;}
  }
  if(decoded.length!==size)throw new Error('Invalid ZIP length');
  let checksum=0xffffffff;for(const b of decoded){checksum^=b;for(let n=0;n<8;n++)checksum=(checksum>>>1)^((checksum&1)?0xedb88320:0);}if(((checksum^0xffffffff)>>>0)!==crc)throw new Error('Invalid ZIP checksum');
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(decoded));
}
