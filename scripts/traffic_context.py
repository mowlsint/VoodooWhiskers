"""Small latest-only traffic context, isolated from VOI history and analysis inputs."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from vessel_identity import resolve, valid_imo, valid_mmsi

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public/data/vessels/traffic_context_latest.json"
MAX_BYTES = 1024 * 1024
MAX_CONTACTS = 10_000
MAX_AGE_HOURS = 24
PROVIDERS = ("openwaters", "fintraffic", "barentswatch")
COLUMNS = ["mmsi", "imo", "latitude", "longitude", "observed_at", "source_provider",
           "upstream_source", "position_timestamp_basis"]


def utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def compact(row, provider, now):
    """Whitelist fields; never carry raw frames, positions, popups or private URLs."""
    if not isinstance(row, dict) or row.get("position_timestamp_valid") is not True:
        return None
    mmsi = valid_mmsi(row.get("mmsi"))
    t = utc(row.get("observed_at") or row.get("last_seen_utc"))
    lat, lon = row.get("latitude"), row.get("longitude")
    if not mmsi or not t or not now - timedelta(hours=MAX_AGE_HOURS) <= t <= now + timedelta(minutes=5):
        return None
    if not all(isinstance(n, (float, int)) and not isinstance(n, bool) and math.isfinite(n) for n in (lat, lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"mmsi": mmsi, "imo": valid_imo(row.get("imo")),
            "latitude": round(lat, 6), "longitude": round(lon, 6),
            "observed_at": t.isoformat(), "source_provider": provider,
            "upstream_source": str(row.get("upstream_source") or "")[:64] if provider == "openwaters" else "",
            "position_timestamp_basis": str(row.get("position_timestamp_basis") or "provider_timestamp")[:64]}


def balanced(rows, limit):
    """Round-robin half-degree cells and provider routes; deterministic, not a completeness claim."""
    buckets = defaultdict(list)
    for row in rows:
        key = (row["source_provider"], math.floor(row["latitude"] * 2), math.floor(row["longitude"] * 2))
        buckets[key].append(row)
    keys = sorted(buckets, key=lambda key: hashlib.sha256(repr(key).encode()).digest())
    providers = defaultdict(deque)
    for key in keys:
        providers[key[0]].append(deque(sorted(buckets[key], key=lambda r: (r["observed_at"], r["mmsi"]), reverse=True)))
    queue = deque(providers[p] for p in sorted(providers))
    selected = []
    while queue and len(selected) < limit:
        provider = queue.popleft()
        bucket = provider.popleft()
        selected.append(bucket.popleft())
        if bucket:
            provider.append(bucket)
        if provider:
            queue.append(provider)
    return selected


class TrafficContext:
    def __init__(self, now=None):
        self.now = utc(now)
        self.fixed_clock = now is not None
        self.sources = {}
        self.status = {p: {"state": "unavailable", "received_valid": 0} for p in PROVIDERS}
        self.lock = Lock()

    def add_source(self, provider, rows, *, partial=False):
        if provider not in PROVIDERS:
            raise ValueError("Unknown traffic provider")
        # Each callback is fed by the already required source request, before VOI filtering.
        by_mmsi = {}
        valid_count = 0
        for raw in rows:
            row = compact(raw, provider, self.now if self.fixed_clock else utc())
            if row is None:
                continue
            valid_count += 1
            previous = by_mmsi.get(row["mmsi"])
            if previous is None or row["observed_at"] > previous["observed_at"]:
                by_mmsi[row["mmsi"]] = row
        bounded = balanced(by_mmsi.values(), MAX_CONTACTS)
        with self.lock:
            self.sources[provider] = bounded
            self.status[provider] = {"state": "partial" if partial else "ok", "received_valid": valid_count,
                                     "unique_before_cap": len(by_mmsi), "source_cap_applied": len(by_mmsi) > len(bounded)}

    def build(self, monitored=(), *, max_contacts=MAX_CONTACTS, max_bytes=MAX_BYTES):
        if not 0 < max_contacts <= MAX_CONTACTS or not 2048 <= max_bytes <= MAX_BYTES:
            raise ValueError("Traffic context budgets outside safety limits")
        monitored = list(monitored)
        mm = {valid_mmsi(r.get("mmsi")) for r in monitored if isinstance(r, dict)} - {""}
        im = {valid_imo(r.get("imo")) for r in monitored if isinstance(r, dict)} - {""}
        # Exclude aliases before dropping their IMO-bearing source observation.
        mm.update(r["mmsi"] for values in self.sources.values() for r in values if r["imo"] in im)
        now = self.now if self.fixed_clock else utc()
        rows = [r for values in self.sources.values() for r in values
                if r["mmsi"] not in mm and r["imo"] not in im and utc(r["observed_at"]) >= now - timedelta(hours=MAX_AGE_HOURS)]
        grouped = {}
        for row in resolve(rows):
            key = row["vessel_identity"]
            if key not in grouped or row["observed_at"] > grouped[key]["observed_at"]:
                grouped[key] = row
        ordered = balanced(grouped.values(), max_contacts)
        sources_limited = any(s.get("source_cap_applied") for s in self.status.values())
        payload = {"schema_version": "1.0.0", "kind": "sampled_traffic_context",
                   "generated_at": now.isoformat(), "expires_at": (now + timedelta(hours=MAX_AGE_HOURS)).isoformat(),
                   "max_age_hours": MAX_AGE_HOURS, "coverage_complete": False, "context_only": True,
                   "position_timestamp_valid": True, "max_contacts": MAX_CONTACTS, "max_bytes": MAX_BYTES,
                   "columns": COLUMNS, "providers": self.status,
                   "attribution": "Open Waters; Fintraffic Digitraffic (CC BY 4.0); BarentsWatch / Kystverket (NLOD)",
                   "eligible_count": len(grouped), "source_cap_applied": sources_limited}

        def encode(count):
            chosen = ordered[:count]
            payload.update({"count": count, "omitted_count": len(grouped) - count,
                            "cap_applied": sources_limited or count < len(grouped),
                            "contacts": [[row[c] for c in COLUMNS] for row in chosen]})
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()

        # Binary search includes metadata and JSON overhead in the hard byte budget.
        low, high = 0, len(ordered)
        while low < high:
            mid = (low + high + 1) // 2
            if len(encode(mid)) <= max_bytes:
                low = mid
            else:
                high = mid - 1
        raw = encode(low)
        if len(raw) > max_bytes:
            raise ValueError("Traffic metadata exceeds byte budget")
        return json.loads(raw), raw

    def publish(self, monitored=(), path=OUTPUT):
        payload, raw = self.build(monitored)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        return payload
