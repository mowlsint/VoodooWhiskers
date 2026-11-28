const {test}=require('node:test');
const assert=require('node:assert/strict');
const S=require('../public/assets/satellite-images.js');
const PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=';
const job=extra=>({state:'ready',request:{bbox:[11.9,54.5,12,54.6],area_name:'Local test area'},scene:{scene_id:'S1_TEST_LOCAL_ONLY',datetime:'2026-11-10T12:00:00Z'},images:{preview_is_derived:true,quicklook:PNG},...extra});
test('only a completed bounded PNG preview with valid image/time/georeference is accepted',()=>{
 const r=S.normalizeImage(job());assert.deepEqual(r.bbox,[11.9,54.5,12,54.6]);assert.equal(r.width,1);assert.equal(r.height,1);
 for(const extra of [{state:'catalog_ready'},{is_fixture:true},{images:{preview_is_derived:true,quicklook:'https://example.invalid/image.png'}},{scene:{scene_id:'test',datetime:'invalid'}},{scene:{scene_id:'test',datetime:new Date(Date.now()+3600000).toISOString()}},{request:{bbox:[0,0,30,30]}}])assert.throws(()=>S.normalizeImage(job(extra)));
 assert.throws(()=>S.normalizeImage(job({candidates:{metadata:{aoi_bbox_wgs84:[12,54,13,55]}}})),/georeference/);
 assert.throws(()=>S.pngInfo('data:image/svg+xml;base64,PHN2Zz4='));assert.throws(()=>S.pngInfo('data:image/png;base64,PHN2Zz4='));
});
test('handoff allowlist strips credentials, notes, candidate data and arbitrary extra fields',()=>{
 const r=S.normalizeImage(job({password:'test-marker-do-not-transfer',githubToken:'test-marker-do-not-transfer',analyst_review:{privateNote:'test-marker-do-not-transfer'},context:{features:[{private:'test-marker-do-not-transfer'}]}}));
 assert.ok(!JSON.stringify(r).includes('test-marker-do-not-transfer'));assert.equal(r.image,PNG);
 const normalized=S.fromTransfer({...r,password:'test-marker-do-not-transfer'});assert.deepEqual(normalized,r);assert.ok(!JSON.stringify(normalized).includes('test-marker'));
});
test('download footprint describes the processed area and original acquisition, not a full scene or raw raster',()=>{
 const r=S.normalizeImage(job()),f=S.footprint(r);assert.deepEqual(f.features[0].geometry.coordinates[0],[[11.9,54.5],[12,54.5],[12,54.6],[11.9,54.6],[11.9,54.5]]);
 assert.equal(f.features[0].properties.raw_geotiff_available,false);assert.equal(f.features[0].properties.acquired_at,'2026-11-10T12:00:00.000Z');assert.match(S.filename({...r,scene_id:'<img onerror=/>'},'png'),/^[A-Za-z0-9_-]+\.png$/);
});
test('encrypted local workspace opens using existing crypto/ZIP reader with no provider or upload call',async()=>{
 const {encryptObject}=await import('../public/assets/sar-crypto.mjs');
 const envelope=await encryptObject({v:1,jobs:[job()]},'test-only-local-password','workspace'),bytes=new TextEncoder().encode(JSON.stringify(envelope));
 const file={size:bytes.length,arrayBuffer:async()=>bytes.buffer};
 const results=await S.openWorkspace(file,'test-only-local-password');assert.equal(results.length,1);assert.equal(results[0].image,PNG);
 await assert.rejects(()=>S.openWorkspace(file,'wrong-test-only-password'));
 await assert.rejects(()=>S.openWorkspace({size:25*1024*1024},'test-only-local-password'),/24 MiB/);
 await assert.rejects(()=>S.openWorkspace(file,'unused',{readWorkspaceFile:async()=>({}),decryptObject:async()=>({v:1,jobs:[]})}),/no completed/);
});
test('tab transfer is user-started, session-scoped and acknowledged without a shared opener',async()=>{
 const channels=[],sent=[],notices=[];const session=crypto.randomUUID();
 class Channel{constructor(name){this.name=name;channels.push(this);}postMessage(message){sent.push(message);for(const c of channels)if(c!==this&&!c.closed&&c.name===this.name)queueMicrotask(()=>c.onmessage?.({data:message}));}close(){this.closed=true;}}
 const receiver=new Channel('voodoo-satellite-'+session);let received;
 receiver.onmessage=({data:m})=>{if(m.type==='offer')receiver.postMessage({type:'ready',id:m.id});if(m.type==='image'){received=m.image;receiver.postMessage({type:'received',id:m.id});}};
 const sender=S.createSender({Channel,session,onStatus:s=>notices.push(s),openTab:()=>assert.fail('linked map should stay open')});assert.equal(channels.length,1);
 sender.send(job({password:'test-marker-do-not-transfer'}));await new Promise(resolve=>setImmediate(resolve));assert.ok(received);assert.ok(!JSON.stringify(sent).includes('test-marker-do-not-transfer'));assert.match(notices.at(-1),/Image added/);assert.ok(channels[1].closed);sender.dispose();
});
test('a cancelled handoff cannot send a late decrypted image',async()=>{
 const sent=[];let c;class Channel{constructor(){c=this;}postMessage(m){sent.push(m);}close(){}}
 const sender=S.createSender({Channel,session:crypto.randomUUID(),onStatus:()=>{}});sender.send(job());sender.cancel();c.onmessage({data:{type:'ready'}});assert.deepEqual(sent.map(m=>m.type),['offer']);sender.dispose();
 assert.throws(()=>S.createSender({Channel:null,session:crypto.randomUUID()}).send(job()),/handoff is unavailable/);
});
test('a closed original map can be replaced by a fresh tab with a separate session',()=>{
 const prior=global.location;global.location={href:'https://example.invalid/voodoo/sar.html#event=test'};
 const opened=[],sent=[],channels=[],session=crypto.randomUUID();
 class Channel{constructor(name){this.name=name;channels.push(this);}postMessage(m){sent.push(m);}close(){this.closed=true;}}
 const sender=S.createSender({Channel,session,openTab:url=>opened.push(url)});
 try{
  sender.send(job(),{newMap:true});assert.equal(opened.length,1);const u=new URL(opened[0]);
  assert.equal(u.pathname,'/voodoo/index.html');assert.equal(u.hash,'');assert.notEqual(u.searchParams.get('satellite_session'),session);
  assert.equal(channels[0].name,'voodoo-satellite-'+u.searchParams.get('satellite_session'));
  assert.equal(sent.length,0);channels[0].onmessage({data:{type:'ready'}});assert.equal(sent[0].type,'image');
  channels[0].onmessage({data:{type:'received',id:sent[0].id}});assert.ok(channels[0].closed);
 }finally{sender.dispose();if(prior===undefined)delete global.location;else global.location=prior;}
});
