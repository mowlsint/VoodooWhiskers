const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('../public/assets/vessel-categories.js');
const Core=require('../public/assets/local-project-core.js');
const now=Date.now();
const stamp=(hours=0)=>new Date(now-hours*3600000).toISOString();
const row=(hours=0,extra={})=>({mmsi:'273123456',latitude:55,longitude:10,observed_at:stamp(hours),source_provider:'openwaters',...extra});

test('other categories and coloured rings are independent of Russia evidence',()=>{
 const p=row(0,{sanctioned:true,shadow_fleet:true,known_voi_match:true,destination:'NL RTM>RU ULU'});
 const a=C.describe(p),b=C.describe({...p,source_provider:'fintraffic'});
 assert.equal(a.primary.id,'sanctioned');assert.equal(a.secondary.id,'shadow_fleet');
 assert.deepEqual(a.style,b.style);assert.ok(a.categories.some(c=>c.id==='watchlist'));
 assert.ok(a.categories.some(c=>c.id==='russia_voyage'));
 assert.deepEqual(C.describe(row(0,{mmsi:'211123456'})).style,C.describe(row(0,{mmsi:'211123456',source_provider:'barentswatch'})).style);
});
test('sanctions and Russian MMSI are independent classifications',()=>{
 assert.equal(C.describe(row(0,{sanctioned:true})).primary.id,'sanctioned');
 assert.equal(C.describe(row(0,{shadow_fleet:true})).primary.id,'shadow_fleet');
 const review=C.describe(row(0,{sanctioned:false,sanctions_listed_historical:true,categories:['sanctions_shadowfleet']}));
 assert.equal(review.primary.id,'sanctions_review');
 assert.ok(!C.describe(row(0,{sanctioned:'false',shadow_fleet:'false'})).categories.some(c=>['sanctioned','shadow_fleet'].includes(c.id)));
 assert.equal(C.describe(row()).russia.related,false);
 assert.equal(C.describe(row()).primary.id,'russian_mmsi');
});
test('current exact Russian destination adds a voyage only within 72 hours',()=>{
 for(const value of ['RU ULU','TO MURMANSK','NL RTM>RU ULU','FROM ROTTERDAM TO UST LUGA']){
  const v=C.describe(row(0,{destination:value}));
  assert.equal(v.russia.to,true,value);assert.equal(v.russia.from,false,value);
  assert.ok(v.russia.labels.some(l=>l.includes('Declared destination')),value);
  assert.equal(v.primary.id,'russia_voyage');
 }
 assert.equal(C.describe(row(80,{destination:'RU ULU'})).russia.related,false);
});
test('direction unclear or merely historic text is no longer a Russia voyage',()=>{
 for(const value of ['RUULU>NLRTM','Ust-Luga>Rotterdam','FROM UST LUGA TO ROTTERDAM',
  'RUULU>NLRTM>RUULU','OPERATING NEAR RUSSIA','NO RUSSIAN LINKS','NO LINK TO RUSSIA','RUST','BLYTH']){
   const v=C.describe(row(0,{destination:value}));
   assert.equal(v.russia.related,false,value);
   assert.ok(!v.russia.labels.includes('Russia-related — direction unclear'));
 }
 assert.equal(C.parseVoyage('RUULU>NLRTM').from,true);
 assert.equal(C.parseVoyage('NLRTM>RUULU').to,true);
 assert.equal(C.parseVoyage('RUULU>NLRTM>RUULU').related,false);
});
test('remembered route flags and a later destination change do not count as a visit',()=>{
 const v=C.describe(row(0,{destination:'NL RTM',to_russia_declared:true,from_russia_confirmed:true,
   recent_russian_portcall_unconfirmed_10d:true,recent_russian_portcall_confirmed_10d:true,
   recent_ru_portcall_basis:'destination_changed_after_russian_destination',
   categories:['recent_russian_portcall_10d','russia_voyage']}));
 assert.equal(v.russia.related,false);
 assert.equal(v.primary.id,'russian_mmsi');
});
test('dated port visit is retained for ten days, never fourteen days',()=>{
 const visit=row(0,{last_port_name:'Ust-Luga',last_ru_port_date:stamp(9*24)});
 assert.equal(C.describe(visit).russia.from,true);
 assert.equal(C.describe({...visit,last_ru_port_date:stamp(11*24)}).russia.related,false);
 assert.equal(C.describe(row(0,{last_port_name:'Ust-Luga'})).russia.related,false);
 assert.equal(C.describe(row(0,{last_ru_port_date:stamp(24),last_port_name:'Rotterdam'})).russia.related,false);
});
test('history contributes only a dated recent port visit, not old AIS voyage declarations',()=>{
 const current=row(0,{destination:'NL RTM'});
 const history=[row(24,{destination:'RU ULU>NL RTM'}),row(50,{destination:'RU ULU'}),
                row(26,{last_ru_port_date:stamp(28),last_port_name:'Ust-Luga'}),
                row(-1,{last_ru_port_date:stamp(2),last_port_name:'Ust-Luga'})];
 const view=C.describe(current,C.index(history));
 assert.equal(view.russia.related,true);
 assert.equal(view.russia.from,true);
 assert.equal(view.russia.to,false);
 assert.equal(view.russia.evidence.length,1);
 assert.equal(C.describe(current,C.index(history.slice(0,2))).russia.related,false);
 assert.equal(C.describe(current,C.index([row(12*24,{last_ru_port_date:stamp(12*24),last_port_name:'Ust-Luga'})])).russia.related,false);
});
test('identity matching prevents unrelated ships sharing a name or changing IMO',()=>{
 const history=[row(24,{imo:'9198226',name:'Same name',last_port_name:'Ust-Luga',last_ru_port_date:stamp(27)})];
 assert.equal(C.describe(row(0,{imo:'9349291',name:'Same name'}),C.index(history)).russia.related,false);
 assert.equal(C.describe(row(0,{mmsi:'211555555',name:'Same name'}),C.index(history)).russia.related,false);
 assert.equal(C.describe(row(0,{imo:'9198226',mmsi:'211555555'}),C.index(history)).russia.from,true);
});
test('metadata inheritance never leaks future entries into the past',()=>{
 const history=[row(24,{sanctioned:true,shadow_fleet:true,categories:['sanctions_shadowfleet']}),row(-1,{behavioral_voi:true})];
 const p=row();const original=JSON.stringify(history);const v=C.describe(p,C.index(history));
 assert.equal(v.primary.id,'sanctioned');
 assert.ok(!v.categories.some(c=>c.id==='behavioral_voi'));
 assert.equal(JSON.stringify(history),original);
 assert.equal(p.sanctioned,undefined);
 assert.ok(!C.describe({...p,sanctioned:false},C.index(history)).categories.some(c=>c.id==='sanctioned'));
});
test('local projects keep an explicit destination but do not invent a Russia visit',()=>{
 let p=Core.addImport(Core.newProject('Example'),{observations:[row(0)],routes:[]},'example.csv','csv');
 p=Core.withWorkflowContext(p,[row(1,{sanctioned:true,shadow_fleet:true,destination:'NL RTM>RU ULU',categories:['sanctions_shadowfleet']})],{now});
 const saved=Core.loadProject(Core.serialize(p)),v=C.describe(saved.workflow_context.observations[0]);
 assert.equal(v.primary.id,'sanctioned');assert.equal(v.russia.to,true);assert.equal(v.russia.from,false);
});
