#!/usr/bin/env python3
"""Create public data/download manifests after all Voodoo Whiskers builders ran."""

from __future__ import annotations

import json
import mimetypes
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        name = tmp.name
    Path(name).replace(path)


def file_entry(path: Path, href: str, label: str, group: str, description: str) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    return {
        "id": path.stem,
        "label": label,
        "group": group,
        "description": description,
        "href": href,
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }


def main() -> int:
    generated_at = now_iso()
    products = []
    specs = [
        ("ais_contacts_latest.json", "AISStream or historical fallback contacts — JSON", "ais", "Filtered AISStream contacts when live; otherwise explicitly labelled historical fallback data from regional providers."),
        ("ais_contacts_latest.geojson", "AISStream or historical fallback positions — GeoJSON", "ais", "Map-ready AISStream positions when live; otherwise explicitly labelled historical fallback data from regional providers."),
        ("ais_dk_last_two_positions.json", "Danish historical AIS — latest two observations — JSON", "ais_history", "Latest two temporally distinct historical Danish AIS observations per vessel matched to a known Voodoo watchlist/VOI category. Delayed data; not current vessel positions."),
        ("ais_dk_last_two_positions.geojson", "Danish historical AIS — latest two observations — GeoJSON", "ais_history", "Map-ready latest two historical Danish AIS observations and short connectors per vessel matched to a known Voodoo watchlist/VOI category. Delayed data; not current vessel positions."),
        ("ais_dk_import_status.json", "Danish historical AIS — import status", "ais_history", "Source date, lag, row-quality counters and import health for the delayed Danish AIS supplement."),
        ("maritime_common_snapshot_latest.json", "Harmonized common maritime snapshot — JSON", "ais_history", "Canonical delayed vessel snapshot at the newest common source watermark. Includes real observation times and source-health metadata."),
        ("maritime_common_snapshot_latest.geojson", "Harmonized common maritime snapshot — GeoJSON", "ais_history", "Map-ready canonical vessel positions selected at the newest common source watermark."),
        ("maritime_common_snapshot_status.json", "Harmonized common maritime snapshot — status", "ais_history", "Snapshot ID, observation watermark, generation time, mandatory-source coverage and green/orange/red status."),
        ("sar_detections_latest.geojson", "SAR vessel-detection cells — GeoJSON", "sar", "Delayed Global Fishing Watch Sentinel-1 SAR report cells for broad North Sea and Baltic query regions. Coordinates are 0.01-degree grid-cell centres, not exact or current vessel positions."),
        ("sar_ais_context_latest.geojson", "Time-aligned historical SAR–AIS comparison — GeoJSON", "sar", "Delayed SAR cells, bounded GFW AIS Vessel Presence cells from corresponding historical hours, and same-identity connectors. Current AIS must not be overlaid; cell centres are not exact positions and connectors are not tracks."),
        ("sar_import_status.json", "SAR and historical AIS comparison status", "sar", "Query dates, identity-scope limits, time-aligned correlation counters, errors and data limitations for the delayed Global Fishing Watch SAR–AIS comparison."),
        ("voi_list_latest.json", "VOI list — JSON", "voi", "Machine-readable current priority VOI list."),
        ("voi_list_latest.csv", "VOI list — CSV", "voi", "Tabular current priority VOI list."),
        ("voi_list_latest.md", "VOI list — Markdown", "voi", "Readable current priority VOI list."),
        ("infrastructure_watch_latest.json", "Infrastructure Watch — JSON", "infrastructure", "Machine-readable review events and assessment metadata."),
        ("infrastructure_watch_latest.csv", "Infrastructure Watch — CSV", "infrastructure", "Tabular infrastructure proximity review events."),
        ("infrastructure_watch_latest.md", "Infrastructure Watch — Markdown", "infrastructure", "Readable infrastructure proximity assessment."),
        ("infrastructure_watch_latest.geojson", "Infrastructure Watch — GeoJSON", "infrastructure", "Map-ready event points for GIS and Leaflet."),
    ]
    for filename, label, group, description in specs:
        entry = file_entry(PUBLIC / "downloads" / filename, f"./{filename}", label, group, description)
        if entry:
            products.append(entry)
    manifest = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "source": "Voodoo Whiskers",
        "repository_public": True,
        "hosting_target": "Cloudflare Pages later",
        "assessment_limit": "VOI, SAR and proximity products support analyst review and do not establish hostile intent, attribution or unlawful activity.",
        "groups": [
            {"id": "ais", "label": "AISStream / historical fallback data"},
            {"id": "ais_history", "label": "Historical AIS supplements"},
            {"id": "sar", "label": "SAR satellite detection cells"},
            {"id": "voi", "label": "VOI lists"},
            {"id": "infrastructure", "label": "Critical Infrastructure Watch"},
        ],
        "products": products,
    }
    atomic_json(PUBLIC / "downloads" / "manifest.json", manifest)
    data_manifest = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "source": "Voodoo Whiskers",
        "provider_label": "AIS + delayed SAR context",
        "web_app": "./index.html",
        "vessels": "./data/vessels/manifest.json",
        "danish_historical_ais": "./data/vessels/ais_dk_last_two_positions.geojson",
        "danish_historical_status": "./data/vessels/ais_dk_import_status.json",
        "common_maritime_snapshot": "./data/vessels/maritime_common_snapshot_latest.geojson",
        "common_maritime_snapshot_status": "./data/vessels/maritime_common_snapshot_status.json",
        "sar_detections": "./data/vessels/sar_detections_latest.geojson",
        "sar_historical_ais_context": "./data/vessels/sar_ais_context_latest.geojson",
        "sar_status": "./data/vessels/sar_import_status.json",
        "emodnet": "./data/reference/emodnet/manifest.json",
        "infrastructure_events": "./data/analysis/infrastructure_events_latest.json",
        "infrastructure_events_geojson": "./data/analysis/infrastructure_events_latest.geojson",
        "infrastructure_summary": "./data/analysis/infrastructure_summary_latest.json",
        "score_shadow": "./data/analysis/infrastructure_score_shadow.json",
        "downloads": "./downloads/manifest.json",
        "active_score_integration": False,
    }
    atomic_json(PUBLIC / "data" / "manifest.json", data_manifest)
    print(json.dumps({"generated_at": generated_at, "download_products": len(products)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
