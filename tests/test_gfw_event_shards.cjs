// Offline mock of the exact web-reader path: all shards or an explicitly partial fallback.
const assert=require('node:assert/strict');
const {load,checkManifest}=require('../public/assets/gfw-events.js');
async function run(){
  const manifest={kind:'gfw_events_14d',historical:true,event_count:3,part_count:2,coverage_complete:true,
    parts:[{filename:'part-0001.json',event_count:2,bytes:300},
           {filename:'part-0002.json',event_count:1,bytes:200}]};
  const reads=[];
  const responses={
    './data/vessels/gfw_events_14d/manifest.json':manifest,
    './data/vessels/gfw_events_14d/part-0001.json':{historical:true,count:2,events:[{id:'a'},{id:'b'}]},
    './data/vessels/gfw_events_14d/part-0002.json':{historical:true,count:1,events:[{id:'c'}]},
    './data/vessels/gfw_events_latest.json':{historical:true,coverage_complete:true,events:[{id:'a'}]}
  };
  const fetchJson=async key=>{reads.push(key);if(!(key in responses))throw Error('not found');return responses[key]};
  const result=await load(fetchJson,'./data/vessels/gfw_events_14d/manifest.json',
                          './data/vessels/gfw_events_latest.json');
  assert.deepEqual(result.events.map(e=>e.id),['a','b','c']);
  assert.equal(result.coverage_complete,true);
  assert.equal(reads.includes('./data/vessels/gfw_events_latest.json'),false);
  // A broken shard can never quietly turn a partial preview into complete coverage.
  delete responses['./data/vessels/gfw_events_14d/part-0002.json'];
  const priorWarn=console.warn;console.warn=()=>{};
  try{
    const fallback=await load(fetchJson,'./data/vessels/gfw_events_14d/manifest.json',
                              './data/vessels/gfw_events_latest.json');
    assert.equal(fallback.coverage_complete,false);
    assert.equal(fallback.shard_load_error,true);
    assert.equal(fallback.events.length,1);
    responses['./data/vessels/gfw_events_latest.json']=null;
    assert.equal(await load(fetchJson,'./data/vessels/gfw_events_14d/manifest.json',
                            './data/vessels/gfw_events_latest.json'),null);
  }finally{console.warn=priorWarn;}
  assert.throws(()=>checkManifest({...manifest,parts:[{filename:'../bad.json',bytes:100,event_count:3}],part_count:1}));
  assert.throws(()=>checkManifest({...manifest,event_count:4}));
}
run().then(()=>console.log('GFW shard reader tests passed')).catch(error=>{console.error(error);process.exitCode=1;});
