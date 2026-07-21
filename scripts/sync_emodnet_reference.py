#!/usr/bin/env python3
"""Synchronise selected EMODnet Human Activities WFS layers into public GeoJSON.

The public output is deliberately bounded for a public Git repository:
- server-side bbox request,
- exact client-side clipping to the configured maritime area,
- topology-preserving simplification,
- coordinate rounding,
- conservative property pruning,
- compact JSON serialisation,
- hard per-file and total-size guards before any commit can occur.

Last-known-good files are preserved when a remote request fails, no matching feature
class is found, or a processed layer still exceeds the configured hard limit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import GeometryCollection, MultiLineString, MultiPoint, MultiPolygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "emodnet_layers.json"
OUT_DIR = ROOT / "public" / "data" / "reference" / "emodnet"
STATUS_PATH = OUT_DIR / "sync_status.json"
MANIFEST_PATH = OUT_DIR / "manifest.json"

DEFAULT_PROPERTY_HINTS = (
    "name", "title", "label", "status", "state", "type", "category", "class",
    "operator", "owner", "country", "nation", "code", "identifier", "id",
    "source", "provider", "url", "link", "year", "date", "capacity", "voltage",
    "product", "diameter", "length", "route", "project", "site", "location",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_bytes(payload: Any, *, compact: bool) -> bytes:
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    return text.encode("utf-8")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    Path(temp_name).replace(path)


def atomic_write_json(path: Path, payload: Any, *, compact: bool = False) -> int:
    encoded = json_bytes(payload, compact=compact)
    atomic_write_bytes(path, encoded)
    return len(encoded)


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": os.getenv("EMODNET_USER_AGENT", "MOwlSINT Voodoo Whiskers/1.1"),
        "Accept": "application/json, application/xml, text/xml;q=0.9, */*;q=0.1",
    })
    return session


def norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def parse_feature_types(xml_bytes: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    found: list[dict[str, str]] = []
    for node in root.iter():
        if node.tag.split("}")[-1] != "FeatureType":
            continue
        values: dict[str, str] = {}
        for child in list(node):
            key = child.tag.split("}")[-1]
            if key in {"Name", "Title", "Abstract"}:
                values[key.lower()] = "".join(child.itertext()).strip()
        if values.get("name"):
            found.append({
                "name": values.get("name", ""),
                "title": values.get("title", ""),
                "abstract": values.get("abstract", ""),
            })
    return found


def score_feature_type(feature_type: dict[str, str], rule: dict[str, Any]) -> float:
    text = norm(" ".join([feature_type.get("name", ""), feature_type.get("title", ""), feature_type.get("abstract", "")]))
    exact_override = [norm(x) for x in rule.get("exact_type_names", []) if norm(x)]
    if exact_override and norm(feature_type.get("name")) in exact_override:
        return 1000.0

    exclude = [norm(x) for x in rule.get("title_exclude", []) if norm(x)]
    if any(term in text for term in exclude):
        return -100.0

    include_all = [norm(x) for x in rule.get("title_include_all", []) if norm(x)]
    if any(term not in text for term in include_all):
        return -50.0

    include_any = [norm(x) for x in rule.get("title_include_any", []) if norm(x)]
    hits = sum(1 for term in include_any if term in text)
    if include_any and hits == 0:
        return -10.0

    title = norm(feature_type.get("title"))
    name = norm(feature_type.get("name"))
    return hits * 10 + len(include_all) * 5 + (2 if any(term in title for term in include_any) else 0) + (1 if any(term in name for term in include_any) else 0)


def choose_feature_types(feature_types: list[dict[str, str]], rule: dict[str, Any]) -> list[dict[str, str]]:
    ranked = [(score_feature_type(ft, rule), ft) for ft in feature_types]
    ranked = [(score, ft) for score, ft in ranked if score > 0]
    ranked.sort(key=lambda row: (-row[0], row[1].get("name", "")))
    if not ranked:
        return []
    best_score = ranked[0][0]
    max_types = max(1, int(rule.get("max_feature_types", 3)))
    return [ft for score, ft in ranked if score >= max(1.0, best_score - 4.0)][:max_types]


def coordinates_iter(geometry: dict[str, Any]) -> Iterable[tuple[float, float]]:
    coords = geometry.get("coordinates")
    if not isinstance(coords, list):
        return

    def walk(value: Any) -> Iterable[tuple[float, float]]:
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
            yield float(value[0]), float(value[1])
            return
        if isinstance(value, list):
            for item in value:
                yield from walk(item)

    yield from walk(coords)


def feature_intersects_bbox(feature: dict[str, Any], bbox: list[float]) -> bool:
    geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
    points = list(coordinates_iter(geometry))
    if not points:
        return False
    min_lon = min(p[0] for p in points)
    max_lon = max(p[0] for p in points)
    min_lat = min(p[1] for p in points)
    max_lat = max(p[1] for p in points)
    west, south, east, north = bbox
    return not (max_lon < west or min_lon > east or max_lat < south or min_lat > north)


def request_json(session: requests.Session, base_url: str, params: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    response = session.get(base_url, params=params, timeout=(20, timeout))
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        snippet = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"WFS returned non-JSON content: {snippet}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("WFS response is not a JSON object")
    return data


def fetch_feature_type(
    session: requests.Session,
    base_url: str,
    typename: str,
    bbox: list[float],
    bbox_crs: str,
    page_size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    features: list[dict[str, Any]] = []
    start_index = 0
    page_count = 0
    mode = "wfs2"

    while True:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": typename,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "bbox": ",".join(map(str, bbox + [bbox_crs])),
            "count": page_size,
            "startIndex": start_index,
        }
        try:
            payload = request_json(session, base_url, params)
        except Exception:
            if start_index > 0:
                raise
            mode = "wfs1"
            params = {
                "service": "WFS",
                "version": "1.1.0",
                "request": "GetFeature",
                "typeName": typename,
                "outputFormat": "application/json",
                "srsName": "EPSG:4326",
                "bbox": ",".join(map(str, bbox)),
                "maxFeatures": page_size,
            }
            payload = request_json(session, base_url, params)

        batch = payload.get("features") if isinstance(payload.get("features"), list) else []
        for feature in batch:
            if isinstance(feature, dict) and feature_intersects_bbox(feature, bbox):
                features.append(feature)
        page_count += 1

        if mode == "wfs1" or len(batch) < page_size:
            break
        number_matched = payload.get("numberMatched")
        start_index += len(batch)
        if isinstance(number_matched, int) and start_index >= number_matched:
            break
        if page_count >= 50:
            raise RuntimeError(f"pagination safety limit reached for {typename}")
        time.sleep(0.2)

    return features, {"request_mode": mode, "pages": page_count}


def load_existing_feature_count(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return len(payload.get("features", [])) if isinstance(payload.get("features"), list) else 0
    except Exception:
        return 0


def scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def safe_scalar(value: Any, max_chars: int) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        return value.strip()[:max_chars]
    return value


def property_score(key: str, hints: tuple[str, ...]) -> tuple[int, int, str]:
    lowered = norm(key)
    exact = lowered in hints
    contains = any(hint in lowered for hint in hints)
    return (2 if exact else 1 if contains else 0, len(key), key.lower())


def prune_properties(properties: dict[str, Any], rule: dict[str, Any], typename: str, title: str) -> dict[str, Any]:
    hints = tuple(norm(x) for x in rule.get("property_hints", DEFAULT_PROPERTY_HINTS) if norm(x))
    max_properties = max(4, int(rule.get("max_properties", 18)))
    max_chars = max(40, int(rule.get("max_property_chars", 240)))
    candidates: list[tuple[tuple[int, int, str], str, Any]] = []
    for key, value in properties.items():
        if str(key).startswith("_") or not scalar(value):
            continue
        score = property_score(str(key), hints)
        if score[0] <= 0:
            continue
        cleaned = safe_scalar(value, max_chars)
        if cleaned in (None, ""):
            continue
        candidates.append((score, str(key), cleaned))
    candidates.sort(key=lambda row: (-row[0][0], row[0][1], row[0][2]))
    selected = {key: value for _score, key, value in candidates[:max_properties]}
    selected["_vw_layer"] = rule["id"]
    selected["_emodnet_typename"] = typename
    selected["_emodnet_title"] = title[:max_chars]
    return selected


def geometry_components(geometry: BaseGeometry, expected: str) -> list[BaseGeometry]:
    if geometry.is_empty:
        return []
    if expected == "line":
        if geometry.geom_type == "LineString":
            return [geometry]
        if geometry.geom_type == "MultiLineString":
            return list(geometry.geoms)
    elif expected == "point":
        if geometry.geom_type == "Point":
            return [geometry]
        if geometry.geom_type == "MultiPoint":
            return list(geometry.geoms)
    elif expected == "polygon":
        if geometry.geom_type == "Polygon":
            return [geometry]
        if geometry.geom_type == "MultiPolygon":
            return list(geometry.geoms)
    elif expected == "mixed":
        if geometry.geom_type != "GeometryCollection":
            return [geometry]
    if isinstance(geometry, GeometryCollection) or hasattr(geometry, "geoms"):
        result: list[BaseGeometry] = []
        for part in geometry.geoms:
            result.extend(geometry_components(part, expected))
        return result
    return []


def rebuild_geometry(parts: list[BaseGeometry], expected: str) -> BaseGeometry | None:
    parts = [part for part in parts if not part.is_empty]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    if expected == "line":
        lines = [part for part in parts if part.geom_type == "LineString"]
        return MultiLineString(lines) if lines else None
    if expected == "point":
        points = [part for part in parts if part.geom_type == "Point"]
        return MultiPoint(points) if points else None
    if expected == "polygon":
        polygons = [part for part in parts if part.geom_type == "Polygon"]
        return MultiPolygon(polygons) if polygons else None
    return unary_union(parts)


def round_coordinates(value: Any, precision: int) -> Any:
    if isinstance(value, tuple):
        return [round_coordinates(item, precision) for item in value]
    if isinstance(value, list):
        return [round_coordinates(item, precision) for item in value]
    if isinstance(value, float):
        rounded = round(value, precision)
        return 0.0 if rounded == -0.0 else rounded
    return value


def process_geometry(raw_geometry: dict[str, Any], clip_polygon: BaseGeometry, rule: dict[str, Any], tolerance: float, precision: int) -> dict[str, Any] | None:
    try:
        geom = shape(raw_geometry)
        if geom.is_empty:
            return None
        if not geom.is_valid:
            geom = make_valid(geom)
        geom = geom.intersection(clip_polygon)
        expected = str(rule.get("geometry", "mixed"))
        parts = geometry_components(geom, expected)
        geom = rebuild_geometry(parts, expected)
        if geom is None or geom.is_empty:
            return None
        if tolerance > 0 and geom.geom_type not in {"Point", "MultiPoint"}:
            geom = geom.simplify(tolerance, preserve_topology=True)
            parts = geometry_components(geom, expected)
            geom = rebuild_geometry(parts, expected)
            if geom is None or geom.is_empty:
                return None
        mapped = mapping(geom)
        if "coordinates" in mapped:
            mapped["coordinates"] = round_coordinates(mapped["coordinates"], precision)
        return mapped
    except Exception:
        return None


def feature_identity(feature: dict[str, Any]) -> str:
    if feature.get("id") not in (None, ""):
        return str(feature["id"])
    digest = hashlib.sha1(json_bytes([feature.get("geometry"), feature.get("properties")], compact=True)).hexdigest()
    return digest


def build_processed_features(raw_rows: list[tuple[dict[str, Any], str, str]], config: dict[str, Any], rule: dict[str, Any], tolerance: float) -> tuple[list[dict[str, Any]], dict[str, int]]:
    clip_polygon = box(*config["bbox"])
    precision = int(rule.get("coordinate_precision", config.get("coordinate_precision", 5)))
    processed: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped_geometry = 0
    duplicates = 0
    for raw_feature, typename, title in raw_rows:
        raw_geometry = raw_feature.get("geometry") if isinstance(raw_feature.get("geometry"), dict) else None
        if not raw_geometry:
            dropped_geometry += 1
            continue
        geometry = process_geometry(raw_geometry, clip_polygon, rule, tolerance, precision)
        if geometry is None:
            dropped_geometry += 1
            continue
        properties = raw_feature.get("properties") if isinstance(raw_feature.get("properties"), dict) else {}
        feature: dict[str, Any] = {
            "type": "Feature",
            "geometry": geometry,
            "properties": prune_properties(properties, rule, typename, title),
        }
        if raw_feature.get("id") not in (None, ""):
            feature["id"] = str(raw_feature["id"])
        identity = feature_identity(feature)
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        processed.append(feature)
    return processed, {"dropped_geometry": dropped_geometry, "duplicates": duplicates}


def build_layer_payload(config: dict[str, Any], rule: dict[str, Any], raw_rows: list[tuple[dict[str, Any], str, str]], fetch_meta: list[dict[str, Any]], errors: list[str], generated_at: str, tolerance: float) -> tuple[dict[str, Any], dict[str, int]]:
    features, process_stats = build_processed_features(raw_rows, config, rule, tolerance)
    payload = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "schema_version": "1.1.0",
            "generated_at": generated_at,
            "layer_id": rule["id"],
            "source": "EMODnet Human Activities",
            "service_url": config["service_url"],
            "attribution": config.get("attribution"),
            "license_note": config.get("license_note"),
            "bbox": config["bbox"],
            "clipped_to_bbox": True,
            "simplify_tolerance_degrees": tolerance,
            "coordinate_precision": int(rule.get("coordinate_precision", config.get("coordinate_precision", 5))),
            "geometry_accuracy_note": "Public display/analyst-lead geometry is clipped and topology-preserving simplified. It is not a navigational or engineering survey product.",
            "property_policy": "Selected scalar identification/context fields only",
            "raw_feature_count": len(raw_rows),
            "published_feature_count": len(features),
            "dropped_geometry_count": process_stats["dropped_geometry"],
            "duplicate_count": process_stats["duplicates"],
            "feature_types": fetch_meta,
            "style": rule.get("style", {}),
            "errors": errors,
        },
    }
    return payload, process_stats


def sync_layer(session: requests.Session, config: dict[str, Any], feature_types: list[dict[str, str]], rule: dict[str, Any]) -> dict[str, Any]:
    layer_id = rule["id"]
    out_path = OUT_DIR / rule["filename"]
    selected = choose_feature_types(feature_types, rule)
    status: dict[str, Any] = {
        "id": layer_id,
        "filename": rule["filename"],
        "selected_feature_types": selected,
        "ok": False,
        "preserved_last_known_good": False,
    }
    if not selected:
        status["error"] = "No matching WFS feature type discovered"
        status["existing_feature_count"] = load_existing_feature_count(out_path)
        status["preserved_last_known_good"] = out_path.exists()
        return status

    raw_rows: list[tuple[dict[str, Any], str, str]] = []
    fetch_meta: list[dict[str, Any]] = []
    errors: list[str] = []
    for ft in selected:
        try:
            batch, meta = fetch_feature_type(
                session,
                config["service_url"],
                ft["name"],
                config["bbox"],
                config.get("bbox_crs", "urn:ogc:def:crs:OGC::CRS84"),
                int(config.get("page_size", 5000)),
            )
            fetch_meta.append({"type_name": ft["name"], "title": ft.get("title", ""), "feature_count": len(batch), **meta})
            raw_rows.extend((feature, ft["name"], ft.get("title", "")) for feature in batch)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ft['name']}: {type(exc).__name__}: {exc}")

    if not raw_rows and errors:
        status["error"] = "; ".join(errors)
        status["existing_feature_count"] = load_existing_feature_count(out_path)
        status["preserved_last_known_good"] = out_path.exists()
        return status

    generated_at = utc_now_iso()
    target_bytes = int(rule.get("target_max_bytes", config.get("target_max_layer_bytes", 18 * 1024 * 1024)))
    hard_bytes = int(rule.get("hard_max_bytes", config.get("hard_max_layer_bytes", 45 * 1024 * 1024)))
    tolerance = float(rule.get("simplify_tolerance_degrees", config.get("simplify_tolerance_degrees", 0.0005)))
    max_tolerance = float(rule.get("max_simplify_tolerance_degrees", config.get("max_simplify_tolerance_degrees", 0.003)))
    growth = max(1.2, float(config.get("simplify_growth_factor", 1.8)))
    attempts: list[dict[str, Any]] = []
    final_payload: dict[str, Any] | None = None
    final_bytes: bytes | None = None

    while True:
        payload, _stats = build_layer_payload(config, rule, raw_rows, fetch_meta, errors, generated_at, tolerance)
        encoded = json_bytes(payload, compact=True)
        attempts.append({
            "simplify_tolerance_degrees": tolerance,
            "feature_count": len(payload["features"]),
            "output_bytes": len(encoded),
        })
        final_payload, final_bytes = payload, encoded
        if len(encoded) <= target_bytes or tolerance >= max_tolerance:
            break
        tolerance = min(max_tolerance, tolerance * growth)

    assert final_payload is not None and final_bytes is not None
    if len(final_bytes) > hard_bytes:
        status.update({
            "error": f"Processed layer remains too large: {len(final_bytes)} bytes > hard limit {hard_bytes}",
            "raw_feature_count": len(raw_rows),
            "processed_feature_count": len(final_payload.get("features", [])),
            "size_attempts": attempts,
            "existing_feature_count": load_existing_feature_count(out_path),
            "preserved_last_known_good": out_path.exists(),
        })
        return status

    final_payload["metadata"]["target_max_bytes"] = target_bytes
    final_payload["metadata"]["hard_max_bytes"] = hard_bytes
    final_payload["metadata"]["size_attempts"] = attempts
    # Re-encode twice so the embedded byte count reflects the actual compact blob.
    final_payload["metadata"]["output_bytes"] = len(final_bytes)
    final_bytes = json_bytes(final_payload, compact=True)
    final_payload["metadata"]["output_bytes"] = len(final_bytes)
    final_bytes = json_bytes(final_payload, compact=True)
    if len(final_bytes) > hard_bytes:
        status.update({
            "error": f"Processed layer exceeds hard limit after metadata: {len(final_bytes)} bytes > {hard_bytes}",
            "size_attempts": attempts,
            "preserved_last_known_good": out_path.exists(),
        })
        return status

    atomic_write_bytes(out_path, final_bytes)
    status.update({
        "ok": True,
        "generated_at": generated_at,
        "raw_feature_count": len(raw_rows),
        "feature_count": len(final_payload.get("features", [])),
        "output_bytes": len(final_bytes),
        "simplify_tolerance_degrees": final_payload["metadata"]["simplify_tolerance_degrees"],
        "fetch": fetch_meta,
        "warnings": errors,
        "size_attempts": attempts,
    })
    return status


def validate_public_reference_sizes(config: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, int]:
    hard_layer = int(config.get("hard_max_layer_bytes", 45 * 1024 * 1024))
    hard_total = int(config.get("hard_max_total_reference_bytes", 85 * 1024 * 1024))
    total = 0
    largest = 0
    for row in results:
        filename = row.get("filename")
        if not filename:
            continue
        path = OUT_DIR / str(filename)
        if not path.exists():
            continue
        size = path.stat().st_size
        total += size
        largest = max(largest, size)
        if size > hard_layer:
            raise RuntimeError(f"Reference file exceeds hard limit before commit: {path} = {size} bytes")
    if total > hard_total:
        raise RuntimeError(f"Combined EMODnet reference output exceeds hard total limit: {total} > {hard_total} bytes")
    return {"total_reference_bytes": total, "largest_reference_file_bytes": largest}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--strict", action="store_true", help="Fail when every configured layer fails")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = build_session()
    generated_at = utc_now_iso()
    response = session.get(
        config["service_url"],
        params={"SERVICE": "WFS", "REQUEST": "GetCapabilities", "VERSION": config.get("service_version", "2.0.0")},
        timeout=(20, 180),
    )
    response.raise_for_status()
    feature_types = parse_feature_types(response.content)
    if not feature_types:
        raise RuntimeError("EMODnet WFS GetCapabilities returned no feature types")

    results = [sync_layer(session, config, feature_types, rule) for rule in config.get("layers", [])]
    ok_count = sum(1 for row in results if row.get("ok"))
    size_summary = validate_public_reference_sizes(config, results)
    status = {
        "schema_version": "1.1.0",
        "generated_at": generated_at,
        "service_url": config["service_url"],
        "feature_type_count": len(feature_types),
        "configured_layer_count": len(results),
        "successful_layer_count": ok_count,
        **size_summary,
        "layers": results,
    }
    atomic_write_json(STATUS_PATH, status)
    manifest = {
        "schema_version": "1.1.0",
        "generated_at": generated_at,
        "source": "EMODnet Human Activities",
        "attribution": config.get("attribution"),
        "license_note": config.get("license_note"),
        "bbox": config.get("bbox"),
        **size_summary,
        "layers": [
            {
                "id": row.get("id"),
                "href": f"./{row.get('filename')}",
                "feature_count": row.get("feature_count", row.get("existing_feature_count", 0)),
                "output_bytes": row.get("output_bytes"),
                "simplify_tolerance_degrees": row.get("simplify_tolerance_degrees"),
                "ok": bool(row.get("ok")),
                "preserved_last_known_good": bool(row.get("preserved_last_known_good")),
            }
            for row in results
        ],
    }
    atomic_write_json(MANIFEST_PATH, manifest)
    print(json.dumps({
        "feature_types": len(feature_types),
        "successful_layers": ok_count,
        "configured_layers": len(results),
        **size_summary,
    }, indent=2))
    oversized = [row for row in results if "too large" in str(row.get("error", "")).lower() or "exceeds hard limit" in str(row.get("error", "")).lower()]
    if oversized:
        print(json.dumps({"error": "One or more processed EMODnet layers exceeded the pre-commit hard limit", "layers": [row.get("id") for row in oversized]}, indent=2))
        return 2
    if args.strict and ok_count == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
