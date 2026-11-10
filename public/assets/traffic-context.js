/* Optional latest-only traffic. Never passed to vessel tracks, SAR association or scoring. */
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TrafficContext = api;
})(globalThis, () => {
  'use strict';
  const MAX_BYTES = 1024 * 1024, MAX_CONTACTS = 10000, MAX_AGE = 24 * 3600000;
  const SOURCES = new Set(['openwaters', 'fintraffic', 'barentswatch']);
  const COLUMNS = ['mmsi','imo','latitude','longitude','observed_at','source_provider','upstream_source','position_timestamp_basis'];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function decode(pack, now = Date.now()) {
    if (!pack || pack.kind !== 'sampled_traffic_context' || pack.schema_version !== '1.0.0' ||
        pack.coverage_complete !== false || pack.context_only !== true || pack.position_timestamp_valid !== true ||
        JSON.stringify(pack.columns) !== JSON.stringify(COLUMNS) || !Array.isArray(pack.contacts) || pack.contacts.length > MAX_CONTACTS) {
      throw new Error('Invalid or oversized traffic product');
    }
    const generated = Date.parse(pack.generated_at), expires = Date.parse(pack.expires_at);
    if (!Number.isFinite(generated) || !Number.isFinite(expires) || generated > now + 300000 ||
        expires <= generated || expires > generated + MAX_AGE || now > expires || now - generated > MAX_AGE) {
      throw new Error('Traffic context expired or timestamps invalid');
    }
    const rows = [];
    for (const tuple of pack.contacts) {
      if (!Array.isArray(tuple) || tuple.length !== COLUMNS.length) continue;
      const p = Object.fromEntries(COLUMNS.map((name, i) => [name, tuple[i]]));
      const time = Date.parse(p.observed_at);
      if (!/^\d{9}$/.test(p.mmsi) || p.mmsi === '000000000' || !SOURCES.has(p.source_provider) ||
          typeof p.latitude !== 'number' || typeof p.longitude !== 'number' ||
          !Number.isFinite(p.latitude) || !Number.isFinite(p.longitude) || Math.abs(p.latitude) > 90 || Math.abs(p.longitude) > 180 ||
          !Number.isFinite(time) || now - time > MAX_AGE || time > now + 300000) continue;
      rows.push({...p, position_timestamp_valid: true});
    }
    return rows;
  }

  function visibleRows(rows, monitored, vm, now = Date.now()) {
    const mm = new Set(monitored.map(p => vm.mmsi(p.mmsi)).filter(Boolean));
    const im = new Set(monitored.map(p => vm.imo(p.imo)).filter(Boolean));
    const latest = [];
    for (const items of vm.groups(rows.filter(p => !mm.has(vm.mmsi(p.mmsi)) && !im.has(vm.imo(p.imo)) &&
        now - Date.parse(p.observed_at) <= MAX_AGE)).values()) {
      latest.push(items.reduce((a,b) => Date.parse(a.observed_at) >= Date.parse(b.observed_at) ? a : b));
    }
    return latest;
  }

  function aggregate(rows, project, zoom) {
    if (zoom >= 8) return rows.map(p => ({rows:[p], latitude:p.latitude, longitude:p.longitude}));
    const buckets = new Map();
    for (const p of rows) {
      const point = project(p);
      const key = `${p.source_provider}:${Math.floor(point.x/28)}:${Math.floor(point.y/28)}`;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(p);
    }
    return [...buckets.values()].map(items => ({rows:items,
      latitude:items.reduce((n,p)=>n+p.latitude,0)/items.length,
      longitude:items.reduce((n,p)=>n+p.longitude,0)/items.length}));
  }

  async function fetchPack(url) {
    const response = await fetch(url, {cache:'no-store', signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error('Traffic data unavailable');
    const reader = response.body.getReader(), chunks = []; let size = 0;
    try {
      while (true) {
        const {done, value} = await reader.read(); if (done) break;
        size += value.byteLength;
        if (size > MAX_BYTES) throw new Error('Traffic data exceed 1 MiB');
        chunks.push(value);
      }
    } finally { await reader.cancel(); }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  }

  function popup(p, vm) {
    const link = vm.vesselFinder(p);
    return `<div class="popupTitle">Traffic context · ${esc(p.mmsi)}</div><div class="popupMeta">` +
      `<b>Source</b><span>${esc(vm.sourceLabel(p))}</span><b>IMO</b><span>${esc(vm.imo(p.imo)||'unknown')}</span>` +
      `<b>Observation UTC</b><span>${esc(p.observed_at)}</span><b>Data age</b><span>${esc(vm.age(p))}</span>` +
      `</div><p>Latest received position, not continuous live tracking. Traffic context only; no history is stored.</p>` +
      (link ? `<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">VesselFinder ↗</a>` : '');
  }

  function attach(map, getView) {
    const vm = globalThis.VesselModel, $ = id => document.getElementById(id);
    const toggle = $('showTrafficContext'), status = $('trafficContextStatus');
    const notice = $('trafficCoverageNotice'), reopen = $('showCoverageNotice'), close = $('closeCoverageNotice');
    close.addEventListener('click', () => { notice.hidden = true; reopen.hidden = false; reopen.focus({preventScroll:true}); });
    reopen.addEventListener('click', () => { notice.hidden = false; reopen.hidden = true; close.focus({preventScroll:true}); });
    for (const node of [notice,reopen]) { L.DomEvent.disableClickPropagation(node); L.DomEvent.disableScrollPropagation(node); }
    map.createPane('trafficContextPane').style.zIndex = '390';
    const renderer = L.canvas({pane:'trafficContextPane',padding:.2,tolerance:4});
    const layer = L.layerGroup().addTo(map);
    let pack = null, rows = [], loading = null, error = '', loadedAt = 0;
    let contextPopup = null, popupMmsi = '';
    function closeContextPopup() {
      if (contextPopup) map.closePopup(contextPopup);
      contextPopup = null; popupMmsi = '';
    }

    function render() {
      layer.clearLayers();
      if (!toggle.checked) { closeContextPopup(); status.textContent = 'Hidden · background context without vessel history.'; return; }
      if (getView().timeMode !== 'latest' || $('sarDetectionsToggle')?.checked) {
        closeContextPopup(); status.textContent = 'Hidden in historical time views and SAR comparison. Available only in “Latest AIS observations”.'; return;
      }
      if (loading) { closeContextPopup(); status.textContent = 'Loading traffic context …'; return; }
      if (error || !pack) { closeContextPopup(); status.textContent = error || 'Traffic context has not been loaded.'; return; }
      try { rows = decode(pack); } catch (e) { closeContextPopup(); status.textContent = e.message + '; no background points shown.'; return; }
      const now = Date.now(), query = $('vesselSearch').value.trim();
      const allowed = visibleRows(rows,getView().monitoredRows,vm,now).filter(p => {
        const source = document.querySelector(`[data-source="${p.source_provider}"]`);
        return (!source || source.checked) && (!query || [p.mmsi,p.imo].some(s=>String(s).includes(query)));
      });
      if (contextPopup && !allowed.some(p=>p.mmsi===popupMmsi)) closeContextPopup();
      const view = allowed.filter(p => map.getBounds().pad(.1).contains([p.latitude,p.longitude]));
      const groups = aggregate(view, p => map.project([p.latitude,p.longitude],map.getZoom()), map.getZoom());
      for (const group of groups) {
        const p = group.rows[0], clustered = group.rows.length > 1;
        const age = now - Math.max(...group.rows.map(r=>Date.parse(r.observed_at)));
        const marker = L.circleMarker([group.latitude,group.longitude],{pane:'trafficContextPane',renderer,
          radius:clustered ? Math.min(3.5,2.5+Math.log2(group.rows.length)*.25) : 2.5,
          color:vm.SOURCES[p.source_provider].color,fillColor:vm.SOURCES[p.source_provider].color,
          weight:clustered?1:0,opacity:.55,fillOpacity:age > 6*3600000 ? .2 : .5});
        if (clustered) marker.bindTooltip(`${group.rows.length} received contacts · ${vm.SOURCES[p.source_provider].label} · zoom in to see individual contacts`)
          .on('click',()=>map.fitBounds(L.latLngBounds(group.rows.map(r=>[r.latitude,r.longitude])).pad(.15),{maxZoom:Math.max(8,map.getZoom()+2)}));
        // A standalone popup survives canvas marker replacement during its own auto-pan.
        else marker.on('click',()=>{
          popupMmsi=p.mmsi;
          contextPopup=L.popup({maxWidth:360}).setLatLng([p.latitude,p.longitude]).setContent(popup(p,vm)).openOn(map);
        });
        marker.addTo(layer);
      }
      const failed = Object.entries(pack.providers||{}).filter(([,s])=>s.state!=='ok').map(([id])=>vm.SOURCES[id]?.label||id);
      const cap = pack.cap_applied ? ` Capacity-limited selection${pack.omitted_count ? `: ${pack.omitted_count} additional contacts omitted` : '; already limited at the source processing stage'}.` : '';
      const stale = allowed.filter(p=>now-Date.parse(p.observed_at)>6*3600000).length;
      status.textContent = `${view.length} contacts in view · ${groups.length} dots/groups · ${allowed.length} after filters. Updated: ${new Date(pack.generated_at).toISOString().slice(0,16).replace('T',' ')} UTC.${cap}` +
        (failed.length ? ` Sources missing/partial: ${failed.join(', ')}.` : '') +
        (stale ? ` ${stale} positions older than 6 h, shown fainter.` : '') +
        (rows.length ? ' Received sample, not a complete traffic picture.' : ' No usable background contacts; this does not establish an absence of traffic.');
    }

    async function load() {
      if (loading) return loading;
      loading = fetchPack('./data/vessels/traffic_context_latest.json').then(value=>{
        rows=decode(value); pack=value; error=''; loadedAt=Date.now();
      }).catch(()=>{pack=null;rows=[];error='Traffic context is missing, expired or could not be loaded; this is not a confirmed zero result.';})
        .finally(()=>{loading=null;render();});
      render(); return loading;
    }
    toggle.addEventListener('change',()=>{ if(toggle.checked && (!pack || Date.now()-loadedAt>300000)) load(); else render(); });
    $('sarDetectionsToggle')?.addEventListener('change',render);
    map.on('moveend zoomend',render);
    const timer = setInterval(render,60000);
    map.once('unload',()=>clearInterval(timer));
    render();
    return {render};
  }

  return {decode,visibleRows,aggregate,popup,attach,MAX_BYTES,MAX_CONTACTS};
});
