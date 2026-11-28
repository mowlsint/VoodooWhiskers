#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import copernicus_sar_catalog as catalog  # noqa: E402
import copernicus_sar_review_queue as review  # noqa: E402


REVIEW_CONFIG = json.loads(
    (ROOT / "config/sar_copernicus_review.json").read_text(encoding="utf-8")
)


CANDIDATE_SPECS = {
    "SARCAND-20260816T052402Z-0001": {
        "coordinates": [12.2470677, 54.5483816],
        "state": "AMBIGUOUS",
        "mmsi": "314115000",
        "name": "LUNA PELAGOS",
        "length": 243.0,
        "projected": [12.2470215, 54.5483214],
        "distance": 7.3,
        "radius": 550.97,
        "cog": 200.82,
        "sog": 11.23,
        "gap": 3,
        "bracket": 11,
        "profiles": 3,
        "consensus": "ALL_PROFILES",
        "vh": True,
        "vv_db": 16.4689,
        "vh_db": -0.2463,
        "span": {"x": 40.0, "y": 89.98},
        "within": True,
    },
    "SARCAND-20260816T052402Z-0002": {
        "coordinates": [12.2278267, 54.5200815],
        "state": "AMBIGUOUS",
        "mmsi": "352005354",
        "name": "TANGO 2",
        "length": 244.0,
        "projected": [12.2276844, 54.5200869],
        "distance": 9.2,
        "radius": 557.97,
        "cog": 197.36,
        "sog": 10.13,
        "gap": 4,
        "bracket": 11,
        "profiles": 3,
        "consensus": "ALL_PROFILES",
        "vh": True,
        "vv_db": 15.3826,
        "vh_db": 0.7233,
        "span": {"x": 49.99, "y": 99.98},
        "within": True,
    },
    "SARCAND-20260816T052402Z-0003": {
        "coordinates": [11.9568227, 54.4844232],
        "state": "MATCHED",
        "mmsi": "211727510",
        "name": "BERLIN",
        "length": 170.0,
        "projected": [11.9559113, 54.4822503],
        "distance": 248.7,
        "radius": 586.95,
        "cog": 346.37,
        "sog": 14.83,
        "gap": 2,
        "bracket": 6,
        "profiles": 3,
        "consensus": "ALL_PROFILES",
        "vh": True,
        "vv_db": 14.5788,
        "vh_db": 1.4161,
        "span": {"x": 59.99, "y": 159.96},
        "within": True,
    },
    "SARCAND-20260816T052402Z-0004": {
        "coordinates": [12.2455447, 54.5466323],
        "state": "AMBIGUOUS",
        "mmsi": "314115000",
        "name": "LUNA PELAGOS",
        "length": 243.0,
        "projected": [12.2470215, 54.5483214],
        "distance": 210.6,
        "radius": 524.09,
        "cog": 200.82,
        "sog": 11.23,
        "gap": 3,
        "bracket": 11,
        "profiles": 3,
        "consensus": "ALL_PROFILES",
        "vh": False,
        "vv_db": 6.5439,
        "vh_db": -6.4124,
        "span": {"x": 20.0, "y": 39.99},
        "within": True,
    },
    "SARCAND-20260816T052402Z-0005": {
        "coordinates": [12.1302557, 54.5668575],
        "state": "POSSIBLE_MATCH",
        "mmsi": "219022256",
        "name": "DANPILOT ALFA",
        "length": 20.0,
        "projected": [12.1298723, 54.560182],
        "distance": 742.7,
        "radius": 516.2,
        "cog": 260.68,
        "sog": 20.02,
        "gap": 2,
        "bracket": 6,
        "profiles": 2,
        "consensus": "MULTI_PROFILE",
        "vh": True,
        "vv_db": -4.22,
        "vh_db": -13.8401,
        "span": {"x": 20.0, "y": 20.0},
        "within": False,
    },
    "SARCAND-20260816T052402Z-0006": {
        "coordinates": [12.226891, 54.5183467],
        "state": "AMBIGUOUS",
        "mmsi": "352005354",
        "name": "TANGO 2",
        "length": 244.0,
        "projected": [12.2276844, 54.5200869],
        "distance": 200.2,
        "radius": 513.26,
        "cog": 197.36,
        "sog": 10.13,
        "gap": 4,
        "bracket": 11,
        "profiles": 2,
        "consensus": "MULTI_PROFILE",
        "vh": True,
        "vv_db": 5.8991,
        "vh_db": -2.0006,
        "span": {"x": 20.0, "y": 10.0},
        "within": True,
    },
}


