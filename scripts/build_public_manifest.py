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
        ("ais_contacts_latest.json", "Current monitored AIS contacts — JSON", "ais", "Provider-neutral filtered AIS contacts currently monitored by Voodoo Whiskers."),
        ("ais_contacts_latest.geojson", "Current monitored AIS positions — GeoJSON", "ais", "Map-ready filtered AIS positions currently monitored by Voodoo Whiskers."),
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
        "assessment_limit": "VOI and proximity products support analyst review and do not establish hostile intent, attribution or unlawful activity.",
        "groups": [
            {"id": "ais", "label": "Monitored AIS positions"},
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
        "provider_label": "AIS",
        "web_app": "./index.html",
        "vessels": "./data/vessels/manifest.json",
        "emodnet": "./data/reference/emodnet/manifest.json",
        "infrastructure_events": "./data/analysis/infrastructure_events_latest.json",
        "infrastructure_events_geojson": "./data/analysis/infrastructure_events_latest.geojson",
        "infrastructure_summary": "./data/analysis/infrastructure_summary_latest.json",
        "score_shadow": "./data/analysis/infrastructure_score_shadow.json",
        "downloads": "./downloads/manifest.json",
        "active_score_integration": False,
    }
    atomic_json(PUBLIC / "data" / "manifest.json", data_manifest)
    print(json.dumps({"download_products": len(products), "generated_at": generated_at}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
