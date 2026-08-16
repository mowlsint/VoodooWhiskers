#!/usr/bin/env python3
"""Bounded VOI-history helpers shared by builders and regression tests."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_POLICY = {
    "schema_version": "1.0.0",
    "retention_days": 20,
    "source_max_bytes": 45 * 1024 * 1024,
    "public_max_bytes": 40 * 1024 * 1024,
    "public_filename": "voi_history_14d.jsonl",
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

    for key in ("retention_days", "source_max_bytes", "public_max_bytes", "future_tolerance_hours"):
        policy[key] = int(policy[key])
    policy["public_filename"] = str(policy["public_filename"]).strip()

    if not 1 <= policy["retention_days"] <= 60:
        raise ValueError("VOI history retention_days must be between 1 and 60")
    if not 1024 * 1024 <= policy["source_max_bytes"] < 50 * 1024 * 1024:
        raise ValueError("VOI source history size guard must be between 1 MiB and 50 MiB")
    if not 1024 * 1024 <= policy["public_max_bytes"] < 50 * 1024 * 1024:
        raise ValueError("VOI public history size guard must be between 1 MiB and 50 MiB")
    if not policy["public_filename"].endswith(".jsonl") or "/" in policy["public_filename"]:
        raise ValueError("VOI public history filename must be a plain .jsonl filename")
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


def retained_rows(
    path: Path,
    policy: dict[str, Any],
    *,
    max_age_days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    current = (now or utc_now()).astimezone(timezone.utc)
    cutoff = current - timedelta(days=max_age_days or policy["retention_days"])
    future_limit = current + timedelta(hours=policy["future_tolerance_hours"])
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
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
            if dt and cutoff <= dt <= future_limit:
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
        "oversized_single_rows": 0,
        "dropped_for_size": 0,
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

    if path.exists():
        with path.open("r", encoding="utf-8", errors="replace") as handle:
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
    selected_reverse: list[tuple[datetime, str]] = []
    byte_count = 0
    max_bytes = policy["source_max_bytes"]
    for index, (dt, _key, row) in enumerate(reversed(ordered)):
        text = compact_json_line(row)
        line_bytes = len((text + "\n").encode("utf-8"))
        if line_bytes > max_bytes:
            stats["oversized_single_rows"] += 1
            continue
        if byte_count + line_bytes > max_bytes:
            stats["dropped_for_size"] += len(ordered) - index
            break
        selected_reverse.append((dt, text))
        byte_count += line_bytes
    selected = list(reversed(selected_reverse))
    atomic_text(path, "\n".join(text for _dt, text in selected) + ("\n" if selected else ""))

    stats.update({
        "schema_version": "1.0.0",
        "generated_at": current.replace(microsecond=0).isoformat(),
        "history_path": path.as_posix(),
        "retention_days": policy["retention_days"],
        "max_bytes": max_bytes,
        "kept_rows": len(selected),
        "file_bytes": byte_count,
        "oldest_kept_at": selected[0][0].isoformat() if selected else None,
        "newest_kept_at": selected[-1][0].isoformat() if selected else None,
        "complete_time_window": stats["dropped_for_size"] == 0 and stats["oversized_single_rows"] == 0,
        "policy_note": f"At most {policy['retention_days']} days are retained. If the byte guard is reached, the newest complete JSONL rows are kept and the truncation is reported here.",
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
