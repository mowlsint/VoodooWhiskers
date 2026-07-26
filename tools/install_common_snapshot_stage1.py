#!/usr/bin/env python3
"""Validate the direct Stage 1 replacement files before building first products.

Version 0.2.1 ships complete replacement files derived from the Voodoo Whiskers
files supplied on 2026-07-26. This validator deliberately performs no text patching.
It fails closed when a required file or end-state marker is missing.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_MARKERS = {
    ROOT / ".github/workflows/update-regional-ais.yml": [
        "Archive regional and Danish AIS snapshots",
        "Build harmonized common maritime snapshot",
        "maritime_source_coverage_21d.jsonl",
        "maritime_common_snapshot_status.json",
    ],
    ROOT / "scripts/build_public_manifest.py": [
        "Harmonized common maritime snapshot — JSON",
        '"common_maritime_snapshot"',
        '"common_maritime_snapshot_status"',
    ],
    ROOT / "public/index.html": [
        'id="commonSnapshotBanner"',
        "common-snapshot-status.css",
        "common-snapshot-status.js",
        "Harmonized common AIS snapshot",
        'data-time-mode="latest">Common snapshot',
    ],
    ROOT / "public/assets/infrastructure-watch.js": [
        'commonSnapshot:"./data/vessels/maritime_common_snapshot_latest.geojson"',
        'commonStatus:"./data/vessels/maritime_common_snapshot_status.json"',
        "function applyCommonCategoryLayers()",
        "state.data.common_snapshot=commonSnapshot;",
        "state.data.common_status=commonStatus;",
        "Common snapshot unavailable",
        "restoreProviderCategoryLayers();",
        "<b>Common snapshot</b>",
    ],
}

FORBIDDEN_MARKERS = {
    ROOT / "public/assets/infrastructure-watch.js": [
        '$("timeStatus").textContent=commonReady ? `Common ${commonDate} UTC` : "Provider-latest fallback";',
    ],
}


def main() -> int:
    for path, markers in REQUIRED_MARKERS.items():
        if not path.exists():
            raise SystemExit(f"required Stage 1 file missing: {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8")
        missing = [marker for marker in markers if marker not in text]
        if missing:
            raise SystemExit(f"Stage 1 validation failed for {path.relative_to(ROOT)}: missing {missing}")

    for path, markers in FORBIDDEN_MARKERS.items():
        text = path.read_text(encoding="utf-8")
        present = [marker for marker in markers if marker in text]
        if present:
            raise SystemExit(f"Stage 1 validation failed for {path.relative_to(ROOT)}: obsolete markers {present}")

    config = json.loads((ROOT / "config/common_snapshot.json").read_text(encoding="utf-8"))
    regions = json.loads((ROOT / "data/common_snapshot_regions.geojson").read_text(encoding="utf-8"))
    if not config.get("mandatory_sources"):
        raise SystemExit("common_snapshot.json has no mandatory_sources")
    if regions.get("type") != "FeatureCollection" or not regions.get("features"):
        raise SystemExit("common_snapshot_regions.geojson has no query regions")

    print("Stage 1 direct replacements and configuration validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
