/* Optional test-only DOM dependency; never loaded by the dashboard. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require(process.env.VOODOO_TEST_DOM_MODULE||'jsdom');
const root=path.resolve(__dirname,'..');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let n=0;n<100;n++){if(fn())return;await wait(10);}throw Error('DOM condition timed out');}
async function boot(t){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'public/index.html'),'utf8'),{url:'https://voodoo.example/',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,now=Date.now(),requests=[],errors=[],downloads=[];t.after(()=>w.close());
 w.Date.now=()=>now;w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;w.Blob=Blob;w.CSS={escape:v=>v};w.confirm=()=>true;
 w.SVGSVGElement.prototype.createSVGRect=()=>({});
 w.URL.createObjectURL=b=>{downloads.push(b);return 'blob:local-test';};w.URL.revokeObjectURL=()=>{};
 w.HTMLAnchorElement.prototype.click=function(){};
 const point=(name,mmsi,hours,lon,priority=false)=>({name,mmsi,latitude:55,longitude:lon,observed_at:new Date(now-hours*3600000).toISOString(),source_provider:'openwaters',known_voi_match:priority,categories:priority?['watchlist']:[]});
 const fresh=point('Current ship','273123456',1,10,true),old=point('Older ship','273654321',48,11),background=point('Other received ship','273987654',2,9);
 const fc=rows=>({type:'FeatureCollection',features:rows.map(p=>({type:'Feature',geometry:{type:'Point',coordinates:[p.longitude,p.latitude]},properties:p}))});
 w.fetch=async(url,options={})=>{requests.push({url:String(url),method:options.method||'GET'});const p=new URL(String(url),w.location.href).pathname;let data;
  if(p.endsWith('ais_contacts_latest.geojson'))data=fc([fresh,old,background]);
  else if(p.endsWith('vessel_positions_latest.geojson'))data=fc([fresh]);
  else if(p.endsWith('voi_history_14d.jsonl'))return {ok:true,status:200,text:async()=>[fresh,old].map(JSON.stringify).join('\n')};
  else if(p.endsWith('manifest.json'))data={generated_at:new Date(now).toISOString(),products:[],snapshot:{item_count:1},layers:[]};
  else if(p.endsWith('status.json')||p.includes('status_latest'))data={status:'unavailable',snapshot_current:false};
  else data={type:'FeatureCollection',features:[],records:[],events:[]};
  return {ok:true,status:200,text:async()=>JSON.stringify(data),json:async()=>data};
 };
 w.console.error=(...args)=>errors.push(args.map(String).join(' '));
 let map;
 for(const asset of ['vendor/leaflet.js','sar-map-hook.js','vessel-model.js','traffic-context.js','gfw-grid.js','local-project-core.js','local-project-ui.js','infrastructure-watch.js']){
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
 const saved=JSON.parse(await b.downloads[0].text());assert.equal(saved.schema,'VOODOO_LOCAL_PROJECT_V1');assert.equal(saved.imports[0].observations.length,2);assert.equal(Object.keys(saved.decisions).length,1);
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
