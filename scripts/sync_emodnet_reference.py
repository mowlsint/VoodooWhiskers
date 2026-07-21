#!/usr/bin/env python3
"""Synchronise selected EMODnet Human Activities WFS layers into public GeoJSON.

The script discovers current WFS feature type names from GetCapabilities instead of
hard-coding fragile server layer names. Last-known-good files are preserved when a
remote request fails or no matching feature type is found.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "emodnet_layers.json"
OUT_DIR = ROOT / "public" / "data" / "reference" / "emodnet"
STATUS_PATH = OUT_DIR / "sync_status.json"
MANIFEST_PATH = OUT_DIR / "manifest.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


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
        "User-Agent": os.getenv("EMODNET_USER_AGENT", "MOwlSINT Voodoo Whiskers/1.0"),
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

    merged: list[dict[str, Any]] = []
    fetch_meta: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
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
            for feature in batch:
                feature = dict(feature)
                props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
                props = dict(props)
                props["_vw_layer"] = layer_id
                props["_emodnet_typename"] = ft["name"]
                props["_emodnet_title"] = ft.get("title", "")
                feature["properties"] = props
                identity = str(feature.get("id") or "")
                if not identity:
                    identity = json.dumps([feature.get("geometry"), props.get("name"), props.get("Name")], sort_keys=True, default=str)
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
                merged.append(feature)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ft['name']}: {type(exc).__name__}: {exc}")

    if not merged and errors:
        status["error"] = "; ".join(errors)
        status["existing_feature_count"] = load_existing_feature_count(out_path)
        status["preserved_last_known_good"] = out_path.exists()
        return status

    generated_at = utc_now_iso()
    payload = {
        "type": "FeatureCollection",
        "features": merged,
        "metadata": {
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "layer_id": layer_id,
            "source": "EMODnet Human Activities",
            "service_url": config["service_url"],
            "attribution": config.get("attribution"),
            "license_note": config.get("license_note"),
            "bbox": config["bbox"],
            "feature_types": fetch_meta,
            "style": rule.get("style", {}),
            "errors": errors,
        },
    }
    atomic_write_json(out_path, payload)
    status.update({
        "ok": True,
        "generated_at": generated_at,
        "feature_count": len(merged),
        "fetch": fetch_meta,
        "warnings": errors,
    })
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--strict", action="store_true", help="Fail when every configured layer fails")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = build_session()
    generated_at = utc_now_iso()
    capabilities_url = config["service_url"]
    response = session.get(
        capabilities_url,
        params={"SERVICE": "WFS", "REQUEST": "GetCapabilities", "VERSION": config.get("service_version", "2.0.0")},
        timeout=(20, 180),
    )
    response.raise_for_status()
    feature_types = parse_feature_types(response.content)
    if not feature_types:
        raise RuntimeError("EMODnet WFS GetCapabilities returned no feature types")

    results = [sync_layer(session, config, feature_types, rule) for rule in config.get("layers", [])]
    ok_count = sum(1 for row in results if row.get("ok"))
    status = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "service_url": config["service_url"],
        "feature_type_count": len(feature_types),
        "configured_layer_count": len(results),
        "successful_layer_count": ok_count,
        "layers": results,
    }
    atomic_write_json(STATUS_PATH, status)
    manifest = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "source": "EMODnet Human Activities",
        "attribution": config.get("attribution"),
        "license_note": config.get("license_note"),
        "bbox": config.get("bbox"),
        "layers": [
            {
                "id": row.get("id"),
                "href": f"./{row.get('filename')}",
                "feature_count": row.get("feature_count", row.get("existing_feature_count", 0)),
                "ok": bool(row.get("ok")),
                "preserved_last_known_good": bool(row.get("preserved_last_known_good")),
            }
            for row in results
        ],
    }
    atomic_write_json(MANIFEST_PATH, manifest)
    print(json.dumps({"feature_types": len(feature_types), "successful_layers": ok_count, "configured_layers": len(results)}, indent=2))
    if args.strict and ok_count == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
