#!/usr/bin/env python3
"""Match one reviewed Phase 3 SAR candidate set to time-aligned historical AIS.

Phase 4 reads the fixed Danish AIS daily archive for the Sentinel-1 acquisition
date, retains only a bounded one-hour window, projects vessel tracks to the SAR
acquisition time and writes audit-only candidate context. The raw ZIP remains in
an operating-system temporary directory and is deleted before outputs are saved.

The matching parameters are experimental. An unmatched candidate means only
that no plausible match was found in the available archive. It is never proof of
AIS disablement, unlawful conduct, identity, intent, or even that the SAR return
is a vessel.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import copernicus_sar_catalog as catalog
import fetch_aisdk_historical as aisdk


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/sar_copernicus_ais_match.json"
DEFAULT_PILOT_CONFIG = ROOT / "config/sar_copernicus_pilot.json"
DEFAULT_CANDIDATES = ROOT / "data/sar_copernicus_cfar_candidates_latest.geojson"
DEFAULT_CONTEXT = ROOT / "data/sar_copernicus_ais_context_latest.geojson"
DEFAULT_AIS_WINDOW = ROOT / "data/sar_copernicus_historical_ais_window_latest.geojson"
DEFAULT_STATUS = ROOT / "data/sar_copernicus_ais_match_status_latest.json"

PHASE = "single_scene_historical_ais_match"
EXPECTED_SCENE = "S1C_IW_GRDH_1SDV_20260816T052402_20260816T052427_009016_011E61_438E"
EXPECTED_ACQUISITION = "2026-08-16T05:24:02Z"
ALLOWED_MATCH_STATES = {
    "MATCHED",
    "POSSIBLE_MATCH",
    "AMBIGUOUS",
    "UNMATCHED_IN_AVAILABLE_AIS",
    "NO_AIS_COVERAGE",
}
ASSESSMENT_LIMIT = (
    "This is an experimental comparison with a reception-dependent historical AIS archive. "
    "A missing match is not proof that AIS was disabled, and neither a match nor an unmatched "
    "SAR return establishes vessel identity, conduct, legality or intent."
)
MATCH_METHOD_NOTE = (
    "AIS tracks are projected to the fixed Sentinel-1 acquisition time. The pilot uses a "
    "candidate-specific dynamic core radius with a 500 m floor and rejects forced assignments "
    "beyond 2 km. These are calibration hypotheses, not production thresholds."
)


def _repo_path(value: Any, expected: Path | None = None) -> Path:
    relative = Path(str(value or ""))
    if not str(relative) or relative.is_absolute():
        raise catalog.PilotError("Phase 4 path must be repository-relative")
    resolved = (ROOT / relative).resolve()
    if resolved != ROOT and ROOT not in resolved.parents:
        raise catalog.PilotError("Phase 4 path escapes the repository root")
    if expected is not None and resolved != expected.resolve():
        raise catalog.PilotError(f"Phase 4 path must remain fixed at {expected.relative_to(ROOT)}")
    return resolved


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


def _rounded(value: Any, digits: int = 4) -> float:
    return round(float(value), digits)


def validate_phase_config(phase_config: dict[str, Any], pilot_config: dict[str, Any]) -> None:
    catalog.validate_config(pilot_config)
    if phase_config.get("schema_version") != "1.0.0" or phase_config.get("phase") != PHASE:
        raise catalog.PilotError("Unsupported Phase 4 configuration")
    if phase_config.get("pilot_config") != "config/sar_copernicus_pilot.json":
        raise catalog.PilotError("Phase 4 must reference the fixed pilot configuration")
    if phase_config.get("candidate_input") != "data/sar_copernicus_cfar_candidates_latest.geojson":
        raise catalog.PilotError("Phase 4 must read the isolated Phase 3 candidate product")

    source = phase_config.get("source")
    if not isinstance(source, dict) or source.get("provider") != "ais_dk_historical":
        raise catalog.PilotError("Phase 4 requires the reviewed Danish historical AIS source")
    archive_url = str(source.get("archive_url") or "")
    parsed_url = urlparse(archive_url)
    if parsed_url.scheme not in {"http", "https"} or parsed_url.netloc != "aisdata.ais.dk":
        raise catalog.PilotError("Phase 4 archive URL must remain on aisdata.ais.dk")
    if Path(parsed_url.path).name != "aisdk-2026-08-16.zip":
        raise catalog.PilotError("Phase 4 archive URL does not identify the fixed acquisition date")
    if source.get("archive_filename") != "aisdk-2026-08-16.zip":
        raise catalog.PilotError("Phase 4 archive filename changed unexpectedly")
    if source.get("archive_date") != "2026-08-16":
        raise catalog.PilotError("Phase 4 archive date changed unexpectedly")
    expected_sha = str(source.get("expected_sha256") or "")
    if len(expected_sha) != 64 or any(character not in "0123456789abcdef" for character in expected_sha):
        raise catalog.PilotError("Phase 4 expected archive SHA-256 is invalid")
    expected_bytes = _integer(source.get("expected_download_bytes"), "source.expected_download_bytes")
    maximum_bytes = _integer(source.get("maximum_download_bytes"), "source.maximum_download_bytes")
    if expected_bytes <= 100 or maximum_bytes < expected_bytes or maximum_bytes > 2 * 1024 * 1024 * 1024:
        raise catalog.PilotError("Phase 4 archive byte guards are invalid")
    if source.get("timestamp_timezone_assumption") != "UTC":
        raise catalog.PilotError("Phase 4 must preserve the explicit Danish timestamp UTC assumption")

    alignment = phase_config.get("time_alignment")
    if not isinstance(alignment, dict):
        raise catalog.PilotError("Phase 4 has no time_alignment object")
    acquisition = catalog.parse_utc(
        alignment.get("scene_acquisition_time_utc"),
        "time_alignment.scene_acquisition_time_utc",
    )
    if catalog.iso_z(acquisition) != EXPECTED_ACQUISITION:
        raise catalog.PilotError("Phase 4 acquisition time does not match the fixed scene")
    if acquisition.date().isoformat() != source.get("archive_date"):
        raise catalog.PilotError("Phase 4 archive date does not match the SAR acquisition date")
    before = _integer(alignment.get("window_before_minutes"), "time_alignment.window_before_minutes")
    after = _integer(alignment.get("window_after_minutes"), "time_alignment.window_after_minutes")
    if not 15 <= before <= 30 or not 15 <= after <= 30:
        raise catalog.PilotError("Phase 4 AIS window is outside the reviewed pilot range")
    if alignment.get("current_workflow_time_is_irrelevant") is not True:
        raise catalog.PilotError("Phase 4 must disregard current workflow time")

    trajectory = phase_config.get("trajectory")
    if not isinstance(trajectory, dict):
        raise catalog.PilotError("Phase 4 has no trajectory object")
    required_trajectory = {
        "projection_target": "scene_acquisition_time",
        "prefer_bracketing_linear_interpolation": True,
        "allow_sog_cog_extrapolation": True,
    }
    for key, expected in required_trajectory.items():
        if trajectory.get(key) != expected:
            raise catalog.PilotError(f"Phase 4 trajectory field {key} changed unexpectedly")
    unknown_speed = _number(trajectory.get("unknown_speed_guard_knots"), "trajectory.unknown_speed_guard_knots")
    maximum_sog = _number(trajectory.get("maximum_valid_sog_knots"), "trajectory.maximum_valid_sog_knots")
    retained = _integer(trajectory.get("retained_observations_per_side"), "trajectory.retained_observations_per_side")
    high_gap = _integer(
        trajectory.get("high_quality_max_nearest_gap_seconds"),
        "trajectory.high_quality_max_nearest_gap_seconds",
    )
    medium_gap = _integer(
        trajectory.get("medium_quality_max_nearest_gap_seconds"),
        "trajectory.medium_quality_max_nearest_gap_seconds",
    )
    if not 20 <= unknown_speed <= 60 or not 60 <= maximum_sog <= 102.2:
        raise catalog.PilotError("Phase 4 trajectory speed guards are invalid")
    if not 2 <= retained <= 10 or not 60 <= high_gap < medium_gap <= 1800:
        raise catalog.PilotError("Phase 4 trajectory retention/quality guards are invalid")

    coverage = phase_config.get("coverage_assessment")
    if not isinstance(coverage, dict):
        raise catalog.PilotError("Phase 4 has no coverage_assessment object")
    padding = _number(coverage.get("local_padding_nm"), "coverage_assessment.local_padding_nm")
    bin_minutes = _integer(coverage.get("bin_minutes"), "coverage_assessment.bin_minutes")
    occupied = _integer(coverage.get("minimum_occupied_bins"), "coverage_assessment.minimum_occupied_bins")
    minimum_mmsi = _integer(coverage.get("minimum_distinct_mmsi"), "coverage_assessment.minimum_distinct_mmsi")
    minimum_messages = _integer(coverage.get("minimum_valid_messages"), "coverage_assessment.minimum_valid_messages")
    total_minutes = before + after
    total_bins = math.ceil(total_minutes / bin_minutes)
    if not 2 <= padding <= 25 or bin_minutes not in {5, 10, 15}:
        raise catalog.PilotError("Phase 4 coverage geography/bin size is invalid")
    if not 1 <= occupied <= total_bins or not 1 <= minimum_mmsi <= 100 or not 1 <= minimum_messages <= 10000:
        raise catalog.PilotError("Phase 4 coverage thresholds are invalid")

    matching = phase_config.get("matching")
    if not isinstance(matching, dict) or matching.get("method") != "projected_position_dynamic_radius_pilot":
        raise catalog.PilotError("Phase 4 matching method changed unexpectedly")
    minimum_radius = _number(matching.get("minimum_dynamic_radius_m"), "matching.minimum_dynamic_radius_m")
    maximum_distance = _number(
        matching.get("maximum_plausible_distance_m"),
        "matching.maximum_plausible_distance_m",
    )
    movement_fraction = _number(
        matching.get("movement_uncertainty_fraction"),
        "matching.movement_uncertainty_fraction",
    )
    movement_cap = _number(matching.get("movement_uncertainty_cap_m"), "matching.movement_uncertainty_cap_m")
    single_penalty = _number(
        matching.get("single_sided_projection_penalty_m"),
        "matching.single_sided_projection_penalty_m",
    )
    no_motion_penalty = _number(
        matching.get("no_motion_projection_penalty_m"),
        "matching.no_motion_projection_penalty_m",
    )
    extent_cap = _number(
        matching.get("candidate_extent_allowance_cap_m"),
        "matching.candidate_extent_allowance_cap_m",
    )
    if not 250 <= minimum_radius <= 1000 or not minimum_radius < maximum_distance <= 3000:
        raise catalog.PilotError("Phase 4 spatial matching bounds are invalid")
    if not 0 <= movement_fraction <= 0.5 or not 0 <= movement_cap <= 1000:
        raise catalog.PilotError("Phase 4 movement uncertainty parameters are invalid")
    if not 0 <= single_penalty <= 1000 or not single_penalty <= no_motion_penalty <= 1500:
        raise catalog.PilotError("Phase 4 projection penalties are invalid")
    if not 0 <= extent_cap <= 500:
        raise catalog.PilotError("Phase 4 candidate extent allowance is invalid")
    required_matching_true = (
        "low_quality_match_must_remain_possible",
        "one_ais_to_multiple_sar_is_ambiguous",
        "multiple_ais_to_one_sar_is_ambiguous",
        "parameters_are_experimental",
    )
    if any(matching.get(key) is not True for key in required_matching_true):
        raise catalog.PilotError("Mandatory conservative Phase 4 matching guards are missing")

    outputs = phase_config.get("outputs")
    expected_outputs = {
        "candidate_context_geojson": DEFAULT_CONTEXT,
        "historical_ais_window_geojson": DEFAULT_AIS_WINDOW,
        "status_json": DEFAULT_STATUS,
    }
    if not isinstance(outputs, dict):
        raise catalog.PilotError("Phase 4 has no outputs object")
    for key, expected in expected_outputs.items():
        _repo_path(outputs.get(key), expected)

    guardrails = phase_config.get("guardrails")
    if not isinstance(guardrails, dict):
        raise catalog.PilotError("Phase 4 has no guardrails object")
    required_true = (
        "manual_workflow_only",
        "exact_phase3_scene_required",
        "temporary_raw_archive_only",
        "delete_raw_archive_after_processing",
        "use_historical_ais_only",
        "unmatched_and_no_coverage_must_remain_distinct",
    )
    if any(guardrails.get(key) is not True for key in required_true):
        raise catalog.PilotError("Mandatory Phase 4 guardrails are missing")
    required_false = (
        "persist_raw_archive",
        "extract_raw_csv_to_repository",
        "overlay_current_ais",
        "claim_dark_vessel",
        "classify_sar_candidate_as_vessel",
        "publish_public_layer",
        "modify_phase3_candidates",
        "modify_existing_ais_products",
        "modify_gfw_products",
        "modify_magic_paws",
    )
    if any(guardrails.get(key) is not False for key in required_false):
        raise catalog.PilotError("Phase 4 configuration enables a forbidden operation")
    if guardrails.get("confirmation_phrase") != "MATCH_HISTORICAL_AIS_ONE_SCENE":
        raise catalog.PilotError("Phase 4 confirmation phrase differs from the reviewed guard")


def validate_candidate_input(payload: dict[str, Any], pilot_config: dict[str, Any]) -> None:
    if payload.get("type") != "FeatureCollection" or payload.get("schema_version") != "1.0.0":
        raise catalog.PilotError("Phase 3 candidate input is not the expected GeoJSON product")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise catalog.PilotError("Phase 3 candidate input has no metadata")
    if metadata.get("phase") != "single_scene_cfar_parameter_comparison":
        raise catalog.PilotError("Phase 4 input is not the isolated Phase 3 product")
    if metadata.get("pilot_id") != pilot_config.get("pilot_id"):
        raise catalog.PilotError("Phase 3 candidate input belongs to a different pilot")
    scene = metadata.get("scene")
    if not isinstance(scene, dict) or scene.get("normalized_scene_id") != EXPECTED_SCENE:
        raise catalog.PilotError("Phase 3 candidate input belongs to a different Sentinel-1 scene")
    if scene.get("datetime") != EXPECTED_ACQUISITION:
        raise catalog.PilotError("Phase 3 candidate acquisition time changed unexpectedly")
    if metadata.get("ais_context_status") != "NOT_CHECKED":
        raise catalog.PilotError("Phase 3 candidates already claim an AIS assessment")
    if metadata.get("current_ais_overlaid") is not False or metadata.get("dark_vessel_claim") is not False:
        raise catalog.PilotError("Phase 3 candidate input contains unsafe AIS semantics")
    if metadata.get("visual_review_required") is not True or metadata.get("public_layer") is not False:
        raise catalog.PilotError("Phase 3 candidate input bypasses its review/publication guard")
    features = payload.get("features")
    if not isinstance(features, list) or not 1 <= len(features) <= 1000:
        raise catalog.PilotError("Phase 3 candidate count is outside the pilot range")
    bbox = pilot_config["aoi"]["bbox_wgs84"]
    identifiers: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise catalog.PilotError("Phase 3 candidate input contains an invalid feature")
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            raise catalog.PilotError("Phase 3 candidate input has incomplete feature data")
        identifier = str(properties.get("detection_id") or "")
        if not identifier or identifier in identifiers or feature.get("id") != identifier:
            raise catalog.PilotError("Phase 3 candidate identifiers are missing or duplicated")
        identifiers.add(identifier)
        if properties.get("candidate_status") != "UNREVIEWED_SAR_CANDIDATE":
            raise catalog.PilotError(f"{identifier} is no longer an unreviewed SAR candidate")
        if properties.get("candidate_is_not_vessel_classification") is not True:
            raise catalog.PilotError(f"{identifier} omits the non-classification guard")
        if properties.get("ais_context_status") != "NOT_CHECKED":
            raise catalog.PilotError(f"{identifier} already claims AIS context")
        if properties.get("current_ais_overlaid") is not False or properties.get("dark_vessel_claim") is not False:
            raise catalog.PilotError(f"{identifier} contains unsafe AIS semantics")
        coordinates = geometry.get("coordinates") if geometry.get("type") == "Point" else None
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise catalog.PilotError(f"{identifier} has no point geometry")
        longitude = _number(coordinates[0], f"{identifier} longitude")
        latitude = _number(coordinates[1], f"{identifier} latitude")
        west, south, east, north = (float(value) for value in bbox)
        if not west <= longitude <= east or not south <= latitude <= north:
            raise catalog.PilotError(f"{identifier} lies outside the fixed pilot AOI")


def expand_bbox_nm(bbox: list[Any], padding_nm: float) -> list[float]:
    west, south, east, north = (float(value) for value in bbox)
    midpoint_latitude = (south + north) / 2.0
    latitude_padding = padding_nm / 60.0
    longitude_scale = max(0.1, math.cos(math.radians(midpoint_latitude)))
    longitude_padding = padding_nm / (60.0 * longitude_scale)
    return [
        west - longitude_padding,
        south - latitude_padding,
        east + longitude_padding,
        north + latitude_padding,
    ]


def point_in_bbox(longitude: float, latitude: float, bbox: list[Any]) -> bool:
    west, south, east, north = (float(value) for value in bbox)
    return west <= longitude <= east and south <= latitude <= north


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


def initial_bearing_degrees(left: tuple[float, float], right: tuple[float, float]) -> float:
    lon1, lat1 = (math.radians(value) for value in left)
    lon2, lat2 = (math.radians(value) for value in right)
    delta_lon = lon2 - lon1
    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination_point(
    point: tuple[float, float], bearing_degrees: float, distance_m: float
) -> tuple[float, float]:
    longitude, latitude = point
    bearing = float(bearing_degrees)
    distance = float(distance_m)
    if distance < 0:
        distance = abs(distance)
        bearing = (bearing + 180.0) % 360.0
    angular_distance = distance / 6371008.8
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    theta = math.radians(bearing)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(theta)
    )
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    normalized_lon = (math.degrees(lon2) + 540.0) % 360.0 - 180.0
    return normalized_lon, math.degrees(lat2)


def _fast_archive_seconds(value: Any, expected_date: date) -> tuple[int | None, str | None]:
    text = str(value or "").strip()
    if len(text) != 19 or text[2] != "/" or text[5] != "/" or text[10] != " " or text[13] != ":" or text[16] != ":":
        return None, "invalid_timestamp"
    try:
        day = int(text[0:2])
        month = int(text[3:5])
        year = int(text[6:10])
        hour = int(text[11:13])
        minute = int(text[14:16])
        second = int(text[17:19])
        parsed_date = date(year, month, day)
    except ValueError:
        return None, "invalid_timestamp"
    if parsed_date != expected_date:
        return None, "outside_archive_date"
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        return None, "invalid_timestamp"
    return hour * 3600 + minute * 60 + second, None


def _public_observation(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_at": record["observed_at"],
        "latitude": record["latitude"],
        "longitude": record["longitude"],
        "sog_knots": record.get("sog"),
        "cog_degrees": record.get("cog"),
        "true_heading_degrees": record.get("true_heading"),
        "navigational_status": record.get("navigational_status") or "",
    }


def _retain_nearest(
    bucket: dict[str, Any],
    record: dict[str, Any],
    scene_time: datetime,
    limit: int,
) -> None:
    observed = record["_observed_dt"]
    side = "before" if observed <= scene_time else "after"
    key = observed.isoformat()
    existing = bucket[side].get(key)
    if existing is None or aisdk.record_score(record) > aisdk.record_score(existing):
        bucket[side][key] = record
    reverse = side == "before"
    ordered = sorted(
        bucket[side].items(),
        key=lambda item: item[1]["_observed_dt"],
        reverse=reverse,
    )
    bucket[side] = dict(ordered[:limit])


def extract_historical_window(
    archive_path: Path,
    phase_config: dict[str, Any],
    pilot_config: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    alignment = phase_config["time_alignment"]
    scene_time = catalog.parse_utc(alignment["scene_acquisition_time_utc"], "scene acquisition")
    window_start = scene_time - timedelta(minutes=int(alignment["window_before_minutes"]))
    window_end = scene_time + timedelta(minutes=int(alignment["window_after_minutes"]))
    archive_date = date.fromisoformat(phase_config["source"]["archive_date"])
    start_second = int((window_start - datetime.combine(archive_date, datetime.min.time(), tzinfo=timezone.utc)).total_seconds())
    end_second = int((window_end - datetime.combine(archive_date, datetime.min.time(), tzinfo=timezone.utc)).total_seconds())
    coverage_config = phase_config["coverage_assessment"]
    coverage_bbox = expand_bbox_nm(
        pilot_config["aoi"]["bbox_wgs84"],
        float(coverage_config["local_padding_nm"]),
    )
    trajectory_config = phase_config["trajectory"]
    matching_config = phase_config["matching"]
    retained_per_side = int(trajectory_config["retained_observations_per_side"])
    unknown_speed = float(trajectory_config["unknown_speed_guard_knots"])
    maximum_sog = float(trajectory_config["maximum_valid_sog_knots"])
    maximum_match_nm = float(matching_config["maximum_plausible_distance_m"]) / 1852.0
    base_bbox = pilot_config["aoi"]["bbox_wgs84"]
    bin_seconds = int(coverage_config["bin_minutes"]) * 60
    total_window_seconds = int((window_end - window_start).total_seconds())
    total_bins = math.ceil(total_window_seconds / bin_seconds)

    counters: Counter[str] = Counter()
    buckets: dict[str, dict[str, Any]] = {}
    coverage_keys: set[tuple[str, str, float, float]] = set()
    coverage_mmsi: set[str] = set()
    coverage_bins: Counter[int] = Counter()
    coverage_min_time: datetime | None = None
    coverage_max_time: datetime | None = None

    try:
        rows = aisdk.iter_csv_rows(archive_path)
        for row in rows:
            counters["rows_read"] += 1
            if len(row) != len(aisdk.COLUMNS):
                counters["wrong_column_count"] += 1
                continue
            if row[0].strip().lower() == "timestamp":
                counters["headers_skipped"] += 1
                continue
            mobile_type = " ".join(row[1].strip().lower().split())
            if mobile_type not in aisdk.ALLOWED_MOBILE_TYPES:
                counters["non_vessel_mobile_type"] += 1
                continue
            seconds, timestamp_reason = _fast_archive_seconds(row[0], archive_date)
            if timestamp_reason:
                counters[timestamp_reason] += 1
                continue
            assert seconds is not None
            if seconds < start_second or seconds > end_second:
                counters["outside_time_window"] += 1
                continue
            record, reason = aisdk.row_to_record(row)
            if reason:
                counters[reason] += 1
                continue
            assert record is not None
            counters["valid_class_ab_messages_in_time_window"] += 1
            observed = record["_observed_dt"]
            longitude = float(record["longitude"])
            latitude = float(record["latitude"])

            if point_in_bbox(longitude, latitude, coverage_bbox):
                coverage_key = (
                    record["mmsi"],
                    record["observed_at"],
                    latitude,
                    longitude,
                )
                if coverage_key not in coverage_keys:
                    coverage_keys.add(coverage_key)
                    coverage_mmsi.add(record["mmsi"])
                    elapsed = max(0, int((observed - window_start).total_seconds()))
                    bin_index = min(total_bins - 1, elapsed // bin_seconds)
                    coverage_bins[bin_index] += 1
                    coverage_min_time = observed if coverage_min_time is None or observed < coverage_min_time else coverage_min_time
                    coverage_max_time = observed if coverage_max_time is None or observed > coverage_max_time else coverage_max_time

            reported_sog = record.get("sog")
            if isinstance(reported_sog, (int, float)) and 0 <= float(reported_sog) <= maximum_sog:
                speed_guard = min(maximum_sog, float(reported_sog) + 5.0)
            else:
                speed_guard = unknown_speed
            time_gap_hours = abs((observed - scene_time).total_seconds()) / 3600.0
            motion_guard_nm = maximum_match_nm + speed_guard * time_gap_hours + 1.0
            extraction_bbox = expand_bbox_nm(base_bbox, motion_guard_nm)
            if not point_in_bbox(longitude, latitude, extraction_bbox):
                counters["outside_dynamic_extraction_guard"] += 1
                continue
            bucket = buckets.setdefault(
                record["mmsi"],
                {"before": {}, "after": {}, "source_messages": 0},
            )
            bucket["source_messages"] += 1
            _retain_nearest(bucket, record, scene_time, retained_per_side)
            counters["messages_inside_dynamic_extraction_guard"] += 1
    except Exception as exc:
        if isinstance(exc, catalog.PilotError):
            raise
        raise catalog.PilotError(f"Historical AIS archive could not be streamed ({type(exc).__name__})") from exc

    occupied_bins = sorted(index for index, count in coverage_bins.items() if count > 0)
    coverage_sufficient = bool(
        len(coverage_keys) >= int(coverage_config["minimum_valid_messages"])
        and len(coverage_mmsi) >= int(coverage_config["minimum_distinct_mmsi"])
        and len(occupied_bins) >= int(coverage_config["minimum_occupied_bins"])
    )
    coverage = {
        "status": "AVAILABLE_AIS_ACTIVITY_OBSERVED" if coverage_sufficient else "NO_AIS_COVERAGE",
        "sufficient_for_available_ais_comparison": coverage_sufficient,
        "assessment_basis": coverage_config["basis"],
        "does_not_prove_complete_reception": True,
        "window_start_utc": catalog.iso_z(window_start),
        "window_end_utc": catalog.iso_z(window_end),
        "local_padding_nm": float(coverage_config["local_padding_nm"]),
        "coverage_bbox_wgs84": [_rounded(value, 7) for value in coverage_bbox],
        "bin_minutes": int(coverage_config["bin_minutes"]),
        "total_bins": total_bins,
        "occupied_bins": occupied_bins,
        "occupied_bin_count": len(occupied_bins),
        "distinct_valid_messages": len(coverage_keys),
        "distinct_mmsi": len(coverage_mmsi),
        "first_local_observation_utc": catalog.iso_z(coverage_min_time) if coverage_min_time else None,
        "last_local_observation_utc": catalog.iso_z(coverage_max_time) if coverage_max_time else None,
        "thresholds": {
            "minimum_occupied_bins": int(coverage_config["minimum_occupied_bins"]),
            "minimum_distinct_mmsi": int(coverage_config["minimum_distinct_mmsi"]),
            "minimum_valid_messages": int(coverage_config["minimum_valid_messages"]),
        },
        "messages_by_bin": {str(index): int(coverage_bins.get(index, 0)) for index in range(total_bins)},
    }
    extraction = {
        "archive_csv_streamed_without_repository_extraction": True,
        "scene_acquisition_time_utc": catalog.iso_z(scene_time),
        "window_start_utc": catalog.iso_z(window_start),
        "window_end_utc": catalog.iso_z(window_end),
        "window_duration_minutes": int((window_end - window_start).total_seconds() / 60),
        "retained_mmsi_buckets": len(buckets),
        "retained_observations": sum(
            len(bucket["before"]) + len(bucket["after"]) for bucket in buckets.values()
        ),
        "counters": dict(counters),
    }
    return buckets, coverage, extraction


def _quality_from_gap(gap_seconds: int, trajectory_config: dict[str, Any], *, bracketed: bool) -> str:
    high = int(trajectory_config["high_quality_max_nearest_gap_seconds"])
    medium = int(trajectory_config["medium_quality_max_nearest_gap_seconds"])
    if bracketed and gap_seconds <= high:
        return "HIGH"
    if gap_seconds <= medium:
        return "MEDIUM"
    return "LOW"


def _identity_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    best = max(records, key=aisdk.record_score)
    return {
        "mmsi": best.get("mmsi"),
        "imo": best.get("imo") or "",
        "callsign": best.get("callsign") or "",
        "name": best.get("name") or "",
        "target_type": best.get("target_type") or "",
        "ship_type_label": best.get("ship_type_label") or "",
        "length_m": best.get("length_m"),
        "width_m": best.get("width_m"),
        "destination": best.get("destination") or "",
    }


def project_trajectory(
    mmsi: str,
    bucket: dict[str, Any],
    scene_time: datetime,
    trajectory_config: dict[str, Any],
) -> dict[str, Any]:
    before = sorted(bucket["before"].values(), key=lambda item: item["_observed_dt"], reverse=True)
    after = sorted(bucket["after"].values(), key=lambda item: item["_observed_dt"])
    all_records = before + after
    if not all_records:
        raise catalog.PilotError(f"AIS trajectory bucket {mmsi} contains no observations")
    identity = _identity_from_records(all_records)
    latest_before = before[0] if before else None
    earliest_after = after[0] if after else None
    method: str
    quality: str
    limitations: list[str] = ["SAR_AZIMUTH_DOPPLER_SHIFT_NOT_CORRECTED"]
    projected_lon: float
    projected_lat: float
    motion_distance = 0.0
    effective_sog: float | None = None
    effective_cog: float | None = None
    nearest_gap_seconds: int
    inputs: list[dict[str, Any]]
    bracket_span_seconds: int | None = None

    if latest_before is not None and latest_before["_observed_dt"] == scene_time:
        method = "EXACT_ARCHIVE_OBSERVATION"
        quality = "HIGH"
        projected_lon = float(latest_before["longitude"])
        projected_lat = float(latest_before["latitude"])
        effective_sog = latest_before.get("sog")
        effective_cog = latest_before.get("cog")
        nearest_gap_seconds = 0
        inputs = [_public_observation(latest_before)]
    elif latest_before is not None and earliest_after is not None:
        before_time = latest_before["_observed_dt"]
        after_time = earliest_after["_observed_dt"]
        span_seconds = int((after_time - before_time).total_seconds())
        if span_seconds <= 0:
            raise catalog.PilotError(f"AIS trajectory {mmsi} has a non-positive interpolation span")
        factor = (scene_time - before_time).total_seconds() / span_seconds
        projected_lon = float(latest_before["longitude"]) + factor * (
            float(earliest_after["longitude"]) - float(latest_before["longitude"])
        )
        projected_lat = float(latest_before["latitude"]) + factor * (
            float(earliest_after["latitude"]) - float(latest_before["latitude"])
        )
        track_distance = haversine_m(
            (float(latest_before["longitude"]), float(latest_before["latitude"])),
            (float(earliest_after["longitude"]), float(earliest_after["latitude"])),
        )
        effective_sog = track_distance / 1852.0 / (span_seconds / 3600.0)
        effective_cog = initial_bearing_degrees(
            (float(latest_before["longitude"]), float(latest_before["latitude"])),
            (float(earliest_after["longitude"]), float(earliest_after["latitude"])),
        )
        before_gap = int((scene_time - before_time).total_seconds())
        after_gap = int((after_time - scene_time).total_seconds())
        nearest_gap_seconds = min(before_gap, after_gap)
        motion_distance = min(track_distance * factor, track_distance * (1.0 - factor))
        bracket_span_seconds = span_seconds
        method = "LINEAR_INTERPOLATION_BRACKETED"
        quality = _quality_from_gap(nearest_gap_seconds, trajectory_config, bracketed=True)
        if effective_sog > float(trajectory_config["maximum_valid_sog_knots"]):
            quality = "LOW"
            limitations.append("DERIVED_SPEED_EXCEEDS_CONFIGURED_AIS_RANGE")
        inputs = [_public_observation(latest_before), _public_observation(earliest_after)]
    else:
        nearest = latest_before or earliest_after
        assert nearest is not None
        delta_seconds = int((scene_time - nearest["_observed_dt"]).total_seconds())
        nearest_gap_seconds = abs(delta_seconds)
        reported_sog = nearest.get("sog")
        reported_cog = nearest.get("cog")
        inputs = [_public_observation(nearest)]
        if (
            isinstance(reported_sog, (int, float))
            and isinstance(reported_cog, (int, float))
            and 0 <= float(reported_sog) <= float(trajectory_config["maximum_valid_sog_knots"])
            and 0 <= float(reported_cog) < 360
        ):
            motion_distance = float(reported_sog) * 1852.0 * (delta_seconds / 3600.0)
            projected_lon, projected_lat = destination_point(
                (float(nearest["longitude"]), float(nearest["latitude"])),
                float(reported_cog),
                motion_distance,
            )
            motion_distance = abs(motion_distance)
            effective_sog = float(reported_sog)
            effective_cog = float(reported_cog)
            method = "SOG_COG_EXTRAPOLATION_SINGLE_SIDED"
            quality = _quality_from_gap(nearest_gap_seconds, trajectory_config, bracketed=False)
            limitations.append("SINGLE_SIDED_CONSTANT_SPEED_COURSE_ASSUMPTION")
        else:
            projected_lon = float(nearest["longitude"])
            projected_lat = float(nearest["latitude"])
            effective_sog = float(reported_sog) if isinstance(reported_sog, (int, float)) else None
            effective_cog = float(reported_cog) if isinstance(reported_cog, (int, float)) else None
            method = "NEAREST_OBSERVATION_NO_MOTION_MODEL"
            quality = "LOW"
            limitations.append("SOG_OR_COG_UNAVAILABLE_FOR_PROJECTION")

    return {
        **identity,
        "historical": True,
        "source_provider": "ais_dk_historical",
        "source_message_count_in_dynamic_guard": int(bucket.get("source_messages") or 0),
        "retained_observation_count": len(before) + len(after),
        "projection_time_utc": catalog.iso_z(scene_time),
        "projected_longitude": round(projected_lon, 7),
        "projected_latitude": round(projected_lat, 7),
        "projection_method": method,
        "projection_quality": quality,
        "nearest_observation_gap_seconds": nearest_gap_seconds,
        "bracket_span_seconds": bracket_span_seconds,
        "motion_distance_from_nearest_observation_m": _rounded(motion_distance, 2),
        "effective_sog_knots": _rounded(effective_sog, 2) if effective_sog is not None else None,
        "effective_cog_degrees": _rounded(effective_cog, 2) if effective_cog is not None else None,
        "input_observations": inputs,
        "limitations": limitations,
    }


def build_projections(
    buckets: dict[str, dict[str, Any]],
    phase_config: dict[str, Any],
    pilot_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scene_time = catalog.parse_utc(
        phase_config["time_alignment"]["scene_acquisition_time_utc"],
        "scene acquisition",
    )
    local_bbox = expand_bbox_nm(
        pilot_config["aoi"]["bbox_wgs84"],
        float(phase_config["coverage_assessment"]["local_padding_nm"]),
    )
    projections: list[dict[str, Any]] = []
    quality_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    discarded_outside_local_context = 0
    for mmsi, bucket in sorted(buckets.items()):
        projection = project_trajectory(mmsi, bucket, scene_time, phase_config["trajectory"])
        if not point_in_bbox(
            float(projection["projected_longitude"]),
            float(projection["projected_latitude"]),
            local_bbox,
        ):
            discarded_outside_local_context += 1
            continue
        projections.append(projection)
        quality_counts[projection["projection_quality"]] += 1
        method_counts[projection["projection_method"]] += 1
    if len(projections) > 5000:
        raise catalog.PilotError("Historical AIS projection product exceeds the Phase 4 vessel guard")
    return projections, {
        "projected_vessels_in_local_context": len(projections),
        "discarded_projected_vessels_outside_local_context": discarded_outside_local_context,
        "quality_counts": dict(quality_counts),
        "method_counts": dict(method_counts),
        "local_context_bbox_wgs84": [_rounded(value, 7) for value in local_bbox],
        "current_positions_used": False,
    }


def _candidate_extent_allowance(properties: dict[str, Any], cap_m: float) -> float:
    measurements = properties.get("signature_measurements")
    span = measurements.get("approximate_axis_aligned_signature_span_m") if isinstance(measurements, dict) else None
    if not isinstance(span, dict):
        return 0.0
    try:
        x = max(0.0, float(span.get("x") or 0.0))
        y = max(0.0, float(span.get("y") or 0.0))
    except (TypeError, ValueError):
        return 0.0
    return min(cap_m, math.hypot(x, y) / 2.0)


def dynamic_radius(
    candidate_properties: dict[str, Any],
    projection: dict[str, Any],
    matching_config: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    minimum_radius = float(matching_config["minimum_dynamic_radius_m"])
    maximum_distance = float(matching_config["maximum_plausible_distance_m"])
    movement_component = min(
        float(matching_config["movement_uncertainty_cap_m"]),
        float(projection["motion_distance_from_nearest_observation_m"])
        * float(matching_config["movement_uncertainty_fraction"]),
    )
    method = projection["projection_method"]
    if method == "SOG_COG_EXTRAPOLATION_SINGLE_SIDED":
        method_penalty = float(matching_config["single_sided_projection_penalty_m"])
    elif method == "NEAREST_OBSERVATION_NO_MOTION_MODEL":
        method_penalty = float(matching_config["no_motion_projection_penalty_m"])
    else:
        method_penalty = 0.0
    extent_allowance = _candidate_extent_allowance(
        candidate_properties,
        float(matching_config["candidate_extent_allowance_cap_m"]),
    )
    radius = min(
        maximum_distance,
        max(minimum_radius, minimum_radius + movement_component + method_penalty + extent_allowance),
    )
    return radius, {
        "minimum_radius_m": _rounded(minimum_radius, 2),
        "movement_uncertainty_m": _rounded(movement_component, 2),
        "projection_method_penalty_m": _rounded(method_penalty, 2),
        "candidate_extent_allowance_m": _rounded(extent_allowance, 2),
        "maximum_plausible_distance_m": _rounded(maximum_distance, 2),
        "resulting_dynamic_radius_m": _rounded(radius, 2),
        "experimental_formula": "clamp(minimum, maximum, minimum + 0.1*projected_motion_capped + method_penalty + half_candidate_diagonal_capped)",
    }


def _pair_evidence(
    candidate: dict[str, Any],
    projection: dict[str, Any],
    matching_config: dict[str, Any],
) -> dict[str, Any]:
    coordinates = candidate["geometry"]["coordinates"]
    distance = haversine_m(
        (float(coordinates[0]), float(coordinates[1])),
        (
            float(projection["projected_longitude"]),
            float(projection["projected_latitude"]),
        ),
    )
    radius, components = dynamic_radius(candidate["properties"], projection, matching_config)
    return {
        "mmsi": projection["mmsi"],
        "imo": projection.get("imo") or "",
        "name": projection.get("name") or "",
        "callsign": projection.get("callsign") or "",
        "ship_type_label": projection.get("ship_type_label") or "",
        "reported_length_m": projection.get("length_m"),
        "projected_position": {
            "longitude": projection["projected_longitude"],
            "latitude": projection["projected_latitude"],
            "time_utc": projection["projection_time_utc"],
        },
        "distance_to_sar_candidate_m": _rounded(distance, 1),
        "within_dynamic_radius": bool(distance <= radius),
        "within_maximum_plausible_distance": bool(
            distance <= float(matching_config["maximum_plausible_distance_m"])
        ),
        "dynamic_radius": components,
        "projection_method": projection["projection_method"],
        "projection_quality": projection["projection_quality"],
        "nearest_observation_gap_seconds": projection["nearest_observation_gap_seconds"],
        "bracket_span_seconds": projection["bracket_span_seconds"],
        "motion_distance_from_nearest_observation_m": projection[
            "motion_distance_from_nearest_observation_m"
        ],
        "effective_sog_knots": projection["effective_sog_knots"],
        "effective_cog_degrees": projection["effective_cog_degrees"],
        "input_observations": projection["input_observations"],
        "limitations": projection["limitations"],
    }


def match_candidates(
    candidate_payload: dict[str, Any],
    projections: list[dict[str, Any]],
    coverage: dict[str, Any],
    phase_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, list[str]]]:
    matching_config = phase_config["matching"]
    candidate_pairs: dict[str, list[dict[str, Any]]] = {}
    ais_to_candidates: dict[str, list[str]] = defaultdict(list)
    features = candidate_payload["features"]
    for candidate in features:
        identifier = candidate["properties"]["detection_id"]
        pairs = [
            _pair_evidence(candidate, projection, matching_config)
            for projection in projections
        ]
        pairs = [
            pair
            for pair in pairs
            if pair["within_maximum_plausible_distance"]
        ]
        pairs.sort(key=lambda pair: (float(pair["distance_to_sar_candidate_m"]), pair["mmsi"]))
        candidate_pairs[identifier] = pairs
        for pair in pairs:
            ais_to_candidates[pair["mmsi"]].append(identifier)

    counts: Counter[str] = Counter()
    result_features: list[dict[str, Any]] = []
    candidate_links_by_mmsi: dict[str, list[str]] = {
        mmsi: sorted(set(identifiers)) for mmsi, identifiers in ais_to_candidates.items()
    }
    for original in features:
        feature = copy.deepcopy(original)
        properties = feature["properties"]
        identifier = properties["detection_id"]
        pairs = candidate_pairs[identifier]
        ambiguity_reasons: list[str] = []
        selected_match: dict[str, Any] | None = None
        if not pairs:
            if coverage["sufficient_for_available_ais_comparison"]:
                match_status = "UNMATCHED_IN_AVAILABLE_AIS"
            else:
                match_status = "NO_AIS_COVERAGE"
        elif len(pairs) > 1:
            match_status = "AMBIGUOUS"
            ambiguity_reasons.append("MULTIPLE_PLAUSIBLE_AIS_VESSELS_FOR_ONE_SAR_CANDIDATE")
        else:
            pair = pairs[0]
            competing_candidates = candidate_links_by_mmsi.get(pair["mmsi"], [])
            if len(competing_candidates) > 1:
                match_status = "AMBIGUOUS"
                ambiguity_reasons.append("ONE_AIS_VESSEL_PLAUSIBLE_FOR_MULTIPLE_SAR_CANDIDATES")
            elif (
                coverage["sufficient_for_available_ais_comparison"]
                and pair["within_dynamic_radius"]
                and pair["projection_quality"] in {"HIGH", "MEDIUM"}
            ):
                match_status = "MATCHED"
                selected_match = pair
            else:
                match_status = "POSSIBLE_MATCH"
                selected_match = pair
        counts[match_status] += 1
        evidence_limit = 20
        historical_context = {
            "status": match_status,
            "source_provider": "ais_dk_historical",
            "source_archive_date": phase_config["source"]["archive_date"],
            "scene_acquisition_time_utc": phase_config["time_alignment"][
                "scene_acquisition_time_utc"
            ],
            "time_window_minutes": {
                "before": int(phase_config["time_alignment"]["window_before_minutes"]),
                "after": int(phase_config["time_alignment"]["window_after_minutes"]),
            },
            "matching_method": matching_config["method"],
            "parameters_are_experimental": True,
            "coverage_sufficient_for_available_ais_comparison": coverage[
                "sufficient_for_available_ais_comparison"
            ],
            "plausible_alternative_count": len(pairs),
            "plausible_alternatives_truncated": len(pairs) > evidence_limit,
            "plausible_alternatives": pairs[:evidence_limit],
            "selected_match": selected_match,
            "ambiguity_reasons": ambiguity_reasons,
            "candidate_competition": {
                pair["mmsi"]: candidate_links_by_mmsi.get(pair["mmsi"], [])
                for pair in pairs[:evidence_limit]
                if len(candidate_links_by_mmsi.get(pair["mmsi"], [])) > 1
            },
            "current_ais_used": False,
            "dark_vessel_claim": False,
            "assessment_limit": ASSESSMENT_LIMIT,
        }
        properties["previous_ais_context_status"] = properties.get("ais_context_status")
        properties["ais_context_status"] = match_status
        properties["ais_match_status"] = match_status
        properties["historical_ais_context"] = historical_context
        properties["current_ais_overlaid"] = False
        properties["dark_vessel_claim"] = False
        properties["candidate_is_not_vessel_classification"] = True
        properties["downstream_eligible"] = False
        result_features.append(feature)
    for state in ALLOWED_MATCH_STATES:
        counts.setdefault(state, 0)
    return result_features, dict(sorted(counts.items())), candidate_links_by_mmsi


def build_candidate_context_geojson(
    source_candidates: dict[str, Any],
    result_features: list[dict[str, Any]],
    status: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "Voodoo Whiskers Phase 4 SAR candidates with historical AIS context",
        "schema_version": "1.0.0",
        "generated_at": status["generated_at"],
        "metadata": {
            "phase": PHASE,
            "pilot_id": status["pilot_id"],
            "scene": status["scene"],
            "source_candidate_sha256": status["inputs"]["phase3_candidate_geojson_sha256"],
            "source_candidate_count": len(source_candidates["features"]),
            "historical_ais_source": status["archive"]["source"],
            "scene_acquisition_time_utc": EXPECTED_ACQUISITION,
            "coverage_assessment": status["coverage_assessment"],
            "match_counts": status["match_counts"],
            "allowed_match_states": sorted(ALLOWED_MATCH_STATES),
            "parameters_are_experimental": True,
            "current_ais_used": False,
            "current_ais_overlaid": False,
            "dark_vessel_claim": False,
            "candidate_is_not_vessel_classification": True,
            "public_layer": False,
            "method_note": MATCH_METHOD_NOTE,
            "assessment_limit": ASSESSMENT_LIMIT,
        },
        "features": result_features,
    }


def build_ais_window_geojson(
    projections: list[dict[str, Any]],
    candidate_links_by_mmsi: dict[str, list[str]],
    status: dict[str, Any],
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for projection in projections:
        mmsi = projection["mmsi"]
        properties = copy.deepcopy(projection)
        longitude = properties.pop("projected_longitude")
        latitude = properties.pop("projected_latitude")
        properties.update(
            {
                "feature_kind": "HISTORICAL_AIS_PROJECTED_POSITION",
                "historical_ais_only": True,
                "current_position": False,
                "plausible_for_candidate_ids": candidate_links_by_mmsi.get(mmsi, []),
                "assessment_limit": ASSESSMENT_LIMIT,
            }
        )
        features.append(
            {
                "type": "Feature",
                "id": f"AISDK-{mmsi}-{EXPECTED_ACQUISITION.replace(':', '').replace('-', '')}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [longitude, latitude],
                },
                "properties": properties,
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "Voodoo Whiskers Phase 4 projected historical AIS window",
        "schema_version": "1.0.0",
        "generated_at": status["generated_at"],
        "metadata": {
            "phase": PHASE,
            "pilot_id": status["pilot_id"],
            "scene": status["scene"],
            "historical": True,
            "coverage_mode": "historical_delayed_reception_dependent",
            "projection_time_utc": EXPECTED_ACQUISITION,
            "window_start_utc": status["extraction"]["window_start_utc"],
            "window_end_utc": status["extraction"]["window_end_utc"],
            "feature_count": len(features),
            "current_positions_included": False,
            "public_layer": False,
            "assessment_limit": ASSESSMENT_LIMIT,
        },
        "features": features,
    }


def _source_probe_metadata(source: Any) -> dict[str, Any]:
    return {
        "url": source.url,
        "filename": source.filename,
        "date_hint": source.date_hint,
        "etag": source.etag,
        "last_modified": source.last_modified,
        "content_length": source.content_length,
    }


def download_fixed_archive(
    session: Any,
    phase_config: dict[str, Any],
    directory: Path,
) -> tuple[Path, str, int, dict[str, Any]]:
    source_config = phase_config["source"]
    source = aisdk.probe_url(session, source_config["archive_url"])
    if source is None:
        raise catalog.PilotError("Fixed Danish historical AIS archive is unavailable")
    if source.filename != source_config["archive_filename"] or source.date_hint != source_config["archive_date"]:
        raise catalog.PilotError("Danish archive probe returned an unexpected file/date")
    if source.content_length not in (None, int(source_config["expected_download_bytes"])):
        raise catalog.PilotError("Danish archive Content-Length differs from the reviewed source receipt")
    target = directory / source_config["archive_filename"]
    digest = hashlib.sha256()
    total = 0
    try:
        with session.get(source.url, stream=True, allow_redirects=True, timeout=(30, 900)) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > int(source_config["maximum_download_bytes"]):
                        raise catalog.PilotError("Danish archive exceeded the Phase 4 download guard")
                    handle.write(chunk)
                    digest.update(chunk)
    except catalog.PilotError:
        raise
    except Exception as exc:
        raise catalog.PilotError(f"Danish archive download failed ({type(exc).__name__})") from exc
    if total != int(source_config["expected_download_bytes"]):
        raise catalog.PilotError("Downloaded Danish archive byte count differs from the reviewed receipt")
    sha256 = digest.hexdigest()
    if sha256 != source_config["expected_sha256"]:
        raise catalog.PilotError("Downloaded Danish archive SHA-256 differs from the reviewed receipt")
    return target, sha256, total, _source_probe_metadata(source)


def base_status(phase_config: dict[str, Any], pilot_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "generated_at": catalog.iso_z(catalog.utc_now()),
        "status": "error",
        "phase": PHASE,
        "pilot_id": pilot_config.get("pilot_id"),
        "pilot_name": pilot_config.get("name"),
        "scene": {
            "normalized_scene_id": EXPECTED_SCENE,
            "acquisition_time_utc": EXPECTED_ACQUISITION,
        },
        "inputs": {
            "phase3_candidate_path": str(DEFAULT_CANDIDATES.relative_to(ROOT)),
            "phase3_candidate_geojson_sha256": None,
            "phase3_candidate_count": None,
            "phase3_candidates_modified": False,
            "current_ais_files_read": False,
        },
        "archive": {
            "source": {
                "provider": (phase_config.get("source") or {}).get("provider"),
                "url": (phase_config.get("source") or {}).get("archive_url"),
                "filename": (phase_config.get("source") or {}).get("archive_filename"),
                "date": (phase_config.get("source") or {}).get("archive_date"),
                "timestamp_timezone_assumption": (phase_config.get("source") or {}).get(
                    "timestamp_timezone_assumption"
                ),
            },
            "probe": None,
            "download_performed": False,
            "download_bytes": None,
            "sha256": None,
            "temporary_archive_written": False,
            "temporary_archive_deleted": False,
            "raw_archive_persisted": False,
            "raw_csv_extracted_to_repository": False,
        },
        "time_alignment": phase_config.get("time_alignment"),
        "trajectory_parameters": phase_config.get("trajectory"),
        "matching_parameters": phase_config.get("matching"),
        "parameters_are_experimental": True,
        "method_note": MATCH_METHOD_NOTE,
        "reference_reading": [
            "https://www.mdpi.com/2072-4292/11/9/1078",
            "https://www.mdpi.com/2072-4292/13/1/104",
        ],
        "extraction": None,
        "coverage_assessment": None,
        "trajectory_projection": None,
        "match_counts": {state: 0 for state in sorted(ALLOWED_MATCH_STATES)},
        "outputs": {
            "candidate_context_geojson": None,
            "historical_ais_window_geojson": None,
        },
        "historical_ais_only": True,
        "current_ais_used": False,
        "current_ais_overlaid": False,
        "dark_vessel_claim": False,
        "sar_candidates_classified_as_vessels": False,
        "public_layer_modified": False,
        "existing_ais_products_modified": False,
        "gfw_products_modified": False,
        "magic_paws_modified": False,
        "production_ready": False,
        "assessment_limit": ASSESSMENT_LIMIT,
        "next_phase": "analyst_review_queue_and_quality_model",
        "errors": [],
    }


def run_phase4(
    phase_config: dict[str, Any],
    pilot_config: dict[str, Any],
    candidate_payload: dict[str, Any],
    *,
    session: Any | None = None,
    archive_bytes: bytes | None = None,
    candidate_source_bytes: bytes | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, bool]:
    status = base_status(phase_config, pilot_config)
    temporary_archive: Path | None = None
    try:
        validate_phase_config(phase_config, pilot_config)
        validate_candidate_input(candidate_payload, pilot_config)
        candidate_bytes = candidate_source_bytes or (
            json.dumps(candidate_payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if candidate_source_bytes is not None:
            try:
                hashed_payload = json.loads(candidate_source_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise catalog.PilotError("Phase 3 candidate source bytes are not valid UTF-8 JSON") from exc
            if hashed_payload != candidate_payload:
                raise catalog.PilotError("Phase 3 candidate source bytes do not match the parsed input")
        status["inputs"].update(
            {
                "phase3_candidate_geojson_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                "phase3_candidate_count": len(candidate_payload["features"]),
            }
        )
        with tempfile.TemporaryDirectory(prefix="voodoo-sar-ais-phase4-") as temporary_directory:
            directory = Path(temporary_directory)
            if archive_bytes is None:
                http = session or aisdk.build_session()
                if hasattr(http, "headers"):
                    http.headers.update({"User-Agent": "MOwlSINT-VoodooWhiskers-SAR-AIS-Phase4/1.0"})
                temporary_archive, sha256, download_bytes, probe = download_fixed_archive(
                    http, phase_config, directory
                )
            else:
                temporary_archive = directory / phase_config["source"]["archive_filename"]
                temporary_archive.write_bytes(archive_bytes)
                sha256 = hashlib.sha256(archive_bytes).hexdigest()
                download_bytes = len(archive_bytes)
                if sha256 != phase_config["source"]["expected_sha256"]:
                    raise catalog.PilotError("Injected historical AIS archive SHA-256 differs from configuration")
                if download_bytes != int(phase_config["source"]["expected_download_bytes"]):
                    raise catalog.PilotError("Injected historical AIS archive byte count differs from configuration")
                probe = {
                    "url": phase_config["source"]["archive_url"],
                    "filename": phase_config["source"]["archive_filename"],
                    "date_hint": phase_config["source"]["archive_date"],
                    "etag": "unit-test-injected" if archive_bytes is not None else None,
                    "last_modified": None,
                    "content_length": download_bytes,
                }
            status["archive"].update(
                {
                    "probe": probe,
                    "download_performed": True,
                    "download_bytes": download_bytes,
                    "sha256": sha256,
                    "temporary_archive_written": True,
                }
            )
            buckets, coverage, extraction = extract_historical_window(
                temporary_archive, phase_config, pilot_config
            )
            projections, projection_status = build_projections(
                buckets, phase_config, pilot_config
            )
            result_features, match_counts, candidate_links = match_candidates(
                candidate_payload, projections, coverage, phase_config
            )
            status["extraction"] = extraction
            status["coverage_assessment"] = coverage
            status["trajectory_projection"] = projection_status
            status["match_counts"] = match_counts

        status["archive"]["temporary_archive_deleted"] = (
            temporary_archive is not None and not temporary_archive.exists()
        )
        if not status["archive"]["temporary_archive_deleted"]:
            raise catalog.PilotError("Temporary Phase 4 AIS archive cleanup could not be verified")

        candidate_context = build_candidate_context_geojson(
            candidate_payload, result_features, status
        )
        ais_window = build_ais_window_geojson(projections, candidate_links, status)
        candidate_output_bytes = (
            json.dumps(candidate_context, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        ais_window_bytes = (
            json.dumps(ais_window, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if len(candidate_output_bytes) > 5 * 1024 * 1024:
            raise catalog.PilotError("Phase 4 candidate context exceeds 5 MiB")
        if len(ais_window_bytes) > 5 * 1024 * 1024:
            raise catalog.PilotError("Phase 4 historical AIS window exceeds 5 MiB")
        status["outputs"] = {
            "candidate_context_geojson": {
                "path": str(DEFAULT_CONTEXT.relative_to(ROOT)),
                "bytes": len(candidate_output_bytes),
                "sha256": hashlib.sha256(candidate_output_bytes).hexdigest(),
                "candidate_count": len(result_features),
                "public_layer": False,
            },
            "historical_ais_window_geojson": {
                "path": str(DEFAULT_AIS_WINDOW.relative_to(ROOT)),
                "bytes": len(ais_window_bytes),
                "sha256": hashlib.sha256(ais_window_bytes).hexdigest(),
                "projected_vessel_count": len(projections),
                "historical_only": True,
                "public_layer": False,
            },
        }
        status["status"] = "ok"
        return status, candidate_context, ais_window, True
    except catalog.PilotError as exc:
        if temporary_archive is not None and not temporary_archive.exists():
            status["archive"]["temporary_archive_deleted"] = True
        status["errors"] = [str(exc)]
        return status, None, None, False
    except Exception as exc:
        if temporary_archive is not None and not temporary_archive.exists():
            status["archive"]["temporary_archive_deleted"] = True
        status["errors"] = [f"Unexpected Phase 4 processing failure ({type(exc).__name__})"]
        return status, None, None, False


def resolve_pilot_config(phase_config: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override
    return _repo_path(phase_config.get("pilot_config"), DEFAULT_PILOT_CONFIG)


def resolve_candidate_input(phase_config: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override
    return _repo_path(phase_config.get("candidate_input"), DEFAULT_CANDIDATES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pilot-config", type=Path)
    parser.add_argument("--candidate-input", type=Path)
    parser.add_argument("--context-output", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--ais-window-output", type=Path, default=DEFAULT_AIS_WINDOW)
    parser.add_argument("--status-output", type=Path, default=DEFAULT_STATUS)
    return parser.parse_args()


def _require_fixed_output(path: Path, expected: Path) -> None:
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError as exc:
        raise catalog.PilotError("Phase 4 output path escapes the repository root") from exc
    _repo_path(str(relative), expected)


def main() -> int:
    args = parse_args()
    phase_config: dict[str, Any] = {"phase": PHASE}
    pilot_config: dict[str, Any] = {}
    candidate_payload: dict[str, Any] = {}
    candidate_source_bytes: bytes | None = None
    try:
        phase_config = catalog.read_json(args.config)
        pilot_config = catalog.read_json(resolve_pilot_config(phase_config, args.pilot_config))
        candidate_path = resolve_candidate_input(phase_config, args.candidate_input)
        candidate_payload = catalog.read_json(candidate_path)
        try:
            candidate_source_bytes = candidate_path.read_bytes()
        except OSError as exc:
            raise catalog.PilotError("Phase 3 candidate input could not be read for hashing") from exc
        _require_fixed_output(args.context_output, DEFAULT_CONTEXT)
        _require_fixed_output(args.ais_window_output, DEFAULT_AIS_WINDOW)
        _require_fixed_output(args.status_output, DEFAULT_STATUS)
    except catalog.PilotError as exc:
        status = base_status(phase_config, pilot_config)
        status["errors"] = [str(exc)]
        catalog.atomic_json(args.status_output, status)
        print(json.dumps({"status": "error", "phase": PHASE, "errors": status["errors"]}))
        return 1

    status, candidate_context, ais_window, ok = run_phase4(
        phase_config,
        pilot_config,
        candidate_payload,
        candidate_source_bytes=candidate_source_bytes,
    )
    if ok and candidate_context is not None and ais_window is not None:
        catalog.atomic_json(args.context_output, candidate_context)
        catalog.atomic_json(args.ais_window_output, ais_window)
    catalog.atomic_json(args.status_output, status)
    print(
        json.dumps(
            {
                "status": status["status"],
                "phase": status["phase"],
                "pilot_id": status["pilot_id"],
                "scene_acquisition_time_utc": EXPECTED_ACQUISITION,
                "archive_deleted": status["archive"]["temporary_archive_deleted"],
                "coverage": (status.get("coverage_assessment") or {}).get("status"),
                "match_counts": status["match_counts"],
                "current_ais_used": status["current_ais_used"],
                "errors": status["errors"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
