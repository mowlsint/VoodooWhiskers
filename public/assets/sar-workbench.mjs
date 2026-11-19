import {encryptObject,decryptObject,validatePassword} from './sar-crypto.mjs?v=20261110-en';
import {inspectBbox,bboxAroundPoint,normalizeScene,safeImage,stateText,STATES,eventFromHash,pointFromHash} from './sar-workbench-core.mjs?v=20261119-map-point-analysis';
import {readWorkspaceFile} from './sar-import.mjs?v=20261110-en';
import {createGitHubClient,WORKFLOW_URL} from './sar-github.mjs?v=20261111-direct';
import './satellite-images.js?v=20261119-simple-sar-load';

const $=id=>document.getElementById(id),RAW='https://raw.githubusercontent.com/mowlsint/VoodooWhiskers/main/public/data/sar/workbench.enc.json';
const state={password:'',workspace:{v:1,jobs:[],analysis_ledger:{}},scenes:[],selected:null,currentJob:null,currentCandidate:null,map:null,mapLayers:null,bounds:null,config:null,event:null,mapPoint:null,pendingRequest:null,trackedRequestId:null,autoSentRequestId:null,run:null,pollTimer:null,pollCount:0,pollGeneration:0,pickingArea:false,centeredPoint:null};
const github=createGitHubClient();
const imageSender=globalThis.SatelliteImages.createSender({onStatus:message=>status('main-map-status',message)});
const ERRORS={SCENE_PREVIEW_EXCEEDS_BOUNDS:'This Sentinel-1 scene is larger than the bounded map-image limit. Choose another acquisition or a nearer area.',SCENE_PREVIEW_PNG_TOO_LARGE:'The scene preview could not be reduced to the map-image size limit. Choose another acquisition.',SCENE_PREVIEW_NO_VALID_PIXELS:'The selected scene contains no usable image pixels for this overview.',SCENE_PREVIEW_STRETCH:'The selected scene could not be rendered as a contextual overview.',SCENE_PREVIEW_GEOREFERENCE_MISMATCH:'The scene image georeference did not match the selected footprint. The image was discarded.',EVENT_PERIOD_OUTSIDE_RETENTION:'The event period is outside the last fourteen days. Open the loitering event again.',EVENT_PERIOD_INVALID:'The event period is invalid.',COPERNICUS_CREDENTIALS_MISSING:'Copernicus credentials are missing from GitHub Secrets.',NO_SUITABLE_SCENE:'No suitable acquisition covers the selected point or, for detail analysis, the full outlined area.',DAILY_ANALYSIS_LIMIT:'Three new image-processing requests have already been made today. Existing image context can still be checked.',REQUEST_EXPIRED:'The request is older than 24 hours. Create a new request.',REQUEST_ALREADY_COMPLETED:'This request has already been processed.',DANISH_ARCHIVE_UNAVAILABLE:'The Danish daily archive is not yet available.',LAND_MASK_CHECKSUM:'The land mask file has changed. Processing has stopped for review.',NO_OPEN_WATER_PIXELS:'Land and the coastal buffer cover the entire area. Choose an area farther offshore.',PilotError:'The Copernicus response or image quality failed validation. Check the credentials and try another acquisition.',RASTER_GEOREFERENCE_MISMATCH:'The radar image georeference does not match the requested area. The image was discarded.'};

