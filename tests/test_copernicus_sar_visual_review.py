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
import copernicus_sar_chip as chip  # noqa: E402
import copernicus_sar_visual_review as visual  # noqa: E402


FIXTURE = json.loads(
    (ROOT / "tests/fixtures/copernicus_s1_grd_catalog.json").read_text(encoding="utf-8")
)
PILOT_CONFIG = json.loads(
    (ROOT / "config/sar_copernicus_pilot.json").read_text(encoding="utf-8")
)
CHIP_CONFIG = json.loads(
    (ROOT / "config/sar_copernicus_chip.json").read_text(encoding="utf-8")
)
VISUAL_CONFIG = json.loads(
    (ROOT / "config/sar_copernicus_visual_review.json").read_text(encoding="utf-8")
)


def json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def pixel_to_wgs84(row: float, column: float, size: int) -> list[float]:
    west, south, east, north = PILOT_CONFIG["aoi"]["bbox_wgs84"]
    longitude = west + ((column + 0.5) / size) * (east - west)
    latitude = north - ((row + 0.5) / size) * (north - south)
    return [round(longitude, 7), round(latitude, 7)]


def small_configs(size: int = 128) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = copy.deepcopy(VISUAL_CONFIG)
    pilot = copy.deepcopy(PILOT_CONFIG)
    chip_config = copy.deepcopy(CHIP_CONFIG)
    pilot["processing_plan"]["expected_width_px"] = size
    pilot["processing_plan"]["expected_height_px"] = size
    chip_config["request"]["width_px"] = size
    chip_config["request"]["height_px"] = size
    config["rendering"]["crop_size_px"] = 64
    config["rendering"]["minimum_content_margin_px"] = 8
    return config, pilot, chip_config


def small_inputs(
    config: dict[str, Any], size: int = 128
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, bytes, bytes]:
    scene = config["scene"]["normalized_scene_id"]
    acquisition = config["scene"]["acquisition_time_utc"]
    candidate_id = "SARCAND-20260816T052402Z-0001"
    object_id = "SARREVIEW-20260816T052402Z-0001"
    candidate_coordinates = pixel_to_wgs84(64.0, 64.0, size)
    ais_coordinates = pixel_to_wgs84(67.0, 68.0, size)
    candidate = {
        "type": "Feature",
        "id": candidate_id,
        "geometry": {"type": "Point", "coordinates": candidate_coordinates},
        "properties": {
            "detection_id": candidate_id,
            "scene_id": scene,
            "candidate_status": "UNREVIEWED_SAR_CANDIDATE",
            "candidate_is_not_vessel_classification": True,
            "current_ais_overlaid": False,
            "dark_vessel_claim": False,
            "centroid_pixel": {"row": 64.0, "column": 64.0},
            "signature_measurements": {
                "vv_peak_db": 16.0,
                "seed_bbox_pixels": {
                    "row_min": 62,
                    "row_max": 66,
                    "column_min": 63,
                    "column_max": 65,
                },
            },
        },
    }
    context = {
        "type": "FeatureCollection",
        "schema_version": "1.0.0",
        "metadata": {
            "phase": "single_scene_historical_ais_match",
            "scene": {"normalized_scene_id": scene},
            "source_candidate_count": 1,
            "current_ais_used": False,
            "current_ais_overlaid": False,
            "dark_vessel_claim": False,
        },
        "features": [candidate],
    }
    item = {
        "review_object_id": object_id,
        "review_state": "READY_FOR_VISUAL_CONFIRMATION",
        "association_hypothesis": "LIKELY_UNIQUE_AIS_ASSOCIATED_SAR_RETURN",
        "candidate_is_not_vessel_classification": True,
        "source_candidates": [
            {
                "candidate_id": candidate_id,
                "member_role": "SINGLE_REVIEW_CANDIDATE",
            }
        ],
        "deduplication": {
            "grouping_method": "SINGLE_CANDIDATE",
            "source_candidate_ids_preserved": [candidate_id],
        },
        "historical_ais_association": {
            "mmsi": "211000001",
            "name": "UNIT TEST CONTACT",
            "projected_position": {
                "coordinates": ais_coordinates,
                "time_utc": acquisition,
            },
            "effective_cog_degrees": 197.0,
            "current_ais_used": False,
        },
        "analyst_review": {
            "analyst_status": "PRELIMINARY_REVIEW_ACCEPTED",
            "visual_confirmation_required": True,
            "visual_confirmation_complete": False,
            "final_disposition": None,
            "automatic_final_disposition": False,
        },
        "current_ais_overlaid": False,
        "dark_vessel_claim": False,
        "downstream_eligible": False,
        "public_layer": False,
    }
    queue = {
        "schema_version": "1.0.0",
        "phase": "single_scene_analyst_review_queue",
        "complete": True,
        "degraded": False,
        "scene": {"normalized_scene_id": scene},
        "queue_summary": {
            "source_candidate_count": 1,
            "review_object_count": 1,
            "visual_confirmation_pending_count": 1,
            "final_disposition_count": 0,
            "downstream_eligible_count": 0,
        },
        "candidate_to_review_object": {candidate_id: object_id},
        "items": [item],
        "current_ais_used": False,
        "current_ais_overlaid": False,
        "dark_vessel_claim": False,
        "automatic_final_disposition": False,
        "downstream_eligible": False,
        "public_layer": False,
    }
    objects = {
        "type": "FeatureCollection",
        "schema_version": "1.0.0",
        "metadata": {
            "source_candidate_count": 1,
            "review_object_count": 1,
            "current_positions_included": False,
            "public_layer": False,
        },
        "features": [
            {
                "type": "Feature",
                "id": object_id,
                "geometry": {"type": "Point", "coordinates": candidate_coordinates},
                "properties": {
                    "review_object_id": object_id,
                    "visual_confirmation_complete": False,
                    "downstream_eligible": False,
                    "public_layer": False,
                },
            }
        ],
    }
    context_raw = json_bytes(context)
    queue_raw = json_bytes(queue)
    objects_raw = json_bytes(objects)
    config["accepted_input_receipt"] = {
        "phase4_context_sha256": hashlib.sha256(context_raw).hexdigest(),
        "phase5_queue_sha256": hashlib.sha256(queue_raw).hexdigest(),
        "phase5_objects_sha256": hashlib.sha256(objects_raw).hexdigest(),
        "source_candidate_count": 1,
        "review_object_count": 1,
    }
    return context, queue, objects, context_raw, queue_raw, objects_raw


