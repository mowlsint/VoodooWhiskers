/* Presentation only: category colours and bounded AIS voyage-text evidence. Never changes source products or scores. */
(function(root,factory){const api=factory(root.VesselModel||(typeof require==='function'?require('./vessel-model.js'):null));if(typeof module==='object'&&module.exports)module.exports=api;else root.VesselCategories=api;})(globalThis,VM=>{
  'use strict';
  const DAYS=10,truth=v=>v===true||v===1||/^(true|yes|1)$/i.test(String(v)),text=v=>String(v??'').trim().slice(0,500);
  // Existing repository reference: data/ports_ru_enhanced_aliases.csv. Short ambiguous three-letter aliases are omitted.
  const PORTS=[{"code":"RULED","name":"St. Petersburg","aliases":["RULED","RU LED","ST PETERSBURG","SAINT PETERSBURG","STPETERSBURG","PETERSBURG","SANKT PETERBURG","LENINGRAD"]},{"code":"RUULU","name":"Ust-Luga","aliases":["RUULU","RU ULU","USTLUGA","UST LUGA","UST-LUGA","UST LUGA PORT","UST-LUGA PORT"]},{"code":"RUKGD","name":"Kaliningrad","aliases":["RUKGD","RU KGD","KALININGRAD"]},{"code":"RUBLT","name":"Baltiysk","aliases":["RUBLT","RU BLT","BALTIYSK","BALTIJSK"]},{"code":"RUVYG","name":"Vyborg","aliases":["RUVYG","RU VYG","VYBORG","VIBORG"]},{"code":"RUVYS","name":"Vysotsk","aliases":["RUVYS","RU VYS","VYSOTSK"]},{"code":"RUPRI","name":"Primorsk","aliases":["RUPRI","RU PRI","PRIMORSK","KOIVISTO"]},{"code":"RUBNK","name":"Bronka Port","aliases":["RUBNK","RU BNK","BRONKA","BRONKA PORT"]},{"code":"RUKDT","name":"Kronshtadt","aliases":["RUKDT","RU KDT","KRONSHTADT","KRONSTADT"]},{"code":"RUMMK","name":"Murmansk","aliases":["RUMMK","RU MMK","MURMANSK"]},{"code":"RUARH","name":"Arkhangelsk","aliases":["RUARH","RU ARH","ARKHANGELSK","ARCHANGELSK"]},{"code":"RUKAN","name":"Kandalaksha","aliases":["RUKAN","RU KAN","KANDALAKSHA"]},{"code":"RUSEW","name":"Severodvinsk","aliases":["RUSEW","RU SEW","SEVERODVINSK"]},{"code":"RUSRM","name":"Severomorsk","aliases":["RUSRM","RU SRM","SEVEROMORSK"]},{"code":"RUVIT","name":"Vitino","aliases":["RUVIT","RU VIT","VITINO"]},{"code":"RUSAB","name":"Sabetta","aliases":["RUSAB","RU SAB","SABETTA"]},{"code":"RUDKS","name":"Dikson","aliases":["RUDKS","RU DKS","DIKSON","DIXON"]},{"code":"RUDUD","name":"Dudinka","aliases":["RUDUD","RU DUD","DUDINKA"]},{"code":"RUVAR","name":"Varandey","aliases":["RUVAR","RU VAR","VARANDEY"]},{"code":"RUIKS","name":"Tiksi","aliases":["RUIKS","RU IKS","TIKSI"]},{"code":"RUPWE","name":"Pevek","aliases":["RUPWE","RU PWE","PEVEK"]},{"code":"RUAMV","name":"Amderma","aliases":["RUAMV","RU AMV","AMDERMA"]},{"code":"RUKSA","name":"Kharasavey","aliases":["RUKSA","RU KSA","KHARASAVEY","KHARASAVEY TERMINAL"]},{"code":"RUSLH","name":"Salekhard","aliases":["RUSLH","RU SLH","SALEKHARD"]},{"code":"RUEGV","name":"Egvekinot","aliases":["RUEGV","RU EGV","EGVEKINOT"]},{"code":"RUDYR","name":"Anadyr","aliases":["RUDYR","RU DYR","ANADYR"]},{"code":"RUNVS","name":"Novorossiysk","aliases":["RUNVS","RU NVS","NOVOROSSIYSK"]},{"code":"RUTUA","name":"Tuapse","aliases":["RUTUA","RU TUA","TUAPSE"]},{"code":"RUKZP","name":"Kavkaz","aliases":["RUKZP","RU KZP","KAVKAZ","PORT KAVKAZ"]},{"code":"RURND","name":"Rostov-on-Don","aliases":["RURND","RU RND","ROSTOV","ROSTOV-ON-DON","ROSTOV ON DON","ROSTOV-NA-DONU"]},{"code":"RUAZO","name":"Azov","aliases":["RUAZO","RU AZO","AZOV"]},{"code":"RUASF","name":"Astrakhan","aliases":["RUASF","RU ASF","ASTRAKHAN"]},{"code":"RUVVO","name":"Vladivostok","aliases":["RUVVO","RU VVO","VLADIVOSTOK"]},{"code":"RUVYP","name":"Vostochnyy Port","aliases":["RUVYP","RU VYP","VOSTOCHNYY PORT","VOSTOCHNY","VOSTOCHNY PORT","VOSTOCHNIY"]},{"code":"RUVNN","name":"Vanino","aliases":["RUVNN","RU VNN","VANINO"]},{"code":"RUDKA","name":"De-Kastri","aliases":["RUDKA","RU DKA","DE-KASTRI","DE KASTRI"]}];
  const DEFINITIONS=[
    {id:'sanctioned',label:'Sanctions-listed vessel',color:'#ef5959'},
    {id:'shadow_fleet',label:'Shadow-fleet list context',color:'#ac83ff'},
    {id:'false_flag_watch',label:'False-flag watch — stronger evidence',color:'#ef76c8'},
    {id:'falseflag_interest',label:'False-flag interest — review lead',color:'#d5a4cf'},
    {id:'behavioral_voi',label:'Behavioural VOI',color:'#29c8b9'},
    {id:'watchlist',label:'Other VOI / watchlist',color:'#62a6ff'},
    {id:'sanctions_review',label:'Sanctions / shadow-fleet entry — status needs review',color:'#d7c879'},
    {id:'russia_voyage',label:'Russia-related voyage',color:'#ffa84d'},
    {id:'russian_mmsi',label:'Russian MMSI context — not a voyage finding',color:'#a5b2bd'},
    {id:'neutral_tanker_context',label:'Neutral tanker context',color:'#8d9ca8'},
    {id:'other',label:'Other monitored vessel',color:'#8d9ca8'}
  ];
  const FLAGS=['sanctioned','shadow_fleet','false_flag','false_flag_candidate','false_flag_hard_watch','known_voi_match','behavioral_voi','neutral_tanker_context','sanctions_listed_historical','to_russia_declared','from_russia_confirmed','recent_russian_portcall_confirmed_10d','recent_russian_portcall_unconfirmed_10d'];
  const META=[...FLAGS,'categories','source_list','source_url','source_status','source_last_checked','sanctions_verification_status','recent_ru_portcall_confidence','recent_ru_portcall_basis','recent_ru_portcall_started_at','recent_ru_portcall_until'];
  const cats=p=>Array.isArray(p.categories)?p.categories.map(String):text(p.categories).split(/[;,]/).map(s=>s.trim()).filter(Boolean);
  const normalize=v=>text(v).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toUpperCase().replace(/[^A-Z0-9]+/g,' ').trim();
  const PLACE_NAMES=new Set(PORTS.flatMap(p=>[p.name,p.code,...p.aliases]).map(normalize));
  const MENTION_NAMES=[...PLACE_NAMES].filter(n=>n.length>=4);
  const negated=v=>/\bNO (?:RUSSIAN?|RUSSIA|RUS)(?: |$)|\bNO (?:LINK|CONNECTION) (?:TO |WITH )?RUSSIA\b/.test(normalize(v));
  function russianPlace(value){
    const n=normalize(value),compact=n.replace(/ /g,'');
    if(/\bRU\s?[A-Z0-9]{3}\b/.test(n)||n==='RU'||n==='RUS'||n==='RUSSIA')return true;
    return PLACE_NAMES.has(compact)||PLACE_NAMES.has(n);
  }
  function mention(value){
    const n=normalize(value);
    if(negated(n))return false;
    return /\bRU\s?[A-Z0-9]{3}\b|\bRUSSIA\b|\bRUSSIAN\b/.test(n)||MENTION_NAMES.some(a=>(' '+n+' ').includes(' '+a+' '));
  }
  function parseVoyage(value){
    const raw=text(value),parts=raw.replace(/→|->|=>/g,'>').split('>').map(s=>s.trim());
    if(parts.length===2&&parts.every(Boolean)&&!/[<>]/.test(parts.join(''))){
      const from=russianPlace(parts[0]),to=russianPlace(parts[1]);
      return {raw,from,to,related:from||to||mention(raw),explicit_route:true,basis:'AIS departure > destination declaration'};
    }
    if(parts.length>1||/[<>]/.test(raw))return {raw,from:false,to:false,related:mention(raw),explicit_route:false,basis:'Ambiguous/multi-leg AIS voyage text'};
    const origin=raw.match(/^FROM\s+(.+?)(?:\s+TO\s+(.+))?$/i),target=raw.match(/^TO\s+(.+)$/i);
    if(origin)return {raw,from:russianPlace(origin[1]),to:origin[2]?russianPlace(origin[2]):false,related:mention(raw),explicit_route:true,basis:'Explicit FROM/TO voyage text'};
    if(target)return {raw,from:false,to:russianPlace(target[1]),related:mention(raw),explicit_route:false,basis:'Explicit TO voyage text'};
    return {raw,from:false,to:russianPlace(raw),related:mention(raw),explicit_route:false,basis:'AIS destination-field declaration'};
  }
  const hasMetadata=p=>cats(p).length||['sanctioned','shadow_fleet','known_voi_match','false_flag','false_flag_hard_watch'].some(k=>Object.hasOwn(p,k));
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
    const current=parseVoyage(p.destination),at=VM.observed(p),evidence=[],preceding=boundedRows(p,history).slice().sort((a,b)=>VM.observed(b)-VM.observed(a));
    const add=(row,parsed,basis)=>{if(parsed.related)evidence.push({raw:parsed.raw,from:parsed.from,to:parsed.to,basis:basis||parsed.basis,observed_at:Number.isFinite(VM.observed(row))?new Date(VM.observed(row)).toISOString():null,source:VM.sourceLabel(row)});};
    add(p,current);
    for(const row of preceding){if(evidence.length>=12)break;const parsed=parseVoyage(row.destination);if(parsed.related&&!evidence.some(e=>e.raw===parsed.raw&&e.from===parsed.from&&e.to===parsed.to))add(row,parsed,'Earlier '+parsed.basis);}
    const port=[p.last_port_name,p.last_port_unlocode,p.last_ru_port,p.last_ru_port_unlocode].map(text).find(russianPlace);
    if(port)evidence.unshift({raw:port,from:true,to:false,basis:'Provider/manual previous-port field; visit date requires separate evidence',observed_at:p.last_ru_port_date||null,source:VM.sourceLabel(p)});
    const until=Date.parse(p.recent_ru_portcall_until),active=!Number.isFinite(until)||!Number.isFinite(at)||until>=at;
    const legacy=active&&(!negated(current.raw)||evidence.length>0)&&(truth(p.recent_russian_portcall_unconfirmed_10d)||truth(p.recent_russian_portcall_confirmed_10d)||truth(p.from_russia_confirmed)||cats(p).includes('recent_russian_portcall_10d'));
    const suppliedPreviousPort=active&&(truth(p.from_russia_confirmed)||truth(p.recent_russian_portcall_confirmed_10d))&&/^(observed_port_field|persisted_observed_port_field|manual_recent_russian_portcall_input|voi_history_confirmed_portcall)$/.test(text(p.recent_ru_portcall_basis));
    if(suppliedPreviousPort&&!port)evidence.unshift({raw:text(p.port_codes_seen)||'Russian previous-port indication supplied by the producer',from:true,to:false,basis:'Retained previous-port evidence; not an independently verified visit',observed_at:p.last_ru_port_date||null,source:VM.sourceLabel(p)});
    const declared=truth(p.to_russia_declared)&&!current.raw;
    const latestText=preceding.find(row=>text(row.destination)),direction=current.raw?current:latestText?parseVoyage(latestText.destination):current;
    let from=direction.from||Boolean(port)||suppliedPreviousPort,to=direction.to;
    if(!current.raw&&!latestText&&declared&&!from)to=true;
    const related=current.related||evidence.length>0||legacy||declared;
    const labels=[];if(from)labels.push('← Likely coming from Russia');if(to)labels.push('→ Likely bound for Russia');if(related&&!labels.length)labels.push('Russia-related — direction unclear');
    return {related:Boolean(related),from,to,labels,evidence,legacy,basis:legacy?text(p.recent_ru_portcall_basis):'',limit:'AIS declarations/positions are indications, not a verified port visit. Times below identify the accompanying observation or supplied port-date field, not necessarily a fresh transmission of the voyage text.'};
  }
  function describe(input,context=null){
    const history=Array.isArray(context)?context:context?.lookup(input)||[],p=enrich(input,history),set=new Set(cats(p)),ru=russia(p,history),ids=new Set();
    if(truth(p.sanctioned))ids.add('sanctioned');
    if(truth(p.shadow_fleet)||set.has('shadowfleet')||set.has('shadow_fleet'))ids.add('shadow_fleet');
    if(truth(p.false_flag_hard_watch)||set.has('false_flag_watch'))ids.add('false_flag_watch');
    if(truth(p.false_flag)||truth(p.false_flag_candidate)||set.has('falseflag_interest'))ids.add('falseflag_interest');
    if(truth(p.behavioral_voi)||set.has('behavioral_voi'))ids.add('behavioral_voi');
    if(truth(p.known_voi_match)||set.has('watchlist')||set.has('watchlist_live'))ids.add('watchlist');
    if((set.has('sanctions_shadowfleet')&&!ids.has('sanctioned')&&!ids.has('shadow_fleet'))||(truth(p.sanctions_listed_historical)&&!truth(p.sanctioned)))ids.add('sanctions_review');
    if(ru.related)ids.add('russia_voyage');
    if(set.has('russian_mmsi')||VM.mmsi(p.mmsi).startsWith('273'))ids.add('russian_mmsi');
    if(truth(p.neutral_tanker_context)||set.has('neutral_tanker_context'))ids.add('neutral_tanker_context');
    if(!ids.size&&truth(p.is_priority_voi))ids.add('watchlist');
    const categories=DEFINITIONS.filter(c=>ids.has(c.id));
    for(const raw of set)if(!['watchlist','watchlist_live','sanctions_shadowfleet','shadowfleet','shadow_fleet','false_flag_watch','falseflag_interest','behavioral_voi','recent_russian_portcall_10d','russian_mmsi','neutral_tanker_context'].includes(raw))categories.push({id:'extra:'+raw,label:text(raw).replace(/_/g,' '),color:'#62a6ff'});
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
    parent.append(box);return view;
  }
  return {DEFINITIONS,PORTS,META,DAYS,parseVoyage,index,describe,appendPopup,russia};
});
