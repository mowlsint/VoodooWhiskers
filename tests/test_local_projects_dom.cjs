/* Optional test-only DOM dependency; never loaded by the dashboard. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require(process.env.VOODOO_TEST_DOM_MODULE||'jsdom');
const root=path.resolve(__dirname,'..');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let n=0;n<100;n++){if(fn())return;await wait(10);}throw Error('DOM condition timed out');}
async function boot(t,{offline=false,prepareRows=()=>{},aircraftResponse=null}={}){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'public/index.html'),'utf8'),{url:'https://voodoo.example/',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,now=Date.now(),requests=[],errors=[],downloads=[];t.after(()=>w.close());
 w.Date.now=()=>now;w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;w.Blob=Blob;w.CSS={escape:v=>v};w.confirm=()=>true;
 w.SVGSVGElement.prototype.createSVGRect=()=>({});
 w.URL.createObjectURL=b=>{downloads.push(b);return 'blob:local-test';};w.URL.revokeObjectURL=()=>{};
 w.HTMLAnchorElement.prototype.click=function(){};
 const point=(name,mmsi,hours,lon,priority=false)=>({name,mmsi,latitude:55,longitude:lon,observed_at:new Date(now-hours*3600000).toISOString(),source_provider:'openwaters',known_voi_match:priority,categories:priority?['watchlist']:[]});
 const fresh=point('Current ship','273123456',1,10,true),old=point('Older ship','273654321',48,11),background=point('Other received ship','273987654',2,9);
 prepareRows({fresh,old,background});
 const fc=rows=>({type:'FeatureCollection',features:rows.map(p=>({type:'Feature',geometry:{type:'Point',coordinates:[p.longitude,p.latitude]},properties:p}))});
 w.fetch=async(url,options={})=>{requests.push({url:String(url),method:options.method||'GET'});const p=new URL(String(url),w.location.href).pathname;let data;
  if(p.endsWith('aircraft_latest.json')){if(!aircraftResponse)return {ok:false,status:404,text:async()=>''};data=typeof aircraftResponse==='function'?aircraftResponse(now):aircraftResponse;return {ok:true,status:200,text:async()=>JSON.stringify(data)};}
  if(offline)return {ok:false,status:503,text:async()=>''};
  if(p.endsWith('ais_contacts_latest.geojson'))data=fc([fresh,old,background]);
  else if(p.endsWith('vessel_positions_latest.geojson'))data=fc([fresh]);
  else if(p.endsWith('voi_history_14d.jsonl'))return {ok:true,status:200,text:async()=>[fresh,old].map(JSON.stringify).join('\n')};
  else if(p.endsWith('manifest.json'))data={generated_at:new Date(now).toISOString(),products:[],snapshot:{item_count:1},layers:[]};
  else if(p.endsWith('status.json')||p.includes('status_latest'))data={status:'unavailable',snapshot_current:false};
  else data={type:'FeatureCollection',features:[],records:[],events:[]};
  return {ok:true,status:200,text:async()=>JSON.stringify(data),json:async()=>data};
 };
 w.console.error=(...args)=>errors.push(args.map(String).join(' '));
 w.console.warn=()=>{};
 let map;
 for(const asset of ['vendor/leaflet.js','sar-map-hook.js','vessel-model.js','vessel-categories.js','traffic-context.js','gfw-grid.js','local-project-core.js','local-project-ui.js','special-aircraft.js','infrastructure-watch.js']){
  if(asset==='sar-map-hook.js'){const original=w.L.map;w.L.map=function(...args){map=original(...args);return map;};}
  w.eval(fs.readFileSync(path.join(root,'public/assets',asset),'utf8'));
 }
 await until(()=>!['Loading data','Initialisation failed'].includes(w.document.getElementById('dataStatus').textContent));
 assert.deepEqual(errors,[]);
 const $=id=>w.document.getElementById(id);
 function fileInput(id,files){Object.defineProperty($(id),'files',{value:files,configurable:true});}
 function input(id,value){$(id).value=value;$(id).dispatchEvent(new w.Event('input',{bubbles:true}));}
 async function time(mode){w.document.querySelector(`[data-time-mode="${mode}"]`).click();await until(()=>$('timeStatus').textContent.startsWith(mode==='latest'?'Latest':mode));}
 return {w,now,$,input,fileInput,time,requests,errors,downloads,getMap:()=>map};
}
test('dashboard search excludes 48-hour-old ships in 24 h and jumps to the point inside the chosen window',async t=>{
 const b=await boot(t);await b.time('24h');b.input('vesselSearch','Older ship');await wait(230);
 assert.match(b.$('vesselList').textContent,/No matching/);
 assert.equal(b.$('vesselList').querySelectorAll('button').length,0);
 await b.time('48h');assert.match(b.$('vesselList').textContent,/Older ship/);
 b.$('vesselList').querySelector('button').click();assert.ok(Math.abs(b.getMap().getCenter().lng-11)<.02); // Leaflet may pan slightly to fit the popup.
 await b.time('24h');assert.match(b.$('vesselList').textContent,/No matching/);
 b.input('vesselSearch','Other received');assert.match(b.$('vesselList').textContent,/Other received ship/);
 b.$('vesselSearch').dispatchEvent(new b.w.KeyboardEvent('keydown',{key:'Enter'}));assert.ok(Math.abs(b.getMap().getCenter().lng-9)<.02);
 assert.deepEqual(b.errors,[]);
});
test('real map markers use category fill/ring across providers and pop-ups show every category and qualified Russia direction',async t=>{
 const b=await boot(t,{prepareRows:({fresh,background})=>{
  Object.assign(fresh,{sanctioned:true,shadow_fleet:true,destination:'RU ULU>NL RTM',source_provider:'fintraffic'});
  Object.assign(background,{sanctioned:true,shadow_fleet:true,known_voi_match:true,categories:['watchlist'],destination:'NL RTM>RU ULU',source_provider:'barentswatch'});
 }});
 await until(()=>b.$('categoryHistoryStatus').textContent.startsWith('Voyage context uses'));
 const markers=Object.values(b.getMap()._layers).filter(l=>l.feature?.geometry?.type==='Point'&&l.getPopup?.());
 const from=markers.find(l=>l.feature.properties.mmsi==='273123456'),to=markers.find(l=>l.feature.properties.mmsi==='273987654');
 assert.ok(from);assert.ok(to);assert.equal(from.options.fillColor,'#ef5959');assert.equal(from.options.color,'#ac83ff');
 assert.equal(to.options.fillColor,from.options.fillColor);assert.equal(to.options.color,from.options.color);
 from.openPopup();let content=from.getPopup().getElement();assert.match(content.textContent,/Sanctions-listed vessel/);assert.match(content.textContent,/Shadow-fleet list context/);assert.match(content.textContent,/Other VOI \/ watchlist/);assert.match(content.textContent,/Russia-related voyage/);assert.match(content.textContent,/← Likely coming from Russia/);assert.doesNotMatch(content.textContent,/→ Likely bound for Russia/);assert.match(content.textContent,/Fintraffic/);
 to.openPopup();content=to.getPopup().getElement();assert.match(content.textContent,/→ Likely bound for Russia/);assert.doesNotMatch(content.textContent,/← Likely coming from Russia/);assert.match(content.textContent,/BarentsWatch/);
 assert.ok(b.$('vesselCategoryLegend').textContent.includes('Russia-related voyage'));
 assert.deepEqual(b.errors,[]);
});
test('a declared Russia destination enters the default category layer and untrusted voyage text remains plain text',async t=>{
 const b=await boot(t,{prepareRows:({background})=>Object.assign(background,{mmsi:'257987654',destination:'RU ULU',source_provider:'barentswatch'})});
 await until(()=>b.$('categoryHistoryStatus').textContent.startsWith('Voyage context uses'));
 const marker=Object.values(b.getMap()._layers).find(l=>l.feature?.properties?.mmsi==='257987654'&&l.getPopup?.());
 assert.ok(marker);assert.equal(marker.options.fillColor,'#ffa84d');marker.openPopup();assert.match(marker.getPopup().getElement().textContent,/→ Likely bound for Russia/);
 const box=b.w.document.createElement('div');b.w.VesselCategories.appendPopup(box,{mmsi:'257987654',observed_at:new Date(b.now).toISOString(),destination:'RUSSIA <img src=x onerror=alert(1)>',categories:['<script>alert(1)</script>']});
 assert.equal(box.querySelector('img,script'),null);assert.match(box.textContent,/<img/);assert.match(box.textContent,/direction unclear/);
 assert.deepEqual(b.errors,[]);
});
test('CSV preview, local merge, conflict choice, download and project overlay work through actual controls',async t=>{
 const b=await boot(t);await b.time('24h');b.input('vesselSearch','');
 const csv=`mmsi,name,latitude,longitude,timestamp\n273123456,Current ship,55,12,${new Date(b.now-3600000).toISOString()}\n273123456,Current ship,55,12,${new Date(b.now-7200000).toISOString()}\n`;
 b.fileInput('localTrackFile',[{name:'example.csv',size:Buffer.byteLength(csv),text:async()=>csv}]);
 b.$('localImportPreview').click();await until(()=>!b.$('localImportAdd').disabled);assert.match(b.$('localProjectStatus').textContent,/2 timestamped/);
 b.$('localImportAdd').click();await until(()=>b.$('localProjectStatus').textContent.startsWith('Imported'));
 assert.match(b.$('localConflictSummary').textContent,/1 unresolved/);
 b.input('vesselSearch','Current ship');assert.equal(b.$('vesselList').querySelectorAll('button').length,2);assert.match(b.$('vesselList').textContent,/Unresolved position/);
 const select=b.$('localConflicts').querySelector('select');const choice=Array.from(select.options).find(o=>o.textContent.includes('example.csv'));assert.ok(choice);
 select.value=choice.value;select.dispatchEvent(new b.w.Event('change'));await until(()=>b.$('localConflictSummary').textContent.startsWith('0 unresolved'));
 assert.equal(b.$('vesselList').querySelectorAll('button').length,1);assert.doesNotMatch(b.$('vesselList').textContent,/Unresolved position/);
 b.$('localProjectSave').click();await until(()=>b.downloads.length===1);
 const saved=JSON.parse(await b.downloads[0].text());assert.equal(saved.schema,'VOODOO_LOCAL_PROJECT_V2');assert.equal(saved.imports[0].observations.length,2);assert.equal(Object.keys(saved.decisions).length,1);assert.equal(saved.workflow_context.observations.length,1);
 const C=b.w.LocalProjectCore;let second=C.newProject('Other project');second=C.addImport(second,{observations:[{mmsi:'273555555',name:'Local older ship',latitude:56,longitude:13,observed_at:new Date(b.now-48*3600000).toISOString()}],routes:[]},'second.csv','csv');
 const text=C.serialize(second);b.fileInput('localProjectFiles',[{name:'second.vwproject.json',size:text.length,text:async()=>text}]);b.$('localProjectFiles').dispatchEvent(new b.w.Event('change'));await until(()=>b.$('localProjectList').querySelectorAll('input').length===2);
 b.input('vesselSearch','Local older');assert.match(b.$('vesselList').textContent,/No matching/);await b.time('48h');assert.match(b.$('vesselList').textContent,/Local older ship/);
 const check=b.$('localProjectList').querySelectorAll('input')[1];check.checked=false;check.dispatchEvent(new b.w.Event('change'));assert.match(b.$('vesselList').textContent,/No matching/);
 assert.ok(b.requests.every(r=>r.method==='GET'&&!/example\.csv|273555555|Local%20older|vwproject/.test(r.url)));
 assert.deepEqual(b.errors,[]);
});
test('KML parser accepts timestamped Points, gx:Track and separate undated routes; rejects DTD and remote links',async t=>{
 const b=await boot(t),C=b.w.LocalProjectCore,when=new Date(b.now-3600000).toISOString();
 const kml=`<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Document><Placemark><name>Point ship</name><ExtendedData><Data name="MMSI"><value>273123456</value></Data></ExtendedData><TimeStamp><when>${when}</when></TimeStamp><Point><coordinates>10,55,0</coordinates></Point></Placemark><Placemark><gx:Track><when>${when}</when><gx:coord>11 56 0</gx:coord></gx:Track></Placemark><Placemark><LineString><coordinates>10,55,0 11,56,0</coordinates></LineString></Placemark></Document></kml>`;
 const p=C.parseKML(kml,{defaults:{mmsi:'273123456'}});assert.equal(p.observations.length,2);assert.equal(p.routes.length,1);assert.equal(p.rejected,0);assert.equal(p.observations[1].longitude,11);
 assert.throws(()=>C.parseKML('<!DOCTYPE kml><kml/>'),/DTD/);assert.throws(()=>C.parseKML('<kml><NetworkLink/></kml>'),/Linked/);assert.throws(()=>C.parseKML('<kml><bad></kml>'),/Invalid/);
 const broken=C.parseKML('<kml xmlns:gx="http://www.google.com/kml/ext/2.2"><Placemark><gx:Track><when>'+when+'</when><gx:coord>10 55 0</gx:coord><gx:coord>11 56 0</gx:coord></gx:Track></Placemark></kml>',{defaults:{mmsi:'273123456'}});assert.equal(broken.observations.length,0);assert.equal(broken.rejected,1);
});
test('untrusted names stay text and local overlays/search pause during historical SAR comparison',async t=>{
 const b=await boot(t);b.input('localProjectName','<img src=x onerror=alert(1)>');b.$('localNewSession').click();await until(()=>b.$('localProjectList').textContent.includes('<img'));
 assert.equal(b.$('localProjectList').querySelector('img'),null);
 b.$('sarDetectionsToggle').checked=true;b.$('sarDetectionsToggle').dispatchEvent(new b.w.Event('change'));await wait(20);b.input('vesselSearch','Current');assert.match(b.$('localOverlayStatus').textContent,/hidden during historical SAR/);assert.match(b.$('vesselList').textContent,/paused/);
});
test('old projects preserve a selected workflow position offline and never use live positions in project mode',async t=>{
 const b=await boot(t),C=b.w.LocalProjectCore;
 const oldTime=new Date(b.now-500*3600000).toISOString(),earlier=new Date(b.now-501*3600000).toISOString();
 const local={mmsi:'273123456',name:'Archived vessel',latitude:55,longitude:12,observed_at:oldTime};
 let p=C.addImport(C.newProject('Historical session'),{observations:[local],routes:[]},'archived.csv','csv');
 p=C.withWorkflowContext(p,[{...local,longitude:8,source_provider:'openwaters'},{...local,longitude:8,observed_at:earlier,source_provider:'openwaters'}],{period:{start:earlier,end:oldTime}});
 const conflict=C.reconcile([p],[],{period:{start:earlier,end:oldTime}}).conflicts[0];p.decisions[conflict.key]=conflict.alternatives.find(x=>x.origin==='workflow').id;
 p.project_period={start:earlier,end:oldTime};const text=C.serialize(p);
 b.fileInput('localProjectFiles',[{name:'historical.vwproject.json',size:text.length,text:async()=>text}]);b.$('localProjectFiles').dispatchEvent(new b.w.Event('change'));await until(()=>b.$('localProjectList').querySelectorAll('input').length===1);
 await b.time('24h');b.input('vesselSearch','Archived vessel');assert.match(b.$('vesselList').textContent,/No matching/);
 b.$('localUseProjectDates').click();await until(()=>b.$('timeStatus').textContent.startsWith('Project period'));
 assert.equal(b.$('vesselList').querySelectorAll('button').length,1);assert.match(b.$('vesselList').textContent,/Saved workflow context/);assert.match(b.$('aisDataAgeBanner').textContent,/live AIS hidden/);
 const count=b.requests.length;b.input('vesselSearch','273123456');await wait(230);assert.equal(b.requests.length,count);
 b.$('vesselList').querySelector('button').click();assert.ok(Math.abs(b.getMap().getCenter().lng-8)<.02);
 b.$('localProjectSave').click();await until(()=>b.downloads.length===1);const saved=await b.downloads[0].text();
 assert.equal(JSON.parse(saved).workflow_context.observations.length,2);assert.equal(JSON.parse(saved).project_period.start,earlier);
 const offline=await boot(t,{offline:true});offline.fileInput('localProjectFiles',[{name:'saved.vwproject.json',size:saved.length,text:async()=>saved}]);offline.$('localProjectFiles').dispatchEvent(new offline.w.Event('change'));await until(()=>offline.$('localProjectList').querySelectorAll('input').length===1);
 offline.w.document.querySelector('[data-time-mode="project"]').click();await until(()=>offline.$('timeStatus').textContent.startsWith('Project period'));
 offline.input('vesselSearch','Archived vessel');assert.equal(offline.$('vesselList').querySelectorAll('button').length,1);assert.match(offline.$('localConflictSummary').textContent,/0 unresolved/);
 offline.$('vesselList').querySelector('button').click();assert.ok(Math.abs(offline.getMap().getCenter().lng-8)<.02);
 assert.deepEqual(offline.errors,[]);assert.ok(offline.requests.every(r=>r.method==='GET'&&!r.url.includes('archived.csv')));
});
test('explicit UTC project ranges narrow multiple old overlays and rolling search stays strict after switching back',async t=>{
 const b=await boot(t),C=b.w.LocalProjectCore;
 const points=[{mmsi:'273444444',name:'Historical recent',latitude:54,longitude:7,observed_at:new Date(b.now-100*3600000).toISOString()},{mmsi:'273555555',name:'Historical older',latitude:54,longitude:8,observed_at:new Date(b.now-200*3600000).toISOString()}];
 const files=points.map((p,i)=>{const text=C.serialize(C.addImport(C.newProject('History '+i),{observations:[p],routes:[]},'history.csv','csv'));return {name:i+'.vwproject.json',size:text.length,text:async()=>text};});
 b.fileInput('localProjectFiles',files);b.$('localProjectFiles').dispatchEvent(new b.w.Event('change'));await until(()=>b.$('localProjectList').querySelectorAll('input').length===2);
 b.$('localUseProjectDates').click();await until(()=>b.$('timeStatus').textContent.startsWith('Project period'));await wait(0);b.input('vesselSearch','Historical');assert.equal(b.$('vesselList').querySelectorAll('button').length,2);
 b.input('vesselSearch','Current ship');assert.match(b.$('vesselList').textContent,/No matching/);
 b.input('localPeriodStart',new Date(b.now-101*3600000).toISOString().replace(/Z$/,''));b.input('localPeriodEnd',new Date(b.now-99*3600000).toISOString().replace(/Z$/,''));b.$('localApplyProjectPeriod').click();await wait(10);
 b.input('vesselSearch','Historical');assert.equal(b.$('vesselList').querySelectorAll('button').length,1);assert.match(b.$('vesselList').textContent,/Historical recent/);
 const status=b.$('timeStatus').textContent;b.input('localPeriodStart','');b.$('localApplyProjectPeriod').click();await wait(10);assert.equal(b.$('timeStatus').textContent,status);
 await b.time('48h');assert.match(b.$('vesselList').textContent,/No matching/);b.input('vesselSearch','Current ship');assert.match(b.$('vesselList').textContent,/Current ship/);
 assert.deepEqual(b.errors,[]);
});

const aircraftFixture=now=>({kind:'selected_aircraft_context',schema_version:'1.0.0',context_only:true,coverage_complete:false,status:'fresh',generated_at:new Date(now).toISOString(),items:[
 {icao24:'abc123',registration:'EXAMPLE-TEST',operator:'<img src=x onerror=alert(1)>',source:'Example provider',latitude:55,longitude:12,observed_at:new Date(now-600000).toISOString(),position_timestamp_valid:true,selection_status:'verified_registry',roles:['police','rpas']},
 {icao24:'def456',registration:'OLD-TEST',latitude:55,longitude:13,observed_at:new Date(now-48*3600000).toISOString(),position_timestamp_valid:true,selection_status:'verified_registry',roles:['coast_guard']},
 {icao24:'fed321',registration:'CIVIL-TEST',latitude:55,longitude:14,observed_at:new Date(now-600000).toISOString(),position_timestamp_valid:true,selection_status:'verified_registry',roles:['offshore_transport']}
]});
const aircraftMarkers=b=>Object.values(b.getMap()._layers).filter(l=>l.options?.specialAircraft);
function toggleAircraft(b,value=true){b.$('showSpecialAircraft').checked=value;b.$('showSpecialAircraft').dispatchEvent(new b.w.Event('change'));}
test('special aircraft control is exposed and usable, defaults off and missing data never breaks vessel search',async t=>{
 const b=await boot(t);assert.equal(b.$('showSpecialAircraft').checked,false);assert.equal(b.$('showSpecialAircraft').disabled,false);assert.equal(b.$('showSpecialAircraft').closest('details'),null);assert.equal(b.requests.filter(r=>r.url.includes('aircraft_latest')).length,0);
 toggleAircraft(b);await until(()=>b.$('specialAircraftStatus').textContent.includes('unavailable'));assert.equal(aircraftMarkers(b).length,0);assert.match(b.$('specialAircraftStatus').textContent,/not a zero result/);
 b.input('vesselSearch','Current ship');assert.match(b.$('vesselList').textContent,/Current ship/);toggleAircraft(b,false);assert.match(b.$('specialAircraftStatus').textContent,/Off/);assert.deepEqual(b.errors,[]);
});
test('aircraft symbols, role/source pop-ups, offshore control and time windows use actual controls',async t=>{
 const b=await boot(t,{aircraftResponse:aircraftFixture});toggleAircraft(b);await until(()=>b.$('specialAircraftStatus').textContent.includes('displayed'));
 assert.equal(aircraftMarkers(b).length,1);b.$('specialAircraftList').querySelector('button').click();const popup=aircraftMarkers(b)[0].getPopup().getElement();assert.match(popup.textContent,/Police, RPAS/);assert.match(popup.textContent,/Example provider/);assert.match(popup.textContent,/<img/);assert.equal(popup.querySelector('img'),null);assert.match(aircraftMarkers(b)[0].getElement().textContent,/✈/);
 b.$('showOffshoreAircraft').checked=true;b.$('showOffshoreAircraft').dispatchEvent(new b.w.Event('change'));assert.equal(aircraftMarkers(b).length,2);
 await b.time('24h');assert.equal(aircraftMarkers(b).length,2);assert.doesNotMatch(b.$('specialAircraftList').textContent,/OLD-TEST/);await b.time('48h');assert.equal(aircraftMarkers(b).length,3);await b.time('24h');assert.equal(aircraftMarkers(b).length,2);
 b.$('resetOverview').click();await until(()=>b.$('timeStatus').textContent.startsWith('Latest'));assert.equal(b.$('showSpecialAircraft').checked,false);assert.equal(aircraftMarkers(b).length,0);assert.deepEqual(b.errors,[]);
});
test('aircraft stay hidden during SAR comparison even when loading completes after the mode switch',async t=>{
 const b=await boot(t,{aircraftResponse:aircraftFixture});toggleAircraft(b);b.$('sarDetectionsToggle').checked=true;b.$('sarDetectionsToggle').dispatchEvent(new b.w.Event('change'));await until(()=>b.$('specialAircraftStatus').textContent.includes('hidden during'));await wait(20);assert.equal(aircraftMarkers(b).length,0);
 b.$('sarDetectionsToggle').checked=false;b.$('sarDetectionsToggle').dispatchEvent(new b.w.Event('change'));await until(()=>aircraftMarkers(b).length===1);assert.deepEqual(b.errors,[]);
});
test('live aircraft are hidden in historical local project mode and return when leaving it',async t=>{
 const b=await boot(t,{aircraftResponse:aircraftFixture}),C=b.w.LocalProjectCore;toggleAircraft(b);await until(()=>aircraftMarkers(b).length===1);
 const text=C.serialize(C.addImport(C.newProject('Aircraft isolation test'),{observations:[{mmsi:'273123456',name:'Historical ship',latitude:55,longitude:10,observed_at:new Date(b.now-500*3600000).toISOString()}],routes:[]},'example.csv','csv'));
 b.fileInput('localProjectFiles',[{name:'test.vwproject.json',size:text.length,text:async()=>text}]);b.$('localProjectFiles').dispatchEvent(new b.w.Event('change'));await until(()=>b.$('localProjectList').querySelectorAll('input').length===1);b.$('localUseProjectDates').click();await until(()=>b.$('timeStatus').textContent.startsWith('Project period'));assert.equal(aircraftMarkers(b).length,0);assert.match(b.$('specialAircraftStatus').textContent,/hidden during/);await b.time('24h');assert.equal(aircraftMarkers(b).length,1);assert.deepEqual(b.errors,[]);
});
test('enabling aircraft inside SAR mode reports the hidden state and does not start a fetch',async t=>{
 const b=await boot(t,{aircraftResponse:aircraftFixture});b.$('sarDetectionsToggle').checked=true;b.$('sarDetectionsToggle').dispatchEvent(new b.w.Event('change'));await wait(20);toggleAircraft(b);await wait(20);assert.match(b.$('specialAircraftStatus').textContent,/hidden during/);assert.equal(b.requests.filter(r=>r.url.includes('aircraft_latest')).length,0);assert.equal(aircraftMarkers(b).length,0);
});
