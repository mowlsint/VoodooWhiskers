import {encryptObject,decryptObject,validatePassword} from './sar-crypto.mjs?v=20261110-en';
import {inspectBbox,normalizeScene,safeImage,stateText,STATES,eventFromHash} from './sar-workbench-core.mjs?v=20261110-en';
import {readWorkspaceFile} from './sar-import.mjs?v=20261110-en';

const $=id=>document.getElementById(id),RAW='https://raw.githubusercontent.com/mowlsint/VoodooWhiskers/main/public/data/sar/workbench.enc.json';
const state={password:'',workspace:{v:1,jobs:[],analysis_ledger:{}},scenes:[],selected:null,currentJob:null,currentCandidate:null,map:null,mapLayers:null,bounds:null,config:null,event:null};
const ERRORS={EVENT_PERIOD_OUTSIDE_RETENTION:'The event period is outside the last fourteen days. Open the loitering event again.',EVENT_PERIOD_INVALID:'The event period is invalid.',COPERNICUS_CREDENTIALS_MISSING:'Copernicus credentials are missing from GitHub Secrets.',NO_SUITABLE_SCENE:'No suitable acquisition fully covers the area in the selected period.',DAILY_ANALYSIS_LIMIT:'Three new image analyses have already been requested today. Existing image context can still be checked.',REQUEST_EXPIRED:'The request is older than 24 hours. Create a new request.',REQUEST_ALREADY_COMPLETED:'This request has already been processed.',DANISH_ARCHIVE_UNAVAILABLE:'The Danish daily archive is not yet available.',LAND_MASK_CHECKSUM:'The land mask file has changed. Processing has stopped for review.',NO_OPEN_WATER_PIXELS:'Land and the coastal buffer cover the entire area. Choose an area farther offshore.',PilotError:'The Copernicus response or image quality failed validation. Check the credentials and try another acquisition.',RASTER_GEOREFERENCE_MISMATCH:'The radar image georeference does not match the requested area. The image was discarded.'};

