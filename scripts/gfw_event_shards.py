"""Publish GFW's delayed 14-day event context as bounded, verifiable JSON parts.

The source event product remains canonical; these public shards are a lossless
representation of all retained events. Fail closed if one event cannot fit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PART_MAX_BYTES = 2 * 1024 * 1024
PART_TARGET_BYTES = 1536 * 1024
PART_PREFIX = "part-"


def packed(payload):
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_event_shards(source, directory, atomic_json):
    """Return a complete shard manifest or raise; never silently omit an event."""
    if not isinstance(source, dict) or source.get("historical") is not True:
        raise ValueError("GFW shard source must be historical")
    events = source.get("events")
    if not isinstance(events, list):
        raise ValueError("GFW shard source must contain an event list")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    parts, group, group_bytes = [], [], 0

    def flush():
        nonlocal group, group_bytes
        if not group:
            return
        filename = f"{PART_PREFIX}{len(parts)+1:04d}.json"
        pack = {"schema_version": "1.0.0", "historical": True,
                "events": group, "count": len(group)}
        body = packed(pack)
        if len(body) > PART_MAX_BYTES:
            raise ValueError("GFW event shard exceeds 2 MiB")
        atomic_json(directory / filename, pack)
        parts.append({"filename": filename, "href": "./" + filename,
                      "event_count": len(group), "bytes": len(body),
                      "sha256": hashlib.sha256(body).hexdigest()})
        group, group_bytes = [], 0

    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid GFW event")
        amount = len(packed(event))
        if amount + 256 > PART_MAX_BYTES:
            raise ValueError("Individual GFW event exceeds shard limit")
        if group and group_bytes + amount + 256 > PART_TARGET_BYTES:
            flush()
        group.append(event)
        group_bytes += amount
    flush()
    if sum(p["event_count"] for p in parts) != len(events):
        raise ValueError("GFW shard event count mismatch")
    manifest = {
        "schema_version": "1.0.0",
        "kind": "gfw_events_14d",
        "historical": True,
        "not_current_positions": True,
        "generated_at": source.get("generated_at"),
        "coverage_start_date": source.get("coverage_start_date"),
        "coverage_end_date_exclusive": source.get("coverage_end_date_exclusive"),
        "coverage_complete": source.get("coverage_complete") is True,
        "status": source.get("status"),
        "retention_days": 14,
        "source": "Global Fishing Watch",
        "license": source.get("license"),
        "attribution": source.get("attribution"),
        "event_count": len(events),
        "part_count": len(parts),
        "total_part_bytes": sum(p["bytes"] for p in parts),
        "max_part_bytes": PART_MAX_BYTES,
        "parts": parts,
    }
    atomic_json(directory / "manifest.json", manifest)
    retained = {p["filename"] for p in parts}
    for old in directory.glob(f"{PART_PREFIX}*.json"):
        if old.name not in retained:
            old.unlink()
    return manifest
