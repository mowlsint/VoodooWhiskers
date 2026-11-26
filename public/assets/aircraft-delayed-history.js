(function(root,factory){const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.AircraftDelayedHistory=api;})(globalThis,()=>{
"use strict";
const DAY=86400,MAX_ROWS=30000,MAX_BYTES=4*1024*1024;
function decode(pack,now=Date.now()/1000){
 if(pack?.schema!=="VOODOO_AIRCRAFT_DELAYED_HISTORY_V1"||pack.coverage_complete!==false||pack.min_delay_hours!==24||typeof pack.available!=="boolean"||!Array.isArray(pack.items)||pack.items.length>MAX_ROWS||!Number.isFinite(Date.parse(pack.generated_at))||Date.parse(pack.generated_at)/1000>now+300)throw Error("Invalid delayed history");
 const end=Math.min(now,Date.parse(pack.generated_at)/1000)-DAY, start=now-14*DAY, unique=new Map(),conflicts=new Set();
 for(const r of pack.items){if(!Array.isArray(r)||r.length!==7)continue;const [id,name,t,lat,lon,alt,source]=r;if(!/^air_[a-z0-9]+$/i.test(id)||typeof name!=="string"||name.length>30||!Number.isFinite(t)||t<start||t>end||!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180||!["adsb","mlat","tisb","ADS-B","MLAT","TIS-B"].includes(source))continue;const key=id+":"+t,old=unique.get(key);if(conflicts.has(key))continue;if(old&&(old[3]!==lat||old[4]!==lon||old[1]!==name)){unique.delete(key);conflicts.add(key);continue;}if(!old)unique.set(key,r);}
 return {items:pack.available?[...unique.values()].sort((a,b)=>a[2]-b[2]):[],available:pack.available,sampled:!!pack.sampled};
}
function segments(rows){
 const out=[];for(let i=1;i<rows.length;i++){const a=rows[i-1],b=rows[i],dt=b[2]-a[2];if(a[0]!==b[0]||a[1]!==b[1]||dt<=0||dt>900)continue;const rad=Math.PI/180,dlat=(b[3]-a[3])*rad,dlon=(b[4]-a[4])*rad,h=Math.sin(dlat/2)**2+Math.cos(a[3]*rad)*Math.cos(b[3]*rad)*Math.sin(dlon/2)**2;const nm=3440.1*2*Math.asin(Math.sqrt(Math.min(1,h)));if(nm*3600/dt<=750&&Math.abs(b[4]-a[4])<180)out.push([a,b]);}return out;
}
function attach(map,getView){
 const $=id=>document.getElementById(id),input=$("aircraftDelayedImport"),select=$("aircraftDelayedSelection"),show=$("aircraftDelayedShow"),status=$("aircraftDelayedStatus"),fit=$("aircraftDelayedFit");if(!input||!select||!show||!status||!fit)return null;
 const layer=L.layerGroup().addTo(map);let pack=null,points=[],generation=0;
 function render(){layer.clearLayers();points=[];if(!pack)return;const view=getView();if(!pack.available){status.textContent="Historical source unavailable; not zero activity.";return;}if(view.isSar||view.mode==="project"||view.mode==="latest"){status.textContent="Delayed samples are hidden outside a historical aircraft review window.";return;}if(!show.checked||!select.value){status.textContent=pack.items.length+" delayed samples loaded locally; select an aircraft and enable the overlay.";return;}
 const now=Date.now()/1000,cutoff=now-Math.min(336,Math.max(0,Number(view.hours)||0))*3600;points=pack.items.filter(r=>r[0]===select.value&&r[2]>=cutoff&&r[2]<=now-DAY);
 for(const [a,b] of segments(points))L.polyline([[a[3],a[4]],[b[3],b[4]]],{color:"#a3c9e8",weight:1.5,dashArray:"4 6",interactive:false}).addTo(layer);
 for(const r of points){const d=document.createElement("div");d.textContent=r[1]+" · "+new Date(r[2]*1000).toISOString()+" · "+r[6]+" · received sample, no mission inferred";L.circleMarker([r[3],r[4]],{radius:3,color:"#a3c9e8",fillOpacity:.7,weight:1}).bindPopup(d).addTo(layer);}
 status.textContent=points.length+" received samples · at least 24 h delayed"+(pack.sampled?" · export sampled":"")+" · dashed lines are not measured flight paths. Data: adsb.lol / ODbL 1.0.";
 }
 input.addEventListener("change",async()=>{const token=++generation;pack=null;show.checked=false;select.replaceChildren(new Option("Select aircraft",""));render();try{const file=input.files?.[0];if(!file||file.size>MAX_BYTES)throw Error();const decoded=decode(JSON.parse(await file.text()));if(token!==generation)return;pack=decoded;const ids=new Map(pack.items.map(r=>[r[0],r[1]]));for(const [id,name] of [...ids].sort((a,b)=>a[1].localeCompare(b[1])))select.append(new Option(name,id));render();}catch{if(token===generation)status.textContent="Invalid, expired or oversized delayed history file.";}});
 select.addEventListener("change",render);show.addEventListener("change",render);fit.addEventListener("click",()=>{if(points.length)map.fitBounds(L.latLngBounds(points.map(r=>[r[3],r[4]])).pad(.1),{maxZoom:10});});$("resetOverview")?.addEventListener("click",()=>{show.checked=false;render();});return {render};
}
return {decode,segments,attach};
});
