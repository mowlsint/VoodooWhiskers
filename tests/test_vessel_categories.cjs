const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('../public/assets/vessel-categories.js');
const Core=require('../public/assets/local-project-core.js');
const now=Date.parse('2026-11-16T12:00:00Z');
const row=(hours=0,extra={})=>({mmsi:'273123456',latitude:55,longitude:10,observed_at:new Date(now-hours*3600000).toISOString(),source_provider:'openwaters',...extra});
test('provider never chooses the category colour; display rank and secondary ring are deterministic',()=>{
 const p=row(0,{sanctioned:true,shadow_fleet:true,known_voi_match:true,destination:'RU ULU>NL RTM'});
 const a=C.describe(p),b=C.describe({...p,source_provider:'fintraffic'});
 assert.equal(a.primary.id,'sanctioned');assert.equal(a.secondary.id,'shadow_fleet');assert.deepEqual(a.style,b.style);
 assert.equal(a.style.fillColor,a.primary.color);assert.equal(a.style.color,a.secondary.color);
 assert.ok(a.categories.some(c=>c.id==='watchlist'));assert.ok(a.categories.some(c=>c.id==='russia_voyage'));
 assert.deepEqual(C.describe(row(0,{mmsi:'211123456'})).style,C.describe(row(0,{mmsi:'211123456',source_provider:'barentswatch'})).style);
});
test('sanctions, shadow fleet and an unresolved shared list are not interchangeable',()=>{
 assert.equal(C.describe(row(0,{sanctioned:true})).primary.id,'sanctioned');
 assert.equal(C.describe(row(0,{shadow_fleet:true})).primary.id,'shadow_fleet');
 const review=C.describe(row(0,{sanctioned:false,sanctions_listed_historical:true,categories:['sanctions_shadowfleet']}));assert.equal(review.primary.id,'sanctions_review');assert.ok(!review.categories.some(c=>c.id==='sanctioned'));
 assert.ok(!C.describe(row(0,{sanctioned:'false',shadow_fleet:'false'})).categories.some(c=>['sanctioned','shadow_fleet'].includes(c.id)));
});
test('Russia departures and destinations have the same colour but opposite popup arrows',()=>{
 const from=C.describe(row(0,{destination:'RU ULU>NL RTM'})),to=C.describe(row(0,{destination:'NL RTM>RU ULU'}));
 assert.ok(from.russia.from&&!from.russia.to);assert.ok(!to.russia.from&&to.russia.to);
 assert.ok(from.russia.labels.includes('← Likely coming from Russia'));assert.ok(to.russia.labels.includes('→ Likely bound for Russia'));
 assert.equal(from.primary.id,'russia_voyage');assert.equal(from.primary.color,to.primary.color);
 for(const value of ['RUULU>NLRTM','Ust-Luga>Rotterdam','FROM UST LUGA TO ROTTERDAM'])assert.ok(C.parseVoyage(value).from,value);
 for(const value of ['RU MMK','RUMMK','TO MURMANSK','Murmansk'])assert.ok(C.parseVoyage(value).to,value);
});
test('multi-leg/ambiguous texts stay unclear and negative disclaimers are not a Russia voyage',()=>{
 for(const value of ['RUULU>NLRTM>RUULU','OPERATING NEAR RUSSIA']){const v=C.describe(row(0,{destination:value}));assert.ok(v.russia.related);assert.deepEqual(v.russia.labels,['Russia-related — direction unclear']);}
 for(const value of ['NO RUSSIAN LINKS','NO LINK TO RUSSIA','RUST','BLYTH'])assert.equal(C.parseVoyage(value).related,false,value);
 assert.equal(C.describe(row(0,{destination:'NO RUSSIAN LINKS',categories:['recent_russian_portcall_10d']})).russia.related,false);
 const both=C.describe(row(0,{destination:'RU ULU>RU MMK'}));assert.equal(both.russia.labels.length,2);
});
test('a destination change and Russian MMSI alone never become a detected departure',()=>{
 const p=row(0,{destination:'NL RTM',recent_russian_portcall_unconfirmed_10d:true,recent_ru_portcall_basis:'destination_changed_after_russian_destination'});
 const view=C.describe(p);assert.deepEqual(view.russia.labels,['Russia-related — direction unclear']);assert.equal(view.russia.from,false);
 assert.equal(C.describe(row()).russia.related,false);assert.equal(C.describe(row()).primary.id,'russian_mmsi');
 const supplied=C.describe(row(0,{from_russia_confirmed:true,recent_ru_portcall_basis:'observed_port_field',port_codes_seen:'RU ULU'}));assert.equal(supplied.russia.from,true);assert.match(supplied.russia.limit,/not a verified port visit/);
});
test('history is bounded by the displayed observation and ten-day lookback; future evidence never leaks backwards',()=>{
 const history=[row(24,{destination:'RU ULU>NL RTM'}),row(264,{destination:'RU MMK'}),row(-1,{destination:'RU VVO'})];
 const current=row(0,{destination:'DE HAM'}),view=C.describe(current,C.index(history));
 assert.equal(view.russia.evidence.length,1);assert.equal(view.russia.evidence[0].observed_at,history[0].observed_at);assert.equal(view.russia.from,false); // current declaration has changed: retain the past evidence without inventing a current direction.
 assert.equal(C.describe(row(300),C.index(history)).russia.related,false);
 assert.equal(C.describe(current,C.index([history[1]])).russia.related,false);
});
test('a missing current destination uses the latest prior declaration, including a later non-Russian destination',()=>{
 const history=[row(48,{destination:'RU ULU'}),row(24,{destination:'NL RTM'})];
 const view=C.describe(row(),C.index(history));assert.ok(view.russia.related);assert.deepEqual(view.russia.labels,['Russia-related — direction unclear']);
 const ru=C.describe(row(),C.index([history[0]]));assert.ok(ru.russia.to);
});
test('identity matching uses IMO/MMSI and avoids same-name or conflicting-IMO joins',()=>{
 const history=[row(24,{imo:'9198226',name:'Same name',destination:'RU ULU>NL RTM'})];
 assert.equal(C.describe(row(0,{imo:'9349291',name:'Same name'}),C.index(history)).russia.related,false);
 assert.equal(C.describe(row(0,{mmsi:'211555555',name:'Same name'}),C.index(history)).russia.related,false);
 assert.equal(C.describe(row(0,{imo:'9198226',mmsi:'211555555'}),C.index(history)).russia.related,true);
});
test('classification metadata is inherited only from observations at or before the displayed point',()=>{
 const history=[row(24,{sanctioned:true,shadow_fleet:true,categories:['sanctions_shadowfleet']}),row(-1,{behavioral_voi:true})];
 const p=row();const original=JSON.stringify(history);const v=C.describe(p,C.index(history));assert.equal(v.primary.id,'sanctioned');assert.ok(!v.categories.some(c=>c.id==='behavioral_voi'));
 assert.equal(JSON.stringify(history),original);assert.equal(p.sanctioned,undefined);
 assert.ok(!C.describe({...p,sanctioned:false},C.index(history)).categories.some(c=>c.id==='sanctioned'));
});
test('local project context keeps display evidence after workflow expiry',()=>{
 let p=Core.addImport(Core.newProject('Example'),{observations:[row(0)],routes:[]},'example.csv','csv');
 p=Core.withWorkflowContext(p,[row(1,{sanctioned:true,shadow_fleet:true,destination:'RU ULU>NL RTM',categories:['sanctions_shadowfleet']})],{now});
 const saved=Core.loadProject(Core.serialize(p)),v=C.describe(saved.workflow_context.observations[0]);
 assert.equal(v.primary.id,'sanctioned');assert.ok(v.russia.from);assert.equal(saved.workflow_context.observations[0].destination,'RU ULU>NL RTM');
});