function element(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function status(id,text,error=false){$(id).textContent=text;$(id).classList.toggle('error',error);}
function fmtDate(value){const d=new Date(value);return Number.isFinite(+d)?new Intl.DateTimeFormat('en-GB',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'UTC'}).format(d)+' UTC':'Time unknown';}
function download(name,payload,type='application/json'){const url=URL.createObjectURL(new Blob([typeof payload==='string'?payload:JSON.stringify(payload,null,2)],{type}));const link=element('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function bbox(){return $('area').value==='custom'?['west','south','east','north'].map(id=>Number($(id).value)):state.config.areas.find(a=>a.id===$('area').value).bbox.slice();}
function applyEvent(){
  if(!state.event)return;
  $('event-context').hidden=false;$('event-description').textContent=`GFW loitering: ${state.event.name||state.event.mmsi||'Identity unknown'} · ${fmtDate(state.event.start)} to ${fmtDate(state.event.end)}. Search the 10 × 10 km area around the event reference position; this is not the complete observed track area.`;
  $('area').value='custom';for(const [i,id] of ['west','south','east','north'].entries())$(id).value=state.event.bbox[i];$('days').disabled=true;updateArea();
}
function updateArea(){
  $('custom-bbox').hidden=$('area').value!=='custom';state.selected=null;$('analyse').disabled=true;
  $('selected-scene').textContent='Select a suitable radar acquisition first.';
  try{const box=bbox(),info=inspectBbox(box,state.config);status('area-summary',`${info.area.toFixed(1)} km² · ${info.widthPx} × ${info.heightPx} pixels at 10 m`);const archiveAllowed=box[0]>=3&&box[2]<=17&&box[1]>=53&&box[3]<=60;$('danish-ais').disabled=!archiveAllowed;if(!archiveAllowed)$('danish-ais').checked=false;renderSearchMap(box);}
  catch(err){status('area-summary',err.message,true);}
  $('scene-list').replaceChildren();state.scenes=[];status('catalog-status','');
}

async function readJson(url){const r=await fetch(url,{cache:'no-store',referrerPolicy:'no-referrer'});if(r.status===404)return null;if(!r.ok)throw new Error('The data could not be loaded.');const length=Number(r.headers.get('Content-Length'));if(length>24*1024*1024)throw new Error('The file exceeds the permitted size.');const text=await r.text();if(text.length>24*1024*1024)throw new Error('The file is too large.');return JSON.parse(text);}
async function getEnvelope(){
  try{const latest=await readJson(`${RAW}?t=${Date.now()}`);if(latest)return latest;}catch{}
  return readJson(`./data/sar/workbench.enc.json?t=${Date.now()}`);
}
async function addBasemap(map){
  try{const coast=await readJson('./assets/sar-basemap.geojson');if(state.map!==map)return;L.geoJSON(coast,{interactive:false,pane:'tilePane',style:{color:'#536776',weight:1,fillColor:'#263c49',fillOpacity:1}}).addTo(map);map.attributionControl.addAttribution('Natural Earth · orientation only, not a nautical chart');}
  catch{status('workspace-status','Coastline overview unavailable. The radar image and candidates remain usable.',true);}
}
function initMap(){
  if(state.map)return;
  if(!globalThis.L){status('workspace-status','Map unavailable. Search and analysis remain usable.',true);return;}
  state.map=L.map('sar-map',{zoomControl:true}).setView([54.6,12.1],8);
  addBasemap(state.map);
  state.mapLayers=L.layerGroup().addTo(state.map);
}
function setBounds(box){state.bounds=box;if(state.map)state.map.fitBounds([[box[1],box[0]],[box[3],box[2]]],{padding:[20,20],maxZoom:12});}
function renderSearchMap(box){initMap();if(!state.map)return;state.mapLayers.clearLayers();L.rectangle([[box[1],box[0]],[box[3],box[2]]],{color:'#e5b66e',weight:2,fillOpacity:.07}).addTo(state.mapLayers);if(state.selected?.footprint)L.geoJSON(state.selected.footprint,{style:{color:'#75b5d6',weight:1,fillOpacity:.025}}).addTo(state.mapLayers);setBounds(box);$('map-caption').textContent='Analysis area · acquisition catalog selection';}

async function unlock(event){
  event.preventDefault();const password=$('password').value;
  try{validatePassword(password);$('unlock-submit').disabled=true;status('unlock-status','Checking encrypted workspace …');const envelope=await getEnvelope();
    const workspace=envelope?await decryptObject(envelope,password,'workspace'):{v:1,jobs:[],analysis_ledger:{}};
    if(workspace.v!==1||!Array.isArray(workspace.jobs))throw new Error('Invalid session.');
    state.password=password;state.workspace=workspace;$('password').value='';$('gate').hidden=true;$('workspace').hidden=false;$('lock').hidden=false;
    initMap();setTimeout(()=>state.map?.invalidateSize(),0);updateArea();renderJobs();applyEvent();
    status('workspace-status',envelope?`Workspace opened · ${fmtDate(workspace.updated_at)}`:'No session has been opened yet. The first GitHub request verifies the password. Download the encrypted GitHub results artifact and load it using Open backup.');
  }catch{status('unlock-status','Unable to open. Check the password and connection; stored data are not changed.',true);}
  finally{$('unlock-submit').disabled=false;}
}
function lock(){
  state.password='';state.workspace={v:1,jobs:[],analysis_ledger:{}};state.scenes=[];state.selected=null;state.currentJob=null;state.currentCandidate=null;
  state.map?.remove();state.map=null;state.mapLayers=null;state.bounds=null;
  for(const id of ['scene-list','candidate-rows','candidate-facts','result-metrics','job-select'])$(id).replaceChildren();
  for(const id of ['candidate-id','candidate-title','candidate-description','selected-scene','result-note','workspace-status','catalog-status','request-envelope','review-note']){const e=$(id);if('value'in e)e.value='';else e.textContent='';}
  $('candidate-chip').removeAttribute('src');$('dispatch-dialog').close();$('workspace').hidden=true;$('gate').hidden=false;$('lock').hidden=true;
  $('password').value='';status('unlock-status','Workbench locked.');$('password').focus();
}

function showScenes(){
  $('scene-list').replaceChildren();
  for(const scene of state.scenes){const button=element('button',undefined,'scene-choice');button.type='button';button.disabled=!scene.processable;button.setAttribute('aria-pressed',String(state.selected?.scene_id===scene.scene_id));
    button.append(element('strong',fmtDate(scene.datetime)),element('span',scene.gfw_delay_elapsed?'GFW delay elapsed':'recent','scene-age'),element('small',`${scene.platform.toUpperCase()} · ${scene.orbit_direction==='ascending'?'ascending':'descending'} · ${scene.processable?'Area covered':'Area not fully suitable'}`));
    button.addEventListener('click',()=>{state.selected=scene;showScenes();$('selected-scene').textContent=`${fmtDate(scene.datetime)} · ${scene.platform.toUpperCase()} · VV / VH`;$('analyse').disabled=false;renderSearchMap(bbox());});
    $('scene-list').append(button);
  }
}
async function search(){
  $('search').disabled=true;status('catalog-status','Searching the Copernicus catalog …');$('search-via-github').hidden=true;
  try{const box=bbox();inspectBbox(box,state.config);const end=state.event?new Date(state.event.end):new Date(),start=state.event?new Date(state.event.start):new Date(+end-Number($('days').value)*86400000);
    const url=new URL('https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd/items');url.search=new URLSearchParams({bbox:box.join(','),datetime:`${start.toISOString()}/${end.toISOString()}`,limit:'40'});
    const payload=await readJson(url);if(!Array.isArray(payload?.features))throw new Error('The catalog did not return a usable response.');
    state.scenes=payload.features.map(f=>normalizeScene(f,box)).filter(s=>s&&Date.parse(s.datetime)>=+start&&Date.parse(s.datetime)<=+end).sort((a,b)=>Date.parse(b.datetime)-Date.parse(a.datetime));
    state.scenes=[...new Map(state.scenes.map(s=>[s.scene_id,s])).values()];state.selected=null;$('analyse').disabled=true;showScenes();
    const suitable=state.scenes.filter(s=>s.processable).length,truncated=(payload.links||[]).some(l=>l.rel==='next');
    status('catalog-status',state.scenes.length?`${state.scenes.length} acquisitions · ${suitable} suitable.${truncated?' Display limited; shorten the period if needed.':''}`:'No suitable acquisition found in this period. Extend the period or move the area.');
  }catch(err){status('catalog-status',`${err.message} Alternatively, request the catalog search through GitHub.`,true);$('search-via-github').hidden=false;}
  finally{$('search').disabled=false;}
}

async function dispatch(action,previous=null){
  try{if(!state.password)throw new Error('Open the workbench first.');const box=previous?previous.request.bbox:bbox();inspectBbox(box,state.config);
    if(action==='analyse'&&!state.selected)throw new Error('Select a suitable acquisition first.');
    const request={v:1,id:crypto.randomUUID(),created_at:new Date().toISOString(),action,area_id:previous?previous.request.area_id:$('area').value,bbox:box,days:previous?previous.request.days:Number($('days').value),scene_id:previous?previous.scene.scene_id:state.selected?.scene_id||'latest',gfw_context:$('gfw-context').checked,danish_ais:$('danish-ais').checked};
    if(state.event&&!previous)Object.assign(request,{range_from:state.event.start,range_to:state.event.end,event_id:state.event.event_id});
    if(action==='refresh_context')request.gfw_context=true;
    $('request-envelope').value=JSON.stringify(await encryptObject(request,state.password,'request'));
    $('dispatch-title').textContent=action==='search'?'Start catalog search on GitHub':action==='refresh_context'?'Recheck historical AIS context':'Analyze this acquisition on GitHub';
    status('copy-status','');$('dispatch-dialog').showModal();
  }catch(err){status('workspace-status',err.message,true);}
}

function areaLabel(request){if(request?.area_id==='custom')return 'Custom area';return state.config?.areas.find(area=>area.id===request?.area_id)?.name||request?.area_name||'Custom area';}
function renderJobs(preferred){
  const select=$('job-select');select.replaceChildren();
  for(const job of state.workspace.jobs){const title=`${areaLabel(job.request)} · ${fmtDate(job.scene?.datetime||job.created_at)} · ${job.state==='ready'?'Analysis':job.state==='catalog_ready'?'Catalog':'Error'}`;const option=element('option',title);option.value=job.request_id;select.append(option);}
  if(!select.options.length){const option=element('option','No saved analysis yet');option.value='';select.append(option);}
  if(preferred&&[...select.options].some(o=>o.value===preferred))select.value=preferred;
  renderJob();
}
function renderJob(){
  const job=state.workspace.jobs.find(j=>j.request_id===$('job-select').value);state.currentJob=job||null;state.currentCandidate=null;
  $('candidate-rows').replaceChildren();$('result-metrics').replaceChildren();$('candidate-detail').hidden=true;$('candidate-chip').removeAttribute('src');$('candidate-review').hidden=true;$('context-refresh').hidden=true;$('download-geojson').disabled=true;
  $('empty-results').hidden=Boolean(job);status('result-note','');
  if(!job)return;
  if(job.is_fixture)status('result-note','Synthetic functional test · not a real satellite analysis.');
  if(job.state==='error'){status('result-note',ERRORS[job.error_code]||`The request could not be completed (${job.error_code||'unknown error'}). Previous results are preserved.`,true);return;}
  if(job.state==='catalog_ready'){
    $('area').value=job.request.area_id;for(const [i,id] of ['west','south','east','north'].entries())$(id).value=job.request.bbox[i];$('days').value=String(job.request.days);updateArea();state.scenes=job.catalog.scenes;state.selected=null;showScenes();status('result-note',`Catalog search complete. ${job.catalog.scenes.length} acquisitions found; select one on the left.`);return;
  }
  const features=job.context?.features||[];if(!Array.isArray(features))return;
  $('context-refresh').hidden=false;$('download-geojson').disabled=false;
  const counts=job.context?.metadata?.match_counts||{};
  for(const [label,count] of [['Candidates',features.length],['Plausible association',counts.MATCHED||0],['Possible association',counts.POSSIBLE_MATCH||0],['No AIS coverage',counts.NO_AIS_COVERAGE||0]]){const metric=element('div',undefined,'metric');metric.append(element('strong',String(count)),element('span',label));$('result-metrics').append(metric);}
  status('result-note',job.is_fixture?'Synthetic functional test · not a real satellite analysis.':`${fmtDate(job.scene.datetime)} · ${areaLabel(job.request)} · ${features.length?'All candidates require your review.':'No candidates found with these parameters. This does not rule out vessels.'}`);
  if(job.context?.metadata?.gfw_context_state==='pending_provider_delay')$('result-note').append(document.createTextNode(' GFW is not yet available for this acquisition.'));
  if(job.context?.metadata?.optional_context_error)$('result-note').append(document.createTextNode(' A supplementary historical source failed. Radar analysis remains available; AIS comparison is limited.'));
  $('candidate-review').hidden=!features.length;
  for(const [i,feature] of features.entries()){
    const props=feature.properties||{},row=element('tr',undefined,'candidate-row');row.dataset.id=props.detection_id;const button=element('button',`Candidate ${String(i+1).padStart(2,'0')}`);button.type='button';button.addEventListener('click',()=>selectCandidate(feature,i));
    const name=element('td');name.append(button);const tag=element('span',stateText(props.ais_match_status),`status-tag ${Object.hasOwn(STATES,props.ais_match_status)?props.ais_match_status:''}`);const statusCell=element('td');statusCell.append(tag);
    row.append(name,statusCell,element('td',`${props.detection_method?.profile_count||0}/3 profiles${props.detection_method?.vh_cfar_corroborated?' · VH corroborated':' · VV only'}`));row.addEventListener('click',()=>selectCandidate(feature,i));$('candidate-rows').append(row);
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
    marker.bindTooltip(element('span',`Candidate ${String(i+1).padStart(2,'0')} · ${stateText(p.ais_match_status)}`));marker.on('click',()=>selectCandidate(f,i));
  }
  setBounds(box);$('map-caption').textContent=`SAR preview · ${fmtDate(job.scene.datetime)} · unreviewed candidates`;
}
function selectCandidate(feature,index){
  const props=feature.properties||{},job=state.currentJob;state.currentCandidate=feature;
  $('candidate-detail').hidden=false;$('candidate-id').textContent=props.detection_id;$('candidate-title').textContent=`Candidate ${String(index+1).padStart(2,'0')}`;
  $('candidate-description').textContent=`${stateText(props.ais_match_status)}. A bright return is not yet a confirmed vessel detection.`;
  $('candidate-facts').replaceChildren();const context=props.historical_ais_context||{},match=context.selected_match;
  const c=feature.geometry.coordinates;
  for(const [name,value] of [['Acquisition',fmtDate(job.scene.datetime)],['Position',`${c[1].toFixed(5)}° N · ${c[0].toFixed(5)}° E`],['AIS identity',match?`${match.name||'Name unknown'} · MMSI ${match.mmsi}`:'Not established'],['AIS time offset',match?`${match.nearest_observation_gap_seconds} seconds`:'No suitable observation'],['Association quality',match?.projection_quality||'No association'],['Assessment','Manual review required']]){$('candidate-facts').append(element('dt',name),element('dd',value));}
  const image=safeImage(job.images?.candidate_chips?.[props.detection_id]);if(image)$('candidate-chip').src=image;else $('candidate-chip').removeAttribute('src');
  const review=job.analyst_review?.[props.detection_id]||{};$('review-disposition').value=review.disposition||'unreviewed';$('review-note').value=review.note||'';
  const gfw=props.gfw_historical_context||{},presence={CONTEXT_AVAILABLE:'AIS presence in the GFW grid',NO_RELEVANT_CONTEXT_IN_AVAILABLE_GRID:'No context in the available grid',NOT_AVAILABLE:'Not yet available',PENDING_PROVIDER_DELAY:'Publication pending'};
  $('candidate-facts').append(element('dt','GFW context'),element('dd',`${presence[gfw.presence_state]||'Not reviewed'} · ${gfw.behavior_events_total||0} time-matched behavior events. Grid context does not confirm identity.`));
  if(match)$('candidate-facts').append(element('dt','Distance to AIS projection'),element('dd',`${match.distance_to_sar_candidate_m} m · Source: ${match.source_provider}`));
  for(const row of $('candidate-rows').children)row.classList.toggle('selected',row.dataset.id===props.detection_id);
}
function saveNote(){if(!state.currentJob||!state.currentCandidate)return;state.currentJob.analyst_review||={};state.currentJob.analyst_review[state.currentCandidate.properties.detection_id]={disposition:$('review-disposition').value,note:$('review-note').value.slice(0,2000),updated_at:new Date().toISOString()};}
$('unlock-form').addEventListener('submit',unlock);$('lock').addEventListener('click',lock);
$('show-password').addEventListener('click',()=>{const shown=$('password').type==='password';$('password').type=shown?'text':'password';$('show-password').textContent=shown?'Hide':'Show';$('show-password').setAttribute('aria-label',shown?'Hide password':'Show password');});
$('area').addEventListener('change',updateArea);for(const id of ['west','south','east','north'])$(id).addEventListener('change',updateArea);
$('search').addEventListener('click',search);$('analyse').addEventListener('click',()=>dispatch('analyse'));$('search-via-github').addEventListener('click',()=>dispatch('search'));
$('context-refresh').addEventListener('click',()=>dispatch('refresh_context',state.currentJob));$('refresh').addEventListener('click',()=>{window.open('https://github.com/mowlsint/VoodooWhiskers/actions/workflows/sar-workbench.yml','_blank','noopener,noreferrer');status('workspace-status','Open the completed run → download the sar-workbench-encrypted artifact → use Open backup here. The ZIP file can be opened directly.');});$('job-select').addEventListener('change',renderJob);
$('fit-map').addEventListener('click',()=>{if(state.bounds)setBounds(state.bounds);});$('review-disposition').addEventListener('change',saveNote);$('review-note').addEventListener('input',saveNote);
$('close-dispatch').addEventListener('click',()=>$('dispatch-dialog').close());$('copy-request').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('request-envelope').value);status('copy-status','Request copied. Open GitHub and paste it into encrypted_request.');}catch{$('request-envelope').focus();$('request-envelope').select();status('copy-status','Copy the selected request with Ctrl+C.');}});
$('download-geojson').addEventListener('click',()=>{if(state.currentJob?.context)download('sar-candidates.geojson',state.currentJob.context,'application/geo+json');});
$('export-session').addEventListener('click',async()=>{try{download('sar-session-encrypted.json',await encryptObject(state.workspace,state.password,'workspace'));}catch{status('workspace-status','The backup could not be created.',true);}});
$('import-session').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>24*1024*1024)throw new Error();const data=await decryptObject(await readWorkspaceFile(file),state.password,'workspace');if(data.v!==1||!Array.isArray(data.jobs))throw new Error();for(const job of data.jobs){const local=state.workspace.jobs.find(j=>j.request_id===job.request_id)||state.workspace.jobs.find(j=>j.scene?.scene_id===job.scene?.scene_id&&JSON.stringify(j.request?.bbox)===JSON.stringify(job.request?.bbox));if(local?.analyst_review)job.analyst_review=local.analyst_review;}state.workspace=data;renderJobs();status('workspace-status','Encrypted session opened.');}catch{status('workspace-status','The backup could not be opened with this password.',true);}finally{e.target.value='';}});

try{state.config=await readJson('./assets/sar-workbench-config.json');for(const area of state.config.areas){const option=element('option',area.name);option.value=area.id;$('area').append(option);}const custom=element('option','Choose a custom area');custom.value='custom';$('area').append(custom);}catch{status('unlock-status','Configuration could not be loaded. Reload the page.',true);$('unlock-submit').disabled=true;}

state.event=eventFromHash(location.hash,state.config);
$('clear-event').addEventListener('click',()=>{state.event=null;$('event-context').hidden=true;$('days').disabled=false;history.replaceState(null,'',location.pathname);});
