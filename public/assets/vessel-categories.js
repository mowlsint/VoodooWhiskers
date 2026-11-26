/* Presentation only: category colours and bounded AIS voyage-text evidence. Never changes source products or scores. */
(function(root,factory){const api=factory(root.VesselModel||(typeof require==='function'?require('./vessel-model.js'):null));if(typeof module==='object'&&module.exports)module.exports=api;else root.VesselCategories=api;})(globalThis,VM=>{
  'use strict';
  const DAYS=10,truth=v=>v===true||v===1||/^(true|yes|1)$/i.test(String(v)),text=v=>String(v??'').trim().slice(0,500);
  // Existing repository reference: data/ports_ru_enhanced_aliases.csv. Short ambiguous three-letter aliases are omitted.
  const PORTS=[{"code":"RULED","name":"St. Petersburg","aliases":["RULED","RU LED","ST PETERSBURG","SAINT PETERSBURG","STPETERSBURG","PETERSBURG","SANKT PETERBURG","LENINGRAD"]},{"code":"RUULU","name":"Ust-Luga","aliases":["RUULU","RU ULU","USTLUGA","UST LUGA","UST-LUGA","UST LUGA PORT","UST-LUGA PORT"]},{"code":"RUKGD","name":"Kaliningrad","aliases":["RUKGD","RU KGD","KALININGRAD"]},{"code":"RUBLT","name":"Baltiysk","aliases":["RUBLT","RU BLT","BALTIYSK","BALTIJSK"]},{"code":"RUVYG","name":"Vyborg","aliases":["RUVYG","RU VYG","VYBORG","VIBORG"]},{"code":"RUVYS","name":"Vysotsk","aliases":["RUVYS","RU VYS","VYSOTSK"]},{"code":"RUPRI","name":"Primorsk","aliases":["RUPRI","RU PRI","PRIMORSK","KOIVISTO"]},{"code":"RUBNK","name":"Bronka Port","aliases":["RUBNK","RU BNK","BRONKA","BRONKA PORT"]},{"code":"RUKDT","name":"Kronshtadt","aliases":["RUKDT","RU KDT","KRONSHTADT","KRONSTADT"]},{"code":"RUMMK","name":"Murmansk","aliases":["RUMMK","RU MMK","MURMANSK"]},{"code":"RUARH","name":"Arkhangelsk","aliases":["RUARH","RU ARH","ARKHANGELSK","ARCHANGELSK"]},{"code":"RUKAN","name":"Kandalaksha","aliases":["RUKAN","RU KAN","KANDALAKSHA"]},{"code":"RUSEW","name":"Severodvinsk","aliases":["RUSEW","RU SEW","SEVERODVINSK"]},{"code":"RUSRM","name":"Severomorsk","aliases":["RUSRM","RU SRM","SEVEROMORSK"]},{"code":"RUVIT","name":"Vitino","aliases":["RUVIT","RU VIT","VITINO"]},{"code":"RUSAB","name":"Sabetta","aliases":["RUSAB","RU SAB","SABETTA"]},{"code":"RUDKS","name":"Dikson","aliases":["RUDKS","RU DKS","DIKSON","DIXON"]},{"code":"RUDUD","name":"Dudinka","aliases":["RUDUD","RU DUD","DUDINKA"]},{"code":"RUVAR","name":"Varandey","aliases":["RUVAR","RU VAR","VARANDEY"]},{"code":"RUIKS","name":"Tiksi","aliases":["RUIKS","RU IKS","TIKSI"]},{"code":"RUPWE","name":"Pevek","aliases":["RUPWE","RU PWE","PEVEK"]},{"code":"RUAMV","name":"Amderma","aliases":["RUAMV","RU AMV","AMDERMA"]},{"code":"RUKSA","name":"Kharasavey","aliases":["RUKSA","RU KSA","KHARASAVEY","KHARASAVEY TERMINAL"]},{"code":"RUSLH","name":"Salekhard","aliases":["RUSLH","RU SLH","SALEKHARD"]},{"code":"RUEGV","name":"Egvekinot","aliases":["RUEGV","RU EGV","EGVEKINOT"]},{"code":"RUDYR","name":"Anadyr","aliases":["RUDYR","RU DYR","ANADYR"]},{"code":"RUNVS","name":"Novorossiysk","aliases":["RUNVS","RU NVS","NOVOROSSIYSK"]},{"code":"RUTUA","name":"Tuapse","aliases":["RUTUA","RU TUA","TUAPSE"]},{"code":"RUKZP","name":"Kavkaz","aliases":["RUKZP","RU KZP","KAVKAZ","PORT KAVKAZ"]},{"code":"RURND","name":"Rostov-on-Don","aliases":["RURND","RU RND","ROSTOV","ROSTOV-ON-DON","ROSTOV ON DON","ROSTOV-NA-DONU"]},{"code":"RUAZO","name":"Azov","aliases":["RUAZO","RU AZO","AZOV"]},{"code":"RUASF","name":"Astrakhan","aliases":["RUASF","RU ASF","ASTRAKHAN"]},{"code":"RUVVO","name":"Vladivostok","aliases":["RUVVO","RU VVO","VLADIVOSTOK"]},{"code":"RUVYP","name":"Vostochnyy Port","aliases":["RUVYP","RU VYP","VOSTOCHNYY PORT","VOSTOCHNY","VOSTOCHNY PORT","VOSTOCHNIY"]},{"code":"RUVNN","name":"Vanino","aliases":["RUVNN","RU VNN","VANINO"]},{"code":"RUDKA","name":"De-Kastri","aliases":["RUDKA","RU DKA","DE-KASTRI","DE KASTRI"]}];
  const DEFINITIONS=[
    {id:'sanctioned',label:'Sanctions-listed · confirmed in refreshed authority data',color:'#ef5959'},
    {id:'shadow_fleet',label:'Shadow-fleet list context · separate from sanctions',color:'#ac83ff'},
    {id:'false_flag_watch',label:'False-flag watch · official ship-specific record',color:'#ef76c8'},
    {id:'falseflag_interest',label:'False-flag interest · sourced lead, status shown',color:'#d5a4cf'},
    {id:'behavioral_voi',label:'Behavioural VOI',color:'#29c8b9'},
    {id:'watchlist',label:'Other VOI / watchlist',color:'#62a6ff'},
    {id:'sanctions_review',label:'Sanctions status needs review · not confirmed',color:'#d7c879'},
    {id:'russia_voyage',label:'Recent Russian port visit / current declared destination',color:'#ffa84d'},
    {id:'russian_mmsi',label:'Russian MMSI context — not a voyage finding',color:'#a5b2bd'},
    {id:'neutral_tanker_context',label:'Neutral tanker context',color:'#8d9ca8'},
    {id:'other',label:'Other monitored vessel',color:'#8d9ca8'}
  ];
  const FLAGS=['sanctioned','shadow_fleet','false_flag','false_flag_candidate','false_flag_hard_watch','sanctions_review_required','known_voi_match','behavioral_voi','neutral_tanker_context','sanctions_listed_historical','to_russia_declared','from_russia_confirmed','recent_russian_portcall_confirmed_10d','recent_russian_portcall_unconfirmed_10d'];
  const META=[...FLAGS,'categories','source_list','source_url','source_status','source_last_checked','sanctions_verification_status','sanctions_current_status','false_flag_claimed_flag','false_flag_status','false_flag_evidence_tier','false_flag_evidence_summary','false_flag_source_primary','false_flag_source_url','false_flag_additional_sources','false_flag_event_date','false_flag_record_last_checked','false_flag_current_status','false_flag_temporal_status','flag_watch_context','flag_detected','flag_risk_reason','flag_registry_review_class','recent_ru_portcall_confidence','recent_ru_portcall_basis','recent_ru_portcall_started_at','recent_ru_portcall_until','russia_voyage_type','russia_visit_at','russia_destination_at','russia_visit_port','russia_destination_port'];
  const cats=p=>Array.isArray(p.categories)?p.categories.map(String):text(p.categories).split(/[;,]/).map(s=>s.trim()).filter(Boolean);
  const normalize=v=>text(v).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toUpperCase().replace(/[^A-Z0-9]+/g,' ').trim();
  const PLACE_NAMES=new Set(PORTS.flatMap(p=>[p.name,p.code,...p.aliases]).map(normalize));
  const normalizePort=v=>normalize(v).replace(/ /g,'');
  const PLACE_KEYS=new Set(PORTS.flatMap(p=>[p.name,p.code,...p.aliases]).map(normalizePort).filter(v=>v.length>=4));
  function russianPlace(value){
    const n=normalizePort(value);
    return n.length>=4&&PLACE_KEYS.has(n);
  }
  function parseVoyage(value){
    const raw=text(value),norm=raw.replace(/\s*(?:→|->|=>)\s*/g,'>');
    const parts=norm.split('>').map(s=>s.trim());
    let from=false,to=false,explicit_route=false;
    if(parts.length===2&&parts.every(Boolean)){
      from=russianPlace(parts[0]);to=russianPlace(parts[1]);explicit_route=true;
    }else if(parts.length>1){return {raw,from:false,to:false,related:false,explicit_route:false,basis:'Ambiguous multi-leg voyage'};}
    else{
      const route=norm.match(/^FROM\s+(.+?)\s+TO\s+(.+)$/i);
      if(route){from=russianPlace(route[1]);to=russianPlace(route[2]);explicit_route=true;}
      else if(/^FROM\s+/i.test(norm)){from=russianPlace(norm.replace(/^FROM\s+/i,''));explicit_route=true;}
      else {const target=norm.match(/^TO\s+(.+)$/i);to=russianPlace(target?target[1]:norm);}
    }
    return {raw,from,to,related:from||to,explicit_route,
      basis:explicit_route?'Explicit AIS FROM/TO text':'Explicit AIS destination'};
  }
  const validTime=(ms,now,maxAge)=>Number.isFinite(ms)&&ms>=now-maxAge&&ms<=now+300000;
  const pickPort=(p,keys)=>keys.map(k=>p[k]).find(russianPlace);
  const hasMetadata=p=>cats(p).length||['sanctioned','shadow_fleet','sanctions_review_required','known_voi_match','false_flag','false_flag_hard_watch','false_flag_evidence_summary'].some(k=>Object.hasOwn(p,k));
  function index(rows){
    const groups=VM.groups(rows.filter(p=>VM.coordinates(p)&&Number.isFinite(VM.observed(p)))),byKey=new Map(),aliases=new Map();
    for(const [key,items] of groups){
      byKey.set(key,items.slice().sort((a,b)=>VM.observed(a)-VM.observed(b)));
      for(const p of items){const m=VM.mmsi(p.mmsi);if(m){if(!aliases.has(m))aliases.set(m,new Set());aliases.get(m).add(key);}}
    }
    return {lookup(p){const i=VM.imo(p.imo),m=VM.mmsi(p.mmsi),set=aliases.get(m);const key=i?'imo:'+i:set?.size===1?[...set][0]:'mmsi:'+m;return byKey.get(key)||[];}};
  }
  function boundedRows(p,history){
    const at=VM.observed(p);if(!Number.isFinite(at))return [];
    return history.filter(r=>Number.isFinite(VM.observed(r))&&VM.observed(r)<=at&&VM.observed(r)>=at-DAYS*86400000);
  }
  function enrich(p,history){
    const candidates=boundedRows(p,history).filter(hasMetadata),meta=candidates.at(-1),out={};
    if(meta)for(const k of META)if(meta[k]!=null)out[k]=meta[k];
    return {...out,...p};
  }
  function russia(p,history=[]){
    const now=Date.now(),at=VM.observed(p),evidence=[];
    // A historical voyage text, a Russian MMSI, a remembered destination
    // change and "from Russia" without a dated port visit are NEVER proof.
    const source=VM.sourceLabel(p);
    const visitCandidates=[p,...boundedRows(p,history)];
    let visit=null;
    for(const row of visitCandidates){
      const port=pickPort(row,['russia_visit_port','last_ru_port_unlocode','last_ru_port',
        'last_port_unlocode','last_port_name','port_unlocode','port_name']);
      const when=Date.parse(row.russia_visit_at||row.last_ru_port_date||
        row.last_port_date||row.port_observed_at||row.port_call_at||'');
      const sample=VM.observed(row);
      if(!port||!validTime(when,now,DAYS*86400000))continue;
      if(Number.isFinite(sample)&&when>sample+300000)continue;
      if(!visit||when>visit.when)visit={port,when,row};
    }
    if(visit){
      evidence.push({raw:visit.port,from:true,to:false,basis:'Dated Russian port record (reported, not independently verified)',
        observed_at:new Date(visit.when).toISOString(),source:VM.sourceLabel(visit.row)});
    }
    const destination=parseVoyage(p.destination||p.Destination||p.destination_unlocode||p.next_port);
    const to=destination.to&&validTime(at,now,72*3600000);
    if(to)evidence.push({raw:destination.raw,from:false,to:true,basis:'Current AIS destination declaration',
      observed_at:new Date(at).toISOString(),source});
    const from=Boolean(visit),related=from||to,labels=[];
    if(from)labels.push('← Dated Russian port visit · last 10 days');
    if(to)labels.push('→ Declared destination: Russian port · AIS observation within 72 h');
    return {related,from,to,labels,evidence,legacy:false,basis:'',
      limit:'A dated reported port field is not independent proof of a port call. An AIS destination is a declaration, not confirmation of vessel movement. Older or unclear Russia references are not promoted.'};
  }
  function describe(input,context=null){
    const history=Array.isArray(context)?context:context?.lookup(input)||[],p=enrich(input,history),set=new Set(cats(p)),ru=russia(p,history),ids=new Set();
    if(truth(p.sanctioned))ids.add('sanctioned');
    if(truth(p.shadow_fleet)||set.has('shadowfleet')||set.has('shadow_fleet'))ids.add('shadow_fleet');
    if(truth(p.false_flag_hard_watch)||set.has('false_flag_watch'))ids.add('false_flag_watch');
    if(truth(p.false_flag)||truth(p.false_flag_candidate)||set.has('falseflag_interest'))ids.add('falseflag_interest');
    if(truth(p.behavioral_voi)||set.has('behavioral_voi'))ids.add('behavioral_voi');
    if(truth(p.known_voi_match)||set.has('watchlist')||set.has('watchlist_live'))ids.add('watchlist');
    if(truth(p.sanctions_review_required)||set.has('sanctions_review')||(set.has('sanctions_shadowfleet')&&!ids.has('sanctioned')&&!ids.has('shadow_fleet'))||(truth(p.sanctions_listed_historical)&&!truth(p.sanctioned)))ids.add('sanctions_review');
    if(ru.related)ids.add('russia_voyage');
    if(set.has('russian_mmsi')||VM.mmsi(p.mmsi).startsWith('273'))ids.add('russian_mmsi');
    if(truth(p.neutral_tanker_context)||set.has('neutral_tanker_context'))ids.add('neutral_tanker_context');
    if(!ids.size&&truth(p.is_priority_voi))ids.add('watchlist');
    const categories=DEFINITIONS.filter(c=>ids.has(c.id));
    for(const raw of set)if(!['watchlist','watchlist_live','sanctions','shadow_fleet','sanctions_review','sanctions_shadowfleet','shadowfleet','false_flag_watch','falseflag_interest','behavioral_voi','recent_russian_portcall_10d','russia_voyage','russian_mmsi','neutral_tanker_context'].includes(raw))categories.push({id:'extra:'+raw,label:text(raw).replace(/_/g,' '),color:'#62a6ff'});
    if(!categories.length)categories.push(DEFINITIONS.at(-1));
    const primary=categories[0],secondary=categories.length>1?categories[1]:null;
    return {properties:p,categories,primary,secondary,russia:ru,style:{fillColor:primary.color,color:secondary?.color||primary.color,weight:secondary?3:1.5,fillOpacity:.85}};
  }
  function appendPopup(parent,p,context=null){
    const view=describe(p,context),doc=parent.ownerDocument,box=doc.createElement('div');box.className='vesselCategoryDetails';
    const title=doc.createElement('strong');title.textContent='Categories';box.append(title);
    for(const cat of view.categories){const line=doc.createElement('div');line.textContent=cat.label;line.style.borderLeft='4px solid '+cat.color;line.style.paddingLeft='5px';box.append(line);}
    if(view.russia.related){
      for(const label of view.russia.labels){const line=doc.createElement('strong');line.textContent=label;box.append(line);}
      for(const e of view.russia.evidence){const line=doc.createElement('div');line.textContent=`${e.basis}: ${e.raw} · ${e.observed_at||'time not established'} · ${e.source}`;box.append(line);}
      if(view.russia.legacy){const line=doc.createElement('div');line.textContent='Retained route context: '+(view.russia.basis||'basis not established')+'. A destination change is not proof of departure from Russia.';box.append(line);}
      const limit=doc.createElement('p');limit.textContent=view.russia.limit;box.append(limit);
    }
      if(view.properties.false_flag_evidence_summary){const line=doc.createElement('div');line.textContent='False-flag evidence: '+text(view.properties.false_flag_evidence_summary);box.append(line);}
      if(view.properties.false_flag_status){const line=doc.createElement('div');line.textContent='False-flag status: '+text(view.properties.false_flag_status)+' · recorded '+text(view.properties.false_flag_event_date||'date unavailable')+'. Current flag: '+text(view.properties.false_flag_current_status||'not reverified')+'.';box.append(line);}
    if(view.properties.false_flag_temporal_status){const line=doc.createElement('div');line.textContent='Temporal status: '+text(view.properties.false_flag_temporal_status);box.append(line);}
    if(view.properties.flag_watch_context){const line=doc.createElement('div');line.textContent='Registry context only: '+text(view.properties.flag_detected)+' · '+text(view.properties.flag_risk_reason);box.append(line);}
    parent.append(box);return view;
  }
  return {DEFINITIONS,PORTS,META,DAYS,parseVoyage,index,describe,appendPopup,russia};
});
