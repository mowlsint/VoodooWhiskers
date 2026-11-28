#!/usr/bin/env python3
"""Reuse the SAR matcher with archived AIS and separate, anonymous GFW context.

No raster download, source API calls, public promotion, or automatic vessel finding.
The original fixed-scene pilot and its review gates remain unchanged.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import copernicus_sar_ais_match as matcher
import copernicus_sar_catalog as catalog
from fetch_gfw_common_presence import parse_dt

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PROVIDERS = {"openwaters", "barentswatch", "fintraffic", "ais_dk_historical"}
LIMIT = "Filtered historical observations are incomplete. An absent match is not evidence of deliberate AIS disablement or hostile activity."


def validate_candidates(payload):
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError("SAR candidates must be a FeatureCollection")
    scene = (payload.get("metadata") or {}).get("scene") or {}
    scene_id = scene.get("normalized_scene_id") or scene.get("id")
    acquired = parse_dt(scene.get("datetime"))
    if not scene_id or not acquired:
        raise ValueError("actual Sentinel-1 scene identifier and acquisition datetime required")
    stamp = re.search(r"_(\d{8}T\d{6})_", scene_id)
    if not stamp or acquired.strftime("%Y%m%dT%H%M%S") != stamp[1]:
        raise ValueError("scene ID and acquisition time disagree")
    features = payload.get("features")
    if not isinstance(features, list) or len(features) > 5000:
        raise ValueError("invalid or oversized candidate collection")
    ids = set()
    for feature in features:
        props = feature.get("properties") or {}
        identifier = props.get("detection_id")
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        if not identifier or identifier in ids or geometry.get("type") != "Point" or len(coords) != 2:
            raise ValueError("unique detection IDs and point geometry required")
        if not all(isinstance(n, (float, int)) and math.isfinite(n) for n in coords):
            raise ValueError("finite candidate coordinates required")
        if not (-180 <= coords[0] <= 180 and -90 <= coords[1] <= 90):
            raise ValueError("candidate outside WGS84 bounds")
        ids.add(identifier)
    return scene_id, acquired


def projections_from_history(rows, acquired, cfg):
    buckets, rejected = {}, Counter()
    for raw in rows:
        provider = raw.get("provider") or raw.get("source_provider")
        t = parse_dt(raw.get("observed_at") or raw.get("last_seen_utc"))
        valid = raw.get("timestamp_valid", raw.get("position_timestamp_valid"))
        if provider not in ALLOWED_PROVIDERS or valid is not True or raw.get("position_is_exact") is False:
            rejected["not_valid_exact_ais"] += 1
            continue
        if not t or abs((t-acquired).total_seconds()) > 1800:
            rejected["outside_acquisition_window"] += 1
            continue
        mmsi = str(raw.get("mmsi") or "")
        lat, lon = raw.get("latitude"), raw.get("longitude")
        if len(mmsi) != 9 or not mmsi.isdigit() or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)):
            rejected["invalid_identity_or_position"] += 1
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        record = {**raw, "observed_at": catalog.iso_z(t), "_observed_dt": t}
        bucket = buckets.setdefault(mmsi, {"before": {}, "after": {}, "source_messages": 0})
        matcher._retain_nearest(bucket, record, acquired, 4)
        bucket["source_messages"] += 1
    projections = []
    for mmsi, bucket in buckets.items():
        projection = matcher.project_trajectory(mmsi, bucket, acquired, cfg["trajectory"])
        providers = sorted({r.get("provider") or r.get("source_provider") for side in ("before", "after") for r in bucket[side].values()})
        projection["source_provider"] = "+".join(providers)
        projection["limitations"].append("FILTERED_SPARSE_HISTORY_NOT_COMPLETE_AIS_COVERAGE")
        projections.append(projection)
    return projections, dict(rejected)


def gfw_context(feature, acquired, grid_pack, events_pack, scene_id):
    lon, lat = feature["geometry"]["coordinates"]
    cells = []
    for cell in grid_pack.get("records") or []:
        start, end = parse_dt(cell.get("interval_start")), parse_dt(cell.get("interval_end"))
        if cell.get("record_type") != "ais_presence_grid" or not start or not end or not start <= acquired < end:
            continue
        try:
            distance = matcher.haversine_m((lon, lat), (float(cell["longitude"]), float(cell["latitude"])))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(distance) and distance <= 2000:
            cells.append({k: cell.get(k) for k in ("latitude", "longitude", "interval_start", "interval_end", "spatial_resolution_degrees", "presence_hours", "source_dataset")})
    events = []
    for event in events_pack.get("events") or []:
        start, end = parse_dt(event.get("start")), parse_dt(event.get("end"))
        if not start or not end or not start <= acquired <= end:
            continue
        try:
            p = event["position"]
            distance = matcher.haversine_m((lon, lat), (float(p["lon"]), float(p["lat"])))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(distance) and distance <= 10000:
            events.append({k: event.get(k) for k in ("id", "type", "start", "end", "source_dataset", "vessel")})
    eligible = datetime.now(timezone.utc) >= acquired+timedelta(days=5)
    relevant_day=grid_pack.get('observation_day')==acquired.date().isoformat()
    return {"presence_state": "CONTEXT_AVAILABLE" if cells else "NO_RELEVANT_CONTEXT_IN_AVAILABLE_GRID" if relevant_day else "NOT_AVAILABLE" if eligible else "PENDING_PROVIDER_DELAY",
            "presence_cells": cells[:20], "presence_cells_total": len(cells),
            "behavior_events": events[:20], "behavior_events_total": len(events),
            "grid_identity_match_permitted": False, "can_establish_ais_coverage": False,
            "spatial_comparison_radius_m": 2000, "event_context_radius_m": 10000,
            "temporal_uncertainty_seconds": 3600, "attribution": "Powered by Global Fishing Watch.",
            "license": "CC BY-NC 4.0", "scene_id": scene_id,
            "sar_from_same_sentinel1_scene_is_not_independent_confirmation": True,
            "sar_detections_used_as_ais": False}


def assess(payload, rows, grid_pack=None, events_pack=None, archive=None, bbox=None):
    scene_id, acquired = validate_candidates(payload)
    cfg = json.loads((ROOT/"config/sar_copernicus_ais_match.json").read_text())
    cfg["time_alignment"]["scene_acquisition_time_utc"] = catalog.iso_z(acquired)
    cfg["source"].update({"provider": "historical_archives", "archive_date": acquired.date().isoformat()})
    coverage = {"sufficient_for_available_ais_comparison": False,
                "status": "NO_AIS_COVERAGE", "basis": "Filtered sparse archive cannot prove local AIS coverage"}
    projections, rejected = projections_from_history(rows, acquired, cfg)
    if archive is not None:
        if bbox is None:
            raise ValueError("Danish archive comparison requires the actual scene/AOI bbox [west,south,east,north]")
        if len(bbox)!=4 or not all(isinstance(n,(int,float)) and math.isfinite(n) for n in bbox):
            raise ValueError("finite WGS84 archive AOI required")
        west,south,east,north=bbox
        if not (-180<=west<east<=180 and -90<=south<north<=90):
            raise ValueError("invalid archive AOI")
        if any(not matcher.point_in_bbox(*f['geometry']['coordinates'],bbox) for f in payload['features']):
            raise ValueError("archive coverage AOI must contain every compared candidate")
        cfg["source"]["archive_date"] = acquired.date().isoformat()
        pilot = {"aoi": {"bbox_wgs84": bbox}}
        buckets, coverage, extraction = matcher.extract_historical_window(archive, cfg, pilot)
        danish, _ = matcher.build_projections(buckets, cfg, pilot)
        # Prefer dense archive projection when the same identity appears in sparse samples.
        index = {p["mmsi"]: p for p in projections}
        index.update({p["mmsi"]: p for p in danish})
        projections = list(index.values())
        coverage["archive_extraction"] = extraction
    features, counts, _ = matcher.match_candidates(payload, projections, coverage, cfg)
    queue = []
    for feature in features:
        props = feature["properties"]
        props["gfw_historical_context"] = gfw_context(feature, acquired, grid_pack or {}, events_pack or {}, scene_id)
        props["human_visual_review_required"] = True
        props["downstream_eligible"] = False
        queue.append({"detection_id": props["detection_id"], "status": props["ais_match_status"],
                      "review_state": "HUMAN_REVIEW_REQUIRED", "downstream_eligible": False})
    meta = {"schema_version": "1.0.0", "generated_at": catalog.iso_z(datetime.now(timezone.utc)),
            "scene": copy.deepcopy(payload["metadata"]["scene"]), "scene_acquisition_time_utc": catalog.iso_z(acquired),
            "match_counts": counts, "coverage_assessment": coverage, "rejected_history": rejected,
            "current_ais_used": False, "public_layer": False, "assessment_limit": LIMIT,
            "source_position_count": len(rows), "projected_vessels": len(projections)}
    return {"type": "FeatureCollection", "metadata": meta, "features": features}, {**meta, "items": queue}


def read_optional(path):
    return json.loads(path.read_text()) if path.exists() else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=ROOT/"data/sar_copernicus_cfar_candidates_latest.geojson")
    parser.add_argument("--history", type=Path, default=ROOT/"data/history/maritime_source_history_21d.jsonl")
    parser.add_argument("--danish-archive", type=Path)
    parser.add_argument("--bbox", type=float, nargs=4)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.history.read_text().splitlines() if line.strip()] if args.history.exists() else []
    context, queue = assess(json.loads(args.candidates.read_text()), rows,
        read_optional(ROOT/"data/gfw_presence_cells_latest.json"), read_optional(ROOT/"data/gfw_events_latest.json"),
        args.danish_archive, args.bbox)
    catalog.atomic_json(ROOT/"data/sar_historical_context_latest.geojson", context)
    catalog.atomic_json(ROOT/"data/sar_historical_review_queue_latest.json", queue)
    print(json.dumps({"counts": context["metadata"]["match_counts"], "public_layer": False, "live_requests_performed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
