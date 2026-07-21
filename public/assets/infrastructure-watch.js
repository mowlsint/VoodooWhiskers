(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const MAP_BOUNDS = L.latLngBounds([[50.0, -6.0], [72.5, 32.0]]);
  const state = { map:null, layers:{}, data:{}, bounds:{} };

  const paths = {
    manifest:"./data/manifest.json",
    vesselManifest:"./data/vessels/manifest.json",
    aisContacts:"./data/vessels/ais_contacts_latest.geojson",
    vesselPositions:"./data/vessels/vessel_positions_latest.geojson",
    neutral_tanker_context:"./data/vessels/layers/neutral_tanker_context.geojson",
    sanctions_shadowfleet:"./data/vessels/layers/sanctions_shadowfleet.geojson",
    falseflag_interest:"./data/vessels/layers/falseflag_interest.geojson",
    russian_mmsi:"./data/vessels/layers/russian_mmsi.geojson",
    recent_russian_portcall_10d:"./data/vessels/layers/recent_russian_portcall_10d.geojson",
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

  async function fetchJson(url){
    const sep = url.includes("?") ? "&" : "?";
    const response = await fetch(`${url}${sep}t=${Date.now()}`, {cache:"no-store", headers:{Accept:"application/json"}});
    if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
    const text = await response.text();
    if (/^\s*</.test(text)) throw new Error(`HTML fallback returned for ${url}`);
    return JSON.parse(text);
  }

  function categoriesOf(properties){
    const categories = properties?.categories;
    if (Array.isArray(categories)) return categories.map(String);
    if (typeof categories === "string") return categories.split(/[;,]/).map(v => v.trim()).filter(Boolean);
    return [];
  }

  function vesselCategory(properties){
    const categories = categoriesOf(properties);
    for (const key of ["falseflag_interest","sanctions_shadowfleet","watchlist","russian_mmsi","recent_russian_portcall_10d","behavioral_voi","neutral_tanker_context"]){
      if (categories.includes(key)) return key;
    }
    return properties?.is_priority_voi ? "watchlist" : "neutral_tanker_context";
  }

  function vesselPopup(feature){
    const p = feature.properties || {};
    const cats = categoriesOf(p).join(", ") || "none";
    const positionQuality = p.position_timestamp_valid === false || p.data_quality?.timestamp_repaired;
    const sourceUrls = String(p.source_url || "").split(/[;\s]+/).filter(v => /^https?:/i.test(v));
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
      ${positionQuality ? `<div class="qualityWarn">Timestamp was repaired from the snapshot/history slot. Do not use it for precise dwell or gap calculations.</div>` : ""}
      ${sourceUrls.length ? `<div style="margin-top:7px">${sourceUrls.slice(0,3).map((url,i)=>`<a href="${esc(url)}" target="_blank" rel="noopener">Source ${i+1}</a>`).join(" · ")}</div>` : ""}
      <div class="assessmentLimit">VOI/watchlist context is an analyst lead, not proof of hostile intent or unlawful activity.</div>`;
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

  function createGeoLayer(layerId, data){
    const isVessel = ["ais_contacts","vessel_positions","neutral_tanker_context","sanctions_shadowfleet","falseflag_interest","russian_mmsi","recent_russian_portcall_10d"].includes(layerId);
    const layer = L.geoJSON(data, {
      filter(feature){
        if (layerId === "vessel_positions") return Boolean(feature?.properties?.is_priority_voi);
        if (layerId === "ais_contacts") return true;
        return true;
      },
      style(feature){
        if (isVessel){
          const color = vesselColors[vesselCategory(feature.properties)] || "#6da8ff";
          return {color,fillColor:color,weight:1,fillOpacity:.82,radius:4};
        }
        if (layerId === "infrastructure_events"){
          const elevated = feature?.properties?.level === "elevated";
          return {...styles.infrastructure_events,color:elevated?"#ff4d5f":"#c084fc",fillColor:elevated?"#ff4d5f":"#c084fc"};
        }
        return styles[layerId] || {color:"#ddd",weight:1,fillOpacity:.1};
      },
      pointToLayer(feature, latlng){
        if (isVessel){
          const category = vesselCategory(feature.properties);
          const color = vesselColors[category] || "#6da8ff";
          const radius = category === "neutral_tanker_context" ? 3 : category === "falseflag_interest" ? 5 : 4;
          return L.circleMarker(latlng,{radius,color,fillColor:color,weight:1,fillOpacity:.82});
        }
        const style = layerId === "infrastructure_events" && feature?.properties?.level === "elevated"
          ? {...styles.infrastructure_events,color:"#ff4d5f",fillColor:"#ff4d5f"}
          : (styles[layerId] || {radius:4,color:"#ddd",fillColor:"#ddd",fillOpacity:.8});
        return L.circleMarker(latlng, style);
      },
      onEachFeature(feature, leafletLayer){
        if (isVessel) leafletLayer.bindPopup(() => vesselPopup(feature),{maxWidth:430});
        else if (layerId === "infrastructure_events") leafletLayer.bindPopup(() => eventPopup(feature),{maxWidth:430});
        else leafletLayer.bindPopup(() => infrastructurePopup(feature,layerId),{maxWidth:430});
      }
    });
    state.layers[layerId] = layer;
    try { state.bounds[layerId] = layer.getBounds(); } catch(_e) {}
    return layer;
  }

  function checkboxEnabled(id){
    const box = document.querySelector(`input[data-layer="${CSS.escape(id)}"]`);
    return Boolean(box?.checked);
  }

  function syncLayerVisibility(id){
    const layer = state.layers[id];
    if (!layer || !state.map) return;
    if (checkboxEnabled(id)) layer.addTo(state.map);
    else state.map.removeLayer(layer);
  }

  async function loadGeoLayer(id, url){
    try {
      const data = await fetchJson(url);
      state.data[id] = data;
      createGeoLayer(id,data);
      syncLayerVisibility(id);
      return data;
    } catch(error){
      console.warn(`Layer ${id} unavailable`,error);
      const box = document.querySelector(`input[data-layer="${CSS.escape(id)}"]`);
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
      const manifest = await fetchJson(paths.downloads);
      const root = $("downloadLinks");
      root.innerHTML = "";
      for (const product of manifest.products || []){
        const a = document.createElement("a");
        a.href = product.href;
        a.download = product.filename || "";
        a.innerHTML = `<span>${esc(product.label)}</span><small>${esc(formatBytes(product.size_bytes))}</small>`;
        root.appendChild(a);
      }
      if (!root.children.length) root.innerHTML = '<span class="empty">No download products are available.</span>';
    }catch(error){
      $("downloadLinks").innerHTML = `<span class="empty">Download manifest unavailable: ${esc(error.message)}</span>`;
    }
  }

  function renderAnalysisList(data){
    const root = $("analysisList");
    root.innerHTML = "";
    const features = data?.features || [];
    if (!features.length){ root.innerHTML='<div class="empty">No combined proximity-and-behaviour review events.</div>'; return; }
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

  async function init(){
    state.map = L.map("map",{zoomControl:true,minZoom:3,maxZoom:18,worldCopyJump:true});
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{
      maxZoom:19,attribution:'&copy; OpenStreetMap contributors'
    }).addTo(state.map);
    state.map.fitBounds(MAP_BOUNDS);
    L.control.scale({imperial:false,nautical:true}).addTo(state.map);

    document.querySelectorAll('input[data-layer]').forEach(input => input.addEventListener("change",()=>syncLayerVisibility(input.dataset.layer)));
    $("fitVessels").addEventListener("click",()=>fit(["ais_contacts","vessel_positions","sanctions_shadowfleet","falseflag_interest","russian_mmsi","recent_russian_portcall_10d","neutral_tanker_context"]));
    $("fitInfrastructure").addEventListener("click",()=>fit(["telecom_cables","power_cables","cable_landings","pipelines","wind_farms","offshore_energy"]));

    await Promise.all([
      loadGeoLayer("telecom_cables",paths.telecom_cables),
      loadGeoLayer("power_cables",paths.power_cables),
      loadGeoLayer("cable_landings",paths.cable_landings),
      loadGeoLayer("pipelines",paths.pipelines),
      loadGeoLayer("wind_farms",paths.wind_farms),
      loadGeoLayer("offshore_energy",paths.offshore_energy)
    ]);
    await Promise.all([
      loadGeoLayer("ais_contacts",paths.aisContacts),
      loadGeoLayer("vessel_positions",paths.vesselPositions),
      loadGeoLayer("neutral_tanker_context",paths.neutral_tanker_context),
      loadGeoLayer("sanctions_shadowfleet",paths.sanctions_shadowfleet),
      loadGeoLayer("falseflag_interest",paths.falseflag_interest),
      loadGeoLayer("russian_mmsi",paths.russian_mmsi),
      loadGeoLayer("recent_russian_portcall_10d",paths.recent_russian_portcall_10d)
    ]);
    const events = await loadGeoLayer("infrastructure_events",paths.infrastructure_events);
    renderAnalysisList(events);
    await Promise.all([loadSummary(),loadDownloads()]);
  }

  init().catch(error => {
    console.error(error);
    $("dataStatus").textContent = "Initialisation failed";
  });
})();
