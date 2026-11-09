import {encryptObject,decryptObject,validatePassword} from './sar-crypto.mjs';
import {inspectBbox,normalizeScene,safeImage,stateText,STATES,eventFromHash} from './sar-workbench-core.mjs?v=20261109-sources-retention';
import {readWorkspaceFile} from './sar-import.mjs';

const $=id=>document.getElementById(id),RAW='https://raw.githubusercontent.com/mowlsint/VoodooWhiskers/main/public/data/sar/workbench.enc.json';
const state={password:'',workspace:{v:1,jobs:[],analysis_ledger:{}},scenes:[],selected:null,currentJob:null,currentCandidate:null,map:null,mapLayers:null,bounds:null,config:null,event:null};
const ERRORS={EVENT_PERIOD_OUTSIDE_RETENTION:'Der Ereigniszeitraum liegt außerhalb der letzten vierzehn Tage. Öffne das Loitering-Ereignis erneut.',EVENT_PERIOD_INVALID:'Der Ereigniszeitraum ist ungültig.',COPERNICUS_CREDENTIALS_MISSING:'Die Copernicus-Zugangsdaten fehlen in den GitHub Secrets.',NO_SUITABLE_SCENE:'Im gewählten Zeitraum ist keine passende vollständige Aufnahme verfügbar.',DAILY_ANALYSIS_LIMIT:'Heute wurden bereits drei neue Bildauswertungen angefordert. Kontextprüfungen vorhandener Bilder bleiben möglich.',REQUEST_EXPIRED:'Der Auftrag ist älter als 24 Stunden. Erzeuge einen neuen Auftrag.',REQUEST_ALREADY_COMPLETED:'Dieser Auftrag wurde schon verarbeitet.',DANISH_ARCHIVE_UNAVAILABLE:'Das dänische Tagesarchiv ist noch nicht verfügbar.',LAND_MASK_CHECKSUM:'Die Landmasken-Datei hat sich geändert. Die Verarbeitung wurde zur Prüfung angehalten.',NO_OPEN_WATER_PIXELS:'Land und Küstenpuffer bedecken den gesamten Ausschnitt. Wähle ein Gebiet weiter auf See.',PilotError:'Copernicus-Antwort oder Bildqualität hat die Verarbeitungskontrolle nicht bestanden. Prüfe Zugangsdaten und versuche eine andere Aufnahme.',RASTER_GEOREFERENCE_MISMATCH:'Die Georeferenz des Radarbildes passt nicht zum angeforderten Ausschnitt. Das Bild wurde verworfen.'};

