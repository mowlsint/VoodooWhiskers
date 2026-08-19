#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import copernicus_sar_catalog as catalog  # noqa: E402
import copernicus_sar_cfar as cfar  # noqa: E402
import copernicus_sar_chip as chip  # noqa: E402


FIXTURE = json.loads((ROOT / "tests/fixtures/copernicus_s1_grd_catalog.json").read_text(encoding="utf-8"))
PILOT_CONFIG = json.loads((ROOT / "config/sar_copernicus_pilot.json").read_text(encoding="utf-8"))
CHIP_CONFIG = json.loads((ROOT / "config/sar_copernicus_chip.json").read_text(encoding="utf-8"))
CFAR_CONFIG = json.loads((ROOT / "config/sar_copernicus_cfar.json").read_text(encoding="utf-8"))
LAND_MASK = json.loads((ROOT / "config/sar_copernicus_land_mask.geojson").read_text(encoding="utf-8"))


def empty_infrastructure_context() -> dict[str, Any]:
    return {
        "available": False,
        "grid": None,
        "feature_count": 0,
        "counts_by_type": {},
        "module": None,
        "error": "Unit-test infrastructure reference intentionally unavailable",
    }


def small_configs(size: int = 128) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    phase_config = copy.deepcopy(CFAR_CONFIG)
    pilot_config = copy.deepcopy(PILOT_CONFIG)
    chip_config = copy.deepcopy(CHIP_CONFIG)
    pilot_config["processing_plan"]["expected_width_px"] = size
    pilot_config["processing_plan"]["expected_height_px"] = size
    chip_config["request"]["width_px"] = size
    chip_config["request"]["height_px"] = size
    phase_config["spatial_context"]["land_buffer_radius_px"] = 2
    phase_config["quicklook"]["maximum_panel_width_px"] = 360
    return phase_config, pilot_config, chip_config


def synthetic_geotiff(size: int = 128) -> bytes:
    import numpy as np
    import tifffile

    array = np.zeros((size, size, 3), dtype=np.float32)
    rows, columns = np.indices((size, size))
    texture = ((rows * 17 + columns * 31) % 19).astype(np.float32) * 0.00001
    array[:, :, 0] = 0.01 + texture
    array[:, :, 1] = 0.001 + texture * 0.1
    array[:, :, 2] = 1.0
    array[65, 75, 0] = 1.0
    array[65, 76, 0] = 0.8
    array[65, 75, 1] = 0.3
    array[65, 76, 1] = 0.2
    array[95, 105, 0] = 0.7

    destination = io.BytesIO()
    tifffile.imwrite(
        destination,
        array,
        photometric="rgb",
        planarconfig="contig",
        metadata=None,
        extratags=[
            (33550, "d", 3, (0.30 / size, 0.18 / size, 0.0), False),
            (33922, "d", 6, (0.0, 0.0, 0.0, 11.95, 54.66, 0.0), False),
            (34735, "H", 8, (1, 1, 0, 1, 2048, 0, 1, 4326), False),
        ],
    )
    return destination.getvalue()


class FakeSession:
    def __init__(self, fixture: dict[str, Any], raster: bytes) -> None:
        self.fixture = fixture
        self.raster = raster
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> chip.BinaryResponse:
        self.calls.append({"url": url, **kwargs})
        if url == catalog.CDSE_TOKEN_URL:
            return chip.BinaryResponse(
                200,
                json.dumps(self.fixture["token"]).encode("utf-8"),
                {"content-type": "application/json"},
            )
        if url == catalog.SENTINEL_HUB_CATALOG_URL:
            return chip.BinaryResponse(
                200,
                json.dumps(self.fixture["sentinel_hub"]).encode("utf-8"),
                {"content-type": "application/json"},
            )
        if url == catalog.PROCESS_API_URL:
            return chip.BinaryResponse(
                200,
                self.raster,
                {
                    "content-type": "image/tiff",
                    "x-processingunits-spent": "0.5",
                },
            )
        return chip.BinaryResponse(404, b"{}", {"content-type": "application/json"})


