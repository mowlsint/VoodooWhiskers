#!/usr/bin/env python3
"""Download and validate one temporary Sentinel-1 GRD pilot chip.

Phase 2 is deliberately limited to one catalogue-confirmed acquisition and one
small Process API request. The Float32 GeoTIFF exists only in an operating-system
temporary directory, is inspected for shape/georeferencing/data quality, and is
deleted before a small JSON status is written. This phase performs no detection,
AIS matching, publication, or downstream product update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import copernicus_sar_catalog as catalog


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/sar_copernicus_chip.json"
DEFAULT_OUTPUT = ROOT / "data/sar_copernicus_chip_status_latest.json"

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["VV", "VH", "dataMask"] }],
    output: {
      id: "default",
      bands: 3,
      sampleType: "FLOAT32"
    }
  };
}

function evaluatePixel(sample) {
  return [sample.VV, sample.VH, sample.dataMask];
}
"""

ASSESSMENT_LIMIT = (
    "This phase validates one temporary Sentinel-1 raster only. It does not perform "
    "vessel detection, AIS matching, or assessment of identity, intent, legality, or "
    "AIS disablement."
)


class BinaryResponse:
    """Small response wrapper for JSON and bounded binary responses."""

    def __init__(self, status_code: int, content: bytes, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class BinarySession:
    """Minimal POST-only HTTP session with explicit response-size limits."""

    DEFAULT_MAX_BYTES = 5 * 1024 * 1024

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    @staticmethod
    def _read_bounded(response: Any, maximum: int) -> bytes:
        content = response.read(maximum + 1)
        if len(content) > maximum:
            raise catalog.PilotError("HTTP response exceeded the configured byte limit")
        return content

    def post(
        self,
        url: str,
        *,
        timeout: int = 60,
        headers: dict[str, str] | None = None,
        data: Any = None,
        **kwargs: Any,
    ) -> BinaryResponse:
        json_payload = kwargs.pop("json", None)
        maximum = int(kwargs.pop("max_bytes", self.DEFAULT_MAX_BYTES))
        if kwargs:
            raise TypeError(f"Unsupported HTTP option(s): {', '.join(sorted(kwargs))}")
        if maximum <= 0:
            raise ValueError("max_bytes must be positive")
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
                content = self._read_bounded(response, maximum)
                return BinaryResponse(int(response.status), content, dict(response.headers.items()))
        except HTTPError as exc:
            content = self._read_bounded(exc, min(maximum, self.DEFAULT_MAX_BYTES))
            return BinaryResponse(int(exc.code), content, dict(exc.headers.items()))


def validate_phase_config(phase_config: dict[str, Any], pilot_config: dict[str, Any]) -> None:
    catalog.validate_config(pilot_config)
    if phase_config.get("schema_version") != "1.0.0":
        raise catalog.PilotError("Unsupported chip configuration schema")
    if phase_config.get("phase") != "single_scene_process_api_chip":
        raise catalog.PilotError("Unexpected chip configuration phase")
    if phase_config.get("pilot_config") != "config/sar_copernicus_pilot.json":
        raise catalog.PilotError("Chip configuration must reference the fixed pilot configuration")

    request = phase_config.get("request")
    if not isinstance(request, dict):
        raise catalog.PilotError("Chip configuration has no request object")
    try:
        width = int(request.get("width_px") or 0)
        height = int(request.get("height_px") or 0)
        maximum_dimension = int(request.get("maximum_dimension_px") or 0)
        maximum_bytes = int(request.get("maximum_response_bytes") or 0)
        minimum_valid_fraction = float(request.get("minimum_valid_fraction"))
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError("Chip dimensions, byte limit, or valid fraction are invalid") from exc
    if min(width, height, maximum_dimension) <= 0:
        raise catalog.PilotError("Chip dimensions must be positive")
    if width > maximum_dimension or height > maximum_dimension or maximum_dimension > 2500:
        raise catalog.PilotError("Chip request exceeds the synchronous Process API dimension guard")
    if not 1024 * 1024 <= maximum_bytes <= 128 * 1024 * 1024:
        raise catalog.PilotError("Chip response byte limit is outside the safe pilot range")
    if not 0.5 <= minimum_valid_fraction <= 1.0:
        raise catalog.PilotError("Chip minimum valid fraction is outside the safe range")

    plan = pilot_config.get("processing_plan") or {}
    if width != int(plan.get("expected_width_px") or 0) or height != int(plan.get("expected_height_px") or 0):
        raise catalog.PilotError("Chip dimensions do not match the reviewed pilot plan")
    if maximum_dimension != int(plan.get("maximum_process_api_dimension_px") or 0):
        raise catalog.PilotError("Chip dimension guard does not match the reviewed pilot plan")

    expected_request_values = {
        "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "bands": ["VV", "VH", "dataMask"],
        "band_units": ["LINEAR_POWER", "LINEAR_POWER", "DN"],
        "sample_type": "FLOAT32",
        "format": "image/tiff",
    }
    for key, expected in expected_request_values.items():
        if request.get(key) != expected:
            raise catalog.PilotError(f"Chip request has an unexpected {key}")

    data_filter = request.get("data_filter")
    expected_filter = {
        "resolution": "HIGH",
        "acquisition_mode": "IW",
        "polarization": "DV",
        "orbit_direction": "DESCENDING",
        "mosaicking_order": "mostRecent",
    }
    if data_filter != expected_filter:
        raise catalog.PilotError("Chip request data filter differs from the reviewed single-scene plan")

    processing = request.get("processing")
    expected_processing = {
        "back_coeff": "GAMMA0_ELLIPSOID",
        "orthorectify": True,
        "dem_instance": "COPERNICUS_30",
        "speckle_filter": "NONE",
        "radiometric_terrain_correction": False,
    }
    if processing != expected_processing:
        raise catalog.PilotError("Chip processing options differ from the reviewed pilot plan")

    time_range = pilot_config.get("time_range") or {}
    start = catalog.parse_utc(time_range.get("from"), "time_range.from")
    end = catalog.parse_utc(time_range.get("to"), "time_range.to")
    if (end - start).total_seconds() > 120:
        raise catalog.PilotError("Single-scene Process API time range exceeds two minutes")
    bbox = (pilot_config.get("aoi") or {}).get("bbox_wgs84") or []
    if len(bbox) != 4 or float(bbox[2]) - float(bbox[0]) > 0.5 or float(bbox[3]) - float(bbox[1]) > 0.5:
        raise catalog.PilotError("Single-scene chip AOI exceeds the reviewed geographic guard")

    guardrails = phase_config.get("guardrails")
    if not isinstance(guardrails, dict):
        raise catalog.PilotError("Chip configuration has no guardrails object")
    required_true = (
        "manual_workflow_only",
        "catalogue_must_return_one_scene",
        "temporary_raster_only",
        "delete_raster_after_validation",
        "current_ais_must_not_be_overlaid",
    )
    if any(guardrails.get(key) is not True for key in required_true):
        raise catalog.PilotError("Mandatory chip guardrails are missing")
    required_false = (
        "persist_raster",
        "upload_raster_artifact",
        "perform_detection",
        "download_ais",
        "publish_public_layer",
        "modify_gfw_products",
        "modify_magic_paws",
    )
    if any(guardrails.get(key) is not False for key in required_false):
        raise catalog.PilotError("Chip configuration enables a forbidden Phase 2 operation")
    if guardrails.get("confirmation_phrase") != "PROCESS_ONE_SCENE":
        raise catalog.PilotError("Chip confirmation phrase differs from the reviewed guard")


def build_catalog_search(pilot_config: dict[str, Any]) -> dict[str, Any]:
    time_range = pilot_config["time_range"]
    return {
        "collections": [pilot_config["collection"]],
        "bbox": pilot_config["aoi"]["bbox_wgs84"],
        "datetime": f"{time_range['from']}/{time_range['to']}",
        "limit": 20,
    }


def build_process_request(phase_config: dict[str, Any], pilot_config: dict[str, Any]) -> dict[str, Any]:
    request = phase_config["request"]
    data_filter = request["data_filter"]
    processing = request["processing"]
    time_range = pilot_config["time_range"]
    return {
        "input": {
            "bounds": {
                "bbox": pilot_config["aoi"]["bbox_wgs84"],
                "properties": {"crs": request["crs"]},
            },
            "data": [
                {
                    "type": "sentinel-1-grd",
                    "dataFilter": {
                        "timeRange": {
                            "from": time_range["from"],
                            "to": time_range["to"],
                        },
                        "mosaickingOrder": data_filter["mosaicking_order"],
                        "resolution": data_filter["resolution"],
                        "acquisitionMode": data_filter["acquisition_mode"],
                        "polarization": data_filter["polarization"],
                        "orbitDirection": data_filter["orbit_direction"],
                    },
                    "processing": {
                        "backCoeff": processing["back_coeff"],
                        "orthorectify": "true",
                        "demInstance": processing["dem_instance"],
                    },
                }
            ],
        },
        "output": {
            "width": request["width_px"],
            "height": request["height_px"],
            "responses": [
                {
                    "identifier": "default",
                    "format": {"type": request["format"]},
                }
            ],
        },
        "evalscript": EVALSCRIPT,
    }


def processing_unit_estimate(phase_config: dict[str, Any]) -> dict[str, Any]:
    request = phase_config.get("request") or {}
    width = int(request.get("width_px") or 0)
    height = int(request.get("height_px") or 0)
    factors = {
        "output_pixels_vs_512_square": (width * height) / (512 * 512),
        "two_chargeable_bands_vs_three": 2 / 3,
        "float32_tiff": 2.0,
        "one_data_sample": 1.0,
        "orthorectification": 2.0,
        "speckle_filter": 1.0,
        "radiometric_terrain_correction": 1.0,
    }
    total = 1.0
    for value in factors.values():
        total *= value
    return {
        "estimated_processing_units": round(total, 3),
        "factors": {key: round(value, 6) for key, value in factors.items()},
        "estimate_only": True,
    }


def request_process_chip(
    session: Any,
    access_token: str,
    body: dict[str, Any],
    maximum_bytes: int,
) -> tuple[bytes, dict[str, str]]:
    try:
        response = session.post(
            catalog.PROCESS_API_URL,
            timeout=240,
            max_bytes=maximum_bytes,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "image/tiff",
                "Content-Type": "application/json",
            },
            json=body,
        )
    except (URLError, TimeoutError, OSError) as exc:
        raise catalog.PilotError(f"Sentinel Hub Process API network request failed ({type(exc).__name__})") from exc
    status_code = int(getattr(response, "status_code", 0) or 0)
    if not 200 <= status_code < 300:
        raise catalog.PilotError(f"Sentinel Hub Process API failed with HTTP {status_code or 'unknown'}")
    content = bytes(getattr(response, "content", b""))
    if not content:
        raise catalog.PilotError("Sentinel Hub Process API returned an empty raster")
    if len(content) > maximum_bytes:
        raise catalog.PilotError("Sentinel Hub Process API raster exceeded the configured byte limit")
    if content[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        raise catalog.PilotError("Sentinel Hub Process API response is not a TIFF")
    headers = {
        str(key).lower(): str(value)
        for key, value in dict(getattr(response, "headers", {}) or {}).items()
    }
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in ("image/tiff", "image/geotiff", "application/octet-stream"):
        raise catalog.PilotError(f"Sentinel Hub Process API returned unexpected content type {content_type}")
    return content, headers


def _rounded(value: Any) -> float:
    return round(float(value), 10)


def _tag_values(page: Any, code: int) -> list[float] | None:
    tag = page.tags.get(code)
    if tag is None:
        return None
    value = tag.value
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return [_rounded(value)]


def analyze_tiff(path: Path, phase_config: dict[str, Any]) -> dict[str, Any]:
    try:
        import numpy as np
        import tifffile
    except ImportError as exc:
        raise catalog.PilotError("Phase 2 requires numpy and tifffile for temporary raster validation") from exc

    request = phase_config["request"]
    expected_height = int(request["height_px"])
    expected_width = int(request["width_px"])
    expected_bands = len(request["bands"])
    try:
        with tifffile.TiffFile(path) as tif:
            array = tif.asarray()
            axes = str(tif.series[0].axes) if tif.series else None
            page = tif.pages[0]
            byte_order = str(tif.byteorder)
            page_count = len(tif.pages)
            model_pixel_scale = _tag_values(page, 33550)
            model_tiepoint = _tag_values(page, 33922)
            model_transformation = _tag_values(page, 34264)
            geo_key_directory_present = page.tags.get(34735) is not None
    except Exception as exc:
        raise catalog.PilotError(f"Temporary raster could not be parsed as TIFF ({type(exc).__name__})") from exc

    if array.shape == (expected_height, expected_width, expected_bands):
        normalized = array
    elif array.shape == (expected_bands, expected_height, expected_width):
        normalized = np.moveaxis(array, 0, -1)
    else:
        raise catalog.PilotError(
            f"Temporary raster has unexpected shape {tuple(int(value) for value in array.shape)}"
        )
    if normalized.dtype != np.dtype("float32"):
        raise catalog.PilotError(f"Temporary raster has unexpected dtype {normalized.dtype}")

    spatial_transform_present = bool(
        (model_pixel_scale is not None and model_tiepoint is not None)
        or model_transformation is not None
    )
    if not spatial_transform_present or not geo_key_directory_present:
        raise catalog.PilotError("Temporary raster is missing required GeoTIFF georeferencing tags")

    mask = normalized[:, :, 2]
    if not np.isfinite(mask).all():
        raise catalog.PilotError("Temporary raster dataMask contains non-finite values")
    mask_min = float(mask.min())
    mask_max = float(mask.max())
    if mask_min < -0.001 or mask_max > 1.001:
        raise catalog.PilotError("Temporary raster dataMask is outside the expected 0..1 range")
    valid = mask >= 0.5
    valid_count = int(valid.sum())
    total_count = int(valid.size)
    valid_fraction = valid_count / total_count if total_count else 0.0
    if valid_fraction < float(request["minimum_valid_fraction"]):
        raise catalog.PilotError(
            f"Temporary raster valid fraction {valid_fraction:.4f} is below the configured minimum"
        )

    band_statistics: dict[str, Any] = {}
    for index, band_name in enumerate(("VV", "VH")):
        values = normalized[:, :, index][valid]
        if not np.isfinite(values).all():
            raise catalog.PilotError(f"Temporary raster {band_name} contains non-finite valid pixels")
        if values.size == 0 or float(values.min()) < 0:
            raise catalog.PilotError(f"Temporary raster {band_name} has no valid non-negative samples")
        linear_percentiles = np.percentile(values, [50, 90, 95, 99, 99.9])
        positive = values[values > 0]
        if positive.size == 0:
            raise catalog.PilotError(f"Temporary raster {band_name} contains no positive samples")
        decibels = 10.0 * np.log10(positive)
        db_percentiles = np.percentile(decibels, [50, 90, 95, 99, 99.9])
        band_statistics[band_name] = {
            "valid_samples": int(values.size),
            "positive_samples": int(positive.size),
            "linear_power": {
                "min": _rounded(values.min()),
                "max": _rounded(values.max()),
                "mean": _rounded(values.mean()),
                "stddev": _rounded(values.std()),
                "p50": _rounded(linear_percentiles[0]),
                "p90": _rounded(linear_percentiles[1]),
                "p95": _rounded(linear_percentiles[2]),
                "p99": _rounded(linear_percentiles[3]),
                "p99_9": _rounded(linear_percentiles[4]),
            },
            "decibels": {
                "min": _rounded(decibels.min()),
                "max": _rounded(decibels.max()),
                "mean": _rounded(decibels.mean()),
                "p50": _rounded(db_percentiles[0]),
                "p90": _rounded(db_percentiles[1]),
                "p95": _rounded(db_percentiles[2]),
                "p99": _rounded(db_percentiles[3]),
                "p99_9": _rounded(db_percentiles[4]),
            },
        }

    return {
        "shape": [int(value) for value in normalized.shape],
        "dtype": str(normalized.dtype),
        "axes_reported_by_tifffile": axes,
        "page_count": page_count,
        "byte_order": byte_order,
        "bands": request["bands"],
        "band_units": request["band_units"],
        "valid_pixels": valid_count,
        "total_pixels": total_count,
        "valid_fraction": round(valid_fraction, 8),
        "data_mask_min": _rounded(mask_min),
        "data_mask_max": _rounded(mask_max),
        "geotiff": {
            "spatial_transform_present": spatial_transform_present,
            "geo_key_directory_present": geo_key_directory_present,
            "model_pixel_scale": model_pixel_scale,
            "model_tiepoint": model_tiepoint,
            "model_transformation_present": model_transformation is not None,
        },
        "band_statistics": band_statistics,
    }


def base_status(phase_config: dict[str, Any], pilot_config: dict[str, Any]) -> dict[str, Any]:
    request = phase_config.get("request") if isinstance(phase_config.get("request"), dict) else {}
    processing = request.get("processing") if isinstance(request, dict) else {}
    data_filter = request.get("data_filter") if isinstance(request, dict) else {}
    return {
        "schema_version": "1.0.0",
        "generated_at": catalog.iso_z(catalog.utc_now()),
        "status": "error",
        "phase": "single_scene_process_api_chip",
        "provider": "copernicus_data_space_ecosystem",
        "pilot_id": pilot_config.get("pilot_id"),
        "pilot_name": pilot_config.get("name"),
        "expected_scene_id": pilot_config.get("expected_cdse_item_id"),
        "aoi": pilot_config.get("aoi"),
        "configured_time_range": pilot_config.get("time_range"),
        "request_summary": {
            "endpoint": catalog.PROCESS_API_URL,
            "crs": request.get("crs") if isinstance(request, dict) else None,
            "width_px": request.get("width_px") if isinstance(request, dict) else None,
            "height_px": request.get("height_px") if isinstance(request, dict) else None,
            "bands": request.get("bands") if isinstance(request, dict) else None,
            "band_units": request.get("band_units") if isinstance(request, dict) else None,
            "sample_type": request.get("sample_type") if isinstance(request, dict) else None,
            "format": request.get("format") if isinstance(request, dict) else None,
            "data_filter": data_filter,
            "processing": processing,
            "evalscript_sha256": hashlib.sha256(EVALSCRIPT.encode("utf-8")).hexdigest(),
        },
        "processing_unit_estimate": processing_unit_estimate(phase_config),
        "oauth": {
            "oauth_client_credentials_valid": False,
            "access_token_received": False,
            "client_id_or_secret_persisted": False,
            "access_token_persisted": False,
        },
        "catalogue_confirmation": {
            "endpoint": catalog.SENTINEL_HUB_CATALOG_URL,
            "authenticated": False,
            "returned_features": None,
            "exact_scene_found_once": False,
            "single_scene_guard_passed": False,
        },
        "selected_scene": None,
        "raster_download_performed": False,
        "raster_response_bytes": None,
        "raster_sha256": None,
        "process_api_content_type": None,
        "process_api_reported_processing_units": None,
        "temporary_raster_written": False,
        "temporary_raster_deleted": False,
        "raster_persisted": False,
        "raster_artifact_uploaded": False,
        "raster_validation": None,
        "raw_product_download_performed": False,
        "ais_download_performed": False,
        "detection_performed": False,
        "public_layer_modified": False,
        "gfw_products_modified": False,
        "magic_paws_modified": False,
        "current_ais_must_not_be_overlaid": True,
        "assessment_limit": ASSESSMENT_LIMIT,
        "next_phase": "single_scene_cfar_candidate_extraction",
        "errors": [],
    }


def run_chip_pilot(
    phase_config: dict[str, Any],
    pilot_config: dict[str, Any],
    client_id: str,
    client_secret: str,
    *,
    session: Any | None = None,
) -> tuple[dict[str, Any], bool]:
    status = base_status(phase_config, pilot_config)
    temporary_path: Path | None = None
    try:
        validate_phase_config(phase_config, pilot_config)
        http = session or BinarySession()
        if hasattr(http, "headers"):
            http.headers.update({"User-Agent": "MOwlSINT-VoodooWhiskers-Copernicus-SAR-Chip/1.0"})

        access_token, oauth_metadata = catalog.request_access_token(http, client_id, client_secret)
        status["oauth"] = oauth_metadata
        search_payload = catalog.safe_post(
            http,
            catalog.SENTINEL_HUB_CATALOG_URL,
            "Sentinel Hub authenticated single-scene catalogue search",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=build_catalog_search(pilot_config),
        )
        scene, returned_features = catalog.select_scene(
            search_payload,
            str(pilot_config["expected_sentinel_hub_product_id"]),
            "Sentinel Hub authenticated single-scene catalogue search",
        )
        if returned_features != 1:
            raise catalog.PilotError(
                f"Single-scene guard rejected {returned_features} catalogue features; expected exactly one"
            )
        status["catalogue_confirmation"] = {
            "endpoint": catalog.SENTINEL_HUB_CATALOG_URL,
            "authenticated": True,
            "returned_features": returned_features,
            "exact_scene_found_once": True,
            "single_scene_guard_passed": True,
        }
        expected_normalized = catalog.normalize_scene_id(
            pilot_config["expected_sentinel_hub_product_id"]
        )
        matched_identifier = next(
            (
                value
                for value in catalog.scene_identifier_values(scene)
                if catalog.normalize_scene_id(value) == expected_normalized
            ),
            None,
        )
        if matched_identifier is None:
            raise catalog.PilotError("Selected catalogue item lost its exact scene identifier")
        status["selected_scene"] = {
            "catalogue_item_id": scene.get("id"),
            "matched_scene_identifier": matched_identifier,
            "normalized_scene_id": expected_normalized,
            "datetime": (scene.get("properties") or {}).get("datetime"),
        }

        process_body = build_process_request(phase_config, pilot_config)
        raster_bytes, response_headers = request_process_chip(
            http,
            access_token,
            process_body,
            int(phase_config["request"]["maximum_response_bytes"]),
        )
        status["raster_download_performed"] = True
        status["raster_response_bytes"] = len(raster_bytes)
        status["raster_sha256"] = hashlib.sha256(raster_bytes).hexdigest()
        status["process_api_content_type"] = response_headers.get("content-type")
        reported_units = (
            response_headers.get("x-processingunits-spent")
            or response_headers.get("x-processing-units-spent")
        )
        if reported_units is not None:
            try:
                status["process_api_reported_processing_units"] = float(reported_units)
            except ValueError:
                status["process_api_reported_processing_units"] = None

        with tempfile.TemporaryDirectory(prefix="voodoo-sar-chip-") as temporary_directory:
            temporary_path = Path(temporary_directory) / "sentinel1_pilot_chip.tiff"
            temporary_path.write_bytes(raster_bytes)
            status["temporary_raster_written"] = True
            status["raster_validation"] = analyze_tiff(temporary_path, phase_config)
        status["temporary_raster_deleted"] = temporary_path is not None and not temporary_path.exists()
        if not status["temporary_raster_deleted"]:
            raise catalog.PilotError("Temporary raster cleanup could not be verified")

        status["status"] = "ok"
        return status, True
    except catalog.PilotError as exc:
        if temporary_path is not None and not temporary_path.exists():
            status["temporary_raster_deleted"] = True
        status["errors"] = [str(exc)]
        return status, False


def resolve_pilot_config_path(phase_config: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override
    relative = Path(str(phase_config.get("pilot_config") or ""))
    resolved = (ROOT / relative).resolve()
    if resolved != ROOT and ROOT not in resolved.parents:
        raise catalog.PilotError("Pilot configuration path escapes the repository root")
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pilot-config", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    phase_config: dict[str, Any] = {"phase": "single_scene_process_api_chip"}
    pilot_config: dict[str, Any] = {}
    try:
        phase_config = catalog.read_json(args.config)
        pilot_config = catalog.read_json(resolve_pilot_config_path(phase_config, args.pilot_config))
    except catalog.PilotError as exc:
        status = base_status(phase_config, pilot_config)
        status["errors"] = [str(exc)]
        catalog.atomic_json(args.output, status)
        print(json.dumps({"status": "error", "phase": status["phase"], "errors": status["errors"]}))
        return 1

    status, ok = run_chip_pilot(
        phase_config,
        pilot_config,
        os.environ.get("CDSE_SH_CLIENT_ID", ""),
        os.environ.get("CDSE_SH_CLIENT_SECRET", ""),
    )
    catalog.atomic_json(args.output, status)
    print(
        json.dumps(
            {
                "status": status["status"],
                "phase": status["phase"],
                "pilot_id": status["pilot_id"],
                "scene_id": (status.get("selected_scene") or {}).get("matched_scene_identifier"),
                "oauth_valid": status.get("oauth", {}).get("oauth_client_credentials_valid"),
                "catalogue_single_scene": status.get("catalogue_confirmation", {}).get(
                    "single_scene_guard_passed"
                ),
                "raster_download_performed": status["raster_download_performed"],
                "temporary_raster_deleted": status["temporary_raster_deleted"],
                "detection_performed": status["detection_performed"],
                "errors": status["errors"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
