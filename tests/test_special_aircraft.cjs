const {test}=require('node:test');
const assert=require('node:assert/strict');
const A=require('../public/assets/special-aircraft.js');
const now=Date.parse('2026-11-17T06:00:00Z');
const row=(hours=0,extra={})=>({icao24:'abc123',latitude:55,longitude:10,observed_at:new Date(now-hours*3600000).toISOString(),selection_status:'verified_registry',position_timestamp_valid:true,roles:['police'],...extra});
const pack=(items=[],extra={})=>({kind:'selected_aircraft_context',schema_version:'1.0.0',context_only:true,coverage_complete:false,status:'fresh',generated_at:new Date(now).toISOString(),items,...extra});
test('only verified, explicitly timed positions with a known role enter the aircraft view',()=>{
 const good=row(0,{roles:['police','rpas','unknown','police']});
 const raw=pack([good,row(0,{icao24:'~abc123'}),row(0,{selection_status:'candidate'}),row(0,{position_timestamp_valid:false}),row(0,{latitude:null}),row(0,{longitude:Infinity}),row(0,{roles:['unknown']}),row(0,{identity_conflict:true})]);
 const result=A.decode(raw,now);assert.equal(result.items.length,1);assert.equal(result.excluded,7);assert.deepEqual(result.items[0].roles,['police','rpas']);assert.equal(good.roles.length,4);
});
test('duplicate position times are deduplicated and conflicting coordinates at one timestamp are withheld',()=>{
 assert.equal(A.decode(pack([row(),row()]),now).items.length,1);
 const conflict=A.decode(pack([row(),row(0,{longitude:12}),row()]),now);assert.equal(conflict.items.length,0);assert.equal(conflict.excluded,3);
});
test('historical windows are strict, future/expired points do not appear and project mode is isolated',()=>{
 const p=A.decode(pack([row(1),row(48,{icao24:'def456'}),row(337,{icao24:'fed321'}),row(-1,{icao24:'ced321'})]),now);
 assert.equal(p.excluded,2);assert.equal(A.visible(p,{now}).length,0);assert.equal(A.visible(p,{mode:'24h',hours:24,now}).length,1);assert.equal(A.visible(p,{mode:'48h',hours:48,now}).length,2);assert.equal(A.visible(p,{mode:'project',now}).length,0);
});
test('routine offshore transport is optional civil context; offshore rescue remains visible',()=>{
 const p=A.decode(pack([row(0,{roles:['offshore_transport']}),row(0,{icao24:'def456',roles:['offshore_sar']})]),now);
 assert.equal(A.visible(p,{now}).length,1);assert.equal(A.visible(p,{now,includeOffshore:true}).length,2);assert.equal(A.colour(p.items[0]),'#a5b2bd');assert.notEqual(A.colour(p.items[1]),'#a5b2bd');
});
test('valid zero, partial, stale, missing and error products remain distinct',()=>{
 for(const state of ['valid_zero','partial','stale','unavailable','error'])assert.equal(A.decode(pack([],{status:state}),now).status,state);
 for(const state of ['unavailable','error'])assert.equal(A.visible(A.decode(pack([row()],{status:state}),now),{now}).length,0);
 assert.throws(()=>A.decode(pack([row()],{status:'valid_zero'}),now),/Zero-result/);assert.throws(()=>A.decode(null,now),/Invalid/);assert.throws(()=>A.decode(pack([],{coverage_complete:true}),now),/Invalid/);
});
test('unsupported, oversized and future-dated products are rejected',()=>{
 assert.throws(()=>A.decode(pack(Array(5001).fill(row())),now),/Invalid/);assert.throws(()=>A.decode(pack([],{generated_at:new Date(now+3600000).toISOString()}),now),/time/);assert.throws(()=>A.decode(pack([],{schema_version:'9.0.0'}),now),/Invalid/);
});
test('consumer rejects advertised oversize and invalid HTML without requesting a provider',async()=>{
 const original=global.fetch;let textRead=false;try{
  global.fetch=async url=>{assert.equal(url,'./data/aircraft/aircraft_latest.json');return {ok:true,headers:{get:()=>String(A.MAX_BYTES+1)},text:async()=>{textRead=true;return '';}};};
  await assert.rejects(()=>A.fetchProduct('./data/aircraft/aircraft_latest.json'),/budget/);assert.equal(textRead,false);
  global.fetch=async()=>({ok:true,text:async()=>'<html>fallback</html>'});await assert.rejects(()=>A.fetchProduct('./data/aircraft/aircraft_latest.json'));
 }finally{global.fetch=original;}
});
test('streaming byte cap cancels an oversized response',async()=>{
 const original=global.fetch;let cancelled=false;try{global.fetch=async()=>({ok:true,body:{getReader:()=>({read:async()=>({done:false,value:new Uint8Array(600000)}),cancel:async()=>{cancelled=true;}})}});await assert.rejects(()=>A.fetchProduct('./data/aircraft/aircraft_latest.json'),/budget/);assert.ok(cancelled);}finally{global.fetch=original;}
});