VESSEL_DETAILS = {
    "314115000": {
        "imo": "9276597",
        "callsign": "8PPC",
        "ship_type": "Tanker",
        "width": 42.0,
        "destination": "SINGAPORE",
    },
    "352005354": {
        "imo": "9389071",
        "callsign": "3E8640",
        "ship_type": "Tanker",
        "width": 42.0,
        "destination": "FOR ORDER",
    },
    "211727510": {
        "imo": "9587855",
        "callsign": "DKDF2",
        "ship_type": "Passenger",
        "width": 27.0,
        "destination": "GED - ROS - GED",
    },
    "219022256": {
        "imo": "9812030",
        "callsign": "OXEB2",
        "ship_type": "Pilot",
        "width": 6.0,
        "destination": "GEDSER",
    },
}


def alternative(spec: dict[str, Any]) -> dict[str, Any]:
    details = VESSEL_DETAILS[spec["mmsi"]]
    return {
        "mmsi": spec["mmsi"],
        "imo": details["imo"],
        "name": spec["name"],
        "callsign": details["callsign"],
        "ship_type_label": details["ship_type"],
        "reported_length_m": spec["length"],
        "projected_position": {
            "longitude": spec["projected"][0],
            "latitude": spec["projected"][1],
            "time_utc": review.EXPECTED_ACQUISITION_START,
        },
        "distance_to_sar_candidate_m": spec["distance"],
        "within_dynamic_radius": spec["within"],
        "within_maximum_plausible_distance": True,
        "dynamic_radius": {
            "resulting_dynamic_radius_m": spec["radius"],
        },
        "projection_method": "LINEAR_INTERPOLATION_BRACKETED",
        "projection_quality": "HIGH",
        "nearest_observation_gap_seconds": spec["gap"],
        "bracket_span_seconds": spec["bracket"],
        "effective_sog_knots": spec["sog"],
        "effective_cog_degrees": spec["cog"],
        "limitations": ["SAR_AZIMUTH_DOPPLER_SHIFT_NOT_CORRECTED"],
    }


def candidate_feature(identifier: str, spec: dict[str, Any]) -> dict[str, Any]:
    alt = alternative(spec)
    return {
        "type": "Feature",
        "id": identifier,
        "geometry": {"type": "Point", "coordinates": spec["coordinates"]},
        "properties": {
            "feature_kind": "SAR_CANDIDATE",
            "candidate_status": "UNREVIEWED_SAR_CANDIDATE",
            "classification": "UNCLASSIFIED_BRIGHT_RETURN",
            "candidate_is_not_vessel_classification": True,
            "downstream_eligible": False,
            "detection_id": identifier,
            "ais_match_status": spec["state"],
            "current_ais_overlaid": False,
            "dark_vessel_claim": False,
            "detection_method": {
                "profile_count": spec["profiles"],
                "parameter_consensus": spec["consensus"],
                "vh_cfar_corroborated": spec["vh"],
            },
            "signature_measurements": {
                "vv_peak_db": spec["vv_db"],
                "vh_at_vv_peak_db": spec["vh_db"],
                "approximate_axis_aligned_signature_span_m": spec["span"],
                "span_is_not_vessel_length": True,
            },
            "spatial_quality": {
                "quality_flags": ["COARSE_LAND_MASK_APPLIED"],
            },
            "infrastructure_context": {
                "near_infrastructure_reference": False,
                "nearest_distance_nm": 3.0,
            },
            "historical_ais_context": {
                "status": spec["state"],
                "plausible_alternatives": [alt],
            },
        },
    }


