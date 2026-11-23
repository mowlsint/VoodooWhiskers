"""Regression tests for complete, bounded delayed GFW event shards."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from data_retention import bound_gfw
from gfw_event_shards import PART_MAX_BYTES, write_event_shards


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8")


class GfwEventShardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 23, tzinfo=timezone.utc)

    def sample(self, n, padding=100):
        start = (self.now - timedelta(days=7)).isoformat()
        end = (self.now - timedelta(days=6)).isoformat()
        return [{"id": str(i), "type": "LOITERING" if i%2 else "GAP",
                 "start": start, "end": end, "details": "x" * padding}
                for i in range(n)]

    def source(self, rows):
        return {"schema_version": "1.0.0", "historical": True,
                "coverage_complete": True, "status": "complete",
                "generated_at": self.now.isoformat(),
                "coverage_start_date": "2026-11-09",
                "coverage_end_date_exclusive": "2026-11-19",
                "events": rows}

    def test_full_shards_retain_rows_beyond_legacy_preview_limit(self):
        rows = self.sample(1000, 5200)
        complete = bound_gfw(self.source(rows), "events", now=self.now,
                             max_bytes=12*1024*1024-128*1024)
        legacy = bound_gfw(self.source(rows), "events", now=self.now,
                           max_bytes=4*1024*1024-128*1024)
        self.assertEqual(len(complete["events"]), len(rows))
        self.assertFalse(complete["cap_applied"])
        self.assertLess(len(legacy["events"]), len(rows))
        self.assertTrue(legacy["cap_applied"])
        manifest = write_event_shards(complete, self.root, write_json)
        self.assertGreater(manifest["part_count"], 1)
        self.assertEqual(manifest["event_count"], len(rows))
        restored = []
        for part in manifest["parts"]:
            path = self.root / part["filename"]
            self.assertEqual(path.stat().st_size, part["bytes"])
            self.assertLessEqual(path.stat().st_size, PART_MAX_BYTES)
            payload = json.loads(path.read_text())
            self.assertEqual(payload["count"], part["event_count"])
            restored += payload["events"]
        self.assertEqual({r["id"] for r in restored}, {r["id"] for r in rows})

    def test_republishing_prunes_only_obsolete_shards(self):
        manifest = write_event_shards(self.source(self.sample(250, 8500)),
                                      self.root, write_json)
        self.assertGreater(manifest["part_count"], 1)
        replacement = write_event_shards(self.source(self.sample(3)),
                                         self.root, write_json)
        self.assertEqual(replacement["part_count"], 1)
        self.assertEqual({p.name for p in self.root.glob("part-*.json")},
                         {"part-0001.json"})

    def test_oversize_event_fails_without_silent_drop(self):
        with self.assertRaisesRegex(ValueError, "Individual GFW event"):
            write_event_shards(self.source(self.sample(1, PART_MAX_BYTES)),
                               self.root, write_json)

    def test_manifest_rejects_nonhistorical_source(self):
        with self.assertRaisesRegex(ValueError, "historical"):
            write_event_shards({"events": self.sample(1)}, self.root, write_json)


if __name__ == "__main__":
    unittest.main()
