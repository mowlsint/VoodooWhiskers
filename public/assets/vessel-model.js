/* Shared, dependency-free source and identity model. Coordinates never imply continuity. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.VesselModel=api;})(globalThis,()=>{
  'use strict';
  const SOURCES={openwaters:{label:'Open Waters',color:'#4da3ff'},fintraffic:{label:'Finnland · Fintraffic',color:'#39d3df'},barentswatch:{label:'Norwegen · BarentsWatch',color:'#61d29a'},ais_dk_historical:{label:'Dänemark · historisches AIS',color:'#ffb44e'},global_fishing_watch:{label:'GFW · historischer Kontext',color:'#c8a1ff'},unknown:{label:'Quelle nicht belegt',color:'#9da6b1'}};
  const digits=v=>String(v??'').replace(/\D/g,'');
  function imo(v){const s=digits(v);return /^\d{7}$/.test(s)&&[...s.slice(0,6)].reduce((n,c,i)=>n+Number(c)*(7-i),0)%10===Number(s[6])?s:'';}
  function mmsi(v){const s=digits(v);return /^\d{9}$/.test(s)&&s!=='000000000'?s:'';}
  function source(p={}){
    const explicit=String(p.source_provider||p.provider||'').toLowerCase();
    if(SOURCES[explicit])return explicit;
    const text=explicit+' '+String(p.source||'').toLowerCase();
    if(/openwaters|open.?waters/.test(text))return 'openwaters';
    if(/fintraffic|digitraffic/.test(text))return 'fintraffic';
    if(/barentswatch|kystverket|norwegian coastal/.test(text))return 'barentswatch';
    if(/ais_dk|danish/.test(text))return 'ais_dk_historical';
    if(/global.fishing.watch|\bgfw\b/.test(text))return 'global_fishing_watch';
    return 'unknown';
  }
  function sourceLabel(p){const id=source(p);return SOURCES[id].label+(id==='openwaters'&&p.upstream_source?` · Zulieferer: ${p.upstream_source}`:'');}
  function observed(p){const raw=p.observed_at||p.last_seen_utc;return p.position_timestamp_valid===false||p.timestamp_valid===false||p.data_quality?.timestamp_repaired?NaN:Date.parse(raw||'');}
  function age(p,now=Date.now()){const t=observed(p);if(!Number.isFinite(t))return 'Beobachtungszeit nicht belastbar';const minutes=Math.max(0,(now-t)/60000);return `${minutes<120?Math.round(minutes)+' min':minutes<2880?(minutes/60).toFixed(1)+' h':(minutes/1440).toFixed(1)+' d'} alt`;}
  function groups(rows){
    const alias=new Map();
    for(const p of rows){const i=imo(p.imo),m=mmsi(p.mmsi);if(i&&m){if(!alias.has(m))alias.set(m,new Set());alias.get(m).add(i);}}
    const result=new Map();
    rows.forEach((p,n)=>{const i=imo(p.imo),m=mmsi(p.mmsi),known=alias.get(m),key=i?`imo:${i}`:m?(known?.size===1?`imo:${[...known][0]}`:`mmsi:${m}`):`unresolved:${n}`;
      const item={...p,vessel_identity:key,identity_conflict:Boolean(m&&known?.size>1)};if(!result.has(key))result.set(key,[]);result.get(key).push(item);});
    return result;
  }
  function coordinates(p){const rawLat=p.latitude??p.lat,rawLon=p.longitude??p.lon;if(rawLat==null||rawLon==null||rawLat===''||rawLon==='')return null;const lat=Number(rawLat),lon=Number(rawLon);return Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=90&&Math.abs(lon)<=180?[lon,lat]:null;}
  function distance(a,b){const rad=Math.PI/180,[x1,y1]=a,[x2,y2]=b;const s=Math.sin((y2-y1)*rad/2)**2+Math.cos(y1*rad)*Math.cos(y2*rad)*Math.sin((x2-x1)*rad/2)**2;return 6371000*2*Math.atan2(Math.sqrt(Math.min(1,s)),Math.sqrt(Math.max(0,1-s)));}
  function track(rows,{hours=336,now=Date.now(),maxPoints=120,maxGapHours=12}={}){
    const eligible=rows.filter(p=>coordinates(p)&&Number.isFinite(observed(p))&&observed(p)>=now-Math.min(336,hours)*3600000&&observed(p)<=now+300000&&source(p)!=='global_fishing_watch');
    const output=[];
    for(const [identity,items] of groups(eligible)){
      items.sort((a,b)=>observed(a)-observed(b)||String(a.source_provider||a.provider).localeCompare(String(b.source_provider||b.provider)));
      const unique=[];
      for(const p of items){const previous=unique.at(-1);if(previous&&observed(previous)===observed(p)&&distance(coordinates(previous),coordinates(p))<1){previous.observation_sources=[...new Set([...(previous.observation_sources||[source(previous)]),source(p)])];continue;}unique.push({...p,observation_sources:[source(p)]});}
      // Keep bounded samples across the whole window; never bridge a known gap hidden by sampling.
      const bad=[];for(let n=1;n<unique.length;n++){const a=unique[n-1],b=unique[n],dt=(observed(b)-observed(a))/3600000,speed=dt>0?distance(coordinates(a),coordinates(b))/1852/dt:Infinity;bad[n]=bad[n-1]||0;bad[n]+=a.identity_conflict||b.identity_conflict||dt<=0||dt>maxGapHours||speed>60?1:0;}
      const indexes=unique.length<=maxPoints?unique.map((_,n)=>n):Array.from({length:maxPoints},(_,n)=>Math.round(n*(unique.length-1)/(maxPoints-1)));
      const points=indexes.map(n=>unique[n]),segments=[],gaps=[];
      for(let n=1;n<points.length;n++){const a=points[n-1],b=points[n],dt=(observed(b)-observed(a))/3600000,speed=dt>0?distance(coordinates(a),coordinates(b))/1852/dt:Infinity;
        if(a.identity_conflict||b.identity_conflict||dt<=0||dt>maxGapHours||speed>60||(bad[indexes[n]]||0)>(bad[indexes[n-1]]||0)){gaps.push({from:a,to:b,reason:a.identity_conflict||b.identity_conflict?'identity_conflict':dt>maxGapHours?'observation_gap':'position_conflict'});continue;}
        segments.push({from:a,to:b,source:source(b),identity});
      }
      output.push({identity,points,latest:points.at(-1),segments,gaps,allPoints:unique.length});
    }
    return output;
  }
  function vesselFinder(p){const i=imo(p.imo),m=mmsi(p.mmsi);return i||m?`https://www.vesselfinder.com/vessels/details/${i||m}`:'';}
  function eventContext(event,now=Date.now()){
    const start=Date.parse(event.start),end=Date.parse(event.end),p=event.position||{},lat=Number(p.lat),lon=Number(p.lon);
    if(!Number.isFinite(start)||!Number.isFinite(end)||end<start||end<now-14*86400000||start>now||!Number.isFinite(lat)||!Number.isFinite(lon)||lat<48||lat>80||lon< -25||lon>35)return null;
    const dx=5/(111.32*Math.cos(lat*Math.PI/180)),dy=5/111.32;
    const vessel=event.vessel||{};
    return {v:1,event_id:String(event.id||'').slice(0,160),name:String(vessel.name||'Unbekanntes Schiff').slice(0,120),mmsi:mmsi(vessel.mmsi||vessel.ssvid),imo:imo(vessel.imo),start:new Date(Math.max(start,now-14*86400000)).toISOString(),end:new Date(Math.min(end,now)).toISOString(),bbox:[Math.max(-25,lon-dx),Math.max(48,lat-dy),Math.min(35,lon+dx),Math.min(80,lat+dy)],source:'global_fishing_watch',period_clipped:start<now-14*86400000||end>now};
  }
  return {SOURCES,imo,mmsi,source,sourceLabel,observed,age,groups,coordinates,distance,track,vesselFinder,eventContext};
});
