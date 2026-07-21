#!/usr/bin/env python3
"""Analyse Voodoo Whiskers tracks near public maritime infrastructure layers.

This is an analyst-review aid. It never labels proximity as sabotage, espionage or
hostile action and does not directly modify the Magic Paws Hybrid Index.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
CONFIG = json.loads((ROOT / "config" / "infrastructure_watch.json").read_text(encoding="utf-8"))
VESSEL_SNAPSHOT = PUBLIC / "data" / "vessels" / "voi_snapshot_latest.json"
VESSEL_HISTORY = PUBLIC / "data" / "vessels" / "voi_history_14d.jsonl"
REFERENCE_DIR = PUBLIC / "data" / "reference" / "emodnet"
ANALYSIS_DIR = PUBLIC / "data" / "analysis"
DOWNLOAD_DIR = PUBLIC / "downloads"

REFERENCE_FILES = {
    "telecommunication_cable": "telecom_cables.geojson",
    "power_cable": "power_cables.geojson",
    "cable_landing": "cable_landings.geojson",
    "pipeline": "pipelines.geojson",
    "wind_farm": "wind_farms.geojson",
    "offshore_energy": "offshore_energy.geojson",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(text)
        name = tmp.name
    Path(name).replace(path)


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def fnum(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def identity(item: dict[str, Any]) -> str:
    for key in ("mmsi", "imo", "callsign", "name"):
        value = str(item.get(key) or "").strip().upper()
        if value:
            return f"{key}:{value}"
    return ""


def feature_name(feature: dict[str, Any], fallback: str) -> str:
    props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    for key in ("name", "Name", "NAME", "title", "Title", "code", "Code", "id"):
        value = str(props.get(key) or "").strip()
        if value:
            return value
    return fallback


def coordinate_pairs(value: Any) -> Iterable[tuple[float, float]]:
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
        yield float(value[0]), float(value[1])
    elif isinstance(value, list):
        for item in value:
            yield from coordinate_pairs(item)


def geometry_parts(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    gtype = str(geometry.get("type") or "")
    coords = geometry.get("coordinates")
    if gtype == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return [[(float(coords[0]), float(coords[1]))]]
    if gtype in {"MultiPoint", "LineString"} and isinstance(coords, list):
        return [[(float(x[0]), float(x[1])) for x in coords if isinstance(x, list) and len(x) >= 2]]
    if gtype in {"MultiLineString", "Polygon"} and isinstance(coords, list):
        return [[(float(x[0]), float(x[1])) for x in part if isinstance(x, list) and len(x) >= 2] for part in coords if isinstance(part, list)]
    if gtype == "MultiPolygon" and isinstance(coords, list):
        return [[(float(x[0]), float(x[1])) for x in ring if isinstance(x, list) and len(x) >= 2] for poly in coords if isinstance(poly, list) for ring in poly if isinstance(ring, list)]
    return []


def local_xy_nm(lon: float, lat: float, ref_lon: float, ref_lat: float) -> tuple[float, float]:
    mean_lat = math.radians((lat + ref_lat) / 2)
    x = (lon - ref_lon) * 60.0 * math.cos(mean_lat)
    y = (lat - ref_lat) * 60.0
    return x, y


def point_segment_distance_nm(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    lon, lat = point
    ax, ay = local_xy_nm(a[0], a[1], lon, lat)
    bx, by = local_xy_nm(b[0], b[1], lon, lat)
    vx, vy = bx - ax, by - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * vx + ay * vy) / denom))
    return math.hypot(ax + t * vx, ay + t * vy)


def point_feature_distance_nm(point: tuple[float, float], feature: dict[str, Any]) -> float:
    geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
    parts = geometry_parts(geometry)
    best = float("inf")
    for part in parts:
        if not part:
            continue
        if len(part) == 1:
            x, y = local_xy_nm(part[0][0], part[0][1], point[0], point[1])
            best = min(best, math.hypot(x, y))
            continue
        for a, b in zip(part, part[1:]):
            best = min(best, point_segment_distance_nm(point, a, b))
    return best


def feature_bbox(feature: dict[str, Any]) -> tuple[float, float, float, float] | None:
    geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
    coords = list(coordinate_pairs(geometry.get("coordinates")))
    if not coords:
        return None
    return min(x for x, _ in coords), min(y for _, y in coords), max(x for x, _ in coords), max(y for _, y in coords)


def cells_for_bbox(bbox: tuple[float, float, float, float], pad_deg: float = 0.05) -> Iterable[tuple[int, int]]:
    west, south, east, north = bbox
    west -= pad_deg
    south -= pad_deg
    east += pad_deg
    north += pad_deg
    for x in range(math.floor(west), math.floor(east) + 1):
        for y in range(math.floor(south), math.floor(north) + 1):
            yield x, y


def load_reference_index() -> tuple[dict[tuple[int, int], list[dict[str, Any]]], list[dict[str, Any]], dict[str, int]]:
    grid: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    all_features: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for infrastructure_type, filename in REFERENCE_FILES.items():
        path = REFERENCE_DIR / filename
        if not path.exists():
            counts[infrastructure_type] = 0
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            counts[infrastructure_type] = 0
            continue
        features = payload.get("features") if isinstance(payload.get("features"), list) else []
        counts[infrastructure_type] = len(features)
        for index, raw in enumerate(features):
            if not isinstance(raw, dict):
                continue
            feature = dict(raw)
            feature["_vw_infrastructure_type"] = infrastructure_type
            feature["_vw_reference_id"] = str(feature.get("id") or f"{infrastructure_type}:{index}")
            bbox = feature_bbox(feature)
            if not bbox:
                continue
            feature["_vw_bbox"] = bbox
            all_features.append(feature)
            for cell in cells_for_bbox(bbox, pad_deg=0.08):
                grid[cell].append(feature)
    return grid, all_features, counts


def candidate_features(grid: dict[tuple[int, int], list[dict[str, Any]]], point: tuple[float, float], radius_nm: float) -> list[dict[str, Any]]:
    lon, lat = point
    lat_deg = radius_nm / 60.0
    lon_deg = radius_nm / max(10.0, 60.0 * math.cos(math.radians(lat)))
    cells = set(cells_for_bbox((lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg), pad_deg=0))
    unique: dict[str, dict[str, Any]] = {}
    for cell in cells:
        for feature in grid.get(cell, []):
            unique[feature["_vw_reference_id"]] = feature
    return list(unique.values())


def nearest_feature(grid: dict[tuple[int, int], list[dict[str, Any]]], point: tuple[float, float], radius_nm: float) -> tuple[dict[str, Any] | None, float]:
    best_feature = None
    best_distance = float("inf")
    for feature in candidate_features(grid, point, radius_nm):
        distance = point_feature_distance_nm(point, feature)
        if distance < best_distance:
            best_feature = feature
            best_distance = distance
    return best_feature, best_distance


def load_tracks(cutoff: datetime) -> dict[str, list[dict[str, Any]]]:
    tracks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not VESSEL_HISTORY.exists():
        return tracks
    with VESSEL_HISTORY.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            dt = parse_dt(item.get("observed_at"))
            lat = fnum(item.get("latitude"))
            lon = fnum(item.get("longitude"))
            key = identity(item)
            if not dt or dt < cutoff or not key or lat is None or lon is None:
                continue
            item["_dt"] = dt
            tracks[key].append(item)
    for rows in tracks.values():
        rows.sort(key=lambda item: item["_dt"])
    return tracks


def count_entries(values: list[bool]) -> int:
    entries = 0
    previous = False
    for value in values:
        if value and not previous:
            entries += 1
        previous = value
    return entries


def prepare_track_points(item: dict[str, Any], track: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = [dict(row) for row in track if isinstance(row, dict)]
    current = dict(item)
    current_dt = parse_dt(current.get("observed_at"))
    if current_dt:
        current["_dt"] = current_dt
        current_lat, current_lon = fnum(current.get("latitude")), fnum(current.get("longitude"))
        current_key = (
            round(current_lat, 5) if current_lat is not None else 999,
            round(current_lon, 5) if current_lon is not None else 999,
            current_dt.isoformat(),
        )
        existing = {
            (
                round(fnum(row.get("latitude")), 5) if fnum(row.get("latitude")) is not None else 999,
                round(fnum(row.get("longitude")), 5) if fnum(row.get("longitude")) is not None else 999,
                (row.get("_dt") or parse_dt(row.get("observed_at"))).isoformat(),
            )
            for row in points
            if (row.get("_dt") or parse_dt(row.get("observed_at")))
        }
        if current_key not in existing:
            points.append(current)
    if not points:
        points = [current]
    points.sort(key=lambda row: row.get("_dt") or parse_dt(row.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return points


def candidate_features_for_track(
    grid: dict[tuple[int, int], list[dict[str, Any]]],
    points: list[dict[str, Any]],
    contextual_nm: float,
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for row in points:
        lon, lat = fnum(row.get("longitude")), fnum(row.get("latitude"))
        if lon is None or lat is None:
            continue
        point = (lon, lat)
        for feature in candidate_features(grid, point, contextual_nm):
            if point_feature_distance_nm(point, feature) <= contextual_nm:
                candidates[feature["_vw_reference_id"]] = feature
    return list(candidates.values())


def evaluate_feature(
    item: dict[str, Any],
    points: list[dict[str, Any]],
    feature: dict[str, Any],
) -> dict[str, Any] | None:
    bands = CONFIG["distance_bands_nm"]
    close = float(bands["close"])
    immediate = float(bands["immediate"])
    evaluated: list[tuple[dict[str, Any], float, bool]] = []
    for row in points:
        lon, lat = fnum(row.get("longitude")), fnum(row.get("latitude"))
        if lon is None or lat is None:
            continue
        distance = point_feature_distance_nm((lon, lat), feature)
        evaluated.append((row, distance, distance <= close))
    if not evaluated:
        return None

    near_rows = [row for row, _distance, is_near in evaluated if is_near]
    if not near_rows:
        return None
    low_speed_rows = [
        row for row in near_rows
        if fnum(row.get("sog")) is not None and fnum(row.get("sog")) <= float(CONFIG["low_speed_knots"])
    ]
    flags = [is_near for _row, _distance, is_near in evaluated]
    entries = count_entries(flags)

    dwell_minutes = 0.0
    max_gap_minutes = float(CONFIG.get("maximum_track_gap_minutes", 180))
    segment_start: datetime | None = None
    segment_last: datetime | None = None
    for row, _distance, is_near in evaluated:
        dt = row.get("_dt") or parse_dt(row.get("observed_at"))
        if not is_near or not dt:
            if segment_start and segment_last:
                dwell_minutes = max(dwell_minutes, (segment_last - segment_start).total_seconds() / 60.0)
            segment_start = segment_last = None
            continue
        if segment_last and (dt - segment_last).total_seconds() / 60.0 > max_gap_minutes:
            dwell_minutes = max(dwell_minutes, (segment_last - segment_start).total_seconds() / 60.0)
            segment_start = dt
        elif segment_start is None:
            segment_start = dt
        segment_last = dt
    if segment_start and segment_last:
        dwell_minutes = max(dwell_minutes, (segment_last - segment_start).total_seconds() / 60.0)

    behavior_signals: list[str] = []
    if len(low_speed_rows) >= int(CONFIG.get("minimum_near_points", 2)):
        behavior_signals.append("low_speed_near_infrastructure")
    if dwell_minutes >= float(CONFIG["minimum_dwell_minutes"]):
        behavior_signals.append("extended_presence_near_infrastructure")
    if entries >= int(CONFIG["repeated_entries"]):
        behavior_signals.append("repeated_entries_into_close_band")
    if not behavior_signals:
        return None

    closest_row, min_distance, _ = min(evaluated, key=lambda value: value[1])
    latest_lon, latest_lat = fnum(item.get("longitude")), fnum(item.get("latitude"))
    latest_distance = point_feature_distance_nm((latest_lon, latest_lat), feature) if latest_lon is not None and latest_lat is not None else None
    timestamp_valid_count = sum(1 for row in near_rows if row.get("position_timestamp_valid", True) is not False)
    timestamp_valid_ratio = timestamp_valid_count / max(1, len(near_rows))

    categories = [str(x) for x in item.get("categories", [])] if isinstance(item.get("categories"), list) else []
    context_signals = ["critical_infrastructure_proximity", *behavior_signals]
    if any(x in categories for x in ("watchlist", "sanctions_shadowfleet", "falseflag_interest", "russian_mmsi", "recent_russian_portcall_10d")):
        context_signals.append("voi_or_watchlist_context")
    if timestamp_valid_ratio < 1.0:
        context_signals.append("timestamp_quality_limited")

    level = "review"
    if min_distance <= immediate or (min_distance <= close and len(behavior_signals) >= 2):
        level = "elevated"
    confidence = "medium" if len(near_rows) >= 3 and timestamp_valid_ratio >= 0.67 else "low_medium"
    props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    reference_id = feature["_vw_reference_id"]
    event_dt = closest_row.get("_dt") or parse_dt(closest_row.get("observed_at"))

    return {
        "event_id": f"VWI-{str(item.get('mmsi') or item.get('imo') or 'unknown')}-{reference_id.replace(':','-')}",
        "event_type": "critical_infrastructure_proximity",
        "level": level,
        "confidence": confidence,
        "vessel": {
            "mmsi": item.get("mmsi"), "imo": item.get("imo"), "name": item.get("name"),
            "callsign": item.get("callsign"), "categories": categories, "source": item.get("source"),
        },
        "infrastructure": {
            "type": feature["_vw_infrastructure_type"], "reference_id": reference_id,
            "name": feature_name(feature, reference_id), "source": "EMODnet Human Activities",
            "source_typename": props.get("_emodnet_typename"),
        },
        "observation": {
            "event_position": {
                "latitude": closest_row.get("latitude"), "longitude": closest_row.get("longitude"),
                "observed_at": event_dt.isoformat() if event_dt else closest_row.get("observed_at"),
            },
            "latest_position": {
                "latitude": item.get("latitude"), "longitude": item.get("longitude"), "observed_at": item.get("observed_at"),
            },
            "minimum_distance_nm": round(min_distance, 3),
            "latest_distance_nm": round(latest_distance, 3) if latest_distance is not None and math.isfinite(latest_distance) else None,
            "track_points_considered": len(evaluated), "points_within_close_band": len(near_rows),
            "low_speed_points_within_close_band": len(low_speed_rows),
            "estimated_dwell_minutes": round(max(0.0, dwell_minutes), 1), "close_band_entries": entries,
            "source_timestamp_points": timestamp_valid_count,
            "fallback_timestamp_points": len(near_rows) - timestamp_valid_count,
        },
        "signals": context_signals,
        "assessment": "Behaviour warrants analyst review. Proximity and movement patterns alone do not indicate hostile intent, attribution or unlawful activity.",
        "score_integration": False,
    }


def analyse_vessel(
    item: dict[str, Any],
    track: list[dict[str, Any]],
    grid: dict[tuple[int, int], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    points = prepare_track_points(item, track)
    contextual = float(CONFIG["distance_bands_nm"]["contextual"])
    events = [event for feature in candidate_features_for_track(grid, points, contextual) if (event := evaluate_feature(item, points, feature))]
    events.sort(key=lambda event: (0 if event["level"] == "elevated" else 1, event["observation"]["minimum_distance_nm"]))
    return events[: max(1, int(CONFIG.get("max_events_per_vessel", 3)))]


def event_feature(event: dict[str, Any]) -> dict[str, Any]:
    position = event["observation"]["event_position"]
    props = {k: v for k, v in event.items() if k != "observation"}
    props["observation"] = event["observation"]
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [position["longitude"], position["latitude"]]},
        "properties": props,
    }


def markdown(events: list[dict[str, Any]], generated_at: str, reference_counts: dict[str, int]) -> str:
    lines = [
        "# Voodoo Whiskers — Critical Infrastructure Watch",
        "",
        f"- Generated: {generated_at}",
        f"- Review events: {len(events)}",
        f"- Reference features: {sum(reference_counts.values())}",
        "- Score integration: disabled (shadow/calibration phase)",
        "",
        "> Proximity and movement patterns are analyst leads. They do not establish sabotage, espionage, hostile intent, attribution or unlawful activity.",
        "",
    ]
    if not events:
        lines += ["No combined proximity-and-behaviour events were generated from the currently available public reference layers and AIS observations.", ""]
    for event in events:
        vessel = event["vessel"]
        infrastructure = event["infrastructure"]
        observation = event["observation"]
        lines += [
            f"## {vessel.get('name') or vessel.get('mmsi') or 'Unknown vessel'} — {event['level'].upper()}",
            "",
            f"- MMSI / IMO: {vessel.get('mmsi') or '–'} / {vessel.get('imo') or '–'}",
            f"- Infrastructure: {infrastructure.get('type')} — {infrastructure.get('name')}",
            f"- Minimum distance: {observation.get('minimum_distance_nm')} nm",
            f"- Close-band points: {observation.get('points_within_close_band')}",
            f"- Estimated dwell: {observation.get('estimated_dwell_minutes')} min",
            f"- Signals: {', '.join(event.get('signals') or [])}",
            f"- Confidence: {event.get('confidence')}",
            "",
            event["assessment"],
            "",
        ]
    return "\n".join(lines)


def write_csv(events: list[dict[str, Any]]) -> None:
    import csv
    path = DOWNLOAD_DIR / "infrastructure_watch_latest.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="") as tmp:
        fields = ["event_id", "level", "confidence", "vessel_name", "mmsi", "imo", "categories", "infrastructure_type", "infrastructure_name", "minimum_distance_nm", "latest_distance_nm", "dwell_minutes", "close_band_entries", "signals", "assessment"]
        writer = csv.DictWriter(tmp, fieldnames=fields)
        writer.writeheader()
        for event in events:
            writer.writerow({
                "event_id": event["event_id"],
                "level": event["level"],
                "confidence": event["confidence"],
                "vessel_name": event["vessel"].get("name"),
                "mmsi": event["vessel"].get("mmsi"),
                "imo": event["vessel"].get("imo"),
                "categories": ",".join(event["vessel"].get("categories") or []),
                "infrastructure_type": event["infrastructure"].get("type"),
                "infrastructure_name": event["infrastructure"].get("name"),
                "minimum_distance_nm": event["observation"].get("minimum_distance_nm"),
                "latest_distance_nm": event["observation"].get("latest_distance_nm"),
                "dwell_minutes": event["observation"].get("estimated_dwell_minutes"),
                "close_band_entries": event["observation"].get("close_band_entries"),
                "signals": ",".join(event.get("signals") or []),
                "assessment": event["assessment"],
            })
        name = tmp.name
    Path(name).replace(path)


def main() -> int:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = iso()
    snapshot = json.loads(VESSEL_SNAPSHOT.read_text(encoding="utf-8")) if VESSEL_SNAPSHOT.exists() else {"items": []}
    items = [item for item in snapshot.get("items", []) if isinstance(item, dict) and item.get("is_priority_voi")]
    cutoff = utc_now() - timedelta(hours=int(CONFIG.get("analysis_window_hours", 120)))
    tracks = load_tracks(cutoff)
    grid, reference_features, reference_counts = load_reference_index()

    events = []
    for item in items:
        events.extend(analyse_vessel(item, tracks.get(identity(item), []), grid))
    events.sort(key=lambda event: (0 if event["level"] == "elevated" else 1, event["observation"]["minimum_distance_nm"]))

    payload = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "source": "Voodoo Whiskers",
        "reference_source": "EMODnet Human Activities",
        "score_integration": False,
        "analysis_window_hours": int(CONFIG.get("analysis_window_hours", 120)),
        "reference_ready": bool(reference_features),
        "reference_feature_counts": reference_counts,
        "event_count": len(events),
        "assessment_limit": "Proximity and movement patterns are analyst leads and do not establish hostile intent, attribution or unlawful activity.",
        "events": events,
    }
    geojson = {
        "type": "FeatureCollection",
        "features": [event_feature(event) for event in events],
        "metadata": {k: v for k, v in payload.items() if k != "events"},
    }
    summary = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "reference_ready": bool(reference_features),
        "reference_feature_count": len(reference_features),
        "event_count": len(events),
        "elevated_count": sum(1 for event in events if event["level"] == "elevated"),
        "review_count": sum(1 for event in events if event["level"] == "review"),
        "vessels_considered": len(items),
        "score_integration": False,
    }
    maximum_delta = int(CONFIG.get("score_shadow", {}).get("maximum_delta", 5))
    suggested = min(maximum_delta, summary["elevated_count"] * 2 + min(2, summary["review_count"]))
    score_shadow = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "mode": "shadow",
        "active_score_integration": False,
        "suggested_score_delta": suggested,
        "maximum_allowed_delta": maximum_delta,
        "reasons": [
            f"{summary['elevated_count']} elevated infrastructure-proximity event(s)",
            f"{summary['review_count']} review event(s)",
        ],
        "calibration_note": "Do not activate before a multi-week false-positive and traffic-baseline review.",
    }

    atomic_json(ANALYSIS_DIR / "infrastructure_events_latest.json", payload)
    atomic_json(ANALYSIS_DIR / "infrastructure_events_latest.geojson", geojson)
    atomic_json(ANALYSIS_DIR / "infrastructure_summary_latest.json", summary)
    atomic_json(ANALYSIS_DIR / "infrastructure_score_shadow.json", score_shadow)
    atomic_json(DOWNLOAD_DIR / "infrastructure_watch_latest.json", payload)
    atomic_json(DOWNLOAD_DIR / "infrastructure_watch_latest.geojson", geojson)
    atomic_text(DOWNLOAD_DIR / "infrastructure_watch_latest.md", markdown(events, generated_at, reference_counts))
    write_csv(events)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
