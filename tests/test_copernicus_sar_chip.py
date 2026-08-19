#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import copernicus_sar_catalog as catalog  # noqa: E402
import copernicus_sar_chip as chip  # noqa: E402


FIXTURE = json.loads((ROOT / "tests/fixtures/copernicus_s1_grd_catalog.json").read_text(encoding="utf-8"))
PILOT_CONFIG = json.loads((ROOT / "config/sar_copernicus_pilot.json").read_text(encoding="utf-8"))
CHIP_CONFIG = json.loads((ROOT / "config/sar_copernicus_chip.json").read_text(encoding="utf-8"))


def small_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    phase_config = deepcopy(CHIP_CONFIG)
    pilot_config = deepcopy(PILOT_CONFIG)
    phase_config["request"]["width_px"] = 5
    phase_config["request"]["height_px"] = 4
    pilot_config["processing_plan"]["expected_width_px"] = 5
    pilot_config["processing_plan"]["expected_height_px"] = 4
    return phase_config, pilot_config


def build_test_geotiff() -> bytes:
    import numpy as np
    import tifffile

    array = np.zeros((4, 5, 3), dtype=np.float32)
    array[:, :, 0] = np.linspace(0.001, 0.2, 20, dtype=np.float32).reshape(4, 5)
    array[:, :, 1] = np.linspace(0.0001, 0.02, 20, dtype=np.float32).reshape(4, 5)
    array[:, :, 2] = 1.0
    array[0, 0, 2] = 0.0
    destination = io.BytesIO()
    tifffile.imwrite(
        destination,
        array,
        photometric="rgb",
        planarconfig="contig",
        metadata=None,
        extratags=[
            (33550, "d", 3, (0.01, 0.01, 0.0), False),
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
                    "x-processingunits-spent": "0.125",
                },
            )
        return chip.BinaryResponse(404, b"{}", {"content-type": "application/json"})


class CopernicusSarChipTests(unittest.TestCase):
    def test_reviewed_configuration_and_processing_unit_estimate(self) -> None:
        chip.validate_phase_config(CHIP_CONFIG, PILOT_CONFIG)
        estimate = chip.processing_unit_estimate(CHIP_CONFIG)
        self.assertAlmostEqual(estimate["estimated_processing_units"], 39.387, places=3)

    def test_process_request_is_exact_and_unfiltered(self) -> None:
        body = chip.build_process_request(CHIP_CONFIG, PILOT_CONFIG)
        self.assertEqual(body["output"]["width"], 1934)
        self.assertEqual(body["output"]["height"], 2002)
        self.assertEqual(body["output"]["responses"][0]["format"]["type"], "image/tiff")
        source = body["input"]["data"]
        self.assertEqual(len(source), 1)
        self.assertEqual(source[0]["type"], "sentinel-1-grd")
        self.assertEqual(source[0]["dataFilter"]["acquisitionMode"], "IW")
        self.assertEqual(source[0]["dataFilter"]["polarization"], "DV")
        self.assertEqual(source[0]["dataFilter"]["orbitDirection"], "DESCENDING")
        self.assertEqual(source[0]["processing"]["backCoeff"], "GAMMA0_ELLIPSOID")
        self.assertEqual(source[0]["processing"]["orthorectify"], "true")
        self.assertNotIn("speckleFilter", source[0]["processing"])
        self.assertIn('sampleType: "FLOAT32"', body["evalscript"])
        self.assertNotIn("detect", body["evalscript"].lower())

    def test_authenticated_chip_is_validated_and_deleted(self) -> None:
        phase_config, pilot_config = small_configs()
        raster = build_test_geotiff()
        fake = FakeSession(FIXTURE, raster)
        client_id = "unit-test-client-id"
        client_secret = "unit-test-client-secret"
        status, ok = chip.run_chip_pilot(
            phase_config,
            pilot_config,
            client_id,
            client_secret,
            session=fake,
        )
        self.assertTrue(ok, status.get("errors"))
        self.assertEqual(status["status"], "ok")
        self.assertTrue(status["oauth"]["oauth_client_credentials_valid"])
        self.assertTrue(status["catalogue_confirmation"]["single_scene_guard_passed"])
        self.assertTrue(status["raster_download_performed"])
        self.assertTrue(status["temporary_raster_written"])
        self.assertTrue(status["temporary_raster_deleted"])
        self.assertFalse(status["raster_persisted"])
        self.assertFalse(status["raster_artifact_uploaded"])
        self.assertFalse(status["detection_performed"])
        self.assertFalse(status["ais_download_performed"])
        self.assertEqual(status["raster_response_bytes"], len(raster))
        self.assertEqual(status["raster_sha256"], hashlib.sha256(raster).hexdigest())
        self.assertEqual(status["process_api_reported_processing_units"], 0.125)
        self.assertEqual(status["raster_validation"]["shape"], [4, 5, 3])
        self.assertEqual(status["raster_validation"]["dtype"], "float32")
        self.assertAlmostEqual(status["raster_validation"]["valid_fraction"], 0.95)
        self.assertTrue(status["raster_validation"]["geotiff"]["spatial_transform_present"])
        self.assertGreater(status["raster_validation"]["band_statistics"]["VV"]["linear_power"]["p99"], 0)
        serialized = json.dumps(status)
        self.assertNotIn(client_id, serialized)
        self.assertNotIn(client_secret, serialized)
        self.assertNotIn(FIXTURE["token"]["access_token"], serialized)
        self.assertEqual(
            [call["url"] for call in fake.calls],
            [catalog.CDSE_TOKEN_URL, catalog.SENTINEL_HUB_CATALOG_URL, catalog.PROCESS_API_URL],
        )

    def test_wrong_scene_fails_before_process_request(self) -> None:
        phase_config, pilot_config = small_configs()
        fixture = deepcopy(FIXTURE)
        fixture["sentinel_hub"]["features"][0]["id"] = "S1C_DIFFERENT_PRODUCT"
        fake = FakeSession(fixture, build_test_geotiff())
        status, ok = chip.run_chip_pilot(
            phase_config,
            pilot_config,
            "unit-test-client-id",
            "unit-test-client-secret",
            session=fake,
        )
        self.assertFalse(ok)
        self.assertEqual(status["status"], "error")
        self.assertFalse(status["raster_download_performed"])
        self.assertEqual(
            [call["url"] for call in fake.calls],
            [catalog.CDSE_TOKEN_URL, catalog.SENTINEL_HUB_CATALOG_URL],
        )

    def test_dimension_guard_fails_closed(self) -> None:
        phase_config = deepcopy(CHIP_CONFIG)
        phase_config["request"]["width_px"] = 2501
        with self.assertRaises(catalog.PilotError):
            chip.validate_phase_config(phase_config, PILOT_CONFIG)

    def test_non_tiff_response_fails_closed(self) -> None:
        phase_config, pilot_config = small_configs()
        status, ok = chip.run_chip_pilot(
            phase_config,
            pilot_config,
            "unit-test-client-id",
            "unit-test-client-secret",
            session=FakeSession(FIXTURE, b"not-a-tiff"),
        )
        self.assertFalse(ok)
        self.assertTrue(status["catalogue_confirmation"]["single_scene_guard_passed"])
        self.assertFalse(status["raster_download_performed"])
        self.assertIn("not a TIFF", status["errors"][0])


if __name__ == "__main__":
    unittest.main()
