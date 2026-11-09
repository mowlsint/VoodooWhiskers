"""Offline parser/area/state checks; never require live AIS credentials."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "live_ais" / "collector.py"
spec = importlib.util.spec_from_file_location("live_ais_collector", FILE)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class LiveAISTests(unittest.TestCase):
    def test_area_limits(self):
        streams = collector.load_boxes(FILE.parent / "bboxes.json")
        self.assertEqual([s["area_deg2"] for s in streams], [399.0, 400.0])

    def test_static_frame_does_not_create_position(self):
        value = collector.from_openwaters({
            "type": "event", "mmsi": 257123456, "time": "2026-11-09T12:00:00Z",
            "msg_type": "ShipStaticData", "lat": 60, "lon": 10,
            "message": {"Name": "TEST SHIP"},
        })
        self.assertIsNone(value["latitude"])
        self.assertIsNone(value["last_seen_utc"])
        self.assertEqual(value["name"], "TEST SHIP")

    def test_position_source_and_sentinels(self):
        value = collector.from_openwaters({
            "type": "event", "mmsi": 257123456, "time": "2026-11-09T12:00:00Z",
            "source": "digitraffic", "msg_type": "PositionReport",
            "lat": 60, "lon": 10,
            "message": {"Sog": 102.3, "Cog": 360, "TrueHeading": 511},
        })
        self.assertEqual(value["source_stream"], "digitraffic")
        self.assertEqual((value["latitude"], value["longitude"]), (60, 10))
        self.assertIsNone(value["sog"])
        self.assertIsNone(value["cog"])
        self.assertIsNone(value["true_heading"])

    def test_fintraffic_unix_seconds(self):
        value = collector.from_fintraffic(
            "vessels-v2/257123456/locations",
            {"time": 1794225600, "lat": 60, "lon": 20}
        )
        self.assertTrue(value["last_seen_utc"].startswith("2026-11-09"))

    def test_timestamp_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            store = collector.Store(Path(folder) / "db.sqlite3", {"257123456"})
            base = {"mmsi": "257123456", "latitude": 60.0,
                    "longitude": 10.0,
                    "last_seen_utc": "2026-11-09T12:00:00.000000+00:00",
                    "name": "", "imo": ""}
            with store.db:
                store.apply({**base, "source_provider": "openwaters"})
                store.apply({**base, "source_provider": "barentswatch", "latitude": 61.0})
            row = store.db.execute("SELECT payload FROM latest").fetchone()[0]
            self.assertEqual(json.loads(row)["latitude"], 61.0)
            self.assertEqual(store.db.execute(
                "SELECT count(*) FROM tracked_positions").fetchone()[0], 2)
            store.db.close()

    def test_bad_timestamp_not_replaced(self):
        value = collector.from_barentswatch(
            {"mmsi": 257123456, "latitude": 60, "longitude": 10,
             "msgtime": "invalid"}
        )
        self.assertIsNone(value["last_seen_utc"])
        self.assertFalse(value["position_timestamp_valid"])


if __name__ == "__main__":
    unittest.main()
