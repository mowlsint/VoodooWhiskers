#!/usr/bin/env python3
"""Validate the binding 14-day VOI source/public history policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from voi_history import load_policy, validate_jsonl


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--source-only", action="store_true")
    scope.add_argument("--public-only", action="store_true")
    args = parser.parse_args()

    policy = load_policy(ROOT)
    results = []
    if not args.public_only:
        results.append(validate_jsonl(
            ROOT / "data" / "voi_history.jsonl",
            max_bytes=policy["source_max_bytes"],
            retention_days=policy["retention_days"],
            future_tolerance_hours=policy["future_tolerance_hours"],
        ))
        status_path = ROOT / "data" / "voi_history_status.json"
        if not status_path.exists():
            raise AssertionError(f"history status missing: {status_path}")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if int(status.get("retention_days", 0)) != policy["retention_days"]:
            raise AssertionError("source history status does not match retention policy")
        if int(status.get("file_bytes", -1)) != (ROOT / "data" / "voi_history.jsonl").stat().st_size:
            raise AssertionError("source history status byte count is stale")

    if not args.source_only:
        results.append(validate_jsonl(
            ROOT / "public" / "data" / "vessels" / policy["public_filename"],
            max_bytes=policy["public_max_bytes"],
            retention_days=policy["retention_days"],
            future_tolerance_hours=policy["future_tolerance_hours"],
        ))

    print(json.dumps({"policy": policy, "validated": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
