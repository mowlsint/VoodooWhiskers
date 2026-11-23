/* Delayed GFW context loader: consume every bounded shard or mark fallback partial. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.GfwEventShards=api;
})(globalThis,function(){
  'use strict';
  function checkManifest(manifest){
    if(!manifest||manifest.kind!=='gfw_events_14d'||manifest.historical!==true||
       !Array.isArray(manifest.parts)||!Number.isSafeInteger(manifest.event_count)||
       manifest.event_count<0||manifest.part_count!==manifest.parts.length||
       manifest.part_count>32)throw new Error('Invalid GFW event shard manifest');
    for(const part of manifest.parts){
      if(!part||typeof part.filename!=='string'||!/^part-[0-9]{4}\.json$/.test(part.filename)||
         !Number.isSafeInteger(part.event_count)||part.event_count<0||
         !Number.isSafeInteger(part.bytes)||part.bytes>2097152||part.bytes<1)
        throw new Error('Invalid GFW event shard metadata');
    }
    if(manifest.parts.reduce((n,p)=>n+p.event_count,0)!==manifest.event_count)
      throw new Error('GFW manifest event total does not match parts');
  }
  async function load(fetchJson, manifestPath, legacyPath){
    try{
      const manifest=await fetchJson(manifestPath);
      checkManifest(manifest);
      const base=manifestPath.slice(0,manifestPath.lastIndexOf('/')+1);
      const chunks=await Promise.all(manifest.parts.map(async part=>{
        const payload=await fetchJson(base+part.filename);
        if(payload?.historical!==true||!Array.isArray(payload.events)||
           payload.events.length!==part.event_count||payload.count!==part.event_count)
          throw new Error('GFW event shard mismatch');
        return payload.events;
      }));
      const events=chunks.flat();
      if(events.length!==manifest.event_count)throw new Error('GFW total event mismatch');
      return {...manifest,events};
    }catch(error){
      // Never claim complete coverage after a missing, inconsistent or stale shard.
      if(typeof console!=='undefined')console.warn('GFW full event shards unavailable; using partial preview',error);
      try{
        const legacy=await fetchJson(legacyPath);
        if(legacy?.historical===true&&Array.isArray(legacy.events))
          return {...legacy,coverage_complete:false,shard_load_error:true};
      }catch(_ignore){}
      return null;
    }
  }
  return {load,checkManifest};
});
