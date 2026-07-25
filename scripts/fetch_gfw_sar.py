#!/usr/bin/env python3
"""Fetch delayed Global Fishing Watch SAR vessel-detection report cells.

The output is deliberately kept separate from AIS products. Coordinates returned by
4Wings reports are centres of 0.01-degree grid cells at HIGH spatial resolution;
they are not represented as exact vessel positions. An AIS-unmatched detection is a
review lead, not proof of an intentionally dark vessel.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parents[1]
API_REPORT = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
API_LAST_REPORT = "https://gateway.api.globalfishingwatch.org/v3/4wings/last-report"
DATASET = "public-global-sar-presence:latest"
ATTRIBUTION = "Powered by Global Fishing Watch."
ATTRIBUTION_URL = "https://globalfishingwatch.org"
ASSESSMENT_LIMIT = (
    "Delayed SAR detection cells support analyst review. Grid-cell centres are not exact "
    "vessel positions. An AIS-unmatched detection does not by itself establish intentional "
    "AIS disablement, identity, unlawful activity, attribution or hostile intent."
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
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def parse_observed_at(row: dict[str, Any]) -> str | None:
    for key in ("entryTimestamp", "exitTimestamp"):
        raw = clean_text(row.get(key))
        if raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return iso_z(parsed)
            except ValueError:
                pass
    raw_date = clean_text(row.get("date"))
    if not raw_date:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw_date, fmt).replace(tzinfo=timezone.utc)
            return iso_z(parsed)
        except ValueError:
            continue
    return None


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
    if row:
        categories.append("watchlist")
    return sorted(set(categories))


def flatten_report(payload: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Return resolved dataset version and flat report rows."""
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
            if str(key).startswith("public-global-sar-presence:"):
                dataset_version = str(key)
            for row in value:
                if isinstance(row, dict):
                    copied = dict(row)
                    copied["_source_dataset"] = str(key)
                    rows.append(copied)
    return dataset_version, rows


def report_is_finished(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("entries"), list)


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
                "User-Agent": "VoodooWhiskers-GFW-SAR/0.1",
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
                if payload.get("status") not in {None, "running"} and not report_is_finished(payload):
                    raise RuntimeError(f"GFW last report failed: {payload}")
            time.sleep(self.poll_interval)
        raise TimeoutError(f"GFW report did not finish within {self.poll_timeout}s; last status={last_status}")

    def report(
        self,
        geometry: dict[str, Any],
        start_date: date,
        end_date: date,
        *,
        matched: bool,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("spatial-resolution", "HIGH"),
            ("temporal-resolution", "HOURLY"),
            ("datasets[0]", DATASET),
            ("date-range", f"{start_date.isoformat()},{end_date.isoformat()}"),
            ("format", "JSON"),
            ("filters[0]", f"matched='{str(matched).lower()}'"),
        ]
        if matched:
            params.append(("group-by", "VESSEL_ID"))
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
                    # A token supports one concurrent report. Let it finish, then retry our own query.
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


