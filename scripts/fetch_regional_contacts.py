#!/usr/bin/env python3
"""Fetch and normalize open regional AIS snapshots for Voodoo Whiskers.

Providers:
- Fintraffic Digitraffic (no credentials)
- BarentsWatch / Norwegian Coastal Administration (OAuth client credentials)

The script writes provider-specific files atomically and never replaces a last-known-good
provider file when a fetch fails. The canonical data/ais_contacts_latest.json is produced
separately by merge_ais_contacts.py.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Reuse the exact filtering logic already used by the AISstream collector.
from fetch_contacts import (
    clean_str,
    digits,
    keep_contact,
    load_flag_risk_mids,
    load_russian_port_terms,
    load_watchlist_index,
)

DATA_DIR = Path("data")
FINTRAFFIC_OUTPUT = DATA_DIR / "ais_contacts_fintraffic_latest.json"
BARENTSWATCH_OUTPUT = DATA_DIR / "ais_contacts_barentswatch_latest.json"
STATUS_OUTPUT = DATA_DIR / "ais_regional_fetch_status_latest.json"

FINTRAFFIC_LOCATIONS_URL = "https://meri.digitraffic.fi/api/ais/v1/locations"
FINTRAFFIC_VESSELS_URL = "https://meri.digitraffic.fi/api/ais/v1/vessels"
BARENTSWATCH_TOKEN_URL = "https://id.barentswatch.no/connect/token"
BARENTSWATCH_LATEST_URL = "https://live.ais.barentswatch.no/v1/latest/combined"

APP_NAME = os.getenv("AIS_APP_NAME", "MOwlSINT Voodoo Whiskers/1.0")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session = requests.Session()
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": APP_NAME, "Accept": "application/json"})
    return session


def first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return ""


def normalize_timestamp(value: Any, fallback: str | None = None) -> tuple[str, bool, str]:
    """Return a timestamp plus explicit quality metadata.

    Invalid epoch-like values (notably 1970 dates from Fintraffic payload variants)
    use retrieval time as a bounded fallback. The fallback is marked as imprecise so
    downstream dwell and track analysis can reduce confidence.
    """
    fallback = fallback or utc_now_iso()
    parsed: datetime | None = None
    if value not in (None, ""):
        text = str(value).strip()
        numeric = isinstance(value, (int, float)) or text.replace(".", "", 1).isdigit()
        if numeric:
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000.0
            try:
                parsed = datetime.fromtimestamp(number, tz=timezone.utc).replace(microsecond=0)
            except (OverflowError, OSError, ValueError):
                parsed = None
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
            except ValueError:
                parsed = None
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if parsed and datetime(2000, 1, 1, tzinfo=timezone.utc) <= parsed <= now + timedelta(days=1):
        return parsed.isoformat(), True, "source_timestamp"
    return fallback, False, "retrieval_time_fallback"


def normalize_sog(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # AIS raw sentinel 1023 is commonly exposed as 102.3 knots.
    if number < 0 or number >= 102.2:
        return None
    return round(number, 2)


def normalize_cog(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number >= 360:
        return None
    return round(number, 2)


def normalize_heading(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number >= 511:
        return None
    return int(round(number))


def iter_records(payload: Any) -> Iterable[dict[str, Any]]:
    """Yield records from arrays, GeoJSON FeatureCollections and common wrappers."""
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(payload, dict):
        return
    if payload.get("type") == "FeatureCollection" and isinstance(payload.get("features"), list):
        for feature in payload["features"]:
            if isinstance(feature, dict):
                yield feature
        return
    for key in ("features", "vessels", "locations", "data", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
            return
    yield payload


def unpack_feature(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    props = record.get("properties") if isinstance(record.get("properties"), dict) else record
    geometry = record.get("geometry") if isinstance(record.get("geometry"), dict) else {}
    return props, geometry


def fintraffic_metadata_index(payload: Any) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in iter_records(payload):
        props, _ = unpack_feature(record)
        mmsi = digits(first_value(props, "mmsi", "MMSI"))
        if mmsi:
            index[mmsi] = props
    return index


def normalize_fintraffic(location_record: dict[str, Any], metadata: dict[str, Any], fetched_at: str) -> dict[str, Any] | None:
    props, geometry = unpack_feature(location_record)
    mmsi = digits(first_value(props, "mmsi", "MMSI"))
    if not mmsi:
        return None

    coordinates = geometry.get("coordinates") if isinstance(geometry.get("coordinates"), list) else []
    lon = coordinates[0] if len(coordinates) >= 2 else first_value(props, "lon", "longitude", "Longitude")
    lat = coordinates[1] if len(coordinates) >= 2 else first_value(props, "lat", "latitude", "Latitude")

    observed_at, timestamp_valid, timestamp_basis = normalize_timestamp(
        first_value(props, "timestamp", "time", "timestampExternal", "msgtime"), fetched_at
    )
    return {
        "mmsi": mmsi,
        "imo": clean_str(first_value(metadata, "imo", "imoNumber", "IMO")),
        "callsign": clean_str(first_value(metadata, "callSign", "callsign", "CallSign")),
        "name": clean_str(first_value(metadata, "name", "shipName", "ShipName")),
        "latitude": lat,
        "longitude": lon,
        "destination": clean_str(first_value(metadata, "destination", "Destination")),
        "ship_type": first_value(metadata, "type", "shipType", "ShipType"),
        "navigational_status": first_value(props, "navStat", "navigationalStatus", "NavigationalStatus"),
        "sog": normalize_sog(first_value(props, "sog", "speedOverGround", "Sog")),
        "cog": normalize_cog(first_value(props, "cog", "courseOverGround", "Cog")),
        "true_heading": normalize_heading(first_value(props, "heading", "trueHeading", "TrueHeading")),
        "source": "Fintraffic Digitraffic",
        "source_provider": "fintraffic",
        "message_type_last": "RegionalSnapshot",
        "last_seen_utc": observed_at,
        "position_timestamp_valid": timestamp_valid,
        "position_timestamp_basis": timestamp_basis,
    }


def normalize_barentswatch(record: dict[str, Any], fetched_at: str) -> dict[str, Any] | None:
    props, geometry = unpack_feature(record)
    mmsi = digits(first_value(props, "mmsi", "MMSI"))
    if not mmsi:
        return None

    coordinates = geometry.get("coordinates") if isinstance(geometry.get("coordinates"), list) else []
    lon = coordinates[0] if len(coordinates) >= 2 else first_value(props, "longitude", "lon", "Longitude")
    lat = coordinates[1] if len(coordinates) >= 2 else first_value(props, "latitude", "lat", "Latitude")

    observed_at, timestamp_valid, timestamp_basis = normalize_timestamp(
        first_value(props, "msgtime", "timestamp", "time"), fetched_at
    )
    contact = {
        "mmsi": mmsi,
        "imo": clean_str(first_value(props, "imoNumber", "imo", "IMO")),
        "callsign": clean_str(first_value(props, "callSign", "callsign", "CallSign")),
        "name": clean_str(first_value(props, "name", "shipName", "ShipName")),
        "latitude": lat,
        "longitude": lon,
        "destination": clean_str(first_value(props, "destination", "Destination")),
        "ship_type": first_value(props, "shipType", "type", "ShipType"),
        "navigational_status": first_value(props, "navigationalStatus", "navStat", "NavigationalStatus"),
        "sog": normalize_sog(first_value(props, "speedOverGround", "sog", "Sog")),
        "cog": normalize_cog(first_value(props, "courseOverGround", "cog", "Cog")),
        "true_heading": normalize_heading(first_value(props, "trueHeading", "heading", "TrueHeading")),
        "source": "BarentsWatch / Norwegian Coastal Administration",
        "source_provider": "barentswatch",
        "message_type_last": "RegionalSnapshot",
        "last_seen_utc": observed_at,
        "position_timestamp_valid": timestamp_valid,
        "position_timestamp_basis": timestamp_basis,
    }
    stream = first_value(props, "stream", "sourceStream")
    if stream not in (None, ""):
        contact["source_stream"] = stream
    return contact


def filter_contacts(contacts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    watch_idx = load_watchlist_index()
    risk_mids = load_flag_risk_mids()
    ru_codes, ru_names = load_russian_port_terms()
    kept = [c for c in contacts if keep_contact(c, watch_idx, risk_mids, ru_codes, ru_names)]
    kept.sort(key=lambda c: (digits(c.get("mmsi")), clean_str(c.get("name"))))
    return kept


def fetch_fintraffic(session: requests.Session) -> tuple[dict[str, Any], dict[str, Any]]:
    locations_response = session.get(FINTRAFFIC_LOCATIONS_URL, timeout=(15, 120))
    locations_response.raise_for_status()
    vessels_response = session.get(FINTRAFFIC_VESSELS_URL, timeout=(15, 120))
    vessels_response.raise_for_status()

    fetched_at = utc_now_iso()
    locations_payload = locations_response.json()
    metadata = fintraffic_metadata_index(vessels_response.json())
    raw_records = list(iter_records(locations_payload))
    normalized = []
    for record in raw_records:
        props, _ = unpack_feature(record)
        mmsi = digits(first_value(props, "mmsi", "MMSI"))
        contact = normalize_fintraffic(record, metadata.get(mmsi, {}), fetched_at)
        if contact:
            normalized.append(contact)
    kept = filter_contacts(normalized)
    payload = {
        "schema_version": "1.0.0",
        "generated_at": fetched_at,
        "source": "Fintraffic Digitraffic",
        "provider": "fintraffic",
        "license": "CC BY 4.0",
        "coverage": "Finnish marine AIS open data coverage",
        "filter_mode": "Voodoo Whiskers VOI + flag-risk + Russian destination/port + tanker-context filters",
        "raw_count": len(normalized),
        "count": len(kept),
        "contacts": kept,
    }
    status = {"ok": True, "raw_count": len(normalized), "kept_count": len(kept)}
    return payload, status


def fetch_barentswatch(session: requests.Session, client_id: str, client_secret: str) -> tuple[dict[str, Any], dict[str, Any]]:
    token_response = session.post(
        BARENTSWATCH_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "ais",
            "grant_type": "client_credentials",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=(15, 60),
    )
    token_response.raise_for_status()
    token_payload = token_response.json()
    access_token = clean_str(token_payload.get("access_token"))
    if not access_token:
        raise RuntimeError("BarentsWatch token response did not contain access_token")

    response = session.get(
        BARENTSWATCH_LATEST_URL,
        params={"modelType": "Full"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=(15, 180),
    )
    response.raise_for_status()
    fetched_at = utc_now_iso()
    records = list(iter_records(response.json()))
    normalized = [contact for record in records if (contact := normalize_barentswatch(record, fetched_at))]
    kept = filter_contacts(normalized)
    payload = {
        "schema_version": "1.0.0",
        "generated_at": fetched_at,
        "source": "BarentsWatch / Norwegian Coastal Administration",
        "provider": "barentswatch",
        "license": "NLOD/open-data terms; attribution required",
        "coverage": "Norwegian economic zone, Svalbard fisheries protection zone and Jan Mayen protection zone",
        "filter_mode": "Voodoo Whiskers VOI + flag-risk + Russian destination/port + tanker-context filters",
        "raw_count": len(normalized),
        "count": len(kept),
        "contacts": kept,
    }
    status = {"ok": True, "raw_count": len(normalized), "kept_count": len(kept)}
    return payload, status


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = build_session()
    status: dict[str, Any] = {"generated_at": utc_now_iso(), "providers": {}}
    successful = 0

    try:
        payload, provider_status = fetch_fintraffic(session)
        atomic_json_write(FINTRAFFIC_OUTPUT, payload)
        status["providers"]["fintraffic"] = provider_status
        successful += 1
        print(f"Fintraffic: {payload['raw_count']} raw / {payload['count']} kept")
    except Exception as exc:  # noqa: BLE001 - provider errors must not destroy LKG files
        status["providers"]["fintraffic"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(f"ERROR Fintraffic: {type(exc).__name__}: {exc}", file=sys.stderr)

    client_id = clean_str(os.getenv("BARENTSWATCH_CLIENT_ID"))
    client_secret = clean_str(os.getenv("BARENTSWATCH_CLIENT_SECRET"))
    if not client_id or not client_secret:
        status["providers"]["barentswatch"] = {
            "ok": False,
            "skipped": True,
            "error": "BARENTSWATCH_CLIENT_ID or BARENTSWATCH_CLIENT_SECRET missing",
        }
        print("BarentsWatch skipped: credentials are not configured.")
    else:
        try:
            payload, provider_status = fetch_barentswatch(session, client_id, client_secret)
            atomic_json_write(BARENTSWATCH_OUTPUT, payload)
            status["providers"]["barentswatch"] = provider_status
            successful += 1
            print(f"BarentsWatch: {payload['raw_count']} raw / {payload['count']} kept")
        except Exception as exc:  # noqa: BLE001 - provider errors must not destroy LKG files
            status["providers"]["barentswatch"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print(f"ERROR BarentsWatch: {type(exc).__name__}: {exc}", file=sys.stderr)

    status["successful_provider_fetches"] = successful
    atomic_json_write(STATUS_OUTPUT, status)

    # Do not fail merely because one provider is unavailable. merge_ais_contacts.py decides
    # whether fresh or acceptable last-known-good provider data remain available.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
