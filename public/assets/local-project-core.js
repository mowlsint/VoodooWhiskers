/* Local AIS projects. No network, storage, provider calls or generated-product writes. */
(function(root,factory){const api=factory(root.VesselModel||(typeof require==='function'?require('./vessel-model.js'):null));if(typeof module==='object'&&module.exports)module.exports=api;else root.LocalProjectCore=api;})(globalThis,VM=>{
  'use strict';
  const SCHEMA='VOODOO_LOCAL_PROJECT_V1',MAX_POINTS=50000,MAX_PROJECTS=8,MAX_FILE=10*1024*1024,MAX_PROJECT_BYTES=20*1024*1024;
  const clean=v=>String(v??'').trim().slice(0,180);
  const field=v=>String(v??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const ALIASES={mmsi:['mmsi','maritimemobileserviceidentity'],imo:['imo','imonumber','imono'],callsign:['callsign','call','rufzeichen'],name:['name','shipname','vesselname','vessel','schiffsname'],latitude:['lat','latitude','breite','breitengrad'],longitude:['lon','lng','long','longitude','lange','langengrad'],observed_at:['timestamp','time','datetime','dateandtime','positiontime','positiontimestamp','timestamputc','datetimeutc','timeutc','observedat','lastseenutc'],date:['date','datum'],sog:['sog','speed','speedoverground'],cog:['cog','course','courseoverground']};
  const canonical=name=>Object.keys(ALIASES).find(k=>ALIASES[k].includes(field(name)));
  const id=()=>globalThis.crypto?.randomUUID?.()||`local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  function timestamp(value,utc=false){
    let s=String(value??'').trim();
    if(/^\d{10}(?:\.\d+)?$/.test(s))s=new Date(Number(s)*1000).toISOString();
    else if(/^\d{13}$/.test(s))s=new Date(Number(s)).toISOString();
    else {s=s.replace(' ','T');if(!/(Z|[+-]\d\d:\d\d)$/i.test(s)){if(!utc)throw Error('Timestamp needs a UTC offset or the explicit UTC option');s+='Z';}}
    if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(Z|[+-]\d\d:\d\d)$/i.test(s))throw Error('Invalid timestamp; use ISO date/time');
    const ms=Date.parse(s);if(!Number.isFinite(ms))throw Error('Invalid timestamp');
    // Date.parse normalizes February 30; reject that rather than inventing an observation day.
    const day=s.slice(0,10),d=new Date(day+'T00:00:00Z');if(!Number.isFinite(d.getTime())||d.toISOString().slice(0,10)!==day)throw Error('Invalid calendar date');
    return new Date(ms).toISOString();
  }
  function number(value){const s=String(value??'').trim().replace(',','.');if(!s||!/^[-+]?\d+(?:\.\d+)?$/.test(s))throw Error('Invalid coordinate');return Number(s);}
  function identity(row,defaults={}){
    const out={};for(const key of ['imo','mmsi','callsign','name'])out[key]=clean(row[key]||defaults[key]);
    if(out.imo&&!VM.imo(out.imo))throw Error('Invalid IMO number/check digit');
    if(out.mmsi&&!VM.mmsi(out.mmsi))throw Error('Invalid MMSI');
    out.imo=out.imo?VM.imo(out.imo):'';out.mmsi=out.mmsi?VM.mmsi(out.mmsi):'';out.callsign=out.callsign.toUpperCase();
    if(!out.imo&&!out.mmsi&&!out.callsign)throw Error('MMSI, IMO or callsign required; names alone cannot identify a vessel');
    return out;
  }
  function observation(row,defaults={},utc=false){
    const p={...identity(row,defaults),latitude:number(row.latitude),longitude:number(row.longitude),observed_at:timestamp(row.observed_at,utc)};
    if(!VM.coordinates(p))throw Error('Coordinate outside latitude/longitude range');
    for(const key of ['sog','cog'])if(row[key]!=null&&String(row[key]).trim()!==''){const n=number(row[key]);if(n<0||n>(key==='sog'?150:360))throw Error('Invalid '+key);p[key]=n;}
    return p;
  }
  function csvRows(text,delimiter){
    const rows=[];let row=[],cell='',quoted=false,closed=false;
    function push(){row.push(cell.trim());cell='';closed=false;}
    for(let i=0;i<text.length;i++){
      const c=text[i];
      if(quoted){if(c==='"'){if(text[i+1]==='"'){cell+='"';i++;}else{quoted=false;closed=true;}}else cell+=c;continue;}
      if(c==='"'){if(cell.trim()||closed)throw Error('Invalid CSV quote');cell='';quoted=true;}
      else if(c===delimiter)push();
      else if(c==='\n'||c==='\r'){if(c==='\r'&&text[i+1]==='\n')i++;push();if(row.some(Boolean))rows.push(row);row=[];if(rows.length>MAX_POINTS+30)throw Error('CSV exceeds the point limit');}
      else {if(closed&&!/\s/.test(c))throw Error('Unexpected text after CSV quote');cell+=c;}
    }
    if(quoted)throw Error('Unclosed CSV quote');push();if(row.some(Boolean))rows.push(row);return rows;
  }
  function parseCSV(text,{defaults={},assumeUTC=false}={}){
    if(text.length>MAX_FILE)throw Error('CSV exceeds 10 MiB');text=text.replace(/^\uFEFF/,'');
    let rows,header,index;
    for(const separator of [',',';','\t']){
      let candidate;try{candidate=csvRows(text,separator);}catch(e){if(separator===','&&text.includes(',')&&!text.includes(';')&&!text.includes('\t'))throw e;continue;}
      const at=candidate.slice(0,25).findIndex(row=>row.map(canonical).includes('latitude')&&row.map(canonical).includes('longitude'));
      if(at>=0){rows=candidate;header=rows[at].map(canonical);index=at;break;}
    }
    if(!rows)throw Error('CSV needs latitude and longitude columns (comma, semicolon or tab separated)');
    const known=header.filter(Boolean);if(new Set(known).size!==known.length)throw Error('Ambiguous duplicate CSV columns');
    const observations=[],issues=[];let rejected=0;
    const utc=assumeUTC||rows[index].some(v=>canonical(v)==='observed_at'&&/utc/i.test(v));
    for(let i=index+1;i<rows.length;i++){
      try{
        if(rows[i].length!==header.length)throw Error('Column count differs from header');
        const item={};header.forEach((key,n)=>{if(key)item[key]=rows[i][n];});
        if(item.date&&item.observed_at&&!/^\d{4}-\d\d-\d\d/.test(item.observed_at))item.observed_at=item.date+'T'+item.observed_at;
        observations.push(observation(item,defaults,utc));
      }catch(e){rejected++;if(issues.length<10)issues.push(`Row ${i+1}: ${e.message}`);}
    }
    return {observations,routes:[],rejected,issues,columns:rows[index]};
  }
  const elements=(node,name)=>Array.from(node.getElementsByTagName('*')).filter(n=>n.localName===name);
  function parseKML(text,{defaults={},assumeUTC=false,Parser=globalThis.DOMParser}={}){
    if(text.length>MAX_FILE)throw Error('KML exceeds 10 MiB');
    if(/<!DOCTYPE|<!ENTITY/i.test(text))throw Error('KML DTDs/entities are not supported');
    if(!Parser)throw Error('KML requires the browser XML parser');
    const doc=new Parser().parseFromString(text,'application/xml');
    if(elements(doc,'parsererror').length||doc.documentElement?.localName!=='kml')throw Error('Invalid KML XML');
    if(elements(doc,'NetworkLink').length)throw Error('Linked KML is not loaded; import a self-contained file');
    const observations=[],routes=[],issues=[];let rejected=0,vertices=0;
    for(const placemark of elements(doc,'Placemark')){
      const meta={name:elements(placemark,'name')[0]?.textContent||''};
      for(const n of [...elements(placemark,'Data'),...elements(placemark,'SimpleData')]){
        const key=canonical(n.getAttribute('name'));if(key)meta[key]=n.localName==='Data'?(elements(n,'value')[0]?.textContent||''):n.textContent;
      }
      const when=elements(placemark,'TimeStamp')[0];if(when&&!meta.observed_at)meta.observed_at=elements(when,'when')[0]?.textContent;
      try{
        const ident=identity(meta,defaults),tracks=elements(placemark,'Track');
        for(const track of tracks){
          const times=elements(track,'when'),coords=elements(track,'coord');
          if(times.length!==coords.length||!times.length)throw Error('Track times and coordinates must have matching lengths');
          if(times.length+observations.length+vertices>MAX_POINTS)throw Error('KML exceeds the point limit');
          // Validate a whole geometry before adding it; malformed tracks are not partially imported.
          const points=coords.map((c,i)=>{const xy=c.textContent.trim().split(/\s+/);return observation({...ident,longitude:xy[0],latitude:xy[1],observed_at:times[i].textContent},defaults,assumeUTC);});
          observations.push(...points);
        }
        for(const point of elements(placemark,'Point')){
          const xy=elements(point,'coordinates')[0]?.textContent.trim().split(',')||[];
          observations.push(observation({...ident,...meta,longitude:xy[0],latitude:xy[1]},defaults,assumeUTC));
        }
        for(const line of elements(placemark,'LineString')){
          const coords=(elements(line,'coordinates')[0]?.textContent||'').trim().split(/\s+/).filter(Boolean).map(v=>{const c=v.split(',').map(number);if(c.length<2||!VM.coordinates({longitude:c[0],latitude:c[1]}))throw Error('Invalid route coordinate');return c.slice(0,2);});
          if(coords.length<2)throw Error('Route needs at least two coordinates');
          routes.push({...ident,coordinates:coords});vertices+=coords.length;
        }
      }catch(e){rejected++;if(issues.length<10)issues.push(`${clean(meta.name)||'Placemark'}: ${e.message}`);}
      if(observations.length+vertices>MAX_POINTS)throw Error('KML exceeds the point limit');
    }
    return {observations,routes,rejected,issues};
  }
  function newProject(label='Local session'){return {schema:SCHEMA,id:id(),label:clean(label)||'Local session',created_at:new Date().toISOString(),imports:[],decisions:{}};}
  function count(projects){return projects.reduce((sum,p)=>sum+p.imports.reduce((n,f)=>n+f.observations.length+f.routes.reduce((m,r)=>m+r.coordinates.length,0),0),0);}
  function addImport(project,parsed,label,format){
    if(!parsed.observations.length&&!parsed.routes.length)throw Error('No usable observations or routes; review the import fields');
    const file={id:id(),label:clean(label),format,observations:parsed.observations,routes:parsed.routes};
    const next={...project,imports:[...project.imports,file]};if(count([next])>MAX_POINTS)throw Error('Project exceeds 50,000 points');
    serialize(next);return next;
  }
  function serialize(project){const text=JSON.stringify(project,null,2);if(new TextEncoder().encode(text).length>MAX_PROJECT_BYTES)throw Error('Project exceeds 20 MiB');return text;}
  function loadProject(text){
    if(new TextEncoder().encode(text).length>MAX_PROJECT_BYTES)throw Error('Project exceeds 20 MiB');
    const raw=JSON.parse(text);
    if(raw.schema!==SCHEMA||typeof raw.id!=='string'||!raw.id||raw.id.length>100||!Array.isArray(raw.imports)||raw.imports.length>500)throw Error('Unsupported project file');
    const p={schema:SCHEMA,id:raw.id,label:clean(raw.label),created_at:timestamp(raw.created_at),imports:[],decisions:{}};
    const ids=new Set();
    for(const file of raw.imports){
      if(typeof file.id!=='string'||!file.id||file.id.length>100||ids.has(file.id)||!Array.isArray(file.observations)||file.observations.length>MAX_POINTS||!Array.isArray(file.routes))throw Error('Invalid project import');ids.add(file.id);
      const f={id:file.id,label:clean(file.label),format:clean(file.format),observations:file.observations.map(r=>observation(r)),routes:[]};
      for(const r of file.routes){if(!Array.isArray(r.coordinates)||r.coordinates.length<2||r.coordinates.length>MAX_POINTS)throw Error('Invalid project route');const coordinates=r.coordinates.map(c=>{if(!Array.isArray(c)||c.length!==2||!VM.coordinates({longitude:c[0],latitude:c[1]}))throw Error('Invalid project coordinate');return c.map(number);});f.routes.push({...identity(r),coordinates});}
      p.imports.push(f);if(count([p])>MAX_POINTS)throw Error('Project exceeds 50,000 points');
    }
    if(raw.decisions&&typeof raw.decisions==='object'&&!Array.isArray(raw.decisions))for(const [key,value] of Object.entries(raw.decisions)){if(key.length<20000&&typeof value==='string'&&value.length<1000)p.decisions[key]=value;}
    return p;
  }
  function fingerprint(p){return JSON.stringify([VM.imo(p.imo),VM.mmsi(p.mmsi),clean(p.callsign).toUpperCase(),VM.observed(p),VM.coordinates(p)]);}
  function indexed(projects,workflow){
    const rows=workflow.filter(p=>VM.coordinates(p)&&Number.isFinite(VM.observed(p))&&VM.source(p)!=='global_fishing_watch').map(p=>({...p,id:'workflow:'+fingerprint(p),origin:'workflow'}));
    const routes=[];
    for(const project of projects)for(const f of project.imports){
      f.observations.forEach((p,n)=>rows.push({...p,id:JSON.stringify([project.id,f.id,n]),origin:'local',project_id:project.id,project_label:project.label,file_label:f.label}));
      f.routes.forEach(r=>routes.push({...r,project_id:project.id,project_label:project.label,file_label:f.label}));
    }
    const mmsiImo=new Map();
    for(const p of rows){const i=VM.imo(p.imo),m=VM.mmsi(p.mmsi);if(i&&m){if(!mmsiImo.has(m))mmsiImo.set(m,new Set());mmsiImo.get(m).add(i);}}
    function strong(p){const i=VM.imo(p.imo),m=VM.mmsi(p.mmsi),aliases=mmsiImo.get(m);return i?'imo:'+i:m?(aliases?.size===1?'imo:'+[...aliases][0]:'mmsi:'+m):'';}
    const calls=new Map();for(const p of rows){const c=clean(p.callsign).toUpperCase(),s=strong(p);if(c&&s){if(!calls.has(c))calls.set(c,new Set());calls.get(c).add(s);}}
    function bind(p){const c=clean(p.callsign).toUpperCase(),aliases=calls.get(c);return {...p,identity:strong(p)||(aliases?.size===1?[...aliases][0]:'callsign:'+c),identity_conflict:Boolean(mmsiImo.get(VM.mmsi(p.mmsi))?.size>1||(!strong(p)&&aliases?.size>1))};}
    const bound=rows.map(bind),localIdentities=new Set(bound.filter(p=>p.origin==='local').map(p=>p.identity));
    return {rows:bound.filter(p=>localIdentities.has(p.identity)),routes:routes.map(bind),localIdentities};
  }
  function reconcile(projects,workflow,{hours=336,now=Date.now()}={}){
    const data=indexed(projects,workflow),groups=new Map(),conflicts=[],managed=new Set();
    data.rows=data.rows.filter(p=>VM.observed(p)>=now-hours*3600000&&VM.observed(p)<=now+300000);
    for(const p of data.rows){if(!groups.has(p.identity))groups.set(p.identity,[]);groups.get(p.identity).push(p);if(p.origin==='workflow')managed.add(fingerprint(p));}
    const decisions=new Map();for(const project of projects)for(const [key,value] of Object.entries(project.decisions||{})){if(!decisions.has(key))decisions.set(key,new Set());decisions.get(key).add(value);}
    const models=[];let comparisons=0;
    for(const [identity,rows] of groups){
      const sorted=[...new Map(rows.map(p=>[p.id,p])).values()].sort((a,b)=>VM.observed(a)-VM.observed(b)||a.id.localeCompare(b.id));
      const parent=new Map();const find=x=>{let n=x;while(parent.get(n)!==n)n=parent.get(n);return n;};
      for(let i=0;i<sorted.length;i++)for(let j=i+1;j<sorted.length&&VM.observed(sorted[j])-VM.observed(sorted[i])<=300000;j++){
        if(++comparisons>2000000)throw Error('Too many overlapping observations; split the input files');
        const a=sorted[i],b=sorted[j];if(a.origin==='workflow'&&b.origin==='workflow')continue;
        const distance=VM.distance(VM.coordinates(a),VM.coordinates(b));
        if(distance<=(VM.observed(a)===VM.observed(b)?1:1852))continue;
        if(!parent.has(a.id))parent.set(a.id,a.id);if(!parent.has(b.id))parent.set(b.id,b.id);parent.set(find(a.id),find(b.id));
      }
      const sets=new Map();for(const p of sorted)if(parent.has(p.id)){const key=find(p.id);if(!sets.has(key))sets.set(key,[]);sets.get(key).push(p);}
      const excluded=new Set(),unresolved=new Set();
      for(const alternatives of sets.values()){
        const key=JSON.stringify(alternatives.map(p=>p.id).sort()),choices=decisions.get(key),winner=choices?.size===1?[...choices][0]:null;
        const resolved=alternatives.some(p=>p.id===winner);
        conflicts.push({key,identity,alternatives,winner:resolved?winner:null});
        for(const p of alternatives)if(resolved&&p.id!==winner)excluded.add(p.id);else if(!resolved)unresolved.add(p.id);
      }
      const points=sorted.filter(p=>!excluded.has(p.id)),segments=[],gaps=[];
      for(let i=1;i<points.length;i++){
        const a=points[i-1],b=points[i],hours=(VM.observed(b)-VM.observed(a))/3600000,distance=VM.distance(VM.coordinates(a),VM.coordinates(b));
        if(hours===0&&distance<1)continue;
        if(unresolved.has(a.id)||unresolved.has(b.id)||a.identity_conflict||b.identity_conflict||hours<=0||hours>12||distance/1852/hours>60){gaps.push({a,b});continue;}
        segments.push({a,b});
      }
      models.push({identity,points,segments,gaps,unresolved});
    }
    return {...data,models,conflicts,managed};
  }
  return {SCHEMA,MAX_POINTS,MAX_PROJECTS,MAX_FILE,MAX_PROJECT_BYTES,parseCSV,parseKML,newProject,addImport,serialize,loadProject,count,reconcile,fingerprint,timestamp};
});