function element(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function status(id,text,error=false){$(id).textContent=text;$(id).classList.toggle('error',error);}
function fmtDate(value){const d=new Date(value);return Number.isFinite(+d)?new Intl.DateTimeFormat('en-GB',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'UTC'}).format(d)+' UTC':'Time unknown';}
function download(name,payload,type='application/json'){const url=URL.createObjectURL(new Blob([typeof payload==='string'?payload:JSON.stringify(payload,null,2)],{type}));const link=element('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function tileSize(){return Number($('tile-size').value)||20;}
function bbox(){
  if($('area').value==='custom'){
    if(state.centeredPoint)return bboxAroundPoint(state.centeredPoint.lat,state.centeredPoint.lon,state.config,tileSize());
    return ['west','south','east','north'].map(id=>Number($(id).value));
  }
  return state.config.areas.find(a=>a.id===$('area').value).bbox.slice();
}
function applyPoint(lat,lon){
  if(state.event)throw new Error('Leave the loitering event time window before choosing another area.');
  const box=bboxAroundPoint(lat,lon,state.config,tileSize());
  state.centeredPoint={lat,lon};$('area').value='custom';
  $('point-coordinates').value=`${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  for(const [i,id] of ['west','south','east','north'].entries())$(id).value=box[i];
  updateArea();
}
function useEnteredPoint(){
  const parts=$('point-coordinates').value.trim().split(/[\s,;]+/).filter(Boolean);
  if(parts.length!==2||parts.some(value=>!Number.isFinite(Number(value)))){
    status('area-summary','Enter latitude, longitude in decimal degrees, for example 54.50000, 12.30000.',true);return;
  }
  try{applyPoint(Number(parts[0]),Number(parts[1]));}
  catch(err){status('area-summary',err.message,true);}
}
function formatCoordinate(value){return Number(value).toFixed(6);}

function applyEvent(){
  if(!state.event)return;
  $('event-context').hidden=false;$('point-coordinates').value='';$('event-description').textContent=`GFW loitering: ${state.event.name||state.event.mmsi||'Identity unknown'} · ${fmtDate(state.event.start)} to ${fmtDate(state.event.end)}. Search the 10 × 10 km area around the event reference position; this is not the complete observed track area.`;
  state.centeredPoint=null;$('area').value='custom';for(const [i,id] of ['west','south','east','north'].entries())$(id).value=state.event.bbox[i];$('days').disabled=true;updateArea();
}
function applyMapPoint(){
  if(!state.mapPoint)return;
  const p=state.mapPoint,identity=[p.mmsi?'MMSI '+p.mmsi:'',p.imo?'IMO '+p.imo:''].filter(Boolean).join(' · ');
  $('map-point-context').hidden=false;
  $('map-point-description').textContent=`Selected location ${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}${identity?' · '+identity:''}${p.observed_at?' · AIS point observed '+fmtDate(p.observed_at):' · no AIS observation time supplied'}.`;
  if(p.observed_at){$('days-label').textContent='Acquisition window around the AIS point';$('days-hint').textContent='The selected window is centered on the AIS observation and limited to the latest 14 days. The exact Sentinel acquisition time determines the radar analysis.';}
  applyPoint(p.lat,p.lon);
}
function catalogWindow(){
  if(state.event)return {start:new Date(state.event.start),end:new Date(state.event.end)};
  const now=Date.now(),span=Number($('days').value)*86400000,anchor=Date.parse(state.mapPoint?.observed_at||'');
  if(!Number.isFinite(anchor)){const end=new Date(now);return {start:new Date(now-span),end};}
  return {start:new Date(Math.max(anchor-span/2,now-14*86400000)),end:new Date(Math.min(anchor+span/2,now))};
}
function updateArea(){
  cancelMapPick();
  $('custom-bbox').hidden=$('area').value!=='custom';state.selected=null;$('analyse').disabled=true;$('preview-scene').disabled=true;
  $('selected-scene').textContent='Select a suitable radar acquisition first.';
  try{
    const box=bbox(),info=inspectBbox(box,state.config),[west,south,east,north]=box;
    if(state.centeredPoint&&$('area').value==='custom')for(const [i,id] of ['west','south','east','north'].entries())$(id).value=box[i];
    const widthKm=(east-west)*111.32*Math.cos((south+north)/2*Math.PI/180),heightKm=(north-south)*111.32;
    const center=state.centeredPoint&&$('area').value==='custom'?`Center ${state.centeredPoint.lat.toFixed(5)}, ${state.centeredPoint.lon.toFixed(5)} · `:'';
    const bounds=`W/S/E/N ${formatCoordinate(west)}, ${formatCoordinate(south)}, ${formatCoordinate(east)}, ${formatCoordinate(north)}`;
    status('area-summary',`${center}${widthKm.toFixed(2)} × ${heightKm.toFixed(2)} km · ${info.widthPx} × ${info.heightPx} pixels at 10 m · ${bounds}`);
    $('copy-tile-bounds').disabled=false;const archiveAllowed=west>=3&&east<=17&&south>=53&&north<=60;$('danish-ais').disabled=!archiveAllowed;if(!archiveAllowed)$('danish-ais').checked=false;renderSearchMap(box);
  }
  catch(err){status('area-summary',err.message,true);$('copy-tile-bounds').disabled=true;}
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
  state.map.on('click',event=>{if(!state.pickingArea)return;try{applyPoint(event.latlng.lat,event.latlng.lng);}catch(err){status('area-summary',err.message,true);}});
  addBasemap(state.map);
  state.mapLayers=L.layerGroup().addTo(state.map);
}
function setBounds(box){state.bounds=box;if(state.map)state.map.fitBounds([[box[1],box[0]],[box[3],box[2]]],{padding:[20,20],maxZoom:12});}
function renderSearchMap(box){initMap();if(!state.map)return;state.mapLayers.clearLayers();L.rectangle([[box[1],box[0]],[box[3],box[2]]],{color:'#e5b66e',weight:2,fillOpacity:.05}).addTo(state.mapLayers);L.circleMarker([(box[1]+box[3])/2,(box[0]+box[2])/2],{radius:5,color:'#0a1420',weight:2,fillColor:'#e5b66e',fillOpacity:1}).bindTooltip('Selected search location').addTo(state.mapLayers);if(state.selected?.footprint){L.geoJSON(state.selected.footprint,{style:{color:'#75b5d6',weight:2,fillOpacity:.05}}).addTo(state.mapLayers);setBounds(state.selected.bbox||box);$('map-caption').textContent='Selected Sentinel-1 footprint · point marked in gold';}else{setBounds(box);$('map-caption').textContent='Selected search location · small detail area outlined';}}
function cancelMapPick(){state.pickingArea=false;$('pick-area').setAttribute('aria-pressed','false');state.map?.getContainer().classList.remove('picking-area');}
function pickArea(){
  if(state.event){status('area-summary','Leave the loitering event time window before choosing another area.',true);return;}
  initMap();if(!state.map)return;
  if(state.pickingArea){cancelMapPick();return;}
  state.pickingArea=true;$('pick-area').setAttribute('aria-pressed','true');state.map.getContainer().classList.add('picking-area');
  state.map.fitBounds([[48,-25],[80,35]]);status('area-summary',`Click the centre of the ${tileSize()} × ${tileSize()} km detail tile. The outline will show its full bounds.`);
  $('sar-map').scrollIntoView({block:'center',behavior:'smooth'});
}

async function unlock(event){
  event.preventDefault();const password=$('password').value;
  try{validatePassword(password);$('unlock-submit').disabled=true;status('unlock-status','Checking encrypted workspace …');const envelope=await getEnvelope();
    const workspace=envelope?await decryptObject(envelope,password,'workspace'):{v:1,jobs:[],analysis_ledger:{}};
    if(workspace.v!==1||!Array.isArray(workspace.jobs))throw new Error('Invalid session.');
    state.password=password;state.workspace=workspace;$('password').value='';$('gate').hidden=true;$('workspace').hidden=false;$('lock').hidden=false;
    initMap();setTimeout(()=>state.map?.invalidateSize(),0);updateArea();renderJobs();applyEvent();applyMapPoint();
    status('workspace-status',envelope?`Workspace opened · ${fmtDate(workspace.updated_at)}`:'No results loaded. Existing results are saved as encrypted GitHub artifacts: use Latest results, then open the downloaded ZIP. The first processed request verifies this password.');
  }catch{status('unlock-status','Unable to open. Check the password and connection; stored data are not changed.',true);}
  finally{$('unlock-submit').disabled=false;}
}
function lock(){
  imageSender.cancel();$('show-on-main-map').disabled=true;$('new-main-map').hidden=true;status('main-map-status','');
  stopPolling();github.disconnect();$('github-token').value='';$('github-connect').disabled=false;$('github-disconnect').hidden=true;$('latest-results').disabled=true;$('direct-start').disabled=true;$('github-run-panel').hidden=true;$('github-artifact-link').removeAttribute('href');$('github-run-link').removeAttribute('href');state.pendingRequest=null;state.trackedRequestId=null;state.run=null;cancelMapPick();status('github-status','Not connected. Manual GitHub steps remain available.');
  state.password='';state.workspace={v:1,jobs:[],analysis_ledger:{}};state.autoSentRequestId=null;state.scenes=[];state.selected=null;state.currentJob=null;state.currentCandidate=null;
  state.map?.remove();state.map=null;state.mapLayers=null;state.bounds=null;
  for(const id of ['scene-list','candidate-rows','candidate-facts','result-metrics','job-select'])$(id).replaceChildren();
  for(const id of ['candidate-id','candidate-title','candidate-description','selected-scene','result-note','workspace-status','catalog-status','request-envelope','review-note','dispatch-summary','copy-status','github-run-status']){const e=$(id);if('value'in e)e.value='';else e.textContent='';}
  $('area').value=state.config.areas[0].id;for(const [i,id]of ['west','south','east','north'].entries())$(id).value=state.config.areas[0].bbox[i];$('days').value='14';
  $('candidate-chip').removeAttribute('src');$('dispatch-dialog').close();$('workspace').hidden=true;$('gate').hidden=false;$('lock').hidden=true;
  $('password').value='';status('unlock-status','Workbench locked.');$('password').focus();
}

function showScenes(){
  $('scene-list').replaceChildren();
  for(const scene of state.scenes){const button=element('button',undefined,'scene-choice');button.type='button';button.disabled=!scene.previewable;button.setAttribute('aria-pressed',String(state.selected?.scene_id===scene.scene_id));
    button.append(element('strong',fmtDate(scene.datetime)),element('span',scene.gfw_delay_elapsed?'GFW delay elapsed':'recent','scene-age'),element('small',`${scene.platform.toUpperCase()} · ${scene.orbit_direction==='ascending'?'ascending':'descending'} · ${scene.previewable?'Covers selected point':'Selected point not covered'} · ${scene.processable?'small area also covered':'small area extends outside scene'}`));
    button.addEventListener('click',()=>{state.selected=scene;showScenes();$('selected-scene').textContent=`${fmtDate(scene.datetime)} · ${scene.platform.toUpperCase()} · clicked point covered · scene footprint outlined on map`;$('preview-scene').disabled=false;$('analyse').disabled=!scene.processable;renderSearchMap(bbox());});
    $('scene-list').append(button);
  }
}
async function search(){
  $('search').disabled=true;status('catalog-status','Searching the Copernicus catalog …');$('search-via-github').hidden=true;
  try{const box=bbox();inspectBbox(box,state.config);const {start,end}=catalogWindow();
    const url=new URL('https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd/items');url.search=new URLSearchParams({bbox:box.join(','),datetime:`${start.toISOString()}/${end.toISOString()}`,limit:'40'});
    const payload=await readJson(url);if(!Array.isArray(payload?.features))throw new Error('The catalog did not return a usable response.');
    state.scenes=payload.features.map(f=>normalizeScene(f,box)).filter(s=>s&&Date.parse(s.datetime)>=+start&&Date.parse(s.datetime)<=+end).sort((a,b)=>Date.parse(b.datetime)-Date.parse(a.datetime));
    state.scenes=[...new Map(state.scenes.map(s=>[s.scene_id,s])).values()];state.selected=null;$('analyse').disabled=true;showScenes();
    const suitable=state.scenes.filter(s=>s.previewable).length,truncated=(payload.links||[]).some(l=>l.rel==='next');
    status('catalog-status',state.scenes.length?`${state.scenes.length} acquisitions intersect the selected area · ${suitable} cover the clicked point.${truncated?' Display limited; shorten the period if needed.':''}`:'No acquisition covers the selected point in this period. Extend the period or move the point.');
  }catch(err){status('catalog-status',`${err.message} Alternatively, request the catalog search through GitHub.`,true);$('search-via-github').hidden=false;}
  finally{$('search').disabled=false;}
}

async function dispatch(action,previous=null){
  try{if(!state.password)throw new Error('Open the workbench first.');if(state.run&&state.run.status!=='completed')throw new Error('A request is already being tracked. Check its status before starting another.');const box=previous?previous.request.bbox:bbox();inspectBbox(box,state.config);
    if(['analyse','scene_preview'].includes(action)&&!state.selected)throw new Error('Select an acquisition that covers the clicked point first.');
    if(action==='analyse'&&!state.selected.processable)throw new Error('This acquisition covers the point but not the whole small detail area. Choose a scene that covers the area, or prepare its full-scene map image.');
    if(action==='scene_preview'&&!state.selected.previewable)throw new Error('The selected acquisition does not cover the clicked point.');
    const request={v:1,id:crypto.randomUUID(),created_at:new Date().toISOString(),action,area_id:previous?previous.request.area_id:$('area').value,bbox:box,days:previous?previous.request.days:Number($('days').value),scene_id:previous?previous.scene.scene_id:state.selected?.scene_id||'latest',gfw_context:action==='analyse'&&$('gfw-context').checked,danish_ais:action==='analyse'&&$('danish-ais').checked};
    if(state.event&&!previous)Object.assign(request,{range_from:state.event.start,range_to:state.event.end,event_id:state.event.event_id});
    if(state.mapPoint&&!previous){request.point_reference={lat:state.mapPoint.lat,lon:state.mapPoint.lon,...(state.mapPoint.observed_at?{observed_at:state.mapPoint.observed_at}:{}),...(state.mapPoint.mmsi?{mmsi:state.mapPoint.mmsi}:{}),...(state.mapPoint.imo?{imo:state.mapPoint.imo}:{})};if(state.mapPoint.observed_at){const window=catalogWindow();request.range_from=window.start.toISOString();request.range_to=window.end.toISOString();}}
    if(action==='refresh_context')request.gfw_context=true;
    const generation=state.pollGeneration,envelope=await encryptObject(request,state.password,'request');if(!state.password||generation!==state.pollGeneration)return;
    $('request-envelope').value=JSON.stringify(envelope);
    state.pendingRequest=request;$('direct-start').disabled=!github.connected;$('dispatch-summary').textContent=`${areaLabel(request)} · ${box.map(n=>n.toFixed(4)).join(', ')} · ${action==='search'?'catalog metadata only':action==='refresh_context'?'saved candidates; no new radar image':action==='scene_preview'?`${fmtDate(state.selected.datetime)} · bounded full-scene map overview`:fmtDate(state.selected.datetime)}`;
    $('dispatch-title').textContent=action==='search'?'Start catalog search on GitHub':action==='refresh_context'?'Recheck historical AIS context':action==='scene_preview'?'Prepare this Sentinel-1 scene map image':'Analyze this acquisition on GitHub';
    status('copy-status',github.connected?'Ready. Start workflow sends this encrypted request to GitHub.':'Nothing has started. Connect GitHub above for direct start, or follow the manual steps below.');$('dispatch-dialog').showModal();
  }catch(err){status('workspace-status',err.message,true);}
}

function stopPolling(){clearTimeout(state.pollTimer);state.pollTimer=null;state.pollGeneration++;}
async function connectGitHub(){
  const value=$('github-token').value;$('github-token').value='';$('github-connect').disabled=true;$('github-disconnect').hidden=true;$('latest-results').disabled=true;$('direct-start').disabled=true;status('github-status','Checking repository owner and workflow access …');
  try{await github.connect(value);if(!state.password){github.disconnect();return;}$('github-disconnect').hidden=false;$('latest-results').disabled=false;$('direct-start').disabled=!state.pendingRequest;status('github-status','Connected for this tab. Direct start is available. Lock or reload disconnects GitHub.');}
  catch(err){status('github-status',err.message,true);}finally{$('github-connect').disabled=false;}
}
async function showRun(run,generation){
  if(generation!==state.pollGeneration||!state.password)return;
  state.run=run;$('github-run-panel').hidden=false;$('github-run-link').href=`https://github.com/mowlsint/VoodooWhiskers/actions/runs/${run.id}`;
  status('github-run-status',run.status==='completed'?`GitHub run finished: ${run.conclusion}. Loading the result download link …`:`GitHub run ${run.status.replaceAll('_',' ')}. You may keep this tab open; no additional request is started.`);
  if(run.status!=='completed')return;
  $('latest-results').disabled=!github.connected;
  const link=await github.artifactLink(run.id);if(generation!==state.pollGeneration||!state.password)return;
  $('github-artifact-link').hidden=!link;if(link)$('github-artifact-link').href=link;
  status('github-run-status',link?`Run ${run.conclusion}. Download the encrypted results ZIP, then choose Open results ZIP here. Results do not load automatically from GitHub.`:`Run ${run.conclusion}. No unexpired result artifact was found in this run. Open the run for details.`,run.conclusion!=='success');
}
async function pollRun(requestId,generation){
  if(generation!==state.pollGeneration||!state.password||!github.connected)return;
  try{const run=state.run?.id?await github.run(state.run.id):await github.findRun(requestId);if(generation!==state.pollGeneration)return;
    if(run){await showRun(run,generation);if(run.status==='completed')return;}
    else status('github-run-status','GitHub accepted the request. Waiting for its run to appear …');
    if(++state.pollCount<80)state.pollTimer=setTimeout(()=>pollRun(requestId,generation),15000);
    else status('github-run-status','Status checking paused after 20 minutes. Open the run on GitHub; no request is repeated.');
  }catch(err){if(generation===state.pollGeneration)status('github-run-status',err.message+' Open the workflow to check its status.',true);}
}
async function startDirect(){
  if(!state.pendingRequest||!github.connected)return;
  const request=state.pendingRequest,envelope=$('request-envelope').value;$('direct-start').disabled=true;
  status('copy-status','Sending the encrypted request …');
  try{await github.dispatch(envelope,request.id);if(!state.password)return;
    state.pendingRequest=null;state.trackedRequestId=request.id;stopPolling();state.pollCount=0;state.run={status:'requested'};$('latest-results').disabled=true;$('github-artifact-link').hidden=true;$('github-run-link').href=WORKFLOW_URL;$('github-run-panel').hidden=false;
    $('dispatch-dialog').close();status('github-run-status','GitHub accepted the request. Waiting for its run …');pollRun(request.id,state.pollGeneration);
  }catch(err){if(state.password)status('copy-status',err.message,true);}
}
async function latestResults(){
  if(state.run&&state.run.status!=='completed'){status('github-status','A request is still being tracked. Open its run before loading older results.');return;}
  $('latest-results').disabled=true;stopPolling();const generation=state.pollGeneration;
  try{const run=await github.latestRun();if(!run){status('github-status','No completed SAR run was found.');return;}await showRun(run,generation);}
  catch(err){if(state.password)status('github-status',err.message,true);}finally{$('latest-results').disabled=!github.connected;}
}

function areaLabel(request){if(request?.area_id==='custom')return 'Custom area';return state.config?.areas.find(area=>area.id===request?.area_id)?.name||request?.area_name||'Custom area';}
function renderJobs(preferred){
  const select=$('job-select');select.replaceChildren();
  for(const job of state.workspace.jobs){const kind=job.scene_preview_only?'Full-scene map image':job.state==='ready'?'Detail analysis':job.state==='catalog_ready'?'Catalog':'Error';const title=`${areaLabel(job.request)} · ${fmtDate(job.scene?.datetime||job.created_at)} · ${kind}`;const option=element('option',title);option.value=job.request_id;select.append(option);}
  if(!select.options.length){const option=element('option','No saved analysis yet');option.value='';select.append(option);}
  if(preferred&&[...select.options].some(o=>o.value===preferred))select.value=preferred;
  renderJob();
}
function renderJob(){
  imageSender.cancel();$('show-on-main-map').disabled=true;$('new-main-map').hidden=true;status('main-map-status','');
  const job=state.workspace.jobs.find(j=>j.request_id===$('job-select').value);state.currentJob=job||null;state.currentCandidate=null;
  $('candidate-rows').replaceChildren();$('result-metrics').replaceChildren();$('candidate-detail').hidden=true;$('candidate-chip').removeAttribute('src');$('candidate-review').hidden=true;$('context-refresh').hidden=true;$('download-geojson').disabled=true;
  $('empty-results').hidden=Boolean(job);status('result-note','');
  if(!job)return;
  if(job.is_fixture)status('result-note','Synthetic functional test · not a real satellite analysis.');
  if(job.state==='error'){status('result-note',ERRORS[job.error_code]||`The request could not be completed (${job.error_code||'unknown error'}). Previous results are preserved.`,true);return;}
  if(job.state==='catalog_ready'){
    $('area').value=job.request.area_id;for(const [i,id] of ['west','south','east','north'].entries())$(id).value=job.request.bbox[i];$('days').value=String(job.request.days);updateArea();state.scenes=job.catalog.scenes;state.selected=null;showScenes();status('result-note',`Catalog search complete. ${job.catalog.scenes.length} acquisitions found; select one on the left.`);return;
  }
  if(job.scene_preview_only){
    status('result-note',`${fmtDate(job.scene.datetime)} · full-scene contextual overview · about ${Math.round(job.preview_meters_per_pixel)} m/pixel. This image shows acquisition coverage and broad surface patterns; it is not vessel-scale detail.`);
    renderResultMap(job,[]);try{globalThis.SatelliteImages.normalizeImage(job);$('show-on-main-map').disabled=false;
      if((job.request_id===state.trackedRequestId||job.request_id===state.pendingRequest?.id)&&state.autoSentRequestId!==job.request_id){state.autoSentRequestId=job.request_id;imageSender.send(job);$('new-main-map').hidden=false;}
    }catch(err){if(job.request_id===state.trackedRequestId||job.request_id===state.pendingRequest?.id)status('main-map-status',`The preview is ready here, but automatic map transfer failed: ${err.message}`,true);}return;
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
  try{globalThis.SatelliteImages.normalizeImage(job);$('show-on-main-map').disabled=false;}catch{}
}

function renderResultMap(job,features){
  initMap();if(!state.map)return;state.mapLayers.clearLayers();const overview=job.scene_preview_only===true,box=overview?job.scene_preview_bbox:job.request.bbox;
  const preview=safeImage(overview?job.images?.scene_preview:job.images?.quicklook);if(preview)L.imageOverlay(preview,[[box[1],box[0]],[box[3],box[2]]],{opacity:.95}).addTo(state.mapLayers);
  if(overview&&job.scene_footprint)L.geoJSON(job.scene_footprint,{style:{color:'#d9e4ea',weight:1,fillOpacity:0}}).addTo(state.mapLayers);
  else L.rectangle([[box[1],box[0]],[box[3],box[2]]],{color:'#d9e4ea',weight:1,fillOpacity:0}).addTo(state.mapLayers);
  for(const [i,f] of features.entries()){
    const c=f.geometry?.coordinates,p=f.properties||{};if(!Array.isArray(c)||!c.every(Number.isFinite))continue;
    const color={MATCHED:'#8ac6ae',POSSIBLE_MATCH:'#e5b66e',AMBIGUOUS:'#c5a5dd',UNMATCHED_IN_AVAILABLE_AIS:'#f19786',NO_AIS_COVERAGE:'#75b5d6'}[p.ais_match_status]||'#dbe5ec';
    const marker=L.circleMarker([c[1],c[0]],{radius:7,color:'#0a1420',weight:2,fillColor:color,fillOpacity:1}).addTo(state.mapLayers);
    marker.bindTooltip(element('span',`Candidate ${String(i+1).padStart(2,'0')} · ${stateText(p.ais_match_status)}`));marker.on('click',()=>selectCandidate(f,i));
  }
  setBounds(box);$('map-caption').textContent=overview?`Sentinel-1 full-scene overview · ${fmtDate(job.scene.datetime)} · contextual, not vessel-scale detail`:`SAR preview · ${fmtDate(job.scene.datetime)} · unreviewed candidates`;
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
$('pick-area').addEventListener('click',pickArea);document.addEventListener('keydown',event=>{if(event.key==='Escape')cancelMapPick();});
$('github-connect').addEventListener('click',connectGitHub);$('direct-start').addEventListener('click',startDirect);$('latest-results').addEventListener('click',latestResults);$('open-run-result').addEventListener('click',()=>$('import-session').click());
$('check-run-status').addEventListener('click',()=>{if(!github.connected){status('github-run-status','Connect GitHub for this tab, or open the run to check it.',true);return;}if(!state.trackedRequestId){latestResults();return;}stopPolling();state.pollCount=0;pollRun(state.trackedRequestId,state.pollGeneration);});
$('github-disconnect').addEventListener('click',()=>{stopPolling();github.disconnect();$('github-token').value='';$('github-disconnect').hidden=true;$('latest-results').disabled=true;$('direct-start').disabled=true;status('github-status','GitHub disconnected. Any already started run continues; open GitHub to check it.');});
$('show-password').addEventListener('click',()=>{const shown=$('password').type==='password';$('password').type=shown?'text':'password';$('show-password').textContent=shown?'Hide':'Show';$('show-password').setAttribute('aria-label',shown?'Hide password':'Show password');});
$('area').addEventListener('change',updateArea);
$('tile-size').addEventListener('change',()=>{if(state.centeredPoint&&$('area').value==='custom')updateArea();});
$('point-coordinates').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();useEnteredPoint();}});
$('use-coordinates').addEventListener('click',useEnteredPoint);
$('copy-tile-bounds').addEventListener('click',async()=>{
  try{const [west,south,east,north]=bbox();await navigator.clipboard.writeText([west,south,east,north].map(formatCoordinate).join(', '));status('tile-copy-status','Copied west, south, east, north bounds.');}
  catch{status('tile-copy-status','Copy unavailable. Read the W/S/E/N bounds above.',true);}
});
for(const id of ['west','south','east','north'])$(id).addEventListener('change',()=>{state.centeredPoint=null;updateArea();});
$('search').addEventListener('click',search);$('preview-scene').addEventListener('click',()=>dispatch('scene_preview'));$('analyse').addEventListener('click',()=>dispatch('analyse'));$('search-via-github').addEventListener('click',()=>dispatch('search'));
$('context-refresh').addEventListener('click',()=>dispatch('refresh_context',state.currentJob));$('refresh').addEventListener('click',()=>{window.open('https://github.com/mowlsint/VoodooWhiskers/actions/workflows/sar-workbench.yml','_blank','noopener,noreferrer');status('workspace-status','Open the completed run → download the sar-workbench-encrypted artifact → use Open backup here. The ZIP file can be opened directly.');});$('job-select').addEventListener('change',renderJob);
$('fit-map').addEventListener('click',()=>{if(state.bounds)setBounds(state.bounds);});$('review-disposition').addEventListener('change',saveNote);$('review-note').addEventListener('input',saveNote);
$('close-dispatch').addEventListener('click',()=>$('dispatch-dialog').close());$('copy-request').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('request-envelope').value);status('copy-status','Request copied. Open GitHub and paste it into encrypted_request.');}catch{$('request-envelope').focus();$('request-envelope').select();status('copy-status','Copy the selected request with Ctrl+C.');}});
$('download-geojson').addEventListener('click',()=>{if(state.currentJob?.context)download('sar-candidates.geojson',state.currentJob.context,'application/geo+json');});
$('show-on-main-map').addEventListener('click',()=>{try{imageSender.send(state.currentJob);$('new-main-map').hidden=false;}catch(err){status('main-map-status',err.message,true);}});
$('new-main-map').addEventListener('click',()=>{try{imageSender.send(state.currentJob,{newMap:true});}catch(err){status('main-map-status',err.message,true);}});
window.addEventListener('pagehide',()=>imageSender.dispose(),{once:true});
$('export-session').addEventListener('click',async()=>{try{download('sar-session-encrypted.json',await encryptObject(state.workspace,state.password,'workspace'));}catch{status('workspace-status','The backup could not be created.',true);}});
$('import-session').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>24*1024*1024)throw new Error();const data=await decryptObject(await readWorkspaceFile(file),state.password,'workspace');if(data.v!==1||!Array.isArray(data.jobs))throw new Error();for(const job of data.jobs){const local=state.workspace.jobs.find(j=>j.request_id===job.request_id)||state.workspace.jobs.find(j=>j.scene?.scene_id===job.scene?.scene_id&&JSON.stringify(j.request?.bbox)===JSON.stringify(job.request?.bbox));if(local?.analyst_review)job.analyst_review=local.analyst_review;}state.workspace=data;renderJobs();status('workspace-status','Encrypted session opened.');}catch{status('workspace-status','The backup could not be opened with this password.',true);}finally{e.target.value='';}});

try{state.config=await readJson('./assets/sar-workbench-config.json');for(const area of state.config.areas){const option=element('option',area.name);option.value=area.id;$('area').append(option);}const custom=element('option','Choose a custom area');custom.value='custom';$('area').append(custom);}catch{status('unlock-status','Configuration could not be loaded. Reload the page.',true);$('unlock-submit').disabled=true;}

state.event=eventFromHash(location.hash,state.config);
state.mapPoint=pointFromHash(location.hash);
$('clear-event').addEventListener('click',()=>{state.event=null;$('event-context').hidden=true;$('days').disabled=false;history.replaceState(null,'',location.pathname+location.search);});
$('clear-map-point').addEventListener('click',()=>{state.mapPoint=null;$('map-point-context').hidden=true;$('days-label').textContent='Acquisitions from the last';$('days-hint').textContent='Choose an acquisition date to see its actual footprint on the map. All acquisition times are UTC. Sentinel-1 IW VV/VH scenes are supported.';history.replaceState(null,'',location.pathname+location.search);});

