(() => {
  "use strict";

  const $ = id => document.getElementById(id);
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
    dynamicReady:false
  };

  const paths = {
    manifest:"./data/manifest.json",
    vesselManifest:"./data/vessels/manifest.json",
    aisContacts:"./data/vessels/ais_contacts_latest.geojson",
    vesselPositions:"./data/vessels/vessel_positions_latest.geojson",
    voiHistory:"./data/vessels/voi_history_14d.jsonl",
    danishHistory:"./data/vessels/ais_dk_last_two_positions.geojson",
    danishStatus:"./data/vessels/ais_dk_import_status.json",
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
    downloads:"./downloads/manifest.json"
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
    falseflag_interest:"#ff8c42",
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
    const response = await fetch(`${url}${sep}t=${Date.now()}`, {cache:"no-store"});
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
    return `<div class="popupTitle">${esc(p.name || "Unknown vessel")}</div>
      <div class="popupMeta">
        <b>MMSI</b><span>${esc(p.mmsi || "–")}</span>
        <b>IMO</b><span>${esc(p.imo || "–")}</span>
        <b>Categories</b><span>${esc(cats)}</span>
        <b>Destination</b><span>${esc(p.destination || "–")}</span>
        <b>SOG / COG</b><span>${esc(p.sog ?? "–")} kn / ${esc(p.cog ?? "–")}°</span>
        <b>Observed</b><span>${esc(p.observed_at || p.last_seen_utc || "–")}</span>
        <b>AIS source</b><span>${esc(p.source || "AIS")}</span>
        <b>Watch source</b><span>${esc(p.source_list || "–")}</span>
      </div>
      ${historical ? `<div class="historyWarn">Historical point inside the selected time window. It is not necessarily the vessel's current position.</div>` : ""}
      ${positionQuality ? `<div class="qualityWarn">Timestamp was repaired from the snapshot/history slot. Do not use it for precise dwell or gap calculations.</div>` : ""}
      ${sourceUrls.length ? `<div style="margin-top:7px">${sourceUrls.slice(0,3).map((url,i)=>`<a href="${esc(url)}" target="_blank" rel="noopener">Source ${i+1}</a>`).join(" · ")}</div>` : ""}
      <div class="assessmentLimit">VOI/watchlist context is an analyst lead, not proof of hostile intent or unlawful activity.</div>`;
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
        <b>Source</b><span>Danish historical AIS</span>
      </div>
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
          const color = vesselColors[vesselCategory(p)] || "#6da8ff";
          return {color,weight:1.5,opacity:.48};
        }
        if (isVessel){
          const color = vesselColors[vesselCategory(p)] || "#6da8ff";
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
          const color = vesselColors[category] || "#6da8ff";
          const historical = Boolean(p.timeline_role);
          const radius = historical ? (p.timeline_role === "latest_in_window" ? 5 : 3) : category === "neutral_tanker_context" ? 3 : category === "falseflag_interest" ? 5 : 4;
          return L.circleMarker(latlng,{radius,color,fillColor:color,weight:historical ? 1.5 : 1,fillOpacity:historical ? .68 : .82});
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
          if (feature?.geometry?.type === "Point") leafletLayer.bindPopup(() => vesselPopup(feature),{maxWidth:430});
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
    if (checkboxEnabled(id)) layer.addTo(state.map);
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
      if (box){ box.checked=false; box.disabled=true; box.closest("label")?.setAttribute("title",String(error)); }
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

  function historyGeoJson(rows,hours){
    const cutoff=Date.now()-hours*3600*1000;
    const groups=new Map();
    for (const item of rows){
      if (!item?.is_priority_voi) continue;
      const date=parseDate(item.observed_at || item.last_seen_utc);
      const lat=Number(item.latitude ?? item.lat);
      const lon=Number(item.longitude ?? item.lon);
      if (!date || date.getTime()<cutoff || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat)>90 || Math.abs(lon)>180) continue;
      const key=identityKey(item);
      if (!key) continue;
      if (!groups.has(key)) groups.set(key,[]);
      groups.get(key).push({...item,_date:date,_lat:lat,_lon:lon});
    }
    const features=[];
    for (const points of groups.values()){
      points.sort((a,b)=>a._date-b._date);
      const unique=[];
      const seen=new Set();
      for (const point of points){
        const key=`${point._date.toISOString()}|${point._lat}|${point._lon}`;
        if (seen.has(key)) continue;
        seen.add(key); unique.push(point);
      }
      const selected=unique.slice(-30);
      selected.forEach((point,index)=>{
        const props={...point};
        delete props._date; delete props._lat; delete props._lon;
        props.timeline_role=index===selected.length-1 ? "latest_in_window" : "historical_point";
        props.timeline_window_hours=hours;
        features.push({type:"Feature",geometry:{type:"Point",coordinates:[point._lon,point._lat]},properties:props});
      });
      if (selected.length>=2){
        const latest=selected[selected.length-1];
        const lineProps={...latest};
        delete lineProps._date; delete lineProps._lat; delete lineProps._lon;
        lineProps.timeline_role="historical_track";
        lineProps.timeline_window_hours=hours;
        lineProps.track_point_count=selected.length;
        features.push({type:"Feature",geometry:{type:"LineString",coordinates:selected.map(point=>[point._lon,point._lat])},properties:lineProps});
      }
    }
    return {type:"FeatureCollection",features,metadata:{window_hours:hours,source:"Voodoo bounded VOI history",max_track_points_per_vessel:30}};
  }

  function regionalCurrentGeoJson(){
    const source=state.data.ais_contacts || EMPTY_FC();
    return {...source,features:(source.features || []).filter(isRegionalContact),metadata:{...(source.metadata || {}),display_mode:"fintraffic_barentswatch_current"}};
  }

  function updateTimeButtons(mode){
    document.querySelectorAll("button[data-time-mode]").forEach(button=>button.classList.toggle("active",button.dataset.timeMode===mode));
  }

  async function applyTimeMode(mode){
    state.timeMode=mode;
    updateTimeButtons(mode);
    $("timeStatus").textContent="Loading…";
    const hours=TIME_HOURS[mode] || null;

    if (mode === "latest"){
      setControl("ais_contacts",{enabled:true});
      setControl("vessel_positions",{enabled:true});
      setControl("danish_history",{enabled:false,checked:false});
      replaceLayer("ais_contacts",state.data.ais_contacts || EMPTY_FC());
      replaceLayer("vessel_positions",state.data.vessel_positions || EMPTY_FC());
      replaceLayer("danish_history",EMPTY_FC());
      const events=filterEvents(null);
      replaceLayer("infrastructure_events",events);
      renderAnalysisList(events);
      $("timeStatus").textContent="Latest";
      return;
    }

    if (mode === "dk24"){
      setControl("ais_contacts",{enabled:true,checked:true});
      setControl("vessel_positions",{enabled:false,checked:false});
      const dkAvailable=Boolean(state.data.danish_history?.features?.length);
      setControl("danish_history",{enabled:dkAvailable,checked:dkAvailable});
      replaceLayer("ais_contacts",regionalCurrentGeoJson());
      replaceLayer("vessel_positions",EMPTY_FC());
      replaceLayer("danish_history",state.data.danish_history || EMPTY_FC());
      const events=filterEvents(24);
      replaceLayer("infrastructure_events",events);
      renderAnalysisList(events);
      const dkMeta=state.data.danish_history?.metadata || {};
      const date=dkMeta.data_max_timestamp_utc ? String(dkMeta.data_max_timestamp_utc).slice(0,16).replace("T"," ") : "unavailable";
      $("timeStatus").textContent=`24 h + DK ${date}`;
      return;
    }

    setControl("ais_contacts",{enabled:false,checked:false});
    setControl("vessel_positions",{enabled:true,checked:true});
    setControl("danish_history",{enabled:false,checked:false});
    replaceLayer("ais_contacts",EMPTY_FC());
    replaceLayer("danish_history",EMPTY_FC());
    try{
      const rows=await loadVoiHistory();
      const history=historyGeoJson(rows,hours);
      replaceLayer("vessel_positions",history);
      const events=filterEvents(hours);
      replaceLayer("infrastructure_events",events);
      renderAnalysisList(events);
      const pointCount=(history.features || []).filter(f=>f.geometry?.type==="Point").length;
      $("timeStatus").textContent=`${mode.replace("h"," h").replace("d"," d")} · ${pointCount} points`;
    }catch(error){
      console.error("VOI history unavailable",error);
      replaceLayer("vessel_positions",EMPTY_FC());
      $("timeStatus").textContent="History unavailable";
    }
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
    const [ais,vessels,events,danish,danishStatus]=await Promise.all([
      fetchJson(paths.aisContacts).catch(error=>{console.warn("Current AIS unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.vesselPositions).catch(error=>{console.warn("Current VOI positions unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.infrastructure_events).catch(error=>{console.warn("Infrastructure events unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.danishHistory).catch(error=>{console.warn("Danish historical AIS unavailable",error);return EMPTY_FC();}),
      fetchJson(paths.danishStatus).catch(()=>null)
    ]);
    state.data.ais_contacts=ais;
    state.data.vessel_positions=vessels;
    state.data.infrastructure_events=events;
    state.data.danish_history=danish;
    state.data.danish_status=danishStatus;
    state.dynamicReady=true;
  }

  async function init(){
    state.map = L.map("map",{zoomControl:true,minZoom:3,maxZoom:18,worldCopyJump:true});
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{
      maxZoom:19,attribution:'&copy; OpenStreetMap contributors'
    }).addTo(state.map);
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
    await loadDynamicData();
    await applyTimeMode("latest");
    await Promise.all([loadSummary(),loadDownloads()]);
  }

  init().catch(error => {
    console.error(error);
    $("dataStatus").textContent = "Initialisation failed";
  });
})();
