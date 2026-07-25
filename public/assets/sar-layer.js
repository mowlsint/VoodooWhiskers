(() => {
  "use strict";

  const paths = {
    detections: "./data/vessels/sar_detections_latest.geojson",
    status: "./data/vessels/sar_import_status.json",
  };

  const state = {
    map: null,
    payload: null,
    status: null,
    layer: null,
    loaded: false,
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const formatTime = (value) => {
    if (!value) return "unknown";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toISOString().replace("T", " ").replace(".000Z", " UTC");
  };

  async function fetchJson(path) {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function setStatus(text, tone = "neutral") {
    const el = document.getElementById("sarStatus");
    if (!el) return;
    el.textContent = text;
    el.dataset.tone = tone;
  }

  function markerStyle(props) {
    const watch = props.watchlist_match === true;
    const unmatched = props.ais_matched === false || props.match_status === "ais_unmatched";
    const count = Math.max(1, Number(props.detections || 1));
    return {
      pane: "sarDetectionsPane",
      radius: Math.min(10, 4 + Math.log2(count + 1)),
      color: watch ? "#ff4d5f" : (unmatched ? "#c069ff" : "#6bd6ff"),
      weight: watch ? 3 : 1.5,
      fillColor: unmatched ? "#7d3cb5" : "#3287a8",
      fillOpacity: unmatched ? 0.76 : 0.48,
    };
  }

  function popupHtml(props) {
    const identity = props.name || props.watchlist_name || props.gfw_vessel_id || "No resolved AIS identity";
    const ids = [
      props.imo ? `IMO ${escapeHtml(props.imo)}` : "",
      props.mmsi ? `MMSI ${escapeHtml(props.mmsi)}` : "",
      props.callsign ? `Callsign ${escapeHtml(props.callsign)}` : "",
    ].filter(Boolean).join(" · ");
    const match = props.ais_matched ? "Matched by GFW to AIS context" : "No GFW AIS match for this detection cell";
    const watch = props.watchlist_match
      ? `<div class="sarWatchAlert"><strong>Voodoo watchlist match</strong><br>${escapeHtml((props.categories || []).join(", ") || props.watch_priority || "watchlist")}</div>`
      : "";
    return `
      <div class="sarPopup">
        <h3>SAR detection cell</h3>
        <div><strong>${escapeHtml(identity)}</strong></div>
        ${ids ? `<div>${ids}</div>` : ""}
        <div>Region: ${escapeHtml(props.region_name || props.region_id || "unknown")}</div>
        <div>Observed: ${escapeHtml(formatTime(props.observed_at))}</div>
        <div>Detections in cell/hour: ${escapeHtml(props.detections || 1)}</div>
        <div>Match assessment: ${escapeHtml(match)}</div>
        ${watch}
        <p class="sarCaveat"><strong>Delayed analytical context.</strong> The marker is the centre of a 0.01° report grid cell, not an exact or current vessel position. An AIS-unmatched cell does not by itself prove deliberate AIS disablement or unlawful activity.</p>
        <a href="https://globalfishingwatch.org" target="_blank" rel="noopener noreferrer">Powered by Global Fishing Watch.</a>
      </div>`;
  }

  function selectedFeatures() {
    const features = state.payload?.features || [];
    const showUnmatched = document.getElementById("sarUnmatchedToggle")?.checked !== false;
    const showMatched = document.getElementById("sarMatchedToggle")?.checked !== false;
    const watchOnly = document.getElementById("sarWatchlistOnlyToggle")?.checked === true;
    return features.filter((feature) => {
      const props = feature.properties || {};
      if (watchOnly && props.watchlist_match !== true) return false;
      if (props.ais_matched === true && !showMatched) return false;
      if (props.ais_matched !== true && !showUnmatched) return false;
      return true;
    });
  }

  function render() {
    if (!state.map || !state.loaded) return;
    if (state.layer) {
      state.map.removeLayer(state.layer);
      state.layer = null;
    }
    const enabled = document.getElementById("sarDetectionsToggle")?.checked === true;
    if (!enabled) {
      setStatus("SAR layer off", "neutral");
      return;
    }
    const features = selectedFeatures();
    state.layer = L.geoJSON({ type: "FeatureCollection", features }, {
      pointToLayer: (feature, latlng) => L.circleMarker(latlng, markerStyle(feature.properties || {})),
      onEachFeature: (feature, layer) => layer.bindPopup(popupHtml(feature.properties || {}), { maxWidth: 420 }),
    }).addTo(state.map);
    const unmatched = features.filter((feature) => feature.properties?.ais_matched !== true).length;
    const watch = features.filter((feature) => feature.properties?.watchlist_match === true).length;
    setStatus(`${features.length} cells · ${unmatched} AIS-unmatched · ${watch} watchlist`, state.status?.status === "degraded" ? "warn" : "ok");
  }

  function fit() {
    if (!state.layer || !state.map) return;
    const bounds = state.layer.getBounds();
    if (bounds.isValid()) state.map.fitBounds(bounds.pad(0.08));
  }

  async function load() {
    setStatus("Loading delayed SAR data…", "neutral");
    try {
      const [payload, status] = await Promise.all([
        fetchJson(paths.detections),
        fetchJson(paths.status).catch(() => null),
      ]);
      state.payload = payload;
      state.status = status;
      state.loaded = true;
      const toggle = document.getElementById("sarDetectionsToggle");
      if (toggle) toggle.disabled = false;
      setStatus(`${payload.features?.length || 0} delayed detection cells available`, status?.status === "degraded" ? "warn" : "ok");
      render();
    } catch (error) {
      state.loaded = false;
      const toggle = document.getElementById("sarDetectionsToggle");
      if (toggle) {
        toggle.checked = false;
        toggle.disabled = true;
      }
      setStatus(`SAR data unavailable: ${error.message}`, "warn");
    }
  }

  function init(map) {
    if (state.map) return;
    state.map = map;
    if (!map.getPane("sarDetectionsPane")) {
      const pane = map.createPane("sarDetectionsPane");
      pane.style.zIndex = "680";
    }
    map.attributionControl.addAttribution('<a href="https://globalfishingwatch.org" target="_blank" rel="noopener noreferrer">Powered by Global Fishing Watch.</a>');
    ["sarDetectionsToggle", "sarUnmatchedToggle", "sarMatchedToggle", "sarWatchlistOnlyToggle"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", render);
    });
    document.getElementById("fitSar")?.addEventListener("click", fit);
    load();
  }

  if (window.__voodooLeafletMap) init(window.__voodooLeafletMap);
  else window.addEventListener("voodoo-map-ready", (event) => init(event.detail.map), { once: true });
})();
