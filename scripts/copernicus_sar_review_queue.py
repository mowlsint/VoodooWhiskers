#!/usr/bin/env python3
"""Build one isolated analyst-review queue from the accepted Phase 4 SAR/AIS result.

Phase 5 performs no network access, downloads no imagery or AIS and does not alter
the Phase 4 products. It preserves all six source candidate IDs, groups two pairs
of likely fragmented scattering returns into four review objects, and reports
detection, data and match quality separately. Every result remains preliminary,
requires visual confirmation and is ineligible for downstream/public use.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import copernicus_sar_catalog as catalog


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/sar_copernicus_review.json"
DEFAULT_CONTEXT_INPUT = ROOT / "data/sar_copernicus_ais_context_latest.geojson"
DEFAULT_AIS_WINDOW_INPUT = (
    ROOT / "data/sar_copernicus_historical_ais_window_latest.geojson"
)
DEFAULT_QUEUE_OUTPUT = ROOT / "data/sar_copernicus_review_queue_latest.json"
DEFAULT_OBJECTS_OUTPUT = ROOT / "data/sar_copernicus_review_objects_latest.geojson"
DEFAULT_STATUS_OUTPUT = ROOT / "data/sar_copernicus_review_queue_status_latest.json"

PHASE = "single_scene_analyst_review_queue"
EXPECTED_SCENE = "S1C_IW_GRDH_1SDV_20260816T052402_20260816T052427_009016_011E61_438E"
EXPECTED_ACQUISITION_START = "2026-08-16T05:24:02Z"
EXPECTED_ACQUISITION_END = "2026-08-16T05:24:27Z"
EXPECTED_CANDIDATES = {f"SARCAND-20260816T052402Z-{index:04d}" for index in range(1, 7)}
EXPECTED_PHASE4_COUNTS = {
    "AMBIGUOUS": 4,
    "MATCHED": 1,
    "NO_AIS_COVERAGE": 0,
    "POSSIBLE_MATCH": 1,
    "UNMATCHED_IN_AVAILABLE_AIS": 0,
}
ALLOWED_GROUPING_METHODS = {
    "COURSE_AND_LENGTH_ALIGNED_FRAGMENT_CLUSTER",
    "SINGLE_CANDIDATE",
}
ALLOWED_REVIEW_STATES = {
    "READY_FOR_VISUAL_CONFIRMATION",
    "NEEDS_MORE_DATA",
    "FINAL_REVIEW_COMPLETE",
}
ALLOWED_ANALYST_STATUSES = {
    "UNREVIEWED",
    "PRELIMINARY_REVIEW_ACCEPTED",
    "CONFIRMED_ASSOCIATION",
    "REJECTED_FALSE_POSITIVE",
    "NEEDS_MORE_DATA",
}
ASSESSMENT_LIMIT = (
    "Phase 5 records preliminary analyst-review hypotheses, not vessel classifications, "
    "attribution, intent or proof of AIS disablement. Visual confirmation remains required."
)


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError(f"Invalid numeric value in {label}") from exc
    if not math.isfinite(number):
        raise catalog.PilotError(f"Non-finite numeric value in {label}")
    return number


def _integer(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise catalog.PilotError(f"Invalid integer value in {label}") from exc


def _repo_path(value: Any, expected: Path) -> Path:
    relative = Path(str(value or ""))
    if not str(relative) or relative.is_absolute():
        raise catalog.PilotError("Phase 5 paths must remain repository-relative")
    resolved = (ROOT / relative).resolve()
    if resolved != expected.resolve():
        raise catalog.PilotError(
            f"Phase 5 path must remain fixed at {expected.relative_to(ROOT)}"
        )
    return resolved


def _valid_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(
        character in "0123456789abcdef" for character in text
    )


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _rounded(value: Any, digits: int = 2) -> float:
    return round(float(value), digits)


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != "1.0.0" or config.get("phase") != PHASE:
        raise catalog.PilotError("Unsupported Phase 5 configuration")
    _repo_path(config.get("phase4_context_input"), DEFAULT_CONTEXT_INPUT)
    _repo_path(config.get("phase4_ais_window_input"), DEFAULT_AIS_WINDOW_INPUT)

    receipt = config.get("accepted_input_receipt")
    if not isinstance(receipt, dict):
        raise catalog.PilotError("Phase 5 has no accepted Phase 4 input receipt")
    for key in (
        "phase4_context_sha256",
        "phase4_ais_window_sha256",
        "phase3_candidate_sha256",
    ):
        if not _valid_sha256(receipt.get(key)):
            raise catalog.PilotError(f"Invalid accepted input hash: {key}")
    if _integer(receipt.get("phase4_candidate_count"), "phase4 candidate count") != 6:
        raise catalog.PilotError(
            "Phase 5 must preserve exactly six accepted source candidates"
        )
    projected_count = _integer(
        receipt.get("phase4_projected_historical_vessel_count"),
        "Phase 4 projected historical vessel count",
    )
    if not 1 <= projected_count <= 5000:
        raise catalog.PilotError("Accepted Phase 4 projected-vessel count is invalid")

    scene = config.get("scene")
    expected_scene = {
        "normalized_scene_id": EXPECTED_SCENE,
        "acquisition_start_utc": EXPECTED_ACQUISITION_START,
        "acquisition_end_utc": EXPECTED_ACQUISITION_END,
        "platform": "Sentinel-1C",
        "product_level": "GRD Level-1",
    }
    if scene != expected_scene:
        raise catalog.PilotError("Phase 5 scene receipt changed unexpectedly")

    acceptance = config.get("review_acceptance")
    if not isinstance(acceptance, dict):
        raise catalog.PilotError("Phase 5 review acceptance is missing")
    if acceptance.get("accepted") is not True:
        raise catalog.PilotError("Phase 4 review has not been accepted")
    if acceptance.get("accepted_on_utc_date") != "2026-08-19":
        raise catalog.PilotError("Phase 4 review acceptance date changed")
    if acceptance.get("actor_role") != "project_owner_analyst":
        raise catalog.PilotError("Phase 4 review acceptance role changed")
    if (
        acceptance.get("scope")
        != "preliminary_phase4_interpretation_not_final_vessel_classification"
    ):
        raise catalog.PilotError("Phase 5 acceptance scope is unsafe")
    if acceptance.get("visual_confirmation_still_required") is not True:
        raise catalog.PilotError("Phase 5 must retain visual confirmation")

    dedupe = config.get("deduplication_model")
    if (
        not isinstance(dedupe, dict)
        or dedupe.get("method_version") != "course_length_fragment_grouping_v1"
    ):
        raise catalog.PilotError("Unsupported Phase 5 deduplication model")
    minimum_fraction = _number(
        dedupe.get("minimum_pair_separation_fraction_of_reported_length"),
        "minimum pair separation fraction",
    )
    maximum_fraction = _number(
        dedupe.get("maximum_pair_separation_fraction_of_reported_length"),
        "maximum pair separation fraction",
    )
    maximum_alignment = _number(
        dedupe.get("maximum_alignment_difference_degrees"),
        "maximum alignment difference",
    )
    primary_distance = _number(
        dedupe.get("maximum_representative_candidate_distance_to_ais_m"),
        "maximum representative distance",
    )
    if not 0.25 <= minimum_fraction < maximum_fraction <= 2.0:
        raise catalog.PilotError("Phase 5 length-fraction bounds are invalid")
    if not 1 <= maximum_alignment <= 30 or not 10 <= primary_distance <= 250:
        raise catalog.PilotError("Phase 5 fragment geometry guards are invalid")
    for key in (
        "all_cluster_members_must_be_within_dynamic_radius",
        "preserve_every_source_candidate_id",
        "deduplicated_fragment_is_not_false_positive",
    ):
        if dedupe.get(key) is not True:
            raise catalog.PilotError(
                f"Mandatory Phase 5 deduplication guard is missing: {key}"
            )

    quality = config.get("quality_model")
    if not isinstance(quality, dict):
        raise catalog.PilotError("Phase 5 quality model is missing")
    if quality.get("method_version") != "separate_detection_data_match_quality_v1":
        raise catalog.PilotError("Unsupported Phase 5 quality model")
    if set(quality.get("allowed_labels") or []) != {
        "HIGH",
        "MEDIUM",
        "LOW",
        "NOT_ASSESSABLE",
    }:
        raise catalog.PilotError("Phase 5 quality labels changed")
    if quality.get("no_blended_operational_score") is not True:
        raise catalog.PilotError(
            "Phase 5 must not blend quality into an operational score"
        )
    if quality.get("parameters_are_experimental") is not True:
        raise catalog.PilotError("Phase 5 quality parameters must remain experimental")
    if (quality.get("match_quality") or {}).get(
        "doppler_correction_applied"
    ) is not False:
        raise catalog.PilotError("Phase 5 incorrectly claims Doppler correction")

    objects = config.get("review_objects")
    if not isinstance(objects, list) or len(objects) != 4:
        raise catalog.PilotError(
            "Phase 5 must produce exactly four accepted review objects"
        )
    object_ids: set[str] = set()
    candidate_ids: list[str] = []
    for item in objects:
        if not isinstance(item, dict):
            raise catalog.PilotError("Phase 5 review object configuration is invalid")
        identifier = str(item.get("review_object_id") or "")
        if (
            not identifier.startswith("SARREVIEW-20260816T052402Z-")
            or identifier in object_ids
        ):
            raise catalog.PilotError(
                "Phase 5 review object ID is missing or duplicated"
            )
        object_ids.add(identifier)
        grouping = item.get("grouping_method")
        members = item.get("candidate_ids")
        if grouping not in ALLOWED_GROUPING_METHODS or not isinstance(members, list):
            raise catalog.PilotError(
                f"{identifier} has an invalid grouping method or member list"
            )
        expected_member_count = (
            2 if grouping == "COURSE_AND_LENGTH_ALIGNED_FRAGMENT_CLUSTER" else 1
        )
        if len(members) != expected_member_count or len(set(members)) != len(members):
            raise catalog.PilotError(
                f"{identifier} has an invalid source-candidate count"
            )
        if item.get("representative_candidate_id") not in members:
            raise catalog.PilotError(
                f"{identifier} has no valid representative candidate"
            )
        roles = item.get("member_roles")
        if not isinstance(roles, dict) or set(roles) != set(members):
            raise catalog.PilotError(f"{identifier} member roles are incomplete")
        if item.get("expected_source_match_state") not in {
            "AMBIGUOUS",
            "MATCHED",
            "POSSIBLE_MATCH",
        }:
            raise catalog.PilotError(
                f"{identifier} has an invalid accepted Phase 4 state"
            )
        mmsi = str(item.get("expected_mmsi") or "")
        if len(mmsi) != 9 or not mmsi.isdigit():
            raise catalog.PilotError(f"{identifier} has an invalid MMSI")
        if not str(item.get("expected_vessel_name") or "").strip():
            raise catalog.PilotError(f"{identifier} has no expected vessel name")
        if _number(item.get("expected_reported_length_m"), f"{identifier} length") <= 0:
            raise catalog.PilotError(f"{identifier} has an invalid vessel length")
        if item.get("analyst_status") != "PRELIMINARY_REVIEW_ACCEPTED":
            raise catalog.PilotError(
                f"{identifier} bypasses the accepted preliminary status"
            )
        if item.get("review_state") != "READY_FOR_VISUAL_CONFIRMATION":
            raise catalog.PilotError(f"{identifier} bypasses visual confirmation")
        if item.get("false_positive_status") != "NOT_CONFIRMED":
            raise catalog.PilotError(
                f"{identifier} contains an unsupported false-positive decision"
            )
        expected_quality = item.get("expected_quality")
        if not isinstance(expected_quality, dict) or set(expected_quality) != {
            "detection",
            "data",
            "match",
        }:
            raise catalog.PilotError(f"{identifier} expected quality is incomplete")
        if any(
            value not in quality["allowed_labels"]
            for value in expected_quality.values()
        ):
            raise catalog.PilotError(f"{identifier} expected quality label is invalid")
        note = str(item.get("analyst_note") or "")
        if (
            len(note) < 80
            or "visual" not in note.lower()
            and "doppler" not in note.lower()
        ):
            raise catalog.PilotError(f"{identifier} analyst note is incomplete")
        candidate_ids.extend(str(member) for member in members)
    if set(candidate_ids) != EXPECTED_CANDIDATES or len(candidate_ids) != len(
        EXPECTED_CANDIDATES
    ):
        raise catalog.PilotError(
            "Phase 5 does not preserve each accepted source candidate exactly once"
        )

    outputs = config.get("outputs")
    if not isinstance(outputs, dict):
        raise catalog.PilotError("Phase 5 outputs are missing")
    _repo_path(outputs.get("review_queue_json"), DEFAULT_QUEUE_OUTPUT)
    _repo_path(outputs.get("review_objects_geojson"), DEFAULT_OBJECTS_OUTPUT)
    _repo_path(outputs.get("status_json"), DEFAULT_STATUS_OUTPUT)

    guardrails = config.get("guardrails")
    if not isinstance(guardrails, dict):
        raise catalog.PilotError("Phase 5 guardrails are missing")
    required_true = (
        "manual_workflow_only",
        "exact_accepted_phase4_hashes_required",
        "preserve_phase4_inputs",
        "preserve_every_source_candidate_id",
        "false_positive_requires_explicit_reason",
        "visual_confirmation_required",
        "current_ais_must_not_be_read",
    )
    required_false = (
        "current_ais_overlaid",
        "claim_dark_vessel",
        "classify_sar_candidate_as_vessel",
        "automatic_final_disposition",
        "downstream_eligible",
        "publish_public_layer",
        "modify_phase4_outputs",
        "modify_existing_ais_products",
        "modify_gfw_products",
        "modify_magic_paws",
        "change_hybrid_index",
    )
    if any(guardrails.get(key) is not True for key in required_true):
        raise catalog.PilotError("A mandatory Phase 5 safety guard is disabled")
    if any(guardrails.get(key) is not False for key in required_false):
        raise catalog.PilotError("Phase 5 enables a forbidden operation")
    if guardrails.get("confirmation_phrase") != "BUILD_SAR_REVIEW_QUEUE_ONE_SCENE":
        raise catalog.PilotError("Phase 5 confirmation phrase changed")


def validate_inputs(
    context: dict[str, Any],
    ais_window: dict[str, Any],
    config: dict[str, Any],
) -> None:
    if (
        context.get("type") != "FeatureCollection"
        or context.get("schema_version") != "1.0.0"
    ):
        raise catalog.PilotError(
            "Phase 5 context input is not the accepted GeoJSON product"
        )
    metadata = context.get("metadata")
    if (
        not isinstance(metadata, dict)
        or metadata.get("phase") != "single_scene_historical_ais_match"
    ):
        raise catalog.PilotError("Phase 5 context input is not a Phase 4 product")
    scene = metadata.get("scene")
    if (
        not isinstance(scene, dict)
        or scene.get("normalized_scene_id") != EXPECTED_SCENE
    ):
        raise catalog.PilotError("Phase 5 context input belongs to a different scene")
    if scene.get("acquisition_time_utc") != EXPECTED_ACQUISITION_START:
        raise catalog.PilotError("Phase 5 context input has the wrong acquisition time")
    receipt = config["accepted_input_receipt"]
    if metadata.get("source_candidate_sha256") != receipt["phase3_candidate_sha256"]:
        raise catalog.PilotError(
            "Phase 5 context input has a different Phase 3 source receipt"
        )
    if (
        metadata.get("source_candidate_count") != 6
        or metadata.get("match_counts") != EXPECTED_PHASE4_COUNTS
    ):
        raise catalog.PilotError(
            "Phase 5 context input does not contain the accepted Phase 4 counts"
        )
    if (
        metadata.get("current_ais_used") is not False
        or metadata.get("current_ais_overlaid") is not False
    ):
        raise catalog.PilotError("Phase 5 context input contains current AIS semantics")
    if (
        metadata.get("dark_vessel_claim") is not False
        or metadata.get("public_layer") is not False
    ):
        raise catalog.PilotError(
            "Phase 5 context input contains unsafe claim/publication semantics"
        )
    coverage = metadata.get("coverage_assessment")
    if (
        not isinstance(coverage, dict)
        or coverage.get("status") != "AVAILABLE_AIS_ACTIVITY_OBSERVED"
    ):
        raise catalog.PilotError(
            "Phase 5 requires the accepted local historical AIS activity receipt"
        )
    if coverage.get("sufficient_for_available_ais_comparison") is not True:
        raise catalog.PilotError(
            "Phase 5 accepted comparison no longer has sufficient local AIS context"
        )

    features = context.get("features")
    if not isinstance(features, list) or len(features) != 6:
        raise catalog.PilotError(
            "Phase 5 context input must contain exactly six candidates"
        )
    seen: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise catalog.PilotError(
                "Phase 5 context contains an invalid candidate feature"
            )
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            raise catalog.PilotError(
                "Phase 5 context contains incomplete candidate data"
            )
        identifier = str(properties.get("detection_id") or "")
        if (
            identifier not in EXPECTED_CANDIDATES
            or identifier in seen
            or feature.get("id") != identifier
        ):
            raise catalog.PilotError(
                "Phase 5 context candidate IDs changed or were duplicated"
            )
        seen.add(identifier)
        if properties.get("candidate_status") != "UNREVIEWED_SAR_CANDIDATE":
            raise catalog.PilotError(f"{identifier} source candidate status changed")
        if properties.get("candidate_is_not_vessel_classification") is not True:
            raise catalog.PilotError(f"{identifier} omits its non-classification guard")
        if (
            properties.get("current_ais_overlaid") is not False
            or properties.get("dark_vessel_claim") is not False
        ):
            raise catalog.PilotError(f"{identifier} has unsafe AIS semantics")
        if properties.get("downstream_eligible") is not False:
            raise catalog.PilotError(
                f"{identifier} source candidate is downstream eligible"
            )
        coordinates = (
            geometry.get("coordinates") if geometry.get("type") == "Point" else None
        )
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise catalog.PilotError(f"{identifier} has invalid point geometry")
        historical = properties.get("historical_ais_context")
        if not isinstance(historical, dict) or historical.get(
            "status"
        ) != properties.get("ais_match_status"):
            raise catalog.PilotError(
                f"{identifier} has inconsistent Phase 4 AIS context"
            )
        alternatives = historical.get("plausible_alternatives")
        if not isinstance(alternatives, list) or len(alternatives) != 1:
            raise catalog.PilotError(
                f"{identifier} must retain exactly one accepted AIS alternative"
            )
        alternative = alternatives[0]
        if alternative.get("projection_time_utc") is not None:
            raise catalog.PilotError(
                f"{identifier} has an unexpected projection-time schema"
            )
        projected = alternative.get("projected_position")
        if (
            not isinstance(projected, dict)
            or projected.get("time_utc") != EXPECTED_ACQUISITION_START
        ):
            raise catalog.PilotError(
                f"{identifier} AIS alternative is not aligned to SAR time"
            )
        if alternative.get("projection_quality") not in {"HIGH", "MEDIUM", "LOW"}:
            raise catalog.PilotError(f"{identifier} has an invalid projection quality")

    if (
        ais_window.get("type") != "FeatureCollection"
        or ais_window.get("schema_version") != "1.0.0"
    ):
        raise catalog.PilotError(
            "Phase 5 AIS input is not the accepted GeoJSON product"
        )
    window_metadata = ais_window.get("metadata")
    if (
        not isinstance(window_metadata, dict)
        or window_metadata.get("phase") != "single_scene_historical_ais_match"
    ):
        raise catalog.PilotError("Phase 5 AIS input is not a Phase 4 product")
    if window_metadata.get("projection_time_utc") != EXPECTED_ACQUISITION_START:
        raise catalog.PilotError("Phase 5 AIS input uses the wrong projection time")
    if (
        window_metadata.get("current_positions_included") is not False
        or window_metadata.get("public_layer") is not False
    ):
        raise catalog.PilotError("Phase 5 AIS input contains current/public semantics")
    window_features = ais_window.get("features")
    if not isinstance(window_features, list):
        raise catalog.PilotError("Phase 5 AIS input has no feature list")
    expected_count = int(receipt["phase4_projected_historical_vessel_count"])
    if (
        len(window_features) != expected_count
        or window_metadata.get("feature_count") != expected_count
    ):
        raise catalog.PilotError("Phase 5 AIS input projected-vessel count changed")
    seen_mmsi: set[str] = set()
    for feature in window_features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            raise catalog.PilotError("Phase 5 AIS input contains an invalid feature")
        mmsi = str(properties.get("mmsi") or "")
        if len(mmsi) != 9 or not mmsi.isdigit() or mmsi in seen_mmsi:
            raise catalog.PilotError("Phase 5 AIS input has an invalid/duplicate MMSI")
        seen_mmsi.add(mmsi)
        if (
            properties.get("historical_ais_only") is not True
            or properties.get("current_position") is not False
        ):
            raise catalog.PilotError(f"Projected AIS {mmsi} has unsafe time semantics")
        if properties.get("projection_time_utc") != EXPECTED_ACQUISITION_START:
            raise catalog.PilotError(f"Projected AIS {mmsi} uses the wrong time")


def haversine_m(left: tuple[float, float], right: tuple[float, float]) -> float:
    lon1, lat1 = (math.radians(value) for value in left)
    lon2, lat2 = (math.radians(value) for value in right)
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 6371008.8 * 2.0 * math.asin(min(1.0, math.sqrt(value)))


def bearing_degrees(left: tuple[float, float], right: tuple[float, float]) -> float:
    lon1, lat1 = (math.radians(value) for value in left)
    lon2, lat2 = (math.radians(value) for value in right)
    delta_lon = lon2 - lon1
    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        delta_lon
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_difference_degrees(left: float, right: float) -> float:
    return abs((float(left) - float(right) + 180.0) % 360.0 - 180.0)


def _feature_maps(
    context: dict[str, Any], ais_window: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates = {
        feature["properties"]["detection_id"]: feature
        for feature in context["features"]
    }
    projections = {
        feature["properties"]["mmsi"]: feature for feature in ais_window["features"]
    }
    return candidates, projections


def _alternative(feature: dict[str, Any]) -> dict[str, Any]:
    return feature["properties"]["historical_ais_context"]["plausible_alternatives"][0]


def _signature_major_span(feature: dict[str, Any]) -> float:
    span = feature["properties"]["signature_measurements"][
        "approximate_axis_aligned_signature_span_m"
    ]
    return max(
        _number(span.get("x"), "signature span x"),
        _number(span.get("y"), "signature span y"),
    )


def detection_quality(
    members: list[dict[str, Any]], quality_config: dict[str, Any]
) -> dict[str, Any]:
    rubric = quality_config["detection_quality"]
    profile_counts = [
        _integer(
            member["properties"]["detection_method"].get("profile_count"),
            "profile count",
        )
        for member in members
    ]
    vh_results = [
        member["properties"]["detection_method"].get("vh_cfar_corroborated") is True
        for member in members
    ]
    if max(profile_counts) >= int(rubric["high_minimum_profile_count"]) and (
        not rubric["high_requires_vh_on_at_least_one_member"] or any(vh_results)
    ):
        label = "HIGH"
    elif max(profile_counts) >= int(rubric["medium_minimum_profile_count"]):
        label = "MEDIUM"
    else:
        label = "LOW"
    return {
        "label": label,
        "basis": "CFAR profile consensus and VH corroboration are assessed without treating a bright return as a vessel classification.",
        "maximum_profile_count": max(profile_counts),
        "minimum_profile_count": min(profile_counts),
        "vh_corroborated_member_count": sum(vh_results),
        "member_count": len(members),
    }


def data_quality(
    context: dict[str, Any],
    alternatives: list[dict[str, Any]],
    quality_config: dict[str, Any],
) -> dict[str, Any]:
    rubric = quality_config["data_quality"]
    coverage = context["metadata"]["coverage_assessment"]
    coverage_ok = coverage["sufficient_for_available_ais_comparison"] is True
    bins_complete = coverage["occupied_bin_count"] == coverage["total_bins"]
    gaps = [
        int(alternative["nearest_observation_gap_seconds"])
        for alternative in alternatives
    ]
    qualities = [alternative["projection_quality"] for alternative in alternatives]
    high = (
        (not rubric["high_requires_sufficient_local_coverage"] or coverage_ok)
        and (not rubric["high_requires_every_coverage_bin_occupied"] or bins_complete)
        and max(gaps) <= int(rubric["high_maximum_nearest_observation_gap_seconds"])
        and set(qualities).issubset(set(rubric["high_allowed_projection_qualities"]))
    )
    if high:
        label = "HIGH"
    elif coverage_ok and all(quality in {"HIGH", "MEDIUM"} for quality in qualities):
        label = "MEDIUM"
    else:
        label = "LOW"
    return {
        "label": label,
        "basis": "Local historical AIS activity, temporal bin occupancy and trajectory-projection quality are reported separately from match quality.",
        "coverage_status": coverage["status"],
        "coverage_sufficient": coverage_ok,
        "occupied_bins": coverage["occupied_bin_count"],
        "total_bins": coverage["total_bins"],
        "distinct_valid_messages": coverage["distinct_valid_messages"],
        "distinct_mmsi": coverage["distinct_mmsi"],
        "maximum_nearest_observation_gap_seconds": max(gaps),
        "projection_qualities": sorted(set(qualities)),
        "does_not_prove_complete_reception": True,
    }


def fragment_geometry(
    definition: dict[str, Any],
    members: list[dict[str, Any]],
    alternative: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    if len(members) != 2:
        raise catalog.PilotError(
            f"{definition['review_object_id']} fragment cluster must have two members"
        )
    by_id = {member["id"]: member for member in members}
    representative = by_id[definition["representative_candidate_id"]]
    secondary = next(
        member for member in members if member["id"] != representative["id"]
    )
    representative_point = tuple(
        float(value) for value in representative["geometry"]["coordinates"]
    )
    secondary_point = tuple(
        float(value) for value in secondary["geometry"]["coordinates"]
    )
    projected = alternative["projected_position"]
    projected_point = (float(projected["longitude"]), float(projected["latitude"]))
    separation = haversine_m(representative_point, secondary_point)
    pair_bearing = bearing_degrees(representative_point, secondary_point)
    course = _number(alternative.get("effective_cog_degrees"), "effective COG")
    alignment = angular_difference_degrees(pair_bearing, course)
    length = _number(alternative.get("reported_length_m"), "reported vessel length")
    length_fraction = separation / length
    representative_distance = haversine_m(representative_point, projected_point)
    dedupe = config["deduplication_model"]
    checks = {
        "same_accepted_mmsi": all(
            _alternative(member)["mmsi"] == alternative["mmsi"] for member in members
        ),
        "separation_within_reported_length_fraction": bool(
            float(dedupe["minimum_pair_separation_fraction_of_reported_length"])
            <= length_fraction
            <= float(dedupe["maximum_pair_separation_fraction_of_reported_length"])
        ),
        "pair_aligned_with_ais_course": bool(
            alignment <= float(dedupe["maximum_alignment_difference_degrees"])
        ),
        "representative_close_to_projected_ais": bool(
            representative_distance
            <= float(dedupe["maximum_representative_candidate_distance_to_ais_m"])
        ),
        "all_members_within_dynamic_radius": all(
            _alternative(member)["within_dynamic_radius"] is True for member in members
        ),
    }
    return {
        "pair_separation_m": _rounded(separation, 1),
        "reported_vessel_length_m": _rounded(length, 1),
        "separation_fraction_of_reported_length": _rounded(length_fraction, 4),
        "representative_to_secondary_bearing_degrees": _rounded(pair_bearing, 2),
        "effective_ais_course_degrees": _rounded(course, 2),
        "alignment_difference_degrees": _rounded(alignment, 2),
        "representative_to_projected_ais_distance_m": _rounded(
            representative_distance, 1
        ),
        "checks": checks,
        "all_geometry_checks_passed": all(checks.values()),
        "interpretation": "Likely separate SAR scattering groups from one reported long vessel; not a false-positive decision.",
    }


def match_quality(
    definition: dict[str, Any],
    members: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    geometry_evidence: dict[str, Any] | None,
    quality_config: dict[str, Any],
) -> dict[str, Any]:
    rubric = quality_config["match_quality"]
    if definition["grouping_method"] == "COURSE_AND_LENGTH_ALIGNED_FRAGMENT_CLUSTER":
        same_mmsi = len({alternative["mmsi"] for alternative in alternatives}) == 1
        all_plausible = all(
            alternative["within_maximum_plausible_distance"] is True
            for alternative in alternatives
        )
        if (
            same_mmsi
            and all_plausible
            and geometry_evidence
            and geometry_evidence["all_geometry_checks_passed"]
        ):
            label = "HIGH"
        elif same_mmsi and all_plausible:
            label = "MEDIUM"
        else:
            label = "LOW"
        return {
            "label": label,
            "basis": "One-to-many Phase 4 ambiguity is re-expressed only when same-MMSI, length and course-alignment checks support fragmentation.",
            "source_match_state": definition["expected_source_match_state"],
            "same_mmsi_for_all_members": same_mmsi,
            "all_members_within_hard_maximum": all_plausible,
            "all_fragment_geometry_checks_passed": bool(
                geometry_evidence and geometry_evidence["all_geometry_checks_passed"]
            ),
            "doppler_correction_applied": False,
        }

    member = members[0]
    alternative = alternatives[0]
    source_state = member["properties"]["ais_match_status"]
    within_dynamic = alternative["within_dynamic_radius"] is True
    projection_good = alternative["projection_quality"] in {"HIGH", "MEDIUM"}
    distance = float(alternative["distance_to_sar_candidate_m"])
    hard_maximum = float(rubric["hard_maximum_plausible_distance_m"])
    reported_length = float(alternative["reported_length_m"])
    signature_span = _signature_major_span(member)
    size_ratio = signature_span / reported_length
    size_plausible = bool(
        float(rubric["possible_match_minimum_size_ratio"])
        <= size_ratio
        <= float(rubric["possible_match_maximum_size_ratio"])
    )
    if source_state == "MATCHED" and within_dynamic and projection_good:
        label = "HIGH"
    elif (
        source_state == "POSSIBLE_MATCH"
        and projection_good
        and distance <= hard_maximum
        and size_plausible
    ):
        label = "MEDIUM"
    elif distance <= hard_maximum:
        label = "LOW"
    else:
        label = "NOT_ASSESSABLE"
    return {
        "label": label,
        "basis": "Unique-association quality retains the Phase 4 dynamic radius, hard maximum, projection quality and transparent size agreement.",
        "source_match_state": source_state,
        "within_dynamic_radius": within_dynamic,
        "within_hard_maximum": distance <= hard_maximum,
        "distance_to_candidate_m": _rounded(distance, 1),
        "dynamic_radius_m": alternative["dynamic_radius"]["resulting_dynamic_radius_m"],
        "signature_major_span_m": _rounded(signature_span, 1),
        "reported_vessel_length_m": _rounded(reported_length, 1),
        "signature_to_reported_length_ratio": _rounded(size_ratio, 4),
        "size_ratio_plausible": size_plausible,
        "doppler_correction_applied": False,
    }


def build_review_item(
    definition: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    projections: dict[str, dict[str, Any]],
    context: dict[str, Any],
    config: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    identifier = definition["review_object_id"]
    members = [candidates[candidate_id] for candidate_id in definition["candidate_ids"]]
    if any(
        member["properties"]["ais_match_status"]
        != definition["expected_source_match_state"]
        for member in members
    ):
        raise catalog.PilotError(
            f"{identifier} source match state differs from accepted review"
        )
    alternatives = [_alternative(member) for member in members]
    mmsi_values = {str(alternative.get("mmsi") or "") for alternative in alternatives}
    if mmsi_values != {definition["expected_mmsi"]}:
        raise catalog.PilotError(f"{identifier} MMSI differs from accepted review")
    projection_feature = projections.get(definition["expected_mmsi"])
    if projection_feature is None:
        raise catalog.PilotError(
            f"{identifier} accepted MMSI is missing from the historical window"
        )
    projection = projection_feature["properties"]
    if projection.get("name") != definition["expected_vessel_name"]:
        raise catalog.PilotError(
            f"{identifier} vessel name differs from accepted review"
        )
    if (
        abs(
            float(projection.get("length_m"))
            - float(definition["expected_reported_length_m"])
        )
        > 0.1
    ):
        raise catalog.PilotError(
            f"{identifier} vessel length differs from accepted review"
        )
    for alternative in alternatives:
        projected = alternative["projected_position"]
        if (
            haversine_m(
                (float(projected["longitude"]), float(projected["latitude"])),
                tuple(
                    float(value)
                    for value in projection_feature["geometry"]["coordinates"]
                ),
            )
            > 2.0
        ):
            raise catalog.PilotError(
                f"{identifier} Phase 4 alternative and AIS-window projection disagree"
            )

    geometry_evidence = None
    if definition["grouping_method"] == "COURSE_AND_LENGTH_ALIGNED_FRAGMENT_CLUSTER":
        geometry_evidence = fragment_geometry(
            definition, members, alternatives[0], config
        )

    quality_config = config["quality_model"]
    detection = detection_quality(members, quality_config)
    data = data_quality(context, alternatives, quality_config)
    match = match_quality(
        definition, members, alternatives, geometry_evidence, quality_config
    )
    actual_quality = {
        "detection": detection["label"],
        "data": data["label"],
        "match": match["label"],
    }
    if actual_quality != definition["expected_quality"]:
        raise catalog.PilotError(
            f"{identifier} computed quality {actual_quality} differs from accepted review"
        )

    member_records = []
    for member, alternative in zip(members, alternatives, strict=True):
        properties = member["properties"]
        method = properties["detection_method"]
        measurements = properties["signature_measurements"]
        member_records.append(
            {
                "candidate_id": member["id"],
                "member_role": definition["member_roles"][member["id"]],
                "coordinates": member["geometry"]["coordinates"],
                "source_candidate_status": properties["candidate_status"],
                "source_phase4_match_state": properties["ais_match_status"],
                "cfar_profile_consensus": method["parameter_consensus"],
                "cfar_profile_count": method["profile_count"],
                "vh_cfar_corroborated": method["vh_cfar_corroborated"],
                "vv_peak_db": measurements["vv_peak_db"],
                "vh_at_vv_peak_db": measurements["vh_at_vv_peak_db"],
                "axis_aligned_signature_span_m": measurements[
                    "approximate_axis_aligned_signature_span_m"
                ],
                "span_is_not_vessel_length": measurements["span_is_not_vessel_length"],
                "distance_to_projected_ais_m": alternative[
                    "distance_to_sar_candidate_m"
                ],
                "dynamic_radius_m": alternative["dynamic_radius"][
                    "resulting_dynamic_radius_m"
                ],
                "within_dynamic_radius": alternative["within_dynamic_radius"],
                "near_infrastructure_reference": properties["infrastructure_context"][
                    "near_infrastructure_reference"
                ],
                "nearest_infrastructure_distance_nm": properties[
                    "infrastructure_context"
                ]["nearest_distance_nm"],
                "spatial_quality_flags": properties["spatial_quality"]["quality_flags"],
            }
        )

    coordinates = [member["geometry"]["coordinates"] for member in members]
    representative = candidates[definition["representative_candidate_id"]]
    geometry = (
        {"type": "Point", "coordinates": coordinates[0]}
        if len(coordinates) == 1
        else {"type": "MultiPoint", "coordinates": coordinates}
    )
    source_generated = context.get("generated_at")
    projection_limitations = sorted(
        {
            limitation
            for alternative in alternatives
            for limitation in alternative.get("limitations") or []
        }
    )
    return {
        "review_object_id": identifier,
        "review_state": definition["review_state"],
        "review_priority": definition["review_priority"],
        "association_hypothesis": definition["association_hypothesis"],
        "candidate_is_not_vessel_classification": True,
        "observation": {
            "scene_id": EXPECTED_SCENE,
            "acquisition_start_utc": EXPECTED_ACQUISITION_START,
            "acquisition_end_utc": EXPECTED_ACQUISITION_END,
            "source": "Copernicus / Sentinel-1C",
            "product_level": "GRD Level-1",
            "phase4_processing_time_utc": source_generated,
            "phase5_processing_time_utc": generated_at,
        },
        "geometry": geometry,
        "representative_candidate_id": definition["representative_candidate_id"],
        "representative_coordinates": representative["geometry"]["coordinates"],
        "source_candidates": member_records,
        "deduplication": {
            "grouping_method": definition["grouping_method"],
            "method_version": config["deduplication_model"]["method_version"],
            "source_candidate_count": len(members),
            "source_candidate_ids_preserved": [member["id"] for member in members],
            "member_roles": definition["member_roles"],
            "fragment_geometry": geometry_evidence,
            "fragment_is_not_recorded_as_false_positive": len(members) > 1,
            "repeat_detection_status": "NOT_ASSESSED_SINGLE_SCENE",
            "priority_inflation_from_duplicate_processing": False,
        },
        "historical_ais_association": {
            "source_provider": "ais_dk_historical",
            "coverage_status": context["metadata"]["coverage_assessment"]["status"],
            "coverage_sufficient_for_available_ais_comparison": True,
            "mmsi": projection["mmsi"],
            "imo": projection.get("imo") or "",
            "name": projection.get("name") or "",
            "callsign": projection.get("callsign") or "",
            "ship_type_label": projection.get("ship_type_label") or "",
            "reported_length_m": projection.get("length_m"),
            "reported_width_m": projection.get("width_m"),
            "destination": projection.get("destination") or "",
            "projected_position": {
                "coordinates": projection_feature["geometry"]["coordinates"],
                "time_utc": projection["projection_time_utc"],
            },
            "projection_method": projection["projection_method"],
            "projection_quality": projection["projection_quality"],
            "nearest_observation_gap_seconds": projection[
                "nearest_observation_gap_seconds"
            ],
            "bracket_span_seconds": projection["bracket_span_seconds"],
            "effective_sog_knots": projection["effective_sog_knots"],
            "effective_cog_degrees": projection["effective_cog_degrees"],
            "input_observations": projection["input_observations"],
            "candidate_distances_m": {
                member["id"]: alternative["distance_to_sar_candidate_m"]
                for member, alternative in zip(members, alternatives, strict=True)
            },
            "dynamic_radii_m": {
                member["id"]: alternative["dynamic_radius"][
                    "resulting_dynamic_radius_m"
                ]
                for member, alternative in zip(members, alternatives, strict=True)
            },
            "current_ais_used": False,
            "limitations": projection_limitations,
        },
        "quality": {
            "method_version": quality_config["method_version"],
            "parameters_are_experimental": True,
            "detection_quality": detection,
            "data_quality": data,
            "match_quality": match,
            "blended_operational_score": None,
        },
        "analyst_review": {
            "analyst_status": definition["analyst_status"],
            "acceptance_scope": config["review_acceptance"]["scope"],
            "analyst_note": definition["analyst_note"],
            "false_positive_status": definition["false_positive_status"],
            "false_positive_reason": None,
            "visual_confirmation_required": True,
            "visual_confirmation_complete": False,
            "final_disposition": None,
            "automatic_final_disposition": False,
        },
        "external_research_helpers": {
            "vesselfinder_by_mmsi": f"https://www.vesselfinder.com/vessels/details/{projection['mmsi']}",
            "external_current_positions_are_not_part_of_this_historical_assessment": True,
        },
        "current_ais_overlaid": False,
        "dark_vessel_claim": False,
        "downstream_eligible": False,
        "public_layer": False,
        "assessment_limit": ASSESSMENT_LIMIT,
    }


def build_products(
    context: dict[str, Any],
    ais_window: dict[str, Any],
    config: dict[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    candidates, projections = _feature_maps(context, ais_window)
    items = [
        build_review_item(
            definition, candidates, projections, context, config, generated_at
        )
        for definition in config["review_objects"]
    ]
    candidate_to_object: dict[str, str] = {}
    for item in items:
        for candidate_id in item["deduplication"]["source_candidate_ids_preserved"]:
            if candidate_id in candidate_to_object:
                raise catalog.PilotError(
                    f"Source candidate {candidate_id} appears in multiple review objects"
                )
            candidate_to_object[candidate_id] = item["review_object_id"]
    if set(candidate_to_object) != EXPECTED_CANDIDATES:
        raise catalog.PilotError(
            "Phase 5 review products do not preserve every source candidate"
        )

    quality_counts = {
        dimension: dict(
            sorted(
                Counter(
                    item["quality"][f"{dimension}_quality"]["label"] for item in items
                ).items()
            )
        )
        for dimension in ("detection", "data", "match")
    }
    association_counts = dict(
        sorted(Counter(item["association_hypothesis"] for item in items).items())
    )
    summary = {
        "source_candidate_count": len(context["features"]),
        "review_object_count": len(items),
        "deduplicated_source_candidate_reduction": len(context["features"])
        - len(items),
        "fragment_cluster_count": sum(
            item["deduplication"]["grouping_method"]
            == "COURSE_AND_LENGTH_ALIGNED_FRAGMENT_CLUSTER"
            for item in items
        ),
        "single_candidate_object_count": sum(
            item["deduplication"]["grouping_method"] == "SINGLE_CANDIDATE"
            for item in items
        ),
        "preliminary_review_accepted_count": sum(
            item["analyst_review"]["analyst_status"] == "PRELIMINARY_REVIEW_ACCEPTED"
            for item in items
        ),
        "visual_confirmation_pending_count": sum(
            item["analyst_review"]["visual_confirmation_complete"] is False
            for item in items
        ),
        "confirmed_false_positive_count": 0,
        "final_disposition_count": 0,
        "downstream_eligible_count": 0,
        "quality_counts": quality_counts,
        "association_hypothesis_counts": association_counts,
    }
    queue = {
        "schema_version": "1.0.0",
        "phase": PHASE,
        "generated_at": generated_at,
        "complete": True,
        "degraded": False,
        "scene": config["scene"],
        "source": {
            "phase4_context_path": config["phase4_context_input"],
            "phase4_ais_window_path": config["phase4_ais_window_input"],
            "phase4_context_sha256": config["accepted_input_receipt"][
                "phase4_context_sha256"
            ],
            "phase4_ais_window_sha256": config["accepted_input_receipt"][
                "phase4_ais_window_sha256"
            ],
            "phase3_candidate_sha256": config["accepted_input_receipt"][
                "phase3_candidate_sha256"
            ],
            "phase4_generated_at": context.get("generated_at"),
            "historical_ais_only": True,
        },
        "coverage_area_wgs84": context["metadata"]["coverage_assessment"][
            "coverage_bbox_wgs84"
        ],
        "review_acceptance": config["review_acceptance"],
        "deduplication_model": config["deduplication_model"],
        "quality_model": config["quality_model"],
        "allowed_review_states": sorted(ALLOWED_REVIEW_STATES),
        "allowed_analyst_statuses": sorted(ALLOWED_ANALYST_STATUSES),
        "queue_summary": summary,
        "candidate_to_review_object": dict(sorted(candidate_to_object.items())),
        "items": items,
        "current_ais_used": False,
        "current_ais_overlaid": False,
        "dark_vessel_claim": False,
        "automatic_final_disposition": False,
        "public_layer": False,
        "downstream_eligible": False,
        "assessment_limit": ASSESSMENT_LIMIT,
    }

    geojson_features = []
    for item in items:
        geojson_features.append(
            {
                "type": "Feature",
                "id": item["review_object_id"],
                "geometry": copy.deepcopy(item["geometry"]),
                "properties": {
                    "review_object_id": item["review_object_id"],
                    "review_state": item["review_state"],
                    "review_priority": item["review_priority"],
                    "association_hypothesis": item["association_hypothesis"],
                    "source_candidate_ids": item["deduplication"][
                        "source_candidate_ids_preserved"
                    ],
                    "source_candidate_count": item["deduplication"][
                        "source_candidate_count"
                    ],
                    "grouping_method": item["deduplication"]["grouping_method"],
                    "representative_candidate_id": item["representative_candidate_id"],
                    "representative_coordinates": item["representative_coordinates"],
                    "historical_ais_mmsi": item["historical_ais_association"]["mmsi"],
                    "historical_ais_name": item["historical_ais_association"]["name"],
                    "historical_ais_projected_position": item[
                        "historical_ais_association"
                    ]["projected_position"],
                    "detection_quality": item["quality"]["detection_quality"]["label"],
                    "data_quality": item["quality"]["data_quality"]["label"],
                    "match_quality": item["quality"]["match_quality"]["label"],
                    "analyst_status": item["analyst_review"]["analyst_status"],
                    "analyst_note": item["analyst_review"]["analyst_note"],
                    "false_positive_status": item["analyst_review"][
                        "false_positive_status"
                    ],
                    "visual_confirmation_required": True,
                    "visual_confirmation_complete": False,
                    "candidate_is_not_vessel_classification": True,
                    "current_ais_overlaid": False,
                    "dark_vessel_claim": False,
                    "downstream_eligible": False,
                    "public_layer": False,
                    "assessment_limit": ASSESSMENT_LIMIT,
                },
            }
        )
    objects_geojson = {
        "type": "FeatureCollection",
        "name": "Voodoo Whiskers Copernicus SAR Phase 5 analyst review objects",
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "metadata": {
            "phase": PHASE,
            "scene": config["scene"],
            "source_candidate_count": len(context["features"]),
            "review_object_count": len(items),
            "deduplicated_source_candidate_reduction": 2,
            "historical_ais_only": True,
            "current_positions_included": False,
            "visual_confirmation_required": True,
            "public_layer": False,
            "assessment_limit": ASSESSMENT_LIMIT,
        },
        "features": geojson_features,
    }
    return queue, objects_geojson, summary


def base_status(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "phase": PHASE,
        "generated_at": catalog.iso_z(catalog.utc_now()),
        "status": "error",
        "scene": config.get("scene")
        or {
            "normalized_scene_id": EXPECTED_SCENE,
            "acquisition_start_utc": EXPECTED_ACQUISITION_START,
            "acquisition_end_utc": EXPECTED_ACQUISITION_END,
        },
        "inputs": {
            "phase4_context_path": "data/sar_copernicus_ais_context_latest.geojson",
            "phase4_ais_window_path": "data/sar_copernicus_historical_ais_window_latest.geojson",
            "phase4_context_sha256": None,
            "phase4_ais_window_sha256": None,
            "exact_accepted_hashes_verified": False,
            "phase4_outputs_modified": False,
            "current_ais_files_read": False,
        },
        "processing": None,
        "outputs": {
            "review_queue_json": None,
            "review_objects_geojson": None,
        },
        "network_access_performed": False,
        "sar_or_ais_download_performed": False,
        "historical_ais_only": True,
        "current_ais_used": False,
        "current_ais_overlaid": False,
        "dark_vessel_claim": False,
        "sar_candidates_classified_as_vessels": False,
        "automatic_final_disposition": False,
        "visual_confirmation_required": True,
        "public_layer_modified": False,
        "existing_ais_products_modified": False,
        "gfw_products_modified": False,
        "magic_paws_modified": False,
        "hybrid_index_modified": False,
        "production_ready": False,
        "assessment_limit": ASSESSMENT_LIMIT,
        "next_phase": "voodoo_derived_product_integration_after_visual_confirmation",
        "errors": [],
    }


def _verified_source_bytes(
    payload: dict[str, Any], source_bytes: bytes | None, label: str
) -> bytes:
    if source_bytes is None:
        return _canonical_bytes(payload)
    try:
        parsed = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise catalog.PilotError(
            f"{label} source bytes are not valid UTF-8 JSON"
        ) from exc
    if parsed != payload:
        raise catalog.PilotError(f"{label} source bytes do not match the parsed input")
    return source_bytes


def run_phase5(
    config: dict[str, Any],
    context: dict[str, Any],
    ais_window: dict[str, Any],
    *,
    context_source_bytes: bytes | None = None,
    ais_window_source_bytes: bytes | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, bool]:
    status = base_status(config)
    try:
        validate_config(config)
        context_bytes = _verified_source_bytes(
            context, context_source_bytes, "Phase 4 context"
        )
        window_bytes = _verified_source_bytes(
            ais_window, ais_window_source_bytes, "Phase 4 AIS window"
        )
        context_sha = hashlib.sha256(context_bytes).hexdigest()
        window_sha = hashlib.sha256(window_bytes).hexdigest()
        receipt = config["accepted_input_receipt"]
        if context_sha != receipt["phase4_context_sha256"]:
            raise catalog.PilotError(
                "Phase 4 context SHA-256 differs from the accepted review receipt"
            )
        if window_sha != receipt["phase4_ais_window_sha256"]:
            raise catalog.PilotError(
                "Phase 4 AIS-window SHA-256 differs from the accepted review receipt"
            )
        status["inputs"].update(
            {
                "phase4_context_sha256": context_sha,
                "phase4_ais_window_sha256": window_sha,
                "exact_accepted_hashes_verified": True,
            }
        )
        validate_inputs(context, ais_window, config)
        queue, objects_geojson, summary = build_products(
            context, ais_window, config, status["generated_at"]
        )
        queue_bytes = _canonical_bytes(queue)
        objects_bytes = _canonical_bytes(objects_geojson)
        if len(queue_bytes) > 5 * 1024 * 1024 or len(objects_bytes) > 2 * 1024 * 1024:
            raise catalog.PilotError(
                "Phase 5 output exceeds its small-product byte guard"
            )
        status["processing"] = {
            **summary,
            "deduplication_method": config["deduplication_model"]["method_version"],
            "quality_method": config["quality_model"]["method_version"],
            "source_candidate_ids_preserved_exactly_once": True,
            "false_positive_reasons_required_for_future_rejections": True,
            "doppler_correction_applied": False,
        }
        status["outputs"] = {
            "review_queue_json": {
                "path": str(DEFAULT_QUEUE_OUTPUT.relative_to(ROOT)),
                "bytes": len(queue_bytes),
                "sha256": hashlib.sha256(queue_bytes).hexdigest(),
                "review_object_count": len(queue["items"]),
                "public_layer": False,
            },
            "review_objects_geojson": {
                "path": str(DEFAULT_OBJECTS_OUTPUT.relative_to(ROOT)),
                "bytes": len(objects_bytes),
                "sha256": hashlib.sha256(objects_bytes).hexdigest(),
                "feature_count": len(objects_geojson["features"]),
                "public_layer": False,
            },
        }
        status["status"] = "ok"
        return status, queue, objects_geojson, True
    except catalog.PilotError as exc:
        status["errors"] = [str(exc)]
        return status, None, None, False
    except Exception as exc:
        status["errors"] = [
            f"Unexpected Phase 5 processing failure ({type(exc).__name__})"
        ]
        return status, None, None, False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--context-input", type=Path, default=DEFAULT_CONTEXT_INPUT)
    parser.add_argument(
        "--ais-window-input", type=Path, default=DEFAULT_AIS_WINDOW_INPUT
    )
    parser.add_argument("--queue-output", type=Path, default=DEFAULT_QUEUE_OUTPUT)
    parser.add_argument("--objects-output", type=Path, default=DEFAULT_OBJECTS_OUTPUT)
    parser.add_argument("--status-output", type=Path, default=DEFAULT_STATUS_OUTPUT)
    return parser.parse_args()


def _require_fixed_path(path: Path, expected: Path) -> None:
    if path.resolve() != expected.resolve():
        raise catalog.PilotError(
            f"Phase 5 path must remain fixed at {expected.relative_to(ROOT)}"
        )


def _read_json_with_bytes(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        source_bytes = path.read_bytes()
        payload = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise catalog.PilotError(f"{label} could not be read as UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise catalog.PilotError(f"{label} must contain a JSON object")
    return payload, source_bytes


def main() -> int:
    args = parse_args()
    config: dict[str, Any] = {"phase": PHASE}
    context: dict[str, Any] = {}
    ais_window: dict[str, Any] = {}
    context_bytes: bytes | None = None
    window_bytes: bytes | None = None
    try:
        _require_fixed_path(args.context_input, DEFAULT_CONTEXT_INPUT)
        _require_fixed_path(args.ais_window_input, DEFAULT_AIS_WINDOW_INPUT)
        _require_fixed_path(args.queue_output, DEFAULT_QUEUE_OUTPUT)
        _require_fixed_path(args.objects_output, DEFAULT_OBJECTS_OUTPUT)
        _require_fixed_path(args.status_output, DEFAULT_STATUS_OUTPUT)
        config = catalog.read_json(args.config)
        context, context_bytes = _read_json_with_bytes(
            args.context_input, "Phase 4 context input"
        )
        ais_window, window_bytes = _read_json_with_bytes(
            args.ais_window_input, "Phase 4 historical AIS-window input"
        )
    except catalog.PilotError as exc:
        status = base_status(config)
        status["errors"] = [str(exc)]
        catalog.atomic_json(args.status_output, status)
        print(
            json.dumps({"status": "error", "phase": PHASE, "errors": status["errors"]})
        )
        return 1

    status, queue, objects_geojson, ok = run_phase5(
        config,
        context,
        ais_window,
        context_source_bytes=context_bytes,
        ais_window_source_bytes=window_bytes,
    )
    if ok and queue is not None and objects_geojson is not None:
        catalog.atomic_json(args.queue_output, queue)
        catalog.atomic_json(args.objects_output, objects_geojson)
    catalog.atomic_json(args.status_output, status)
    print(
        json.dumps(
            {
                "status": status["status"],
                "phase": PHASE,
                "source_candidates": (status.get("processing") or {}).get(
                    "source_candidate_count"
                ),
                "review_objects": (status.get("processing") or {}).get(
                    "review_object_count"
                ),
                "fragment_clusters": (status.get("processing") or {}).get(
                    "fragment_cluster_count"
                ),
                "quality_counts": (status.get("processing") or {}).get(
                    "quality_counts"
                ),
                "visual_confirmation_required": status["visual_confirmation_required"],
                "current_ais_used": status["current_ais_used"],
                "errors": status["errors"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
