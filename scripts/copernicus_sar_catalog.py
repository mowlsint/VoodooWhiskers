#!/usr/bin/env python3
"""Validate CDSE OAuth access and select one exact Sentinel-1 GRD pilot scene.

Phase 1 is deliberately metadata-only. It queries the public CDSE STAC catalogue,
validates a Sentinel Hub OAuth client, confirms that the same acquisition is
available to Sentinel Hub, and writes one small audit/status JSON file. It never
downloads a SAR raster, SAFE product, TIFF, or AIS archive.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/sar_copernicus_pilot.json"
DEFAULT_OUTPUT = ROOT / "data/sar_copernicus_scene_status_latest.json"

CDSE_STAC_SEARCH_URL = "https://stac.dataspace.copernicus.eu/v1/search"
SENTINEL_HUB_CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search"
CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_API_URL = "https://sh.dataspace.copernicus.eu/process/v1"

ASSESSMENT_LIMIT = (
    "Catalogue availability proves access to one Sentinel-1 acquisition only. It does not "
    "constitute a vessel detection, an AIS comparison, or evidence of intentional AIS "
    "disablement, unlawful activity, attribution, or hostile intent."
)


class PilotError(RuntimeError):
    """Expected, secret-safe pilot failure."""


class StdlibResponse:
    """Small requests-like response wrapper backed by Python's standard library."""

    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return json.loads(self._body.decode("utf-8"))