def projection_feature(mmsi: str, spec: dict[str, Any]) -> dict[str, Any]:
    details = VESSEL_DETAILS[mmsi]
    return {
        "type": "Feature",
        "id": f"AISDK-{mmsi}-20260816T052402Z",
        "geometry": {"type": "Point", "coordinates": spec["projected"]},
        "properties": {
            "mmsi": mmsi,
            "imo": details["imo"],
            "callsign": details["callsign"],
            "name": spec["name"],
            "ship_type_label": details["ship_type"],
            "length_m": spec["length"],
            "width_m": details["width"],
            "destination": details["destination"],
            "projection_time_utc": review.EXPECTED_ACQUISITION_START,
            "projection_method": "LINEAR_INTERPOLATION_BRACKETED",
            "projection_quality": "HIGH",
            "nearest_observation_gap_seconds": spec["gap"],
            "bracket_span_seconds": spec["bracket"],
            "effective_sog_knots": spec["sog"],
            "effective_cog_degrees": spec["cog"],
            "input_observations": [
                {
                    "observed_at": "2026-08-16T05:24:00+00:00",
                    "latitude": spec["projected"][1],
                    "longitude": spec["projected"][0],
                }
            ],
            "limitations": ["SAR_AZIMUTH_DOPPLER_SHIFT_NOT_CORRECTED"],
            "historical_ais_only": True,
            "current_position": False,
        },
    }


def synthetic_inputs() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], bytes, bytes
]:
    context = {
        "type": "FeatureCollection",
        "schema_version": "1.0.0",
        "generated_at": "2026-08-19T18:00:00Z",
        "metadata": {
            "phase": "single_scene_historical_ais_match",
            "scene": {
                "normalized_scene_id": review.EXPECTED_SCENE,
                "acquisition_time_utc": review.EXPECTED_ACQUISITION_START,
            },
            "source_candidate_sha256": REVIEW_CONFIG["accepted_input_receipt"][
                "phase3_candidate_sha256"
            ],
            "source_candidate_count": 6,
            "match_counts": copy.deepcopy(review.EXPECTED_PHASE4_COUNTS),
            "current_ais_used": False,
            "current_ais_overlaid": False,
            "dark_vessel_claim": False,
            "public_layer": False,
            "coverage_assessment": {
                "status": "AVAILABLE_AIS_ACTIVITY_OBSERVED",
                "sufficient_for_available_ais_comparison": True,
                "occupied_bin_count": 6,
                "total_bins": 6,
                "distinct_valid_messages": 12565,
                "distinct_mmsi": 68,
                "coverage_bbox_wgs84": [11.66, 54.31, 12.54, 54.83],
            },
        },
        "features": [
            candidate_feature(identifier, spec)
            for identifier, spec in CANDIDATE_SPECS.items()
        ],
    }
    vessel_specs: dict[str, dict[str, Any]] = {}
    for spec in CANDIDATE_SPECS.values():
        vessel_specs.setdefault(spec["mmsi"], spec)
    window = {
        "type": "FeatureCollection",
        "schema_version": "1.0.0",
        "generated_at": "2026-08-19T18:00:00Z",
        "metadata": {
            "phase": "single_scene_historical_ais_match",
            "projection_time_utc": review.EXPECTED_ACQUISITION_START,
            "feature_count": len(vessel_specs),
            "current_positions_included": False,
            "public_layer": False,
        },
        "features": [
            projection_feature(mmsi, spec) for mmsi, spec in vessel_specs.items()
        ],
    }
    context_bytes = review._canonical_bytes(context)
    window_bytes = review._canonical_bytes(window)
    config = copy.deepcopy(REVIEW_CONFIG)
    config["accepted_input_receipt"]["phase4_context_sha256"] = hashlib.sha256(
        context_bytes
    ).hexdigest()
    config["accepted_input_receipt"]["phase4_ais_window_sha256"] = hashlib.sha256(
        window_bytes
    ).hexdigest()
    config["accepted_input_receipt"]["phase4_projected_historical_vessel_count"] = len(
        vessel_specs
    )
    return config, context, window, context_bytes, window_bytes


