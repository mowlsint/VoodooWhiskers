#!/usr/bin/env python3
"""Fetch delayed GFW SAR detections and time-aligned historical AIS context.

The public comparison product intentionally keeps current AIS layers separate. SAR
markers and AIS-presence markers are 0.01-degree report-cell centres, not exact
positions. Only GFW AIS-presence rows from the same historical time window are
used for connectors. AIS-unmatched SAR detections remain unconnected review leads.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests

ROOT = Path(__file__).resolve().parents[1]
API_REPORT = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
API_LAST_REPORT = "https://gateway.api.globalfishingwatch.org/v3/4wings/last-report"
SAR_DATASET = "public-global-sar-presence:latest"
AIS_PRESENCE_DATASET = "public-global-presence:latest"
ATTRIBUTION = "Powered by Global Fishing Watch."
ATTRIBUTION_URL = "https://globalfishingwatch.org"
ASSESSMENT_LIMIT = (
    "Delayed SAR and historical AIS-presence cells support analyst review. Grid-cell "
    "centres are not exact vessel positions. An AIS-unmatched SAR detection does not by "
    "itself establish intentional AIS disablement, identity, unlawful activity, attribution "
    "or hostile intent."
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    raw = clean_text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_observed_at(row: dict[str, Any]) -> str | None:
    for key in ("entryTimestamp", "exitTimestamp", "observed_at", "timestamp"):
        parsed = parse_datetime(row.get(key))
        if parsed:
            return iso_z(parsed)
    parsed = parse_datetime(row.get("date"))
    return iso_z(parsed) if parsed else None


def atomic_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        if compact:
            json.dump(payload, tmp, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        name = tmp.name
    Path(name).replace(path)


def load_regions(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError(f"region file is not a FeatureCollection: {path}")
    regions: list[dict[str, Any]] = []
    for index, feature in enumerate(payload.get("features") or []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        props = feature.get("properties") or {}
        region_id = clean_text(props.get("id")) or f"region_{index + 1}"
        regions.append(
            {
                "id": region_id,
                "name": clean_text(props.get("name")) or region_id,
                "geometry": geometry,
                "note": clean_text(props.get("note")),
            }
        )
    if not regions:
        raise ValueError(f"no Polygon or MultiPolygon regions found in {path}")
    return regions


def load_watchlist(path: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    by_imo: dict[str, dict[str, str]] = {}
    by_mmsi: dict[str, dict[str, str]] = {}
    if not path.exists():
        return by_imo, by_mmsi
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            normalized = {str(key): clean_text(value) for key, value in row.items() if key is not None}
            imo = digits(normalized.get("imo"))
            mmsi = digits(normalized.get("mmsi"))
            if len(imo) == 7:
                by_imo[imo] = normalized
            if len(mmsi) == 9:
                by_mmsi[mmsi] = normalized
    return by_imo, by_mmsi


def watchlist_categories(row: dict[str, str] | None) -> list[str]:
    if not row:
        return []
    mapping = {
        "track_sanctions": "sanctions_shadowfleet",
        "track_shadowfleet": "shadowfleet",
        "track_falseflag": "falseflag_interest",
        "track_behavior": "behavioral_voi",
        "track_russian_mmsi": "russian_mmsi",
    }
    categories = [category for column, category in mapping.items() if truthy(row.get(column))]
    categories.append("watchlist")
    return sorted(set(categories))


def flatten_report(payload: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    dataset_version: str | None = None
    rows: list[dict[str, Any]] = []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return dataset_version, rows
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key, value in entry.items():
            if not isinstance(value, list):
                continue
            if str(key).startswith(("public-global-sar-presence:", "public-global-presence:")):
                dataset_version = str(key)
            for row in value:
                if isinstance(row, dict):
                    copied = dict(row)
                    copied["_source_dataset"] = str(key)
                    rows.append(copied)
    return dataset_version, rows


def report_is_finished(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("entries"), list)


def batches(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def quote_filter(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@dataclass
class GFWClient:
    token: str
    request_timeout: int = 120
    poll_timeout: int = 900
    poll_interval: int = 15

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "VoodooWhiskers-GFW-SAR-AIS/0.2",
            }
        )

    def _poll_last_report(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.poll_timeout
        last_status = "unknown"
        while time.monotonic() < deadline:
            response = self.session.get(API_LAST_REPORT, timeout=min(self.request_timeout, 60))
            if response.status_code == 404:
                time.sleep(self.poll_interval)
                continue
            response.raise_for_status()
            payload = response.json()
            if report_is_finished(payload):
                return payload
            if isinstance(payload, dict):
                last_status = str(payload.get("status") or payload.get("message") or "running")
                if payload.get("status") not in {None, "running"}:
                    raise RuntimeError(f"GFW last report failed: {payload}")
            time.sleep(self.poll_interval)
        raise TimeoutError(f"GFW report did not finish within {self.poll_timeout}s; last status={last_status}")

    def report(
        self,
        geometry: dict[str, Any],
        start_date: date,
        end_date: date,
        *,
        dataset: str,
        filters: list[str] | None = None,
        group_by: str | None = None,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("spatial-resolution", "HIGH"),
            ("temporal-resolution", "HOURLY"),
            ("spatial-aggregation", "false"),
            ("datasets[0]", dataset),
            ("date-range", f"{start_date.isoformat()},{end_date.isoformat()}"),
            ("format", "JSON"),
        ]
        if group_by:
            params.append(("group-by", group_by))
        for item in filters or []:
            params.append(("filters[0]", item))
        body = {"geojson": geometry}

        for attempt in range(2):
            try:
                response = self.session.post(API_REPORT, params=params, json=body, timeout=self.request_timeout)
            except requests.Timeout:
                return self._poll_last_report()
            if response.status_code == 524:
                return self._poll_last_report()
            if response.status_code == 429:
                if attempt == 0:
                    try:
                        self._poll_last_report()
                    except Exception:
                        pass
                    time.sleep(5)
                    continue
                raise RuntimeError("GFW refused the report because another report is still running (HTTP 429)")
            response.raise_for_status()
            payload = response.json()
            if report_is_finished(payload):
                return payload
            if isinstance(payload, dict) and payload.get("status") == "running":
                return self._poll_last_report()
            raise RuntimeError(f"unexpected GFW report response: {payload}")
        raise RuntimeError("GFW report retry exhausted")


def build_sar_records(
    rows: Iterable[dict[str, Any]],
    *,
    region: dict[str, Any],
    ais_matched: bool,
    watch_imo: dict[str, dict[str, str]],
    watch_mmsi: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    stats = {"rows_seen": 0, "rows_invalid": 0, "duplicates": 0}
    seen: set[tuple[Any, ...]] = set()

    for row in rows:
        stats["rows_seen"] += 1
        lat = parse_float(row.get("lat"))
        lon = parse_float(row.get("lon"))
        observed_at = parse_observed_at(row)
        if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or not observed_at:
            stats["rows_invalid"] += 1
            continue
        detections = max(1, int(parse_float(row.get("detections")) or 1))
        imo = digits(row.get("imo"))
        mmsi = digits(row.get("mmsi"))
        vessel_id = clean_text(row.get("vesselId") or row.get("vessel_id"))
        dedupe_key = (region["id"], ais_matched, observed_at, round(lat, 5), round(lon, 5), vessel_id, imo, mmsi)
        if dedupe_key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(dedupe_key)

        watch_row = watch_imo.get(imo) if len(imo) == 7 else None
        match_basis: list[str] = []
        if watch_row:
            match_basis.append("imo")
        if len(mmsi) == 9 and mmsi in watch_mmsi:
            if not watch_row:
                watch_row = watch_mmsi[mmsi]
            match_basis.append("mmsi")
        watch_match = bool(watch_row)
        source_dataset = clean_text(row.get("_source_dataset")) or SAR_DATASET
        identity = "|".join(str(part) for part in dedupe_key)
        record_id = "sar_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:18]

        records.append(
            {
                "id": record_id,
                "feature_role": "sar_detection",
                "provider": "global_fishing_watch",
                "source_dataset": source_dataset,
                "coverage_mode": "satellite_sar_delayed",
                "historical": True,
                "not_current_position": True,
                "position_is_exact": False,
                "location_representation": "0.01_degree_grid_cell_center",
                "region_id": region["id"],
                "region_name": region["name"],
                "observed_at": observed_at,
                "report_date": clean_text(row.get("date")),
                "latitude": lat,
                "longitude": lon,
                "detections": detections,
                "ais_matched": ais_matched,
                "match_status": "ais_matched" if ais_matched else "ais_unmatched",
                "gfw_vessel_id": vessel_id or None,
                "imo": imo if len(imo) == 7 else None,
                "mmsi": mmsi if len(mmsi) == 9 else None,
                "callsign": clean_text(row.get("callsign")) or None,
                "name": clean_text(row.get("shipName") or row.get("ship_name")) or None,
                "flag": clean_text(row.get("flag")) or None,
                "vessel_type": clean_text(row.get("vesselType") or row.get("vessel_type")) or None,
                "watchlist_match": watch_match,
                "watchlist_match_basis": sorted(set(match_basis)),
                "watch_priority": clean_text((watch_row or {}).get("watch_priority")) or None,
                "watchlist_name": clean_text((watch_row or {}).get("name")) or None,
                "categories": watchlist_categories(watch_row),
                "assessment_limit": ASSESSMENT_LIMIT,
            }
        )
    return records, stats


# Backwards-compatible alias used by the v0.1 tests and downstream code.
build_records = build_sar_records


def build_ais_presence_records(rows: Iterable[dict[str, Any]], *, region: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    stats = {"rows_seen": 0, "rows_invalid": 0, "duplicates": 0}
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        stats["rows_seen"] += 1
        lat = parse_float(row.get("lat"))
        lon = parse_float(row.get("lon"))
        observed_at = parse_observed_at(row)
        vessel_id = clean_text(row.get("vesselId") or row.get("vessel_id"))
        if not vessel_id or lat is None or lon is None or not observed_at or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            stats["rows_invalid"] += 1
            continue
        key = (region["id"], vessel_id, observed_at, round(lat, 5), round(lon, 5))
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        imo = digits(row.get("imo"))
        mmsi = digits(row.get("mmsi"))
        identity = "|".join(str(part) for part in key)
        records.append(
            {
                "id": "aisctx_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:18],
                "feature_role": "historical_ais_context",
                "provider": "global_fishing_watch",
                "source_dataset": clean_text(row.get("_source_dataset")) or AIS_PRESENCE_DATASET,
                "coverage_mode": "historical_ais_presence",
                "historical": True,
                "not_current_position": True,
                "position_is_exact": False,
                "location_representation": "0.01_degree_grid_cell_center",
                "region_id": region["id"],
                "region_name": region["name"],
                "observed_at": observed_at,
                "report_date": clean_text(row.get("date")),
                "latitude": lat,
                "longitude": lon,
                "presence_hours": parse_float(row.get("hours")),
                "gfw_vessel_id": vessel_id,
                "imo": imo if len(imo) == 7 else None,
                "mmsi": mmsi if len(mmsi) == 9 else None,
                "callsign": clean_text(row.get("callsign")) or None,
                "name": clean_text(row.get("shipName") or row.get("ship_name")) or None,
                "flag": clean_text(row.get("flag")) or None,
                "vessel_type": clean_text(row.get("vesselType") or row.get("vessel_type")) or None,
                "assessment_limit": ASSESSMENT_LIMIT,
            }
        )
    return records, stats


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def sar_sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
    return (1 if record.get("watchlist_match") else 0, 1 if not record.get("ais_matched") else 0, str(record.get("observed_at") or ""))


def select_context_vessel_ids(sar_records: list[dict[str, Any]], limit: int) -> tuple[list[str], int]:
    candidates = [r for r in sar_records if r.get("ais_matched") and r.get("gfw_vessel_id")]
    candidates.sort(key=lambda r: str(r.get("observed_at") or ""), reverse=True)
    watch_ids: list[str] = []
    other_ids: list[str] = []
    seen: set[str] = set()
    for record in candidates:
        vessel_id = str(record["gfw_vessel_id"])
        if vessel_id in seen:
            continue
        seen.add(vessel_id)
        (watch_ids if record.get("watchlist_match") else other_ids).append(vessel_id)
    # Voodoo watchlist identities are never dropped by the general context cap.
    remaining = max(0, limit - len(watch_ids))
    selected = watch_ids + other_ids[:remaining]
    return selected, len(watch_ids) + len(other_ids)


def correlate_records(
    sar_records: list[dict[str, Any]],
    ais_records: list[dict[str, Any]],
    selected_vessel_ids: set[str],
    max_delta_minutes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    by_region_vessel: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_vessel: dict[str, list[dict[str, Any]]] = {}
    for ais in ais_records:
        vessel_id = str(ais.get("gfw_vessel_id") or "")
        if not vessel_id:
            continue
        by_region_vessel.setdefault((str(ais.get("region_id") or ""), vessel_id), []).append(ais)
        by_vessel.setdefault(vessel_id, []).append(ais)

    context_features: list[dict[str, Any]] = []
    canonical_correlations: list[dict[str, Any]] = []
    stats = {
        "sar_records": len(sar_records),
        "ais_unmatched_sar": 0,
        "matched_identity_not_selected": 0,
        "matched_identity_selected": 0,
        "time_aligned_correlations": 0,
        "selected_without_time_aligned_context": 0,
    }

    for sar in sar_records:
        sar_props = dict(sar)
        sar_lat = float(sar["latitude"])
        sar_lon = float(sar["longitude"])
        sar_time = parse_datetime(sar.get("observed_at"))
        vessel_id = str(sar.get("gfw_vessel_id") or "")
        correlation: dict[str, Any] | None = None

        if not sar.get("ais_matched"):
            stats["ais_unmatched_sar"] += 1
            sar_props["correlation_status"] = "not_applicable_ais_unmatched"
            sar_props["historical_ais_context_requested"] = False
        elif not vessel_id or vessel_id not in selected_vessel_ids:
            stats["matched_identity_not_selected"] += 1
            sar_props["correlation_status"] = "not_requested_identity_cap"
            sar_props["historical_ais_context_requested"] = False
        else:
            stats["matched_identity_selected"] += 1
            sar_props["historical_ais_context_requested"] = True
            candidates = by_region_vessel.get((str(sar.get("region_id") or ""), vessel_id)) or by_vessel.get(vessel_id, [])
            ranked: list[tuple[float, float, dict[str, Any]]] = []
            for ais in candidates:
                ais_time = parse_datetime(ais.get("observed_at"))
                if not sar_time or not ais_time:
                    continue
                delta = abs((ais_time - sar_time).total_seconds()) / 60.0
                distance = haversine_nm(sar_lat, sar_lon, float(ais["latitude"]), float(ais["longitude"]))
                ranked.append((delta, distance, ais))
            ranked.sort(key=lambda item: (item[0], item[1]))
            if ranked and ranked[0][0] <= max_delta_minutes:
                delta, distance, ais = ranked[0]
                correlation_id = "sarcorr_" + hashlib.sha1(f"{sar['id']}|{ais['id']}".encode("utf-8")).hexdigest()[:18]
                correlation = {
                    "id": correlation_id,
                    "sar_id": sar["id"],
                    "ais_context_id": ais["id"],
                    "gfw_vessel_id": vessel_id,
                    "sar_observed_at": sar["observed_at"],
                    "ais_observed_at": ais["observed_at"],
                    "time_delta_minutes": round(delta, 1),
                    "distance_nm": round(distance, 2),
                    "correlation_status": "time_aligned_same_identity",
                    "time_alignment_limit_minutes": max_delta_minutes,
                    "region_id": sar.get("region_id"),
                    "watchlist_match": bool(sar.get("watchlist_match")),
                }
                canonical_correlations.append(correlation)
                stats["time_aligned_correlations"] += 1
                sar_props.update(
                    {
                        "correlation_status": correlation["correlation_status"],
                        "ais_context_id": ais["id"],
                        "ais_context_observed_at": ais["observed_at"],
                        "time_delta_minutes": correlation["time_delta_minutes"],
                        "distance_nm": correlation["distance_nm"],
                    }
                )
                ais_props = dict(ais)
                ais_props.update(
                    {
                        "sar_id": sar["id"],
                        "correlation_id": correlation_id,
                        "correlation_status": correlation["correlation_status"],
                        "sar_observed_at": sar["observed_at"],
                        "time_delta_minutes": correlation["time_delta_minutes"],
                        "distance_nm": correlation["distance_nm"],
                        "watchlist_match": bool(sar.get("watchlist_match")),
                        "categories": sar.get("categories") or [],
                    }
                )
                map_ais_id = "aismap_" + hashlib.sha1(f"{sar['id']}|{ais['id']}".encode("utf-8")).hexdigest()[:18]
                ais_props["source_ais_context_id"] = ais["id"]
                context_features.append(
                    {
                        "type": "Feature",
                        "id": map_ais_id,
                        "geometry": {"type": "Point", "coordinates": [ais["longitude"], ais["latitude"]]},
                        "properties": {k: v for k, v in ais_props.items() if k not in {"latitude", "longitude"}},
                    }
                )
                line_props = {
                    "id": correlation_id,
                    "feature_role": "sar_ais_connector",
                    **correlation,
                    "imo": sar.get("imo") or ais.get("imo"),
                    "mmsi": sar.get("mmsi") or ais.get("mmsi"),
                    "name": sar.get("name") or ais.get("name"),
                    "watchlist_match": bool(sar.get("watchlist_match")),
                    "categories": sar.get("categories") or [],
                    "historical": True,
                    "not_current_position": True,
                    "assessment_limit": ASSESSMENT_LIMIT,
                }
                context_features.append(
                    {
                        "type": "Feature",
                        "id": correlation_id,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[sar_lon, sar_lat], [ais["longitude"], ais["latitude"]]],
                        },
                        "properties": line_props,
                    }
                )
            else:
                stats["selected_without_time_aligned_context"] += 1
                sar_props["correlation_status"] = "requested_but_no_time_aligned_context"
                sar_props["time_alignment_limit_minutes"] = max_delta_minutes

        context_features.append(
            {
                "type": "Feature",
                "id": sar["id"],
                "geometry": {"type": "Point", "coordinates": [sar_lon, sar_lat]},
                "properties": {k: v for k, v in sar_props.items() if k not in {"latitude", "longitude"}},
            }
        )
    return context_features, canonical_correlations, stats


def as_sar_geojson(records: list[dict[str, Any]], generated_at: str, summary: dict[str, Any]) -> dict[str, Any]:
    features = [
        {
            "type": "Feature",
            "id": record["id"],
            "geometry": {"type": "Point", "coordinates": [record["longitude"], record["latitude"]]},
            "properties": {key: value for key, value in record.items() if key not in {"latitude", "longitude"}},
        }
        for record in records
    ]
    return {
        "type": "FeatureCollection",
        "name": "Voodoo Whiskers delayed GFW SAR vessel-detection cells",
        "generated_at": generated_at,
        "source": "Global Fishing Watch 4Wings API / Sentinel-1 SAR vessel detections",
        "attribution": ATTRIBUTION,
        "attribution_url": ATTRIBUTION_URL,
        "assessment_limit": ASSESSMENT_LIMIT,
        "summary": summary,
        "features": features,
    }


# Backwards-compatible alias.
as_geojson = as_sar_geojson


def main() -> int:
    token = clean_text(os.environ.get("GFW_TOKEN"))
    if not token:
        raise SystemExit("GFW_TOKEN is required")

    region_path = ROOT / os.environ.get("SAR_GFW_REGION_FILE", "data/sar_regions.geojson")
    watchlist_path = ROOT / os.environ.get("SAR_GFW_WATCHLIST_FILE", "data/watchlist_master.csv")
    lookback_days = max(1, min(60, int(os.environ.get("SAR_GFW_LOOKBACK_DAYS", "14"))))
    lag_days = max(5, int(os.environ.get("SAR_GFW_DATA_LAG_DAYS", "5")))
    max_features = max(100, int(os.environ.get("SAR_GFW_MAX_FEATURES", "12000")))
    max_context_vessels = max(1, int(os.environ.get("SAR_GFW_AIS_CONTEXT_MAX_VESSELS", "150")))
    context_batch_size = max(1, min(60, int(os.environ.get("SAR_GFW_AIS_CONTEXT_BATCH_SIZE", "50"))))
    max_delta_minutes = max(1, int(os.environ.get("SAR_GFW_AIS_CONTEXT_MAX_DELTA_MINUTES", "60")))
    request_timeout = max(30, int(os.environ.get("SAR_GFW_REQUEST_TIMEOUT_SECONDS", "120")))
    poll_timeout = max(120, int(os.environ.get("SAR_GFW_POLL_TIMEOUT_SECONDS", "900")))

    generated_dt = utc_now()
    end_date = generated_dt.date() - timedelta(days=lag_days)
    start_date = end_date - timedelta(days=lookback_days - 1)
    regions = load_regions(region_path)
    region_by_id = {region["id"]: region for region in regions}
    watch_imo, watch_mmsi = load_watchlist(watchlist_path)
    client = GFWClient(token=token, request_timeout=request_timeout, poll_timeout=poll_timeout)

    all_sar: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    query_results: list[dict[str, Any]] = []
    aggregate_stats = {"rows_seen": 0, "rows_invalid": 0, "duplicates": 0}
    resolved_sar_datasets: set[str] = set()

    for region in regions:
        for matched in (False, True):
            query_label = f"sar:{region['id']}:{'matched' if matched else 'unmatched'}"
            try:
                payload = client.report(
                    region["geometry"],
                    start_date,
                    end_date,
                    dataset=SAR_DATASET,
                    filters=[f"matched='{str(matched).lower()}'"],
                    group_by="VESSEL_ID" if matched else None,
                )
                resolved_dataset, rows = flatten_report(payload)
                if resolved_dataset:
                    resolved_sar_datasets.add(resolved_dataset)
                records, stats = build_sar_records(
                    rows,
                    region=region,
                    ais_matched=matched,
                    watch_imo=watch_imo,
                    watch_mmsi=watch_mmsi,
                )
                all_sar.extend(records)
                for key in aggregate_stats:
                    aggregate_stats[key] += stats[key]
                query_results.append(
                    {"query": query_label, "status": "ok", "source_dataset": resolved_dataset, "report_rows": len(rows), "valid_records": len(records)}
                )
            except Exception as exc:
                errors.append({"query": query_label, "error": f"{type(exc).__name__}: {exc}"})
                query_results.append({"query": query_label, "status": "error", "error": str(exc)})

    sar_successful = sum(1 for item in query_results if item.get("query", "").startswith("sar:") and item.get("status") == "ok")
    if sar_successful == 0:
        for item in errors:
            print(f"ERROR {item['query']}: {item['error']}", file=sys.stderr)
        raise SystemExit("all GFW SAR report queries failed; previous products left untouched")

    unique_sar: dict[str, dict[str, Any]] = {}
    for record in all_sar:
        unique_sar.setdefault(record["id"], record)
    sar_records = sorted(unique_sar.values(), key=sar_sort_key, reverse=True)
    before_cap = len(sar_records)
    sar_records = sar_records[:max_features]
    feature_cap_applied = before_cap > len(sar_records)

    selected_ids_list, matched_identity_total = select_context_vessel_ids(sar_records, max_context_vessels)
    selected_ids = set(selected_ids_list)
    selected_by_region: dict[str, list[str]] = {}
    for region_id in region_by_id:
        ids = {
            str(record.get("gfw_vessel_id"))
            for record in sar_records
            if record.get("region_id") == region_id and record.get("gfw_vessel_id") in selected_ids
        }
        selected_by_region[region_id] = sorted(ids)

    ais_records: list[dict[str, Any]] = []
    resolved_ais_datasets: set[str] = set()
    ais_stats = {"rows_seen": 0, "rows_invalid": 0, "duplicates": 0}
    ais_query_count = 0
    ais_query_success = 0
    for region_id, ids in selected_by_region.items():
        region = region_by_id[region_id]
        for batch_index, vessel_batch in enumerate(batches(ids, context_batch_size), start=1):
            ais_query_count += 1
            query_label = f"ais_context:{region_id}:batch_{batch_index}"
            filter_value = "vessel_id in (" + ",".join(quote_filter(value) for value in vessel_batch) + ")"
            try:
                payload = client.report(
                    region["geometry"],
                    start_date,
                    end_date,
                    dataset=AIS_PRESENCE_DATASET,
                    filters=[filter_value],
                    group_by="VESSEL_ID",
                )
                resolved_dataset, rows = flatten_report(payload)
                if resolved_dataset:
                    resolved_ais_datasets.add(resolved_dataset)
                records, stats = build_ais_presence_records(rows, region=region)
                ais_records.extend(records)
                for key in ais_stats:
                    ais_stats[key] += stats[key]
                ais_query_success += 1
                query_results.append(
                    {"query": query_label, "status": "ok", "source_dataset": resolved_dataset, "requested_vessel_ids": len(vessel_batch), "report_rows": len(rows), "valid_records": len(records)}
                )
            except Exception as exc:
                errors.append({"query": query_label, "error": f"{type(exc).__name__}: {exc}"})
                query_results.append({"query": query_label, "status": "error", "requested_vessel_ids": len(vessel_batch), "error": str(exc)})

    unique_ais: dict[str, dict[str, Any]] = {}
    for record in ais_records:
        unique_ais.setdefault(record["id"], record)
    ais_records = list(unique_ais.values())

    context_features, correlations, correlation_stats = correlate_records(
        sar_records,
        ais_records,
        selected_ids,
        max_delta_minutes,
    )

    summary = {
        "queries_total": len(query_results),
        "sar_queries_successful": sar_successful,
        "sar_queries_failed": (len(regions) * 2) - sar_successful,
        "ais_context_queries_total": ais_query_count,
        "ais_context_queries_successful": ais_query_success,
        "ais_context_queries_failed": ais_query_count - ais_query_success,
        "records_total": len(sar_records),
        "records_before_cap": before_cap,
        "feature_cap": max_features,
        "feature_cap_applied": feature_cap_applied,
        "ais_unmatched": sum(1 for record in sar_records if not record["ais_matched"]),
        "ais_matched": sum(1 for record in sar_records if record["ais_matched"]),
        "watchlist_matches": sum(1 for record in sar_records if record["watchlist_match"]),
        "matched_vessel_identities_total": matched_identity_total,
        "matched_vessel_identities_selected_for_context": len(selected_ids),
        "matched_vessel_identity_cap": max_context_vessels,
        "matched_vessel_identity_cap_applied": matched_identity_total > len(selected_ids),
        "historical_ais_presence_records": len(ais_records),
        "time_aligned_correlations": len(correlations),
        **aggregate_stats,
        "ais_presence_rows_seen": ais_stats["rows_seen"],
        "ais_presence_rows_invalid": ais_stats["rows_invalid"],
        "ais_presence_duplicates": ais_stats["duplicates"],
        **correlation_stats,
    }
    generated_at = iso_z(generated_dt)
    degraded = (
        sar_successful < 4
        or feature_cap_applied
        or (ais_query_count > 0 and ais_query_success < ais_query_count)
        or (matched_identity_total > len(selected_ids))
        or (matched_identity_total > 0 and len(correlations) == 0)
    )
    health = "degraded" if degraded else "ok"
    status = {
        "schema_version": "2.0.0",
        "generated_at": generated_at,
        "status": health,
        "provider": "global_fishing_watch",
        "datasets_requested": {"sar": SAR_DATASET, "historical_ais_presence": AIS_PRESENCE_DATASET},
        "datasets_resolved": {"sar": sorted(resolved_sar_datasets), "historical_ais_presence": sorted(resolved_ais_datasets)},
        "date_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "configured_lag_days": lag_days,
        "coverage_mode": "time_aligned_historical_sar_ais_context",
        "spatial_resolution": "HIGH / 0.01-degree report grid",
        "temporal_resolution": "HOURLY",
        "time_alignment_limit_minutes": max_delta_minutes,
        "current_ais_must_not_be_overlaid": True,
        "regions": [{key: region[key] for key in ("id", "name", "note")} for region in regions],
        "summary": summary,
        "queries": query_results,
        "errors": errors,
        "attribution": ATTRIBUTION,
        "attribution_url": ATTRIBUTION_URL,
        "assessment_limit": ASSESSMENT_LIMIT,
    }
    canonical = {
        "schema_version": "2.0.0",
        "generated_at": generated_at,
        "source": "Global Fishing Watch 4Wings API",
        "datasets_requested": status["datasets_requested"],
        "date_range": status["date_range"],
        "coverage_mode": status["coverage_mode"],
        "historical": True,
        "attribution": ATTRIBUTION,
        "attribution_url": ATTRIBUTION_URL,
        "assessment_limit": ASSESSMENT_LIMIT,
        "summary": summary,
        "sar_records": sar_records,
        "historical_ais_presence_records": ais_records,
        "correlations": correlations,
    }
    sar_geojson = as_sar_geojson(sar_records, generated_at, summary)
    context_geojson = {
        "type": "FeatureCollection",
        "name": "Voodoo Whiskers time-aligned historical SAR and AIS context",
        "generated_at": generated_at,
        "source": "Global Fishing Watch 4Wings API / SAR vessel detections and AIS vessel presence",
        "coverage_mode": "time_aligned_historical_sar_ais_context",
        "current_ais_must_not_be_overlaid": True,
        "time_alignment_limit_minutes": max_delta_minutes,
        "date_range": status["date_range"],
        "attribution": ATTRIBUTION,
        "attribution_url": ATTRIBUTION_URL,
        "assessment_limit": ASSESSMENT_LIMIT,
        "summary": summary,
        "features": context_features,
    }

    outputs = {
        ROOT / "data/sar_gfw_latest.json": canonical,
        ROOT / "data/sar_gfw_status_latest.json": status,
        ROOT / "data/sar_gfw_ais_context_latest.json": context_geojson,
        ROOT / "public/data/vessels/sar_detections_latest.geojson": sar_geojson,
        ROOT / "public/data/vessels/sar_ais_context_latest.geojson": context_geojson,
        ROOT / "public/data/vessels/sar_import_status.json": status,
        ROOT / "public/downloads/sar_detections_latest.geojson": sar_geojson,
        ROOT / "public/downloads/sar_ais_context_latest.geojson": context_geojson,
        ROOT / "public/downloads/sar_import_status.json": status,
    }
    for path, payload in outputs.items():
        atomic_json(path, payload, compact=path.name.endswith("latest.geojson"))
        if path.stat().st_size > 25 * 1024 * 1024:
            raise SystemExit(f"SAR/AIS output exceeds 25 MiB guard: {path}")

    print(
        json.dumps(
            {
                "status": health,
                "date_range": status["date_range"],
                "sar_records": len(sar_records),
                "historical_ais_records": len(ais_records),
                "correlations": len(correlations),
                "selected_matched_vessels": len(selected_ids),
                "matched_vessels_total": matched_identity_total,
                "queries_successful": sum(1 for item in query_results if item.get("status") == "ok"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
