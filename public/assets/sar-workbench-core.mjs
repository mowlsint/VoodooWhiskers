export const STATES={MATCHED:'Plausible association',POSSIBLE_MATCH:'Possible association',AMBIGUOUS:'Ambiguous',UNMATCHED_IN_AVAILABLE_AIS:'No match in available AIS',NO_AIS_COVERAGE:'No AIS coverage'};
export function bboxAroundPoint(lat,lon,cfg,sideKm=20){
  if(!Number.isFinite(lat)||!Number.isFinite(lon)||lat<48||lat>80||lon< -25||lon>35)throw new Error('Choose a point within 25° W–35° E and 48–80° N.');
  if(![10,20].includes(sideKm))throw new Error('Choose a 10 km or 20 km detail tile.');
  const side=Math.min(sideKm,Math.sqrt(cfg.max_area_km2),cfg.max_dimension_px*cfg.pixel_spacing_m/1000);
  const dy=side/2/111.32,dx=dy/Math.cos(lat*Math.PI/180);
  const box=[lon-dx,lat-dy,lon+dx,lat+dy].map(n=>Number(n.toFixed(6)));
  inspectBbox(box,cfg);return box;
}
export function inspectBbox(bbox,cfg){
  if(!Array.isArray(bbox)||bbox.length!==4||bbox.some(v=>typeof v!=='number'||!Number.isFinite(v)))throw new Error('Enter four valid coordinates.');
  const [w,s,e,n]=bbox;
  if(!(w>=-25&&w<e&&e<=35&&s>=48&&s<n&&n<=80))throw new Error('The area must be in the North Sea, Baltic Sea or adjacent northern European waters.');
  const width=(e-w)*111320*Math.cos((s+n)/2*Math.PI/180),height=(n-s)*111320,area=width*height/1e6;
  const widthPx=Math.max(64,Math.ceil(width/cfg.pixel_spacing_m)),heightPx=Math.max(64,Math.ceil(height/cfg.pixel_spacing_m));
  if(area>cfg.max_area_km2||Math.max(widthPx,heightPx)>cfg.max_dimension_px||Math.min(width,height)<1000)throw new Error(`Choose a smaller area: at most ${cfg.max_area_km2} km² and about 25 km per side, with at least 1 km per side.`);
  return {area,widthPx,heightPx};
}
function inRing([x,y],ring){let inside=false;for(let i=0;i+1<ring.length;i++){const [x1,y1]=ring[i],[x2,y2]=ring[i+1];if((y1>y)!==(y2>y)&&x<(x2-x1)*(y-y1)/(y2-y1)+x1)inside=!inside;}return inside;}
export function contains(geometry,point){const polygons=geometry?.type==='Polygon'?[geometry.coordinates]:geometry?.type==='MultiPolygon'?geometry.coordinates:[];return polygons.some(p=>p?.length&&inRing(point,p[0])&&!p.slice(1).some(h=>inRing(point,h)));}
export function geometryBbox(geometry){
  const bounds=[Infinity,Infinity,-Infinity,-Infinity];let found=false;
  const visit=value=>{if(!Array.isArray(value))return;if(value.length>=2&&Number.isFinite(value[0])&&Number.isFinite(value[1])){bounds[0]=Math.min(bounds[0],value[0]);bounds[1]=Math.min(bounds[1],value[1]);bounds[2]=Math.max(bounds[2],value[0]);bounds[3]=Math.max(bounds[3],value[1]);found=true;return;}for(const item of value)visit(item);};
  visit(geometry?.coordinates);return found?bounds:null;
}
export function normalizeScene(feature,bbox,now=Date.now()){
  const p=feature?.properties||{},id=String(feature?.id||'');
  if(!/^S1[A-D]_IW_GRDH_1SDV_[A-Za-z0-9_]{35,120}$/.test(id)||p['sar:instrument_mode']!=='IW'||!['VV','VH'].every(v=>(p['sar:polarizations']||[]).includes(v)))return null;
  const dt=p.start_datetime||p.datetime,time=Date.parse(dt);
  if(!Number.isFinite(time))return null;
  const stamp=id.match(/_(\d{8}T\d{6})_/),compact=new Date(time).toISOString().replace(/[-:]/g,'').slice(0,15);
  if(!stamp||stamp[1]!==compact)return null;
  const [w,s,e,n]=bbox,orbit=p['sat:orbit_state'],coversPoint=contains(feature.geometry,[(w+e)/2,(s+n)/2]),covers=[[w,s],[w,n],[e,s],[e,n],[(w+e)/2,(s+n)/2]].every(pt=>contains(feature.geometry,pt));
  return {item_id:id,scene_id:id.replace(/_COG$/,''),datetime:dt,platform:p.platform||id.slice(0,3),orbit_direction:orbit,footprint:feature.geometry,bbox:geometryBbox(feature.geometry),covers_point:coversPoint,previewable:coversPoint&&['ascending','descending'].includes(orbit),covers_aoi:covers,processable:covers&&['ascending','descending'].includes(orbit),gfw_delay_elapsed:now-time>=5*86400000};
}
export function safeImage(value){return typeof value==='string'&&value.length<3*1024*1024&&/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(value)?value:'';}
export function stateText(state){return STATES[state]||'Not yet reviewed';}

export function eventFromHash(hash,cfg,now=Date.now()){
  if(!hash.startsWith('#event=')||hash.length>3000)return null;
  try{const e=JSON.parse(decodeURIComponent(hash.slice(7)));inspectBbox(e.bbox,cfg);const start=Date.parse(e.start),end=Date.parse(e.end);
    if(e.v!==1||e.source!=='global_fishing_watch'||typeof e.event_id!=='string'||!e.event_id||e.event_id.length>160||!Number.isFinite(start)||!Number.isFinite(end)||start>end||start<now-14*86400000-300000||end>now+300000)return null;
    return {...e,start:new Date(start).toISOString(),end:new Date(end).toISOString()};
  }catch{return null;}
}