def synthetic_geotiff(size: int = 128) -> bytes:
    import numpy as np
    import tifffile

    array = np.zeros((size, size, 3), dtype=np.float32)
    rows, columns = np.indices((size, size))
    texture = ((rows * 17 + columns * 31) % 23).astype(np.float32) * 0.00002
    array[:, :, 0] = 0.01 + texture
    array[:, :, 1] = 0.001 + texture * 0.2
    array[:, :, 2] = 1.0
    array[62:67, 63:66, 0] = 0.8
    array[63:66, 63:66, 1] = 0.2

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


class CopernicusSarVisualReviewTests(unittest.TestCase):
    def test_reviewed_configuration_requires_native_clean_panels_and_no_decision(self) -> None:
        visual.validate_config(VISUAL_CONFIG, PILOT_CONFIG, CHIP_CONFIG)
        self.assertEqual(VISUAL_CONFIG["rendering"]["panel_order"], visual.PANEL_ORDER)
        self.assertEqual(
            VISUAL_CONFIG["rendering"]["source_pixels_per_output_pixel"], 1.0
        )
        self.assertEqual(VISUAL_CONFIG["rendering"]["resampling"], "NONE")
        guardrails = VISUAL_CONFIG["guardrails"]
        self.assertTrue(guardrails["clean_unmarked_panels_required"])
        self.assertTrue(guardrails["current_ais_files_must_not_be_read"])
        self.assertFalse(guardrails["analyst_decision_recorded"])
        self.assertFalse(guardrails["downstream_eligible"])
        self.assertFalse(guardrails["publish_public_layer"])

    def test_configuration_fails_if_current_ais_decision_or_resampling_is_enabled(self) -> None:
        for key in (
            "current_ais_overlaid",
            "analyst_decision_recorded",
            "automatic_final_disposition",
            "downstream_eligible",
        ):
            changed = copy.deepcopy(VISUAL_CONFIG)
            changed["guardrails"][key] = True
            with self.assertRaises(catalog.PilotError, msg=key):
                visual.validate_config(changed, PILOT_CONFIG, CHIP_CONFIG)
        changed = copy.deepcopy(VISUAL_CONFIG)
        changed["rendering"]["source_pixels_per_output_pixel"] = 2.0
        with self.assertRaises(catalog.PilotError):
            visual.validate_config(changed, PILOT_CONFIG, CHIP_CONFIG)
        changed = copy.deepcopy(VISUAL_CONFIG)
        changed["rendering"]["resampling"] = "BILINEAR"
        with self.assertRaises(catalog.PilotError):
            visual.validate_config(changed, PILOT_CONFIG, CHIP_CONFIG)

    def test_crop_clamps_to_source_edge_without_losing_review_points(self) -> None:
        self.assertEqual(
            visual.crop_origin([(2.0, 2.0), (10.0, 12.0)], (128, 128), 64, 8),
            (0, 0),
        )
        self.assertEqual(
            visual.crop_origin([(118.0, 119.0), (125.0, 124.0)], (128, 128), 64, 8),
            (64, 64),
        )
        with self.assertRaises(catalog.PilotError):
            visual.crop_origin([(5.0, 5.0), (100.0, 100.0)], (128, 128), 64, 8)

    def test_full_offline_phase_creates_separate_native_panels_and_deletes_tiff(self) -> None:
        from PIL import Image

        config, pilot, chip_config = small_configs()
        context, queue, objects, context_raw, queue_raw, objects_raw = small_inputs(config)
        raster = synthetic_geotiff()
        fake = FakeSession(FIXTURE, raster)
        client_id = "unit-test-client-id"
        client_secret = "unit-test-client-secret"
        status, manifest, png_bytes, ok = visual.run_visual_review(
            config,
            pilot,
            chip_config,
            context,
            queue,
            objects,
            client_id,
            client_secret,
            context_bytes=context_raw,
            queue_bytes=queue_raw,
            objects_bytes=objects_raw,
            session=fake,
        )
        self.assertTrue(ok, status.get("errors"))
        self.assertIsNotNone(manifest)
        self.assertIsNotNone(png_bytes)
        assert manifest is not None
        assert png_bytes is not None
        self.assertEqual(png_bytes[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(status["temporary_raster_written"])
        self.assertTrue(status["temporary_raster_deleted"])
        self.assertFalse(status["raster_persisted"])
        self.assertFalse(status["raster_artifact_uploaded"])
        self.assertFalse(status["ais_download_performed"])
        self.assertTrue(status["historical_ais_only"])
        self.assertFalse(status["current_ais_used"])
        self.assertFalse(status["current_ais_overlaid"])
        self.assertFalse(status["analyst_decision_recorded"])
        self.assertEqual(status["visual_confirmation_complete_count"], 0)
        self.assertEqual(status["downstream_eligible_count"], 0)
        self.assertTrue(status["rendering"]["clean_unmarked_panels_created"])
        self.assertTrue(status["rendering"]["separate_overlay_panels_created"])
        self.assertTrue(status["rendering"]["native_pixel_scale_preserved"])
        self.assertEqual(status["raster_sha256"], hashlib.sha256(raster).hexdigest())
        self.assertEqual(
            [call["url"] for call in fake.calls],
            [catalog.CDSE_TOKEN_URL, catalog.SENTINEL_HUB_CATALOG_URL, catalog.PROCESS_API_URL],
        )

        sheet = manifest["review_sheet"]
        self.assertEqual(sheet["native_source_pixels_per_output_pixel"], 1.0)
        self.assertEqual(sheet["resampling"], "NONE")
        self.assertTrue(sheet["clean_panels_unmarked"])
        self.assertEqual(sheet["review_object_count"], 1)
        record = sheet["review_panels"][0]
        self.assertFalse(record["visual_confirmation"]["complete"])
        self.assertFalse(record["visual_confirmation"]["analyst_decision_recorded"])
        self.assertEqual(record["source_pixel_window"]["width_px"], 64)

        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        origins = record["sheet_panel_origins_px"]
        centroid = record["overlay"]["candidates"][0]["centroid_crop_pixel"]
        local_x = int(round(centroid["column"]))
        local_y = int(round(centroid["row"]))
        clean_origin = origins["VV_CLEAN_NATIVE_1_TO_1"]
        overlay_origin = origins["VV_HISTORICAL_AIS_OVERLAY_NATIVE_1_TO_1"]
        clean_pixel = image.getpixel(
            (clean_origin["x"] + local_x, clean_origin["y"] + local_y)
        )
        overlay_pixel = image.getpixel(
            (overlay_origin["x"] + local_x, overlay_origin["y"] + local_y)
        )
        self.assertEqual(clean_pixel[0], clean_pixel[1])
        self.assertEqual(clean_pixel[1], clean_pixel[2])
        self.assertNotEqual(overlay_pixel, clean_pixel)
        self.assertNotEqual(overlay_pixel[0], overlay_pixel[1])

        serialized = json.dumps({"status": status, "manifest": manifest})
        self.assertNotIn(client_id, serialized)
        self.assertNotIn(client_secret, serialized)
        self.assertNotIn(FIXTURE["token"]["access_token"], serialized)

    def test_hash_mismatch_fails_before_any_network_request(self) -> None:
        config, pilot, chip_config = small_configs()
        context, queue, objects, context_raw, queue_raw, objects_raw = small_inputs(config)
        fake = FakeSession(FIXTURE, synthetic_geotiff())
        status, manifest, png_bytes, ok = visual.run_visual_review(
            config,
            pilot,
            chip_config,
            context,
            queue,
            objects,
            "unit-test-client-id",
            "unit-test-client-secret",
            context_bytes=context_raw + b" ",
            queue_bytes=queue_raw,
            objects_bytes=objects_raw,
            session=fake,
        )
        self.assertFalse(ok)
        self.assertIsNone(manifest)
        self.assertIsNone(png_bytes)
        self.assertEqual(fake.calls, [])
        self.assertFalse(status["network_access_performed"])
        self.assertFalse(status["sar_download_performed"])
        self.assertIn("SHA-256", status["errors"][0])


if __name__ == "__main__":
    unittest.main()