class CopernicusSarCfarTests(unittest.TestCase):
    def test_reviewed_configuration_has_three_distinct_experimental_profiles(self) -> None:
        cfar.validate_phase_config(CFAR_CONFIG, PILOT_CONFIG, CHIP_CONFIG)
        profiles = CFAR_CONFIG["algorithm"]["profiles"]
        self.assertEqual(
            [profile["id"] for profile in profiles],
            ["strict_wide", "balanced", "exploratory_local"],
        )
        combinations = {
            (
                profile["training_radius_px"],
                profile["guard_radius_px"],
                profile["probability_of_false_alarm"],
            )
            for profile in profiles
        }
        self.assertEqual(len(combinations), 3)
        self.assertTrue(CFAR_CONFIG["guardrails"]["parameters_are_experimental"])
        self.assertTrue(CFAR_CONFIG["guardrails"]["visual_review_required"])
        self.assertFalse(CFAR_CONFIG["guardrails"]["download_ais"])
        self.assertFalse(CFAR_CONFIG["guardrails"]["overlay_current_ais"])
        self.assertFalse(CFAR_CONFIG["guardrails"]["claim_dark_vessel"])

    def test_configuration_fails_if_current_ais_or_downstream_is_enabled(self) -> None:
        changed = copy.deepcopy(CFAR_CONFIG)
        changed["guardrails"]["overlay_current_ais"] = True
        with self.assertRaises(catalog.PilotError):
            cfar.validate_phase_config(changed, PILOT_CONFIG, CHIP_CONFIG)
        changed = copy.deepcopy(CFAR_CONFIG)
        changed["guardrails"]["modify_magic_paws"] = True
        with self.assertRaises(catalog.PilotError):
            cfar.validate_phase_config(changed, PILOT_CONFIG, CHIP_CONFIG)

    def test_fixed_land_mask_has_provenance_and_removes_western_land(self) -> None:
        import numpy as np

        phase_config = copy.deepcopy(CFAR_CONFIG)
        phase_config["spatial_context"]["land_buffer_radius_px"] = 10
        source_valid = np.ones((200, 193), dtype=bool)
        processing_valid, metadata = cfar.apply_land_mask(
            source_valid, PILOT_CONFIG, phase_config, LAND_MASK
        )
        self.assertTrue(metadata["applied"])
        self.assertEqual(metadata["source_version"], "5.1.1")
        self.assertGreater(metadata["land_pixels"], 0)
        self.assertGreater(metadata["buffer_only_pixels"], 0)
        self.assertFalse(processing_valid[30, 2])
        self.assertTrue(processing_valid[150, 150])
        self.assertFalse(metadata["navigational_use"])

    def test_ca_cfar_detects_isolated_bright_return(self) -> None:
        import numpy as np

        power = np.full((101, 101), 0.01, dtype=np.float32)
        power[50, 50] = 1.0
        valid = np.ones(power.shape, dtype=bool)
        profile = {
            "id": "unit_test",
            "training_radius_px": 10,
            "guard_radius_px": 2,
            "probability_of_false_alarm": 1e-5,
        }
        hits, excess, summary = cfar.ca_cfar(power, valid, profile, 0.8, 10)
        self.assertTrue(hits[50, 50])
        self.assertEqual(int(hits.sum()), 1)
        self.assertGreater(float(excess[50, 50]), 0)
        self.assertEqual(summary["seed_pixels"], 1)

    def test_vh_corroboration_cannot_create_a_candidate(self) -> None:
        import numpy as np

        phase_config, pilot_config, _ = small_configs(81)
        phase_config["spatial_context"]["land_buffer_radius_px"] = 1
        vv = np.full((81, 81), 0.01, dtype=np.float32)
        vh = np.full((81, 81), 0.001, dtype=np.float32)
        vh[40, 50] = 1.0
        valid = np.ones(vv.shape, dtype=bool)
        masks, excesses, vh_hits, vh_excess, _, _ = cfar.compare_profiles(
            vv, vh, valid, phase_config
        )
        self.assertEqual(sum(int(mask.sum()) for mask in masks.values()), 0)
        self.assertGreater(int(vh_hits.sum()), 0)
        features = cfar.group_candidates(
            vv,
            vh,
            masks,
            excesses,
            vh_hits,
            vh_excess,
            phase_config,
            pilot_config,
            {
                "catalogue_item_id": "test",
                "normalized_scene_id": "S1C_TEST",
                "datetime": "2026-08-16T05:24:02Z",
            },
            empty_infrastructure_context(),
        )
        self.assertEqual(features, [])

    def test_seed_noise_guard_fails_closed(self) -> None:
        import numpy as np

        phase_config = copy.deepcopy(CFAR_CONFIG)
        for index, profile in enumerate(phase_config["algorithm"]["profiles"]):
            profile["training_radius_px"] = 5 + index
            profile["guard_radius_px"] = 1
            profile["probability_of_false_alarm"] = 1e-3
        phase_config["algorithm"]["maximum_seed_pixels_per_profile"] = 100
        vv = np.full((256, 256), 0.001, dtype=np.float32)
        vv[12:244:8, 12:244:8] = 1.0
        vh = np.full_like(vv, 0.001)
        valid = np.ones(vv.shape, dtype=bool)
        with self.assertRaisesRegex(catalog.PilotError, "noise guard"):
            cfar.compare_profiles(vv, vh, valid, phase_config)

    def test_full_offline_pilot_deletes_tiff_and_emits_review_only_outputs(self) -> None:
        phase_config, pilot_config, chip_config = small_configs()
        raster = synthetic_geotiff()
        fake = FakeSession(FIXTURE, raster)
        client_id = "unit-test-client-id"
        client_secret = "unit-test-client-secret"
        status, geojson, png_bytes, ok = cfar.run_cfar_pilot(
            phase_config,
            pilot_config,
            chip_config,
            client_id,
            client_secret,
            session=fake,
            infrastructure_context=empty_infrastructure_context(),
            land_mask_payload=LAND_MASK,
        )
        self.assertTrue(ok, status.get("errors"))
        self.assertEqual(status["status"], "ok")
        self.assertTrue(status["catalogue_confirmation"]["single_scene_guard_passed"])
        self.assertTrue(status["raster_download_performed"])
        self.assertTrue(status["temporary_raster_written"])
        self.assertTrue(status["temporary_raster_deleted"])
        self.assertFalse(status["raster_persisted"])
        self.assertFalse(status["raster_artifact_uploaded"])
        self.assertTrue(status["candidate_extraction_performed"])
        self.assertEqual(status["raster_sha256"], hashlib.sha256(raster).hexdigest())
        self.assertTrue(status["spatial_context"]["land_mask"]["applied"])
        self.assertFalse(status["ais_download_performed"])
        self.assertEqual(status["ais_context_status"], "NOT_CHECKED")
        self.assertFalse(status["current_ais_overlaid"])
        self.assertFalse(status["dark_vessel_claim"])
        self.assertFalse(status["public_layer_modified"])
        self.assertFalse(status["gfw_products_modified"])
        self.assertFalse(status["magic_paws_modified"])
        self.assertFalse(status["production_ready"])

        self.assertIsNotNone(geojson)
        self.assertIsNotNone(png_bytes)
        assert geojson is not None
        assert png_bytes is not None
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertEqual(geojson["metadata"]["ais_context_status"], "NOT_CHECKED")
        self.assertTrue(geojson["metadata"]["coastline_mask_integrated"])
        self.assertGreaterEqual(len(geojson["features"]), 2)
        self.assertLessEqual(len(geojson["features"]), 4)
        ids = [feature["properties"]["detection_id"] for feature in geojson["features"]]
        self.assertEqual(len(ids), len(set(ids)))
        for feature in geojson["features"]:
            properties = feature["properties"]
            self.assertEqual(properties["candidate_status"], "UNREVIEWED_SAR_CANDIDATE")
            self.assertEqual(properties["review_status"], "unreviewed")
            self.assertTrue(properties["candidate_is_not_vessel_classification"])
            self.assertTrue(properties["human_visual_review_required"])
            self.assertFalse(properties["downstream_eligible"])
            self.assertEqual(properties["ais_context_status"], "NOT_CHECKED")
            self.assertFalse(properties["current_ais_overlaid"])
            self.assertFalse(properties["dark_vessel_claim"])
            self.assertIn(
                properties["detection_method"]["parameter_consensus"],
                ("ALL_PROFILES", "MULTI_PROFILE", "SINGLE_PROFILE"),
            )
        self.assertEqual(png_bytes[:8], b"\x89PNG\r\n\x1a\n")
        self.assertLessEqual(len(png_bytes), phase_config["quicklook"]["maximum_png_bytes"])

        serialized = json.dumps({"status": status, "geojson": geojson})
        self.assertNotIn(client_id, serialized)
        self.assertNotIn(client_secret, serialized)
        self.assertNotIn(FIXTURE["token"]["access_token"], serialized)
        self.assertEqual(
            [call["url"] for call in fake.calls],
            [catalog.CDSE_TOKEN_URL, catalog.SENTINEL_HUB_CATALOG_URL, catalog.PROCESS_API_URL],
        )

    def test_wrong_scene_fails_before_raster_request(self) -> None:
        phase_config, pilot_config, chip_config = small_configs()
        fixture = copy.deepcopy(FIXTURE)
        fixture["sentinel_hub"]["features"][0]["id"] = "S1C_DIFFERENT_PRODUCT"
        fake = FakeSession(fixture, synthetic_geotiff())
        status, geojson, png_bytes, ok = cfar.run_cfar_pilot(
            phase_config,
            pilot_config,
            chip_config,
            "unit-test-client-id",
            "unit-test-client-secret",
            session=fake,
            infrastructure_context=empty_infrastructure_context(),
            land_mask_payload=LAND_MASK,
        )
        self.assertFalse(ok)
        self.assertIsNone(geojson)
        self.assertIsNone(png_bytes)
        self.assertFalse(status["raster_download_performed"])
        self.assertEqual(
            [call["url"] for call in fake.calls],
            [catalog.CDSE_TOKEN_URL, catalog.SENTINEL_HUB_CATALOG_URL],
        )


if __name__ == "__main__":
    unittest.main()