def build_records(
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
        dedupe_key = (
            region["id"],
            ais_matched,
            observed_at,
            round(lat, 5),
            round(lon, 5),
            vessel_id,
            imo,
            mmsi,
        )
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
        source_dataset = clean_text(row.get("_source_dataset")) or DATASET
        identity = "|".join(str(part) for part in dedupe_key)
        record_id = "sar_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:18]

        record = {
            "id": record_id,
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
        records.append(record)
    return records, stats


def record_sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
    return (
        1 if record.get("watchlist_match") else 0,
        1 if not record.get("ais_matched") else 0,
        str(record.get("observed_at") or ""),
    )


def as_geojson(records: list[dict[str, Any]], generated_at: str, summary: dict[str, Any]) -> dict[str, Any]:
    features = []
    for record in records:
        props = {key: value for key, value in record.items() if key not in {"latitude", "longitude"}}
        features.append(
            {
                "type": "Feature",
                "id": record["id"],
                "geometry": {
                    "type": "Point",
                    "coordinates": [record["longitude"], record["latitude"]],
                },
                "properties": props,
            }
        )
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


def main() -> int:
    token = clean_text(os.environ.get("GFW_TOKEN"))
    if not token:
        raise SystemExit("GFW_TOKEN is required")

    region_path = ROOT / os.environ.get("SAR_GFW_REGION_FILE", "data/sar_regions.geojson")
    watchlist_path = ROOT / os.environ.get("SAR_GFW_WATCHLIST_FILE", "data/watchlist_master.csv")
    lookback_days = max(1, min(60, int(os.environ.get("SAR_GFW_LOOKBACK_DAYS", "14"))))
    lag_days = max(5, int(os.environ.get("SAR_GFW_DATA_LAG_DAYS", "5")))
    max_features = max(100, int(os.environ.get("SAR_GFW_MAX_FEATURES", "12000")))
    request_timeout = max(30, int(os.environ.get("SAR_GFW_REQUEST_TIMEOUT_SECONDS", "120")))
    poll_timeout = max(120, int(os.environ.get("SAR_GFW_POLL_TIMEOUT_SECONDS", "900")))

    generated_dt = utc_now()
    end_date = generated_dt.date() - timedelta(days=lag_days)
    start_date = end_date - timedelta(days=lookback_days - 1)
    regions = load_regions(region_path)
    watch_imo, watch_mmsi = load_watchlist(watchlist_path)
    client = GFWClient(token=token, request_timeout=request_timeout, poll_timeout=poll_timeout)

    all_records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    query_results: list[dict[str, Any]] = []
    aggregate_stats = {"rows_seen": 0, "rows_invalid": 0, "duplicates": 0}
    resolved_datasets: set[str] = set()

    for region in regions:
        for matched in (False, True):
            query_label = f"{region['id']}:{'matched' if matched else 'unmatched'}"
            try:
                payload = client.report(region["geometry"], start_date, end_date, matched=matched)
                resolved_dataset, rows = flatten_report(payload)
                if resolved_dataset:
                    resolved_datasets.add(resolved_dataset)
                records, stats = build_records(
                    rows,
                    region=region,
                    ais_matched=matched,
                    watch_imo=watch_imo,
                    watch_mmsi=watch_mmsi,
                )
                all_records.extend(records)
                for key in aggregate_stats:
                    aggregate_stats[key] += stats[key]
                query_results.append(
                    {
                        "query": query_label,
                        "status": "ok",
                        "source_dataset": resolved_dataset,
                        "report_rows": len(rows),
                        "valid_records": len(records),
                    }
                )
            except Exception as exc:  # keep successful region/match products available
                errors.append({"query": query_label, "error": f"{type(exc).__name__}: {exc}"})
                query_results.append({"query": query_label, "status": "error", "error": str(exc)})

    successful = sum(1 for item in query_results if item.get("status") == "ok")
    if successful == 0:
        for item in errors:
            print(f"ERROR {item['query']}: {item['error']}", file=sys.stderr)
        raise SystemExit("all GFW SAR report queries failed; previous products left untouched")

    # Deduplicate across query responses without erasing separate regional attribution.
    unique: dict[str, dict[str, Any]] = {}
    for record in all_records:
        unique.setdefault(record["id"], record)
    records = sorted(unique.values(), key=record_sort_key, reverse=True)
    before_cap = len(records)
    records = records[:max_features]
    cap_applied = before_cap > len(records)

    summary = {
        "queries_total": len(query_results),
        "queries_successful": successful,
        "queries_failed": len(query_results) - successful,
        "records_total": len(records),
        "records_before_cap": before_cap,
        "feature_cap": max_features,
        "feature_cap_applied": cap_applied,
        "ais_unmatched": sum(1 for record in records if not record["ais_matched"]),
        "ais_matched": sum(1 for record in records if record["ais_matched"]),
        "watchlist_matches": sum(1 for record in records if record["watchlist_match"]),
        **aggregate_stats,
    }
    generated_at = iso_z(generated_dt)
    health = "ok" if successful == len(query_results) and not cap_applied else "degraded"
    status = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "status": health,
        "provider": "global_fishing_watch",
        "dataset_requested": DATASET,
        "datasets_resolved": sorted(resolved_datasets),
        "date_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "configured_lag_days": lag_days,
        "coverage_mode": "satellite_sar_delayed",
        "spatial_resolution": "HIGH / 0.01-degree report grid",
        "temporal_resolution": "HOURLY",
        "regions": [{key: region[key] for key in ("id", "name", "note")} for region in regions],
        "summary": summary,
        "queries": query_results,
        "errors": errors,
        "attribution": ATTRIBUTION,
        "attribution_url": ATTRIBUTION_URL,
        "assessment_limit": ASSESSMENT_LIMIT,
    }
    canonical = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "source": "Global Fishing Watch 4Wings API",
        "dataset_requested": DATASET,
        "date_range": status["date_range"],
        "coverage_mode": "satellite_sar_delayed",
        "historical": True,
        "attribution": ATTRIBUTION,
        "attribution_url": ATTRIBUTION_URL,
        "assessment_limit": ASSESSMENT_LIMIT,
        "summary": summary,
        "records": records,
    }
    geojson = as_geojson(records, generated_at, summary)

    outputs = {
        ROOT / "data/sar_gfw_latest.json": canonical,
        ROOT / "data/sar_gfw_status_latest.json": status,
        ROOT / "public/data/vessels/sar_detections_latest.geojson": geojson,
        ROOT / "public/data/vessels/sar_import_status.json": status,
        ROOT / "public/downloads/sar_detections_latest.geojson": geojson,
        ROOT / "public/downloads/sar_import_status.json": status,
    }
    for path, payload in outputs.items():
        atomic_json(path, payload, compact=path.name.endswith("latest.geojson"))
        if path.stat().st_size > 25 * 1024 * 1024:
            raise SystemExit(f"SAR output exceeds 25 MiB guard: {path}")

    print(
        json.dumps(
            {
                "status": health,
                "date_range": status["date_range"],
                "records": len(records),
                "unmatched": summary["ais_unmatched"],
                "matched": summary["ais_matched"],
                "watchlist_matches": summary["watchlist_matches"],
                "queries_successful": successful,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
