const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('../public/assets/local-project-core.js');
const now=Date.parse('2026-11-16T12:00:00Z');
const point=(hours=1,extra={})=>({mmsi:'273123456',imo:'9198226',callsign:'TEST1',name:'Example ship',latitude:55,longitude:10,observed_at:new Date(now-hours*3600000).toISOString(),...extra});
const project=(points,label='Session')=>C.addImport(C.newProject(label),{observations:points,routes:[]},'positions.csv','csv');
test('CSV headers, UTF-8 BOM, semicolon decimal commas and quoted names',()=>{
 const parsed=C.parseCSV('\uFEFFExport from AIS\nMMSI;IMO;Ship Name;Latitude;Longitude;Time (UTC)\n273123456;9198226;"Example; ship";55,5;10,25;2026-11-16 11:00:00\n');
 assert.equal(parsed.rejected,0);assert.equal(parsed.observations[0].name,'Example; ship');assert.equal(parsed.observations[0].latitude,55.5);assert.equal(parsed.observations[0].observed_at,'2026-11-16T11:00:00.000Z');
});
test('CSV tab separator, separate date/time and fallback identity',()=>{
 const p=C.parseCSV('lat\tlon\tdate\ttime\n55\t10\t2026-11-16\t11:00:00\n',{defaults:{mmsi:'273123456',name:'Ship'},assumeUTC:true});assert.equal(p.observations.length,1);assert.equal(p.observations[0].name,'Ship');
});
test('invalid identities, ambiguous dates, impossible coordinates and bad quotes are rejected',()=>{
 const head='mmsi,name,latitude,longitude,timestamp\n';
 for(const row of ['bad,Ship,55,10,2026-11-16T11:00:00Z',',Ship,55,10,2026-11-16T11:00:00Z','273123456,Ship,91,10,2026-11-16T11:00:00Z','273123456,Ship,55,10,2026-11-16 11:00:00','273123456,Ship,55,10,2026-02-30T11:00:00Z'])assert.equal(C.parseCSV(head+row).rejected,1);
 assert.throws(()=>C.parseCSV(head+'273123456,"Unclosed,55,10,time'),/quote/);
 assert.equal(C.parseCSV('imo,lat,lon,timestamp\n9198227,55,10,2026-11-16T11:00:00Z').rejected,1);
});
test('preview retains valid zero coordinates and reports rejected rows separately',()=>{
 const p=C.parseCSV('mmsi,lat,lon,timestamp\n273123456,0,0,2026-11-16T11:00:00Z\n273123456,,10,2026-11-16T11:00:00Z\n');assert.equal(p.observations.length,1);assert.equal(p.observations[0].latitude,0);assert.equal(p.rejected,1);
});
test('canonical project roundtrip preserves points, geometry, identities and local choices',()=>{
 const p=project([point(),point(2,{longitude:10.1})]);p.imports[0].routes.push({mmsi:'273123456',imo:'',callsign:'',name:'Route',coordinates:[[10,55],[10.1,55.1]]});p.decisions.example='choice';assert.deepEqual(C.loadProject(C.serialize(p)),p);
});
test('project validation rejects unexpected schemas, duplicate import IDs and invalid points',()=>{
 const p=project([point()]);assert.throws(()=>C.loadProject(JSON.stringify({...p,schema:'other'})));p.imports.push(p.imports[0]);assert.throws(()=>C.loadProject(JSON.stringify(p)),/import/);
 const q=project([point()]);q.imports[0].observations[0].latitude=999;assert.throws(()=>C.loadProject(JSON.stringify(q)),/coordinate/i);
});
test('same names never merge; MMSI/IMO and unique callsigns do',()=>{
 const p=project([point(1,{imo:'',mmsi:'',callsign:'TEST1'})]);
 assert.equal(C.reconcile([p],[point(2)],{now}).models.length,1);
 const other=point(2,{imo:'9349291',mmsi:'273654321',callsign:'OTHER'});
 assert.equal(C.reconcile([project([point()])],[other],{now}).models[0].points.length,1);
 const ambiguous=C.reconcile([p],[point(2),point(2,{imo:'9349291',mmsi:'273654321'})],{now});assert.ok(ambiguous.models[0].points[0].identity_conflict);assert.equal(ambiguous.models[0].points.length,1);
});
test('conflicting MMSI/IMO combinations block automatic track continuity',()=>{
 const p=project([point(1,{imo:'9349291'})]);const m=C.reconcile([p],[point(2)],{now});assert.equal(m.models[0].points.length,1);assert.equal(m.models[0].points[0].identity_conflict,true);
});
test('position discrepancy requires a choice, keeps raw evidence and stores a persistent decision',()=>{
 const p=project([point(1,{longitude:12}),point(2,{longitude:12})]);const workflow=[point(1)];
 const before=C.reconcile([p],workflow,{now});assert.equal(before.conflicts.length,1);assert.equal(before.models[0].segments.length,0);
 const c=before.conflicts[0],winner=c.alternatives.find(p=>p.origin==='local').id;p.decisions[c.key]=winner;
 const after=C.reconcile([C.loadProject(C.serialize(p))],workflow,{now});assert.equal(after.conflicts[0].winner,winner);assert.equal(after.models[0].points.length,2);assert.equal(after.models[0].segments.length,1);assert.equal(workflow[0].longitude,10);assert.equal(p.imports[0].observations.length,2);
});
test('decisions from overlapping projects that disagree remain unresolved',()=>{
 const a=project([point(1,{longitude:12})],'A'),b=project([point(1,{longitude:14})],'B');const m=C.reconcile([a,b],[],{now}),c=m.conflicts[0];a.decisions[c.key]=c.alternatives[0].id;b.decisions[c.key]=c.alternatives[1].id;assert.equal(C.reconcile([a,b],[],{now}).conflicts[0].winner,null);
});
test('different coordinates at exactly the same timestamp are a conflict even below one nautical mile',()=>{
 const p=project([point(1,{longitude:10.001})]);assert.equal(C.reconcile([p],[point(1)],{now}).conflicts.length,1);
});
test('time windows apply before points become models or search candidates',()=>{
 const p=project([point(48)]);assert.equal(C.reconcile([p],[],{now,hours:24}).models.length,0);assert.equal(C.reconcile([p],[],{now,hours:48}).models.length,1);
 const fresh=project([point(1)]);const m=C.reconcile([fresh],[point(48,{name:'Old-only name'})],{now,hours:24});assert.equal(m.models[0].points.length,1);assert.equal(m.models[0].points[0].name,'Example ship');
});
test('all project layers participate without mutating workflow or clipping saved historical points',()=>{
 const a=project([point(400)]),b=project([point(2,{imo:'9349291',mmsi:'273654321'})]);const wf=[point(1)];const before=JSON.stringify(wf);const m=C.reconcile([a,b],wf,{now,hours:24});assert.equal(m.models.length,2);assert.equal(JSON.stringify(wf),before);assert.equal(a.imports[0].observations.length,1);assert.equal(C.count([a,b]),2);
});
test('gaps and implausible jumps are not joined and same-point duplicates create no zero-length lines',()=>{
 const p=project([point(1),point(1),point(14),point(14.1,{longitude:30})]);const m=C.reconcile([p],[],{now}).models[0];assert.equal(m.segments.length,0);assert.equal(m.gaps.length,2);
});
test('saved workflow context keeps an accepted workflow position reproducible after source expiry',()=>{
 const period={start:new Date(now-502*3600000).toISOString(),end:new Date(now-499*3600000).toISOString()};
 let p=project([point(500,{longitude:12})]);
 const workflow=[point(500,{source_provider:'openwaters',position_is_exact:false,upstream_source:'Regional relay',private_endpoint:'must-not-copy'}),point(501),point(1),point(500,{imo:'9349291',mmsi:'273654321',callsign:'OTHER'})];
 const original=JSON.stringify(workflow);p=C.withWorkflowContext(p,workflow,{period});
 assert.equal(p.workflow_context.observations.length,2);assert.equal(C.count([p]),3);assert.equal(p.workflow_context.observations[0].observed_at,point(500).observed_at);
 assert.equal(p.workflow_context.observations[0].position_is_exact,false);assert.equal(p.workflow_context.observations[0].source_provider,'openwaters');
 assert.doesNotMatch(C.serialize(p),/must-not-copy/);assert.equal(JSON.stringify(workflow),original);
 const conflict=C.reconcile([p],workflow,{period}).conflicts[0];const winner=conflict.alternatives.find(x=>x.origin==='workflow').id;
 p.decisions[conflict.key]=winner;p.project_period=period;
 const reopened=C.loadProject(C.serialize(p));const historical=C.reconcile([reopened],[],{period,now});
 assert.equal(historical.conflicts[0].winner,winner);assert.ok(historical.models[0].points.some(x=>x.longitude===10&&x._saved_context));assert.ok(!historical.models[0].points.some(x=>x.longitude===12));
 assert.equal(C.reconcile([reopened],[],{now,hours:24}).models.length,0);
 assert.deepEqual(C.projectPeriod([reopened],{savedSelection:true}),period);
});
test('context capture accumulates without duplicate copies and live duplicates keep stable conflict IDs',()=>{
 let p=project([point()]);const wf=[point(2,{source_provider:'fintraffic'})];
 p=C.withWorkflowContext(p,wf,{now});const once=C.serialize(p);p=C.withWorkflowContext(p,wf,{now});assert.equal(C.serialize(p),once);
 const m=C.reconcile([p],wf,{now});assert.equal(m.models[0].points.length,2);assert.equal(m.models[0].points.filter(x=>x.origin==='workflow').length,1);assert.equal(m.models[0].points.find(x=>x.origin==='workflow')._saved_context,undefined);
 p=C.withWorkflowContext(p,[point(3)],{now});assert.equal(p.workflow_context.observations.length,2);
});
test('legacy V1 files migrate without inventing missing context and V2 validates context and dates',()=>{
 const original=project([point()]);const legacy={...original,schema:C.LEGACY_SCHEMA};delete legacy.workflow_context;delete legacy.project_period;
 const migrated=C.loadProject(JSON.stringify(legacy));assert.equal(migrated.schema,C.SCHEMA);assert.equal(migrated.id,original.id);assert.equal(migrated.workflow_context.observations.length,0);assert.equal(migrated.workflow_context.captured_at,null);
 assert.throws(()=>C.loadProject(JSON.stringify({...original,workflow_context:null})),/context/);
 assert.throws(()=>C.loadProject(JSON.stringify({...original,workflow_context:{...original.workflow_context,observations:[point()],captured_at:null}})),/context/);
 assert.throws(()=>C.validatePeriod({start:point(1).observed_at,end:point(2).observed_at}),/later/);
 assert.throws(()=>C.loadProject(JSON.stringify({...original,project_period:{start:'bad',end:'bad'}})));
});
test('project date ranges combine multiple sessions without weakening rolling windows',()=>{
 const a=project([point(600)]),b=project([point(300,{mmsi:'273654321',imo:'9349291'})]);const period=C.projectPeriod([a,b]);
 assert.equal(period.start,point(600).observed_at);assert.equal(period.end,point(300).observed_at);
 assert.equal(C.reconcile([a,b],[],{period,now}).models.length,2);assert.equal(C.reconcile([a,b],[],{hours:48,now}).models.length,0);
 const narrowed={start:point(601).observed_at,end:point(599).observed_at};assert.equal(C.reconcile([a,b],[],{period:narrowed,now}).models.length,1);
});
test('saved context retains ambiguous identity warnings after the conflicting upstream alias expires',()=>{
 let p=project([point(1),point(2)]);
 p=C.withWorkflowContext(p,[point(3),point(3,{imo:'9349291'})],{now});
 const reopened=C.loadProject(C.serialize(p)),m=C.reconcile([reopened],[],{now});
 assert.ok(reopened.workflow_context.observations.some(x=>x.identity_conflict));assert.ok(m.models[0].points.every(x=>x.identity_conflict));assert.equal(m.models[0].segments.length,0);
});
