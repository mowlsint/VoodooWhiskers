#!/usr/bin/env python3
"""Bounded VOI-history helpers shared by builders and regression tests."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_POLICY = {
    "schema_version": "1.0.0",
    "retention_days": 14,
    "source_max_bytes": 45 * 1024 * 1024,
    "source_shard_max_bytes": 20 * 1024 * 1024,
    "source_shard_directory": "voi_history_14d",
    "public_max_bytes": 22 * 1024 * 1024,
    "public_filename": "voi_history_14d.jsonl",
    "public_shard_max_bytes": 20 * 1024 * 1024,
    "public_shard_directory": "voi_history_14d",
    "public_shard_manifest": "manifest.json",
    "download_manifest_filename": "voi_history_14d_manifest.json",
    "future_tolerance_hours": 24,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace(" +0000 UTC", "+00:00").replace(" UTC", "+00:00")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_policy(root: Path) -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    path = root / "config" / "voi_history_policy.json"
    if path.exists():
        candidate = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            raise ValueError(f"{path} must contain a JSON object")
        policy.update(candidate)

    for key in (
        "retention_days",
        "source_max_bytes",
        "source_shard_max_bytes",
        "public_max_bytes",
        "public_shard_max_bytes",
        "future_tolerance_hours",
    ):
        policy[key] = int(policy[key])
    for key in (
        "source_shard_directory",
        "public_filename",
        "public_shard_directory",
        "public_shard_manifest",
        "download_manifest_filename",
    ):
        policy[key] = str(policy[key]).strip()

    if not 1 <= policy["retention_days"] <= 60:
        raise ValueError("VOI history retention_days must be between 1 and 60")
    if not 1024 * 1024 <= policy["source_max_bytes"] < 50 * 1024 * 1024:
        raise ValueError("VOI source history size guard must be between 1 MiB and 50 MiB")
    if not 1024 * 1024 <= policy["source_shard_max_bytes"] <= 22 * 1024 * 1024:
        raise ValueError("VOI source shard size guard must be between 1 MiB and 22 MiB")
    if not 1024 * 1024 <= policy["public_max_bytes"] <= 22 * 1024 * 1024:
        raise ValueError("VOI public compatibility history must be between 1 MiB and 22 MiB")
    if not 1024 * 1024 <= policy["public_shard_max_bytes"] <= 22 * 1024 * 1024:
        raise ValueError("VOI public shard size guard must be between 1 MiB and 22 MiB")
    if not policy["public_filename"].endswith(".jsonl") or "/" in policy["public_filename"]:
        raise ValueError("VOI public history filename must be a plain .jsonl filename")
    for key in ("source_shard_directory", "public_shard_directory"):
        value = policy[key]
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError(f"VOI {key} must be a plain directory name")
    for key in ("public_shard_manifest", "download_manifest_filename"):
        value = policy[key]
        if not value.endswith(".json") or "/" in value or "\\" in value:
            raise ValueError(f"VOI {key} must be a plain .json filename")
    if not 0 <= policy["future_tolerance_hours"] <= 48:
        raise ValueError("VOI future_tolerance_hours must be between 0 and 48")
    return policy


def history_dt(row: dict[str, Any]) -> datetime | None:
    for key in ("last_seen_utc", "observed_at", "last_ru_port_date", "generated_at", "slot"):
        dt = parse_dt(row.get(key))
        if dt:
            return dt
    history_key = str(row.get("_history_key") or "")
    if "|" in history_key:
        return parse_dt(history_key.split("|", 1)[0])
    return None


def history_key(row: dict[str, Any]) -> str:
    existing = str(row.get("_history_key") or "").strip()
    if existing:
        return existing
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "legacy:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compact_json_line(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="") as tmp:
        tmp.write(text)
        name = tmp.name
    Path(name).replace(path)


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def history_jsonl_paths(path: Path, policy: dict[str, Any]) -> list[Path]:
    """Return canonical source shards, falling back to the legacy single file.

    Once a shard manifest exists, it is authoritative. This prevents the capped
    legacy file from silently hiding older rows that are still inside retention.
    """
    shard_dir = path.parent / str(policy.get("source_shard_directory") or "voi_history_14d")
    manifest_path = shard_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        parts = manifest.get("parts") if isinstance(manifest, dict) else None
        if not isinstance(parts, list):
            raise ValueError(f"invalid VOI source shard manifest: {manifest_path}")
        result: list[Path] = []
        for part in parts:
            filename = str(part.get("filename") or "") if isinstance(part, dict) else ""
            if not filename or Path(filename).name != filename or not filename.endswith(".jsonl"):
                raise ValueError(f"invalid VOI source shard filename in {manifest_path}: {filename!r}")
            candidate = shard_dir / filename
            if not candidate.is_file():
                raise FileNotFoundError(candidate)
            result.append(candidate)
        return result
    loose_parts = sorted(shard_dir.glob("voi-history-*.jsonl")) if shard_dir.exists() else []
    if loose_parts:
        return loose_parts
    return [path] if path.exists() else []


def _encoded_history_entry(entry: tuple[datetime, str, dict[str, Any]]) -> tuple[datetime, str, dict[str, Any], str, bytes]:
    dt, key, row = entry
    text = compact_json_line(row)
    return dt, key, row, text, (text + "\n").encode("utf-8")


def write_jsonl_shards(
    directory: Path,
    entries: Iterable[tuple[datetime, str, dict[str, Any]]],
    *,
    max_part_bytes: int,
    retention_days: int,
    generated_at: str,
    kind: str,
    manifest_filename: str = "manifest.json",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write complete, day-oriented JSONL shards and an atomic manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    encoded = sorted((_encoded_history_entry(entry) for entry in entries), key=lambda item: (item[0], item[1]))
    by_day: dict[str, list[tuple[datetime, str, dict[str, Any], str, bytes]]] = defaultdict(list)
    for entry in encoded:
        if len(entry[4]) > max_part_bytes:
            raise ValueError(
                f"single VOI history row exceeds shard limit: {entry[1]} "
                f"({len(entry[4])} > {max_part_bytes} bytes)"
            )
        by_day[entry[0].date().isoformat()].append(entry)

    parts: list[dict[str, Any]] = []
    keep_names: set[str] = set()
    total_bytes = 0
    total_rows = 0
    for day in sorted(by_day):
        day_entries = by_day[day]
        batches: list[list[tuple[datetime, str, dict[str, Any], str, bytes]]] = []
        current: list[tuple[datetime, str, dict[str, Any], str, bytes]] = []
        current_bytes = 0
        for entry in day_entries:
            line_bytes = len(entry[4])
            if current and current_bytes + line_bytes > max_part_bytes:
                batches.append(current)
                current = []
                current_bytes = 0
            current.append(entry)
            current_bytes += line_bytes
        if current:
            batches.append(current)

        for part_number, batch in enumerate(batches, start=1):
            filename = f"voi-history-{day}-part-{part_number:03d}.jsonl"
            payload = b"".join(entry[4] for entry in batch)
            atomic_text(directory / filename, payload.decode("utf-8"))
            keep_names.add(filename)
            part = {
                "filename": filename,
                "href": f"./{filename}",
                "date_utc": day,
                "part": part_number,
                "rows": len(batch),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "oldest": batch[0][0].isoformat(),
                "newest": batch[-1][0].isoformat(),
            }
            parts.append(part)
            total_rows += len(batch)
            total_bytes += len(payload)

    for stale in directory.glob("voi-history-*.jsonl"):
        if stale.name not in keep_names:
            stale.unlink()

    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "kind": kind,
        "generated_at": generated_at,
        "retention_days": retention_days,
        "max_part_bytes": max_part_bytes,
        "part_count": len(parts),
        "total_rows": total_rows,
        "total_bytes": total_bytes,
        "oldest": encoded[0][0].isoformat() if encoded else None,
        "newest": encoded[-1][0].isoformat() if encoded else None,
        "complete_time_window": True,
        "parts": parts,
    }
    if metadata:
        manifest.update(metadata)
    atomic_json(directory / manifest_filename, manifest)
    return manifest


def history_identity(row: dict[str, Any], key: str) -> str:
    for field in ("mmsi", "imo", "callsign", "name"):
        value = re.sub(r"\s+", " ", str(row.get(field) or "").strip().lower())
        if value and value not in {"0", "unknown", "none", "null", "n/a"}:
            return f"{field}:{value}"
    return f"row:{key}"


def _balanced_interior_positions(length: int) -> list[int]:
    if length <= 2:
        return []
    result: list[int] = []
    intervals: deque[tuple[int, int]] = deque([(1, length - 2)])
    while intervals:
        low, high = intervals.popleft()
        if low > high:
            continue
        middle = (low + high) // 2
        result.append(middle)
        intervals.append((low, middle - 1))
        intervals.append((middle + 1, high))
    return result


def select_balanced_history(
    entries: Iterable[tuple[datetime, str, dict[str, Any]]],
    *,
    max_bytes: int,
) -> tuple[list[tuple[datetime, str, dict[str, Any]]], dict[str, Any]]:
    """Build a capped compatibility view while retaining broad vessel/time coverage."""
    ordered = sorted(entries, key=lambda item: (item[0], item[1]))
    encoded = [_encoded_history_entry(entry) for entry in ordered]
    total_bytes = sum(len(item[4]) for item in encoded)
    identities = {history_identity(item[2], item[1]) for item in encoded}
    if total_bytes <= max_bytes:
        return ordered, {
            "selection": "complete",
            "total_rows": len(ordered),
            "selected_rows": len(ordered),
            "selected_bytes": total_bytes,
            "total_identities": len(identities),
            "covered_identities": len(identities),
            "complete_time_window": True,
        }

    groups: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(encoded):
        groups[history_identity(item[2], item[1])].append(index)
    group_order = sorted(
        groups,
        key=lambda identity: (
            not any(bool(encoded[index][2].get("is_priority_voi")) for index in groups[identity]),
            -encoded[groups[identity][-1]][0].timestamp(),
            identity,
        ),
    )

    selected_indexes: set[int] = set()
    selected_bytes = 0

    def try_add(index: int) -> None:
        nonlocal selected_bytes
        if index in selected_indexes:
            return
        line_bytes = len(encoded[index][4])
        if selected_bytes + line_bytes <= max_bytes:
            selected_indexes.add(index)
            selected_bytes += line_bytes

    # Latest observation first gives every identity a current anchor. Earliest
    # observation second preserves span. Midpoints are then added round-robin.
    for identity in group_order:
        try_add(groups[identity][-1])
    for identity in group_order:
        try_add(groups[identity][0])

    sequences = {
        identity: deque(groups[identity][position] for position in _balanced_interior_positions(len(groups[identity])))
        for identity in group_order
    }
    while any(sequences.values()):
        for identity in group_order:
            if sequences[identity]:
                try_add(sequences[identity].popleft())

    selected = [ordered[index] for index in sorted(selected_indexes)]
    covered = {history_identity(row, key) for _dt, key, row in selected}
    return selected, {
        "selection": "identity_balanced_temporal_sampling",
        "total_rows": len(ordered),
        "selected_rows": len(selected),
        "selected_bytes": selected_bytes,
        "dropped_rows": len(ordered) - len(selected),
        "total_identities": len(identities),
        "covered_identities": len(covered),
        "identity_coverage_percent": round(100 * len(covered) / len(identities), 2) if identities else 100.0,
        "complete_time_window": len(selected) == len(ordered),
    }


def retained_rows(
    path: Path,
    policy: dict[str, Any],
    *,
    max_age_days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    paths = history_jsonl_paths(path, policy)
    if not paths:
        return []
    current = (now or utc_now()).astimezone(timezone.utc)
    cutoff = current - timedelta(days=max_age_days or policy["retention_days"])
    future_limit = current + timedelta(hours=policy["future_tolerance_hours"])
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_path in paths:
        with source_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                dt = history_dt(row)
                key = history_key(row)
                if dt and cutoff <= dt <= future_limit and key not in seen:
                    seen.add(key)
                    rows.append(row)
    return rows


def compact_source_history(
    path: Path,
    new_rows: Iterable[dict[str, Any]],
    policy: dict[str, Any],
    status_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(timezone.utc)
    cutoff = current - timedelta(days=policy["retention_days"])
    future_limit = current + timedelta(hours=policy["future_tolerance_hours"])
    stats: dict[str, Any] = {
        "source_rows": 0,
        "new_rows_seen": 0,
        "malformed_rows": 0,
        "missing_timestamp_rows": 0,
        "outside_window_rows": 0,
        "future_timestamp_rows": 0,
        "duplicate_rows": 0,
        "legacy_oversized_single_rows": 0,
        "legacy_dropped_for_size": 0,
    }
    by_key: dict[str, tuple[datetime, dict[str, Any]]] = {}

    def consider(row: Any, *, is_new: bool = False) -> None:
        if is_new:
            stats["new_rows_seen"] += 1
        else:
            stats["source_rows"] += 1
        if not isinstance(row, dict):
            stats["malformed_rows"] += 1
            return
        dt = history_dt(row)
        if not dt:
            stats["missing_timestamp_rows"] += 1
            return
        if dt < cutoff:
            stats["outside_window_rows"] += 1
            return
        if dt > future_limit:
            stats["future_timestamp_rows"] += 1
            return
        key = history_key(row)
        clean = dict(row)
        clean.setdefault("_history_key", key)
        previous = by_key.get(key)
        if previous is not None:
            stats["duplicate_rows"] += 1
            if previous[0] > dt:
                return
        by_key[key] = (dt, clean)

    for source_path in history_jsonl_paths(path, policy):
        with source_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    consider(json.loads(line))
                except json.JSONDecodeError:
                    stats["source_rows"] += 1
                    stats["malformed_rows"] += 1

    for row in new_rows:
        consider(row, is_new=True)

    ordered = sorted(((dt, key, row) for key, (dt, row) in by_key.items()), key=lambda item: (item[0], item[1]))
    generated_at = current.replace(microsecond=0).isoformat()
    shard_dir = path.parent / str(policy.get("source_shard_directory") or "voi_history_14d")
    full_manifest = write_jsonl_shards(
        shard_dir,
        ordered,
        max_part_bytes=int(policy.get("source_shard_max_bytes") or 20 * 1024 * 1024),
        retention_days=policy["retention_days"],
        generated_at=generated_at,
        kind="canonical_source_history",
        metadata={
            "legacy_compatibility_file": path.name,
            "legacy_max_bytes": policy["source_max_bytes"],
            "note": "The shards are canonical. The single legacy file may be size-bounded without losing rows from the shard set.",
        },
    )

    selected_reverse: list[tuple[datetime, str]] = []
    byte_count = 0
    max_bytes = policy["source_max_bytes"]
    for dt, _key, row in reversed(ordered):
        text = compact_json_line(row)
        line_bytes = len((text + "\n").encode("utf-8"))
        if line_bytes > max_bytes:
            stats["legacy_oversized_single_rows"] += 1
            continue
        if byte_count + line_bytes > max_bytes:
            stats["legacy_dropped_for_size"] += len(ordered) - len(selected_reverse)
            break
        selected_reverse.append((dt, text))
        byte_count += line_bytes
    selected = list(reversed(selected_reverse))
    atomic_text(path, "\n".join(text for _dt, text in selected) + ("\n" if selected else ""))

    stats.update({
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "history_path": path.as_posix(),
        "retention_days": policy["retention_days"],
        "max_bytes": max_bytes,
        "kept_rows": len(selected),
        "file_bytes": byte_count,
        "oldest_kept_at": selected[0][0].isoformat() if selected else None,
        "newest_kept_at": selected[-1][0].isoformat() if selected else None,
        "legacy_complete_time_window": stats["legacy_dropped_for_size"] == 0 and stats["legacy_oversized_single_rows"] == 0,
        "full_manifest_path": (shard_dir / "manifest.json").as_posix(),
        "full_rows": full_manifest["total_rows"],
        "full_bytes": full_manifest["total_bytes"],
        "full_part_count": full_manifest["part_count"],
        "full_oldest_at": full_manifest["oldest"],
        "full_newest_at": full_manifest["newest"],
        "complete_time_window": True,
        "oversized_single_rows": 0,
        "dropped_for_size": 0,
        "policy_note": (
            f"All valid rows from the latest {policy['retention_days']} days are retained in canonical shards. "
            "The single legacy JSONL file keeps newest complete rows only when its byte guard is reached."
        ),
    })
    atomic_json(status_path, stats)
    return stats


def validate_jsonl(
    path: Path,
    *,
    max_bytes: int,
    retention_days: int,
    future_tolerance_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not path.exists():
        raise AssertionError(f"history file missing: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise AssertionError(f"history file exceeds policy: {path} ({size} > {max_bytes} bytes)")
    current = (now or utc_now()).astimezone(timezone.utc)
    cutoff = current - timedelta(days=retention_days)
    future_limit = current + timedelta(hours=future_tolerance_hours)
    keys: set[str] = set()
    rows = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"malformed JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise AssertionError(f"non-object JSONL row at {path}:{line_number}")
            dt = history_dt(row)
            if not dt:
                raise AssertionError(f"missing history timestamp at {path}:{line_number}")
            if dt < cutoff - timedelta(minutes=5) or dt > future_limit:
                raise AssertionError(f"history timestamp outside policy at {path}:{line_number}: {dt.isoformat()}")
            key = history_key(row)
            if key in keys:
                raise AssertionError(f"duplicate history key at {path}:{line_number}: {key}")
            keys.add(key)
            rows += 1
            oldest = dt if oldest is None or dt < oldest else oldest
            newest = dt if newest is None or dt > newest else newest
    return {
        "path": path.as_posix(),
        "rows": rows,
        "bytes": size,
        "oldest": oldest.isoformat() if oldest else None,
        "newest": newest.isoformat() if newest else None,
        "retention_days": retention_days,
        "max_bytes": max_bytes,
    }
