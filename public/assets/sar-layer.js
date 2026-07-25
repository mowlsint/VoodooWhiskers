(() => {
  "use strict";

  const paths = {
    context: "./data/vessels/sar_ais_context_latest.geojson",
    status: "./data/vessels/sar_import_status.json",
  };

  const vesselLayerIds = [
    "ais_contacts",
    "vessel_positions",
    "danish_history",
    "neutral_tanker_context",
    "sanctions_shadowfleet",
    "watchlist",
    "falseflag_interest",
    "false_flag_watch",
    "russian_mmsi",
    "recent_russian_portcall_10d",
    "behavioral_voi",
  ];

  const state = {
    map: null,
    payload: null,
    status: null,
    layer: null,
    loaded: false,
    syncModeActive: false,
    savedControls: new Map(),
    savedTimeButtons: new Map(),
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const digits = (value) => String(value ?? "").replace(/\D/g, "");

  const formatTime = (value) => {
    if (!value) return "unknown";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toISOString().replace("T", " ").replace(".000Z", " UTC");
  };

  function vesselFinderUrl(props) {
    const imo = digits(props.imo);
    const mmsi = digits(props.mmsi);
    const id = imo.length === 7 ? imo : (mmsi.length === 9 ? mmsi : "");
    return id ? `https://www.vesselfinder.com/vessels/details/${encodeURIComponent(id)}` : "";
  }

  function vesselFinderLink(props) {
    const url = vesselFinderUrl(props);
    return url ? `<a class="vfPopupLink" href="${url}" target="_blank" rel="noopener noreferrer">Open in VesselFinder ↗</a>` : "";
  }

  async function fetchJson(path) {
    const response = await fetch(`${path}${path.includes("?") ? "&" : "?"}t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function setStatus(text, tone = "neutral") {
    const el = document.getElementById("sarStatus");
    if (!el) return;
    el.textContent = text;
    el.dataset.tone = tone;
  }

  function saveAndHideCurrentVesselLayers() {
    if (state.syncModeActive) return;
    state.syncModeActive = true;
    state.savedControls.clear();
    for (const id of vesselLayerIds) {
      const input = document.querySelector(`input[data-layer="${id}"]`);
      if (!input) continue;
      state.savedControls.set(id, { checked: input.checked, disabled: input.disabled });
      input.checked = false;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.disabled = true;
      input.closest("label")?.classList.add("sarTemporarilyDisabled");
    }
    document.querySelectorAll("button[data-time-mode]").forEach((button) => {
      state.savedTimeButtons.set(button, button.disabled);
      button.disabled = true;
    });
    const fit = document.getElementById("fitVessels");
    if (fit) {
      state.savedTimeButtons.set(fit, fit.disabled);
      fit.disabled = true;
    }
    const timeStatus = document.getElementById("timeStatus");
    if (timeStatus) {
      timeStatus.dataset.beforeSar = timeStatus.textContent || "Latest";
      timeStatus.textContent = "Historical SAR + time-aligned AIS";
    }
  }

  function restoreCurrentVesselLayers() {
    if (!state.syncModeActive) return;
    for (const id of vesselLayerIds) {
      const input = document.querySelector(`input[data-layer="${id}"]`);
      const saved = state.savedControls.get(id);
      if (!input || !saved) continue;
      input.disabled = saved.disabled;
      input.checked = saved.checked;
      input.closest("label")?.classList.remove("sarTemporarilyDisabled");
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    for (const [control, disabled] of state.savedTimeButtons.entries()) control.disabled = disabled;
    const timeStatus = document.getElementById("timeStatus");
    if (timeStatus) {
      timeStatus.textContent = timeStatus.dataset.beforeSar || "Latest";
      delete timeStatus.dataset.beforeSar;
    }
    state.savedControls.clear();
    state.savedTimeButtons.clear();
    state.syncModeActive = false;
  }

  function markerStyle(props) {
    const watch = props.watchlist_match === true;
    const role = props.feature_role;
    if (role === "historical_ais_context") {
      return {
        pane: "sarDetectionsPane",
        radius: 4.5,
        color: watch ? "#ff4d5f" : "#d9fbff",
        weight: watch ? 3 : 1.5,
        fillColor: "#15b9d5",
        fillOpacity: 0.88,
      };
    }
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
    const role = props.feature_role;
    const identity = props.name || props.watchlist_name || props.gfw_vessel_id || "No resolved AIS identity";
    const ids = [
      props.imo ? `IMO ${escapeHtml(props.imo)}` : "",
      props.mmsi ? `MMSI ${escapeHtml(props.mmsi)}` : "",
      props.callsign ? `Callsign ${escapeHtml(props.callsign)}` : "",
    ].filter(Boolean).join(" · ");
    const watch = props.watchlist_match
      ? `<div class="sarWatchAlert"><strong>Voodoo watchlist match</strong><br>${escapeHtml((props.categories || []).join(", ") || props.watch_priority || "watchlist")}</div>`
      : "";

    if (role === "historical_ais_context") {
      return `<div class="sarPopup">
        <h3>Historical AIS comparison point</h3>
        <div><strong>${escapeHtml(identity)}</strong></div>
        ${ids ? `<div>${ids}</div>` : ""}
        <div>Historical AIS timestamp: ${escapeHtml(formatTime(props.observed_at))}</div>
        <div>Paired SAR observation: ${escapeHtml(formatTime(props.sar_observed_at))}</div>
        <div>Time difference: ${escapeHtml(props.time_delta_minutes ?? "–")} min</div>
        <div>SAR/AIS cell-centre distance: ${escapeHtml(props.distance_nm ?? "–")} nm</div>
        <div>Source: GFW AIS Vessel Presence</div>
        ${watch}
        <p class="sarCaveat"><strong>Historical comparison only.</strong> This is the centre of a 0.01° AIS-presence report cell from the corresponding historical time window, not a live or exact AIS position.</p>
        ${vesselFinderLink(props)}
        <a href="https://globalfishingwatch.org" target="_blank" rel="noopener noreferrer">Powered by Global Fishing Watch.</a>
      </div>`;
    }

    if (role === "sar_ais_connector") {
      return `<div class="sarPopup">
        <h3>SAR–AIS historical connector</h3>
        <div><strong>${escapeHtml(identity)}</strong></div>
        ${ids ? `<div>${ids}</div>` : ""}
        <div>SAR: ${escapeHtml(formatTime(props.sar_observed_at))}</div>
        <div>AIS context: ${escapeHtml(formatTime(props.ais_observed_at))}</div>
        <div>Time difference: ${escapeHtml(props.time_delta_minutes ?? "–")} min</div>
        <div>Cell-centre distance: ${escapeHtml(props.distance_nm ?? "–")} nm</div>
        <p class="sarCaveat">The dashed line links two historical report-cell centres for the same GFW vessel identity. It is not a vessel track.</p>
        ${vesselFinderLink(props)}
      </div>`;
    }

    const match = props.ais_matched ? "GFW linked this SAR detection to an AIS vessel identity" : "GFW found no AIS match for this SAR detection cell";
    const correlation = props.correlation_status === "time_aligned_same_identity"
      ? `<div class="sarCorrelationOk"><strong>Historical AIS comparison available</strong><br>AIS hour ${escapeHtml(formatTime(props.ais_context_observed_at))} · Δt ${escapeHtml(props.time_delta_minutes)} min · ${escapeHtml(props.distance_nm)} nm between cell centres</div>`
      : props.ais_matched
        ? `<div class="sarCorrelationWarn"><strong>No displayed time-aligned AIS point.</strong><br>${props.correlation_status === "not_requested_identity_cap" ? "This matched identity was outside the bounded AIS-context request set." : "No AIS-presence cell was returned within the configured time tolerance."}</div>`
        : "";
    return `<div class="sarPopup">
      <h3>SAR detection cell</h3>
      <div><strong>${escapeHtml(identity)}</strong></div>
      ${ids ? `<div>${ids}</div>` : ""}
      <div>Region: ${escapeHtml(props.region_name || props.region_id || "unknown")}</div>
      <div>SAR observed: ${escapeHtml(formatTime(props.observed_at))}</div>
      <div>Detections in cell/hour: ${escapeHtml(props.detections || 1)}</div>
      <div>GFW match assessment: ${escapeHtml(match)}</div>
      ${correlation}
      ${watch}
      <p class="sarCaveat"><strong>Delayed historical context.</strong> The marker is the centre of a 0.01° SAR report cell, not an exact or current vessel position. An AIS-unmatched cell does not by itself prove deliberate AIS disablement or unlawful activity.</p>
      ${vesselFinderLink(props)}
      <a href="https://globalfishingwatch.org" target="_blank" rel="noopener noreferrer">Powered by Global Fishing Watch.</a>
    </div>`;
  }

  function selectedFeatures() {
    const features = state.payload?.features || [];
    const showUnmatched = document.getElementById("sarUnmatchedToggle")?.checked !== false;
    const showMatched = document.getElementById("sarMatchedToggle")?.checked !== false;
    const showAis = document.getElementById("sarAisContextToggle")?.checked !== false;
    const showConnectors = document.getElementById("sarConnectorsToggle")?.checked !== false;
    const watchOnly = document.getElementById("sarWatchlistOnlyToggle")?.checked === true;
    return features.filter((feature) => {
      const props = feature.properties || {};
      const role = props.feature_role || "sar_detection";
      if (watchOnly && props.watchlist_match !== true) return false;
      if (role === "historical_ais_context") return showMatched && showAis;
      if (role === "sar_ais_connector") return showMatched && showConnectors;
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
      restoreCurrentVesselLayers();
      setStatus("Historical SAR–AIS comparison off", "neutral");
      return;
    }
    saveAndHideCurrentVesselLayers();
    const features = selectedFeatures();
    state.layer = L.geoJSON({ type: "FeatureCollection", features }, {
      style: (feature) => {
        const props = feature.properties || {};
        if (props.feature_role === "sar_ais_connector") {
          return { pane: "sarConnectorsPane", color: props.watchlist_match ? "#ff4d5f" : "#9ddbe7", weight: 1.6, opacity: 0.72, dashArray: "6 5" };
        }
        return undefined;
      },
      pointToLayer: (feature, latlng) => L.circleMarker(latlng, markerStyle(feature.properties || {})),
      onEachFeature: (feature, layer) => layer.bindPopup(popupHtml(feature.properties || {}), { maxWidth: 440 }),
    }).addTo(state.map);
    const sarFeatures = features.filter((feature) => (feature.properties?.feature_role || "sar_detection") === "sar_detection");
    const unmatched = sarFeatures.filter((feature) => feature.properties?.ais_matched !== true).length;
    const aisPoints = features.filter((feature) => feature.properties?.feature_role === "historical_ais_context").length;
    const connectors = features.filter((feature) => feature.properties?.feature_role === "sar_ais_connector").length;
    setStatus(`${sarFeatures.length} SAR cells · ${unmatched} AIS-unmatched · ${aisPoints} historical AIS points · ${connectors} connectors`, state.status?.status === "degraded" ? "warn" : "ok");
  }

  function fit() {
    if (!state.layer || !state.map) return;
    const bounds = state.layer.getBounds();
    if (bounds.isValid()) state.map.fitBounds(bounds.pad(0.08));
  }

  async function load() {
    setStatus("Loading synchronized historical SAR and AIS context…", "neutral");
    try {
      const [payload, status] = await Promise.all([
        fetchJson(paths.context),
        fetchJson(paths.status).catch(() => null),
      ]);
      state.payload = payload;
      state.status = status;
      state.loaded = true;
      const toggle = document.getElementById("sarDetectionsToggle");
      if (toggle) toggle.disabled = false;
      const summary = payload.summary || status?.summary || {};
      const tolerance = status?.time_alignment_limit_minutes ?? payload.time_alignment_limit_minutes ?? "–";
      setStatus(`${summary.records_total ?? 0} SAR cells available · ${summary.time_aligned_correlations ?? 0} AIS comparisons · tolerance ≤${tolerance} min`, status?.status === "degraded" ? "warn" : "ok");
      render();
    } catch (error) {
      state.loaded = false;
      const toggle = document.getElementById("sarDetectionsToggle");
      if (toggle) {
        toggle.checked = false;
        toggle.disabled = true;
      }
      restoreCurrentVesselLayers();
      setStatus(`Historical SAR–AIS data unavailable: ${error.message}`, "warn");
    }
  }

  function init(map) {
    if (state.map) return;
    state.map = map;
    if (!map.getPane("sarConnectorsPane")) {
      const pane = map.createPane("sarConnectorsPane");
      pane.style.zIndex = "675";
    }
    if (!map.getPane("sarDetectionsPane")) {
      const pane = map.createPane("sarDetectionsPane");
      pane.style.zIndex = "680";
    }
    map.attributionControl.addAttribution('<a href="https://globalfishingwatch.org" target="_blank" rel="noopener noreferrer">Powered by Global Fishing Watch.</a>');
    ["sarDetectionsToggle", "sarUnmatchedToggle", "sarMatchedToggle", "sarAisContextToggle", "sarConnectorsToggle", "sarWatchlistOnlyToggle"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", render);
    });
    document.getElementById("fitSar")?.addEventListener("click", fit);
    load();
  }

  window.addEventListener("beforeunload", restoreCurrentVesselLayers);
  if (window.__voodooLeafletMap) init(window.__voodooLeafletMap);
  else window.addEventListener("voodoo-map-ready", (event) => init(event.detail.map), { once: true });
})();
