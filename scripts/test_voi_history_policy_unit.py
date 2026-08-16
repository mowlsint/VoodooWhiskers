#!/usr/bin/env python3
"""Synthetic regression test for VOI-history migration and size bounding."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from voi_history import compact_source_history, validate_jsonl


def row(dt: datetime, key: str, payload: str = "x") -> dict:
    return {
        "mmsi": key,
        "last_seen_utc": dt.isoformat(),
        "categories": ["watchlist"],
        "payload": payload,
        "_history_key": key,
    }


def main() -> int:
    now = datetime(2026, 8, 16, 8, 0, tzinfo=timezone.utc)
    policy = {
        "retention_days": 14,
        "source_max_bytes": 1300,
        "public_max_bytes": 1200,
        "public_filename": "voi_history_14d.jsonl",
        "future_tolerance_hours": 24,
    }
    with tempfile.TemporaryDirectory() as tmp_name:
        root = Path(tmp_name)
        history = root / "data" / "voi_history.jsonl"
        status = root / "data" / "voi_history_status.json"
        history.parent.mkdir(parents=True)
        initial = [
            row(now - timedelta(days=15), "expired"),
            row(now - timedelta(days=4), "duplicate", "old"),
            row(now - timedelta(days=3), "duplicate", "new"),
            {"_history_key": "missing-time"},
            row(now + timedelta(days=2), "future"),
        ]
        history.write_text(
            "\n".join(json.dumps(item) for item in initial) + "\n{malformed\n",
            encoding="utf-8",
        )
        new_rows = [
            row(now - timedelta(days=8), "new-1", "a" * 180),
            row(now - timedelta(days=7), "new-2", "b" * 180),
            row(now - timedelta(days=6), "new-3", "c" * 180),
            row(now - timedelta(days=5), "new-4", "d" * 180),
        ]
        stats = compact_source_history(history, new_rows, policy, status, now=now)
        validated = validate_jsonl(
            history,
            max_bytes=policy["source_max_bytes"],
            retention_days=policy["retention_days"],
            future_tolerance_hours=policy["future_tolerance_hours"],
            now=now,
        )
        kept = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines() if line]
        keys = {item["_history_key"] for item in kept}
        assert "expired" not in keys
        assert "future" not in keys
        assert "missing-time" not in keys
        assert sum(1 for item in kept if item["_history_key"] == "duplicate") == 1
        duplicate = next((item for item in kept if item["_history_key"] == "duplicate"), None)
        assert duplicate and duplicate["payload"] == "new"
        assert stats["malformed_rows"] == 1
        assert stats["missing_timestamp_rows"] == 1
        assert stats["outside_window_rows"] == 1
        assert stats["future_timestamp_rows"] == 1
        assert status.exists()
        assert validated["bytes"] <= policy["source_max_bytes"]
        print(json.dumps({"stats": stats, "validated": validated}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
