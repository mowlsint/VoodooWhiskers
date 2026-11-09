#!/usr/bin/env python3
"""Delayed GFW grid presence and loitering/gap events; never live AIS or tracks."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from fetch_gfw_common_presence import (ROOT, DATASET, ATTRIBUTION, flatten, finite, parse_dt,
                                       atomic_json, iso_z, regions)

GRID = ROOT / "data/gfw_presence_cells_latest.json"
EVENTS = ROOT / "data/gfw_events_latest.json"
STATUS = ROOT / "data/gfw_context_status_latest.json"
REPORT_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
EVENTS_URL = "https://gateway.api.globalfishingwatch.org/v3/events"
EVENT_DATASETS = {"LOITERING": "public-global-loitering-events:latest",
                  "GAP": "public-global-gaps-events:latest"}


def normalize_cell(row, region_id):
    observed = parse_dt(row.get("entryTimestamp") or row.get("date") or row.get("timestamp"))
    lat, lon = finite(row.get("lat")), finite(row.get("lon"))
    if not observed or lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    start = observed.replace(minute=0, second=0, microsecond=0)
    return {"record_type": "ais_presence_grid", "provider": "global_fishing_watch",
            "latitude": lat, "longitude": lon, "interval_start": iso_z(start),
            "interval_end": iso_z(start+timedelta(hours=1)),
            "spatial_resolution_degrees": .01, "temporal_resolution_seconds": 3600,
            "presence_hours": finite(row.get("hours")), "flag": row.get("flag"),
            "source_dataset": row.get("_source_dataset") or DATASET,
            "position_is_exact": False, "historical": True, "not_current_position": True,
            "region_id": region_id, "vessel_identity_available": False}


def normalize_event(event, kind, region_id):
    start, end = parse_dt(event.get("start")), parse_dt(event.get("end"))
    if not event.get("id") or not start or not end or end < start:
        return None
    vessel = event.get("vessel") or {}
    position = event.get("position") or {}
    lat, lon = finite(position.get("lat")), finite(position.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    # Keep only the existing consumer fields, without embedding the raw API response.
    return {"id": str(event["id"]), "type": kind, "start": iso_z(start), "end": iso_z(end),
            "position": {"lat": lat, "lon": lon}, "vessel": {
                k: vessel[k] for k in ("id", "name", "ssvid", "mmsi", "callsign", "type", "flag") if k in vessel},
            "loitering": {"averageSpeedKnots": finite((event.get("loitering") or {}).get("averageSpeedKnots")),
                          "totalTimeHours": (end-start).total_seconds()/3600},
            "record_type": "gfw_behavior_event", "source_dataset": EVENT_DATASETS[kind],
            "region_id": region_id, "historical": True, "not_current_position": True,
            "assessment_limit": "Provider event context; no automatic finding of intentional AIS disablement or unlawful conduct."}


def fetch_region(session, region, day, include_events=True, max_pages=2):
    end = day+timedelta(days=1)
    params = {"spatial-resolution": "HIGH", "temporal-resolution": "HOURLY",
              "spatial-aggregation": "false", "group-by": "FLAG", "datasets[0]": DATASET,
              "date-range": f"{day.isoformat()},{end.isoformat()}", "format": "JSON"}
    grid, events, queries = [], [], []
    try:
        response = session.post(REPORT_URL, params=params, json={"geojson": region["geometry"]}, timeout=(10, 60))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise ValueError("report pending or invalid; deferred to next manual batch")
        _, rows = flatten(payload)
        grid = [r for row in rows if (r := normalize_cell(row, region["id"]))]
        queries.append({"kind": "presence_grid", "region_id": region["id"], "ok": True,
                        "rows": len(grid), "invalid_rows": len(rows)-len(grid)})
    except Exception as exc:
        queries.append({"kind": "presence_grid", "region_id": region["id"], "ok": False, "error": type(exc).__name__})
    if include_events:
        for kind, dataset in EVENT_DATASETS.items():
            offset = 0
            for page in range(max_pages):
                try:
                    response = session.post(EVENTS_URL, params={"offset": offset, "limit": 250},
                        json={"datasets": [dataset], "types": [kind], "startDate": (day-timedelta(days=7)).isoformat(),
                              "endDate": end.isoformat(), "geometry": region["geometry"], "duration": 60}, timeout=(10, 45))
                    response.raise_for_status()
                    payload = response.json()
                    rows = payload.get("entries")
                    if not isinstance(rows, list):
                        raise ValueError("invalid events response")
                    events.extend(r for row in rows if (r := normalize_event(row, kind, region["id"])))
                    next_offset = payload.get("nextOffset")
                    more = isinstance(next_offset, int) and next_offset > offset
                    if not more and len(rows) >= 250:
                        more, next_offset = True, offset+250
                    queries.append({"kind": kind, "region_id": region["id"], "page": page+1,
                                    "ok": True, "rows": len(rows), "cap_applied": more and page+1 == max_pages})
                    if not more:
                        break
                    offset = next_offset
                except Exception as exc:
                    queries.append({"kind": kind, "region_id": region["id"], "ok": False, "error": type(exc).__name__})
                    break
    return grid, events, queries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", help="UTC observation day; default five days ago")
    parser.add_argument("--presence-only", action="store_true")
    parser.add_argument("--candidates", type=Path, help="Use the actual SAR acquisition day and scene/AOI footprint")
    parser.add_argument("--bbox", type=float, nargs=4, help="Scene/AOI west south east north")
    args = parser.parse_args(argv)
    token = os.environ.get("GFW_TOKEN", "").strip()
    if not token:
        raise SystemExit("GFW_TOKEN missing; no live request performed")
    now = datetime.now(timezone.utc)
    selected_regions=regions()
    scene_id=None
    if args.candidates:
        from copernicus_historical_context import validate_candidates
        payload=json.loads(args.candidates.read_text())
        scene_id,acquired=validate_candidates(payload)
        day=acquired.date()
        bbox=args.bbox or (payload.get('metadata',{}).get('scene',{}).get('bbox'))
        if bbox is None:
            pilot=json.loads((ROOT/'config/sar_copernicus_pilot.json').read_text())
            if scene_id==pilot['expected_sentinel_hub_product_id']:
                bbox=pilot['aoi']['bbox_wgs84']
        if not bbox or len(bbox)!=4:
            raise SystemExit('Actual scene footprint/AOI bbox required for GFW context')
        west,south,east,north=bbox
        if not all(finite(v) is not None for v in bbox) or not(-180<=west<east<=180 and -90<=south<north<=90):
            raise SystemExit('Invalid scene/AOI bbox')
        if any(not(west<=f['geometry']['coordinates'][0]<=east and south<=f['geometry']['coordinates'][1]<=north) for f in payload['features']):
            raise SystemExit('Scene/AOI bbox does not contain all compared candidates')
        selected_regions=[{'id':'sar_scene','name':scene_id,'geometry':{'type':'Polygon','coordinates':[[[west,south],[east,south],[east,north],[west,north],[west,south]]]}}]
    else:
        day = datetime.fromisoformat(args.day).date() if args.day else now.date()-timedelta(days=5)
    if day > now.date()-timedelta(days=4):
        raise SystemExit("Observation day must be at least four days old")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "User-Agent": "MOwlSINT-GFW-context/1.0"})
    cells, events, queries = [], [], []
    for region in selected_regions:
        g, e, q = fetch_region(session, region, day, not args.presence_only)
        cells.extend(g); events.extend(e); queries.extend(q)
    common = {"schema_version": "1.0.0", "generated_at": iso_z(now), "observation_day": day.isoformat(),
              "scene_id":scene_id,"query_regions":[{'id':r['id'],'geometry':r['geometry']} for r in selected_regions],
              "historical": True, "not_current_positions": True, "attribution": ATTRIBUTION,
              "license": "CC BY-NC 4.0", "coverage_complete": False}
    grid_ok = [q for q in queries if q["kind"] == "presence_grid" and q["ok"]]
    if grid_ok:
        dedup = {(c["latitude"], c["longitude"], c["interval_start"], c.get("flag")): c for c in cells}
        records = list(dedup.values())[:20000]
        grid_pack = {**common, "records": records, "count": len(records), "vessel_identity_available": False,
                     "cap_applied": len(dedup)>len(records), "queries": [q for q in queries if q["kind"] == "presence_grid"]}
        atomic_json(GRID, grid_pack)
        # Old identity-aware product is retained, with an explicit incompatible/current status.
        atomic_json(ROOT/"data/gfw_historical_ais_status_latest.json", {**common,
            "status": "vessel_identified_cells_unavailable", "coverage_complete": False,
            "current_presence_grid": "data/gfw_presence_cells_latest.json", "count": 0,
            "reason": "GFW restriction 2026-11-03; grid cells cannot populate vessel tracks"})
    event_queries = [q for q in queries if q["kind"] in EVENT_DATASETS]
    # Preserve a coherent LKG event product if a region or page failed/truncated.
    if event_queries and all(q["ok"] and not q.get("cap_applied") for q in event_queries):
        dedup = {(e["type"], e["id"]): e for e in events}
        if len(dedup) <= 10000:
            atomic_json(EVENTS, {**common, "events": list(dedup.values()), "count": len(dedup), "queries": event_queries})
    atomic_json(STATUS, {**common, "queries": queries, "live_requests_performed": True,
                        "ok": bool(queries) and all(q["ok"] and not q.get("cap_applied") for q in queries)})
    return 0 if any(q["ok"] for q in queries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
