(() => {
  "use strict";

  const fetchJson = async (url) => {
    const response = await fetch(`${url}?t=${Date.now()}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };

  const formatUtc = (value) => {
    const date = value ? new Date(value) : null;
    if (!date || !Number.isFinite(date.getTime())) return "unavailable";
    return date.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  };

  const ageHours = (value) => {
    const ms = Date.parse(String(value || ""));
    return Number.isFinite(ms) ? Math.max(0, (Date.now() - ms) / 3600000) : Infinity;
  };

  const sourceCodes = (status) => {
    const coverage = status?.source_coverage && typeof status.source_coverage === "object"
      ? status.source_coverage
      : {};
    return [
      ["barentswatch", "(N)"],
      ["fintraffic", "(FIN)"],
      ["ais_dk_historical", "(DK)"],
      ["global_fishing_watch", "(glob.)"],
    ].filter(([provider]) => Object.hasOwn(coverage, provider)).map(([, code]) => code);
  };

  const effectiveStatus = (status) => {
    if (!status || !status.snapshot_id || !status.snapshot_at || !status.generated_at) {
      return {tone: "red", label: "snapshot unavailable", buildAgeHours: Infinity};
    }
    const expected = Math.max(1, Number(status.expected_refresh_hours || 30));
    const hard = Math.max(expected, Number(status.hard_stale_hours || 72));
    const buildAgeHours = ageHours(status.generated_at);
    const complete = status.snapshot_complete === true || status.snapshot_current === true;
    if (buildAgeHours > hard) return {tone: "red", label: "snapshot stale", buildAgeHours};
    if (complete && buildAgeHours <= expected) return {tone: "green", label: "snapshot current", buildAgeHours};
    return {tone: "orange", label: complete ? "snapshot overdue" : String(status.status || "warming up").replaceAll("_", " "), buildAgeHours};
  };

  const render = async () => {
    const host = document.getElementById("commonSnapshotBanner");
    if (!host) return;
    try {
      const status = await fetchJson("./data/vessels/maritime_common_snapshot_status.json");
      const effective = effectiveStatus(status);
      const codes = sourceCodes(status);
      host.dataset.tone = effective.tone;
      host.innerHTML = `
        <strong>Harmonized vessel data</strong>
        <span>Observed: ${formatUtc(status.snapshot_at)}</span>
        <span>Historical fallback data${codes.length ? ` ${codes.join(" ")}` : ""}</span>
        <span>Built: ${formatUtc(status.generated_at)}</span>
        <span>${effective.label}</span>
      `;
      host.title = "Green means the newest expected common snapshot was built successfully from every mandatory source and is within its refresh window. It does not mean live AIS.";
    } catch (error) {
      host.dataset.tone = "red";
      host.innerHTML = "<strong>Harmonized vessel data</strong><span>Status unavailable</span>";
      host.title = String(error);
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render, {once: true});
  } else {
    render();
  }
})();