class StdlibSession:
    """Minimal POST-only HTTP session without a third-party runtime dependency."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def post(
        self,
        url: str,
        *,
        timeout: int = 60,
        headers: dict[str, str] | None = None,
        data: Any = None,
        **kwargs: Any,
    ) -> StdlibResponse:
        json_payload = kwargs.pop("json", None)
        if kwargs:
            raise TypeError(f"Unsupported HTTP option(s): {', '.join(sorted(kwargs))}")
        if json_payload is not None and data is not None:
            raise TypeError("HTTP request cannot contain both form data and JSON")

        request_headers = dict(self.headers)
        request_headers.update(headers or {})
        if json_payload is not None:
            body = json.dumps(json_payload, separators=(",", ":")).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, dict):
            body = urlencode(data).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif data is None:
            body = None
        elif isinstance(data, bytes):
            body = data
        else:
            body = str(data).encode("utf-8")

        request = Request(url, data=body, headers=request_headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                return StdlibResponse(int(response.status), response.read())
        except HTTPError as exc:
            return StdlibResponse(int(exc.code), exc.read())


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PilotError(f"Configuration file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PilotError(f"Configuration is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise PilotError("Pilot configuration must be a JSON object")
    return payload


def parse_utc(value: Any, label: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise PilotError(f"Missing {label}")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PilotError(f"Invalid UTC timestamp in {label}") from exc
    if parsed.tzinfo is None:
        raise PilotError(f"Timestamp in {label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != "1.0.0":
        raise PilotError("Unsupported pilot configuration schema")
    if config.get("collection") != "sentinel-1-grd":
        raise PilotError("Phase 1 supports only the sentinel-1-grd collection")
    if not str(config.get("pilot_id") or "").strip():
        raise PilotError("Pilot configuration has no pilot_id")
    if not str(config.get("expected_cdse_item_id") or "").startswith("S1"):
        raise PilotError("Expected CDSE Sentinel-1 item ID is missing or invalid")
    if not str(config.get("expected_sentinel_hub_product_id") or "").startswith("S1"):
        raise PilotError("Expected Sentinel Hub product ID is missing or invalid")

    time_range = config.get("time_range")
    if not isinstance(time_range, dict):
        raise PilotError("Pilot configuration has no time_range object")
    start = parse_utc(time_range.get("from"), "time_range.from")
    end = parse_utc(time_range.get("to"), "time_range.to")
    if start >= end:
        raise PilotError("Pilot time range is empty or reversed")
    if (end - start).total_seconds() > 3600:
        raise PilotError("Phase 1 time range must not exceed one hour")

    aoi = config.get("aoi")
    bbox = aoi.get("bbox_wgs84") if isinstance(aoi, dict) else None
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise PilotError("Pilot AOI must contain a four-value WGS84 bounding box")
    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise PilotError("Pilot AOI bounding box values must be numeric") from exc
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise PilotError("Pilot AOI bounding box is outside WGS84 limits or reversed")

    plan = config.get("processing_plan")
    if not isinstance(plan, dict):
        raise PilotError("Pilot configuration has no processing_plan")
    max_dimension = int(plan.get("maximum_process_api_dimension_px") or 0)
    width = int(plan.get("expected_width_px") or 0)
    height = int(plan.get("expected_height_px") or 0)
    if min(width, height, max_dimension) <= 0 or width > max_dimension or height > max_dimension:
        raise PilotError("Planned Process API image exceeds the configured dimension guard")

    guardrails = config.get("guardrails")
    required_guards = (
        "manual_workflow_only",
        "current_ais_must_not_be_overlaid",
    )
    if not isinstance(guardrails, dict) or any(guardrails.get(key) is not True for key in required_guards):
        raise PilotError("Mandatory Phase 1 guardrails are missing")
    forbidden_phase_1 = (
        "raw_download_performed_in_phase_1",
        "publish_public_layer_in_phase_1",
        "modify_magic_paws_in_phase_1",
        "modify_gfw_products_in_phase_1",
    )
    if any(guardrails.get(key) is not False for key in forbidden_phase_1):
        raise PilotError("Phase 1 configuration enables a forbidden operation")


def normalize_scene_id(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    normalized = normalized.removesuffix(".SAFE")
    normalized = normalized.removesuffix("_COG")
    return normalized


def scene_identifier_values(feature: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if feature.get("id") is not None:
        values.append(str(feature["id"]))
    properties = feature.get("properties")
    if isinstance(properties, dict):
        for key in (
            "title",
            "productIdentifier",
            "sentinel1ProductId",
            "s1:product_id",
            "product:id",
            "product_name",
        ):
            if properties.get(key) is not None:
                values.append(str(properties[key]))
    return values


def select_scene(payload: dict[str, Any], expected_id: str, label: str) -> tuple[dict[str, Any], int]:
    features = payload.get("features")
    if not isinstance(features, list):
        raise PilotError(f"{label} response has no features array")
    expected = normalize_scene_id(expected_id)
    matches: list[dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        identifiers = [normalize_scene_id(value) for value in scene_identifier_values(feature)]
        if expected in identifiers:
            matches.append(feature)
    if len(matches) != 1:
        raise PilotError(f"{label} returned {len(matches)} exact matches for the configured scene; expected one")
    return matches[0], len(features)


def response_json(response: Any, label: str) -> dict[str, Any]:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if not 200 <= status_code < 300:
        raise PilotError(f"{label} failed with HTTP {status_code or 'unknown'}")
    try:
        payload = response.json()
    except (UnicodeDecodeError, ValueError) as exc:
        raise PilotError(f"{label} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PilotError(f"{label} returned a non-object JSON response")
    return payload


def safe_post(session: Any, url: str, label: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = session.post(url, timeout=60, **kwargs)
    except (URLError, TimeoutError, OSError) as exc:
        raise PilotError(f"{label} network request failed ({type(exc).__name__})") from exc
    return response_json(response, label)


def request_access_token(session: Any, client_id: str, client_secret: str) -> tuple[str, dict[str, Any]]:
    if not client_id.strip() or not client_secret.strip():
        raise PilotError("CDSE Sentinel Hub OAuth credentials are missing")
    payload = safe_post(
        session,
        CDSE_TOKEN_URL,
        "CDSE OAuth token request",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise PilotError("CDSE OAuth token response contained no access token")
    metadata = {
        "oauth_client_credentials_valid": True,
        "access_token_received": True,
        "token_type": str(payload.get("token_type") or "Bearer"),
        "expires_in_seconds": int(payload.get("expires_in") or 0) or None,
        "client_id_or_secret_persisted": False,
        "access_token_persisted": False,
    }
    return token, metadata


def scene_summary(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    private = properties.get("_private") if isinstance(properties.get("_private"), dict) else {}
    assets = feature.get("assets") if isinstance(feature.get("assets"), dict) else {}
    asset_sizes: dict[str, int] = {}
    for key in ("Product", "vv", "vh", "safe_manifest"):
        asset = assets.get(key)
        if not isinstance(asset, dict):
            continue
        try:
            size = int(asset.get("file:size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > 0:
            asset_sizes[key] = size
    return {
        "item_id": feature.get("id"),
        "collection": feature.get("collection") or "sentinel-1-grd",
        "start_datetime": properties.get("start_datetime") or properties.get("datetime"),
        "end_datetime": properties.get("end_datetime") or properties.get("datetime"),
        "platform": properties.get("platform"),
        "instrument_mode": properties.get("sar:instrument_mode"),
        "polarizations": properties.get("sar:polarizations") or properties.get("s1:polarization"),
        "orbit_state": properties.get("sat:orbit_state"),
        "absolute_orbit": properties.get("sat:absolute_orbit"),
        "relative_orbit": properties.get("sat:relative_orbit"),
        "processing_level": properties.get("processing:level"),
        "product_type": properties.get("product:type"),
        "pixel_spacing_range_m": properties.get("sar:pixel_spacing_range"),
        "pixel_spacing_azimuth_m": properties.get("sar:pixel_spacing_azimuth"),
        "product_size_bytes": private.get("product_size") or asset_sizes.get("Product"),
        "asset_sizes_bytes": asset_sizes,
        "bbox": feature.get("bbox"),
    }


def base_status(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "generated_at": iso_z(utc_now()),
        "status": "error",
        "phase": "account_and_catalog_test",
        "provider": "copernicus_data_space_ecosystem",
        "pilot_id": config.get("pilot_id"),
        "pilot_name": config.get("name"),
        "collection": config.get("collection"),
        "aoi": config.get("aoi"),
        "configured_time_range": config.get("time_range"),
        "processing_plan": config.get("processing_plan"),
        "oauth": {
            "oauth_client_credentials_valid": False,
            "access_token_received": False,
            "client_id_or_secret_persisted": False,
            "access_token_persisted": False,
        },
        "catalogues": {},
        "selected_scene": None,
        "raw_download_performed": False,
        "raster_download_performed": False,
        "ais_download_performed": False,
        "large_products_persisted": False,
        "public_layer_modified": False,
        "gfw_products_modified": False,
        "magic_paws_modified": False,
        "current_ais_must_not_be_overlaid": True,
        "assessment_limit": ASSESSMENT_LIMIT,
        "next_phase": "single_scene_process_api_chip_download",
        "errors": [],
    }


def run_catalog_pilot(
    config: dict[str, Any],
    client_id: str,
    client_secret: str,
    *,
    session: Any | None = None,
) -> tuple[dict[str, Any], bool]:
    status = base_status(config)
    try:
        validate_config(config)
        http = session or StdlibSession()
        if hasattr(http, "headers"):
            http.headers.update({"User-Agent": "MOwlSINT-VoodooWhiskers-Copernicus-SAR-Pilot/1.0"})

        time_range = config["time_range"]
        search_body = {
            "collections": [config["collection"]],
            "bbox": config["aoi"]["bbox_wgs84"],
            "datetime": f"{time_range['from']}/{time_range['to']}",
            "limit": 20,
        }
        cdse_payload = safe_post(
            http,
            CDSE_STAC_SEARCH_URL,
            "CDSE public STAC search",
            headers={"Content-Type": "application/json"},
            json=search_body,
        )
        cdse_scene, cdse_returned = select_scene(
            cdse_payload,
            str(config["expected_cdse_item_id"]),
            "CDSE public STAC search",
        )
        status["catalogues"]["cdse_public_stac"] = {
            "endpoint": CDSE_STAC_SEARCH_URL,
            "authenticated": False,
            "query_returned_features": cdse_returned,
            "configured_scene_found_once": True,
        }

        access_token, oauth_metadata = request_access_token(http, client_id, client_secret)
        status["oauth"] = oauth_metadata
        sh_payload = safe_post(
            http,
            SENTINEL_HUB_CATALOG_URL,
            "Sentinel Hub authenticated catalogue search",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=search_body,
        )
        _, sh_returned = select_scene(
            sh_payload,
            str(config["expected_sentinel_hub_product_id"]),
            "Sentinel Hub authenticated catalogue search",
        )
        status["catalogues"]["sentinel_hub"] = {
            "endpoint": SENTINEL_HUB_CATALOG_URL,
            "authenticated": True,
            "query_returned_features": sh_returned,
            "configured_scene_found_once": True,
            "process_api_endpoint_for_next_phase": PROCESS_API_URL,
        }
        status["selected_scene"] = scene_summary(cdse_scene)
        status["status"] = "ok"
        return status, True
    except PilotError as exc:
        status["errors"] = [str(exc)]
        return status, False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = read_json(args.config)
    except PilotError as exc:
        config = {"pilot_id": None, "name": None, "collection": "sentinel-1-grd"}
        status = base_status(config)
        status["errors"] = [str(exc)]
        atomic_json(args.output, status)
        print(json.dumps({"status": "error", "phase": status["phase"], "errors": status["errors"]}))
        return 1

    client_id = os.environ.get("CDSE_SH_CLIENT_ID", "")
    client_secret = os.environ.get("CDSE_SH_CLIENT_SECRET", "")
    status, ok = run_catalog_pilot(config, client_id, client_secret)
    atomic_json(args.output, status)
    print(
        json.dumps(
            {
                "status": status["status"],
                "phase": status["phase"],
                "pilot_id": status["pilot_id"],
                "scene_id": (status.get("selected_scene") or {}).get("item_id"),
                "oauth_valid": status.get("oauth", {}).get("oauth_client_credentials_valid"),
                "raw_download_performed": status["raw_download_performed"],
                "errors": status["errors"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
