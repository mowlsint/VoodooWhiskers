#!/usr/bin/env python3
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import sys
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import copernicus_sar_ais_match as phase4  # noqa: E402
import copernicus_sar_catalog as catalog  # noqa: E402
import fetch_aisdk_historical as aisdk  # noqa: E402


PILOT_CONFIG = json.loads(
    (ROOT / "config/sar_copernicus_pilot.json").read_text(encoding="utf-8")
)
PHASE4_CONFIG = json.loads(
    (ROOT / "config/sar_copernicus_ais_match.json").read_text(encoding="utf-8")
)


def candidate_payload(points: list[tuple[str, float, float]]) -> dict[str, Any]:
    features = []
    for identifier, longitude, latitude in points:
        features.append(
            {
                "type": "Feature",
                "id": identifier,
                "geometry": {
                    "type": "Point",
                    "coordinates": [longitude, latitude],
                },
                "properties": {
                    "detection_id": identifier,
                    "candidate_status": "UNREVIEWED_SAR_CANDIDATE",
                    "classification": "UNCLASSIFIED_BRIGHT_RETURN",
                    "candidate_is_not_vessel_classification": True,
                    "human_visual_review_required": True,
                    "downstream_eligible": False,
                    "ais_context_status": "NOT_CHECKED",
                    "ais_match_status": "NOT_ASSESSED",
                    "current_ais_overlaid": False,
                    "dark_vessel_claim": False,
                    "signature_measurements": {
                        "approximate_axis_aligned_signature_span_m": {
                            "x": 20.0,
                            "y": 40.0,
                        }
                    },
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "schema_version": "1.0.0",
        "metadata": {
            "phase": "single_scene_cfar_parameter_comparison",
            "pilot_id": PILOT_CONFIG["pilot_id"],
            "scene": {
                "normalized_scene_id": phase4.EXPECTED_SCENE,
                "datetime": phase4.EXPECTED_ACQUISITION,
            },
            "ais_context_status": "NOT_CHECKED",
            "current_ais_overlaid": False,
            "dark_vessel_claim": False,
            "visual_review_required": True,
            "public_layer": False,
        },
        "features": features,
    }


def archive_row(
    observed: datetime,
    mmsi: str,
    longitude: float,
    latitude: float,
    *,
    sog: float | None = 8.0,
    cog: float | None = 90.0,
    mobile_type: str = "Class A",
) -> list[str]:
    values = {column: "" for column in aisdk.COLUMNS}
    values.update(
        {
            "timestamp": observed.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M:%S"),
            "type_of_mobile": mobile_type,
            "mmsi": mmsi,
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "navigational_status": "Under way using engine",
            "sog": "" if sog is None else str(sog),
            "cog": "" if cog is None else str(cog),
            "heading": "90",
            "imo": "1234567",
            "callsign": f"T{mmsi[-5:]}",
            "name": f"TEST {mmsi}",
            "ship_type": "Cargo",
            "width": "18",
            "length": "120",
            "position_fixing_device": "GPS",
            "data_source_type": "AIS",
        }
    )
    return [values[column] for column in aisdk.COLUMNS]


def zip_archive(rows: list[list[str]]) -> bytes:
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer)
    writer.writerow(aisdk.COLUMNS)
    writer.writerows(rows)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("aisdk-2026-08-16.csv", csv_buffer.getvalue().encode("utf-8"))
    return archive_buffer.getvalue()


def config_for_archive(archive: bytes) -> dict[str, Any]:
    config = copy.deepcopy(PHASE4_CONFIG)
    config["source"]["expected_sha256"] = hashlib.sha256(archive).hexdigest()
    config["source"]["expected_download_bytes"] = len(archive)
    config["source"]["maximum_download_bytes"] = max(
        len(archive), int(config["source"]["maximum_download_bytes"])
    )
    return config


def direct_observation(
    mmsi: str,
    observed: datetime,
    longitude: float,
    latitude: float,
    *,
    sog: float | None = 10.0,
    cog: float | None = 90.0,
) -> dict[str, Any]:
    return {
        "mmsi": mmsi,
        "imo": "1234567",
        "callsign": "UNIT",
        "name": "UNIT TEST",
        "target_type": "Class A",
        "ship_type_label": "Cargo",
        "length_m": 100.0,
        "width_m": 15.0,
        "destination": "TEST",
        "latitude": latitude,
        "longitude": longitude,
        "navigational_status": "Under way using engine",
        "sog": sog,
        "cog": cog,
        "true_heading": 90,
        "observed_at": observed.isoformat(),
        "_observed_dt": observed,
    }


def projected_vessel(
    mmsi: str,
    longitude: float,
    latitude: float,
    *,
    quality: str = "HIGH",
    method: str = "EXACT_ARCHIVE_OBSERVATION",
) -> dict[str, Any]:
    return {
        "mmsi": mmsi,
        "imo": "1234567",
        "callsign": "UNIT",
        "name": f"TEST {mmsi}",
        "ship_type_label": "Cargo",
        "length_m": 100.0,
        "projected_longitude": longitude,
        "projected_latitude": latitude,
        "projection_time_utc": phase4.EXPECTED_ACQUISITION,
        "projection_method": method,
        "projection_quality": quality,
        "nearest_observation_gap_seconds": 0,
        "bracket_span_seconds": None,
        "motion_distance_from_nearest_observation_m": 0.0,
        "effective_sog_knots": 0.0,
        "effective_cog_degrees": 0.0,
        "input_observations": [],
        "limitations": ["SAR_AZIMUTH_DOPPLER_SHIFT_NOT_CORRECTED"],
    }


def sufficient_coverage() -> dict[str, Any]:
    return {"sufficient_for_available_ais_comparison": True}


class CopernicusSarHistoricalAisMatchTests(unittest.TestCase):
    def test_reviewed_configuration_is_historical_only_and_fails_closed(self) -> None:
        phase4.validate_phase_config(PHASE4_CONFIG, PILOT_CONFIG)
        self.assertEqual(PHASE4_CONFIG["time_alignment"]["window_before_minutes"], 30)
        self.assertEqual(PHASE4_CONFIG["time_alignment"]["window_after_minutes"], 30)
        self.assertEqual(PHASE4_CONFIG["matching"]["minimum_dynamic_radius_m"], 500.0)
        self.assertEqual(PHASE4_CONFIG["matching"]["maximum_plausible_distance_m"], 2000.0)
        self.assertTrue(PHASE4_CONFIG["guardrails"]["use_historical_ais_only"])
        self.assertFalse(PHASE4_CONFIG["guardrails"]["overlay_current_ais"])
        self.assertFalse(PHASE4_CONFIG["guardrails"]["claim_dark_vessel"])

        for key, unsafe_value in (
            ("overlay_current_ais", True),
            ("claim_dark_vessel", True),
            ("modify_existing_ais_products", True),
        ):
            changed = copy.deepcopy(PHASE4_CONFIG)
            changed["guardrails"][key] = unsafe_value
            with self.assertRaises(catalog.PilotError):
                phase4.validate_phase_config(changed, PILOT_CONFIG)

    def test_phase3_input_must_be_the_exact_unreviewed_scene(self) -> None:
        payload = candidate_payload([("CAND-1", 12.1, 54.55)])
        phase4.validate_candidate_input(payload, PILOT_CONFIG)
        changed = copy.deepcopy(payload)
        changed["metadata"]["scene"]["normalized_scene_id"] = "S1C_OTHER_SCENE"
        with self.assertRaises(catalog.PilotError):
            phase4.validate_candidate_input(changed, PILOT_CONFIG)
        changed = copy.deepcopy(payload)
        changed["features"][0]["properties"]["candidate_status"] = "VESSEL"
        with self.assertRaises(catalog.PilotError):
            phase4.validate_candidate_input(changed, PILOT_CONFIG)

    def test_projection_prefers_exact_then_bracketing_and_uses_bounded_fallbacks(self) -> None:
        scene_time = catalog.parse_utc(phase4.EXPECTED_ACQUISITION, "scene")
        exact = direct_observation("111111111", scene_time, 12.1, 54.55)
        projection = phase4.project_trajectory(
            "111111111",
            {"before": {scene_time.isoformat(): exact}, "after": {}, "source_messages": 1},
            scene_time,
            PHASE4_CONFIG["trajectory"],
        )
        self.assertEqual(projection["projection_method"], "EXACT_ARCHIVE_OBSERVATION")
        self.assertEqual(projection["projection_quality"], "HIGH")
        self.assertEqual(projection["projected_longitude"], 12.1)

        before_time = scene_time - timedelta(minutes=5)
        after_time = scene_time + timedelta(minutes=5)
        before = direct_observation("222222222", before_time, 12.0, 54.55)
        after = direct_observation("222222222", after_time, 12.002, 54.55)
        projection = phase4.project_trajectory(
            "222222222",
            {
                "before": {before_time.isoformat(): before},
                "after": {after_time.isoformat(): after},
                "source_messages": 2,
            },
            scene_time,
            PHASE4_CONFIG["trajectory"],
        )
        self.assertEqual(projection["projection_method"], "LINEAR_INTERPOLATION_BRACKETED")
        self.assertEqual(projection["projection_quality"], "HIGH")
        self.assertAlmostEqual(projection["projected_longitude"], 12.001, places=6)

        earlier = scene_time - timedelta(seconds=60)
        single = direct_observation("333333333", earlier, 12.0, 54.55, sog=10.0, cog=90.0)
        projection = phase4.project_trajectory(
            "333333333",
            {"before": {earlier.isoformat(): single}, "after": {}, "source_messages": 1},
            scene_time,
            PHASE4_CONFIG["trajectory"],
        )
        self.assertEqual(projection["projection_method"], "SOG_COG_EXTRAPOLATION_SINGLE_SIDED")
        self.assertEqual(projection["projection_quality"], "MEDIUM")
        self.assertAlmostEqual(projection["motion_distance_from_nearest_observation_m"], 308.67, places=1)

        no_motion = direct_observation("444444444", earlier, 12.0, 54.55, sog=None, cog=None)
        projection = phase4.project_trajectory(
            "444444444",
            {"before": {earlier.isoformat(): no_motion}, "after": {}, "source_messages": 1},
            scene_time,
            PHASE4_CONFIG["trajectory"],
        )
        self.assertEqual(projection["projection_method"], "NEAREST_OBSERVATION_NO_MOTION_MODEL")
        self.assertEqual(projection["projection_quality"], "LOW")

    def test_matching_keeps_unique_possible_ambiguous_unmatched_and_no_coverage_distinct(self) -> None:
        payload = candidate_payload(
            [
                ("CAND-MATCHED", 12.0, 54.50),
                ("CAND-POSSIBLE", 12.10, 54.50),
                ("CAND-AMB-A", 12.20, 54.60),
                ("CAND-AMB-B", 12.202, 54.60),
                ("CAND-UNMATCHED", 11.96, 54.65),
            ]
        )
        possible_position = phase4.destination_point((12.10, 54.50), 0.0, 1500.0)
        projections = [
            projected_vessel("111111111", 12.001, 54.50),
            projected_vessel("222222222", *possible_position),
            projected_vessel("333333333", 12.201, 54.60),
        ]
        features, counts, links = phase4.match_candidates(
            payload, projections, sufficient_coverage(), PHASE4_CONFIG
        )
        states = {
            feature["id"]: feature["properties"]["ais_match_status"] for feature in features
        }
        self.assertEqual(states["CAND-MATCHED"], "MATCHED")
        self.assertEqual(states["CAND-POSSIBLE"], "POSSIBLE_MATCH")
        self.assertEqual(states["CAND-AMB-A"], "AMBIGUOUS")
        self.assertEqual(states["CAND-AMB-B"], "AMBIGUOUS")
        self.assertEqual(states["CAND-UNMATCHED"], "UNMATCHED_IN_AVAILABLE_AIS")
        self.assertEqual(counts["MATCHED"], 1)
        self.assertEqual(counts["POSSIBLE_MATCH"], 1)
        self.assertEqual(counts["AMBIGUOUS"], 2)
        self.assertEqual(counts["UNMATCHED_IN_AVAILABLE_AIS"], 1)
        self.assertEqual(links["333333333"], ["CAND-AMB-A", "CAND-AMB-B"])

        no_coverage_payload = candidate_payload([("CAND-NO-COVERAGE", 12.24, 54.49)])
        no_coverage, no_coverage_counts, _ = phase4.match_candidates(
            no_coverage_payload,
            [],
            {"sufficient_for_available_ais_comparison": False},
            PHASE4_CONFIG,
        )
        self.assertEqual(
            no_coverage[0]["properties"]["ais_match_status"], "NO_AIS_COVERAGE"
        )
        self.assertEqual(no_coverage_counts["NO_AIS_COVERAGE"], 1)

        sparse_with_pair, sparse_counts, _ = phase4.match_candidates(
            candidate_payload([("CAND-SPARSE-PAIR", 12.0, 54.50)]),
            [projected_vessel("111111111", 12.001, 54.50)],
            {"sufficient_for_available_ais_comparison": False},
            PHASE4_CONFIG,
        )
        self.assertEqual(
            sparse_with_pair[0]["properties"]["ais_match_status"], "POSSIBLE_MATCH"
        )
        self.assertEqual(sparse_counts["MATCHED"], 0)
        self.assertEqual(sparse_counts["POSSIBLE_MATCH"], 1)

    def test_full_injected_archive_is_streamed_projected_matched_and_deleted(self) -> None:
        scene_time = catalog.parse_utc(phase4.EXPECTED_ACQUISITION, "scene")
        times = [
            datetime(2026, 8, 16, 4, 55, tzinfo=timezone.utc),
            datetime(2026, 8, 16, 5, 5, tzinfo=timezone.utc),
            datetime(2026, 8, 16, 5, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 16, 5, 25, tzinfo=timezone.utc),
            datetime(2026, 8, 16, 5, 35, tzinfo=timezone.utc),
            datetime(2026, 8, 16, 5, 45, tzinfo=timezone.utc),
        ]
        tracks = {
            "111111111": (12.129, 54.56),
            "222222222": (11.98, 54.50),
            "333333333": (12.24, 54.64),
            "444444444": (12.24, 54.49),
            "555555555": (11.97, 54.64),
        }
        rows: list[list[str]] = []
        for mmsi, (longitude, latitude) in tracks.items():
            for observed in times:
                track_longitude = longitude
                if mmsi == "111111111" and observed > scene_time:
                    track_longitude = 12.131
                rows.append(archive_row(observed, mmsi, track_longitude, latitude))
        rows.append(
            archive_row(
                datetime(2026, 8, 16, 3, 0, tzinfo=timezone.utc),
                "999999999",
                12.1,
                54.55,
            )
        )
        archive = zip_archive(rows)
        config = config_for_archive(archive)
        payload = candidate_payload([("CAND-FULL", 12.130807, 54.56)])
        source_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )

        status, context, ais_window, ok = phase4.run_phase4(
            config,
            PILOT_CONFIG,
            payload,
            archive_bytes=archive,
            candidate_source_bytes=source_bytes,
        )
        self.assertTrue(ok, status.get("errors"))
        self.assertIsNotNone(context)
        self.assertIsNotNone(ais_window)
        assert context is not None
        assert ais_window is not None
        self.assertEqual(status["status"], "ok")
        self.assertEqual(
            status["inputs"]["phase3_candidate_geojson_sha256"],
            hashlib.sha256(source_bytes).hexdigest(),
        )
        self.assertTrue(status["archive"]["temporary_archive_written"])
        self.assertTrue(status["archive"]["temporary_archive_deleted"])
        self.assertFalse(status["archive"]["raw_archive_persisted"])
        self.assertFalse(status["archive"]["raw_csv_extracted_to_repository"])
        self.assertEqual(status["extraction"]["window_duration_minutes"], 60)
        self.assertGreater(status["extraction"]["counters"]["outside_time_window"], 0)
        self.assertTrue(
            status["coverage_assessment"]["sufficient_for_available_ais_comparison"]
        )
        self.assertEqual(status["coverage_assessment"]["distinct_valid_messages"], 30)
        self.assertEqual(status["coverage_assessment"]["occupied_bin_count"], 6)
        self.assertEqual(status["match_counts"]["MATCHED"], 1)
        candidate = context["features"][0]["properties"]
        self.assertEqual(candidate["ais_match_status"], "MATCHED")
        self.assertFalse(candidate["current_ais_overlaid"])
        self.assertFalse(candidate["dark_vessel_claim"])
        selected = candidate["historical_ais_context"]["selected_match"]
        self.assertEqual(selected["mmsi"], "111111111")
        self.assertEqual(selected["projected_position"]["time_utc"], phase4.EXPECTED_ACQUISITION)
        self.assertLess(selected["distance_to_sar_candidate_m"], 5.0)
        self.assertTrue(selected["within_dynamic_radius"])
        self.assertTrue(ais_window["metadata"]["historical"])
        self.assertFalse(ais_window["metadata"]["current_positions_included"])
        self.assertEqual(len(ais_window["features"]), 5)
        self.assertTrue(
            all(
                feature["properties"]["historical_ais_only"] is True
                and feature["properties"]["current_position"] is False
                for feature in ais_window["features"]
            )
        )

        context_bytes = (json.dumps(context, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        window_bytes = (json.dumps(ais_window, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        self.assertEqual(
            status["outputs"]["candidate_context_geojson"]["sha256"],
            hashlib.sha256(context_bytes).hexdigest(),
        )
        self.assertEqual(
            status["outputs"]["historical_ais_window_geojson"]["sha256"],
            hashlib.sha256(window_bytes).hexdigest(),
        )

    def test_empty_local_archive_produces_no_coverage_not_unmatched(self) -> None:
        archive = zip_archive([])
        config = config_for_archive(archive)
        payload = candidate_payload([("CAND-NO-COVERAGE", 12.1, 54.55)])
        status, context, ais_window, ok = phase4.run_phase4(
            config,
            PILOT_CONFIG,
            payload,
            archive_bytes=archive,
        )
        self.assertTrue(ok, status.get("errors"))
        assert context is not None
        assert ais_window is not None
        self.assertEqual(status["coverage_assessment"]["status"], "NO_AIS_COVERAGE")
        self.assertEqual(status["match_counts"]["NO_AIS_COVERAGE"], 1)
        self.assertEqual(status["match_counts"]["UNMATCHED_IN_AVAILABLE_AIS"], 0)
        self.assertEqual(
            context["features"][0]["properties"]["ais_match_status"],
            "NO_AIS_COVERAGE",
        )
        self.assertEqual(ais_window["features"], [])
        self.assertTrue(status["archive"]["temporary_archive_deleted"])

    def test_candidate_hash_audit_rejects_mismatched_source_bytes(self) -> None:
        archive = zip_archive([])
        config = config_for_archive(archive)
        payload = candidate_payload([("CAND-HASH", 12.1, 54.55)])
        status, context, ais_window, ok = phase4.run_phase4(
            config,
            PILOT_CONFIG,
            payload,
            archive_bytes=archive,
            candidate_source_bytes=b'{"different":true}',
        )
        self.assertFalse(ok)
        self.assertIsNone(context)
        self.assertIsNone(ais_window)
        self.assertIn("do not match", status["errors"][0])


if __name__ == "__main__":
    unittest.main()