function element(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function status(id,text,error=false){$(id).textContent=text;$(id).classList.toggle('error',error);}
function fmtDate(value){const d=new Date(value);return Number.isFinite(+d)?new Intl.DateTimeFormat('de-DE',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'UTC'}).format(d)+' UTC':'Zeit unbekannt';}
function download(name,payload,type='application/json'){const url=URL.createObjectURL(new Blob([typeof payload==='string'?payload:JSON.stringify(payload,null,2)],{type}));const link=element('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function bbox(){return $('area').value==='custom'?['west','south','east','north'].map(id=>Number($(id).value)):state.config.areas.find(a=>a.id===$('area').value).bbox.slice();}
function applyEvent(){
  if(!state.event)return;
  $('event-context').hidden=false;$('event-description').textContent=`GFW-Loitering: ${state.event.name||state.event.mmsi||'Identität offen'} · ${fmtDate(state.event.start)} bis ${fmtDate(state.event.end)}. Suche im 10 × 10 km Ausschnitt um die Ereignis-Referenzposition; das ist nicht die gesamte beobachtete Fahrtfläche.`;
  $('area').value='custom';for(const [i,id] of ['west','south','east','north'].entries())$(id).value=state.event.bbox[i];$('days').disabled=true;updateArea();
}
function updateArea(){
  $('custom-bbox').hidden=$('area').value!=='custom';state.selected=null;$('analyse').disabled=true;
  $('selected-scene').textContent='Wähle zuerst eine passende Radaraufnahme.';
  try{const box=bbox(),info=inspectBbox(box,state.config);status('area-summary',`${info.area.toFixed(1)} km² · ${info.widthPx} × ${info.heightPx} Pixel bei 10 m`);const archiveAllowed=box[0]>=3&&box[2]<=17&&box[1]>=53&&box[3]<=60;$('danish-ais').disabled=!archiveAllowed;if(!archiveAllowed)$('danish-ais').checked=false;renderSearchMap(box);}
  catch(err){status('area-summary',err.message,true);}
  $('scene-list').replaceChildren();state.scenes=[];status('catalog-status','');
}

async function readJson(url){const r=await fetch(url,{cache:'no-store',referrerPolicy:'no-referrer'});if(r.status===404)return null;if(!r.ok)throw new Error('Die Daten konnten nicht geladen werden.');const length=Number(r.headers.get('Content-Length'));if(length>24*1024*1024)throw new Error('Die Datei überschreitet die zulässige Größe.');const text=await r.text();if(text.length>24*1024*1024)throw new Error('Die Datei ist zu groß.');return JSON.parse(text);}
async function getEnvelope(){
  try{const latest=await readJson(`${RAW}?t=${Date.now()}`);if(latest)return latest;}catch{}
  return readJson(`./data/sar/workbench.enc.json?t=${Date.now()}`);
}
async function addBasemap(map){
  try{const coast=await readJson('./assets/sar-basemap.geojson');if(state.map!==map)return;L.geoJSON(coast,{interactive:false,pane:'tilePane',style:{color:'#536776',weight:1,fillColor:'#263c49',fillOpacity:1}}).addTo(map);map.attributionControl.addAttribution('Natural Earth · Orientierung, keine Seekarte');}
  catch{status('workspace-status','Küstenübersicht nicht verfügbar. Radarbild und Kandidaten bleiben nutzbar.',true);}
}
function initMap(){
  if(state.map)return;
  if(!globalThis.L){status('workspace-status','Karte nicht verfügbar. Suche und Auswertung bleiben nutzbar.',true);return;}
  state.map=L.map('sar-map',{zoomControl:true}).setView([54.6,12.1],8);
  addBasemap(state.map);
  state.mapLayers=L.layerGroup().addTo(state.map);
}
function setBounds(box){state.bounds=box;if(state.map)state.map.fitBounds([[box[1],box[0]],[box[3],box[2]]],{padding:[20,20],maxZoom:12});}
function renderSearchMap(box){initMap();if(!state.map)return;state.mapLayers.clearLayers();L.rectangle([[box[1],box[0]],[box[3],box[2]]],{color:'#e5b66e',weight:2,fillOpacity:.07}).addTo(state.mapLayers);if(state.selected?.footprint)L.geoJSON(state.selected.footprint,{style:{color:'#75b5d6',weight:1,fillOpacity:.025}}).addTo(state.mapLayers);setBounds(box);$('map-caption').textContent='Untersuchungsgebiet · Auswahl aus dem Aufnahmenkatalog';}

async function unlock(event){
  event.preventDefault();const password=$('password').value;
  try{validatePassword(password);$('unlock-submit').disabled=true;status('unlock-status','Verschlüsselte Ablage wird geprüft …');const envelope=await getEnvelope();
    const workspace=envelope?await decryptObject(envelope,password,'workspace'):{v:1,jobs:[],analysis_ledger:{}};
    if(workspace.v!==1||!Array.isArray(workspace.jobs))throw new Error('Ungültige Sitzung.');
    state.password=password;state.workspace=workspace;$('password').value='';$('gate').hidden=true;$('workspace').hidden=false;$('lock').hidden=false;
    initMap();setTimeout(()=>state.map?.invalidateSize(),0);updateArea();renderJobs();applyEvent();
    status('workspace-status',envelope?`Ablage geöffnet · ${fmtDate(workspace.updated_at)}`:'Noch keine Sitzung geöffnet. Das Passwort wird beim ersten GitHub-Auftrag geprüft. Ergebnisse als verschlüsseltes GitHub-Artefakt herunterladen und über Sicherung öffnen laden.');
  }catch{status('unlock-status','Öffnen nicht möglich. Prüfe Passwort und Verbindung; gespeicherte Daten werden nicht verändert.',true);}
  finally{$('unlock-submit').disabled=false;}
}
function lock(){
  state.password='';state.workspace={v:1,jobs:[],analysis_ledger:{}};state.scenes=[];state.selected=null;state.currentJob=null;state.currentCandidate=null;
  state.map?.remove();state.map=null;state.mapLayers=null;state.bounds=null;
  for(const id of ['scene-list','candidate-rows','candidate-facts','result-metrics','job-select'])$(id).replaceChildren();
  for(const id of ['candidate-id','candidate-title','candidate-description','selected-scene','result-note','workspace-status','catalog-status','request-envelope','review-note']){const e=$(id);if('value'in e)e.value='';else e.textContent='';}
  $('candidate-chip').removeAttribute('src');$('dispatch-dialog').close();$('workspace').hidden=true;$('gate').hidden=false;$('lock').hidden=true;
  $('password').value='';status('unlock-status','Arbeitsplatz gesperrt.');$('password').focus();
}

function showScenes(){
  $('scene-list').replaceChildren();
  for(const scene of state.scenes){const button=element('button',undefined,'scene-choice');button.type='button';button.disabled=!scene.processable;button.setAttribute('aria-pressed',String(state.selected?.scene_id===scene.scene_id));
    button.append(element('strong',fmtDate(scene.datetime)),element('span',scene.gfw_delay_elapsed?'GFW-Zeitfenster':'frisch','scene-age'),element('small',`${scene.platform.toUpperCase()} · ${scene.orbit_direction==='ascending'?'aufsteigend':'absteigend'} · ${scene.processable?'Ausschnitt abgedeckt':'Ausschnitt nicht vollständig geeignet'}`));
    button.addEventListener('click',()=>{state.selected=scene;showScenes();$('selected-scene').textContent=`${fmtDate(scene.datetime)} · ${scene.platform.toUpperCase()} · VV / VH`;$('analyse').disabled=false;renderSearchMap(bbox());});
    $('scene-list').append(button);
  }
}
async function search(){
  $('search').disabled=true;status('catalog-status','Copernicus-Katalog wird durchsucht …');$('search-via-github').hidden=true;
  try{const box=bbox();inspectBbox(box,state.config);const end=state.event?new Date(state.event.end):new Date(),start=state.event?new Date(state.event.start):new Date(+end-Number($('days').value)*86400000);
    const url=new URL('https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd/items');url.search=new URLSearchParams({bbox:box.join(','),datetime:`${start.toISOString()}/${end.toISOString()}`,limit:'40'});
    const payload=await readJson(url);if(!Array.isArray(payload?.features))throw new Error('Der Katalog hat keine verwertbare Antwort geliefert.');
    state.scenes=payload.features.map(f=>normalizeScene(f,box)).filter(s=>s&&Date.parse(s.datetime)>=+start&&Date.parse(s.datetime)<=+end).sort((a,b)=>Date.parse(b.datetime)-Date.parse(a.datetime));
    state.scenes=[...new Map(state.scenes.map(s=>[s.scene_id,s])).values()];state.selected=null;$('analyse').disabled=true;showScenes();
    const suitable=state.scenes.filter(s=>s.processable).length,truncated=(payload.links||[]).some(l=>l.rel==='next');
    status('catalog-status',state.scenes.length?`${state.scenes.length} Aufnahmen · ${suitable} geeignet.${truncated?' Anzeige begrenzt; verkürze bei Bedarf den Zeitraum.':''}`:'Keine passende Aufnahme in diesem Zeitraum gefunden. Vergrößere den Zeitraum oder verschiebe das Gebiet.');
  }catch(err){status('catalog-status',`${err.message} Alternativ kannst du die Katalogsuche über GitHub anfordern.`,true);$('search-via-github').hidden=false;}
  finally{$('search').disabled=false;}
}

async function dispatch(action,previous=null){
  try{if(!state.password)throw new Error('Bitte den Arbeitsplatz zuerst öffnen.');const box=previous?previous.request.bbox:bbox();inspectBbox(box,state.config);
    if(action==='analyse'&&!state.selected)throw new Error('Wähle zuerst eine geeignete Aufnahme.');
    const request={v:1,id:crypto.randomUUID(),created_at:new Date().toISOString(),action,area_id:previous?previous.request.area_id:$('area').value,bbox:box,days:previous?previous.request.days:Number($('days').value),scene_id:previous?previous.scene.scene_id:state.selected?.scene_id||'latest',gfw_context:$('gfw-context').checked,danish_ais:$('danish-ais').checked};
    if(state.event&&!previous)Object.assign(request,{range_from:state.event.start,range_to:state.event.end,event_id:state.event.event_id});
    if(action==='refresh_context')request.gfw_context=true;
    $('request-envelope').value=JSON.stringify(await encryptObject(request,state.password,'request'));
    $('dispatch-title').textContent=action==='search'?'Katalogsuche bei GitHub starten':action==='refresh_context'?'Historischen AIS-Kontext erneut prüfen':'Diese Aufnahme bei GitHub auswerten';
    status('copy-status','');$('dispatch-dialog').showModal();
  }catch(err){status('workspace-status',err.message,true);}
}

function renderJobs(preferred){
  const select=$('job-select');select.replaceChildren();
  for(const job of state.workspace.jobs){const title=`${job.request?.area_name||'SAR-Auftrag'} · ${fmtDate(job.scene?.datetime||job.created_at)} · ${job.state==='ready'?'Auswertung':job.state==='catalog_ready'?'Katalog':'Fehler'}`;const option=element('option',title);option.value=job.request_id;select.append(option);}
  if(!select.options.length){const option=element('option','Noch keine Auswertung gespeichert');option.value='';select.append(option);}
  if(preferred&&[...select.options].some(o=>o.value===preferred))select.value=preferred;
  renderJob();
}
function renderJob(){
  const job=state.workspace.jobs.find(j=>j.request_id===$('job-select').value);state.currentJob=job||null;state.currentCandidate=null;
  $('candidate-rows').replaceChildren();$('result-metrics').replaceChildren();$('candidate-detail').hidden=true;$('candidate-chip').removeAttribute('src');$('candidate-review').hidden=true;$('context-refresh').hidden=true;$('download-geojson').disabled=true;
  $('empty-results').hidden=Boolean(job);status('result-note','');
  if(!job)return;
  if(job.is_fixture)status('result-note','Synthetischer Funktionstest · keine reale Satellitenauswertung.');
  if(job.state==='error'){status('result-note',ERRORS[job.error_code]||`Auftrag konnte nicht abgeschlossen werden (${job.error_code||'unbekannter Fehler'}). Vorherige Ergebnisse bleiben erhalten.`,true);return;}
  if(job.state==='catalog_ready'){
    $('area').value=job.request.area_id;for(const [i,id] of ['west','south','east','north'].entries())$(id).value=job.request.bbox[i];$('days').value=String(job.request.days);updateArea();state.scenes=job.catalog.scenes;state.selected=null;showScenes();status('result-note',`Katalogsuche abgeschlossen. ${job.catalog.scenes.length} Aufnahmen gefunden; wähle links eine Aufnahme.`);return;
  }
  const features=job.context?.features||[];if(!Array.isArray(features))return;
  $('context-refresh').hidden=false;$('download-geojson').disabled=false;
  const counts=job.context?.metadata?.match_counts||{};
  for(const [label,count] of [['Kandidaten',features.length],['Zuordnung plausibel',counts.MATCHED||0],['Mögliche Zuordnung',counts.POSSIBLE_MATCH||0],['AIS-Abdeckung fehlt',counts.NO_AIS_COVERAGE||0]]){const metric=element('div',undefined,'metric');metric.append(element('strong',String(count)),element('span',label));$('result-metrics').append(metric);}
  status('result-note',job.is_fixture?'Synthetischer Funktionstest · keine reale Satellitenauswertung.':`${fmtDate(job.scene.datetime)} · ${job.request.area_name} · ${features.length?'Alle Kandidaten benötigen deine Prüfung.':'Keine Kandidaten mit diesen Parametern gefunden. Das schließt Schiffe nicht aus.'}`);
  if(job.context?.metadata?.gfw_context_state==='pending_provider_delay')$('result-note').append(document.createTextNode(' GFW ist für diese Aufnahme noch nicht verfügbar.'));
  if(job.context?.metadata?.optional_context_error)$('result-note').append(document.createTextNode(' Eine ergänzende historische Quelle ist ausgefallen. Die Radar-Auswertung bleibt verfügbar; der AIS-Abgleich ist eingeschränkt.'));
  $('candidate-review').hidden=!features.length;
  for(const [i,feature] of features.entries()){
    const props=feature.properties||{},row=element('tr',undefined,'candidate-row');row.dataset.id=props.detection_id;const button=element('button',`Kandidat ${String(i+1).padStart(2,'0')}`);button.type='button';button.addEventListener('click',()=>selectCandidate(feature,i));
    const name=element('td');name.append(button);const tag=element('span',stateText(props.ais_match_status),`status-tag ${Object.hasOwn(STATES,props.ais_match_status)?props.ais_match_status:''}`);const statusCell=element('td');statusCell.append(tag);
    row.append(name,statusCell,element('td',`${props.detection_method?.profile_count||0}/3 Profile${props.detection_method?.vh_cfar_corroborated?' · VH bestätigt':' · nur VV'}`));row.addEventListener('click',()=>selectCandidate(feature,i));$('candidate-rows').append(row);
  }
  renderResultMap(job,features);
}

function renderResultMap(job,features){
  initMap();if(!state.map)return;state.mapLayers.clearLayers();const box=job.request.bbox;
  const preview=safeImage(job.images?.quicklook);if(preview)L.imageOverlay(preview,[[box[1],box[0]],[box[3],box[2]]],{opacity:.95}).addTo(state.mapLayers);
  L.rectangle([[box[1],box[0]],[box[3],box[2]]],{color:'#d9e4ea',weight:1,fillOpacity:0}).addTo(state.mapLayers);
  for(const [i,f] of features.entries()){
    const c=f.geometry?.coordinates,p=f.properties||{};if(!Array.isArray(c)||!c.every(Number.isFinite))continue;
    const color={MATCHED:'#8ac6ae',POSSIBLE_MATCH:'#e5b66e',AMBIGUOUS:'#c5a5dd',UNMATCHED_IN_AVAILABLE_AIS:'#f19786',NO_AIS_COVERAGE:'#75b5d6'}[p.ais_match_status]||'#dbe5ec';
    const marker=L.circleMarker([c[1],c[0]],{radius:7,color:'#0a1420',weight:2,fillColor:color,fillOpacity:1}).addTo(state.mapLayers);
    marker.bindTooltip(element('span',`Kandidat ${String(i+1).padStart(2,'0')} · ${stateText(p.ais_match_status)}`));marker.on('click',()=>selectCandidate(f,i));
  }
  setBounds(box);$('map-caption').textContent=`SAR-Vorschau · ${fmtDate(job.scene.datetime)} · ungeprüfte Kandidaten`;
}
function selectCandidate(feature,index){
  const props=feature.properties||{},job=state.currentJob;state.currentCandidate=feature;
  $('candidate-detail').hidden=false;$('candidate-id').textContent=props.detection_id;$('candidate-title').textContent=`Kandidat ${String(index+1).padStart(2,'0')}`;
  $('candidate-description').textContent=`${stateText(props.ais_match_status)}. Ein heller Reflex ist noch keine bestätigte Schiffsdetektion.`;
  $('candidate-facts').replaceChildren();const context=props.historical_ais_context||{},match=context.selected_match;
  const c=feature.geometry.coordinates;
  for(const [name,value] of [['Aufnahme',fmtDate(job.scene.datetime)],['Position',`${c[1].toFixed(5)}° N · ${c[0].toFixed(5)}° E`],['AIS-Identität',match?`${match.name||'Name unbekannt'} · MMSI ${match.mmsi}`:'Nicht belegt'],['Zeitabstand AIS',match?`${match.nearest_observation_gap_seconds} Sekunden`:'Keine geeignete Beobachtung'],['Zuordnungsqualität',match?.projection_quality||'Keine Zuordnung'],['Bewertung','Manuelle Prüfung erforderlich']]){$('candidate-facts').append(element('dt',name),element('dd',value));}
  const image=safeImage(job.images?.candidate_chips?.[props.detection_id]);if(image)$('candidate-chip').src=image;else $('candidate-chip').removeAttribute('src');
  const review=job.analyst_review?.[props.detection_id]||{};$('review-disposition').value=review.disposition||'unreviewed';$('review-note').value=review.note||'';
  const gfw=props.gfw_historical_context||{},presence={CONTEXT_AVAILABLE:'AIS-Präsenz im GFW-Raster vorhanden',NO_RELEVANT_CONTEXT_IN_AVAILABLE_GRID:'Kein Kontext im verfügbaren Raster',NOT_AVAILABLE:'Noch nicht verfügbar',PENDING_PROVIDER_DELAY:'Veröffentlichung noch ausstehend'};
  $('candidate-facts').append(element('dt','GFW-Kontext'),element('dd',`${presence[gfw.presence_state]||'Nicht geprüft'} · ${gfw.behavior_events_total||0} zeitlich passende Verhaltensereignisse. Rasterkontext bestätigt keine Identität.`));
  if(match)$('candidate-facts').append(element('dt','Abstand zur AIS-Projektion'),element('dd',`${match.distance_to_sar_candidate_m} m · Quelle: ${match.source_provider}`));
  for(const row of $('candidate-rows').children)row.classList.toggle('selected',row.dataset.id===props.detection_id);
}
function saveNote(){if(!state.currentJob||!state.currentCandidate)return;state.currentJob.analyst_review||={};state.currentJob.analyst_review[state.currentCandidate.properties.detection_id]={disposition:$('review-disposition').value,note:$('review-note').value.slice(0,2000),updated_at:new Date().toISOString()};}
$('unlock-form').addEventListener('submit',unlock);$('lock').addEventListener('click',lock);
$('show-password').addEventListener('click',()=>{const shown=$('password').type==='password';$('password').type=shown?'text':'password';$('show-password').textContent=shown?'Verbergen':'Anzeigen';});
$('area').addEventListener('change',updateArea);for(const id of ['west','south','east','north'])$(id).addEventListener('change',updateArea);
$('search').addEventListener('click',search);$('analyse').addEventListener('click',()=>dispatch('analyse'));$('search-via-github').addEventListener('click',()=>dispatch('search'));
$('context-refresh').addEventListener('click',()=>dispatch('refresh_context',state.currentJob));$('refresh').addEventListener('click',()=>{window.open('https://github.com/mowlsint/VoodooWhiskers/actions/workflows/sar-workbench.yml','_blank','noopener,noreferrer');status('workspace-status','Abgeschlossenen Lauf öffnen → Artefakt sar-workbench-encrypted herunterladen → hier Sicherung öffnen. Die ZIP-Datei kann direkt geöffnet werden.');});$('job-select').addEventListener('change',renderJob);
$('fit-map').addEventListener('click',()=>{if(state.bounds)setBounds(state.bounds);});$('review-disposition').addEventListener('change',saveNote);$('review-note').addEventListener('input',saveNote);
$('close-dispatch').addEventListener('click',()=>$('dispatch-dialog').close());$('copy-request').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('request-envelope').value);status('copy-status','Auftrag kopiert. Jetzt GitHub öffnen und in encrypted_request einfügen.');}catch{$('request-envelope').focus();$('request-envelope').select();status('copy-status','Bitte den markierten Auftrag mit Strg+C kopieren.');}});
$('download-geojson').addEventListener('click',()=>{if(state.currentJob?.context)download('sar-kandidaten.geojson',state.currentJob.context,'application/geo+json');});
$('export-session').addEventListener('click',async()=>{try{download('sar-sitzung-verschluesselt.json',await encryptObject(state.workspace,state.password,'workspace'));}catch{status('workspace-status','Sicherung konnte nicht erstellt werden.',true);}});
$('import-session').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>24*1024*1024)throw new Error();const data=await decryptObject(await readWorkspaceFile(file),state.password,'workspace');if(data.v!==1||!Array.isArray(data.jobs))throw new Error();for(const job of data.jobs){const local=state.workspace.jobs.find(j=>j.request_id===job.request_id)||state.workspace.jobs.find(j=>j.scene?.scene_id===job.scene?.scene_id&&JSON.stringify(j.request?.bbox)===JSON.stringify(job.request?.bbox));if(local?.analyst_review)job.analyst_review=local.analyst_review;}state.workspace=data;renderJobs();status('workspace-status','Verschlüsselte Sitzung geöffnet.');}catch{status('workspace-status','Sicherung konnte mit diesem Passwort nicht geöffnet werden.',true);}finally{e.target.value='';}});

try{state.config=await readJson('./assets/sar-workbench-config.json');for(const area of state.config.areas){const option=element('option',area.name);option.value=area.id;$('area').append(option);}const custom=element('option','Eigenen Ausschnitt wählen');custom.value='custom';$('area').append(custom);}catch{status('unlock-status','Konfiguration konnte nicht geladen werden. Bitte lade die Seite erneut.',true);$('unlock-submit').disabled=true;}

state.event=eventFromHash(location.hash,state.config);
$('clear-event').addEventListener('click',()=>{state.event=null;$('event-context').hidden=true;$('days').disabled=false;history.replaceState(null,'',location.pathname);});
