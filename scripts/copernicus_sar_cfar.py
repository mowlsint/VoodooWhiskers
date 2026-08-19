#!/usr/bin/env python3
"""Extract unreviewed SAR candidate groups from one temporary Sentinel-1 chip.

Phase 3 compares three deliberately experimental CA-CFAR parameter profiles on
VV linear power. VH is used only as a corroborating signal. The output is a
small internal GeoJSON, an audit/status JSON and a derived comparison PNG. The
source GeoTIFF remains temporary and is deleted before any output is written.

Nothing produced here is a vessel classification, an AIS assessment, a claim
about AIS disablement, or an operational alert. Human visual review is required.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import copernicus_sar_catalog as catalog
import copernicus_sar_chip as chip


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/sar_copernicus_cfar.json"
DEFAULT_STATUS = ROOT / "data/sar_copernicus_cfar_status_latest.json"
DEFAULT_CANDIDATES = ROOT / "data/sar_copernicus_cfar_candidates_latest.geojson"
DEFAULT_QUICKLOOK = ROOT / "data/sar_copernicus_cfar_parameter_comparison_latest.png"
DEFAULT_LAND_MASK = ROOT / "config/sar_copernicus_land_mask.geojson"

PHASE = "single_scene_cfar_parameter_comparison"
CANDIDATE_STATUS = "UNREVIEWED_SAR_CANDIDATE"
AIS_CONTEXT_STATUS = "NOT_CHECKED"
ASSESSMENT_LIMIT = (
    "Experimental CFAR bright-return groups are not vessel classifications. No AIS data was "
    "retrieved or overlaid, no claim about a so-called dark vessel is made, and every candidate "
    "requires visual analyst review before any later use."
)
METHOD_NOTE = (
    "The three CA-CFAR settings are an uncalibrated parameter comparison for one scene, not "
    "validated operating thresholds. VV is primary; VH can corroborate but cannot create a "
    "candidate by itself."
)


def _repo_path(value: Any, expected: Path | None = None) -> Path:
    relative = Path(str(value or ""))
    if not str(relative) or relative.is_absolute():
        raise catalog.PilotError("Phase 3 output/configuration path must be repository-relative")
    resolved = (ROOT / relative).resolve()
    if resolved != ROOT and ROOT not in resolved.parents:
        raise catalog.PilotError("Phase 3 path escapes the repository root")
    if expected is not None and resolved != expected.resolve():
        raise catalog.PilotError(f"Phase 3 path must remain fixed at {expected.relative_to(ROOT)}")
    return resolved


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError(f"Invalid numeric value in {label}") from exc
    if not math.isfinite(number):
        raise catalog.PilotError(f"Non-finite numeric value in {label}")
    return number


def _validate_profile(profile: dict[str, Any], label: str) -> None:
    if not isinstance(profile, dict):
        raise catalog.PilotError(f"{label} is not an object")
    profile_id = str(profile.get("id") or "").strip()
    if label.startswith("algorithm.profiles") and not profile_id:
        raise catalog.PilotError(f"{label} has no id")
    try:
        training_radius = int(profile.get("training_radius_px"))
        guard_radius = int(profile.get("guard_radius_px"))
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError(f"{label} radii are invalid") from exc
    probability = _number(profile.get("probability_of_false_alarm"), f"{label}.probability_of_false_alarm")
    if not 5 <= training_radius <= 64:
        raise catalog.PilotError(f"{label} training radius is outside the pilot range")
    if not 1 <= guard_radius < training_radius:
        raise catalog.PilotError(f"{label} guard radius is invalid")
    if not 1e-10 <= probability <= 1e-3:
        raise catalog.PilotError(f"{label} false-alarm probability is outside the pilot range")


def validate_phase_config(
    phase_config: dict[str, Any],
    pilot_config: dict[str, Any],
    chip_config: dict[str, Any],
) -> None:
    chip.validate_phase_config(chip_config, pilot_config)
    if phase_config.get("schema_version") != "1.0.0":
        raise catalog.PilotError("Unsupported Phase 3 configuration schema")
    if phase_config.get("phase") != PHASE:
        raise catalog.PilotError("Unexpected Phase 3 configuration phase")
    if phase_config.get("pilot_config") != "config/sar_copernicus_pilot.json":
        raise catalog.PilotError("Phase 3 must reference the fixed pilot configuration")
    if phase_config.get("chip_config") != "config/sar_copernicus_chip.json":
        raise catalog.PilotError("Phase 3 must reference the fixed chip configuration")

    algorithm = phase_config.get("algorithm")
    if not isinstance(algorithm, dict):
        raise catalog.PilotError("Phase 3 configuration has no algorithm object")
    if algorithm.get("name") != "CA_CFAR":
        raise catalog.PilotError("Phase 3 supports only the reviewed CA-CFAR prototype")
    if algorithm.get("implementation_version") != "0.1.0-prototype":
        raise catalog.PilotError("Unexpected CA-CFAR implementation version")
    if algorithm.get("primary_band") != "VV" or algorithm.get("secondary_band") != "VH":
        raise catalog.PilotError("Phase 3 requires VV primary and VH secondary")
    profiles = algorithm.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 3:
        raise catalog.PilotError("Phase 3 requires exactly three CFAR comparison profiles")
    expected_ids = ["strict_wide", "balanced", "exploratory_local"]
    if [str(item.get("id") or "") for item in profiles if isinstance(item, dict)] != expected_ids:
        raise catalog.PilotError("CFAR comparison profile identifiers or order changed")
    combinations: set[tuple[int, int, float]] = set()
    for index, profile in enumerate(profiles):
        _validate_profile(profile, f"algorithm.profiles[{index}]")
        combinations.add(
            (
                int(profile["training_radius_px"]),
                int(profile["guard_radius_px"]),
                float(profile["probability_of_false_alarm"]),
            )
        )
    if len(combinations) != 3:
        raise catalog.PilotError("CFAR comparison profiles must use three distinct parameter combinations")
    vh_profile = algorithm.get("vh_corroboration")
    _validate_profile(vh_profile, "algorithm.vh_corroboration")

    minimum_fraction = _number(
        algorithm.get("minimum_training_fraction"), "algorithm.minimum_training_fraction"
    )
    if not 0.5 <= minimum_fraction <= 1.0:
        raise catalog.PilotError("Minimum CFAR training fraction is outside the pilot range")
    try:
        link_radius = int(algorithm.get("candidate_link_radius_px"))
        maximum_seeds = int(algorithm.get("maximum_seed_pixels_per_profile"))
        maximum_candidates = int(algorithm.get("maximum_union_candidates"))
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError("CFAR grouping/noise guards are invalid") from exc
    if not 0 <= link_radius <= 3:
        raise catalog.PilotError("Candidate link radius is outside the pilot range")
    if not 100 <= maximum_seeds <= 100000:
        raise catalog.PilotError("Per-profile seed guard is outside the pilot range")
    if not 10 <= maximum_candidates <= 5000:
        raise catalog.PilotError("Candidate-count guard is outside the pilot range")

    spatial = phase_config.get("spatial_context")
    if not isinstance(spatial, dict):
        raise catalog.PilotError("Phase 3 configuration has no spatial_context object")
    if spatial.get("open_water_aoi_reviewed") is not True:
        raise catalog.PilotError("The reviewed open-water AOI guard is missing")
    if spatial.get("land_mask_required") is not True:
        raise catalog.PilotError("The Phase 3 land-mask guard is missing")
    _repo_path(spatial.get("land_mask_file"), DEFAULT_LAND_MASK)
    if spatial.get("land_mask_source") != "Natural Earth 1:10m land v5.1.1":
        raise catalog.PilotError("The Phase 3 land-mask source changed unexpectedly")
    if spatial.get("coastline_mask_integrated") is not True:
        raise catalog.PilotError("Phase 3 requires its fixed pilot coastline/land mask")
    try:
        land_buffer_radius = int(spatial.get("land_buffer_radius_px"))
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError("The Phase 3 land-buffer radius is invalid") from exc
    if not 1 <= land_buffer_radius <= 250:
        raise catalog.PilotError("The Phase 3 land-buffer radius is outside the pilot range")
    if spatial.get("exclude_largest_cfar_border") is not True:
        raise catalog.PilotError("The common CFAR border exclusion guard is missing")
    search_radius = _number(
        spatial.get("infrastructure_search_radius_nm"),
        "spatial_context.infrastructure_search_radius_nm",
    )
    review_radius = _number(
        spatial.get("infrastructure_review_radius_nm"),
        "spatial_context.infrastructure_review_radius_nm",
    )
    if not 1 <= search_radius <= 25 or not 0 < review_radius <= search_radius:
        raise catalog.PilotError("Infrastructure context radii are invalid")

    quicklook = phase_config.get("quicklook")
    if not isinstance(quicklook, dict):
        raise catalog.PilotError("Phase 3 configuration has no quicklook object")
    try:
        maximum_width = int(quicklook.get("maximum_panel_width_px"))
        maximum_png_bytes = int(quicklook.get("maximum_png_bytes"))
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError("Quicklook size guards are invalid") from exc
    low = _number(quicklook.get("vv_db_percentile_low"), "quicklook.vv_db_percentile_low")
    high = _number(quicklook.get("vv_db_percentile_high"), "quicklook.vv_db_percentile_high")
    if not 240 <= maximum_width <= 900 or not 0 <= low < high <= 100:
        raise catalog.PilotError("Quicklook dimensions or display percentiles are invalid")
    if not 100000 <= maximum_png_bytes <= 5 * 1024 * 1024:
        raise catalog.PilotError("Quicklook byte limit exceeds the Phase 3 guard")

    outputs = phase_config.get("outputs")
    expected_outputs = {
        "candidate_geojson": DEFAULT_CANDIDATES,
        "status_json": DEFAULT_STATUS,
        "parameter_comparison_png": DEFAULT_QUICKLOOK,
    }
    if not isinstance(outputs, dict):
        raise catalog.PilotError("Phase 3 configuration has no outputs object")
    for key, expected in expected_outputs.items():
        _repo_path(outputs.get(key), expected)

    guardrails = phase_config.get("guardrails")
    if not isinstance(guardrails, dict):
        raise catalog.PilotError("Phase 3 configuration has no guardrails object")
    required_true = (
        "manual_workflow_only",
        "catalogue_must_return_one_scene",
        "temporary_raster_only",
        "delete_raster_after_processing",
        "parameters_are_experimental",
        "visual_review_required",
        "candidate_is_not_vessel_classification",
        "infrastructure_context_must_not_delete_candidates",
    )
    if any(guardrails.get(key) is not True for key in required_true):
        raise catalog.PilotError("Mandatory Phase 3 guardrails are missing")
    required_false = (
        "persist_raster",
        "upload_raster_artifact",
        "download_ais",
        "overlay_current_ais",
        "claim_dark_vessel",
        "publish_public_layer",
        "modify_gfw_products",
        "modify_magic_paws",
    )
    if any(guardrails.get(key) is not False for key in required_false):
        raise catalog.PilotError("Phase 3 configuration enables a forbidden operation")
    if guardrails.get("confirmation_phrase") != "RUN_CFAR_ONE_SCENE":
        raise catalog.PilotError("Phase 3 confirmation phrase differs from the reviewed guard")

    validate_land_mask_definition(catalog.read_json(DEFAULT_LAND_MASK), pilot_config)


def resolve_related_config(phase_config: dict[str, Any], key: str, override: Path | None) -> Path:
    if override is not None:
        return override
    return _repo_path(phase_config.get(key))


def _geometry_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geometry_type = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, list):
        return [coordinates]
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        return [polygon for polygon in coordinates if isinstance(polygon, list)]
    raise catalog.PilotError("Pilot land mask contains an unsupported geometry type")


def validate_land_mask_definition(payload: dict[str, Any], pilot_config: dict[str, Any]) -> None:
    if payload.get("type") != "FeatureCollection" or payload.get("schema_version") != "1.0.0":
        raise catalog.PilotError("Pilot land mask is not the expected GeoJSON FeatureCollection")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise catalog.PilotError("Pilot land mask has no provenance metadata")
    expected_metadata = {
        "pilot_id": pilot_config.get("pilot_id"),
        "source": "Natural Earth 1:10m land polygons",
        "source_version": "5.1.1",
        "source_archive_sha256": "e547d749445eaa0964aba76738090ec88f5e63c4585122170f98c67a7ea922dc",
        "license": "Natural Earth public domain",
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise catalog.PilotError(f"Pilot land-mask provenance field {key} changed unexpectedly")
    if metadata.get("source_url") != (
        "https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-land/"
    ):
        raise catalog.PilotError("Pilot land-mask source URL changed unexpectedly")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise catalog.PilotError("Pilot land mask contains no land features")
    west, south, east, north = (float(value) for value in pilot_config["aoi"]["bbox_wgs84"])
    ring_count = 0
    for feature in features:
        if not isinstance(feature, dict):
            raise catalog.PilotError("Pilot land mask contains a non-object feature")
        properties = feature.get("properties")
        if not isinstance(properties, dict) or properties.get("mask_class") != "LAND":
            raise catalog.PilotError("Pilot land-mask feature is not classified as LAND")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            raise catalog.PilotError("Pilot land-mask feature has no geometry")
        for polygon in _geometry_rings(geometry):
            if not polygon:
                raise catalog.PilotError("Pilot land-mask polygon has no rings")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise catalog.PilotError("Pilot land-mask ring is invalid or not closed")
                ring_count += 1
                for coordinate in ring:
                    if not isinstance(coordinate, list) or len(coordinate) < 2:
                        raise catalog.PilotError("Pilot land-mask coordinate is invalid")
                    longitude = _number(coordinate[0], "land-mask longitude")
                    latitude = _number(coordinate[1], "land-mask latitude")
                    if not west <= longitude <= east or not south <= latitude <= north:
                        raise catalog.PilotError("Pilot land-mask coordinate escapes the fixed AOI")
    if ring_count == 0:
        raise catalog.PilotError("Pilot land mask contains no usable rings")


def apply_land_mask(
    data_valid_mask: Any,
    pilot_config: dict[str, Any],
    phase_config: dict[str, Any],
    land_mask_payload: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    try:
        import numpy as np
        from PIL import Image, ImageDraw
        from scipy import ndimage
    except ImportError as exc:
        raise catalog.PilotError("Phase 3 requires numpy, Pillow and scipy for land masking") from exc

    validate_land_mask_definition(land_mask_payload, pilot_config)
    source_valid = np.asarray(data_valid_mask, dtype=bool)
    if source_valid.ndim != 2:
        raise catalog.PilotError("Land mask requires a two-dimensional source-valid mask")
    height, width = source_valid.shape
    west, south, east, north = (float(value) for value in pilot_config["aoi"]["bbox_wgs84"])

    image = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(image)

    def pixel_ring(ring: list[list[float]]) -> list[tuple[int, int]]:
        points: list[tuple[int, int]] = []
        for longitude, latitude, *_ in ring:
            x = round((float(longitude) - west) * width / (east - west))
            y = round((north - float(latitude)) * height / (north - south))
            points.append((int(x), int(y)))
        return points

    for feature in land_mask_payload["features"]:
        for polygon in _geometry_rings(feature["geometry"]):
            draw.polygon(pixel_ring(polygon[0]), fill=1)
            for hole in polygon[1:]:
                draw.polygon(pixel_ring(hole), fill=0)
    land = np.asarray(image, dtype=bool)
    if not bool(land.any()):
        raise catalog.PilotError("Required pilot land mask rasterized to zero pixels")
    buffer_radius = int(phase_config["spatial_context"]["land_buffer_radius_px"])
    distance_from_land = ndimage.distance_transform_edt(~land)
    buffered_land = distance_from_land <= float(buffer_radius)
    processing_valid = source_valid & ~buffered_land
    if not bool(processing_valid.any()):
        raise catalog.PilotError("Land mask and buffer removed every valid processing pixel")
    source_valid_count = int(source_valid.sum())
    processing_valid_count = int(processing_valid.sum())
    metadata = land_mask_payload["metadata"]
    return processing_valid, {
        "applied": True,
        "required": True,
        "source": metadata["source"],
        "source_version": metadata["source_version"],
        "source_url": metadata["source_url"],
        "source_archive_sha256": metadata["source_archive_sha256"],
        "land_mask_path": str(DEFAULT_LAND_MASK.relative_to(ROOT)),
        "land_pixels": int(land.sum()),
        "land_plus_buffer_pixels": int(buffered_land.sum()),
        "buffer_only_pixels": int((buffered_land & ~land).sum()),
        "buffer_radius_px": buffer_radius,
        "approximate_buffer_radius_m": _rounded(
            buffer_radius
            * max(
                float(pilot_config["aoi"]["approx_width_m"]) / width,
                float(pilot_config["aoi"]["approx_height_m"]) / height,
            ),
            1,
        ),
        "source_valid_pixels": source_valid_count,
        "processing_valid_pixels_after_land_mask": processing_valid_count,
        "processing_valid_fraction_after_land_mask": _rounded(
            processing_valid_count / source_valid.size, 8
        ),
        "valid_pixels_excluded_by_land_and_buffer": source_valid_count - processing_valid_count,
        "cartographic_generalization_warning": metadata[
            "cartographic_generalization_warning"
        ],
        "navigational_use": False,
    }


def _rounded(value: Any, digits: int = 6) -> float:
    return round(float(value), digits)


def _profile_public(profile: dict[str, Any]) -> dict[str, Any]:
    training_radius = int(profile["training_radius_px"])
    guard_radius = int(profile["guard_radius_px"])
    training_cells = (2 * training_radius + 1) ** 2 - (2 * guard_radius + 1) ** 2
    probability = float(profile["probability_of_false_alarm"])
    full_alpha = training_cells * (probability ** (-1.0 / training_cells) - 1.0)
    result = {
        "training_radius_px": training_radius,
        "guard_radius_px": guard_radius,
        "probability_of_false_alarm": probability,
        "full_training_cell_count": training_cells,
        "full_training_alpha": _rounded(full_alpha),
    }
    if profile.get("id"):
        result["id"] = str(profile["id"])
    return result


def ca_cfar(
    power: Any,
    valid_mask: Any,
    profile: dict[str, Any],
    minimum_training_fraction: float,
    common_border_radius: int,
) -> tuple[Any, Any, dict[str, Any]]:
    """Run one cell-averaging CFAR profile on a linear-power raster."""

    try:
        import numpy as np
        from scipy import ndimage
    except ImportError as exc:
        raise catalog.PilotError("Phase 3 requires numpy and scipy for CFAR processing") from exc

    values = np.asarray(power, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool)
    if values.ndim != 2 or valid.shape != values.shape:
        raise catalog.PilotError("CFAR power and valid-mask arrays must be matching two-dimensional arrays")
    if min(values.shape) <= 2 * common_border_radius:
        raise catalog.PilotError("Raster is too small for the configured common CFAR border")
    if not np.isfinite(values[valid]).all() or (values[valid] < 0).any():
        raise catalog.PilotError("CFAR input contains invalid linear-power samples")

    training_radius = int(profile["training_radius_px"])
    guard_radius = int(profile["guard_radius_px"])
    probability = float(profile["probability_of_false_alarm"])
    outer_size = 2 * training_radius + 1
    guard_size = 2 * guard_radius + 1
    theoretical_cells = outer_size * outer_size - guard_size * guard_size
    minimum_cells = int(math.ceil(theoretical_cells * minimum_training_fraction))

    valid_float = valid.astype(np.float32, copy=False)
    weighted = np.where(valid, values, 0.0).astype(np.float32, copy=False)
    outer_sum = ndimage.uniform_filter(
        weighted, size=outer_size, mode="constant", cval=0.0
    ) * float(outer_size * outer_size)
    guard_sum = ndimage.uniform_filter(
        weighted, size=guard_size, mode="constant", cval=0.0
    ) * float(guard_size * guard_size)
    training_sum = outer_sum - guard_sum
    del outer_sum, guard_sum

    outer_count = ndimage.uniform_filter(
        valid_float, size=outer_size, mode="constant", cval=0.0
    ) * float(outer_size * outer_size)
    guard_count = ndimage.uniform_filter(
        valid_float, size=guard_size, mode="constant", cval=0.0
    ) * float(guard_size * guard_size)
    training_count = np.rint(outer_count - guard_count).astype(np.float32, copy=False)
    del outer_count, guard_count

    eligible = valid & (values > 0) & (training_count >= minimum_cells)
    border = int(common_border_radius)
    eligible[:border, :] = False
    eligible[-border:, :] = False
    eligible[:, :border] = False
    eligible[:, -border:] = False

    noise_mean = np.zeros(values.shape, dtype=np.float32)
    np.divide(training_sum, training_count, out=noise_mean, where=training_count > 0)
    alpha = np.zeros(values.shape, dtype=np.float32)
    positive_count = training_count > 0
    alpha[positive_count] = training_count[positive_count] * np.expm1(
        -math.log(probability) / training_count[positive_count]
    )
    threshold = noise_mean * alpha
    hits = eligible & (threshold > 0) & (values > threshold)
    excess_db = np.full(values.shape, np.nan, dtype=np.float32)
    excess_db[hits] = 10.0 * np.log10(values[hits] / threshold[hits])

    eligible_training_counts = training_count[eligible]
    summary = _profile_public(profile)
    summary.update(
        {
            "minimum_training_fraction": _rounded(minimum_training_fraction),
            "minimum_training_cells": minimum_cells,
            "common_border_radius_px": border,
            "eligible_pixels": int(eligible.sum()),
            "seed_pixels": int(hits.sum()),
            "training_cells_min_observed": int(eligible_training_counts.min())
            if eligible_training_counts.size
            else None,
            "training_cells_max_observed": int(eligible_training_counts.max())
            if eligible_training_counts.size
            else None,
        }
    )
    return hits, excess_db, summary


def compare_profiles(
    vv_power: Any,
    vh_power: Any,
    valid_mask: Any,
    phase_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Any, Any, list[dict[str, Any]], dict[str, Any]]:
    algorithm = phase_config["algorithm"]
    profiles = algorithm["profiles"]
    common_border = max(int(item["training_radius_px"]) for item in profiles)
    minimum_fraction = float(algorithm["minimum_training_fraction"])
    maximum_seeds = int(algorithm["maximum_seed_pixels_per_profile"])

    masks: dict[str, Any] = {}
    excesses: dict[str, Any] = {}
    summaries: list[dict[str, Any]] = []
    for profile in profiles:
        profile_id = str(profile["id"])
        hits, excess_db, summary = ca_cfar(
            vv_power, valid_mask, profile, minimum_fraction, common_border
        )
        if int(summary["seed_pixels"]) > maximum_seeds:
            raise catalog.PilotError(
                f"CFAR noise guard rejected profile {profile_id}: "
                f"{summary['seed_pixels']} seed pixels exceeds {maximum_seeds}"
            )
        masks[profile_id] = hits
        excesses[profile_id] = excess_db
        summaries.append(summary)

    vh_profile = dict(algorithm["vh_corroboration"])
    vh_profile["id"] = "vh_corroboration_only"
    vh_hits, vh_excess, vh_summary = ca_cfar(
        vh_power, valid_mask, vh_profile, minimum_fraction, common_border
    )
    if int(vh_summary["seed_pixels"]) > maximum_seeds:
        raise catalog.PilotError(
            "CFAR noise guard rejected VH corroboration: "
            f"{vh_summary['seed_pixels']} seed pixels exceeds {maximum_seeds}"
        )
    return masks, excesses, vh_hits, vh_excess, summaries, vh_summary


def _pixel_to_wgs84(row: float, column: float, shape: tuple[int, int], bbox: list[Any]) -> tuple[float, float]:
    height, width = shape
    west, south, east, north = (float(value) for value in bbox)
    longitude = west + ((column + 0.5) / width) * (east - west)
    latitude = north - ((row + 0.5) / height) * (north - south)
    return round(longitude, 7), round(latitude, 7)


def load_infrastructure_context() -> dict[str, Any]:
    try:
        import analyze_infrastructure_proximity as infrastructure

        grid, features, counts = infrastructure.load_reference_index()
        return {
            "available": bool(features),
            "grid": grid,
            "feature_count": len(features),
            "counts_by_type": counts,
            "module": infrastructure,
            "error": None if features else "No usable local infrastructure features were loaded",
        }
    except Exception as exc:
        return {
            "available": False,
            "grid": None,
            "feature_count": 0,
            "counts_by_type": {},
            "module": None,
            "error": f"Local infrastructure reference unavailable ({type(exc).__name__})",
        }


def infrastructure_for_point(
    point: tuple[float, float],
    context: dict[str, Any],
    search_radius_nm: float,
    review_radius_nm: float,
) -> dict[str, Any]:
    result = {
        "reference_available": bool(context.get("available")),
        "found_within_search_radius": False,
        "search_radius_nm": _rounded(search_radius_nm, 3),
        "review_radius_nm": _rounded(review_radius_nm, 3),
        "near_infrastructure_reference": False,
        "nearest_type": None,
        "nearest_reference_id": None,
        "nearest_name": None,
        "nearest_distance_nm": None,
        "source": "local EMODnet reference snapshot",
    }
    if not context.get("available"):
        return result
    module = context.get("module")
    feature, distance = module.nearest_feature(context["grid"], point, search_radius_nm)
    if feature is None or not math.isfinite(distance) or distance > search_radius_nm:
        return result
    reference_id = str(feature.get("_vw_reference_id") or "")
    result.update(
        {
            "found_within_search_radius": True,
            "near_infrastructure_reference": bool(distance <= review_radius_nm),
            "nearest_type": str(feature.get("_vw_infrastructure_type") or "unknown"),
            "nearest_reference_id": reference_id or None,
            "nearest_name": module.feature_name(feature, reference_id or "unnamed reference"),
            "nearest_distance_nm": _rounded(distance, 4),
        }
    )
    return result


def group_candidates(
    vv_power: Any,
    vh_power: Any,
    profile_masks: dict[str, Any],
    profile_excesses: dict[str, Any],
    vh_hits: Any,
    vh_excess: Any,
    phase_config: dict[str, Any],
    pilot_config: dict[str, Any],
    selected_scene: dict[str, Any],
    infrastructure_context: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        import numpy as np
        from scipy import ndimage
    except ImportError as exc:
        raise catalog.PilotError("Phase 3 requires numpy and scipy for candidate grouping") from exc

    profile_ids = [str(item["id"]) for item in phase_config["algorithm"]["profiles"]]
    union = np.zeros(np.asarray(vv_power).shape, dtype=bool)
    for profile_id in profile_ids:
        union |= np.asarray(profile_masks[profile_id], dtype=bool)
    link_radius = int(phase_config["algorithm"]["candidate_link_radius_px"])
    expanded = union
    if link_radius:
        expanded = ndimage.binary_dilation(
            union,
            structure=np.ones((3, 3), dtype=bool),
            iterations=link_radius,
        )
    labels, candidate_count = ndimage.label(expanded, structure=np.ones((3, 3), dtype=np.uint8))
    maximum_candidates = int(phase_config["algorithm"]["maximum_union_candidates"])
    if candidate_count > maximum_candidates:
        raise catalog.PilotError(
            f"CFAR candidate-count guard rejected {candidate_count} groups; maximum is {maximum_candidates}"
        )

    bbox = pilot_config["aoi"]["bbox_wgs84"]
    approx_width_m = float(pilot_config["aoi"]["approx_width_m"])
    approx_height_m = float(pilot_config["aoi"]["approx_height_m"])
    height, width = union.shape
    pixel_width_m = approx_width_m / width
    pixel_height_m = approx_height_m / height
    spatial = phase_config["spatial_context"]
    search_radius = float(spatial["infrastructure_search_radius_nm"])
    review_radius = float(spatial["infrastructure_review_radius_nm"])
    acquisition_time = selected_scene.get("datetime")
    normalized_scene = selected_scene.get("normalized_scene_id")

    raw_candidates: list[dict[str, Any]] = []
    for label_index, area_slice in enumerate(ndimage.find_objects(labels), start=1):
        if area_slice is None:
            continue
        component = labels[area_slice] == label_index
        local_union = union[area_slice] & component
        local_rows, local_columns = np.nonzero(local_union)
        if local_rows.size == 0:
            continue
        row_offset = int(area_slice[0].start or 0)
        column_offset = int(area_slice[1].start or 0)
        rows = local_rows + row_offset
        columns = local_columns + column_offset

        seed_values = np.asarray(vv_power)[rows, columns]
        positive_weights = np.maximum(seed_values.astype(np.float64), 0.0)
        if float(positive_weights.sum()) > 0:
            centroid_row = float(np.average(rows, weights=positive_weights))
            centroid_column = float(np.average(columns, weights=positive_weights))
        else:
            centroid_row = float(rows.mean())
            centroid_column = float(columns.mean())
        peak_position = int(np.argmax(seed_values))
        peak_row = int(rows[peak_position])
        peak_column = int(columns[peak_position])
        longitude, latitude = _pixel_to_wgs84(
            centroid_row, centroid_column, (height, width), bbox
        )

        detected_profiles: list[str] = []
        profile_max_excess: dict[str, float] = {}
        for profile_id in profile_ids:
            local_hits = profile_masks[profile_id][area_slice] & component
            if bool(local_hits.any()):
                detected_profiles.append(profile_id)
                values = profile_excesses[profile_id][area_slice][local_hits]
                profile_max_excess[profile_id] = _rounded(float(np.nanmax(values)), 4)
        profile_count = len(detected_profiles)
        if profile_count == len(profile_ids):
            consensus = "ALL_PROFILES"
        elif profile_count > 1:
            consensus = "MULTI_PROFILE"
        else:
            consensus = "SINGLE_PROFILE"

        local_vh_hits = np.asarray(vh_hits)[area_slice] & component
        vh_corroborated = bool(local_vh_hits.any())
        vh_excess_max = None
        if vh_corroborated:
            vh_values = np.asarray(vh_excess)[area_slice][local_vh_hits]
            vh_excess_max = _rounded(float(np.nanmax(vh_values)), 4)

        vv_peak = float(np.asarray(vv_power)[peak_row, peak_column])
        vh_at_vv_peak = float(np.asarray(vh_power)[peak_row, peak_column])
        vv_peak_db = 10.0 * math.log10(vv_peak) if vv_peak > 0 else None
        vh_at_vv_peak_db = 10.0 * math.log10(vh_at_vv_peak) if vh_at_vv_peak > 0 else None
        row_min, row_max = int(rows.min()), int(rows.max())
        column_min, column_max = int(columns.min()), int(columns.max())
        span_x = (column_max - column_min + 1) * pixel_width_m
        span_y = (row_max - row_min + 1) * pixel_height_m
        infrastructure = infrastructure_for_point(
            (longitude, latitude), infrastructure_context, search_radius, review_radius
        )

        flags = ["COARSE_LAND_MASK_APPLIED", "LAND_MASK_GENERALIZATION_REVIEW_REQUIRED"]
        if profile_count == 1:
            flags.append("SINGLE_PROFILE_ONLY")
        if int(local_rows.size) == 1:
            flags.append("SINGLE_PIXEL_SIGNATURE")
        if vh_corroborated:
            flags.append("VH_CFAR_CORROBORATED")
        if infrastructure["near_infrastructure_reference"]:
            flags.append("NEAR_INFRASTRUCTURE_REFERENCE")
        if not infrastructure["reference_available"]:
            flags.append("INFRASTRUCTURE_REFERENCE_UNAVAILABLE")

        raw_candidates.append(
            {
                "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                "sort_key": (
                    max(profile_max_excess.values()) if profile_max_excess else 0.0,
                    vv_peak_db if vv_peak_db is not None else -999.0,
                    -peak_row,
                    -peak_column,
                ),
                "properties": {
                    "feature_kind": "SAR_CANDIDATE",
                    "candidate_status": CANDIDATE_STATUS,
                    "review_status": "unreviewed",
                    "analyst_disposition": None,
                    "classification": "UNCLASSIFIED_BRIGHT_RETURN",
                    "candidate_is_not_vessel_classification": True,
                    "human_visual_review_required": True,
                    "downstream_eligible": False,
                    "scene_id": normalized_scene,
                    "catalogue_item_id": selected_scene.get("catalogue_item_id"),
                    "acquisition_time": acquisition_time,
                    "platform": "Sentinel-1C",
                    "instrument_mode": "IW",
                    "product_level": "GRD Level-1",
                    "polarizations": ["VV", "VH"],
                    "detection_method": {
                        "algorithm": "CA_CFAR",
                        "implementation_version": phase_config["algorithm"][
                            "implementation_version"
                        ],
                        "primary_band": "VV",
                        "detected_by_profiles": detected_profiles,
                        "profile_count": profile_count,
                        "parameter_consensus": consensus,
                        "profile_max_threshold_excess_db": profile_max_excess,
                        "vh_role": "corroboration_only",
                        "vh_cfar_corroborated": vh_corroborated,
                        "vh_max_threshold_excess_db": vh_excess_max,
                    },
                    "signature_measurements": {
                        "vv_peak_linear_power": _rounded(vv_peak, 8),
                        "vv_peak_db": _rounded(vv_peak_db, 4) if vv_peak_db is not None else None,
                        "vh_at_vv_peak_linear_power": _rounded(vh_at_vv_peak, 8),
                        "vh_at_vv_peak_db": _rounded(vh_at_vv_peak_db, 4)
                        if vh_at_vv_peak_db is not None
                        else None,
                        "primary_seed_pixel_count": int(local_rows.size),
                        "seed_bbox_pixels": {
                            "row_min": row_min,
                            "row_max": row_max,
                            "column_min": column_min,
                            "column_max": column_max,
                        },
                        "approximate_axis_aligned_signature_span_m": {
                            "x": _rounded(span_x, 2),
                            "y": _rounded(span_y, 2),
                        },
                        "span_is_not_vessel_length": True,
                    },
                    "spatial_quality": {
                        "open_water_aoi_reviewed": True,
                        "coastline_mask_integrated": True,
                        "land_mask_source": phase_config["spatial_context"]["land_mask_source"],
                        "land_buffer_radius_px": int(
                            phase_config["spatial_context"]["land_buffer_radius_px"]
                        ),
                        "common_cfar_border_excluded": True,
                        "quality_flags": flags,
                    },
                    "infrastructure_context": infrastructure,
                    "ais_context_status": AIS_CONTEXT_STATUS,
                    "ais_match_status": "NOT_ASSESSED",
                    "current_ais_overlaid": False,
                    "dark_vessel_claim": False,
                    "assessment_limit": ASSESSMENT_LIMIT,
                    "_centroid_pixel": {
                        "row": _rounded(centroid_row, 3),
                        "column": _rounded(centroid_column, 3),
                    },
                },
            }
        )

    raw_candidates.sort(key=lambda item: item["sort_key"], reverse=True)
    acquisition_compact = str(acquisition_time or "unknown").replace("-", "").replace(":", "")
    acquisition_compact = acquisition_compact.replace(".", "").replace("+0000", "Z")
    acquisition_compact = acquisition_compact.split("Z", 1)[0] + "Z" if "T" in acquisition_compact else "UNKNOWN"
    features: list[dict[str, Any]] = []
    for index, item in enumerate(raw_candidates, start=1):
        detection_id = f"SARCAND-{acquisition_compact}-{index:04d}"
        properties = item["properties"]
        properties["detection_id"] = detection_id
        centroid_pixel = properties.pop("_centroid_pixel")
        properties["centroid_pixel"] = centroid_pixel
        features.append(
            {
                "type": "Feature",
                "id": detection_id,
                "geometry": item["geometry"],
                "properties": properties,
            }
        )
    return features


def render_parameter_comparison(
    vv_power: Any,
    valid_mask: Any,
    features: list[dict[str, Any]],
    profile_masks: dict[str, Any],
    phase_config: dict[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    try:
        import numpy as np
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise catalog.PilotError("Phase 3 requires numpy and Pillow for the comparison quicklook") from exc

    values = np.asarray(vv_power, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(values) & (values > 0)
    if not bool(valid.any()):
        raise catalog.PilotError("Quicklook cannot be rendered without positive valid VV samples")
    display_db = np.full(values.shape, np.nan, dtype=np.float32)
    display_db[valid] = 10.0 * np.log10(values[valid])
    quicklook_config = phase_config["quicklook"]
    low, high = np.percentile(
        display_db[valid],
        [
            float(quicklook_config["vv_db_percentile_low"]),
            float(quicklook_config["vv_db_percentile_high"]),
        ],
    )
    if not math.isfinite(float(low)) or not math.isfinite(float(high)) or high <= low:
        raise catalog.PilotError("VV display stretch is invalid")
    normalized = np.zeros(values.shape, dtype=np.uint8)
    scaled = np.clip((display_db[valid] - low) / (high - low), 0.0, 1.0)
    normalized[valid] = np.rint(scaled * 255.0).astype(np.uint8)
    source_image = Image.fromarray(normalized, mode="L")

    profile_ids = [str(item["id"]) for item in phase_config["algorithm"]["profiles"]]
    panels = [("VV dB + all candidate groups", None)] + [
        (f"VV CA-CFAR: {profile_id}", profile_id) for profile_id in profile_ids
    ]
    marker_colours = {
        None: (255, 64, 64),
        "strict_wide": (255, 64, 220),
        "balanced": (255, 174, 48),
        "exploratory_local": (64, 224, 255),
    }
    maximum_bytes = int(quicklook_config["maximum_png_bytes"])
    requested_width = int(quicklook_config["maximum_panel_width_px"])
    candidate_by_profile: dict[str | None, list[dict[str, Any]]] = {None: list(features)}
    for profile_id in profile_ids:
        candidate_by_profile[profile_id] = [
            feature
            for feature in features
            if profile_id
            in feature["properties"]["detection_method"]["detected_by_profiles"]
        ]

    def render(panel_width: int) -> tuple[bytes, tuple[int, int]]:
        panel_height = max(1, round(values.shape[0] * panel_width / values.shape[1]))
        label_height = 34
        global_header = 52
        canvas = Image.new(
            "RGB",
            (panel_width * 2, global_header + (panel_height + label_height) * 2),
            (16, 20, 26),
        )
        draw = ImageDraw.Draw(canvas)
        draw.text(
            (12, 8),
            "PHASE 3 - UNREVIEWED CFAR CANDIDATES - NO AIS ASSESSMENT",
            fill=(255, 225, 120),
        )
        draw.text(
            (12, 27),
            f"VV display stretch: {float(low):.2f} to {float(high):.2f} dB | markers are candidate groups",
            fill=(220, 225, 230),
        )
        resized = source_image.resize((panel_width, panel_height), Image.Resampling.BILINEAR).convert("RGB")
        for panel_index, (label, profile_id) in enumerate(panels):
            grid_x = panel_index % 2
            grid_y = panel_index // 2
            x0 = grid_x * panel_width
            y0 = global_header + grid_y * (panel_height + label_height)
            canvas.paste(resized, (x0, y0 + label_height))
            draw.text((x0 + 8, y0 + 9), label, fill=(238, 240, 244))
            colour = marker_colours[profile_id]
            rows, columns = values.shape
            for candidate_index, feature in enumerate(candidate_by_profile[profile_id], start=1):
                centroid = feature["properties"]["centroid_pixel"]
                px = x0 + int((float(centroid["column"]) + 0.5) * panel_width / columns)
                py = y0 + label_height + int((float(centroid["row"]) + 0.5) * panel_height / rows)
                radius = 5
                draw.ellipse((px - radius, py - radius, px + radius, py + radius), outline=colour, width=2)
                if len(features) <= 80:
                    draw.text((px + 6, py - 7), str(candidate_index), fill=colour)
        palette = canvas.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        destination = io.BytesIO()
        palette.save(destination, format="PNG", optimize=True)
        return destination.getvalue(), canvas.size

    attempted_widths = []
    for panel_width in dict.fromkeys((requested_width, max(360, requested_width * 3 // 4), 360)):
        attempted_widths.append(panel_width)
        png_bytes, dimensions = render(panel_width)
        if len(png_bytes) <= maximum_bytes:
            profile_seed_counts = {
                profile_id: int(np.asarray(profile_masks[profile_id], dtype=bool).sum())
                for profile_id in profile_ids
            }
            return png_bytes, {
                "format": "PNG",
                "width_px": int(dimensions[0]),
                "height_px": int(dimensions[1]),
                "bytes": len(png_bytes),
                "sha256": hashlib.sha256(png_bytes).hexdigest(),
                "vv_display_stretch_db": {
                    "low": _rounded(low, 4),
                    "high": _rounded(high, 4),
                },
                "profile_seed_counts": profile_seed_counts,
                "candidate_group_count": len(features),
                "contains_source_raster": False,
                "derived_review_visual_only": True,
                "attempted_panel_widths_px": attempted_widths,
            }
    raise catalog.PilotError("Derived parameter-comparison PNG exceeded its configured byte limit")


def _load_normalized_raster(path: Path, chip_config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    try:
        import numpy as np
        import tifffile
    except ImportError as exc:
        raise catalog.PilotError("Phase 3 requires numpy and tifffile for temporary raster processing") from exc
    validation = chip.analyze_tiff(path, chip_config)
    try:
        array = tifffile.imread(path)
    except Exception as exc:
        raise catalog.PilotError(f"Temporary raster could not be loaded ({type(exc).__name__})") from exc
    expected_height = int(chip_config["request"]["height_px"])
    expected_width = int(chip_config["request"]["width_px"])
    if array.shape == (3, expected_height, expected_width):
        array = np.moveaxis(array, 0, -1)
    if array.shape != (expected_height, expected_width, 3):
        raise catalog.PilotError("Temporary raster shape changed after validation")
    return np.asarray(array, dtype=np.float32), validation


def _scene_selection(search_payload: dict[str, Any], pilot_config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    scene, returned_features = catalog.select_scene(
        search_payload,
        str(pilot_config["expected_sentinel_hub_product_id"]),
        "Sentinel Hub authenticated Phase 3 single-scene catalogue search",
    )
    if returned_features != 1:
        raise catalog.PilotError(
            f"Single-scene guard rejected {returned_features} catalogue features; expected exactly one"
        )
    expected_normalized = catalog.normalize_scene_id(pilot_config["expected_sentinel_hub_product_id"])
    matched_identifier = next(
        (
            value
            for value in catalog.scene_identifier_values(scene)
            if catalog.normalize_scene_id(value) == expected_normalized
        ),
        None,
    )
    if matched_identifier is None:
        raise catalog.PilotError("Selected Phase 3 catalogue item lost its exact scene identifier")
    selected = {
        "catalogue_item_id": scene.get("id"),
        "matched_scene_identifier": matched_identifier,
        "normalized_scene_id": expected_normalized,
        "datetime": (scene.get("properties") or {}).get("datetime"),
    }
    confirmation = {
        "endpoint": catalog.SENTINEL_HUB_CATALOG_URL,
        "authenticated": True,
        "returned_features": returned_features,
        "exact_scene_found_once": True,
        "single_scene_guard_passed": True,
    }
    return selected, confirmation


def base_status(
    phase_config: dict[str, Any],
    pilot_config: dict[str, Any],
    chip_config: dict[str, Any],
) -> dict[str, Any]:
    algorithm = phase_config.get("algorithm") if isinstance(phase_config.get("algorithm"), dict) else {}
    profiles = algorithm.get("profiles") if isinstance(algorithm.get("profiles"), list) else []
    return {
        "schema_version": "1.0.0",
        "generated_at": catalog.iso_z(catalog.utc_now()),
        "status": "error",
        "phase": PHASE,
        "provider": "copernicus_data_space_ecosystem",
        "pilot_id": pilot_config.get("pilot_id"),
        "pilot_name": pilot_config.get("name"),
        "expected_scene_id": pilot_config.get("expected_cdse_item_id"),
        "aoi": pilot_config.get("aoi"),
        "configured_time_range": pilot_config.get("time_range"),
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
        "process_api_request": {
            "endpoint": catalog.PROCESS_API_URL,
            "width_px": (chip_config.get("request") or {}).get("width_px"),
            "height_px": (chip_config.get("request") or {}).get("height_px"),
            "bands": (chip_config.get("request") or {}).get("bands"),
            "estimated_processing_units": chip.processing_unit_estimate(chip_config).get(
                "estimated_processing_units"
            )
            if chip_config
            else None,
        },
        "process_api_reported_processing_units": None,
        "raster_download_performed": False,
        "raster_response_bytes": None,
        "raster_sha256": None,
        "temporary_raster_written": False,
        "temporary_raster_deleted": False,
        "raster_persisted": False,
        "raster_artifact_uploaded": False,
        "raster_validation": None,
        "candidate_extraction_performed": False,
        "detection_performed": False,
        "algorithm": {
            "name": algorithm.get("name"),
            "implementation_version": algorithm.get("implementation_version"),
            "primary_band": algorithm.get("primary_band"),
            "secondary_band": algorithm.get("secondary_band"),
            "secondary_band_role": "corroboration_only",
            "parameters_are_experimental": True,
            "profiles": [_profile_public(item) for item in profiles if isinstance(item, dict)],
            "method_note": METHOD_NOTE,
            "reference_reading": [
                "https://www.mdpi.com/2072-4292/9/3/246",
                "https://www.mdpi.com/2072-4292/11/9/1078",
                "https://www.mdpi.com/2072-4292/13/10/1995",
            ],
        },
        "profile_results": [],
        "vh_corroboration_result": None,
        "candidate_counts": {
            "total_unreviewed_candidate_groups": 0,
            "all_profiles": 0,
            "multi_profile": 0,
            "single_profile": 0,
            "vh_corroborated": 0,
            "near_infrastructure_reference": 0,
        },
        "spatial_context": {
            "open_water_aoi_reviewed": True,
            "coastline_mask_integrated": True,
            "land_mask_required": True,
            "land_mask": None,
            "land_mask_generalization_limitation_disclosed": True,
            "common_cfar_border_excluded": True,
            "infrastructure_reference_available": False,
            "infrastructure_reference_feature_count": 0,
            "infrastructure_reference_counts_by_type": {},
            "infrastructure_reference_error": None,
            "infrastructure_context_deleted_candidates": False,
        },
        "outputs": {
            "candidate_geojson": None,
            "parameter_comparison_png": None,
        },
        "ais_download_performed": False,
        "ais_context_status": AIS_CONTEXT_STATUS,
        "current_ais_overlaid": False,
        "dark_vessel_claim": False,
        "public_layer_modified": False,
        "gfw_products_modified": False,
        "magic_paws_modified": False,
        "visual_review_required": True,
        "production_ready": False,
        "assessment_limit": ASSESSMENT_LIMIT,
        "next_phase": "historical_ais_context_after_visual_cfar_review",
        "errors": [],
    }


def _geojson_payload(
    status: dict[str, Any],
    phase_config: dict[str, Any],
    features: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "Voodoo Whiskers Phase 3 unreviewed SAR candidates",
        "schema_version": "1.0.0",
        "generated_at": status["generated_at"],
        "metadata": {
            "phase": PHASE,
            "pilot_id": status["pilot_id"],
            "scene": status["selected_scene"],
            "aoi": status["aoi"],
            "coordinate_reference_system": "OGC CRS84 / WGS84 longitude-latitude",
            "candidate_status": CANDIDATE_STATUS,
            "candidate_count": len(features),
            "parameters_are_experimental": True,
            "visual_review_required": True,
            "coastline_mask_integrated": True,
            "land_mask": status["spatial_context"]["land_mask"],
            "ais_context_status": AIS_CONTEXT_STATUS,
            "current_ais_overlaid": False,
            "dark_vessel_claim": False,
            "public_layer": False,
            "method_note": METHOD_NOTE,
            "assessment_limit": ASSESSMENT_LIMIT,
            "profile_parameters": [
                _profile_public(item) for item in phase_config["algorithm"]["profiles"]
            ],
        },
        "features": features,
    }


def run_cfar_pilot(
    phase_config: dict[str, Any],
    pilot_config: dict[str, Any],
    chip_config: dict[str, Any],
    client_id: str,
    client_secret: str,
    *,
    session: Any | None = None,
    infrastructure_context: dict[str, Any] | None = None,
    land_mask_payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, bytes | None, bool]:
    status = base_status(phase_config, pilot_config, chip_config)
    temporary_path: Path | None = None
    try:
        validate_phase_config(phase_config, pilot_config, chip_config)
        http = session or chip.BinarySession()
        if hasattr(http, "headers"):
            http.headers.update({"User-Agent": "MOwlSINT-VoodooWhiskers-Copernicus-SAR-CFAR/0.1"})
        access_token, oauth_metadata = catalog.request_access_token(http, client_id, client_secret)
        status["oauth"] = oauth_metadata
        search_payload = catalog.safe_post(
            http,
            catalog.SENTINEL_HUB_CATALOG_URL,
            "Sentinel Hub authenticated Phase 3 single-scene catalogue search",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=chip.build_catalog_search(pilot_config),
        )
        selected_scene, confirmation = _scene_selection(search_payload, pilot_config)
        status["selected_scene"] = selected_scene
        status["catalogue_confirmation"] = confirmation

        raster_bytes, response_headers = chip.request_process_chip(
            http,
            access_token,
            chip.build_process_request(chip_config, pilot_config),
            int(chip_config["request"]["maximum_response_bytes"]),
        )
        status["raster_download_performed"] = True
        status["raster_response_bytes"] = len(raster_bytes)
        status["raster_sha256"] = hashlib.sha256(raster_bytes).hexdigest()
        reported_units = response_headers.get("x-processingunits-spent") or response_headers.get(
            "x-processing-units-spent"
        )
        if reported_units is not None:
            try:
                status["process_api_reported_processing_units"] = float(reported_units)
            except ValueError:
                status["process_api_reported_processing_units"] = None

        with tempfile.TemporaryDirectory(prefix="voodoo-sar-cfar-") as temporary_directory:
            temporary_path = Path(temporary_directory) / "sentinel1_phase3_temporary.tiff"
            temporary_path.write_bytes(raster_bytes)
            status["temporary_raster_written"] = True
            del raster_bytes
            array, validation = _load_normalized_raster(temporary_path, chip_config)
            status["raster_validation"] = validation
            vv_power = array[:, :, 0]
            vh_power = array[:, :, 1]
            data_valid_mask = array[:, :, 2] >= 0.5
            land_definition = (
                land_mask_payload
                if land_mask_payload is not None
                else catalog.read_json(DEFAULT_LAND_MASK)
            )
            valid_mask, land_mask_status = apply_land_mask(
                data_valid_mask, pilot_config, phase_config, land_definition
            )
            status["spatial_context"]["land_mask"] = land_mask_status
            (
                profile_masks,
                profile_excesses,
                vh_hits,
                vh_excess,
                profile_summaries,
                vh_summary,
            ) = compare_profiles(vv_power, vh_power, valid_mask, phase_config)
            context = infrastructure_context if infrastructure_context is not None else load_infrastructure_context()
            features = group_candidates(
                vv_power,
                vh_power,
                profile_masks,
                profile_excesses,
                vh_hits,
                vh_excess,
                phase_config,
                pilot_config,
                selected_scene,
                context,
            )
            png_bytes, quicklook_metadata = render_parameter_comparison(
                vv_power, valid_mask, features, profile_masks, phase_config
            )
            status["profile_results"] = profile_summaries
            status["vh_corroboration_result"] = vh_summary
            status["candidate_extraction_performed"] = True
            status["detection_performed"] = True
            status["spatial_context"].update(
                {
                    "infrastructure_reference_available": bool(context.get("available")),
                    "infrastructure_reference_feature_count": int(context.get("feature_count") or 0),
                    "infrastructure_reference_counts_by_type": context.get("counts_by_type") or {},
                    "infrastructure_reference_error": context.get("error"),
                }
            )

        status["temporary_raster_deleted"] = temporary_path is not None and not temporary_path.exists()
        if not status["temporary_raster_deleted"]:
            raise catalog.PilotError("Temporary Phase 3 raster cleanup could not be verified")

        counts = status["candidate_counts"]
        counts["total_unreviewed_candidate_groups"] = len(features)
        for feature in features:
            method = feature["properties"]["detection_method"]
            consensus = method["parameter_consensus"]
            if consensus == "ALL_PROFILES":
                counts["all_profiles"] += 1
            elif consensus == "MULTI_PROFILE":
                counts["multi_profile"] += 1
            else:
                counts["single_profile"] += 1
            if method["vh_cfar_corroborated"]:
                counts["vh_corroborated"] += 1
            if feature["properties"]["infrastructure_context"]["near_infrastructure_reference"]:
                counts["near_infrastructure_reference"] += 1

        geojson = _geojson_payload(status, phase_config, features)
        geojson_bytes = (json.dumps(geojson, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        status["outputs"] = {
            "candidate_geojson": {
                "path": str(DEFAULT_CANDIDATES.relative_to(ROOT)),
                "bytes": len(geojson_bytes),
                "sha256": hashlib.sha256(geojson_bytes).hexdigest(),
                "feature_count": len(features),
                "public_layer": False,
            },
            "parameter_comparison_png": {
                "path": str(DEFAULT_QUICKLOOK.relative_to(ROOT)),
                **quicklook_metadata,
            },
        }
        status["status"] = "ok"
        return status, geojson, png_bytes, True
    except catalog.PilotError as exc:
        if temporary_path is not None and not temporary_path.exists():
            status["temporary_raster_deleted"] = True
        status["errors"] = [str(exc)]
        return status, None, None, False
    except Exception as exc:
        if temporary_path is not None and not temporary_path.exists():
            status["temporary_raster_deleted"] = True
        status["errors"] = [f"Unexpected Phase 3 processing failure ({type(exc).__name__})"]
        return status, None, None, False


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temp_name = handle.name
    Path(temp_name).replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pilot-config", type=Path)
    parser.add_argument("--chip-config", type=Path)
    parser.add_argument("--status-output", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--candidate-output", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--quicklook-output", type=Path, default=DEFAULT_QUICKLOOK)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    phase_config: dict[str, Any] = {"phase": PHASE}
    pilot_config: dict[str, Any] = {}
    chip_config: dict[str, Any] = {}
    try:
        phase_config = catalog.read_json(args.config)
        pilot_config = catalog.read_json(
            resolve_related_config(phase_config, "pilot_config", args.pilot_config)
        )
        chip_config = catalog.read_json(
            resolve_related_config(phase_config, "chip_config", args.chip_config)
        )
        _repo_path(str(args.status_output.resolve().relative_to(ROOT.resolve())), DEFAULT_STATUS)
        _repo_path(str(args.candidate_output.resolve().relative_to(ROOT.resolve())), DEFAULT_CANDIDATES)
        _repo_path(str(args.quicklook_output.resolve().relative_to(ROOT.resolve())), DEFAULT_QUICKLOOK)
    except (catalog.PilotError, ValueError) as exc:
        status = base_status(phase_config, pilot_config, chip_config)
        status["errors"] = [str(exc)]
        catalog.atomic_json(args.status_output, status)
        print(json.dumps({"status": "error", "phase": PHASE, "errors": status["errors"]}))
        return 1

    status, geojson, png_bytes, ok = run_cfar_pilot(
        phase_config,
        pilot_config,
        chip_config,
        os.environ.get("CDSE_SH_CLIENT_ID", ""),
        os.environ.get("CDSE_SH_CLIENT_SECRET", ""),
    )
    if ok and geojson is not None and png_bytes is not None:
        catalog.atomic_json(args.candidate_output, geojson)
        atomic_bytes(args.quicklook_output, png_bytes)
    catalog.atomic_json(args.status_output, status)
    print(
        json.dumps(
            {
                "status": status["status"],
                "phase": status["phase"],
                "pilot_id": status["pilot_id"],
                "scene_id": (status.get("selected_scene") or {}).get("matched_scene_identifier"),
                "temporary_raster_deleted": status["temporary_raster_deleted"],
                "candidate_count": status["candidate_counts"][
                    "total_unreviewed_candidate_groups"
                ],
                "ais_context_status": status["ais_context_status"],
                "visual_review_required": status["visual_review_required"],
                "errors": status["errors"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
