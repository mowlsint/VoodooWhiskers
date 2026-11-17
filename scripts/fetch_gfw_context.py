#!/usr/bin/env python3
"""Delayed GFW grid presence and loitering/gap events; never live AIS or tracks."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from data_retention import bound_gfw

from fetch_gfw_common_presence import (ROOT, DATASET, ATTRIBUTION, flatten, finite, parse_dt,
                                       atomic_json, iso_z, regions)

GRID = ROOT / "data/gfw_presence_cells_latest.json"
EVENTS = ROOT / "data/gfw_events_latest.json"
STATUS = ROOT / "data/gfw_context_status_latest.json"
EVENT_STATUS = ROOT / "data/gfw_events_status_latest.json"
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


def fetch_event_region(session, region, start_date, end_date, max_pages=2):
    events, queries = [], []
    limit = 250
    for kind, dataset in EVENT_DATASETS.items():
        offset = 0
        for page in range(max_pages):
            try:
                response = session.post(
                    EVENTS_URL,
                    params={"offset": offset, "limit": limit},
                    json={"datasets": [dataset], "types": [kind],
                          "startDate": start_date.isoformat(), "endDate": end_date.isoformat(),
                          "geometry": region["geometry"], "duration": 60},
                    timeout=(10, 45),
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("entries")
                if not isinstance(rows, list):
                    raise ValueError("invalid events response")
                normalized = [event for row in rows
                              if (event := normalize_event(row, kind, region["id"]))]
                events.extend(normalized)

                next_offset = payload.get("nextOffset")
                total = payload.get("total")
                if isinstance(next_offset, int) and next_offset > offset:
                    more, following_offset = True, next_offset
                elif isinstance(total, int) and total > offset + len(rows):
                    more, following_offset = True, offset + len(rows)
                elif len(rows) >= limit:
                    # Continue conservatively when a full page has no pagination metadata.
                    more, following_offset = True, offset + len(rows)
                else:
                    more, following_offset = False, offset

                queries.append({
                    "kind": kind, "region_id": region["id"], "page": page + 1,
                    "ok": True, "rows": len(rows), "normalized_rows": len(normalized),
                    "total": total if isinstance(total, int) else None,
                    "cap_applied": more and page + 1 == max_pages,
                })
                if not more or page + 1 == max_pages:
                    break
                if following_offset <= offset:
                    raise ValueError("events pagination offset did not advance")
                offset = following_offset
            except Exception as exc:
                queries.append({
                    "kind": kind, "region_id": region["id"], "page": page + 1,
                    "ok": False, "error": type(exc).__name__,
                })
                break
    return events, queries


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
        event_rows, event_queries = fetch_event_region(
            session, region, day-timedelta(days=7), end, max_pages=max_pages
        )
        events.extend(event_rows)
        queries.extend(event_queries)
    return grid, events, queries


def fetch_daily_events(session, selected_regions, now):
    # GFW event data are delayed. Query only dates old enough to be available,
    # and retain the product within the existing rolling 14-day storage bound.
    end_date = now.date() - timedelta(days=4)
    start_date = now.date() - timedelta(days=14)
    all_events, queries = [], []
    for region in selected_regions:
        event_rows, event_queries = fetch_event_region(
            session, region, start_date, end_date, max_pages=40
        )
        all_events.extend(event_rows)
        queries.extend(event_queries)

    expected_queries = len(selected_regions) * len(EVENT_DATASETS)
    completed_queries = {
        (query.get("region_id"), query.get("kind"))
        for query in queries if query.get("ok") and not query.get("cap_applied")
    }
    api_complete = (
        len(completed_queries) == expected_queries
        and all(query.get("ok") and not query.get("cap_applied") for query in queries)
    )

    previous = {}
    try:
        previous = json.loads(EVENT_STATUS.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, json.JSONDecodeError):
        previous = {}
    previous_product = {}
    try:
        previous_product = json.loads(EVENTS.read_text(encoding="utf-8"))
        if not isinstance(previous_product, dict) or not isinstance(previous_product.get("events"), list):
            previous_product = {}
    except (OSError, json.JSONDecodeError):
        previous_product = {}

    failures = [
        {key: query.get(key) for key in ("kind", "region_id", "page", "error", "cap_applied")}
        for query in queries if not query.get("ok") or query.get("cap_applied")
    ]
    product_written = False
    size_limited = False
    if api_complete:
        dedup = {(event["type"], event["id"]): event for event in all_events}
        product = bound_gfw({
            "schema_version": "1.0.0",
            "generated_at": iso_z(now),
            "coverage_start_date": start_date.isoformat(),
            "coverage_end_date_exclusive": end_date.isoformat(),
            "query_regions": [{"id": region["id"], "geometry": region["geometry"]}
                              for region in selected_regions],
            "events": list(dedup.values()),
            "count": len(dedup),
            "historical": True,
            "not_current_positions": True,
            "coverage_complete": True,
            "attribution": ATTRIBUTION,
            "license": "CC BY-NC 4.0",
            "queries": queries,
        }, "events", max_bytes=4 * 1024 * 1024 - 128 * 1024)
        size_limited = bool(product.get("cap_applied"))
        product["coverage_complete"] = not size_limited
        product["status"] = "storage_limited" if size_limited else "complete"
        atomic_json(EVENTS, product)
        product_written = True

    available_product = product_written or bool(previous_product)
    retained_events = product.get("events", []) if product_written else previous_product.get("events", [])
    coverage_complete = product_written and not size_limited
    status_name = "complete" if coverage_complete else (
        "storage_limited" if product_written and size_limited else "incomplete"
    )
    event_status = {
        "schema_version": "1.0.0",
        "generated_at": iso_z(now),
        "historical": True,
        "provider": "Global Fishing Watch",
        "status": status_name,
        "coverage_complete": coverage_complete,
        "coverage_start_date": start_date.isoformat(),
        "coverage_end_date_exclusive": end_date.isoformat(),
        "provider_delay_days": 4,
        "expected_query_count": expected_queries,
        "completed_query_count": len(completed_queries),
        "query_count": len(queries),
        "queries": queries,
        "failures": failures,
        "product_written": product_written,
        "product_available": available_product,
        "event_count": len(retained_events),
        "loitering_count": sum(1 for event in retained_events if event.get("type") == "LOITERING"),
        "size_limited": size_limited,
        "last_complete_generated_at": (
            iso_z(now) if coverage_complete
            else previous.get("last_complete_generated_at")
        ),
        "product_generated_at": (
            iso_z(now) if product_written
            else previous_product.get("generated_at")
        ),
        "message": (
            "All GFW event queries completed."
            if coverage_complete else
            "All queries completed, but the 4 MiB output bound removed some events."
            if size_limited else
            "Event coverage is incomplete; the last complete product was preserved when available."
        ),
    }
    atomic_json(EVENT_STATUS, event_status)
    print(json.dumps({
        "status": status_name,
        "coverage_start_date": start_date.isoformat(),
        "coverage_end_date_exclusive": end_date.isoformat(),
        "query_count": len(queries),
        "completed_query_count": len(completed_queries),
        "event_count": len(retained_events),
        "product_written": product_written,
    }, ensure_ascii=False))
    # An incomplete provider response is published as status, not as a zero-result failure.
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", help="UTC observation day; default five days ago")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--presence-only", action="store_true")
    mode.add_argument("--events-only", action="store_true",
                      help="Refresh delayed GFW Loitering/GAP events without changing AIS presence products")
    parser.add_argument("--candidates", type=Path, help="Use the actual SAR acquisition day and scene/AOI footprint")
    parser.add_argument("--bbox", type=float, nargs=4, help="Scene/AOI west south east north")
    args = parser.parse_args(argv)
    if args.events_only and args.candidates:
        parser.error("--events-only cannot be combined with --candidates")
    token = os.environ.get("GFW_TOKEN", "").strip()
    if not token:
        raise SystemExit("GFW_TOKEN missing; no live request performed")
    now = datetime.now(timezone.utc)
    selected_regions = regions()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "User-Agent": "MOwlSINT-GFW-context/1.0"})
    if args.events_only:
        return fetch_daily_events(session, selected_regions, now)

    scene_id = None
    if args.candidates:
        from copernicus_historical_context import validate_candidates
        payload = json.loads(args.candidates.read_text())
        scene_id, acquired = validate_candidates(payload)
        day = acquired.date()
        bbox = args.bbox or payload.get("metadata", {}).get("scene", {}).get("bbox")
        if bbox is None:
            pilot = json.loads((ROOT / "config/sar_copernicus_pilot.json").read_text())
            if scene_id == pilot["expected_sentinel_hub_product_id"]:
                bbox = pilot["aoi"]["bbox_wgs84"]
        if not bbox or len(bbox) != 4:
            raise SystemExit("Actual scene footprint/AOI bbox required for GFW context")
        west, south, east, north = bbox
        if not all(finite(v) is not None for v in bbox) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise SystemExit("Invalid scene/AOI bbox")
        if any(not (west <= f["geometry"]["coordinates"][0] <= east and south <= f["geometry"]["coordinates"][1] <= north)
               for f in payload["features"]):
            raise SystemExit("Scene/AOI bbox does not contain all compared candidates")
        selected_regions = [{"id": "sar_scene", "name": scene_id, "geometry": {
            "type": "Polygon",
            "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
        }}]
    else:
        day = datetime.fromisoformat(args.day).date() if args.day else now.date() - timedelta(days=5)
    if day > now.date() - timedelta(days=4):
        raise SystemExit("Observation day must be at least four days old")

    cells, events, queries = [], [], []
    for region in selected_regions:
        grid_rows, event_rows, region_queries = fetch_region(
            session, region, day, not args.presence_only
        )
        cells.extend(grid_rows)
        events.extend(event_rows)
        queries.extend(region_queries)
    common = {
        "schema_version": "1.0.0", "generated_at": iso_z(now),
        "observation_day": day.isoformat(), "scene_id": scene_id,
        "query_regions": [{"id": region["id"], "geometry": region["geometry"]}
                          for region in selected_regions],
        "historical": True, "not_current_positions": True,
        "attribution": ATTRIBUTION, "license": "CC BY-NC 4.0",
        "coverage_complete": False,
    }
    grid_ok = [query for query in queries if query["kind"] == "presence_grid" and query["ok"]]
    if grid_ok:
        dedup = {(cell["latitude"], cell["longitude"], cell["interval_start"], cell.get("flag")): cell
                 for cell in cells}
        records = list(dedup.values())[:20000]
        grid_pack = {
            **common, "records": records, "count": len(records),
            "vessel_identity_available": False, "cap_applied": len(dedup) > len(records),
            "queries": [query for query in queries if query["kind"] == "presence_grid"],
        }
        atomic_json(GRID, bound_gfw(grid_pack, "presence"))
        atomic_json(ROOT / "data/gfw_historical_ais_status_latest.json", {
            **common, "status": "vessel_identified_cells_unavailable",
            "coverage_complete": False,
            "current_presence_grid": "data/gfw_presence_cells_latest.json",
            "count": 0,
            "reason": "GFW restriction 2026-11-03; grid cells cannot populate vessel tracks",
        })
    event_queries = [query for query in queries if query["kind"] in EVENT_DATASETS]
    # Preserve a coherent last-known-good event product if a region or page failed/truncated.
    if event_queries and all(query["ok"] and not query.get("cap_applied") for query in event_queries):
        dedup = {(event["type"], event["id"]): event for event in events}
        if len(dedup) <= 10000:
            atomic_json(EVENTS, bound_gfw({
                **common, "events": list(dedup.values()), "count": len(dedup),
                "queries": event_queries,
            }, "events"))
    atomic_json(STATUS, {
        **common, "queries": queries, "live_requests_performed": True,
        "ok": bool(queries) and all(query["ok"] and not query.get("cap_applied") for query in queries),
    })
    return 0 if any(query["ok"] for query in queries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
