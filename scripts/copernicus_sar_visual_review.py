#!/usr/bin/env python3
"""Render native-resolution evidence for one manual SAR visual review.

Phase 5.1 re-downloads the already accepted single Sentinel-1 scene into an
operating-system temporary directory. It creates clean VV and VH crops plus a
separate VV overlay using only the historical AIS projection already recorded
by Phase 4/5. The source TIFF is deleted before the small PNG/JSON evidence
products are written. This phase deliberately records no analyst decision.
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
PHASE = "single_scene_visual_confirmation_evidence"

DEFAULT_CONFIG = ROOT / "config/sar_copernicus_visual_review.json"
DEFAULT_CONTEXT_INPUT = ROOT / "data/sar_copernicus_ais_context_latest.geojson"
DEFAULT_QUEUE_INPUT = ROOT / "data/sar_copernicus_review_queue_latest.json"
DEFAULT_OBJECTS_INPUT = ROOT / "data/sar_copernicus_review_objects_latest.geojson"
DEFAULT_SHEET_OUTPUT = ROOT / "data/sar_copernicus_visual_review_sheet_latest.png"
DEFAULT_MANIFEST_OUTPUT = ROOT / "data/sar_copernicus_visual_review_manifest_latest.json"
DEFAULT_STATUS_OUTPUT = ROOT / "data/sar_copernicus_visual_review_status_latest.json"

PANEL_ORDER = [
    "VV_CLEAN_NATIVE_1_TO_1",
    "VH_CLEAN_NATIVE_1_TO_1",
    "VV_HISTORICAL_AIS_OVERLAY_NATIVE_1_TO_1",
]

ASSESSMENT_LIMIT = (
    "Phase 5.1 supplies native-resolution visual-review evidence only. Historical "
    "AIS names and positions are association hypotheses at the fixed SAR acquisition "
    "time, not SAR-derived vessel identities. No current AIS is read, no analyst "
    "decision is recorded, and no object becomes downstream eligible in this phase."
)


def _repo_path(value: Any, expected: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise catalog.PilotError(f"Expected fixed repository path {expected.relative_to(ROOT)}")
    candidate = (ROOT / value).resolve()
    if candidate != expected.resolve():
        raise catalog.PilotError(
            f"Phase 5.1 path must remain fixed at {expected.relative_to(ROOT)}"
        )
    return candidate


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise catalog.PilotError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise catalog.PilotError(f"{label} must be finite")
    return result


def _integer(value: Any, label: str) -> int:
    number = _number(value, label)
    result = int(number)
    if result != number:
        raise catalog.PilotError(f"{label} must be an integer")
    return result


def _rgb(value: Any, label: str) -> tuple[int, int, int]:
    if not isinstance(value, list) or len(value) != 3:
        raise catalog.PilotError(f"{label} must contain three RGB integers")
    result = tuple(_integer(item, label) for item in value)
    if any(item < 0 or item > 255 for item in result):
        raise catalog.PilotError(f"{label} values must be between 0 and 255")
    return result  # type: ignore[return-value]


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _read_json_bytes(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        source_bytes = path.read_bytes()
        payload = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise catalog.PilotError(f"{label} could not be read as UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise catalog.PilotError(f"{label} must contain a JSON object")
    return payload, source_bytes


def validate_config(
    config: dict[str, Any],
    pilot_config: dict[str, Any],
    chip_config: dict[str, Any],
) -> None:
    catalog.validate_config(pilot_config)
    chip.validate_phase_config(chip_config, pilot_config)
    if config.get("schema_version") != "1.0.0":
        raise catalog.PilotError("Unsupported Phase 5.1 configuration schema")
    if config.get("phase") != PHASE:
        raise catalog.PilotError("Unexpected Phase 5.1 configuration phase")
    if config.get("pilot_config") != "config/sar_copernicus_pilot.json":
        raise catalog.PilotError("Phase 5.1 must reference the fixed pilot configuration")
    if config.get("chip_config") != "config/sar_copernicus_chip.json":
        raise catalog.PilotError("Phase 5.1 must reference the fixed chip configuration")

    for key, expected in (
        ("phase4_context_input", DEFAULT_CONTEXT_INPUT),
        ("phase5_queue_input", DEFAULT_QUEUE_INPUT),
        ("phase5_objects_input", DEFAULT_OBJECTS_INPUT),
    ):
        _repo_path(config.get(key), expected)
    outputs = config.get("outputs")
    if not isinstance(outputs, dict):
        raise catalog.PilotError("Phase 5.1 configuration has no outputs object")
    for key, expected in (
        ("review_sheet_png", DEFAULT_SHEET_OUTPUT),
        ("review_manifest_json", DEFAULT_MANIFEST_OUTPUT),
        ("status_json", DEFAULT_STATUS_OUTPUT),
    ):
        _repo_path(outputs.get(key), expected)

    receipt = config.get("accepted_input_receipt")
    if not isinstance(receipt, dict):
        raise catalog.PilotError("Phase 5.1 configuration has no accepted input receipt")
    for key in (
        "phase4_context_sha256",
        "phase5_queue_sha256",
        "phase5_objects_sha256",
    ):
        if not _valid_sha256(receipt.get(key)):
            raise catalog.PilotError(f"Phase 5.1 input receipt {key} is not a SHA-256 value")
    source_count = _integer(receipt.get("source_candidate_count"), "source candidate count")
    object_count = _integer(receipt.get("review_object_count"), "review object count")
    if not 1 <= source_count <= 1000 or not 1 <= object_count <= source_count:
        raise catalog.PilotError("Phase 5.1 accepted input counts are outside the pilot guard")

    expected_scene = catalog.normalize_scene_id(
        pilot_config.get("expected_sentinel_hub_product_id")
    )
    scene = config.get("scene")
    if not isinstance(scene, dict) or scene.get("normalized_scene_id") != expected_scene:
        raise catalog.PilotError("Phase 5.1 scene differs from the fixed pilot scene")
    acquisition = catalog.parse_utc(
        scene.get("acquisition_time_utc"), "scene.acquisition_time_utc"
    )
    configured_start = catalog.parse_utc(
        (pilot_config.get("time_range") or {}).get("from"), "time_range.from"
    )
    configured_end = catalog.parse_utc(
        (pilot_config.get("time_range") or {}).get("to"), "time_range.to"
    )
    if not configured_start <= acquisition <= configured_end:
        raise catalog.PilotError("Phase 5.1 acquisition time is outside the pilot time range")

    rendering = config.get("rendering")
    if not isinstance(rendering, dict):
        raise catalog.PilotError("Phase 5.1 configuration has no rendering object")
    crop_size = _integer(rendering.get("crop_size_px"), "rendering.crop_size_px")
    margin = _integer(
        rendering.get("minimum_content_margin_px"),
        "rendering.minimum_content_margin_px",
    )
    width = int(chip_config["request"]["width_px"])
    height = int(chip_config["request"]["height_px"])
    if not 64 <= crop_size <= 512 or crop_size > min(width, height):
        raise catalog.PilotError("Phase 5.1 crop size is outside the native-review guard")
    if not 4 <= margin < crop_size // 3:
        raise catalog.PilotError("Phase 5.1 content margin is outside the review guard")
    if rendering.get("panel_order") != PANEL_ORDER:
        raise catalog.PilotError("Phase 5.1 must retain clean VV/VH and separate overlay panels")
    if _number(
        rendering.get("source_pixels_per_output_pixel"),
        "rendering.source_pixels_per_output_pixel",
    ) != 1.0:
        raise catalog.PilotError("Phase 5.1 clean panels must retain native 1:1 pixels")
    if rendering.get("resampling") != "NONE":
        raise catalog.PilotError("Phase 5.1 clean panels must not be resampled")
    percentiles = rendering.get("display_percentiles")
    if not isinstance(percentiles, dict):
        raise catalog.PilotError("Phase 5.1 display percentiles are missing")
    for key in ("vv_db", "vh_db"):
        values = percentiles.get(key)
        if not isinstance(values, list) or len(values) != 2:
            raise catalog.PilotError(f"Phase 5.1 {key} stretch must contain two percentiles")
        low = _number(values[0], f"{key} low percentile")
        high = _number(values[1], f"{key} high percentile")
        if not 0 <= low < high <= 100 or high < 95:
            raise catalog.PilotError(f"Phase 5.1 {key} display stretch is unsafe")
    for key in (
        "candidate_marker_rgb",
        "secondary_candidate_marker_rgb",
        "historical_ais_marker_rgb",
    ):
        _rgb(rendering.get(key), f"rendering.{key}")
    if not 50 <= _number(rendering.get("course_arrow_length_m"), "course arrow") <= 2000:
        raise catalog.PilotError("Phase 5.1 course arrow is outside the review guard")
    if not 25 <= _number(rendering.get("scale_bar_length_m"), "scale bar") <= 1000:
        raise catalog.PilotError("Phase 5.1 scale bar is outside the review guard")
    maximum_png_bytes = _integer(
        rendering.get("maximum_png_bytes"), "rendering.maximum_png_bytes"
    )
    if not 1024 * 1024 <= maximum_png_bytes <= 10 * 1024 * 1024:
        raise catalog.PilotError("Phase 5.1 PNG byte guard is outside the safe range")

    guardrails = config.get("guardrails")
    if not isinstance(guardrails, dict):
        raise catalog.PilotError("Phase 5.1 configuration has no guardrails object")
    required_true = (
        "manual_workflow_only",
        "catalogue_must_return_one_scene",
        "temporary_raster_only",
        "delete_raster_after_rendering",
        "clean_unmarked_panels_required",
        "overlay_must_be_separate_panel",
        "native_pixel_scale_required",
        "historical_ais_only",
        "current_ais_files_must_not_be_read",
    )
    required_false = (
        "persist_raster",
        "upload_raster_artifact",
        "current_ais_overlaid",
        "analyst_decision_recorded",
        "visual_confirmation_completed_automatically",
        "automatic_final_disposition",
        "downstream_eligible",
        "publish_public_layer",
        "modify_phase4_outputs",
        "modify_phase5_outputs",
        "modify_existing_ais_products",
        "modify_gfw_products",
        "modify_magic_paws",
        "change_hybrid_index",
        "claim_dark_vessel",
        "classify_sar_candidate_as_vessel",
    )
    if any(guardrails.get(key) is not True for key in required_true):
        raise catalog.PilotError("Mandatory Phase 5.1 rendering guardrails are missing")
    if any(guardrails.get(key) is not False for key in required_false):
        raise catalog.PilotError("Phase 5.1 enables a forbidden decision or publication operation")
    if guardrails.get("confirmation_phrase") != "RENDER_SAR_VISUAL_REVIEW_ONE_SCENE":
        raise catalog.PilotError("Phase 5.1 confirmation phrase differs from the reviewed guard")


def _hash_verified(source_bytes: bytes, expected: str, label: str) -> str:
    actual = hashlib.sha256(source_bytes).hexdigest()
    if actual != expected:
        raise catalog.PilotError(f"Accepted {label} SHA-256 receipt does not match")
    return actual


def validate_inputs(
    config: dict[str, Any],
    context: dict[str, Any],
    queue: dict[str, Any],
    objects: dict[str, Any],
    *,
    context_bytes: bytes,
    queue_bytes: bytes,
    objects_bytes: bytes,
    raster_shape: tuple[int, int] | None = None,
) -> dict[str, Any]:
    receipt = config["accepted_input_receipt"]
    hashes = {
        "phase4_context_sha256": _hash_verified(
            context_bytes, receipt["phase4_context_sha256"], "Phase 4 context"
        ),
        "phase5_queue_sha256": _hash_verified(
            queue_bytes, receipt["phase5_queue_sha256"], "Phase 5 queue"
        ),
        "phase5_objects_sha256": _hash_verified(
            objects_bytes, receipt["phase5_objects_sha256"], "Phase 5 review objects"
        ),
    }
    expected_scene = config["scene"]["normalized_scene_id"]
    expected_candidate_count = int(receipt["source_candidate_count"])
    expected_object_count = int(receipt["review_object_count"])

    if context.get("type") != "FeatureCollection" or not isinstance(
        context.get("features"), list
    ):
        raise catalog.PilotError("Phase 4 context is not a GeoJSON FeatureCollection")
    context_metadata = context.get("metadata")
    if not isinstance(context_metadata, dict):
        raise catalog.PilotError("Phase 4 context metadata is missing")
    if (context_metadata.get("scene") or {}).get("normalized_scene_id") != expected_scene:
        raise catalog.PilotError("Phase 4 context scene differs from Phase 5.1")
    if context_metadata.get("source_candidate_count") != expected_candidate_count:
        raise catalog.PilotError("Phase 4 context candidate count differs from the receipt")
    if context_metadata.get("current_ais_used") is not False:
        raise catalog.PilotError("Phase 4 context unexpectedly uses current AIS")
    if context_metadata.get("current_ais_overlaid") is not False:
        raise catalog.PilotError("Phase 4 context unexpectedly overlays current AIS")
    if context_metadata.get("dark_vessel_claim") is not False:
        raise catalog.PilotError("Phase 4 context contains an unsafe dark-vessel claim")

    candidate_features: dict[str, dict[str, Any]] = {}
    for feature in context["features"]:
        if not isinstance(feature, dict):
            raise catalog.PilotError("Phase 4 context contains a non-object feature")
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise catalog.PilotError("Phase 4 context candidate has no properties")
        identifier = properties.get("detection_id")
        if not isinstance(identifier, str) or not identifier or identifier in candidate_features:
            raise catalog.PilotError("Phase 4 candidate ID is missing or duplicated")
        if feature.get("id") != identifier:
            raise catalog.PilotError(f"Phase 4 candidate {identifier} has an inconsistent ID")
        if properties.get("scene_id") != expected_scene:
            raise catalog.PilotError(f"Phase 4 candidate {identifier} has the wrong scene")
        if properties.get("current_ais_overlaid") is not False:
            raise catalog.PilotError(f"Phase 4 candidate {identifier} uses current AIS")
        if properties.get("dark_vessel_claim") is not False:
            raise catalog.PilotError(f"Phase 4 candidate {identifier} has an unsafe claim")
        centroid = properties.get("centroid_pixel")
        if not isinstance(centroid, dict):
            raise catalog.PilotError(f"Phase 4 candidate {identifier} has no source pixel")
        row = _number(centroid.get("row"), f"{identifier} centroid row")
        column = _number(centroid.get("column"), f"{identifier} centroid column")
        if raster_shape is not None:
            height, width = raster_shape
            if not 0 <= row < height or not 0 <= column < width:
                raise catalog.PilotError(f"Phase 4 candidate {identifier} is outside the raster")
        signature = properties.get("signature_measurements")
        if not isinstance(signature, dict) or not isinstance(
            signature.get("seed_bbox_pixels"), dict
        ):
            raise catalog.PilotError(f"Phase 4 candidate {identifier} has no seed pixel box")
        candidate_features[identifier] = feature
    if len(candidate_features) != expected_candidate_count:
        raise catalog.PilotError("Phase 4 context feature count differs from the receipt")

    if queue.get("schema_version") != "1.0.0" or queue.get("phase") != "single_scene_analyst_review_queue":
        raise catalog.PilotError("Phase 5 review queue has the wrong schema or phase")
    if queue.get("complete") is not True or queue.get("degraded") is not False:
        raise catalog.PilotError("Phase 5 review queue is not complete and healthy")
    if (queue.get("scene") or {}).get("normalized_scene_id") != expected_scene:
        raise catalog.PilotError("Phase 5 review queue scene differs from Phase 5.1")
    for key, expected in (
        ("current_ais_used", False),
        ("current_ais_overlaid", False),
        ("dark_vessel_claim", False),
        ("automatic_final_disposition", False),
        ("downstream_eligible", False),
        ("public_layer", False),
    ):
        if queue.get(key) is not expected:
            raise catalog.PilotError(f"Phase 5 review queue safety field {key} changed")
    summary = queue.get("queue_summary") or {}
    if summary.get("source_candidate_count") != expected_candidate_count:
        raise catalog.PilotError("Phase 5 queue candidate count differs from the receipt")
    if summary.get("review_object_count") != expected_object_count:
        raise catalog.PilotError("Phase 5 queue object count differs from the receipt")
    if summary.get("visual_confirmation_pending_count") != expected_object_count:
        raise catalog.PilotError("Phase 5 queue no longer has every object pending visual review")
    if summary.get("final_disposition_count") != 0 or summary.get("downstream_eligible_count") != 0:
        raise catalog.PilotError("Phase 5 queue already contains a final/downstream disposition")

    items = queue.get("items")
    if not isinstance(items, list) or len(items) != expected_object_count:
        raise catalog.PilotError("Phase 5 review queue item count differs from the receipt")
    review_items: dict[str, dict[str, Any]] = {}
    seen_candidates: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise catalog.PilotError("Phase 5 review queue contains a non-object item")
        identifier = item.get("review_object_id")
        if not isinstance(identifier, str) or not identifier or identifier in review_items:
            raise catalog.PilotError("Phase 5 review-object ID is missing or duplicated")
        if item.get("review_state") != "READY_FOR_VISUAL_CONFIRMATION":
            raise catalog.PilotError(f"{identifier} is not ready for visual confirmation")
        analyst = item.get("analyst_review")
        if not isinstance(analyst, dict):
            raise catalog.PilotError(f"{identifier} has no analyst-review state")
        if analyst.get("visual_confirmation_required") is not True:
            raise catalog.PilotError(f"{identifier} bypasses required visual confirmation")
        if analyst.get("visual_confirmation_complete") is not False:
            raise catalog.PilotError(f"{identifier} already claims visual confirmation")
        if analyst.get("final_disposition") is not None:
            raise catalog.PilotError(f"{identifier} already has a final disposition")
        if analyst.get("automatic_final_disposition") is not False:
            raise catalog.PilotError(f"{identifier} enables automatic final disposition")
        if item.get("downstream_eligible") is not False or item.get("public_layer") is not False:
            raise catalog.PilotError(f"{identifier} bypasses review isolation")
        if item.get("current_ais_overlaid") is not False or item.get("dark_vessel_claim") is not False:
            raise catalog.PilotError(f"{identifier} has unsafe time or claim semantics")
        ais = item.get("historical_ais_association")
        if not isinstance(ais, dict) or ais.get("current_ais_used") is not False:
            raise catalog.PilotError(f"{identifier} has no historical-only AIS association")
        projected = ais.get("projected_position")
        if not isinstance(projected, dict) or not isinstance(projected.get("coordinates"), list):
            raise catalog.PilotError(f"{identifier} has no historical AIS projected position")
        coordinates = projected["coordinates"]
        if len(coordinates) != 2:
            raise catalog.PilotError(f"{identifier} historical AIS position is invalid")
        _number(coordinates[0], f"{identifier} AIS longitude")
        _number(coordinates[1], f"{identifier} AIS latitude")
        if projected.get("time_utc") != config["scene"]["acquisition_time_utc"]:
            raise catalog.PilotError(f"{identifier} AIS projection is not at the SAR time")
        _number(ais.get("effective_cog_degrees"), f"{identifier} historical AIS COG")

        source_candidates = item.get("source_candidates")
        if not isinstance(source_candidates, list) or not source_candidates:
            raise catalog.PilotError(f"{identifier} has no source candidates")
        member_ids = []
        for member in source_candidates:
            if not isinstance(member, dict) or not isinstance(member.get("candidate_id"), str):
                raise catalog.PilotError(f"{identifier} has an invalid source-candidate member")
            member_ids.append(member["candidate_id"])
        preserved = (item.get("deduplication") or {}).get("source_candidate_ids_preserved")
        if not isinstance(preserved, list) or set(preserved) != set(member_ids):
            raise catalog.PilotError(f"{identifier} does not preserve its source candidates")
        if len(member_ids) != len(set(member_ids)) or seen_candidates.intersection(member_ids):
            raise catalog.PilotError(f"{identifier} duplicates a source candidate")
        if not set(member_ids).issubset(candidate_features):
            raise catalog.PilotError(f"{identifier} references an unknown source candidate")
        seen_candidates.update(member_ids)
        review_items[identifier] = item
    if seen_candidates != set(candidate_features):
        raise catalog.PilotError("Phase 5.1 does not cover every Phase 4 source candidate exactly once")

    if objects.get("type") != "FeatureCollection" or objects.get("schema_version") != "1.0.0":
        raise catalog.PilotError("Phase 5 review-object product is not the expected GeoJSON")
    object_metadata = objects.get("metadata") or {}
    if object_metadata.get("source_candidate_count") != expected_candidate_count:
        raise catalog.PilotError("Phase 5 review-object candidate count changed")
    if object_metadata.get("review_object_count") != expected_object_count:
        raise catalog.PilotError("Phase 5 review-object count changed")
    if object_metadata.get("current_positions_included") is not False:
        raise catalog.PilotError("Phase 5 review-object product includes current positions")
    if object_metadata.get("public_layer") is not False:
        raise catalog.PilotError("Phase 5 review-object product became public")
    object_features = objects.get("features")
    if not isinstance(object_features, list) or len(object_features) != expected_object_count:
        raise catalog.PilotError("Phase 5 review-object GeoJSON feature count changed")
    object_ids: set[str] = set()
    for feature in object_features:
        if not isinstance(feature, dict):
            raise catalog.PilotError("Phase 5 review-object GeoJSON has a non-object feature")
        identifier = feature.get("id")
        properties = feature.get("properties") or {}
        if identifier not in review_items or properties.get("review_object_id") != identifier:
            raise catalog.PilotError("Phase 5 review-object GeoJSON ID is inconsistent")
        if properties.get("visual_confirmation_complete") is not False:
            raise catalog.PilotError(f"{identifier} GeoJSON already claims visual confirmation")
        if properties.get("downstream_eligible") is not False or properties.get("public_layer") is not False:
            raise catalog.PilotError(f"{identifier} GeoJSON bypasses review isolation")
        object_ids.add(identifier)
    if object_ids != set(review_items):
        raise catalog.PilotError("Phase 5 queue and review-object GeoJSON differ")

    return {
        "hashes": hashes,
        "candidate_features": candidate_features,
        "review_items": review_items,
        "review_item_order": [item["review_object_id"] for item in items],
    }


def wgs84_to_pixel(
    coordinates: list[Any], shape: tuple[int, int], bbox: list[Any]
) -> tuple[float, float]:
    if len(coordinates) != 2 or len(bbox) != 4:
        raise catalog.PilotError("Coordinate-to-pixel conversion received invalid geometry")
    height, width = shape
    longitude = _number(coordinates[0], "longitude")
    latitude = _number(coordinates[1], "latitude")
    west, south, east, north = (_number(value, "AOI bounding box") for value in bbox)
    column = ((longitude - west) / (east - west)) * width - 0.5
    row = ((north - latitude) / (north - south)) * height - 0.5
    return row, column


def approximate_pixel_scale_m(shape: tuple[int, int], bbox: list[Any]) -> dict[str, float]:
    height, width = shape
    west, south, east, north = (float(value) for value in bbox)
    mean_latitude = (south + north) / 2.0
    x_m = abs(east - west) * 111_320.0 * math.cos(math.radians(mean_latitude)) / width
    y_m = abs(north - south) * 111_132.0 / height
    return {
        "x_m_per_pixel": round(x_m, 4),
        "y_m_per_pixel": round(y_m, 4),
        "mean_m_per_pixel": round((x_m + y_m) / 2.0, 4),
        "approximate_only": True,
    }


def crop_origin(
    points: list[tuple[float, float]],
    shape: tuple[int, int],
    crop_size: int,
    margin: int,
) -> tuple[int, int]:
    if not points:
        raise catalog.PilotError("Visual-review crop has no candidate or AIS points")
    height, width = shape
    if crop_size > height or crop_size > width:
        raise catalog.PilotError("Visual-review crop exceeds the source raster")
    rows = [row for row, _ in points]
    columns = [column for _, column in points]
    if max(rows) - min(rows) > crop_size - 2 * margin:
        raise catalog.PilotError("Visual-review points exceed the vertical crop guard")
    if max(columns) - min(columns) > crop_size - 2 * margin:
        raise catalog.PilotError("Visual-review points exceed the horizontal crop guard")
    center_row = (min(rows) + max(rows)) / 2.0
    center_column = (min(columns) + max(columns)) / 2.0
    row0 = max(0, min(height - crop_size, int(round(center_row - crop_size / 2))))
    column0 = max(0, min(width - crop_size, int(round(center_column - crop_size / 2))))
    for row, column in points:
        if not row0 <= row < row0 + crop_size or not column0 <= column < column0 + crop_size:
            raise catalog.PilotError("Visual-review crop does not contain every review point")
    return row0, column0


def _display_band(
    power: Any,
    valid_mask: Any,
    percentile_range: list[Any],
    label: str,
) -> tuple[Any, dict[str, float]]:
    try:
        import numpy as np
    except ImportError as exc:
        raise catalog.PilotError("Phase 5.1 requires numpy for review rendering") from exc
    values = np.asarray(power, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(values) & (values > 0)
    if not bool(valid.any()):
        raise catalog.PilotError(f"Phase 5.1 {label} band has no positive valid samples")
    decibels = 10.0 * np.log10(values[valid])
    low, high = np.percentile(
        decibels, [float(percentile_range[0]), float(percentile_range[1])]
    )
    if not math.isfinite(float(low)) or not math.isfinite(float(high)) or high <= low:
        raise catalog.PilotError(f"Phase 5.1 {label} display stretch is invalid")
    output = np.zeros(values.shape, dtype=np.uint8)
    scaled = np.clip((10.0 * np.log10(values[valid]) - low) / (high - low), 0.0, 1.0)
    output[valid] = np.rint(scaled * 255.0).astype(np.uint8)
    return output, {"low_db": round(float(low), 4), "high_db": round(float(high), 4)}


def _font(size: int) -> Any:
    from PIL import ImageFont

    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _short_candidate_id(identifier: str) -> str:
    suffix = identifier.rsplit("-", 1)[-1]
    try:
        return f"C{int(suffix)}"
    except ValueError:
        return identifier[-8:]


def _draw_label(draw: Any, xy: tuple[int, int], text: str, colour: tuple[int, int, int]) -> None:
    font = _font(12)
    x, y = xy
    box = draw.textbbox((x, y), text, font=font)
    draw.rectangle((box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1), fill=(12, 16, 20))
    draw.text((x, y), text, fill=colour, font=font)


def _draw_arrow(
    draw: Any,
    start: tuple[float, float],
    course_degrees: float,
    length_pixels: float,
    colour: tuple[int, int, int],
) -> tuple[float, float]:
    start_x, start_y = start
    radians = math.radians(course_degrees)
    end_x = start_x + math.sin(radians) * length_pixels
    end_y = start_y - math.cos(radians) * length_pixels
    draw.line((start_x, start_y, end_x, end_y), fill=colour, width=2)
    head_length = 7.0
    for offset in (-150.0, 150.0):
        head_radians = math.radians(course_degrees + offset)
        head_x = end_x + math.sin(head_radians) * head_length
        head_y = end_y - math.cos(head_radians) * head_length
        draw.line((end_x, end_y, head_x, head_y), fill=colour, width=2)
    return end_x, end_y


def _overlay_panel(
    base_image: Any,
    item: dict[str, Any],
    candidate_features: dict[str, dict[str, Any]],
    row0: int,
    column0: int,
    ais_pixel: tuple[float, float],
    pixel_scale: dict[str, float],
    rendering: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    from PIL import ImageDraw

    image = base_image.copy()
    draw = ImageDraw.Draw(image)
    primary_colour = _rgb(rendering["candidate_marker_rgb"], "candidate marker")
    secondary_colour = _rgb(
        rendering["secondary_candidate_marker_rgb"], "secondary candidate marker"
    )
    ais_colour = _rgb(rendering["historical_ais_marker_rgb"], "historical AIS marker")
    candidate_metadata = []
    candidate_draws = []
    source_candidates = item["source_candidates"]
    for index, member in enumerate(source_candidates):
        identifier = member["candidate_id"]
        properties = candidate_features[identifier]["properties"]
        centroid = properties["centroid_pixel"]
        local_x = float(centroid["column"]) - column0
        local_y = float(centroid["row"]) - row0
        colour = primary_colour if index == 0 else secondary_colour
        bbox = properties["signature_measurements"]["seed_bbox_pixels"]
        left = int(bbox["column_min"]) - column0
        right = int(bbox["column_max"]) - column0
        top = int(bbox["row_min"]) - row0
        bottom = int(bbox["row_max"]) - row0
        candidate_draws.append(
            (index, identifier, colour, left, top, right, bottom, local_x, local_y)
        )
        candidate_metadata.append(
            {
                "candidate_id": identifier,
                "member_role": member.get("member_role"),
                "centroid_source_pixel": {
                    "row": round(float(centroid["row"]), 3),
                    "column": round(float(centroid["column"]), 3),
                },
                "centroid_crop_pixel": {
                    "row": round(local_y, 3),
                    "column": round(local_x, 3),
                },
                "seed_bbox_source_pixels": bbox,
            }
        )

    ais_row, ais_column = ais_pixel
    ais_x = ais_column - column0
    ais_y = ais_row - row0
    radius = 5
    draw.polygon(
        (
            (ais_x, ais_y - radius),
            (ais_x + radius, ais_y),
            (ais_x, ais_y + radius),
            (ais_x - radius, ais_y),
        ),
        outline=ais_colour,
    )
    course = float(item["historical_ais_association"]["effective_cog_degrees"])
    arrow_length_px = float(rendering["course_arrow_length_m"]) / float(
        pixel_scale["mean_m_per_pixel"]
    )
    arrow_end = _draw_arrow(draw, (ais_x, ais_y), course, arrow_length_px, ais_colour)
    label_x = max(2, min(image.width - 64, int(round(ais_x + 7))))
    ais_label_offset_y = -22 if ais_y > image.height - 42 else 5
    label_y = max(2, min(image.height - 16, int(round(ais_y + ais_label_offset_y))))
    _draw_label(draw, (label_x, label_y), "AIS HIST", ais_colour)

    scale_m = float(rendering["scale_bar_length_m"])
    scale_pixels = max(1, int(round(scale_m / float(pixel_scale["mean_m_per_pixel"]))))
    review_points = [(ais_x, ais_y)] + [
        (entry[7], entry[8]) for entry in candidate_draws
    ]
    bottom_left_busy = any(
        point_y > image.height - 48 and point_x < image.width / 2
        for point_x, point_y in review_points
    )
    scale_x = image.width - scale_pixels - 10 if bottom_left_busy else 10
    scale_y = image.height - 13
    scale_text = f"{int(round(scale_m))} m"
    scale_text_x = max(5, min(image.width - 46, scale_x))
    background_left = max(2, min(scale_x, scale_text_x) - 5)
    background_right = min(
        image.width - 2, max(scale_x + scale_pixels, scale_text_x + 44) + 5
    )
    draw.rectangle(
        (background_left, image.height - 32, background_right, image.height - 4),
        fill=(12, 16, 20),
    )
    draw.line((scale_x, scale_y, scale_x + scale_pixels, scale_y), fill=(255, 255, 255), width=3)
    draw.line((scale_x, scale_y - 3, scale_x, scale_y + 3), fill=(255, 255, 255), width=1)
    draw.line(
        (scale_x + scale_pixels, scale_y - 3, scale_x + scale_pixels, scale_y + 3),
        fill=(255, 255, 255),
        width=1,
    )
    draw.text(
        (scale_text_x, image.height - 30),
        scale_text,
        fill=(255, 255, 255),
        font=_font(11),
    )
    # Candidate boxes and labels are drawn last so the historical-AIS arrow or
    # label can never hide a secondary fragment such as C4 or C6.
    for index, identifier, colour, left, top, right, bottom, local_x, local_y in candidate_draws:
        draw.rectangle((left, top, right, bottom), outline=colour, width=2)
        draw.line((local_x - 4, local_y, local_x + 4, local_y), fill=colour, width=1)
        draw.line((local_x, local_y - 4, local_x, local_y + 4), fill=colour, width=1)
        label_x = max(2, min(image.width - 40, int(round(local_x + 6))))
        label_offset_y = -18 if index == 0 else 5
        label_y = max(2, min(image.height - 16, int(round(local_y + label_offset_y))))
        _draw_label(draw, (label_x, label_y), _short_candidate_id(identifier), colour)
    return image, {
        "candidates": candidate_metadata,
        "historical_ais_source_pixel": {
            "row": round(ais_row, 3),
            "column": round(ais_column, 3),
        },
        "historical_ais_crop_pixel": {
            "row": round(ais_y, 3),
            "column": round(ais_x, 3),
        },
        "historical_ais_course_degrees": round(course, 2),
        "course_arrow_end_crop_pixel": {
            "row": round(arrow_end[1], 3),
            "column": round(arrow_end[0], 3),
        },
        "scale_bar_length_m": scale_m,
        "scale_bar_length_px": scale_pixels,
    }


def render_review_sheet(
    raster: Any,
    pilot_config: dict[str, Any],
    config: dict[str, Any],
    candidate_features: dict[str, dict[str, Any]],
    review_items: dict[str, dict[str, Any]],
    review_item_order: list[str],
) -> tuple[bytes, dict[str, Any]]:
    try:
        import numpy as np
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise catalog.PilotError("Phase 5.1 requires numpy and Pillow for review rendering") from exc

    array = np.asarray(raster, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise catalog.PilotError("Phase 5.1 raster must contain VV, VH and dataMask")
    height, width, _ = array.shape
    valid = array[:, :, 2] >= 0.5
    rendering = config["rendering"]
    vv_display, vv_stretch = _display_band(
        array[:, :, 0], valid, rendering["display_percentiles"]["vv_db"], "VV"
    )
    vh_display, vh_stretch = _display_band(
        array[:, :, 1], valid, rendering["display_percentiles"]["vh_db"], "VH"
    )
    crop_size = int(rendering["crop_size_px"])
    margin = int(rendering["minimum_content_margin_px"])
    bbox = pilot_config["aoi"]["bbox_wgs84"]
    pixel_scale = approximate_pixel_scale_m((height, width), bbox)

    panel_gap = 14
    left_margin = 18
    right_margin = 18
    header_height = 92
    row_title_height = 54
    panel_label_height = 24
    row_gap = 22
    panel_count = len(PANEL_ORDER)
    canvas_width = left_margin + panel_count * crop_size + (panel_count - 1) * panel_gap + right_margin
    row_height = row_title_height + panel_label_height + crop_size + row_gap
    canvas_height = header_height + len(review_item_order) * row_height
    canvas = Image.new("RGB", (canvas_width, canvas_height), (16, 20, 26))
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (left_margin, 12),
        "PHASE 5.1 — NATIVE-RESOLUTION SAR VISUAL REVIEW EVIDENCE",
        fill=(255, 226, 124),
        font=_font(18),
    )
    draw.text(
        (left_margin, 40),
        "Historical AIS at scene time only · no current AIS · no analyst decision · no vessel classification",
        fill=(220, 226, 233),
        font=_font(12),
    )
    draw.text(
        (left_margin, 61),
        f"Scene: {config['scene']['normalized_scene_id']} · acquisition {config['scene']['acquisition_time_utc']}",
        fill=(180, 190, 202),
        font=_font(11),
    )

    panel_records = []
    panel_labels = (
        "VV CLEAN · NATIVE 1:1 · NO MARKERS",
        "VH CLEAN · NATIVE 1:1 · NO MARKERS",
        "VV OVERLAY · CANDIDATES + HISTORICAL AIS",
    )
    for row_index, identifier in enumerate(review_item_order, start=1):
        item = review_items[identifier]
        ais = item["historical_ais_association"]
        ais_pixel = wgs84_to_pixel(ais["projected_position"]["coordinates"], (height, width), bbox)
        member_ids = [member["candidate_id"] for member in item["source_candidates"]]
        points = [ais_pixel]
        for candidate_id in member_ids:
            centroid = candidate_features[candidate_id]["properties"]["centroid_pixel"]
            points.append((float(centroid["row"]), float(centroid["column"])))
        row0, column0 = crop_origin(points, (height, width), crop_size, margin)
        vv_crop = Image.fromarray(
            vv_display[row0 : row0 + crop_size, column0 : column0 + crop_size], mode="L"
        ).convert("RGB")
        vh_crop = Image.fromarray(
            vh_display[row0 : row0 + crop_size, column0 : column0 + crop_size], mode="L"
        ).convert("RGB")
        overlay, overlay_metadata = _overlay_panel(
            vv_crop,
            item,
            candidate_features,
            row0,
            column0,
            ais_pixel,
            pixel_scale,
            rendering,
        )

        row_y = header_height + (row_index - 1) * row_height
        candidate_label = " + ".join(_short_candidate_id(value) for value in member_ids)
        name = str(ais.get("name") or "unnamed historical AIS contact")
        draw.text(
            (left_margin, row_y + 4),
            f"{row_index:02d} · {identifier} · historical AIS hypothesis: {name}",
            fill=(245, 247, 250),
            font=_font(14),
        )
        draw.text(
            (left_margin, row_y + 28),
            f"Source candidates: {candidate_label} · hypothesis only · visual confirmation remains pending",
            fill=(179, 190, 202),
            font=_font(11),
        )
        panel_y = row_y + row_title_height + panel_label_height
        origins = []
        for panel_index, (panel, label) in enumerate(
            zip((vv_crop, vh_crop, overlay), panel_labels, strict=True)
        ):
            panel_x = left_margin + panel_index * (crop_size + panel_gap)
            origins.append({"x": panel_x, "y": panel_y})
            draw.text(
                (panel_x, panel_y - panel_label_height + 5),
                label,
                fill=(220, 226, 233),
                font=_font(10),
            )
            canvas.paste(panel, (panel_x, panel_y))
            draw.rectangle(
                (panel_x - 1, panel_y - 1, panel_x + crop_size, panel_y + crop_size),
                outline=(82, 92, 105),
                width=1,
            )

        panel_records.append(
            {
                "review_object_id": identifier,
                "sheet_row": row_index,
                "historical_ais_association_hypothesis": {
                    "name": ais.get("name"),
                    "mmsi": ais.get("mmsi"),
                    "projected_position": ais.get("projected_position"),
                    "effective_cog_degrees": ais.get("effective_cog_degrees"),
                    "identity_is_not_sar_derived": True,
                },
                "source_candidate_ids": member_ids,
                "source_pixel_window": {
                    "row_min": row0,
                    "row_max_exclusive": row0 + crop_size,
                    "column_min": column0,
                    "column_max_exclusive": column0 + crop_size,
                    "width_px": crop_size,
                    "height_px": crop_size,
                    "source_pixels_per_output_pixel": 1.0,
                    "resampling": "NONE",
                },
                "sheet_panel_origins_px": {
                    PANEL_ORDER[index]: origins[index] for index in range(panel_count)
                },
                "overlay": overlay_metadata,
                "visual_confirmation": {
                    "required": True,
                    "complete": False,
                    "analyst_decision_recorded": False,
                    "state": "AWAITING_ANALYST_REVIEW_OF_PHASE_5_1_EVIDENCE",
                },
            }
        )

    destination = io.BytesIO()
    canvas.save(destination, format="PNG", optimize=True, compress_level=9)
    png_bytes = destination.getvalue()
    if len(png_bytes) > int(rendering["maximum_png_bytes"]):
        raise catalog.PilotError("Phase 5.1 review sheet exceeds the configured PNG byte guard")
    metadata = {
        "format": "PNG",
        "width_px": canvas_width,
        "height_px": canvas_height,
        "bytes": len(png_bytes),
        "sha256": hashlib.sha256(png_bytes).hexdigest(),
        "source_raster_shape": [height, width, 3],
        "panel_order": PANEL_ORDER,
        "clean_panels_unmarked": True,
        "overlay_is_separate_panel": True,
        "native_source_pixels_per_output_pixel": 1.0,
        "resampling": "NONE",
        "pixel_scale": pixel_scale,
        "global_display_stretch_db": {"vv": vv_stretch, "vh": vh_stretch},
        "review_object_count": len(panel_records),
        "review_panels": panel_records,
    }
    return png_bytes, metadata


def _scene_selection(
    search_payload: dict[str, Any], pilot_config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    scene, returned_features = catalog.select_scene(
        search_payload,
        str(pilot_config["expected_sentinel_hub_product_id"]),
        "Sentinel Hub authenticated Phase 5.1 single-scene catalogue search",
    )
    if returned_features != 1:
        raise catalog.PilotError(
            f"Single-scene guard rejected {returned_features} catalogue features; expected exactly one"
        )
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
        raise catalog.PilotError("Selected Phase 5.1 catalogue item lost its exact scene identifier")
    return (
        {
            "catalogue_item_id": scene.get("id"),
            "matched_scene_identifier": matched_identifier,
            "normalized_scene_id": expected_normalized,
            "datetime": (scene.get("properties") or {}).get("datetime"),
        },
        {
            "endpoint": catalog.SENTINEL_HUB_CATALOG_URL,
            "authenticated": True,
            "returned_features": returned_features,
            "exact_scene_found_once": True,
            "single_scene_guard_passed": True,
        },
    )


def _load_raster(path: Path, chip_config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    try:
        import numpy as np
        import tifffile
    except ImportError as exc:
        raise catalog.PilotError("Phase 5.1 requires numpy and tifffile") from exc
    validation = chip.analyze_tiff(path, chip_config)
    try:
        array = tifffile.imread(path)
    except Exception as exc:
        raise catalog.PilotError(
            f"Temporary Phase 5.1 raster could not be loaded ({type(exc).__name__})"
        ) from exc
    expected_height = int(chip_config["request"]["height_px"])
    expected_width = int(chip_config["request"]["width_px"])
    if array.shape == (3, expected_height, expected_width):
        array = np.moveaxis(array, 0, -1)
    if array.shape != (expected_height, expected_width, 3):
        raise catalog.PilotError("Temporary Phase 5.1 raster shape changed after validation")
    return np.asarray(array, dtype=np.float32), validation


def base_status(config: dict[str, Any], pilot_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "generated_at": catalog.iso_z(catalog.utc_now()),
        "status": "error",
        "phase": PHASE,
        "provider": "copernicus_data_space_ecosystem",
        "pilot_id": pilot_config.get("pilot_id"),
        "scene": config.get("scene"),
        "inputs": {
            "phase4_context_path": "data/sar_copernicus_ais_context_latest.geojson",
            "phase5_queue_path": "data/sar_copernicus_review_queue_latest.json",
            "phase5_objects_path": "data/sar_copernicus_review_objects_latest.geojson",
            "phase4_context_sha256": None,
            "phase5_queue_sha256": None,
            "phase5_objects_sha256": None,
            "exact_accepted_hashes_verified": False,
            "phase4_outputs_modified": False,
            "phase5_outputs_modified": False,
            "current_ais_files_read": False,
        },
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
        "network_access_performed": False,
        "sar_download_performed": False,
        "ais_download_performed": False,
        "historical_ais_only": True,
        "current_ais_used": False,
        "current_ais_overlaid": False,
        "raster_response_bytes": None,
        "raster_sha256": None,
        "raster_validation": None,
        "temporary_raster_written": False,
        "temporary_raster_deleted": False,
        "raster_persisted": False,
        "raster_artifact_uploaded": False,
        "rendering": {
            "clean_unmarked_panels_created": False,
            "separate_overlay_panels_created": False,
            "native_pixel_scale_preserved": False,
            "review_object_count": 0,
        },
        "outputs": {
            "review_sheet_png": None,
            "review_manifest_json": None,
        },
        "review_queue_modified": False,
        "review_objects_modified": False,
        "analyst_decision_recorded": False,
        "visual_confirmation_completed_automatically": False,
        "visual_confirmation_complete_count": 0,
        "final_disposition_count": 0,
        "downstream_eligible_count": 0,
        "automatic_final_disposition": False,
        "public_layer_modified": False,
        "existing_ais_products_modified": False,
        "gfw_products_modified": False,
        "magic_paws_modified": False,
        "hybrid_index_modified": False,
        "dark_vessel_claim": False,
        "sar_candidates_classified_as_vessels": False,
        "production_ready": False,
        "assessment_limit": ASSESSMENT_LIMIT,
        "next_phase": "manual_analyst_decision_from_phase_5_1_evidence_before_phase_6",
        "errors": [],
    }


def _manifest(
    status: dict[str, Any], config: dict[str, Any], rendering: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "generated_at": status["generated_at"],
        "phase": PHASE,
        "scene": status["selected_scene"],
        "source": {
            **status["inputs"],
            "temporary_raster_sha256": status["raster_sha256"],
            "temporary_raster_deleted": status["temporary_raster_deleted"],
            "historical_ais_only": True,
        },
        "review_sheet": {
            "path": "data/sar_copernicus_visual_review_sheet_latest.png",
            **rendering,
        },
        "review_scope": {
            "analyst_decision_recorded": False,
            "visual_confirmation_complete_count": 0,
            "final_disposition_count": 0,
            "downstream_eligible_count": 0,
            "automatic_final_disposition": False,
            "public_layer": False,
        },
        "current_ais_used": False,
        "current_ais_overlaid": False,
        "dark_vessel_claim": False,
        "candidate_is_not_vessel_classification": True,
        "assessment_limit": ASSESSMENT_LIMIT,
    }


def run_visual_review(
    config: dict[str, Any],
    pilot_config: dict[str, Any],
    chip_config: dict[str, Any],
    context: dict[str, Any],
    queue: dict[str, Any],
    objects: dict[str, Any],
    client_id: str,
    client_secret: str,
    *,
    context_bytes: bytes,
    queue_bytes: bytes,
    objects_bytes: bytes,
    session: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, bytes | None, bool]:
    status = base_status(config, pilot_config)
    temporary_path: Path | None = None
    try:
        validate_config(config, pilot_config, chip_config)
        expected_shape = (
            int(chip_config["request"]["height_px"]),
            int(chip_config["request"]["width_px"]),
        )
        validated = validate_inputs(
            config,
            context,
            queue,
            objects,
            context_bytes=context_bytes,
            queue_bytes=queue_bytes,
            objects_bytes=objects_bytes,
            raster_shape=expected_shape,
        )
        status["inputs"].update(validated["hashes"])
        status["inputs"]["exact_accepted_hashes_verified"] = True

        http = session or chip.BinarySession()
        if hasattr(http, "headers"):
            http.headers.update(
                {"User-Agent": "MOwlSINT-VoodooWhiskers-Copernicus-SAR-Visual-Review/0.1"}
            )
        status["network_access_performed"] = True
        access_token, oauth_metadata = catalog.request_access_token(
            http, client_id, client_secret
        )
        status["oauth"] = oauth_metadata
        search_payload = catalog.safe_post(
            http,
            catalog.SENTINEL_HUB_CATALOG_URL,
            "Sentinel Hub authenticated Phase 5.1 single-scene catalogue search",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=chip.build_catalog_search(pilot_config),
        )
        selected_scene, confirmation = _scene_selection(search_payload, pilot_config)
        if selected_scene["normalized_scene_id"] != config["scene"]["normalized_scene_id"]:
            raise catalog.PilotError("Phase 5.1 catalogue result differs from the accepted scene")
        status["selected_scene"] = selected_scene
        status["catalogue_confirmation"] = confirmation

        raster_bytes, response_headers = chip.request_process_chip(
            http,
            access_token,
            chip.build_process_request(chip_config, pilot_config),
            int(chip_config["request"]["maximum_response_bytes"]),
        )
        status["sar_download_performed"] = True
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

        with tempfile.TemporaryDirectory(prefix="voodoo-sar-visual-review-") as directory:
            temporary_path = Path(directory) / "sentinel1_phase5_1_temporary.tiff"
            temporary_path.write_bytes(raster_bytes)
            status["temporary_raster_written"] = True
            del raster_bytes
            raster, raster_validation = _load_raster(temporary_path, chip_config)
            status["raster_validation"] = raster_validation
            png_bytes, rendering_metadata = render_review_sheet(
                raster,
                pilot_config,
                config,
                validated["candidate_features"],
                validated["review_items"],
                validated["review_item_order"],
            )
            del raster

        status["temporary_raster_deleted"] = temporary_path is not None and not temporary_path.exists()
        if not status["temporary_raster_deleted"]:
            raise catalog.PilotError("Temporary Phase 5.1 raster cleanup could not be verified")

        status["rendering"] = {
            "clean_unmarked_panels_created": rendering_metadata["clean_panels_unmarked"],
            "separate_overlay_panels_created": rendering_metadata[
                "overlay_is_separate_panel"
            ],
            "native_pixel_scale_preserved": rendering_metadata[
                "native_source_pixels_per_output_pixel"
            ]
            == 1.0
            and rendering_metadata["resampling"] == "NONE",
            "review_object_count": rendering_metadata["review_object_count"],
            "pixel_scale": rendering_metadata["pixel_scale"],
            "global_display_stretch_db": rendering_metadata[
                "global_display_stretch_db"
            ],
        }
        manifest = _manifest(status, config, rendering_metadata)
        manifest_bytes = _json_bytes(manifest)
        status["outputs"] = {
            "review_sheet_png": {
                "path": "data/sar_copernicus_visual_review_sheet_latest.png",
                "bytes": len(png_bytes),
                "sha256": hashlib.sha256(png_bytes).hexdigest(),
                "review_object_count": rendering_metadata["review_object_count"],
                "derived_review_visual_only": True,
                "public_layer": False,
            },
            "review_manifest_json": {
                "path": "data/sar_copernicus_visual_review_manifest_latest.json",
                "bytes": len(manifest_bytes),
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "review_object_count": rendering_metadata["review_object_count"],
                "analyst_decision_recorded": False,
                "public_layer": False,
            },
        }
        status["status"] = "ok"
        return status, manifest, png_bytes, True
    except catalog.PilotError as exc:
        if temporary_path is not None and not temporary_path.exists():
            status["temporary_raster_deleted"] = True
        status["errors"] = [str(exc)]
        return status, None, None, False
    except Exception as exc:
        if temporary_path is not None and not temporary_path.exists():
            status["temporary_raster_deleted"] = True
        status["errors"] = [
            f"Unexpected Phase 5.1 processing failure ({type(exc).__name__})"
        ]
        return status, None, None, False


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary_name = handle.name
    Path(temporary_name).replace(path)


def _require_fixed_path(path: Path, expected: Path) -> None:
    if path.resolve() != expected.resolve():
        raise catalog.PilotError(
            f"Phase 5.1 path must remain fixed at {expected.relative_to(ROOT)}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pilot-config", type=Path, default=ROOT / "config/sar_copernicus_pilot.json")
    parser.add_argument("--chip-config", type=Path, default=ROOT / "config/sar_copernicus_chip.json")
    parser.add_argument("--context-input", type=Path, default=DEFAULT_CONTEXT_INPUT)
    parser.add_argument("--queue-input", type=Path, default=DEFAULT_QUEUE_INPUT)
    parser.add_argument("--objects-input", type=Path, default=DEFAULT_OBJECTS_INPUT)
    parser.add_argument("--sheet-output", type=Path, default=DEFAULT_SHEET_OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST_OUTPUT)
    parser.add_argument("--status-output", type=Path, default=DEFAULT_STATUS_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config: dict[str, Any] = {"phase": PHASE}
    pilot_config: dict[str, Any] = {}
    chip_config: dict[str, Any] = {}
    context: dict[str, Any] = {}
    queue: dict[str, Any] = {}
    objects: dict[str, Any] = {}
    try:
        for path, expected in (
            (args.pilot_config, ROOT / "config/sar_copernicus_pilot.json"),
            (args.chip_config, ROOT / "config/sar_copernicus_chip.json"),
            (args.context_input, DEFAULT_CONTEXT_INPUT),
            (args.queue_input, DEFAULT_QUEUE_INPUT),
            (args.objects_input, DEFAULT_OBJECTS_INPUT),
            (args.sheet_output, DEFAULT_SHEET_OUTPUT),
            (args.manifest_output, DEFAULT_MANIFEST_OUTPUT),
            (args.status_output, DEFAULT_STATUS_OUTPUT),
        ):
            _require_fixed_path(path, expected)
        config = catalog.read_json(args.config)
        pilot_config = catalog.read_json(args.pilot_config)
        chip_config = catalog.read_json(args.chip_config)
        context, context_bytes = _read_json_bytes(args.context_input, "Phase 4 context")
        queue, queue_bytes = _read_json_bytes(args.queue_input, "Phase 5 review queue")
        objects, objects_bytes = _read_json_bytes(
            args.objects_input, "Phase 5 review objects"
        )
    except catalog.PilotError as exc:
        status = base_status(config, pilot_config)
        status["errors"] = [str(exc)]
        catalog.atomic_json(args.status_output, status)
        print(json.dumps({"status": "error", "phase": PHASE, "errors": status["errors"]}))
        return 1

    status, manifest, png_bytes, ok = run_visual_review(
        config,
        pilot_config,
        chip_config,
        context,
        queue,
        objects,
        os.environ.get("CDSE_SH_CLIENT_ID", ""),
        os.environ.get("CDSE_SH_CLIENT_SECRET", ""),
        context_bytes=context_bytes,
        queue_bytes=queue_bytes,
        objects_bytes=objects_bytes,
    )
    if ok and manifest is not None and png_bytes is not None:
        atomic_bytes(args.sheet_output, png_bytes)
        catalog.atomic_json(args.manifest_output, manifest)
    catalog.atomic_json(args.status_output, status)
    print(
        json.dumps(
            {
                "status": status["status"],
                "phase": PHASE,
                "scene": (status.get("selected_scene") or {}).get("normalized_scene_id"),
                "review_objects_rendered": (status.get("rendering") or {}).get(
                    "review_object_count"
                ),
                "clean_native_panels": (status.get("rendering") or {}).get(
                    "clean_unmarked_panels_created"
                ),
                "temporary_raster_deleted": status["temporary_raster_deleted"],
                "analyst_decision_recorded": status["analyst_decision_recorded"],
                "visual_confirmation_complete_count": status[
                    "visual_confirmation_complete_count"
                ],
                "downstream_eligible_count": status["downstream_eligible_count"],
                "current_ais_used": status["current_ais_used"],
                "errors": status["errors"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
