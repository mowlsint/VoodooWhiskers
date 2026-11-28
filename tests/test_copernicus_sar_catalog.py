#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import copernicus_sar_catalog as catalog  # noqa: E402


FIXTURE = json.loads((ROOT / "tests/fixtures/copernicus_s1_grd_catalog.json").read_text(encoding="utf-8"))
CONFIG = json.loads((ROOT / "config/sar_copernicus_pilot.json").read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if url == catalog.CDSE_STAC_SEARCH_URL:
            return FakeResponse(200, self.fixture["cdse"])
        if url == catalog.CDSE_TOKEN_URL:
            return FakeResponse(200, self.fixture["token"])
        if url == catalog.SENTINEL_HUB_CATALOG_URL:
            return FakeResponse(200, self.fixture["sentinel_hub"])
        return FakeResponse(404, {})


class CopernicusSarCatalogTests(unittest.TestCase):
    def test_valid_config_and_exact_scene(self) -> None:
        catalog.validate_config(CONFIG)
        scene, returned = catalog.select_scene(
            FIXTURE["cdse"],
            CONFIG["expected_cdse_item_id"],
            "fixture",
        )
        self.assertEqual(returned, 1)
        self.assertEqual(scene["id"], CONFIG["expected_cdse_item_id"])

    def test_authenticated_metadata_only_pilot(self) -> None:
        fake = FakeSession(FIXTURE)
        client_id = "unit-test-client-id"
        client_secret = "unit-test-client-secret"
        status, ok = catalog.run_catalog_pilot(
            CONFIG,
            client_id,
            client_secret,
            session=fake,
        )
        self.assertTrue(ok)
        self.assertEqual(status["status"], "ok")
        self.assertTrue(status["oauth"]["oauth_client_credentials_valid"])
        self.assertEqual(status["selected_scene"]["platform"], "sentinel-1c")
        self.assertEqual(status["selected_scene"]["asset_sizes_bytes"]["vv"], 590854769)
        self.assertFalse(status["raw_download_performed"])
        self.assertFalse(status["public_layer_modified"])
        self.assertFalse(status["gfw_products_modified"])
        self.assertFalse(status["magic_paws_modified"])
        self.assertEqual(
            status["catalogues"]["sentinel_hub"]["process_api_endpoint_for_next_phase"],
            "https://sh.dataspace.copernicus.eu/process/v1",
        )
        serialized = json.dumps(status)
        self.assertNotIn(client_id, serialized)
        self.assertNotIn(client_secret, serialized)
        self.assertNotIn(FIXTURE["token"]["access_token"], serialized)
        self.assertEqual([call["url"] for call in fake.calls], [
            catalog.CDSE_STAC_SEARCH_URL,
            catalog.CDSE_TOKEN_URL,
            catalog.SENTINEL_HUB_CATALOG_URL,
        ])

    def test_wrong_scene_fails_closed(self) -> None:
        fixture = deepcopy(FIXTURE)
        fixture["sentinel_hub"]["features"][0]["id"] = "S1C_DIFFERENT_PRODUCT"
        status, ok = catalog.run_catalog_pilot(
            CONFIG,
            "unit-test-client-id",
            "unit-test-client-secret",
            session=FakeSession(fixture),
        )
        self.assertFalse(ok)
        self.assertEqual(status["status"], "error")
        self.assertIn("expected one", status["errors"][0])
        self.assertIsNone(status["selected_scene"])

    def test_process_api_dimension_guard(self) -> None:
        config = deepcopy(CONFIG)
        config["processing_plan"]["expected_width_px"] = 2501
        with self.assertRaises(catalog.PilotError):
            catalog.validate_config(config)


if __name__ == "__main__":
    unittest.main()