class CopernicusSarReviewQueueTests(unittest.TestCase):
    def test_reviewed_configuration_is_preliminary_and_fails_closed(self) -> None:
        review.validate_config(REVIEW_CONFIG)
        self.assertTrue(REVIEW_CONFIG["review_acceptance"]["accepted"])
        self.assertTrue(
            REVIEW_CONFIG["review_acceptance"]["visual_confirmation_still_required"]
        )
        self.assertTrue(REVIEW_CONFIG["quality_model"]["no_blended_operational_score"])
        self.assertFalse(
            REVIEW_CONFIG["quality_model"]["match_quality"][
                "doppler_correction_applied"
            ]
        )
        self.assertFalse(REVIEW_CONFIG["guardrails"]["automatic_final_disposition"])
        self.assertFalse(REVIEW_CONFIG["guardrails"]["downstream_eligible"])
        self.assertFalse(REVIEW_CONFIG["guardrails"]["publish_public_layer"])

        for key, unsafe in (
            ("current_ais_overlaid", True),
            ("claim_dark_vessel", True),
            ("automatic_final_disposition", True),
            ("change_hybrid_index", True),
        ):
            changed = copy.deepcopy(REVIEW_CONFIG)
            changed["guardrails"][key] = unsafe
            with self.assertRaises(catalog.PilotError):
                review.validate_config(changed)

    def test_every_source_candidate_is_preserved_exactly_once(self) -> None:
        config, context, window, context_bytes, window_bytes = synthetic_inputs()
        status, queue, objects, ok = review.run_phase5(
            config,
            context,
            window,
            context_source_bytes=context_bytes,
            ais_window_source_bytes=window_bytes,
        )
        self.assertTrue(ok, status["errors"])
        assert queue is not None
        assert objects is not None
        mapping = queue["candidate_to_review_object"]
        self.assertEqual(set(mapping), review.EXPECTED_CANDIDATES)
        self.assertEqual(len(mapping), 6)
        self.assertEqual(len(set(mapping.values())), 4)
        self.assertEqual(queue["queue_summary"]["source_candidate_count"], 6)
        self.assertEqual(queue["queue_summary"]["review_object_count"], 4)
        self.assertEqual(
            queue["queue_summary"]["deduplicated_source_candidate_reduction"], 2
        )
        self.assertEqual(len(objects["features"]), 4)

    def test_fragment_clusters_pass_course_and_length_checks_without_becoming_false_positives(
        self,
    ) -> None:
        config, context, window, context_bytes, window_bytes = synthetic_inputs()
        status, queue, _, ok = review.run_phase5(
            config,
            context,
            window,
            context_source_bytes=context_bytes,
            ais_window_source_bytes=window_bytes,
        )
        self.assertTrue(ok, status["errors"])
        assert queue is not None
        clusters = [
            item
            for item in queue["items"]
            if item["deduplication"]["grouping_method"]
            == "COURSE_AND_LENGTH_ALIGNED_FRAGMENT_CLUSTER"
        ]
        self.assertEqual(len(clusters), 2)
        for item in clusters:
            geometry = item["deduplication"]["fragment_geometry"]
            self.assertTrue(geometry["all_geometry_checks_passed"])
            self.assertLessEqual(geometry["alignment_difference_degrees"], 15.0)
            self.assertTrue(
                geometry["checks"]["separation_within_reported_length_fraction"]
            )
            self.assertTrue(
                item["deduplication"]["fragment_is_not_recorded_as_false_positive"]
            )
            self.assertEqual(
                item["analyst_review"]["false_positive_status"], "NOT_CONFIRMED"
            )
            self.assertIsNone(item["analyst_review"]["false_positive_reason"])

    def test_quality_dimensions_remain_separate_and_danpilot_is_not_upgraded(
        self,
    ) -> None:
        config, context, window, context_bytes, window_bytes = synthetic_inputs()
        status, queue, _, ok = review.run_phase5(
            config,
            context,
            window,
            context_source_bytes=context_bytes,
            ais_window_source_bytes=window_bytes,
        )
        self.assertTrue(ok, status["errors"])
        assert queue is not None
        by_name = {
            item["historical_ais_association"]["name"]: item for item in queue["items"]
        }
        danpilot = by_name["DANPILOT ALFA"]
        self.assertEqual(danpilot["quality"]["detection_quality"]["label"], "MEDIUM")
        self.assertEqual(danpilot["quality"]["data_quality"]["label"], "HIGH")
        self.assertEqual(danpilot["quality"]["match_quality"]["label"], "MEDIUM")
        self.assertFalse(danpilot["quality"]["match_quality"]["within_dynamic_radius"])
        self.assertFalse(
            danpilot["quality"]["match_quality"]["doppler_correction_applied"]
        )
        self.assertEqual(
            danpilot["association_hypothesis"],
            "POSSIBLE_DOPPLER_DISPLACED_AIS_ASSOCIATION",
        )
        self.assertIsNone(danpilot["quality"]["blended_operational_score"])
        self.assertFalse(danpilot["downstream_eligible"])
        self.assertFalse(danpilot["analyst_review"]["visual_confirmation_complete"])

        for vessel in ("LUNA PELAGOS", "TANGO 2", "BERLIN"):
            item = by_name[vessel]
            self.assertEqual(item["quality"]["detection_quality"]["label"], "HIGH")
            self.assertEqual(item["quality"]["data_quality"]["label"], "HIGH")
            self.assertEqual(item["quality"]["match_quality"]["label"], "HIGH")

    def test_exact_accepted_hashes_reject_phase4_drift(self) -> None:
        config, context, window, context_bytes, window_bytes = synthetic_inputs()
        changed = copy.deepcopy(context)
        changed["features"][0]["properties"]["signature_measurements"]["vv_peak_db"] = (
            99.0
        )
        changed_bytes = review._canonical_bytes(changed)
        status, queue, objects, ok = review.run_phase5(
            config,
            changed,
            window,
            context_source_bytes=changed_bytes,
            ais_window_source_bytes=window_bytes,
        )
        self.assertFalse(ok)
        self.assertIsNone(queue)
        self.assertIsNone(objects)
        self.assertIn("accepted review receipt", status["errors"][0])
        self.assertNotEqual(context_bytes, changed_bytes)

    def test_fragment_geometry_drift_cannot_retain_high_match_quality(self) -> None:
        config, context, window, _, window_bytes = synthetic_inputs()
        changed = copy.deepcopy(context)
        candidate4 = next(
            feature
            for feature in changed["features"]
            if feature["id"] == "SARCAND-20260816T052402Z-0004"
        )
        candidate4["geometry"]["coordinates"] = [12.2470, 54.5520]
        changed_bytes = review._canonical_bytes(changed)
        config["accepted_input_receipt"]["phase4_context_sha256"] = hashlib.sha256(
            changed_bytes
        ).hexdigest()
        status, queue, objects, ok = review.run_phase5(
            config,
            changed,
            window,
            context_source_bytes=changed_bytes,
            ais_window_source_bytes=window_bytes,
        )
        self.assertFalse(ok)
        self.assertIsNone(queue)
        self.assertIsNone(objects)
        self.assertIn("computed quality", status["errors"][0])

    def test_config_rejects_duplicate_candidate_membership_and_false_positive_shortcut(
        self,
    ) -> None:
        changed = copy.deepcopy(REVIEW_CONFIG)
        changed["review_objects"][1]["candidate_ids"][1] = changed["review_objects"][0][
            "candidate_ids"
        ][1]
        changed["review_objects"][1]["member_roles"] = {
            candidate_id: "SECONDARY_ALIGNED_SCATTERING_GROUP"
            for candidate_id in changed["review_objects"][1]["candidate_ids"]
        }
        with self.assertRaises(catalog.PilotError):
            review.validate_config(changed)

        changed = copy.deepcopy(REVIEW_CONFIG)
        changed["review_objects"][0]["false_positive_status"] = "CONFIRMED"
        with self.assertRaises(catalog.PilotError):
            review.validate_config(changed)

    def test_current_ais_or_final_disposition_in_inputs_fails_closed(self) -> None:
        config, context, window, _, window_bytes = synthetic_inputs()
        changed = copy.deepcopy(context)
        changed["metadata"]["current_ais_used"] = True
        changed_bytes = review._canonical_bytes(changed)
        config["accepted_input_receipt"]["phase4_context_sha256"] = hashlib.sha256(
            changed_bytes
        ).hexdigest()
        status, queue, objects, ok = review.run_phase5(
            config,
            changed,
            window,
            context_source_bytes=changed_bytes,
            ais_window_source_bytes=window_bytes,
        )
        self.assertFalse(ok)
        self.assertIsNone(queue)
        self.assertIsNone(objects)
        self.assertIn("current AIS", status["errors"][0])


if __name__ == "__main__":
    unittest.main()
