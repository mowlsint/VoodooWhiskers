/* Browser-only local projects and time-scoped vessel search. */
(()=>{
  'use strict';
  const Core=globalThis.LocalProjectCore,VM=globalThis.VesselModel,$=id=>document.getElementById(id);
  const COLORS=['#f2f27a','#ffafcc','#72efdd','#ffb86b','#b8a1ff','#a9e88b','#fa859a','#90d9ff'];
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;return n;};
  function attach(map,{getWorkflowRows,getHours,isSar,onNeedHistory,onChange,isProjectMode,onSelectProjectPeriod}){
    let projects=[],active=null,preview=null,dirty=new Set(),model=null,layer=null,searchMarker=null,pending=false,searchTimer=null,appliedPeriod=null;
    const enabled=new Set();
    function status(text){$('localProjectStatus').textContent=text;}
    function selected(){return projects.find(p=>p.id===active);}
    function windowOptions(){return isProjectMode()?{period:appliedPeriod}:{hours:getHours(),now:Date.now()};}
    function workflow(){return isProjectMode()?[]:getWorkflowRows().filter(p=>VM.coordinates(p)&&Number.isFinite(VM.observed(p)));}
    function fillPeriod(period){if(period){$('localPeriodStart').value=period.start.replace(/Z$/,'');$('localPeriodEnd').value=period.end.replace(/Z$/,'');}}
    function preparePeriod(){
      clearTimeout(searchTimer);
      if(!appliedPeriod){appliedPeriod=Core.projectPeriod(visibleProjects(),{savedSelection:true});fillPeriod(appliedPeriod);}
      if(!appliedPeriod){status('No dated local project data available. Load a project or import timestamped observations first.');return false;}
      return true;
    }
    function capture(project){return Core.withWorkflowContext(project,workflow(),windowOptions());}
    function hide(){if(layer){map.removeLayer(layer);layer=null;}if(searchMarker){map.removeLayer(searchMarker);searchMarker=null;}}
    function visibleProjects(){return projects.filter(p=>enabled.has(p.id));}
    function replaceProject(project){projects=projects.map(p=>p.id===project.id?project:p);dirty.add(project.id);}
    function ensureCapacity(next){if(next.length>Core.MAX_PROJECTS||Core.count(next)>Core.MAX_POINTS)throw Error('Session limit: eight projects and 50,000 total points');Core.reconcile(next,workflow(),windowOptions());}
    async function run(task){if(pending)return;pending=true;$('localImportAdd').disabled=true;try{await task();}catch(e){status(e.message);}finally{pending=false;$('localImportAdd').disabled=!preview;}}
    function addSession(){
      const p=Core.newProject($('localProjectName').value||'Local session');ensureCapacity([...projects,p]);projects.push(p);enabled.add(p.id);active=p.id;dirty.add(p.id);refreshControls();onChange();
    }
    function popup(p){
      const box=node('div'),title=node('strong',p.name||p.imo||p.mmsi||p.callsign||'Vessel');box.append(title);
      for(const [label,value] of [['Origin',p.origin==='local'?`Local project: ${p.project_label}`:p._saved_context?'Saved workflow context · local project file':'Workflow observation'],['Context captured UTC',p._context_saved_at],['File',p.file_label],['IMO',p.imo],['MMSI',p.mmsi],['Callsign',p.callsign],['Observed UTC',p.observed_at||p.last_seen_utc],['Position',`${p.latitude}, ${p.longitude}`]])if(value)box.append(node('div',`${label}: ${value}`));
      if(p.identity_conflict)box.append(node('div','Conflicting identity details; automatic track connections are blocked.'));
      if(p._local_conflict)box.append(node('div','Unresolved position conflict. No position has been selected as accepted.'));
      return box;
    }
    function draw(){
      if(layer)map.removeLayer(layer);layer=L.layerGroup().addTo(map);
      let drawn=0,total=0;const color=p=>COLORS[Math.max(0,projects.findIndex(x=>x.id===p.project_id))%COLORS.length];
      for(const item of model.models){
        for(const seg of item.segments){total++;if(drawn>=5000)continue;drawn++;L.polyline([[seg.a.latitude,seg.a.longitude],[seg.b.latitude,seg.b.longitude]],{color:seg.b.origin==='local'?color(seg.b):'#adc1ce',weight:2.5,opacity:.8}).addTo(layer);}
        // A bounded map sample; all observations remain in the project file.
        const sample=item.points.length>300?item.points.filter((p,i)=>i%Math.ceil(item.points.length/300)===0||i===item.points.length-1||item.unresolved.has(p.id)):item.points;
        for(const p of sample){total++;if(drawn>=5000)continue;drawn++;L.circleMarker([p.latitude,p.longitude],{radius:p===item.points.at(-1)?6:3,color:item.unresolved.has(p.id)?'#ff7043':p.origin==='local'?color(p):'#adc1ce',fillOpacity:.85,weight:2}).bindPopup(()=>popup({...p,_local_conflict:item.unresolved.has(p.id)})).addTo(layer);}
      }
      if($('localShowUndated').checked)for(const r of model.routes){total++;if(drawn>=5000)break;drawn++;const box=node('div',`${r.name||r.imo||r.mmsi||r.callsign} · ${r.project_label} · Undated imported route; not a timed position.`);L.polyline(r.coordinates.map(c=>[c[1],c[0]]),{color:color(r),weight:2,dashArray:'4 5'}).bindPopup(box).addTo(layer);}
      $('localOverlayStatus').textContent=`${model.models.length} vessels in this time window · ${model.rows.filter(p=>p.origin==='local').length} imported observations · ${model.rows.filter(p=>p._saved_context).length} saved workflow observations · ${model.routes.length} undated routes.${isProjectMode()?' Historical project period; live workflow positions are hidden.':''}${total>5000?' Map display capped at 5,000 elements; project files retain all points.':''}`;
    }
    function renderConflicts(){
      const root=$('localConflicts');root.replaceChildren();
      const unresolved=model.conflicts.filter(c=>!c.winner);
      $('localConflictSummary').textContent=`${unresolved.length} unresolved position conflicts · ${model.rows.filter(p=>p.identity_conflict).length} observations with ambiguous identifiers`;
      for(const conflict of model.conflicts.slice(0,50)){
        const box=node('div'),label=node('label',`${conflict.identity} · Conflicting position observations within five minutes`),choice=node('select');
        const first=node('option','Unresolved: keep positions separate');first.value='';choice.append(first);
        for(const p of conflict.alternatives){const option=node('option',`${p.origin==='local'?p.project_label+' / '+p.file_label:p._saved_context?'Saved workflow context':'Workflow'} · ${p.observed_at||p.last_seen_utc} · ${Number(p.latitude).toFixed(5)}, ${Number(p.longitude).toFixed(5)}`);option.value=p.id;choice.append(option);}
        choice.value=conflict.winner||'';choice.setAttribute('aria-label','Choose the accepted position for '+conflict.identity);
        choice.addEventListener('change',()=>run(async()=>{
          // Store the same choice in every involved local project; contradictory loaded choices stay unresolved.
          const involved=new Set(conflict.alternatives.map(p=>p.project_id).filter(Boolean));
          const changes=projects.map(p=>{if(!involved.has(p.id))return p;const decisions={...p.decisions};if(choice.value)decisions[conflict.key]=choice.value;else delete decisions[conflict.key];return capture({...p,decisions});});
          ensureCapacity(changes);for(const p of changes)if(involved.has(p.id))replaceProject(p);
          onChange();refreshControls();
        }));label.append(choice);box.append(label);root.append(box);
      }
      if(model.conflicts.length>50)root.append(node('p','Only the first 50 conflict groups are listed. Resolve these or split the project.'));
    }
    function refreshControls(){
      const select=$('localActiveProject');select.replaceChildren();
      for(const p of projects){const option=node('option',p.label+(dirty.has(p.id)?' · unsaved':''));option.value=p.id;select.append(option);}select.value=active||'';
      const p=selected();$('localProjectName').value=p?.label||'';
      $('localProjectSave').disabled=!p;$('localProjectRemove').disabled=!p;$('localProjectRename').disabled=!p;
      const list=$('localProjectList');list.replaceChildren();
      for(const p of projects){const label=node('label'),box=node('input');label.style.borderLeft='4px solid '+COLORS[projects.indexOf(p)%COLORS.length];label.style.paddingLeft='5px';box.type='checkbox';box.checked=enabled.has(p.id);box.addEventListener('change',()=>{if(box.checked)enabled.add(p.id);else enabled.delete(p.id);onChange();});label.append(box,document.createTextNode(` ${p.label} · ${Core.count([p])} points · ${p.workflow_context.observations.length} saved workflow${dirty.has(p.id)?' · unsaved':''}`));list.append(label);}
    }
    function matches(p,q){return [p.name,p.imo,p.mmsi,p.callsign].some(v=>String(v||'').toLowerCase().includes(q));}
    function renderSearch(){
      const q=$('vesselSearch').value.trim().toLowerCase();if(!q)return;
      const root=$('vesselList');root.replaceChildren();
      if(isSar()){root.append(node('p','Vessel search is paused during historical SAR comparison.'));return;}
      const candidates=[];
      for(const item of model?.models||[])if(item.points.some(p=>matches(p,q))){
        const latest=item.points.at(-1);
        if(item.unresolved.has(latest.id))candidates.push(...item.points.filter(p=>item.unresolved.has(p.id)&&VM.observed(p)>=VM.observed(latest)-300000).map(p=>({...p,_local_conflict:true})));
        else candidates.push(latest);
      }
      const rows=workflow().filter(p=>!model?.managed.has(Core.fingerprint(p)));
      if(!isProjectMode())for(const item of VM.track(rows,windowOptions()))if(item.points.some(p=>matches(p,q)))candidates.push(item.latest);
      candidates.sort((a,b)=>VM.observed(b)-VM.observed(a));
      if(!candidates.length){root.append(node('p','No matching vessel observations in the selected time window.'));return;}
      root.append(node('p',`${candidates.length} matching position results in the selected time window${candidates.length>50?' · first 50 shown':''}`));
      for(const p of candidates.slice(0,50)){
        const button=node('button',`${p.name||p.imo||p.mmsi||p.callsign} · ${p.observed_at||p.last_seen_utc} · ${p.origin==='local'?'Local project':p._saved_context?'Saved workflow context':'Workflow'}${p._local_conflict?' · Unresolved position':''}`);button.type='button';button.className='vesselListItem';
        button.addEventListener('click',()=>{if(searchMarker)map.removeLayer(searchMarker);searchMarker=L.circleMarker([p.latitude,p.longitude],{radius:9,color:'#fff078',weight:3,fillOpacity:.3}).bindPopup(()=>popup(p)).addTo(map);map.setView([p.latitude,p.longitude],11);searchMarker.openPopup();});root.append(button);
      }
    }
    function refresh(){
      if(searchMarker){map.removeLayer(searchMarker);searchMarker=null;}
      model=null;
      if(isSar()){hide();$('localOverlayStatus').textContent='Local projects are hidden during historical SAR comparison.';renderSearch();return;}
      try{model=Core.reconcile(visibleProjects(),workflow(),windowOptions());draw();renderConflicts();}
      catch(e){hide();status('Local overlay unavailable: '+e.message);}
    }
    $('localNewSession').addEventListener('click',()=>run(addSession));
    $('localActiveProject').addEventListener('change',()=>{active=$('localActiveProject').value;refreshControls();});
    $('localProjectRename').addEventListener('click',()=>run(async()=>{const p=selected();if(p){replaceProject({...p,label:$('localProjectName').value.trim().slice(0,180)||'Local session'});refreshControls();onChange();}}));
    $('localProjectSave').addEventListener('click',()=>run(async()=>{
      const p=selected();if(!p)return;const next=capture({...p,project_period:isProjectMode()?appliedPeriod:p.project_period});ensureCapacity(projects.map(x=>x.id===p.id?next:x));
      const content=Core.serialize(next),url=URL.createObjectURL(new Blob([content],{type:'application/json'})),a=node('a');a.href=url;a.download=(p.label.replace(/[^a-z0-9_-]/gi,'_')||'local-session')+'.vwproject.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);replaceProject(next);dirty.delete(p.id);refreshControls();onChange();status(`Project download requested with ${next.workflow_context.observations.length} saved workflow observations. Keep the downloaded file; this page has no automatic persistence.`);
    }));
    $('localProjectRemove').addEventListener('click',()=>run(async()=>{const p=selected();if(!p||!confirm(`Remove ${p.label} from this page?${dirty.has(p.id)?' Unsaved changes will be lost.':''}`))return;projects=projects.filter(x=>x.id!==p.id);enabled.delete(p.id);dirty.delete(p.id);active=projects[0]?.id||null;refreshControls();onChange();}));
    $('localProjectFiles').addEventListener('change',event=>run(async()=>{
      const next=[...projects],loaded=[];
      try{for(const file of event.target.files){if(file.size>Core.MAX_PROJECT_BYTES)throw Error('Project exceeds 20 MiB');const p=Core.loadProject(await file.text());if(next.some(x=>x.id===p.id))throw Error('This project is already loaded. Remove it before loading another version.');next.push(p);loaded.push(p);}
        ensureCapacity(next);projects=next;for(const p of loaded)enabled.add(p.id);active=loaded.at(-1)?.id||active;
        if(!appliedPeriod)fillPeriod(Core.projectPeriod(visibleProjects(),{savedSelection:true}));
        const historyReady=isProjectMode()?false:await onNeedHistory();refreshControls();onChange();status(`${loaded.length} project files loaded locally. Saved workflow context remains available from these files.${loaded.some(p=>!p.workflow_context.captured_at)?' A project has no saved workflow context; expired source observations cannot be restored.':''}${historyReady||isProjectMode()?'':' Current workflow history unavailable.'}`);
      }finally{event.target.value='';}
    }));
    $('localImportPreview').addEventListener('click',()=>run(async()=>{
      preview=null;$('localImportIssues').replaceChildren();const file=$('localTrackFile').files[0];if(!file)throw Error('Choose a CSV or KML file');if(file.size>Core.MAX_FILE)throw Error('Import exceeds 10 MiB');
      const defaults=Object.fromEntries(['mmsi','imo','callsign','name'].map(k=>[k,$('localDefault_'+k).value]));
      const options={defaults,assumeUTC:$('localAssumeUtc').checked},text=await file.text();
      const format=/\.kml$/i.test(file.name)?'kml':/\.csv$/i.test(file.name)?'csv':null;if(!format)throw Error('Choose a .csv or .kml file');
      const parsed=format==='kml'?Core.parseKML(text,options):Core.parseCSV(text,options);
      for(const issue of parsed.issues)$('localImportIssues').append(node('li',issue));
      status(`${parsed.observations.length} timestamped observations · ${parsed.routes.length} undated routes · ${parsed.rejected} rejected rows/geometries.${parsed.columns?' Detected columns: '+parsed.columns.join(', '):''}`);
      if(parsed.observations.length||parsed.routes.length)preview={parsed,label:file.name,format};
    }));
    $('localImportAdd').addEventListener('click',()=>run(async()=>{
      if(!preview)return;if(!selected())addSession();const next=Core.addImport(selected(),preview.parsed,preview.label,preview.format);
      const historyReady=isProjectMode()?false:await onNeedHistory(),captured=capture(next);
      const candidates=projects.map(p=>p.id===active?captured:p);ensureCapacity(candidates);replaceProject(captured);preview=null;if(!appliedPeriod)fillPeriod(Core.projectPeriod(visibleProjects()));refreshControls();onChange();status('Imported into the active local project. Download the project to retain this session.'+(historyReady||isProjectMode()?'':' Current workflow history unavailable; saved context and loaded observations remain usable.'));
    }));
    $('localTrackFile').addEventListener('change',()=>{preview=null;$('localImportAdd').disabled=true;});
    for(const el of document.querySelectorAll('[data-local-import-option]'))el.addEventListener('input',()=>{preview=null;$('localImportAdd').disabled=true;});
    $('localShowUndated').addEventListener('change',onChange);
    $('localApplyProjectPeriod').addEventListener('click',()=>run(async()=>{const period=Core.validatePeriod({start:$('localPeriodStart').value+'Z',end:$('localPeriodEnd').value+'Z'});appliedPeriod=period;await onSelectProjectPeriod();}));
    $('localUseProjectDates').addEventListener('click',()=>run(async()=>{const period=Core.projectPeriod(visibleProjects());if(!period)throw Error('No dated observations in enabled projects');appliedPeriod=period;fillPeriod(period);await onSelectProjectPeriod();}));
    $('localFitProjects').addEventListener('click',()=>{if(layer?.getLayers().length){const bounds=L.featureGroup(layer.getLayers()).getBounds();if(bounds.isValid())map.fitBounds(bounds.pad(.1),{maxZoom:12});}});
    $('vesselSearch').addEventListener('input',()=>{clearTimeout(searchTimer);if($('vesselSearch').value.trim()&&!isProjectMode())searchTimer=setTimeout(()=>onNeedHistory().then(onChange).catch(()=>status('Historical workflow positions unavailable; search uses the loaded observations.')),180);});
    $('vesselSearch').addEventListener('keydown',event=>{if(event.key==='Enter'){$('vesselList').querySelector('button')?.click();event.preventDefault();}});
    addEventListener('beforeunload',event=>{if(dirty.size){event.preventDefault();event.returnValue='';}});
    refreshControls();
    return {refresh,renderSearch,preparePeriod,getPeriod:()=>appliedPeriod,filterWorkflowRows:rows=>model?rows.filter(p=>!model.managed.has(Core.fingerprint(p))):rows};
  }
  globalThis.LocalVesselProjects={attach};
})();
