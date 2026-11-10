(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const VM=globalThis.VesselModel;
  const MAP_BOUNDS = L.latLngBounds([[50.0, -6.0], [72.5, 32.0]]);
  const EMPTY_FC = () => ({type:"FeatureCollection",features:[]});
  const TIME_HOURS = {"24h":24,"48h":48,"120h":120,"14d":24*14,dk24:24};
  const state = {
    map:null,
    layers:{},
    data:{},
    bounds:{},
    timeMode:"latest",
    historyRows:null,
    historyPromise:null,
    dynamicReady:false, selectedIdentity:null, trackRows:null, gfwEvents:null, gfwGrid:null
  };

  const paths = {
    manifest:"./data/manifest.json",
    vesselManifest:"./data/vessels/manifest.json",
    aisContacts:"./data/vessels/ais_contacts_latest.geojson",
    vesselPositions:"./data/vessels/vessel_positions_latest.geojson",
    voiHistory:"./data/vessels/voi_history_14d.jsonl",
    danishHistory:"./data/vessels/ais_dk_last_two_positions.geojson",
    danishStatus:"./data/vessels/ais_dk_import_status.json",
    commonSnapshot:"./data/vessels/maritime_common_snapshot_latest.geojson",
    commonStatus:"./data/vessels/maritime_common_snapshot_status.json",
    neutral_tanker_context:"./data/vessels/layers/neutral_tanker_context.geojson",
    sanctions_shadowfleet:"./data/vessels/layers/sanctions_shadowfleet.geojson",
    watchlist:"./data/vessels/layers/watchlist_live.geojson",
    falseflag_interest:"./data/vessels/layers/falseflag_interest.geojson",
    false_flag_watch:"./data/vessels/layers/false_flag_watch.geojson",
    russian_mmsi:"./data/vessels/layers/russian_mmsi.geojson",
    recent_russian_portcall_10d:"./data/vessels/layers/recent_russian_portcall_10d.geojson",
    behavioral_voi:"./data/vessels/layers/behavioral_voi.geojson",
    telecom_cables:"./data/reference/emodnet/telecom_cables.geojson",
    power_cables:"./data/reference/emodnet/power_cables.geojson",
    cable_landings:"./data/reference/emodnet/cable_landings.geojson",
    pipelines:"./data/reference/emodnet/pipelines.geojson",
    wind_farms:"./data/reference/emodnet/wind_farms.geojson",
    offshore_energy:"./data/reference/emodnet/offshore_energy.geojson",
    infrastructure_events:"./data/analysis/infrastructure_events_latest.geojson",
    infrastructureSummary:"./data/analysis/infrastructure_summary_latest.json",
    downloads:"./downloads/manifest.json",
    gfwEvents:"./data/vessels/gfw_events_latest.json",
    gfwGrid:"./data/vessels/gfw_presence_cells_latest.json"
  };

  const styles = {
    telecom_cables:{color:"#42d4f4",weight:2,opacity:.85},
    power_cables:{color:"#f4d03f",weight:2,opacity:.85},
    pipelines:{color:"#ff8c42",weight:2,opacity:.82},
    wind_farms:{color:"#7bed9f",weight:1,fillColor:"#7bed9f",fillOpacity:.12,opacity:.7},
    offshore_energy:{color:"#c084fc",weight:1,fillColor:"#c084fc",fillOpacity:.14,opacity:.8},
    cable_landings:{color:"#fff",fillColor:"#fff",radius:4,weight:1,fillOpacity:.95},
    infrastructure_events:{color:"#c084fc",fillColor:"#c084fc",radius:7,weight:2,fillOpacity:.9}
  };

  const vesselColors = {
    falseflag_interest:"#ff00c8",
    false_flag_watch:"#ff8c42",
    sanctions_shadowfleet:"#ff4d5f",
    watchlist:"#ff4d5f",
    russian_mmsi:"#6da8ff",
    recent_russian_portcall_10d:"#f4d03f",
    behavioral_voi:"#c084fc",
    neutral_tanker_context:"#a8b1ba"
  };

  function esc(value){
    return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
  }

  async function fetchText(url){
    const sep = url.includes("?") ? "&" : "?";
    const response = await fetch(`${url}${sep}t=${Date.now()}`, {cache:"no-store",signal:AbortSignal.timeout(30000)});
    if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
    return response.text();
  }

  async function fetchJson(url){
    const text = await fetchText(url);
    if (/^\s*</.test(text)) throw new Error(`HTML fallback returned for ${url}`);
    return JSON.parse(text);
  }

  function parseDate(value){
    if (!value) return null;
    const normalized = String(value).replace(" +0000 UTC","Z").replace(" UTC","Z");
    const date = new Date(normalized);
    return Number.isFinite(date.getTime()) ? date : null;
  }

  function categoriesOf(properties){
    const categories = properties?.categories;
    if (Array.isArray(categories)) return categories.map(String);
    if (typeof categories === "string") return categories.split(/[;,]/).map(v => v.trim()).filter(Boolean);
    return [];
  }

  function vesselCategory(properties){
    const categories = categoriesOf(properties);
    for (const key of ["falseflag_interest","false_flag_watch","sanctions_shadowfleet","watchlist","russian_mmsi","recent_russian_portcall_10d","behavioral_voi","neutral_tanker_context"]){
      if (categories.includes(key)) return key;
    }
    return properties?.is_priority_voi ? "watchlist" : "neutral_tanker_context";
  }

  const DISPLAY_VOI_CATEGORIES = new Set([
    "watchlist","sanctions_shadowfleet","shadowfleet","russian_mmsi",
    "falseflag_interest","false_flag_watch","behavioral_voi","recent_russian_portcall_10d"
  ]);

  function isPriorityVoi(properties){
    const p = properties || {};
    if (p.is_priority_voi || p.known_voi_match || p.sanctioned || p.shadow_fleet || p.false_flag || p.behavioral_voi || p.from_russia_confirmed) return true;
    return categoriesOf(p).some(category => DISPLAY_VOI_CATEGORIES.has(category));
  }

  function providerId(p){return VM.source(p);}
  function aisSourceLabel(p){return VM.sourceLabel(p);}
  function russianPortcallLabel(properties){
    const p = properties || {};
    if (p.recent_russian_portcall_confirmed_10d || p.from_russia_confirmed) return "confirmed";
    if (p.recent_russian_portcall_unconfirmed_10d) return "unconfirmed";
    if (p.to_russia_declared) return "declared destination only — not a confirmed portcall";
    return "";
  }

  function formatAgeDays(value){
    const date = parseDate(value);
    if (!date) return "unknown";
    const days = Math.max(0, (Date.now() - date.getTime()) / 86400000);
    return `${days < 10 ? days.toFixed(1) : Math.round(days)} days`;
  }

  function commonSourceCodes(){
    const status = state.data.common_status || {};
    const coverage = status.source_coverage && typeof status.source_coverage === "object" ? status.source_coverage : {};
    const providers = new Set(Object.keys(coverage));
    for (const feature of state.data.common_snapshot?.features || []) providers.add(providerId(feature?.properties));
    return [
      ["barentswatch","(N)"],
      ["fintraffic","(FIN)"],
      ["ais_dk_historical","(DK)"],
      ["global_fishing_watch","(glob.)"]
    ].filter(([provider]) => providers.has(provider)).map(([,code]) => code);
  }

  function renderAisDataAgeBanner(){
    const host=$('aisDataAgeBanner');if(!host)return;
    const rows=(state.data.vessel_positions?.features||[]).map(f=>f.properties||{});
    const times=rows.map(VM.observed).filter(Number.isFinite),latest=times.length?Math.max(...times):NaN;
    host.textContent=Number.isFinite(latest)?`AIS samples · latest observation ${VM.age({observed_at:new Date(latest).toISOString()})} · not a continuous stream`:'No reliable AIS observation time available';
  }

  function vesselFinderUrl(p){return VM.vesselFinder(p);}

  function vesselFinderLink(properties){
    const url = vesselFinderUrl(properties);
    return url ? `<a class="vfPopupLink" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open in VesselFinder</a>` : "";
  }

  function isRegionalContact(feature){
    const p = feature?.properties || {};
    const provider = String(p.source_provider || "").toLowerCase();
    if (["fintraffic","barentswatch"].includes(provider)) return true;
    const sourceText = [p.source, ...(Array.isArray(p.sources) ? p.sources : [])].join(" ").toLowerCase();
    return sourceText.includes("fintraffic") || sourceText.includes("barentswatch") || sourceText.includes("norwegian coastal administration");
  }

  function vesselPopup(feature){
    const p = feature.properties || {};
    const cats = categoriesOf(p).join(", ") || "none";
    const positionQuality = p.position_timestamp_valid === false || p.data_quality?.timestamp_repaired;
    const sourceUrls = String(p.source_url || "").split(/[;\s]+/).filter(v => /^https?:/i.test(v));
    const historical = p.timeline_role || p.historical;
    const html=`<div class="popupTitle">${esc(p.name || "Unknown vessel")}</div>
      <div class="popupMeta">
        <b>MMSI</b><span>${esc(p.mmsi || "–")}</span>
        <b>IMO</b><span>${esc(p.imo || "–")}</span>
        <b>Observed</b><span>${esc(p.observed_at || p.last_seen_utc || "–")}</span>
        <b>AIS source</b><span>${esc(aisSourceLabel(p))}</span>
        <b>Data age</b><span>${esc(VM.age(p))}</span>
        <b>Retrieved</b><span>${esc(p.retrieved_at||p.snapshot_generated_at||"not established")}</span>
        <b>Other delivery sources</b><span>${esc((p.observation_sources||[]).map(id=>VM.SOURCES[id]?.label||id).join(", ")||"–")}</span>
        <b>Categories</b><span>${esc(cats)}</span>
        <b>Destination</b><span>${esc(p.destination || "–")}</span>
        <b>RU portcall</b><span>${esc(russianPortcallLabel(p) || "–")}</span>
        <b>RU evidence</b><span>${esc(p.recent_ru_portcall_basis || p.to_russia_declared_basis || "–")}</span>
        <b>SOG / COG</b><span>${esc(p.sog ?? "–")} kn / ${esc(p.cog ?? "–")}°</span>
        ${p.from_observed_at?`<b>Spurabschnitt UTC</b><span>${esc(p.from_observed_at)} → ${esc(p.to_observed_at)}</span>`:""}
        <b>Common snapshot</b><span>${esc(p.snapshot_at || "–")}</span>
        <b>Watch source</b><span>${esc(p.source_list || "–")}</span>
      </div>
      <button type="button" class="trackPopupButton" data-track-id="${esc(p.vessel_identity||identityKey(p))}">Show this vessel’s track</button>
      ${p.identity_conflict ? `<div class="qualityWarn">Conflicting IMO/MMSI details: tracks are not linked automatically.</div>` : ""}
      ${historical ? `<div class="historyWarn">Historical point inside the selected time window. It is not necessarily the vessel's current position.</div>` : ""}
      ${positionQuality ? `<div class="qualityWarn">Timestamp was repaired from the snapshot/history slot. Do not use it for precise dwell or gap calculations.</div>` : ""}
      ${(vesselFinderLink(p) || sourceUrls.length) ? `<div style="margin-top:7px">${[vesselFinderLink(p), ...sourceUrls.slice(0,3).map((url,i)=>`<a href="${esc(url)}" target="_blank" rel="noopener">Source ${i+1}</a>`)].filter(Boolean).join(" · ")}</div>` : ""}
      <div class="assessmentLimit">VOI/watchlist context is an analyst lead, not proof of hostile intent or unlawful activity.</div>`;
    const content=document.createElement('div');content.innerHTML=html;
    const trackButton=content.querySelector('[data-track-id]');
    const activate=event=>{event.stopPropagation();if(trackButton.disabled)return;trackButton.disabled=true;selectVesselTrack(p.vessel_identity||identityKey(p)).finally(()=>{trackButton.disabled=false;});};
    trackButton?.addEventListener('click',activate);
    return content;
  }

  function danishPopup(feature){
    const p = feature.properties || {};
    const role = p.feature_role === "latest_historical_position" ? "Latest historical observation" : "Previous historical observation";
    return `<div class="popupTitle">${esc(p.name || "Unknown vessel")}</div>
      <div class="popupMeta">
        <b>Role</b><span>${esc(role)}</span>
        <b>MMSI</b><span>${esc(p.mmsi || "–")}</span>
        <b>IMO</b><span>${esc(p.imo || "–")}</span>
        <b>Observed</b><span>${esc(p.observed_at || "–")}</span>
        <b>SOG / COG</b><span>${esc(p.sog ?? "–")} kn / ${esc(p.cog ?? "–")}°</span>
        <b>Destination</b><span>${esc(p.destination || "–")}</span>
        <b>Source</b><span>Historical fallback data (DK)</span>
      </div>
      ${vesselFinderLink(p) ? `<div style="margin-top:7px">${vesselFinderLink(p)}</div>` : ""}
      <div class="historyWarn">Delayed historical data. Do not use this point as a current vessel position.</div>`;
  }

  function infrastructurePopup(feature, layerId){
    const p = feature.properties || {};
    const name = p.name || p.Name || p.NAME || p.title || p.Title || p.code || p.Code || "Unnamed feature";
    const fields = Object.entries(p).filter(([key,value]) => !key.startsWith("_") && value !== null && value !== "").slice(0,10);
    return `<div class="popupTitle">${esc(name)}</div>
      <div class="popupMeta"><b>Layer</b><span>${esc(layerId.replaceAll("_"," "))}</span>${fields.map(([key,value])=>`<b>${esc(key)}</b><span>${esc(typeof value === "object" ? JSON.stringify(value) : value)}</span>`).join("")}</div>
      <div class="assessmentLimit">Reference source: EMODnet Human Activities and original data providers.</div>`;
  }

  function eventPopup(feature){
    const p = feature.properties || {};
    const vessel = p.vessel || {};
    const infra = p.infrastructure || {};
    const observation = p.observation || {};
    return `<div class="popupTitle">${esc(vessel.name || vessel.mmsi || "Review event")}</div>
      <div class="popupMeta">
        <b>Level</b><span>${esc(p.level || "review")}</span>
        <b>Confidence</b><span>${esc(p.confidence || "–")}</span>
        <b>Infrastructure</b><span>${esc(`${infra.type || ""} — ${infra.name || ""}`)}</span>
        <b>Min. distance</b><span>${esc(observation.minimum_distance_nm ?? "–")} nm</span>
        <b>Dwell</b><span>${esc(observation.estimated_dwell_minutes ?? "–")} min</span>
        <b>Signals</b><span>${esc((p.signals || []).join(", "))}</span>
      </div><div class="assessmentLimit">${esc(p.assessment || "Analyst review required.")}</div>`;
  }

  function isVesselLayer(layerId){
    return ["ais_contacts","vessel_positions","danish_history","neutral_tanker_context","sanctions_shadowfleet","watchlist","falseflag_interest","false_flag_watch","russian_mmsi","recent_russian_portcall_10d","behavioral_voi"].includes(layerId);
  }

  function createGeoLayer(layerId, data){
    const isVessel = isVesselLayer(layerId);
    const layer = L.geoJSON(data || EMPTY_FC(), {
      filter(feature){
        if (layerId === "vessel_positions" && !feature?.properties?.timeline_role) return Boolean(feature?.properties?.is_priority_voi);
        return true;
      },
      style(feature){
        const geometryType = feature?.geometry?.type;
        const p = feature?.properties || {};
        if (layerId === "danish_history"){
          return {color:"#ffd166",weight:2,opacity:.72,dashArray:"6 5"};
        }
        if (layerId === "vessel_positions" && geometryType === "LineString"){
          const color = VM.SOURCES[providerId(p)].color;
          return {color,weight:1.5,opacity:.48};
        }
        if (isVessel){
          const color = VM.SOURCES[providerId(p)].color;
          return {color,fillColor:color,weight:1,fillOpacity:.82,radius:4};
        }
        if (layerId === "infrastructure_events"){
          const elevated = p.level === "elevated";
          return {...styles.infrastructure_events,color:elevated?"#ff4d5f":"#c084fc",fillColor:elevated?"#ff4d5f":"#c084fc"};
        }
        return styles[layerId] || {color:"#ddd",weight:1,fillOpacity:.1};
      },
      pointToLayer(feature, latlng){
        const p = feature?.properties || {};
        if (layerId === "danish_history"){
          const latest = p.feature_role === "latest_historical_position";
          return L.circleMarker(latlng,{
            radius:latest ? 5 : 3.5,
            color:"#ffd166",
            fillColor:latest ? "#ffd166" : "#071017",
            weight:latest ? 1.5 : 2,
            fillOpacity:latest ? .92 : .45
          });
        }
        if (isVessel){
          const category = vesselCategory(p);
          const color = VM.SOURCES[providerId(p)].color;
          const historical = Boolean(p.timeline_role);
          const radius = historical ? (p.timeline_role === "latest_in_window" ? 5 : 3) : category === "neutral_tanker_context" ? 3 : category === "falseflag_interest" ? 5 : 4;
          return L.circleMarker(latlng,{radius,color:(p.false_flag||["falseflag_interest","false_flag_watch"].includes(category))?"#ff00c8":color,fillColor:color,weight:p.false_flag?2.5:1.5,fillOpacity:historical ? .68 : .82});
        }
        const style = layerId === "infrastructure_events" && p.level === "elevated"
          ? {...styles.infrastructure_events,color:"#ff4d5f",fillColor:"#ff4d5f"}
          : (styles[layerId] || {radius:4,color:"#ddd",fillColor:"#ddd",fillOpacity:.8});
        return L.circleMarker(latlng, style);
      },
      onEachFeature(feature, leafletLayer){
        if (layerId === "danish_history"){
          if (feature?.geometry?.type === "Point") leafletLayer.bindPopup(() => danishPopup(feature),{maxWidth:430});
          return;
        }
        if (isVessel){
          if (["Point","LineString"].includes(feature?.geometry?.type)) leafletLayer.bindPopup(() => vesselPopup(feature),{maxWidth:430});
        } else if (layerId === "infrastructure_events") leafletLayer.bindPopup(() => eventPopup(feature),{maxWidth:430});
        else leafletLayer.bindPopup(() => infrastructurePopup(feature,layerId),{maxWidth:430});
      }
    });
    return layer;
  }

  function replaceLayer(id, data){
    if (state.layers[id] && state.map.hasLayer(state.layers[id])) state.map.removeLayer(state.layers[id]);
    const layer = createGeoLayer(id,data);
    state.layers[id] = layer;
    try { state.bounds[id] = layer.getBounds(); } catch(_e) { state.bounds[id] = null; }
    syncLayerVisibility(id);
  }

  function checkbox(id){
    return document.querySelector(`input[data-layer="${CSS.escape(id)}"]`);
  }

  function checkboxEnabled(id){
    const box = checkbox(id);
    return Boolean(box?.checked && !box.disabled);
  }

  function syncLayerVisibility(id){
    const layer = state.layers[id];
    if (!layer || !state.map) return;
    const reference=['telecom_cables','power_cables','cable_landings','pipelines','wind_farms','offshore_energy'].includes(id);
    if (checkboxEnabled(id)&&(!reference||$('showInfrastructure').checked)) layer.addTo(state.map);
    else state.map.removeLayer(layer);
  }

  function setControl(id,{enabled=true,checked}={}){
    const box = checkbox(id);
    if (!box) return;
    box.disabled = !enabled;
    if (checked !== undefined) box.checked = Boolean(checked);
    syncLayerVisibility(id);
  }

  async function loadStaticGeoLayer(id,url){
    try{
      const data = await fetchJson(url);
      state.data[id] = data;
      replaceLayer(id,data);
      return data;
    }catch(error){
      console.warn(`Layer ${id} unavailable`,error);
      const box = checkbox(id);
      if (box && id!=="neutral_tanker_context"){ box.checked=false; box.disabled=true; box.closest("label")?.setAttribute("title",String(error)); }
      return null;
    }
  }

  function formatBytes(bytes){
    const n = Number(bytes);
    if (!Number.isFinite(n)) return "";
    if (n < 1024) return `${n} B`;
    if (n < 1024*1024) return `${(n/1024).toFixed(1)} KB`;
    return `${(n/1024/1024).toFixed(1)} MB`;
  }

  async function loadDownloads(){
    try{
      const manifestUrl = new URL(paths.downloads, window.location.href);
      const manifest = await fetchJson(manifestUrl.href);
      const root = $("downloadLinks");
      root.innerHTML = "";
      for (const product of manifest.products || []){
        const href = String(product.href || "").trim();
        if (!href) continue;
        const a = document.createElement("a");
        a.href = new URL(href, manifestUrl).href;
        a.download = product.filename || "";
        a.innerHTML = `<span>${esc(product.label)}</span><small>${esc(formatBytes(product.size_bytes))}</small>`;
        root.appendChild(a);
      }
      if (!root.children.length) root.innerHTML = '<span class="empty">No download products are available.</span>';
    }catch(error){
      $("downloadLinks").innerHTML = `<span class="empty">Download manifest unavailable: ${esc(error.message)}</span>`;
    }
  }

  function eventDate(feature){
    const obs = feature?.properties?.observation || {};
    return parseDate(obs.event_position?.observed_at || obs.latest_position?.observed_at || feature?.properties?.observed_at);
  }

  function filterEvents(hours){
    const source = state.data.infrastructure_events || EMPTY_FC();
    if (!hours) return source;
    const cutoff = Date.now() - hours*3600*1000;
    return {...source,features:(source.features || []).filter(feature => {
      const date = eventDate(feature);
      return date && date.getTime() >= cutoff;
    })};
  }

  function renderAnalysisList(data){
    const root = $("analysisList");
    root.innerHTML = "";
    const features = data?.features || [];
    if (!features.length){ root.innerHTML='<div class="empty">No review events in the selected time window.</div>'; return; }
    features.slice(0,50).forEach(feature => {
      const p = feature.properties || {};
      const vessel = p.vessel || {};
      const infra = p.infrastructure || {};
      const obs = p.observation || {};
      const node = document.createElement("div");
      node.className = `analysisItem ${p.level === "elevated" ? "elevated" : ""}`;
      node.innerHTML = `<strong>${esc(vessel.name || vessel.mmsi || "Unknown vessel")}</strong><span>${esc(infra.type || "infrastructure")} · ${esc(obs.minimum_distance_nm ?? "–")} nm · ${esc(p.level || "review")}</span>`;
      node.addEventListener("click",()=>{
        const c = feature.geometry?.coordinates;
        if (Array.isArray(c) && c.length>=2) state.map.setView([c[1],c[0]],10);
      });
      root.appendChild(node);
    });
  }

  async function loadVoiHistory(){
    if (state.historyRows) return state.historyRows;
    if (state.historyPromise) return state.historyPromise;
    state.historyPromise = fetchText(paths.voiHistory).then(text => {
      const rows=[];
      for (const line of text.split(/\r?\n/)){
        if (!line.trim()) continue;
        try{
          const row=JSON.parse(line);
          if (row && typeof row === "object") rows.push(row);
        }catch(error){ console.warn("Malformed VOI history line",error); }
      }
      state.historyRows=rows;
      return rows;
    }).finally(()=>{state.historyPromise=null;});
    return state.historyPromise;
  }

  function identityKey(item){
    return String(item.mmsi || item.imo || item.callsign || item.name || "").trim();
  }

  function visibleSource(p){return (p.observation_sources||[VM.source(p)]).some(id=>{const input=document.querySelector(`[data-source="${id}"]`);return !input||input.checked;});}
  function sourceForView(p){const id=(p.observation_sources||[VM.source(p)]).find(id=>{const input=document.querySelector(`[data-source="${id}"]`);return !input||input.checked;});return id?{...p,source_provider:id}:p;}
  function scopedRows(){
    const features=[...(state.data.vessel_positions?.features||[]),...(state.data.ais_contacts?.features||[]),...(state.data.neutral_tanker_context?.features||[])];
    const rows=features.filter(f=>f.geometry?.type==='Point').map(f=>({...f.properties,longitude:f.geometry.coordinates[0],latitude:f.geometry.coordinates[1],_current:true}));
    const result=[];
    for(const items of VM.groups(rows).values()){
      const priority=items.some(isPriorityVoi),neutral=items.some(p=>p.neutral_tanker_context||categoriesOf(p).includes('neutral_tanker_context'));
      if(!priority&&!neutral)continue;
      const categories=[...new Set(items.flatMap(categoriesOf))];
      for(const row of items)result.push({...row,is_priority_voi:priority,categories});
    }
    return result;
  }
  async function selectVesselTrack(identity){
    state.selectedIdentity=identity;$('trackStatus').textContent='Loading vessel history …';
    try{state.trackRows=await loadVoiHistory();renderVesselView();const selected=VM.track([...scopedRows(),...state.trackRows],{hours:TIME_HOURS[state.timeMode]||336}).find(t=>t.identity===identity);
      if(selected?.points.length){state.map.closePopup();state.map.fitBounds(L.latLngBounds(selected.points.map(p=>{const c=VM.coordinates(p);return [c[1],c[0]];})).pad(.15),{maxZoom:11});}
    }catch{$('trackStatus').textContent='Vessel history could not be loaded. Latest positions remain visible.';}
  }
  function renderVesselView(){
    state.traffic?.render();
    // Source/search controls must not re-enable current AIS inside the historical SAR comparison.
    if($('sarDetectionsToggle')?.checked){$('trackStatus').textContent='Historical SAR comparison is active; current AIS layers remain hidden.';return;}
    const latest=state.timeMode==='latest',hours=TIME_HOURS[state.timeMode]||336;
    const rows=latest?[...scopedRows(),...(state.trackRows||[])]:(state.trackRows||[]);
    const models=VM.track(rows,{hours}).sort((a,b)=>Number(b.identity===state.selectedIdentity)-Number(a.identity===state.selectedIdentity));const points=[],lines=[],historyPoints=[];let gaps=0,displayCap=false;
    for(const model of models){
      if(latest&&!model.points.some(p=>p._current))continue;
      const query=$('vesselSearch').value.trim().toLowerCase();if(query&&!model.points.some(p=>[p.name,p.imo,p.mmsi].join(' ').toLowerCase().includes(query)))continue;
      const selected=state.selectedIdentity===model.identity,showTrack=$('showTracks').checked||selected;
      const latestPoint=model.latest;if(!latestPoint)continue;
      const priority=model.points.some(isPriorityVoi);
      const props={...sourceForView(latestPoint),is_priority_voi:priority,vessel_identity:model.identity};
      if(state.timeMode!=='latest')props.timeline_role='latest_in_window';
      if(visibleSource(props))points.push({type:'Feature',geometry:{type:'Point',coordinates:VM.coordinates(props)},properties:props});
      if(showTrack){gaps+=model.gaps.length;for(const p of model.points.slice(0,-1)){if(!visibleSource(p))continue;if(historyPoints.length>=3000){displayCap=true;break;}historyPoints.push({type:'Feature',geometry:{type:'Point',coordinates:VM.coordinates(p)},properties:{...sourceForView(p),is_priority_voi:priority,vessel_identity:model.identity,timeline_role:'historical_point'}});}
      for(const segment of model.segments){
        if(lines.length>=3000){displayCap=true;break;}
        if(!visibleSource(segment.from)||!visibleSource(segment.to))continue;
        lines.push({type:'Feature',geometry:{type:'LineString',coordinates:[VM.coordinates(segment.from),VM.coordinates(segment.to)]},properties:{...sourceForView(segment.to),vessel_identity:model.identity,timeline_role:'historical_track',from_observed_at:segment.from.observed_at||segment.from.last_seen_utc,to_observed_at:segment.to.observed_at||segment.to.last_seen_utc}});
      }}
    }
    replaceLayer('vessel_positions',{type:'FeatureCollection',features:[...lines,...historyPoints.filter(f=>f.properties.is_priority_voi),...points.filter(f=>f.properties.is_priority_voi)]});
    replaceLayer('neutral_tanker_context',{type:'FeatureCollection',features:[...historyPoints,...points].filter(f=>!f.properties.is_priority_voi)});
    setControl('vessel_positions',{enabled:true});
    setControl('neutral_tanker_context',{enabled:true});
    $('vesselList').replaceChildren();
    for(const feature of points.slice(0,25)){
      const p=feature.properties,button=document.createElement('button');button.className='vesselListItem';button.style.borderLeftColor=VM.SOURCES[VM.source(p)].color;button.textContent=`AIS: ${p.name||p.imo||p.mmsi||'Identity unknown'} · ${VM.sourceLabel(p)} · ${VM.age(p)}`;
      button.addEventListener('click',()=>{for(const id of ['vessel_positions','neutral_tanker_context'])state.layers[id]?.eachLayer?.(layer=>{if(layer.feature?.geometry?.type==='Point'&&layer.feature.properties.timeline_role!=='historical_point'&&layer.feature.properties.vessel_identity===p.vessel_identity){state.map.setView([feature.geometry.coordinates[1],feature.geometry.coordinates[0]],9);layer.openPopup();}});});$('vesselList').append(button);
    }
    $('trackStatus').textContent=`${points.length} vessels · ${lines.length} track segments · ${gaps} broken connections. Lines connect observations; data gaps remain open.${displayCap?" Display limited to 3,000 track segments/points. Select one vessel.":""}`;
  }
  function historyGeoJson(rows,hours){
    const models=VM.track(rows,{hours,maxPoints:120});return {type:'FeatureCollection',features:models.flatMap(m=>m.segments.map(seg=>({type:'Feature',geometry:{type:'LineString',coordinates:[VM.coordinates(seg.from),VM.coordinates(seg.to)]},properties:{...seg.to,timeline_role:'historical_track',vessel_identity:m.identity}})))};
  }

  const COMMON_CATEGORY_LAYERS=["neutral_tanker_context","sanctions_shadowfleet","watchlist","falseflag_interest","false_flag_watch","russian_mmsi","recent_russian_portcall_10d","behavioral_voi"];
  function commonSubset(layerId){
    const source=state.data.common_snapshot || EMPTY_FC();
    const features=(source.features || []).filter(feature=>{
      const p=feature?.properties || {};const cats=categoriesOf(p);
      if(layerId==="vessel_positions") return isPriorityVoi(p);
      if(layerId==="neutral_tanker_context") return Boolean((p.neutral_tanker_context || cats.includes("neutral_tanker_context")) && !isPriorityVoi(p));
      if(layerId==="sanctions_shadowfleet") return Boolean(p.sanctioned || p.shadow_fleet || cats.includes("sanctions_shadowfleet") || cats.includes("shadowfleet"));
      if(layerId==="watchlist") return Boolean(p.known_voi_match || cats.includes("watchlist"));
      if(layerId==="falseflag_interest") return Boolean(p.false_flag || cats.includes("falseflag_interest"));
      if(layerId==="false_flag_watch") return cats.includes("false_flag_watch");
      if(layerId==="russian_mmsi") return cats.includes("russian_mmsi") || String(p.mmsi || "").startsWith("273");
      if(layerId==="recent_russian_portcall_10d") return Boolean(p.from_russia_confirmed || cats.includes("recent_russian_portcall_10d"));
      if(layerId==="behavioral_voi") return Boolean(p.behavioral_voi || cats.includes("behavioral_voi"));
      return false;
    });
    return {...source,features,metadata:{...(source.metadata || {}),display_layer:layerId}};
  }
  function applyCommonCategoryLayers(){
    COMMON_CATEGORY_LAYERS.forEach(id=>{
      const data=commonSubset(id);
      replaceLayer(id,data);
      setControl(id,{enabled:Boolean(data.features?.length),checked:id==="neutral_tanker_context" && Boolean(data.features?.length)});
    });
  }
  function restoreProviderCategoryLayers(){
    COMMON_CATEGORY_LAYERS.forEach(id=>{const data=state.data[id] || EMPTY_FC();replaceLayer(id,data);setControl(id,{enabled:Boolean(data.features?.length)});});
  }

  function regionalCurrentGeoJson(){
    const source=state.data.ais_contacts || EMPTY_FC();
    return {...source,features:(source.features || []).filter(isRegionalContact),metadata:{...(source.metadata || {}),display_mode:"fintraffic_barentswatch_current"}};
  }

  function updateTimeButtons(mode){
    document.querySelectorAll("button[data-time-mode]").forEach(button=>button.classList.toggle("active",button.dataset.timeMode===mode));
  }

  async function applyTimeMode(mode){
    state.timeMode=mode;state.traffic?.render();updateTimeButtons(mode);renderAisDataAgeBanner();
    setControl('ais_contacts',{enabled:false,checked:false});replaceLayer('ais_contacts',EMPTY_FC());
    const danish=mode==='dk24';setControl('danish_history',{enabled:danish&&Boolean(state.data.danish_history?.features?.length),checked:danish});
    replaceLayer('danish_history',danish?state.data.danish_history:EMPTY_FC());
    if(mode!=='latest'||$('showTracks').checked||state.selectedIdentity){
      try{state.trackRows=await loadVoiHistory();}catch{$('trackStatus').textContent='History unavailable. No track added.';state.trackRows=[];}
    }
    renderVesselView();const events=filterEvents(TIME_HOURS[mode]||null);replaceLayer('infrastructure_events',events);renderAnalysisList(events);
    $('timeStatus').textContent=mode==='latest'?'Latest AIS observations':`${mode==='dk24'?'24 h + DK':mode} · historical observations`;
    renderGfw();
  }

  function renderGfw(){
    const layers=L.layerGroup(),cells=L.layerGroup(),now=Date.now(),cutoff=now-(TIME_HOURS[state.timeMode]||336)*3600000;
    const events=(state.gfwEvents?.events||[]).filter(e=>e.type==='LOITERING'&&Date.parse(e.end)>=cutoff).map(e=>({event:e,context:VM.eventContext(e,now)})).filter(e=>e.context).sort((a,b)=>Date.parse(b.event.end)-Date.parse(a.event.end));
    const shown=events.slice(0,200);$('loiteringList').replaceChildren();
    for(const {event:e,context:c} of shown){
      const point=e.position,vessel=e.vessel||{},label=vessel.name||c.mmsi||'Identity not published',duration=(Date.parse(e.end)-Date.parse(e.start))/3600000;
      const sar='./sar.html#event='+encodeURIComponent(JSON.stringify(c)),vf=VM.vesselFinder({imo:c.imo,mmsi:c.mmsi});
      const html=`<div class="popupTitle">GFW Loitering · ${esc(label)}</div><div class="popupMeta"><b>MMSI / GFW-ID</b><span>${esc(c.mmsi||vessel.id||'unavailable')}</span><b>Start UTC</b><span>${esc(e.start)}</span><b>End UTC</b><span>${esc(e.end)}</span><b>Duration</b><span>${duration.toFixed(1)} h</span><b>Source</b><span>Global Fishing Watch · historical</span></div><p>Provider-reported event; reference position rather than a complete track. Loitering does not establish unlawful activity.</p><a href="${esc(sar)}" target="_blank" rel="noopener noreferrer">Search SAR acquisitions for this period ↗</a>${vf?`<p><a href="${esc(vf)}" target="_blank" rel="noopener noreferrer">VesselFinder ↗</a></p>`:''}${c.period_clipped?'<p>Search period limited to the available last 14 days.</p>':''}`;
      const marker=L.circleMarker([point.lat,point.lon],{radius:9,color:'#c8a1ff',weight:3,fillColor:'#c8a1ff',fillOpacity:.12}).bindPopup(html,{maxWidth:420}).addTo(layers);
      if($('loiteringList').children.length<20){const button=document.createElement('button');button.className='loiteringItem';button.textContent=`${label} · ${e.start.slice(0,16).replace('T',' ')} UTC · ${duration.toFixed(1)} h`;button.addEventListener('click',()=>{state.map.setView([point.lat,point.lon],10);marker.openPopup();});$('loiteringList').append(button);}
    }
    for(const c of (state.gfwGrid?.records||[]).filter(c=>Date.parse(c.interval_end)>=cutoff&&Date.parse(c.interval_start)<=now).slice(-300)){
      const lat=Number(c.latitude),lon=Number(c.longitude);if(!Number.isFinite(lat)||!Number.isFinite(lon))continue;
      L.rectangle([[lat-.005,lon-.005],[lat+.005,lon+.005]],{color:'#c8a1ff',weight:1,fillOpacity:.08}).bindPopup(`GFW presence cell · ${esc(c.interval_start)} to ${esc(c.interval_end)} · not an exact vessel position or identity`).addTo(cells);
    }
    for(const [id,layer] of [['gfw_loitering',layers],['gfw_presence',cells]]){state.layers[id]?.remove();state.layers[id]=layer;syncLayerVisibility(id);}
    $('gfwStatus').textContent=state.gfwEvents?`${shown.length}/${events.length} loitering events shown · up to 200 markers and 20 list entries. Powered by Global Fishing Watch · CC BY-NC 4.0.`:'GFW event data are missing; this is not a confirmed zero result.';
  }

  async function loadSummary(){
    let vesselManifest=null, summary=null, emodnet=null;
    try { vesselManifest=await fetchJson(paths.vesselManifest); } catch(_e) {}
    try { summary=await fetchJson(paths.infrastructureSummary); } catch(_e) {}
    try { emodnet=await fetchJson("./data/reference/emodnet/manifest.json"); } catch(_e) {}
    const metrics = $("summaryMetrics").querySelectorAll("strong");
    if (metrics[0]) metrics[0].textContent = vesselManifest?.snapshot?.item_count ?? "–";
    if (metrics[1]) metrics[1].textContent = summary?.event_count ?? "–";
    if (metrics[2]) metrics[2].textContent = (summary?.reference_feature_count ?? (emodnet?.layers || []).reduce((sum,row)=>sum+(Number(row.feature_count)||0),0)) || "–";
    const generated = summary?.generated_at || vesselManifest?.generated_at || emodnet?.generated_at;
    $("generatedAt").textContent = `Generated: ${generated ? new Date(generated).toISOString().replace("T"," ").slice(0,16)+" UTC" : "unknown"}`;
    const ready = Boolean(summary?.reference_ready || (emodnet?.layers || []).some(row=>Number(row.feature_count)>0));
    $("dataStatus").textContent = ready ? "Reference data ready" : "Reference sync pending";
  }

  function fit(ids){
    const bounds = L.latLngBounds([]);
    ids.forEach(id => { const b=state.bounds[id]; if (b?.isValid?.()) bounds.extend(b); });
    if (bounds.isValid()) state.map.fitBounds(bounds.pad(.08),{maxZoom:10});
    else state.map.fitBounds(MAP_BOUNDS);
  }

  async function loadDynamicData(){
    const [ais,vessels,events,danish,danishStatus,commonSnapshot,commonStatus]=await Promise.all([
      fetchJson(paths.aisContacts).catch(error=>{console.warn("AIS provider data unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.vesselPositions).catch(error=>{console.warn("Current VOI positions unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.infrastructure_events).catch(error=>{console.warn("Infrastructure events unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.danishHistory).catch(error=>{console.warn("Danish historical AIS unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.danishStatus).catch(()=>null),
      fetchJson(paths.commonSnapshot).catch(error=>{console.warn("Common maritime snapshot unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.commonStatus).catch(()=>null)
    ]);
    state.data.ais_contacts=ais;
    state.data.vessel_positions=vessels;
    state.data.infrastructure_events=events;
    state.data.danish_history=danish;
    state.data.danish_status=danishStatus;
    state.data.common_snapshot=commonSnapshot;
    state.data.common_status=commonStatus;
    const [gfwEvents,gfwGrid]=await Promise.all([fetchJson(paths.gfwEvents).catch(()=>null),fetchJson(paths.gfwGrid).catch(()=>null)]);
    state.gfwEvents=gfwEvents;state.gfwGrid=gfwGrid;
    state.dynamicReady=true;
    renderAisDataAgeBanner();
  }

  async function init(){
    state.map = L.map("map",{zoomControl:true,minZoom:3,maxZoom:18,worldCopyJump:true});
    state.traffic = globalThis.TrafficContext.attach(state.map,()=>({timeMode:state.timeMode,monitoredRows:scopedRows()}));
    fetchJson('./assets/sar-basemap.geojson').then(data=>{L.geoJSON(data,{pane:'tilePane',interactive:false,style:{color:'#536776',weight:1,fillColor:'#263c49',fillOpacity:1}}).addTo(state.map);state.map.attributionControl.addAttribution('Natural Earth · orientation only, not a nautical chart');}).catch(()=>console.warn('Coastline overview unavailable'));
    state.map.fitBounds(MAP_BOUNDS);
    L.control.scale({imperial:false,nautical:true}).addTo(state.map);

    document.querySelectorAll('input[data-layer]').forEach(input => input.addEventListener("change",()=>syncLayerVisibility(input.dataset.layer)));
    document.querySelectorAll('button[data-time-mode]').forEach(button=>button.addEventListener("click",()=>applyTimeMode(button.dataset.timeMode)));
    $("fitVessels").addEventListener("click",()=>fit(["ais_contacts","vessel_positions","danish_history","sanctions_shadowfleet","watchlist","falseflag_interest","false_flag_watch","russian_mmsi","recent_russian_portcall_10d","behavioral_voi","neutral_tanker_context"]));
    $("fitInfrastructure").addEventListener("click",()=>fit(["telecom_cables","power_cables","cable_landings","pipelines","wind_farms","offshore_energy"]));

    await Promise.all([
      loadStaticGeoLayer("telecom_cables",paths.telecom_cables),
      loadStaticGeoLayer("power_cables",paths.power_cables),
      loadStaticGeoLayer("cable_landings",paths.cable_landings),
      loadStaticGeoLayer("pipelines",paths.pipelines),
      loadStaticGeoLayer("wind_farms",paths.wind_farms),
      loadStaticGeoLayer("offshore_energy",paths.offshore_energy)
    ]);
    await Promise.all([
      loadStaticGeoLayer("neutral_tanker_context",paths.neutral_tanker_context),
      loadStaticGeoLayer("sanctions_shadowfleet",paths.sanctions_shadowfleet),
      loadStaticGeoLayer("watchlist",paths.watchlist),
      loadStaticGeoLayer("falseflag_interest",paths.falseflag_interest),
      loadStaticGeoLayer("false_flag_watch",paths.false_flag_watch),
      loadStaticGeoLayer("russian_mmsi",paths.russian_mmsi),
      loadStaticGeoLayer("recent_russian_portcall_10d",paths.recent_russian_portcall_10d),
      loadStaticGeoLayer("behavioral_voi",paths.behavioral_voi)
    ]);
    $('sarDetectionsToggle')?.addEventListener('change',()=>{if(!$('sarDetectionsToggle').checked)setTimeout(renderVesselView,0);});
    $('vesselSearch').addEventListener('input',renderVesselView);
    $('showInfrastructure').addEventListener('change',()=>['telecom_cables','power_cables','cable_landings','pipelines','wind_farms','offshore_energy'].forEach(syncLayerVisibility));
    $('resetOverview').addEventListener('click',()=>{state.selectedIdentity=null;$('showTrafficContext').checked=false;$('showTracks').checked=false;$('vesselSearch').value='';$('showInfrastructure').checked=false;$('showInfrastructure').dispatchEvent(new Event('change'));document.querySelectorAll('[data-source]').forEach(input=>input.checked=true);document.querySelectorAll('input[data-layer]').forEach(input=>{if(['sanctions_shadowfleet','watchlist','falseflag_interest','false_flag_watch','russian_mmsi','recent_russian_portcall_10d','behavioral_voi','gfw_presence'].includes(input.dataset.layer)){input.checked=false;syncLayerVisibility(input.dataset.layer);}});applyTimeMode('latest');});
    document.querySelectorAll('[data-source]').forEach(input=>input.addEventListener('change',renderVesselView));
    $('showTracks').addEventListener('change',()=>applyTimeMode(state.timeMode));
    $('clearTrack').addEventListener('click',()=>{state.selectedIdentity=null;renderVesselView();});

    await loadDynamicData();
    await applyTimeMode("latest");
    await Promise.all([loadSummary(),loadDownloads()]);
  }

  init().catch(error => {
    console.error(error);
    $("dataStatus").textContent = "Initialisation failed";
  });
})();

