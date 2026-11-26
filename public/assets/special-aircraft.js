/* Optional presentation consumer. No provider calls, collection, scoring or vessel joins. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SpecialAircraft=api;})(globalThis,()=>{
  'use strict';
  const MAX_BYTES=1048576,MAX_ITEMS=5000,DAY=86400000,LATEST_AGE=30*60000;
  const LIVE_DATA_BASE='https://raw.githubusercontent.com/mowlsint/VoodooWhiskers/runtime-data/public/';
  const ROLES={police:'Police',coast_guard:'Coastguard',customs_border_guard:'Customs / border guard',military_maritime_patrol:'Military maritime patrol',military_isr:'Military ISR',military_other:'Military context',nato_owned:'NATO-owned',maritime_surveillance:'Maritime surveillance',sar_support:'SAR support',environmental_monitoring:'Environmental monitoring',fisheries_monitoring:'Fisheries monitoring',pollution_monitoring:'Pollution monitoring',emissions_monitoring:'Emissions monitoring',rpas:'RPAS / drone',search_and_rescue:'Search and rescue',hems_maritime_context:'HEMS with maritime context',offshore_sar:'Offshore SAR',offshore_transport:'Civil offshore transport'};
  const STATES=new Set(['fresh','valid_zero','partial','stale','unavailable','error']);
  const clean=v=>String(v??'').trim().slice(0,300);
  const ROLLING_FIELDS=['icao24','observed_unix_seconds','latitude','longitude','altitude_ft','ground_speed_kn','track_deg','position_source','registration','callsign','roles','country','operator','aircraft_type','commissioning_authority','identity_verified_at'];
  function rollingRows(pack,now){
    const r=pack.rolling_24h;
    if(r===undefined)return null; // Legacy product before the first rolling build.
    const at=Date.parse(r?.as_of);
    if(r?.schema!=='VOODOO_AIRCRAFT_ROLLING_24H_V1'||r.coverage_complete!==false||JSON.stringify(r.fields)!==JSON.stringify(ROLLING_FIELDS)||!Number.isFinite(at)||at>now+300000||!Array.isArray(r.items)||r.items.length>128)throw Error('Invalid rolling aircraft product');
    return r.items.flatMap(v=>{
      if(!Array.isArray(v)||v.length!==ROLLING_FIELDS.length)return [];
      const p=Object.fromEntries(ROLLING_FIELDS.map((k,i)=>[k,v[i]])),t=p.observed_unix_seconds*1000;
      if(typeof p.observed_unix_seconds!=='number'||!Number.isFinite(t)||t>at||t<at-DAY||t>now||t<now-DAY||!['adsb','mlat','tisb'].includes(p.position_source))return [];
      return [{...p,observed_at:new Date(t).toISOString(),position_timestamp_valid:true,selection_status:'registry_selected',source:'adsb.lol',position_timestamp_basis:'provider_now_ms_minus_seen_pos_seconds'}];
    });
  }
  function trackSegments(pack,now=Date.now()){
    const rolling=pack.rolling_24h, raw=rolling?.track_segments;
    if(!Array.isArray(raw)||raw.length>500)return [];
    const asOf=Date.parse(rolling.as_of)/1000,end=Math.min(asOf,now/1000),start=now/1000-86400;
    return raw.filter(r=>{
      if(!Array.isArray(r)||r.length!==7)return false;
      const [h,t1,y1,x1,t2,y2,x2]=r,dt=t2-t1;
      if(!/^[a-f0-9]{6}$/.test(h)||![t1,y1,x1,t2,y2,x2].every(Number.isFinite)||t1<start||t2>end||dt<=0||dt>900||Math.abs(y1)>90||Math.abs(y2)>90||Math.abs(x1)>180||Math.abs(x2)>180||Math.abs(x2-x1)>=180)return false;
      const rad=Math.PI/180,hav=Math.sin((y2-y1)*rad/2)**2+Math.cos(y1*rad)*Math.cos(y2*rad)*Math.sin((x2-x1)*rad/2)**2;
      return 3440.1*2*Math.asin(Math.sqrt(Math.min(1,hav)))*3600/dt<=750;
    });
  }
  function decode(pack,now=Date.now()){
    if(!pack||pack.kind!=='selected_aircraft_context'||pack.schema_version!=='1.0.0'||pack.context_only!==true||pack.coverage_complete!==false||!STATES.has(pack.status)||!Array.isArray(pack.items)||pack.items.length>MAX_ITEMS)throw Error('Invalid aircraft product');
    const generated=Date.parse(pack.generated_at);
    if(!Number.isFinite(generated)||generated>now+300000)throw Error('Invalid aircraft product time');
    if(pack.status==='valid_zero'&&pack.items.length)throw Error('Zero-result aircraft product contains positions');
    const recent=rollingRows(pack,now),hasRolling=recent!==null;
    const unique=new Map(),conflicts=new Set();let excluded=0;
    const rawRows=hasRolling?[...recent,...pack.items]:pack.items;
    const rollingTime=hasRolling?Date.parse(pack.rolling_24h.as_of):generated;
    for(const row of rawRows){
      if(!row||typeof row!=='object'){excluded++;continue;}
      const hex=clean(row.icao24).toLowerCase(),at=Date.parse(row.observed_at),lat=row.latitude,lon=row.longitude;
      const roles=Array.isArray(row.roles)?[...new Set(row.roles.filter(r=>Object.hasOwn(ROLES,r)))]:[];
      if(!/^[0-9a-f]{6}$/.test(hex)||['000000','ffffff'].includes(hex)||!['registry_selected','verified_registry'].includes(row.selection_status)||row.position_timestamp_valid!==true||row.identity_conflict===true||!roles.length||typeof lat!=='number'||typeof lon!=='number'||!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180||!Number.isFinite(at)||at>now+300000||at>Math.max(generated,rollingTime)+300000||now-at>14*DAY){excluded++;continue;}
      const key=hex+':'+at,previous=unique.get(key);
      if(conflicts.has(key)){excluded++;continue;}
      if(previous&&(previous.latitude!==lat||previous.longitude!==lon)){unique.delete(key);conflicts.add(key);excluded+=2;continue;}
      if(previous){ // Prefer richer current-poll metadata for the exact same observation.
        if(row.identity_source_url)for(const k of ['identity_source_url','role_source_url','identity_review_note'])if(row[k])previous[k]=clean(row[k]);
        continue;
      }
      const p={icao24:hex,latitude:lat,longitude:lon,observed_at:new Date(at).toISOString(),roles};
      for(const key of ['registration','callsign','operator','commissioning_authority','country','aircraft_type','source','position_source','position_timestamp_basis','identity_source_url','role_source_url','identity_verified_at','identity_review_note'])p[key]=clean(globalThis.VoodooEnglish?globalThis.VoodooEnglish(row[key]):row[key]);
      for(const key of ['altitude_ft','ground_speed_kn','track_deg'])if(typeof row[key]==='number'&&Number.isFinite(row[key]))p[key]=row[key];
      unique.set(hex+':'+at,p);
    }
    return {status:pack.status,collection_code:clean(pack.collection_code),generated_at:new Date(generated).toISOString(),items:[...unique.values()],hasRolling,rolling_as_of:hasRolling?pack.rolling_24h.as_of:null,excluded,input_count:rawRows.length,tracks:hasRolling?trackSegments(pack,now):[],tracksTruncated:!!pack.rolling_24h?.track_segments_truncated,tracksAvailable:Array.isArray(pack.rolling_24h?.track_segments)};
  }
  function visible(product,{mode='latest',hours=24,includeOffshore=false,now=Date.now()}={}){
    if(!product||mode==='project'||(['unavailable','error'].includes(product.status)&&!product.hasRolling)||['collection_disabled','empty_selection'].includes(product.collection_code))return [];
    const cutoff=now-(mode==='latest'?DAY:Math.min(product.hasRolling?24:336,Math.max(0,Number(hours)||0))*3600000),latest=new Map();
    for(const row of product.items){const at=Date.parse(row.observed_at);if(at<cutoff||at>now||(!includeOffshore&&row.roles.every(r=>r==='offshore_transport')))continue;const prev=latest.get(row.icao24);if(!prev||at>Date.parse(prev.observed_at))latest.set(row.icao24,row);}
    return [...latest.values()].sort((a,b)=>Date.parse(b.observed_at)-Date.parse(a.observed_at));
  }
  function ageLabel(p,now=Date.now()){const min=Math.max(0,Math.floor((now-Date.parse(p.observed_at))/60000));return min<1?'less than 1 min ago':min<60?min+' min ago':Math.floor(min/60)+' h '+(min%60)+' min ago';}
  function ageOpacity(p,now=Date.now()){const age=now-Date.parse(p.observed_at);return age<=LATEST_AGE?1:age<=6*3600000?.72:.42;}
  function colour(p){return p.roles.every(r=>r==='offshore_transport')?'#a5b2bd':p.roles.some(r=>['search_and_rescue','offshore_sar','hems_maritime_context'].includes(r))?'#f2d36b':'#9edff6';}
  function researchLinks(p){
    const links=[],reg=clean(p.registration).toUpperCase(),hex=clean(p.icao24).toLowerCase(),at=Date.parse(p.observed_at);
    if(/^[A-Z0-9][A-Z0-9+-]{1,19}$/.test(reg))links.push({label:'Flightradar24 · aircraft flight history',href:'https://www.flightradar24.com/data/aircraft/'+encodeURIComponent(reg.toLowerCase())});
    else links.push({label:'Flightradar24 · search by registration or ICAO24',href:'https://www.flightradar24.com/data/aircraft'});
    if(/^[0-9a-f]{6}$/.test(hex)&&Number.isFinite(at))links.push({label:'adsb.lol · trace for observation day (UTC)',href:'https://globe.adsb.lol/?icao='+hex+'&showTrace='+new Date(at).toISOString().slice(0,10)});
    return links;
  }
  function popup(p,doc){
    const box=doc.createElement('div'),title=doc.createElement('strong');title.textContent=p.registration||p.callsign||p.icao24;box.append(title);
    const fields=[['ICAO24',p.icao24],['Registration',p.registration],['Callsign',p.callsign],['Aircraft type',p.aircraft_type],['Operator',p.operator],['Commissioning authority',p.commissioning_authority],['Country',p.country],['Roles',p.roles.map(r=>ROLES[r]).join(', ')],['Last known position UTC',p.observed_at],['Observation age',ageLabel(p)],['Altitude',p.altitude_ft==null?'':p.altitude_ft+' ft'],['Ground speed',p.ground_speed_kn==null?'':p.ground_speed_kn+' kn'],['Ground track (heading)',p.track_deg==null?'':p.track_deg+'°'],['Source',p.source||'Not supplied'],['Position source',p.position_source],['Time basis',p.position_timestamp_basis],['Registry reviewed',p.identity_verified_at],['Identity evidence',p.identity_source_url],['Role evidence',p.role_source_url],['Identity review note',p.identity_review_note]];
    for(const [label,value] of fields)if(value){const line=doc.createElement('div');line.textContent=label+': '+value;box.append(line);}
    const note=doc.createElement('p');note.textContent='Independently verify whether this is an authority or military aircraft and what its flight activity or flight pattern represents. Specialist civil aircraft may also be included. Sampled positions and operator/role labels do not establish the current mission, a rescue, pollution or surveillance of a vessel. Dashed track segments connect received samples only; the true intervening flight path is unknown.';box.append(note);
    for(const link of researchLinks(p)){const line=doc.createElement('div'),a=doc.createElement('a');a.textContent=link.label;a.href=link.href;a.target='_blank';a.rel='noopener noreferrer';a.referrerPolicy='no-referrer';line.append(a);box.append(line);}
    const help=doc.createElement('p');help.textContent='Use the registration or ICAO24 above to search and select a flight/date. FR24 documents 7 days of free history; older history may require a subscription. Coverage of authority/military aircraft and historical tracks is not guaranteed. External sites are contacted only when you open a link.';box.append(help);
    if(p.source==='adsb.lol'){const credit=doc.createElement('a');credit.href='https://opendatacommons.org/licenses/odbl/1-0/';credit.textContent='Aircraft position data: adsb.lol · ODbL 1.0';credit.target='_blank';credit.rel='noopener noreferrer';box.append(credit);}return box;
  }
  async function fetchProduct(url){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
    try{
      const source=url.startsWith('./data/')?new URL(url.slice(2),LIVE_DATA_BASE).href:url;
      const res=await fetch(source,{cache:'no-store',signal:controller.signal});if(!res.ok)throw Error('Aircraft product unavailable');
      if(Number(res.headers?.get?.('content-length'))>MAX_BYTES)throw Error('Aircraft product exceeds budget');
      let body;
      if(res.body?.getReader){const reader=res.body.getReader(),chunks=[];let size=0;try{for(;;){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>MAX_BYTES)throw Error('Aircraft product exceeds budget');chunks.push(value);}}finally{await reader.cancel().catch(()=>{});}const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.byteLength;}body=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}
      else{body=await res.text();if(new TextEncoder().encode(body).length>MAX_BYTES)throw Error('Aircraft product exceeds budget');}
      return decode(JSON.parse(body));
    }finally{clearTimeout(timer);}
  }
  function attach(map,getView){
    const $=id=>document.getElementById(id),toggle=$('showSpecialAircraft'),offshore=$('showOffshoreAircraft'),status=$('specialAircraftStatus'),list=$('specialAircraftList'),refresh=$('refreshSpecialAircraft');
    const layer=L.layerGroup().addTo(map);let product=null,loading=null,error='';
    const trackToggle=$('showAircraftTracks');
    const blocked=()=>{const v=getView();return v.isSar||v.mode==='project';};
    function render(){
      layer.clearLayers();list.replaceChildren();refresh.hidden=!toggle.checked;
      if(!toggle.checked){status.textContent='Off · aircraft data are loaded only when enabled.';return;}
      if(blocked()){refresh.hidden=true;status.textContent='Aircraft hidden during historical SAR comparison or local project view.';return;}
      if(loading&&!product){status.textContent='Loading selected aircraft observations …';return;}
      if(error&&!product){status.textContent='Aircraft data unavailable. Collection may not be active; this is not a zero result. Use Refresh to retry.';return;}
      if(!product){status.textContent='No aircraft data loaded. Use Refresh to check availability.';return;}
      const view=getView(),roleFilter=$('aircraftRoleFilter')?.value||'',roleGroups={sar:['search_and_rescue','sar_support','offshore_sar','hems_maritime_context'],authority:['police','coast_guard','customs_border_guard'],environment:['environmental_monitoring','fisheries_monitoring','pollution_monitoring','emissions_monitoring'],military:['military_maritime_patrol','military_isr','military_other','nato_owned'],rpas:['rpas'],offshore:['offshore_transport']},rows=visible(product,{...view,includeOffshore:offshore.checked||roleFilter==='offshore'}).filter(p=>!roleFilter||p.roles.some(r=>roleGroups[roleFilter]?.includes(r))),stale=product.status==='stale'||Date.now()-Date.parse(product.generated_at)>LATEST_AGE;
      if(['unavailable','error'].includes(product.status)&&(!product.hasRolling||['collection_disabled','empty_selection'].includes(product.collection_code))){const reasons={daily_version_budget:'Aircraft sampling paused: daily repository growth budget reached',repository_warning_budget:'Aircraft sampling paused: repository size warning reached',repository_size_unavailable:'Aircraft sampling paused: repository size could not be checked',rate_limited:'Aircraft provider rate limit reached; waiting before another query',collection_disabled:'Aircraft collection is switched off',empty_selection:'No aircraft are selected for collection'};status.textContent=(reasons[product.collection_code]||'Aircraft source reports '+product.status)+'; this is not a zero result.';return;}
      if(product.status==='valid_zero'&&!product.hasRolling){status.textContent=(stale?'Stale zero-result product':'Valid zero result in the selected feed')+' · product UTC '+product.generated_at+'. This does not establish absence of aircraft.';return;}
      const prefix=error?'Refresh failed; showing last received observations':loading?'Refreshing; showing last received observations':product.status==='valid_zero'?'Last poll returned no positions': ['unavailable','error'].includes(product.status)?'Source unavailable or paused · retained observations only':stale?'Stale aircraft product':product.status==='partial'?'Partial aircraft data':'Selected aircraft observations';
      status.textContent=`${prefix} · ${rows.length} displayed · product UTC ${product.generated_at}.${product.excluded?' '+product.excluded+' unusable/conflicting records excluded.':''}${!rows.length?' No usable selected positions in this time window.':''} Last known position per aircraft within 24 h; older markers fade. Not current flight locations.${product.hasRolling?'':' Rolling history not yet published; showing available legacy samples only.'}`;
      if(offshore.checked||roleFilter==='offshore')status.textContent+=' · '+rows.filter(r=>r.roles.includes('offshore_transport')).length+' civil offshore aircraft displayed. No observation is not evidence of no flights.';
      if(trackToggle?.checked){
        const byHex=new Map(rows.map(p=>[p.icao24,p])),now=Date.now()/1000,cutoff=now-86400;let count=0;
        for(const [hex,t1,lat1,lon1,t2,lat2,lon2] of product.tracks||[]){
          const p=byHex.get(hex);if(!p||t1<cutoff||t2>now)continue;
          const note=document.createElement('div');note.textContent=(p.registration||hex)+' · '+new Date(t1*1000).toISOString()+' → '+new Date(t2*1000).toISOString()+' · sampled positions, not a measured flight path.';
          L.polyline([[lat1,lon1],[lat2,lon2]],{color:colour(p),weight:2,opacity:.8,dashArray:'5 6'}).bindPopup(note).addTo(layer);count++;
        }
        status.textContent+=' · '+count+' aircraft track segments'+(product.tracksTruncated?' (bounded subset)':product.tracksAvailable?'':' (track product not yet available)')+'. Spatial overlap alone does not establish a simultaneous encounter.';
      }
      for(const p of rows){
        const icon=L.divIcon({className:'specialAircraftIcon',html:'<span aria-hidden="true">✈</span>',iconSize:[22,22],iconAnchor:[11,11]});
        const marker=L.marker([p.latitude,p.longitude],{icon,opacity:Math.min(stale?.65:1,ageOpacity(p)),specialAircraft:true,title:p.registration||p.callsign||p.icao24}).bindPopup(()=>popup(p,document),{maxWidth:430});
        marker.on('add',()=>{const el=marker.getElement();if(el)el.style.color=colour(p);});marker.addTo(layer);
        const button=document.createElement('button');button.type='button';button.className='specialAircraftResult';button.textContent='✈ '+(p.registration||p.callsign||p.icao24)+' · '+p.roles.map(r=>ROLES[r]).join(', ')+' · last seen '+ageLabel(p)+' · '+p.observed_at;button.addEventListener('click',()=>{map.setView([p.latitude,p.longitude],9);marker.openPopup();});list.append(button);
      }

    }
    async function load(){
      if(!toggle.checked||blocked()||loading)return;
      error='';loading=fetchProduct('./data/aircraft/aircraft_latest.json');refresh.disabled=true;render();
      try{product=await loading;}catch{error='unavailable';}finally{loading=null;refresh.disabled=false;render();}
    }
    toggle.addEventListener('change',()=>{render();if(toggle.checked&&!product)load();});offshore.addEventListener('change',render);$('aircraftRoleFilter')?.addEventListener('change',render);refresh.addEventListener('click',load);trackToggle?.addEventListener('change',render);render();
    if(typeof window!=='undefined')window.setInterval(()=>{if(toggle.checked&&!blocked())render();},60000);
    return {render};
  }
  return {MAX_BYTES,ROLES,decode,trackSegments,visible,colour,ageLabel,ageOpacity,researchLinks,popup,fetchProduct,attach};
});
