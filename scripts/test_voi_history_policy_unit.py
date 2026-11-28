#!/usr/bin/env python3
"""Synthetic regression test for VOI-history migration and size bounding."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from voi_history import (
    compact_source_history,
    history_dt,
    history_jsonl_paths,
    history_key,
    select_balanced_history,
    validate_jsonl,
)


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
        "source_shard_max_bytes": 700,
        "source_shard_directory": "voi_history_14d",
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
            row(now - timedelta(days=10) + timedelta(hours=index), f"new-{index:02d}", chr(97 + index % 20) * 180)
            for index in range(12)
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
        full_rows = []
        for part in history_jsonl_paths(history, policy):
            full_rows.extend(json.loads(line) for line in part.read_text(encoding="utf-8").splitlines() if line)
        full_keys = {item["_history_key"] for item in full_rows}
        assert "expired" not in keys
        assert "future" not in keys
        assert "missing-time" not in keys
        assert "expired" not in full_keys
        assert "future" not in full_keys
        assert "missing-time" not in full_keys
        assert sum(1 for item in full_rows if item["_history_key"] == "duplicate") == 1
        duplicate = next((item for item in full_rows if item["_history_key"] == "duplicate"), None)
        assert duplicate and duplicate["payload"] == "new"
        assert stats["malformed_rows"] == 1
        assert stats["missing_timestamp_rows"] == 1
        assert stats["outside_window_rows"] == 1
        assert stats["future_timestamp_rows"] == 1
        assert status.exists()
        assert validated["bytes"] <= policy["source_max_bytes"]
        assert keys.issubset(full_keys)
        assert len(full_rows) == 13
        assert stats["full_rows"] == len(full_rows)
        assert stats["full_part_count"] > 1
        assert stats["legacy_dropped_for_size"] > 0
        assert stats["dropped_for_size"] == 0
        sample_rows = []
        for vessel in range(5):
            for observation in range(6):
                item = row(
                    now - timedelta(days=6 - observation, minutes=vessel),
                    f"sample-{vessel}-{observation}",
                    "z" * 40,
                )
                item["mmsi"] = f"vessel-{vessel}"
                sample_rows.append(item)
        entries = [(history_dt(item), history_key(item), item) for item in sample_rows]
        balanced, selection = select_balanced_history(entries, max_bytes=1800)
        assert len(balanced) < len(entries)
        assert selection["covered_identities"] == selection["total_identities"]
        assert selection["complete_time_window"] is False
        print(json.dumps({"stats": stats, "validated": validated}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
