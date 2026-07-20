#!/usr/bin/env python3
"""Merge provider-specific AIS snapshots into data/ais_contacts_latest.json.

Fresh provider files are preferred. Last-known-good files may remain active for a bounded
hard TTL so a temporary upstream outage cannot replace valid AIS data with an empty file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")
OUTPUT_PATH = DATA_DIR / "ais_contacts_latest.json"

PROVIDERS = (
    {
        "id": "aisstream",
        "path": DATA_DIR / "ais_contacts_aisstream_latest.json",
        "soft_ttl_hours": float(os.getenv("AISSTREAM_SOFT_TTL_HOURS", "40")),
        "hard_ttl_hours": float(os.getenv("AISSTREAM_HARD_TTL_HOURS", "96")),
    },
    {
        "id": "fintraffic",
        "path": DATA_DIR / "ais_contacts_fintraffic_latest.json",
        "soft_ttl_hours": float(os.getenv("REGIONAL_SOFT_TTL_HOURS", "30")),
        "hard_ttl_hours": float(os.getenv("REGIONAL_HARD_TTL_HOURS", "72")),
    },
    {
        "id": "barentswatch",
        "path": DATA_DIR / "ais_contacts_barentswatch_latest.json",
        "soft_ttl_hours": float(os.getenv("REGIONAL_SOFT_TTL_HOURS", "30")),
        "hard_ttl_hours": float(os.getenv("REGIONAL_HARD_TTL_HOURS", "72")),
    },
)

EMPTY_VALUES = ("", None, [], {})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


def digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def norm(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def contact_key(contact: dict[str, Any]) -> str:
    return (
        ("mmsi:" + digits(contact.get("mmsi"))) if digits(contact.get("mmsi")) else
        ("imo:" + digits(contact.get("imo"))) if digits(contact.get("imo")) else
        ("callsign:" + norm(contact.get("callsign"))) if norm(contact.get("callsign")) else
        ("name:" + norm(contact.get("name"))) if norm(contact.get("name")) else
        ""
    )


def merge_nonempty(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in incoming.items():
        if value not in EMPTY_VALUES:
            result[key] = value
    return result


def contact_time(contact: dict[str, Any]) -> datetime:
    return parse_datetime(contact.get("last_seen_utc")) or datetime.min.replace(tzinfo=timezone.utc)


def load_provider(provider: dict[str, Any], now: datetime) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    path: Path = provider["path"]
    status = {"path": str(path), "included": False}
    if not path.exists():
        status["reason"] = "missing"
        return None, status
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        status["reason"] = f"invalid: {type(exc).__name__}: {exc}"
        return None, status

    generated = parse_datetime(payload.get("generated_at"))
    if generated is None:
        status["reason"] = "generated_at missing or invalid"
        return None, status

    age_hours = max(0.0, (now - generated).total_seconds() / 3600.0)
    status.update({
        "generated_at": generated.replace(microsecond=0).isoformat(),
        "age_hours": round(age_hours, 2),
        "soft_ttl_hours": provider["soft_ttl_hours"],
        "hard_ttl_hours": provider["hard_ttl_hours"],
        "stale": age_hours > provider["soft_ttl_hours"],
    })
    if age_hours > provider["hard_ttl_hours"]:
        status["reason"] = "beyond hard TTL"
        return None, status

    contacts = payload.get("contacts")
    if not isinstance(contacts, list):
        status["reason"] = "contacts is not a list"
        return None, status

    status["included"] = True
    status["contact_count"] = len(contacts)
    return payload, status


def main() -> int:
    now = utc_now()
    merged: dict[str, dict[str, Any]] = {}
    provider_status: dict[str, Any] = {}
    included_provider_ids: list[str] = []

    for provider in PROVIDERS:
        payload, status = load_provider(provider, now)
        provider_status[provider["id"]] = status
        if payload is None:
            continue
        included_provider_ids.append(provider["id"])
        for contact in payload.get("contacts", []):
            if not isinstance(contact, dict):
                continue
            key = contact_key(contact)
            if not key:
                continue
            incoming = dict(contact)
            source_name = str(incoming.get("source") or payload.get("source") or provider["id"])
            incoming_sources = set(incoming.get("sources") or [])
            incoming_sources.add(source_name)
            incoming["sources"] = sorted(incoming_sources)

            if key not in merged:
                merged[key] = incoming
                continue

            current = merged[key]
            all_sources = set(current.get("sources") or []) | set(incoming.get("sources") or [])
            if contact_time(incoming) >= contact_time(current):
                combined = merge_nonempty(current, incoming)
            else:
                combined = merge_nonempty(incoming, current)
            combined["sources"] = sorted(all_sources)
            merged[key] = combined

    if not included_provider_ids:
        print("ERROR: no provider snapshot is available within its hard TTL")
        return 1

    contacts = sorted(
        merged.values(),
        key=lambda c: (digits(c.get("mmsi")), norm(c.get("name"))),
    )
    stale = any(provider_status[p].get("stale") for p in included_provider_ids)
    payload = {
        "schema_version": "1.0.0",
        "generated_at": utc_now_iso(),
        "source": "Voodoo Whiskers AIS provider broker",
        "filter_mode": "Merged provider snapshots; provider-level LKG bounded by TTL",
        "coverage_mode": "mixed_regional_and_primary" if len(included_provider_ids) > 1 else "single_provider",
        "stale": bool(stale),
        "providers_included": included_provider_ids,
        "provider_status": provider_status,
        "count": len(contacts),
        "contacts": contacts,
    }
    atomic_json_write(OUTPUT_PATH, payload)
    print(f"Merged {len(contacts)} contacts from: {', '.join(included_provider_ids)}")
    if stale:
        print("WARNING: at least one included provider snapshot is beyond its soft TTL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
