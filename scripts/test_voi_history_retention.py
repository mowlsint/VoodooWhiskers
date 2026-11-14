#!/usr/bin/env python3
"""Validate canonical shards and bounded 14-day VOI compatibility files."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voi_history import history_identity, history_key, load_policy, validate_jsonl


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


def validate_capacity(capacity: dict[str, Any], *, rows: int, size: int,
                      identities: int, complete: bool) -> None:
    """Accept sampling only with explicit, internally consistent capacity evidence."""
    def count(name: str) -> int:
        value = capacity.get(name)
        if type(value) is not int or value < 0:
            raise AssertionError(f"invalid capacity count: {name}")
        return value

    total = count('total_rows')
    total_identities = count('total_identities')
    if count('selected_rows') != rows or count('selected_bytes') != size:
        raise AssertionError('capacity selected row/byte count mismatch')
    if count('covered_identities') != identities:
        raise AssertionError('capacity covered identity count mismatch')
    if not rows <= total or not identities <= total_identities <= total:
        raise AssertionError('capacity totals contradict selected data')
    if count('dropped_rows') != total - rows:
        raise AssertionError('capacity dropped row count mismatch')
    if capacity.get('complete_time_window') is not complete or complete != (total == rows):
        raise AssertionError('capacity completeness contradicts row counts')
    expected_selection = 'complete' if complete else 'identity_balanced_temporal_sampling'
    if capacity.get('selection') != expected_selection:
        raise AssertionError('capacity selection contradicts completeness')
    if complete and total_identities != identities:
        raise AssertionError('complete history is missing identities')
    if 'identity_coverage_percent' in capacity:
        percent = capacity['identity_coverage_percent']
        expected = round(100 * identities / total_identities, 2) if total_identities else 100.0
        if type(percent) not in (int, float) or not math.isfinite(percent) or percent != expected:
            raise AssertionError('capacity identity coverage percentage mismatch')


def validate_shards(
    directory: Path,
    *,
    manifest_filename: str,
    max_part_bytes: int,
    retention_days: int,
    future_tolerance_hours: int,
    now: datetime,
    total_max_bytes: int,
    capacity_key: str,
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
    complete = manifest.get('complete_time_window')
    if type(complete) is not bool:
        raise AssertionError(f"history completeness is not explicit: {manifest_path}")
    if 'total_max_bytes' in manifest and manifest['total_max_bytes'] != total_max_bytes:
        raise AssertionError(f"total byte policy mismatch: {manifest_path}")

    total_rows = 0
    total_bytes = 0
    all_keys: set[str] = set()
    all_identities: set[str] = set()
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
        for line in payload.decode('utf-8').splitlines():
            if line.strip():
                row = json.loads(line)
                all_identities.add(history_identity(row, history_key(row)))
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
    if total_bytes > total_max_bytes:
        raise AssertionError(f"total shard bytes exceed policy: {manifest_path}")
    capacity = manifest.get(capacity_key)
    if capacity is None:
        if not complete:
            raise AssertionError(f"sampled history has no capacity metadata: {manifest_path}")
    elif not isinstance(capacity, dict):
        raise AssertionError(f"invalid history capacity metadata: {manifest_path}")
    else:
        # Complete selections produced by older/current builders omit dropped_rows.
        evidence = dict(capacity)
        if complete:
            evidence.setdefault('dropped_rows', 0)
        validate_capacity(evidence, rows=total_rows, size=total_bytes,
                          identities=len(all_identities), complete=complete)
    return {
        "manifest": manifest_path.as_posix(),
        "parts": len(parts),
        "rows": total_rows,
        "bytes": total_bytes,
        "complete_time_window": complete,
        "capacity": capacity,
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
            total_max_bytes=policy['source_total_max_bytes'],
            capacity_key='capacity',
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
        if status.get("complete_time_window") is not shard_result['complete_time_window']:
            raise AssertionError("source history status completeness contradicts shards")
        if int(status.get('full_bytes', -1)) != shard_result['bytes']:
            raise AssertionError('source history status full-byte count is stale')
        capacity = shard_result['capacity'] or {}
        if int(status.get('dropped_for_size', 0)) != capacity.get('dropped_rows', 0):
            raise AssertionError('source history status dropped-row count contradicts capacity')
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
            total_max_bytes=policy['public_total_max_bytes'],
            capacity_key='total_capacity',
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
        if not manifest['complete_time_window'] and selection.get('complete_time_window') is not False:
            raise AssertionError('public compatibility hides incomplete canonical history')
        download_manifest = ROOT / "public" / "downloads" / policy["download_manifest_filename"]
        if not download_manifest.is_file():
            raise AssertionError(f"download shard manifest missing: {download_manifest}")
        download_payload = json.loads(download_manifest.read_text(encoding="utf-8"))
        if int(download_payload.get("total_rows", -1)) != shard_result["rows"]:
            raise AssertionError("download shard manifest row count is stale")
        for key in ('total_bytes', 'part_count', 'complete_time_window', 'total_capacity'):
            if download_payload.get(key) != manifest.get(key):
                raise AssertionError(f'download shard manifest mismatch: {key}')
        def part_identity(part):
            return {key: part.get(key) for key in ('filename', 'rows', 'bytes', 'sha256')}
        if [part_identity(p) for p in download_payload.get('parts', [])] != [part_identity(p) for p in manifest['parts']]:
            raise AssertionError('download shard manifest parts are stale')
        results.extend([compatibility_result, shard_result])

    print(json.dumps({"policy": policy, "validated": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
