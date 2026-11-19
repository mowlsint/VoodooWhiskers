/* Analyst-opened SAR previews only. No provider calls, workflows, storage or AIS association. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SatelliteImages=api;})(globalThis,()=>{
  'use strict';
  const MAX_IMAGES=6,MAX_PNG=2*1024*1024,MAX_FILE=24*1024*1024;
  const validSession=s=>typeof s==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(s);
  const text=v=>typeof v==='string'?v.trim().slice(0,180):'';
  let mapSession='';
  function pngInfo(image){
    if(typeof image!=='string'||image.length>Math.ceil(MAX_PNG/3)*4+30||!/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(image))throw Error('A bounded PNG preview is required.');
    const raw=atob(image.slice(22));
    if(raw.length<33||raw.length>MAX_PNG||[137,80,78,71,13,10,26,10].some((n,i)=>raw.charCodeAt(i)!==n)||raw.slice(12,16)!=='IHDR')throw Error('Invalid PNG preview.');
    const number=p=>[0,1,2,3].reduce((n,i)=>n*256+raw.charCodeAt(p+i),0),width=number(16),height=number(20);
    if(width<1||height<1||width>1200||height>1200)throw Error('Preview dimensions exceed the image budget.');
    return {width,height};
  }
  function validFootprint(geometry,bbox){
    const polygons=geometry?.type==='Polygon'?[geometry.coordinates]:geometry?.type==='MultiPolygon'?geometry.coordinates:[];
    if(!polygons.length||polygons.length>20)return false;
    let count=0;
    for(const polygon of polygons){if(!Array.isArray(polygon)||!polygon.length)return false;for(const ring of polygon){
      if(!Array.isArray(ring)||ring.length<4)return false;count+=ring.length;if(count>12000)return false;
      for(const point of ring)if(!Array.isArray(point)||point.length<2||!Number.isFinite(point[0])||!Number.isFinite(point[1])||point[0]<-180||point[0]>180||point[1]<-85||point[1]>85)return false;
    }}
    const points=polygons.flat(2),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    const actual=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];
    return actual.every((n,i)=>Math.abs(n-bbox[i])<0.02);
  }
  function normalizeImage(job,now=Date.now()){
    const isOverview=job?.scene_preview_only===true;
    if(!job||job.state!=='ready'||job.is_fixture===true||job.images?.preview_is_derived!==true)throw Error('Choose a completed, real SAR image preview.');
    const box=isOverview?job.scene_preview_bbox:job.request?.bbox,acquired=Date.parse(job.scene?.datetime),scene=text(job.scene?.scene_id);
    if(!scene||!Number.isFinite(acquired)||acquired>now+300000||!Array.isArray(box)||box.length!==4||box.some(n=>typeof n!=='number'||!Number.isFinite(n)))throw Error('The image identity, time or footprint is invalid.');
    const [w,s,e,n]=box,width=(e-w)*111320*Math.cos((s+n)/2*Math.PI/180),height=(n-s)*111320;
    if(!(w>=-180&&w<e&&e<=180&&s>=-85&&s<n&&n<=85))throw Error('The image footprint is invalid.');
    if(isOverview){
      if(width>400000||height>400000||width*height>120000e6||!validFootprint(job.scene_footprint,box))throw Error('The full-scene preview footprint exceeds its bounded map area.');
    }else if(width>25001||height>25001||width*height>500e6)throw Error('The processed image footprint exceeds the bounded SAR area.');
    for(const metadata of [job.candidates?.metadata,job.context?.metadata]){
      if(metadata?.aoi_bbox_wgs84&&JSON.stringify(metadata.aoi_bbox_wgs84)!==JSON.stringify(box))throw Error('The image and processed-area georeference disagree.');
    }
    const image=isOverview?job.images.scene_preview:job.images.quicklook,dimensions=pngInfo(image),at=new Date(acquired).toISOString();
    return {v:1,id:scene+'|'+box.join(',')+'|'+at,image_kind:isOverview?'full_scene_context_overview':'detailed_aoi',
      scene_id:scene,acquired_at:at,area_name:isOverview?'Sentinel-1 full scene':text(job.request.area_name)||'Processed SAR area',
      bbox:box.slice(),source_footprint:isOverview?job.scene_footprint:null,
      meters_per_pixel:isOverview?job.preview_meters_per_pixel:null,image,...dimensions};
  }

  function fromTransfer(value,now=Date.now()){
    if(!value||value.v!==1)throw Error('Unknown image handoff.');
    const overview=value.image_kind==='full_scene_context_overview';
    return normalizeImage(overview?{state:'ready',scene_preview_only:true,scene_preview_bbox:value.bbox,scene_footprint:value.source_footprint,preview_meters_per_pixel:value.meters_per_pixel,
      scene:{scene_id:value.scene_id,datetime:value.acquired_at},images:{scene_preview:value.image,preview_is_derived:true}}:
      {state:'ready',request:{bbox:value.bbox,area_name:value.area_name},scene:{scene_id:value.scene_id,datetime:value.acquired_at},images:{quicklook:value.image,preview_is_derived:true}},now);
  }
  function footprint(record){
    const [w,s,e,n]=record.bbox,geometry=record.source_footprint||{type:'Polygon',coordinates:[[[w,s],[e,s],[e,n],[w,n],[w,s]]]};
    return {type:'FeatureCollection',features:[{type:'Feature',geometry,
      properties:{scene_id:record.scene_id,acquired_at:record.acquired_at,area_name:record.area_name,image_type:record.image_kind||'detailed_aoi',width:record.width,height:record.height,meters_per_pixel:record.meters_per_pixel,raw_geotiff_available:false}}]};
  }
  function filename(record,extension){return ('SAR-'+record.acquired_at.slice(0,19).replace(/[^0-9T]/g,'')+'-'+record.scene_id).replace(/[^a-zA-Z0-9_-]/g,'_').slice(0,150)+'.'+extension;}
  async function openWorkspace(file,password,dependencies){
    if(!file||file.size>MAX_FILE)throw Error('Choose an encrypted result file up to 24 MiB.');
    const io=dependencies||{...(await import('./sar-import.mjs')),...(await import('./sar-crypto.mjs'))};
    const workspace=await io.decryptObject(await io.readWorkspaceFile(file),password,'workspace');
    if(workspace?.v!==1||!Array.isArray(workspace.jobs)||workspace.jobs.length>MAX_IMAGES)throw Error('Invalid or oversized SAR workspace.');
    const records=workspace.jobs.filter(job=>job?.state==='ready').map(job=>normalizeImage(job));
    if(!records.length)throw Error('This file contains no completed image previews.');
    return [...new Map(records.map(record=>[record.id,record])).values()];
  }
  function workbenchUrl(url='./sar.html'){
    const u=new URL(url,location.href);
    if(u.origin!==location.origin||!u.pathname.endsWith('/sar.html'))throw Error('Unexpected workbench destination.');
    if(mapSession)u.searchParams.set('satellite_session',mapSession);
    return u.href;
  }
  function createSender({Channel=globalThis.BroadcastChannel,openTab=(url)=>window.open(url,'_blank','noopener,noreferrer'),onStatus=()=>{},session=new URL(location.href).searchParams.get('satellite_session')}={}){
    let channel=null,pending=null,timer=null,disposed=false;
    function clear(){pending=null;clearTimeout(timer);timer=null;channel?.close();channel=null;}
    return {send(job,{newMap=false}={}){
      if(disposed)throw Error('Image sender is closed.');
      const image=normalizeImage(job);clear();
      if(!Channel)throw Error('Tab handoff is unavailable. Save the encrypted session and open it in Satellite images on the main map.');
      const linked=validSession(session)&&!newMap;if(!linked)session=crypto.randomUUID();
      pending={id:crypto.randomUUID(),image};channel=new Channel('voodoo-satellite-'+session);
      channel.onmessage=event=>{
        const message=event.data;
        if(message?.type==='ready'&&pending&&(!message.id||message.id===pending.id))channel.postMessage({type:'image',id:pending.id,image:pending.image});
        if(message?.type==='received'&&pending&&message.id===pending.id){clear();onStatus('Image added to Satellite images on the main map. Switch back to that tab; the image itself remains optional.');}
      };
      timer=setTimeout(()=>{clear();onStatus('The main map did not respond. Open it, or save the encrypted session and load it in Satellite images.');},10000);
      onStatus('Sending the selected preview to the main map …');
      if(linked)channel.postMessage({type:'offer',id:pending.id});
      else{const url=new URL('./index.html',location.href);url.searchParams.set('satellite_session',session);openTab(url.href);}
    },cancel:clear,dispose(){clear();disposed=true;}};
  }
  function attach(map){
    const $=id=>document.getElementById(id),panel=$('satellitePanel'),open=$('satelliteOpen'),select=$('satelliteSelect'),status=$('satelliteStatus');
    const incoming=new URL(location.href).searchParams.get('satellite_session');mapSession=validSession(incoming)?incoming:crypto.randomUUID();
    const layer=L.layerGroup().addTo(map),records=new Map();let selected='',busy=false,generation=0,channel=null,offer=null,allowInitial=validSession(incoming);
    const selectedRecord=()=>records.get(selected);
    function notice(message){status.textContent=message;}
    function show(){panel.hidden=false;open.setAttribute('aria-expanded','true');}
    function draw(){
      layer.clearLayers();
      for(const record of records.values()){
        const [w,s,e,n]=record.bbox,bounds=[[s,w],[n,e]];
        if(record.showImage)L.imageOverlay(record.image,bounds,{opacity:record.opacity,interactive:false,satellitePreview:true}).on('error',()=>notice('The PNG preview could not be displayed. The footprint and other map layers remain available.')).addTo(layer);
        if(record.showFootprint)L.geoJSON(footprint(record),{style:{color:'#e5b66e',weight:2,fillOpacity:0},satelliteFootprint:true}).bindTooltip(record.image_kind==='full_scene_context_overview'?'Sentinel-1 scene footprint · '+record.acquired_at:'Processed SAR image area · '+record.acquired_at).addTo(layer);
      }
    }
    function controls(){
      const record=selectedRecord();$('satelliteControls').hidden=!record;
      if(!record)return;
      $('satelliteFootprint').checked=record.showFootprint;$('satelliteImage').checked=record.showImage;$('satelliteOpacity').value=String(record.opacity);
      const scale=Number.isFinite(record.meters_per_pixel)?' · about '+Math.round(record.meters_per_pixel)+' m/pixel':'';
      const kind=record.image_kind==='full_scene_context_overview'?'full-scene overview, not vessel-scale detail':'derived SAR preview';
      $('satelliteDetails').textContent=record.area_name+' · '+record.acquired_at+' · '+record.width+' × '+record.height+' px'+scale+' · '+kind+', not the raw GeoTIFF.';
    }
    function add(values,fromTab=false){
      if(new Set([...records.keys(),...values.map(r=>r.id)]).size>MAX_IMAGES)throw Error('At most six images can be held in this tab. Remove an image first.');
      for(const [index,value] of values.entries()){
        const previous=records.get(value.id),showByDefault=fromTab?value.image_kind==='full_scene_context_overview':index===0;
        records.set(value.id,{...value,showFootprint:previous?.showFootprint??(fromTab||index===0),showImage:previous?.showImage??showByDefault,opacity:previous?.opacity??.7});
      }
      selected=values[0].id;select.replaceChildren();
      for(const record of records.values()){const option=document.createElement('option');option.value=record.id;option.textContent=record.area_name+' · '+record.acquired_at;select.append(option);}select.value=selected;controls();draw();show();
      if(!fromTab){const r=selectedRecord();if(r){const [w,s,e,n]=r.bbox;map.fitBounds([[s,w],[n,e]],{maxZoom:13,padding:[25,25]});}}
      notice(fromTab?'Image received from the SAR workbench. The preview and footprint can be switched independently.':'Encrypted result opened locally. The selected preview and footprint are displayed; nothing was uploaded.');
    }
    function save(name,body,type){const url=URL.createObjectURL(new Blob([body],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    open.addEventListener('click',()=>{if(panel.hidden)show();else{panel.hidden=true;open.setAttribute('aria-expanded','false');}});
    $('satelliteClose').addEventListener('click',()=>{panel.hidden=true;open.setAttribute('aria-expanded','false');open.focus();});
    select.addEventListener('change',()=>{selected=select.value;controls();});
    for(const [id,key] of [['satelliteFootprint','showFootprint'],['satelliteImage','showImage']])$(id).addEventListener('change',()=>{const r=selectedRecord();if(r){r[key]=$(id).checked;draw();}});
    $('satelliteOpacity').addEventListener('input',()=>{const r=selectedRecord();if(r){r.opacity=Number($('satelliteOpacity').value);draw();}});
    $('satelliteFit').addEventListener('click',()=>{const r=selectedRecord();if(r){const [w,s,e,n]=r.bbox;map.fitBounds([[s,w],[n,e]],{maxZoom:13,padding:[25,25]});}});
    $('satelliteDownload').addEventListener('click',()=>{const r=selectedRecord();if(!r)return;const raw=atob(r.image.slice(22)),bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));save(filename(r,'png'),bytes,'image/png');});
    $('satelliteGeojson').addEventListener('click',()=>{const r=selectedRecord();if(r)save(filename(r,'geojson'),JSON.stringify(footprint(r),null,2),'application/geo+json');});
    $('satelliteRemove').addEventListener('click',()=>{records.delete(selected);select.querySelector('option:checked')?.remove();selected=select.value;controls();draw();if(!records.size)notice('No images loaded.');});
    $('satelliteClear').addEventListener('click',()=>{generation++;offer=null;allowInitial=false;records.clear();selected='';select.replaceChildren();$('satellitePassword').value='';$('satelliteFile').value='';controls();draw();notice('Images and password cleared from this map tab.');});
    $('satelliteLoad').addEventListener('click',async()=>{
      if(busy)return;busy=true;const ticket=generation;$('satelliteLoad').disabled=true;notice('Opening the encrypted file locally …');
      try{const values=await openWorkspace($('satelliteFile').files[0],$('satellitePassword').value);if(ticket===generation)add(values);}
      catch(err){if(ticket===generation)notice(err.message||'The encrypted file could not be opened. Existing images are unchanged.');}
      finally{$('satellitePassword').value='';$('satelliteFile').value='';busy=false;$('satelliteLoad').disabled=false;}
    });
    document.querySelectorAll('a[href^="./sar.html"]').forEach(a=>{a.href=workbenchUrl(a.getAttribute('href'));a.target='_blank';a.rel='noopener noreferrer';});
    if(globalThis.BroadcastChannel){
      channel=new BroadcastChannel('voodoo-satellite-'+mapSession);channel.onmessage=event=>{
        const message=event.data;
        if(message?.type==='offer'&&validSession(message.id)){offer=message.id;channel.postMessage({type:'ready',id:offer});}
        if(message?.type==='image'&&validSession(message.id)&&(message.id===offer||allowInitial)){
          try{add([fromTransfer(message.image)],true);channel.postMessage({type:'received',id:message.id});offer=null;allowInitial=false;}
          catch(err){show();notice(err.message||'The image handoff was rejected.');}
        }
      };if(validSession(incoming))channel.postMessage({type:'ready'});
    }
    // Move the non-modal window within the map. Arrow keys on its handle provide a keyboard equivalent.
    const handle=$('satelliteHandle');let drag=null;
    function move(x,y){const parent=panel.parentElement.getBoundingClientRect(),box=panel.getBoundingClientRect();panel.style.left=Math.max(0,Math.min(x,parent.width-box.width))+'px';panel.style.top=Math.max(0,Math.min(y,parent.height-Math.min(box.height,80)))+'px';panel.style.right='auto';}
    handle.addEventListener('pointerdown',e=>{if(e.target.closest('button'))return;const box=panel.getBoundingClientRect(),parent=panel.parentElement.getBoundingClientRect();drag={x:e.clientX,y:e.clientY,left:box.left-parent.left,top:box.top-parent.top};handle.setPointerCapture?.(e.pointerId);});
    handle.addEventListener('pointermove',e=>{if(drag)move(drag.left+e.clientX-drag.x,drag.top+e.clientY-drag.y);});handle.addEventListener('pointerup',()=>{drag=null;});handle.addEventListener('pointercancel',()=>{drag=null;});
    handle.addEventListener('keydown',e=>{const delta={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]}[e.key];if(!delta)return;e.preventDefault();const p=panel.parentElement.getBoundingClientRect(),b=panel.getBoundingClientRect();move(b.left-p.left+delta[0],b.top-p.top+delta[1]);});
    panel.addEventListener('keydown',e=>{if(e.key==='Escape')$('satelliteClose').click();});
    window.addEventListener('pagehide',()=>{generation++;channel?.close();records.clear();layer.clearLayers();$('satellitePassword').value='';},{once:true});
    window.addEventListener('resize',()=>{if(!panel.hidden){const p=panel.parentElement.getBoundingClientRect(),b=panel.getBoundingClientRect();move(b.left-p.left,b.top-p.top);}});
    return {resetVisibility(){for(const r of records.values()){r.showImage=false;r.showFootprint=false;}controls();draw();}};
  }
  return {MAX_IMAGES,MAX_PNG,pngInfo,normalizeImage,fromTransfer,footprint,filename,openWorkspace,validSession,workbenchUrl,createSender,attach};
});
