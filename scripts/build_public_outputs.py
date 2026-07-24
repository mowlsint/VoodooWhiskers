#!/usr/bin/env python3
"""Build public Voodoo Whiskers web and download products from canonical data files."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
VESSEL_DIR = PUBLIC / "data" / "vessels"
LAYER_DIR = VESSEL_DIR / "layers"
DOWNLOAD_DIR = PUBLIC / "downloads"
CONFIG = json.loads((ROOT / "config" / "infrastructure_watch.json").read_text(encoding="utf-8"))

SNAPSHOT_PATH = DATA / "voi_snapshot_latest.json"
HISTORY_PATH = DATA / "voi_history.jsonl"
AIS_CONTACTS_PATH = DATA / "ais_contacts_latest.json"
AIS_DK_PATH = DATA / "ais_contacts_aisdk_historical_latest.json"
AIS_DK_STATUS_PATH = DATA / "ais_dk_import_status_latest.json"

CATEGORY_LAYERS = [
    "russian_mmsi.geojson",
    "watchlist_live.geojson",
    "sanctions_shadowfleet.geojson",
    "falseflag_interest.geojson",
    "false_flag_watch.geojson",
    "behavioral_voi.geojson",
    "recent_russian_portcall_10d.geojson",
    "neutral_tanker_context.geojson",
]

PRIORITY_CATEGORIES = {
    "falseflag_interest",
    "false_flag_watch",
    "sanctions_shadowfleet",
    "watchlist",
    "russian_mmsi",
    "recent_russian_portcall_10d",
    "behavioral_voi",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="") as tmp:
        tmp.write(text)
        name = tmp.name
    Path(name).replace(path)


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


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
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def valid_operational_dt(dt: datetime | None) -> bool:
    return bool(dt and datetime(2000, 1, 1, tzinfo=timezone.utc) <= dt <= utc_now() + timedelta(days=1))


def float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def normalized_sog(value: Any) -> tuple[float | None, bool]:
    number = float_or_none(value)
    if number is None or number < 0 or number >= 102.2:
        return None, number is not None
    return round(number, 2), False


def normalized_cog(value: Any) -> tuple[float | None, bool]:
    number = float_or_none(value)
    if number is None or number < 0 or number >= 360:
        return None, number is not None
    return round(number, 2), False


def normalized_heading(value: Any) -> tuple[int | None, bool]:
    number = float_or_none(value)
    if number is None or number < 0 or number >= 511:
        return None, number is not None
    return int(round(number)), False


def fallback_history_dt(item: dict[str, Any], snapshot_generated: datetime | None = None) -> tuple[datetime | None, str]:
    raw = parse_dt(item.get("last_seen_utc"))
    declared_basis = str(item.get("position_timestamp_basis") or "").strip()
    declared_valid = item.get("position_timestamp_valid")
    if valid_operational_dt(raw) and declared_valid is False:
        return raw, declared_basis or "retrieval_time_fallback"
    if valid_operational_dt(raw):
        return raw, declared_basis or "source_timestamp"
    key = str(item.get("_history_key") or "")
    key_dt = parse_dt(key.split("|")[0] if "|" in key else "")
    if valid_operational_dt(key_dt):
        return key_dt, "history_slot_fallback"
    if valid_operational_dt(snapshot_generated):
        return snapshot_generated, "snapshot_generated_at_fallback"
    return None, "missing_or_invalid"


def normalize_item(item: dict[str, Any], snapshot_generated: datetime | None = None) -> dict[str, Any]:
    out = dict(item)
    observed_at, timestamp_basis = fallback_history_dt(item, snapshot_generated)
    sog, sog_invalid = normalized_sog(item.get("sog"))
    cog, cog_invalid = normalized_cog(item.get("cog"))
    heading, heading_invalid = normalized_heading(item.get("true_heading"))
    lat = float_or_none(item.get("latitude", item.get("lat")))
    lon = float_or_none(item.get("longitude", item.get("lon")))
    categories = [str(x) for x in item.get("categories", []) if str(x).strip()] if isinstance(item.get("categories"), list) else []
    priority = bool(PRIORITY_CATEGORIES.intersection(categories)) or any(bool(item.get(k)) for k in ("sanctioned", "shadow_fleet", "false_flag", "false_flag_candidate", "behavioral_voi", "from_russia_confirmed"))

    out.update({
        "latitude": lat,
        "longitude": lon,
        "sog": sog,
        "cog": cog,
        "true_heading": heading,
        "observed_at": observed_at.isoformat() if observed_at else None,
        "position_timestamp_valid": timestamp_basis == "source_timestamp",
        "position_timestamp_basis": timestamp_basis,
        "is_priority_voi": priority and not (categories == ["neutral_tanker_context"]),
        "categories": categories,
        "data_quality": {
            "timestamp_repaired": timestamp_basis != "source_timestamp",
            "sog_invalid_sentinel": sog_invalid,
            "cog_invalid_sentinel": cog_invalid,
            "heading_invalid_sentinel": heading_invalid,
            "has_valid_position": lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180,
        },
    })
    return out


def point_feature(item: dict[str, Any]) -> dict[str, Any] | None:
    lat = float_or_none(item.get("latitude"))
    lon = float_or_none(item.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    props = {k: v for k, v in item.items() if k not in {"latitude", "longitude"}}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="") as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            clean = dict(row)
            for key, value in list(clean.items()):
                if isinstance(value, (list, dict)):
                    clean[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            writer.writerow(clean)
        name = tmp.name
    Path(name).replace(path)


def copy_category_layers() -> list[dict[str, Any]]:
    LAYER_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for filename in CATEGORY_LAYERS:
        src = DATA / filename
        dst = LAYER_DIR / filename
        if src.exists():
            shutil.copy2(src, dst)
            try:
                payload = json.loads(src.read_text(encoding="utf-8"))
                count = len(payload.get("features", [])) if isinstance(payload, dict) and isinstance(payload.get("features"), list) else None
            except Exception:
                count = None
            result.append({"id": filename.removesuffix(".geojson"), "href": f"./layers/{filename}", "feature_count": count})
    return result


def build_bounded_history(snapshot_generated: datetime | None) -> dict[str, Any]:
    max_days = int(CONFIG.get("history_max_age_days", 14))
    max_bytes = int(CONFIG.get("history_max_bytes", 22 * 1024 * 1024))
    cutoff = utc_now() - timedelta(days=max_days)
    rows: list[tuple[datetime, str]] = []
    stats = {"source_rows": 0, "malformed": 0, "outside_window": 0, "missing_time": 0, "invalid_position": 0, "timestamp_repaired": 0}
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                stats["source_rows"] += 1
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    stats["malformed"] += 1
                    continue
                if not isinstance(raw, dict):
                    stats["malformed"] += 1
                    continue
                item = normalize_item(raw, snapshot_generated)
                dt = parse_dt(item.get("observed_at"))
                if not dt:
                    stats["missing_time"] += 1
                    continue
                if dt < cutoff or dt > utc_now() + timedelta(days=1):
                    stats["outside_window"] += 1
                    continue
                if not item.get("data_quality", {}).get("has_valid_position"):
                    stats["invalid_position"] += 1
                    continue
                if item.get("data_quality", {}).get("timestamp_repaired"):
                    stats["timestamp_repaired"] += 1
                text = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                rows.append((dt, text))
    rows.sort(key=lambda pair: pair[0])
    selected_reverse: list[str] = []
    byte_count = 0
    dropped_size = 0
    for _dt, text in reversed(rows):
        line_bytes = len((text + "\n").encode("utf-8"))
        if line_bytes > max_bytes:
            dropped_size += 1
            continue
        if byte_count + line_bytes > max_bytes:
            dropped_size += len(rows) - len(selected_reverse)
            break
        selected_reverse.append(text)
        byte_count += line_bytes
    selected = list(reversed(selected_reverse))
    output = "\n".join(selected) + ("\n" if selected else "")
    atomic_text(VESSEL_DIR / "voi_history_14d.jsonl", output)
    stats.update({"available": True, "published_rows": len(selected), "published_bytes": byte_count, "dropped_size": dropped_size, "max_age_days": max_days, "max_bytes": max_bytes, "build_mode": "daily_or_manual"})
    return stats


def existing_history_stats() -> dict[str, Any]:
    path = VESSEL_DIR / "voi_history_14d.jsonl"
    if not path.exists():
        return {
            "available": False,
            "published_rows": 0,
            "published_bytes": 0,
            "max_age_days": int(CONFIG.get("history_max_age_days", 14)),
            "max_bytes": int(CONFIG.get("history_max_bytes", 22 * 1024 * 1024)),
            "build_mode": "daily_or_manual",
        }
    rows = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                rows += 1
    return {
        "available": True,
        "published_rows": rows,
        "published_bytes": path.stat().st_size,
        "max_age_days": int(CONFIG.get("history_max_age_days", 14)),
        "max_bytes": int(CONFIG.get("history_max_bytes", 22 * 1024 * 1024)),
        "build_mode": "daily_or_manual",
    }



def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return round(radius_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a))), 3)


def build_danish_public_products() -> dict[str, Any]:
    """Publish only the compact two-position Danish historical product.

    Raw Danish ZIP/CSV files are never read by this builder and must never exist in the
    repository. The importer writes only AIS_DK_PATH and a small status/state record.
    """
    if not AIS_DK_PATH.exists():
        return {"available": False, "vessel_count": 0, "position_features": 0, "line_features": 0}
    payload = json.loads(AIS_DK_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{AIS_DK_PATH} must contain a JSON object")
    contacts = payload.get("contacts") if isinstance(payload.get("contacts"), list) else []
    features: list[dict[str, Any]] = []
    position_features = 0
    line_features = 0
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        positions = contact.get("positions") if isinstance(contact.get("positions"), list) else []
        clean_positions = []
        common = {
            key: value for key, value in contact.items()
            if key not in {"positions", "latest_position", "previous_position", "latitude", "longitude", "_observed_dt"}
        }
        for position in positions[:2]:
            if not isinstance(position, dict):
                continue
            lat = float_or_none(position.get("latitude"))
            lon = float_or_none(position.get("longitude"))
            if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            rank = int(position.get("rank") or len(clean_positions) + 1)
            props = dict(common)
            props.update({
                "feature_role": "latest_historical_position" if rank == 1 else "previous_historical_position",
                "position_rank": rank,
                "observed_at": position.get("observed_at"),
                "sog": position.get("sog"),
                "cog": position.get("cog"),
                "true_heading": position.get("true_heading"),
                "navigational_status": position.get("navigational_status"),
                "historical": True,
                "not_current_position": True,
            })
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props})
            clean_positions.append((rank, lat, lon, position.get("observed_at")))
            position_features += 1
        clean_positions.sort(key=lambda row: row[0])
        if len(clean_positions) == 2:
            latest, previous = clean_positions[0], clean_positions[1]
            if (latest[1], latest[2]) != (previous[1], previous[2]):
                props = dict(common)
                props.update({
                    "feature_role": "historical_two_position_connector",
                    "historical": True,
                    "not_current_position": True,
                    "latest_observed_at": latest[3],
                    "previous_observed_at": previous[3],
                    "distance_nm": haversine_nm(previous[1], previous[2], latest[1], latest[2]),
                })
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[previous[2], previous[1]], [latest[2], latest[1]]]},
                    "properties": props,
                })
                line_features += 1

    generated_at = str(payload.get("generated_at") or iso())
    public_payload = dict(payload)
    public_payload.update({
        "public_product": True,
        "provider_label": "Danish historical AIS",
        "assessment_limit": "Delayed historical observations. They are not current vessel positions.",
        "max_positions_per_vessel": 2,
        "contacts": contacts,
    })
    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "source": "Danish historical AIS",
            "provider": "ais_dk_historical",
            "coverage_mode": "historical_delayed",
            "not_current_position": True,
            "max_positions_per_vessel": 2,
            "vessel_count": len(contacts),
            "position_feature_count": position_features,
            "connector_feature_count": line_features,
            "data_max_timestamp_utc": payload.get("data_max_timestamp_utc"),
            "lag_hours_at_build": payload.get("lag_hours_at_build"),
            "coverage_limit": "Danish land-based historical AIS. Delayed, filtered and not a complete current traffic picture.",
        },
    }
    status = {}
    if AIS_DK_STATUS_PATH.exists():
        candidate = json.loads(AIS_DK_STATUS_PATH.read_text(encoding="utf-8"))
        status = candidate if isinstance(candidate, dict) else {}
    atomic_json(VESSEL_DIR / "ais_dk_last_two_positions.json", public_payload)
    atomic_json(VESSEL_DIR / "ais_dk_last_two_positions.geojson", geojson)
    atomic_json(VESSEL_DIR / "ais_dk_import_status.json", status)
    atomic_json(DOWNLOAD_DIR / "ais_dk_last_two_positions.json", public_payload)
    atomic_json(DOWNLOAD_DIR / "ais_dk_last_two_positions.geojson", geojson)
    atomic_json(DOWNLOAD_DIR / "ais_dk_import_status.json", status)
    return {
        "available": True,
        "json_href": "./ais_dk_last_two_positions.json",
        "geojson_href": "./ais_dk_last_two_positions.geojson",
        "status_href": "./ais_dk_import_status.json",
        "vessel_count": len(contacts),
        "position_features": position_features,
        "line_features": line_features,
        "data_max_timestamp_utc": payload.get("data_max_timestamp_utc"),
        "lag_hours_at_build": payload.get("lag_hours_at_build"),
        "max_positions_per_vessel": 2,
        "coverage_mode": "historical_delayed",
    }

def build_markdown(rows: list[dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# Voodoo Whiskers — Current VOI List",
        "",
        f"- Generated: {generated_at}",
        f"- Priority vessels: {len(rows)}",
        "- Neutral tanker context is excluded from this VOI list and remains available as a separate map layer.",
        "- AIS coverage is mixed and not continuous. A listed position is the latest available observation, not proof of current presence.",
        "",
        "| Vessel | IMO | MMSI | Categories | Flag context | Last position | Observed | Source |",
        "|---|---:|---:|---|---|---|---|---|",
    ]
    for row in rows:
        pos = ""
        if row.get("latitude") is not None and row.get("longitude") is not None:
            pos = f"{row['latitude']:.4f}, {row['longitude']:.4f}"
        categories = ", ".join(row.get("categories") or [])
        flag = row.get("flag_detected") or row.get("flag_iso_code") or ""
        lines.append(
            "| " + " | ".join([
                str(row.get("name") or "Unknown").replace("|", "/"),
                str(row.get("imo") or ""),
                str(row.get("mmsi") or ""),
                categories.replace("|", "/"),
                str(flag).replace("|", "/"),
                pos,
                str(row.get("observed_at") or ""),
                str(row.get("source") or "").replace("|", "/"),
            ]) + " |"
        )
    lines += ["", "## Assessment limit", "", "VOI/watchlist context, proximity and movement patterns require analyst review. They do not by themselves establish hostile intent, attribution or unlawful activity.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="Rebuild the bounded public VOI history. Omit for frequent lightweight builds.",
    )
    args = parser.parse_args()

    if not SNAPSHOT_PATH.exists():
        raise FileNotFoundError(SNAPSHOT_PATH)
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    generated_at = str(snapshot.get("generated_at") or iso())
    snapshot_dt = parse_dt(generated_at)
    raw_items = snapshot.get("items") if isinstance(snapshot.get("items"), list) else []
    items = [normalize_item(item, snapshot_dt) for item in raw_items if isinstance(item, dict)]

    ais_pack = {}
    if AIS_CONTACTS_PATH.exists():
        candidate = json.loads(AIS_CONTACTS_PATH.read_text(encoding="utf-8"))
        if isinstance(candidate, dict):
            ais_pack = candidate
    ais_generated_at = str(ais_pack.get("generated_at") or generated_at)
    ais_generated_dt = parse_dt(ais_generated_at) or snapshot_dt
    raw_contacts = ais_pack.get("contacts") if isinstance(ais_pack.get("contacts"), list) else []
    ais_contacts = [normalize_item(item, ais_generated_dt) for item in raw_contacts if isinstance(item, dict)]

    VESSEL_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    normalized_snapshot = dict(snapshot)
    normalized_snapshot.update({
        "schema_version": str(snapshot.get("schema_version") or "1.0.0"),
        "public_product": True,
        "generated_at": generated_at,
        "normalization": {
            "provider_neutral_label": "AIS",
            "invalid_epoch_timestamps_repaired": True,
            "ais_sentinel_values_removed": True,
        },
        "items": items,
    })
    atomic_json(VESSEL_DIR / "voi_snapshot_latest.json", normalized_snapshot)

    features = [feature for item in items if (feature := point_feature(item))]
    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "source": "Voodoo Whiskers",
            "label": "Monitored AIS vessel positions with VOI/watchlist context",
            "feature_count": len(features),
            "coverage_limit": "Filtered and monitored mixed terrestrial/regional AIS snapshots; not a complete traffic picture or continuous tracking.",
        },
    }
    atomic_json(VESSEL_DIR / "vessel_positions_latest.geojson", geojson)

    ais_features = [feature for item in ais_contacts if (feature := point_feature(item))]
    public_ais_pack = dict(ais_pack)
    public_ais_pack.update({
        "schema_version": str(ais_pack.get("schema_version") or "1.0.0"),
        "generated_at": ais_generated_at,
        "source": "Voodoo Whiskers AIS provider broker",
        "provider_label": "AIS",
        "public_product": True,
        "count": len(ais_contacts),
        "normalization": {
            "provider_neutral_label": "AIS",
            "invalid_epoch_timestamps_repaired": True,
            "ais_sentinel_values_removed": True,
        },
        "contacts": ais_contacts,
    })
    atomic_json(VESSEL_DIR / "ais_contacts_latest.json", public_ais_pack)
    ais_geojson = {
        "type": "FeatureCollection",
        "features": ais_features,
        "metadata": {
            "schema_version": "1.0.0",
            "generated_at": ais_generated_at,
            "source": "Voodoo Whiskers AIS provider broker",
            "provider_label": "AIS",
            "feature_count": len(ais_features),
            "coverage_limit": "Filtered and monitored mixed terrestrial/regional AIS snapshots; not a complete traffic picture or continuous tracking.",
        },
    }
    atomic_json(VESSEL_DIR / "ais_contacts_latest.geojson", ais_geojson)
    atomic_json(DOWNLOAD_DIR / "ais_contacts_latest.json", public_ais_pack)
    atomic_json(DOWNLOAD_DIR / "ais_contacts_latest.geojson", ais_geojson)

    danish_history = build_danish_public_products()
    category_layers = copy_category_layers()
    history_stats = build_bounded_history(snapshot_dt) if args.include_history else existing_history_stats()
    history_stats["rebuilt_in_this_run"] = bool(args.include_history)

    voi_rows = [item for item in items if item.get("is_priority_voi")]
    voi_rows.sort(key=lambda row: (str(row.get("name") or ""), str(row.get("mmsi") or "")))
    voi_product = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "source": "Voodoo Whiskers",
        "provider_label": "AIS",
        "priority_vessel_count": len(voi_rows),
        "neutral_tanker_context_excluded": True,
        "assessment_limit": "VOI/watchlist context is an analyst lead, not proof of hostile intent or unlawful activity.",
        "items": voi_rows,
    }
    atomic_json(DOWNLOAD_DIR / "voi_list_latest.json", voi_product)
    fields = [
        "name", "imo", "mmsi", "callsign", "flag_detected", "flag_iso_code", "categories", "watch_priority",
        "sanctioned", "shadow_fleet", "false_flag", "from_russia_confirmed", "destination", "latitude", "longitude",
        "sog", "cog", "observed_at", "position_timestamp_basis", "source", "source_list", "source_url", "notes",
    ]
    write_csv(DOWNLOAD_DIR / "voi_list_latest.csv", voi_rows, fields)
    atomic_text(DOWNLOAD_DIR / "voi_list_latest.md", build_markdown(voi_rows, generated_at))

    vessel_manifest = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "snapshot": {"href": "./voi_snapshot_latest.json", "item_count": len(items)},
        "positions": {"href": "./vessel_positions_latest.geojson", "feature_count": len(features)},
        "ais_contacts": {
            "json_href": "./ais_contacts_latest.json",
            "geojson_href": "./ais_contacts_latest.geojson",
            "contact_count": len(ais_contacts),
            "feature_count": len(ais_features),
            "provider_label": "AIS",
            "display_label": "Current monitored AIS contacts",
        },
        "history": {"href": "./voi_history_14d.jsonl" if history_stats.get("available") else None, **history_stats},
        "danish_historical": danish_history,
        "category_layers": category_layers,
    }
    atomic_json(VESSEL_DIR / "manifest.json", vessel_manifest)

    print(json.dumps({
        "snapshot_items": len(items),
        "priority_voi": len(voi_rows),
        "position_features": len(features),
        "ais_contacts": len(ais_contacts),
        "ais_contact_features": len(ais_features),
        "history": history_stats,
        "danish_historical": danish_history,
        "category_layers": len(category_layers),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
