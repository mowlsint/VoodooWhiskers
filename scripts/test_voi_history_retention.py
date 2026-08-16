#!/usr/bin/env python3
"""Validate canonical shards and bounded 14-day VOI compatibility files."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voi_history import history_key, load_policy, validate_jsonl


ROOT = Path(__file__).resolve().parents[1]


def jsonl_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            key = history_key(row)
            if key in keys:
                raise AssertionError(f"duplicate history key at {path}:{line_number}: {key}")
            keys.add(key)
    return keys


def validate_shards(
    directory: Path,
    *,
    manifest_filename: str,
    max_part_bytes: int,
    retention_days: int,
    future_tolerance_hours: int,
    now: datetime,
) -> tuple[dict[str, Any], set[str]]:
    manifest_path = directory / manifest_filename
    if not manifest_path.is_file():
        raise AssertionError(f"history shard manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parts = manifest.get("parts") if isinstance(manifest.get("parts"), list) else None
    if parts is None:
        raise AssertionError(f"invalid history shard manifest: {manifest_path}")
    if int(manifest.get("max_part_bytes") or 0) != max_part_bytes:
        raise AssertionError(f"shard limit mismatch in {manifest_path}")
    if int(manifest.get("retention_days") or 0) != retention_days:
        raise AssertionError(f"retention mismatch in {manifest_path}")
    if int(manifest.get("part_count", -1)) != len(parts):
        raise AssertionError(f"part count mismatch in {manifest_path}")
    if manifest.get("complete_time_window") is not True:
        raise AssertionError(f"full shard set is not marked complete: {manifest_path}")

    total_rows = 0
    total_bytes = 0
    all_keys: set[str] = set()
    listed_names: set[str] = set()
    results = []
    for part in parts:
        filename = str(part.get("filename") or "") if isinstance(part, dict) else ""
        if not filename or Path(filename).name != filename or not filename.endswith(".jsonl"):
            raise AssertionError(f"unsafe shard filename in {manifest_path}: {filename!r}")
        if filename in listed_names:
            raise AssertionError(f"duplicate shard filename in {manifest_path}: {filename}")
        listed_names.add(filename)
        path = directory / filename
        result = validate_jsonl(
            path,
            max_bytes=max_part_bytes,
            retention_days=retention_days,
            future_tolerance_hours=future_tolerance_hours,
            now=now,
        )
        payload = path.read_bytes()
        if int(part.get("bytes") or -1) != len(payload):
            raise AssertionError(f"shard byte mismatch: {path}")
        if int(part.get("rows") or -1) != result["rows"]:
            raise AssertionError(f"shard row mismatch: {path}")
        if str(part.get("sha256") or "") != hashlib.sha256(payload).hexdigest():
            raise AssertionError(f"shard digest mismatch: {path}")
        part_keys = jsonl_keys(path)
        overlap = all_keys.intersection(part_keys)
        if overlap:
            raise AssertionError(f"history keys occur in multiple shards: {sorted(overlap)[:3]}")
        all_keys.update(part_keys)
        total_rows += result["rows"]
        total_bytes += len(payload)
        results.append(result)

    stale = {path.name for path in directory.glob("voi-history-*.jsonl")} - listed_names
    if stale:
        raise AssertionError(f"unlisted stale shards in {directory}: {sorted(stale)}")
    if int(manifest.get("total_rows") or 0) != total_rows:
        raise AssertionError(f"total shard row mismatch in {manifest_path}")
    if int(manifest.get("total_bytes") or 0) != total_bytes:
        raise AssertionError(f"total shard byte mismatch in {manifest_path}")
    return {
        "manifest": manifest_path.as_posix(),
        "parts": len(parts),
        "rows": total_rows,
        "bytes": total_bytes,
        "files": results,
    }, all_keys


def main() -> int:
    parser = argparse.ArgumentParser()
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--source-only", action="store_true")
    scope.add_argument("--public-only", action="store_true")
    args = parser.parse_args()

    policy = load_policy(ROOT)
    now = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    if not args.public_only:
        legacy_path = ROOT / "data" / "voi_history.jsonl"
        legacy_result = validate_jsonl(
            legacy_path,
            max_bytes=policy["source_max_bytes"],
            retention_days=policy["retention_days"],
            future_tolerance_hours=policy["future_tolerance_hours"],
            now=now,
        )
        shard_result, shard_keys = validate_shards(
            ROOT / "data" / policy["source_shard_directory"],
            manifest_filename="manifest.json",
            max_part_bytes=policy["source_shard_max_bytes"],
            retention_days=policy["retention_days"],
            future_tolerance_hours=policy["future_tolerance_hours"],
            now=now,
        )
        if not jsonl_keys(legacy_path).issubset(shard_keys):
            raise AssertionError("legacy source history contains rows absent from canonical shards")
        status_path = ROOT / "data" / "voi_history_status.json"
        if not status_path.exists():
            raise AssertionError(f"history status missing: {status_path}")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if int(status.get("retention_days", 0)) != policy["retention_days"]:
            raise AssertionError("source history status does not match retention policy")
        if int(status.get("file_bytes", -1)) != legacy_path.stat().st_size:
            raise AssertionError("source history status byte count is stale")
        if int(status.get("full_rows", -1)) != shard_result["rows"]:
            raise AssertionError("source history status full-row count is stale")
        if status.get("complete_time_window") is not True:
            raise AssertionError("canonical source history is not marked complete")
        results.extend([legacy_result, shard_result])

    if not args.source_only:
        compatibility_path = ROOT / "public" / "data" / "vessels" / policy["public_filename"]
        compatibility_result = validate_jsonl(
            compatibility_path,
            max_bytes=policy["public_max_bytes"],
            retention_days=policy["retention_days"],
            future_tolerance_hours=policy["future_tolerance_hours"],
            now=now,
        )
        shard_result, shard_keys = validate_shards(
            ROOT / "public" / "data" / "vessels" / policy["public_shard_directory"],
            manifest_filename=policy["public_shard_manifest"],
            max_part_bytes=policy["public_shard_max_bytes"],
            retention_days=policy["retention_days"],
            future_tolerance_hours=policy["future_tolerance_hours"],
            now=now,
        )
        compatibility_keys = jsonl_keys(compatibility_path)
        if not compatibility_keys.issubset(shard_keys):
            raise AssertionError("public compatibility history contains rows absent from full shards")
        manifest_path = ROOT / "public" / "data" / "vessels" / policy["public_shard_directory"] / policy["public_shard_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        selection = manifest.get("compatibility") if isinstance(manifest.get("compatibility"), dict) else {}
        if int(selection.get("selected_rows", -1)) != compatibility_result["rows"]:
            raise AssertionError("public compatibility row count is stale")
        if int(selection.get("selected_bytes", -1)) != compatibility_result["bytes"]:
            raise AssertionError("public compatibility byte count is stale")
        download_manifest = ROOT / "public" / "downloads" / policy["download_manifest_filename"]
        if not download_manifest.is_file():
            raise AssertionError(f"download shard manifest missing: {download_manifest}")
        download_payload = json.loads(download_manifest.read_text(encoding="utf-8"))
        if int(download_payload.get("total_rows", -1)) != shard_result["rows"]:
            raise AssertionError("download shard manifest row count is stale")
        results.extend([compatibility_result, shard_result])

    print(json.dumps({"policy": policy, "validated": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
