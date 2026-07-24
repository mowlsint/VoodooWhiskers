#!/usr/bin/env python3
"""Download and reduce the newest Danish historical AIS daily archive.

The raw ZIP/CSV is processed only in a temporary runner directory. The repository receives
only a compact, filtered product containing at most two temporally distinct valid positions
per vessel/MMSI plus small import-state and health files.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Deliberately self-contained: importing fetch_contacts.py would also import the
# optional AISStream websocket client, which is not needed for historical CSV work.

DATA_DIR = Path("data")
OUTPUT_PATH = DATA_DIR / "ais_contacts_aisdk_historical_latest.json"
STATUS_PATH = DATA_DIR / "ais_dk_import_status_latest.json"
STATE_PATH = DATA_DIR / "ais_dk_import_state.json"

BASE_URL = os.getenv("AIS_DK_BASE_URL", "https://aisdata.ais.dk/").strip() or "https://aisdata.ais.dk/"
DIRECT_URL = os.getenv("AIS_DK_SOURCE_URL", "").strip()
LOOKBACK_DAYS = int(os.getenv("AIS_DK_LOOKBACK_DAYS", "21"))
FILTER_MODE = os.getenv("AIS_DK_FILTER_MODE", "monitored").strip().lower()
TIMESTAMP_TIMEZONE = os.getenv("AIS_DK_TIMESTAMP_TIMEZONE", "UTC").strip() or "UTC"
TEMP_ROOT = Path(os.getenv("AIS_DK_TEMP_DIR", tempfile.gettempdir()))
MAX_DOWNLOAD_BYTES = int(os.getenv("AIS_DK_MAX_DOWNLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))
MAX_OUTPUT_BYTES = int(os.getenv("AIS_DK_MAX_OUTPUT_BYTES", str(25 * 1024 * 1024)))
APP_NAME = os.getenv("AIS_APP_NAME", "MOwlSINT Voodoo Whiskers/1.0")

COLUMNS = [
    "timestamp",
    "type_of_mobile",
    "mmsi",
    "latitude",
    "longitude",
    "navigational_status",
    "rot",
    "sog",
    "cog",
    "heading",
    "imo",
    "callsign",
    "name",
    "ship_type",
    "cargo_type",
    "width",
    "length",
    "position_fixing_device",
    "draught",
    "destination",
    "eta",
    "data_source_type",
    "size_a",
    "size_b",
    "size_c",
    "size_d",
]

ALLOWED_MOBILE_TYPES = {"class a", "class b"}


WATCHLIST_PATH = DATA_DIR / "watchlist_master.csv"
FLAG_RISK_PATH = DATA_DIR / "flag_risk_reference.csv"
PORTS_RU_PATH = DATA_DIR / "ports_ru.csv"

FALLBACK_FLAG_RISK_MIDS = {
    "306", "307", "312", "314", "341", "351", "352", "353", "354", "355", "356", "357",
    "370", "371", "372", "373", "511", "518", "538", "570", "607", "613", "616", "621",
    "626", "632", "636", "647", "650", "660", "667", "668", "669", "671", "676", "677", "679", "750",
}
RUSSIAN_PORT_ALLOWLIST = {"RUKGD", "RUBLT", "RUULU", "RUUST"}
RUSSIAN_PORT_NAME_ALIASES = {
    "USTLUGA", "STPETERSBURG", "SAINTPETERSBURG", "KALININGRAD", "BALTIYSK", "BALTISK",
    "PRIMORSK", "VYSOTSK", "VYBORG", "MURMANSK", "ARKHANGELSK", "NOVOROSSIYSK",
    "NOVOROSSIISK", "TUAPSE", "TAMAN", "KAVKAZ", "ROSTOV", "ROSTOVONDON", "AZOV",
    "TAGANROG", "MAKHACHKALA", "VLADIVOSTOK", "NAKHODKA", "KOZMINO", "VANINO",
    "DEKASTRI", "KORSAKOV", "SEVASTOPOL", "KERCH", "FEODOSIA",
}
TANKER_CONTEXT_ZONES = [
    {"id":"kaliningrad_baltiysk_approaches","min_lat":54.15,"max_lat":56.20,"min_lon":18.20,"max_lon":22.90},
    {"id":"gulf_of_gdansk","min_lat":53.95,"max_lat":55.85,"min_lon":17.35,"max_lon":20.80},
    {"id":"gulf_of_finland_ru_approaches","min_lat":58.40,"max_lat":60.85,"min_lon":23.40,"max_lon":30.70},
    {"id":"danish_straits_kattegat","min_lat":54.30,"max_lat":58.50,"min_lon":8.00,"max_lon":13.10},
    {"id":"skagen_waiting_area","min_lat":56.80,"max_lat":58.50,"min_lon":8.10,"max_lon":12.30},
    {"id":"german_bight","min_lat":53.00,"max_lat":56.25,"min_lon":4.70,"max_lon":9.40},
    {"id":"dover_channel_gateway","min_lat":50.70,"max_lat":51.75,"min_lon":-0.60,"max_lon":2.30},
    {"id":"gibraltar_west_med_gateway","min_lat":35.10,"max_lat":37.30,"min_lon":-6.20,"max_lon":-2.50},
]


def clean_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def digits(value: Any) -> str:
    return re.sub(r"\D", "", clean_str(value))


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_str(value).upper())


def norm_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", norm_text(value))


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_watchlist_index() -> dict[str, set[str]]:
    index = {"mmsi": set(), "imo": set(), "callsign": set()}
    for row in load_csv_rows(WATCHLIST_PATH):
        mmsi = digits(row.get("mmsi"))
        imo = digits(row.get("imo"))
        callsign = norm_text(row.get("callsign"))
        if mmsi:
            index["mmsi"].add(mmsi)
        if imo:
            index["imo"].add(imo)
        if callsign:
            index["callsign"].add(callsign)
    return index


def load_flag_risk_mids() -> set[str]:
    mids = set(FALLBACK_FLAG_RISK_MIDS)
    for row in load_csv_rows(FLAG_RISK_PATH):
        if clean_str(row.get("active", "true")).lower() not in {"1", "true", "yes", "y"}:
            continue
        for token in re.split(r"[;,|]", clean_str(row.get("mmsi_mid_prefixes"))):
            mid = digits(token)[:3]
            if mid:
                mids.add(mid)
    return mids


def load_russian_port_terms() -> tuple[set[str], set[str]]:
    codes = set(RUSSIAN_PORT_ALLOWLIST)
    names = set(RUSSIAN_PORT_NAME_ALIASES)
    for row in load_csv_rows(PORTS_RU_PATH):
        code = norm_key(row.get("unlocode"))
        name = norm_key(row.get("port_name"))
        if code:
            codes.add(code)
        if name:
            names.add(name)
    return codes, names


def is_tanker_contact(contact: dict[str, Any]) -> bool:
    raw_type = clean_str(contact.get("ship_type") or contact.get("ship_type_label"))
    try:
        code = int(float(raw_type))
        if 80 <= code <= 89:
            return True
    except (TypeError, ValueError):
        pass
    text = norm_text(" ".join(filter(None, [raw_type, clean_str(contact.get("destination"))])))
    return bool(re.search(r"\b(TANKER|OIL\s*TANKER|PRODUCT\s*TANKER|CHEMICAL\s*TANKER|CRUDE\s*TANKER|LNG\s*CARRIER|LPG\s*CARRIER|VLCC|SUEZMAX|AFRAMAX)\b", text))


def is_neutral_tanker_context_candidate(contact: dict[str, Any]) -> bool:
    if not is_tanker_contact(contact):
        return False
    lat = parse_float(contact.get("latitude"))
    lon = parse_float(contact.get("longitude"))
    return bool(lat is not None and lon is not None and any(
        zone["min_lat"] <= lat <= zone["max_lat"] and zone["min_lon"] <= lon <= zone["max_lon"]
        for zone in TANKER_CONTEXT_ZONES
    ))


def keep_contact(
    contact: dict[str, Any],
    watch_index: dict[str, set[str]],
    risk_mids: set[str],
    ru_codes: set[str],
    ru_names: set[str],
) -> bool:
    mmsi = digits(contact.get("mmsi"))
    imo = digits(contact.get("imo"))
    callsign = norm_text(contact.get("callsign"))
    watch_match = bool(
        (mmsi and mmsi in watch_index["mmsi"])
        or (imo and imo in watch_index["imo"])
        or (callsign and callsign in watch_index["callsign"])
    )
    flag_risk = len(mmsi) >= 3 and mmsi[:3] in risk_mids
    destination = norm_key(contact.get("destination"))
    russian_destination = bool(
        destination
        and (
            any(code in destination for code in ru_codes)
            or any(len(name) >= 4 and name in destination for name in ru_names)
        )
    )
    return bool(
        mmsi.startswith("273")
        or watch_match
        or flag_risk
        or russian_destination
        or is_neutral_tanker_context_candidate(contact)
    )


@dataclass(frozen=True)
class SourceInfo:
    url: str
    filename: str
    date_hint: str | None
    etag: str
    last_modified: str
    content_length: int | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": APP_NAME, "Accept": "*/*"})
    return session


def probe_url(session: requests.Session, url: str) -> SourceInfo | None:
    response: requests.Response | None = None
    try:
        response = session.head(url, allow_redirects=True, timeout=(20, 60))
        if response.status_code in {403, 405}:
            response.close()
            response = session.get(
                url,
                headers={"Range": "bytes=0-0"},
                stream=True,
                allow_redirects=True,
                timeout=(20, 60),
            )
        if response.status_code not in {200, 206}:
            return None
        final_url = response.url or url
        filename = Path(urlparse(final_url).path).name or Path(urlparse(url).path).name
        if not filename.lower().endswith(".zip"):
            return None
        length_text = response.headers.get("Content-Length", "").strip()
        content_length = int(length_text) if length_text.isdigit() else None
        match = re.search(r"(20\d{2}-\d{2}-\d{2})", filename)
        return SourceInfo(
            url=final_url,
            filename=filename,
            date_hint=match.group(1) if match else None,
            etag=response.headers.get("ETag", "").strip(),
            last_modified=response.headers.get("Last-Modified", "").strip(),
            content_length=content_length,
        )
    except requests.RequestException:
        return None
    finally:
        if response is not None:
            response.close()


def discover_source(session: requests.Session) -> SourceInfo:
    if DIRECT_URL:
        source = probe_url(session, DIRECT_URL)
        if not source:
            raise RuntimeError(f"Configured AIS_DK_SOURCE_URL is unavailable: {DIRECT_URL}")
        return source

    # Daily archives use the stable aisdk-YYYY-MM-DD.zip convention. Probe newest dates
    # backwards instead of downloading or parsing a potentially slow directory listing.
    today = utc_now().date()
    bases = []
    for base in (BASE_URL, BASE_URL.replace("https://", "http://", 1)):
        if base not in bases:
            bases.append(base)
    for offset in range(0, max(1, LOOKBACK_DAYS) + 1):
        day = today - timedelta(days=offset)
        filename = f"aisdk-{day.isoformat()}.zip"
        for base in bases:
            source = probe_url(session, urljoin(base.rstrip("/") + "/", filename))
            if source:
                return source
    raise RuntimeError(f"No Danish AIS archive found within the last {LOOKBACK_DAYS} days")


def source_fingerprint(source: SourceInfo) -> dict[str, Any]:
    return {
        "source_url": source.url,
        "source_filename": source.filename,
        "source_date_hint": source.date_hint,
        "etag": source.etag,
        "last_modified": source.last_modified,
        "content_length": source.content_length,
    }


def source_unchanged(source: SourceInfo, state: dict[str, Any]) -> bool:
    old = state.get("source") if isinstance(state.get("source"), dict) else {}
    if old.get("source_url") != source.url:
        return False
    comparable = ["etag", "last_modified", "content_length"]
    known = [(key, source_fingerprint(source).get(key), old.get(key)) for key in comparable]
    positive = [(key, new, previous) for key, new, previous in known if new not in (None, "")]
    return bool(positive) and all(new == previous for _key, new, previous in positive)


def download_archive(session: requests.Session, source: SourceInfo, directory: Path) -> tuple[Path, str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.filename
    digest = hashlib.sha256()
    total = 0
    with session.get(source.url, stream=True, allow_redirects=True, timeout=(30, 600)) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"Danish AIS download exceeded limit of {MAX_DOWNLOAD_BYTES} bytes")
                handle.write(chunk)
                digest.update(chunk)
    if total == 0:
        raise RuntimeError("Danish AIS archive download was empty")
    return target, digest.hexdigest(), total


def parse_float(value: Any) -> float | None:
    text = str(value or "").strip().replace("Unknown", "")
    if not text:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    number = parse_float(value)
    return int(number) if number is not None and number.is_integer() else None


def normalize_sog(value: Any) -> float | None:
    number = parse_float(value)
    return round(number, 2) if number is not None and 0 <= number < 102.2 else None


def normalize_cog(value: Any) -> float | None:
    number = parse_float(value)
    return round(number, 2) if number is not None and 0 <= number < 360 else None


def normalize_heading(value: Any) -> int | None:
    number = parse_float(value)
    return int(round(number)) if number is not None and 0 <= number < 511 else None


def parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        # The source documentation describes base-station timestamps as DD/MM/YYYY HH:MM:SS.
        parsed = datetime.strptime(text, "%d/%m/%Y %H:%M:%S")
        # The provider does not encode an offset in the CSV. Voodoo records the UTC assumption
        # explicitly so it is never mistaken for a verified timezone-bearing source timestamp.
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def valid_position(lat: float | None, lon: float | None) -> bool:
    return bool(
        lat is not None
        and lon is not None
        and -90 <= lat <= 90
        and -180 <= lon <= 180
        and not (abs(lat) < 1e-12 and abs(lon) < 1e-12)
    )


def normalize_unknown(value: Any) -> str:
    text = clean_str(value)
    return "" if text.upper() in {"UNKNOWN", "UNKNOWN VALUE", "N/A", "NA", "NULL", "NONE", "0"} else text


def record_score(record: dict[str, Any]) -> int:
    fields = (
        "imo",
        "callsign",
        "name",
        "ship_type_label",
        "cargo_type",
        "width_m",
        "length_m",
        "draught_m",
        "destination",
        "eta_raw",
        "navigational_status",
        "sog",
        "cog",
        "true_heading",
    )
    return sum(record.get(field) not in (None, "") for field in fields)


def row_to_record(row: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    if len(row) != len(COLUMNS):
        return None, "wrong_column_count"
    values = dict(zip(COLUMNS, row, strict=True))
    if values["timestamp"].strip().lower() == "timestamp":
        return None, "header"
    mobile_type = " ".join(values["type_of_mobile"].strip().lower().split())
    if mobile_type not in ALLOWED_MOBILE_TYPES:
        return None, "non_vessel_mobile_type"
    mmsi = digits(values["mmsi"])
    if len(mmsi) != 9:
        return None, "invalid_mmsi"
    observed = parse_timestamp(values["timestamp"])
    if observed is None:
        return None, "invalid_timestamp"
    lat = parse_float(values["latitude"])
    lon = parse_float(values["longitude"])
    if not valid_position(lat, lon):
        return None, "invalid_position"
    imo = digits(values["imo"])
    if len(imo) != 7:
        imo = ""
    ship_type_label = normalize_unknown(values["ship_type"])
    tanker_type = "tanker" in ship_type_label.lower()
    record = {
        "mmsi": mmsi,
        "imo": imo,
        "callsign": normalize_unknown(values["callsign"]),
        "name": normalize_unknown(values["name"]),
        "latitude": round(float(lat), 6),
        "longitude": round(float(lon), 6),
        "navigational_status": normalize_unknown(values["navigational_status"]),
        "rot": parse_float(values["rot"]),
        "sog": normalize_sog(values["sog"]),
        "cog": normalize_cog(values["cog"]),
        "true_heading": normalize_heading(values["heading"]),
        "ship_type": 80 if tanker_type else ship_type_label,
        "ship_type_label": ship_type_label,
        "is_tanker_context_candidate": tanker_type,
        "cargo_type": normalize_unknown(values["cargo_type"]),
        "width_m": parse_float(values["width"]),
        "length_m": parse_float(values["length"]),
        "position_fixing_device": normalize_unknown(values["position_fixing_device"]),
        "draught_m": parse_float(values["draught"]),
        "destination": normalize_unknown(values["destination"]),
        "eta_raw": normalize_unknown(values["eta"]),
        "data_source_type": normalize_unknown(values["data_source_type"]) or "AIS",
        "size_a_m": parse_float(values["size_a"]),
        "size_b_m": parse_float(values["size_b"]),
        "size_c_m": parse_float(values["size_c"]),
        "size_d_m": parse_float(values["size_d"]),
        "target_type": values["type_of_mobile"].strip(),
        "observed_at": observed.isoformat(),
        "last_seen_utc": observed.isoformat(),
        "_observed_dt": observed,
    }
    return record, None


def same_position(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left.get("latitude") == right.get("latitude") and left.get("longitude") == right.get("longitude")


def insert_two_latest(
    store: dict[str, list[dict[str, Any]]],
    record: dict[str, Any],
    counters: dict[str, int],
) -> None:
    mmsi = record["mmsi"]
    current = store.setdefault(mmsi, [])
    observed = record["_observed_dt"]
    for index, existing in enumerate(current):
        if existing["_observed_dt"] != observed:
            continue
        if same_position(existing, record):
            counters["duplicate_same_time_position"] += 1
        else:
            counters["conflicting_positions_same_timestamp"] += 1
        if record_score(record) > record_score(existing):
            current[index] = record
        return
    current.append(record)
    current.sort(key=lambda item: item["_observed_dt"], reverse=True)
    if len(current) > 2:
        current.pop()
        counters["older_positions_discarded"] += 1


def iter_csv_rows(archive_path: Path) -> Iterable[list[str]]:
    with zipfile.ZipFile(archive_path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")]
        if not names:
            raise RuntimeError("Danish AIS ZIP contains no CSV file")
        for name in sorted(names):
            with archive.open(name, "r") as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
                yield from csv.reader(text, delimiter=",", quotechar='"')


def public_position(record: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "observed_at": record["observed_at"],
        "latitude": record["latitude"],
        "longitude": record["longitude"],
        "sog": record.get("sog"),
        "cog": record.get("cog"),
        "true_heading": record.get("true_heading"),
        "navigational_status": record.get("navigational_status") or "",
    }


def build_contact(records: list[dict[str, Any]]) -> dict[str, Any]:
    latest = records[0]
    previous = records[1] if len(records) > 1 else None
    merged = dict(previous or {})
    merged.update({key: value for key, value in latest.items() if value not in (None, "")})
    for key in list(merged):
        if key.startswith("_"):
            merged.pop(key, None)
    positions = [public_position(latest, 1)]
    if previous:
        positions.append(public_position(previous, 2))
    merged.update({
        "source": "Danish historical AIS",
        "source_provider": "ais_dk_historical",
        "source_data_type": latest.get("data_source_type") or "AIS",
        "message_type_last": "HistoricalDailyArchive",
        "historical": True,
        "coverage_mode": "historical_delayed",
        "position_timestamp_valid": True,
        "position_timestamp_basis": "source_basestation_timestamp_utc_assumption",
        "timestamp_timezone_assumption": TIMESTAMP_TIMEZONE,
        "positions": positions,
        "position_count": len(positions),
        "latest_position": positions[0],
        "previous_position": positions[1] if len(positions) > 1 else None,
    })
    return merged


def apply_monitoring_filter(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if FILTER_MODE in {"all", "all_class_ab", "unfiltered"}:
        return contacts
    if FILTER_MODE != "monitored":
        raise RuntimeError(f"Unsupported AIS_DK_FILTER_MODE: {FILTER_MODE}")
    watch_idx = load_watchlist_index()
    risk_mids = load_flag_risk_mids()
    ru_codes, ru_names = load_russian_port_terms()
    return [contact for contact in contacts if keep_contact(contact, watch_idx, risk_mids, ru_codes, ru_names)]


def process_archive(archive_path: Path, source: SourceInfo, sha256: str, download_bytes: int) -> tuple[dict[str, Any], dict[str, Any]]:
    started = utc_now()
    store: dict[str, list[dict[str, Any]]] = {}
    counters: dict[str, int] = {
        "rows_read": 0,
        "headers_skipped": 0,
        "wrong_column_count": 0,
        "non_vessel_mobile_type": 0,
        "invalid_mmsi": 0,
        "invalid_timestamp": 0,
        "invalid_position": 0,
        "duplicate_same_time_position": 0,
        "conflicting_positions_same_timestamp": 0,
        "older_positions_discarded": 0,
    }
    earliest: datetime | None = None
    latest: datetime | None = None
    for row in iter_csv_rows(archive_path):
        counters["rows_read"] += 1
        record, reason = row_to_record(row)
        if reason:
            if reason == "header":
                counters["headers_skipped"] += 1
            else:
                counters[reason] = counters.get(reason, 0) + 1
            continue
        assert record is not None
        observed = record["_observed_dt"]
        earliest = observed if earliest is None or observed < earliest else earliest
        latest = observed if latest is None or observed > latest else latest
        insert_two_latest(store, record, counters)

    contacts_all = [build_contact(records) for _mmsi, records in sorted(store.items())]
    contacts = apply_monitoring_filter(contacts_all)
    contacts.sort(key=lambda item: (digits(item.get("mmsi")), clean_str(item.get("name"))))
    generated = utc_now()
    lag_hours = round((generated - latest).total_seconds() / 3600.0, 2) if latest else None
    payload = {
        "schema_version": "1.0.0",
        "generated_at": iso(generated),
        "source": "Danish historical AIS",
        "provider": "ais_dk_historical",
        "source_url": source.url,
        "source_filename": source.filename,
        "source_date_hint": source.date_hint,
        "source_sha256": sha256,
        "source_download_bytes": download_bytes,
        "coverage": "Danish land-based AIS historical daily archive; delayed and not a current traffic picture",
        "coverage_mode": "historical_delayed",
        "historical": True,
        "filter_mode": FILTER_MODE,
        "timestamp_timezone_assumption": TIMESTAMP_TIMEZONE,
        "data_min_timestamp_utc": earliest.isoformat() if earliest else None,
        "data_max_timestamp_utc": latest.isoformat() if latest else None,
        "lag_hours_at_build": lag_hours,
        "raw_unique_class_ab_vessels": len(contacts_all),
        "count": len(contacts),
        "max_positions_per_vessel": 2,
        "positions_must_have_distinct_timestamps": True,
        "contacts": contacts,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise RuntimeError(f"Reduced Danish AIS product is too large: {len(encoded)} > {MAX_OUTPUT_BYTES} bytes")
    status = {
        "schema_version": "1.0.0",
        "generated_at": iso(generated),
        "ok": True,
        "changed": True,
        "provider": "ais_dk_historical",
        "source": source_fingerprint(source),
        "source_sha256": sha256,
        "source_download_bytes": download_bytes,
        "raw_files_persisted": False,
        "filter_mode": FILTER_MODE,
        "max_positions_per_vessel": 2,
        "data_min_timestamp_utc": payload["data_min_timestamp_utc"],
        "data_max_timestamp_utc": payload["data_max_timestamp_utc"],
        "lag_hours_at_build": lag_hours,
        "raw_unique_class_ab_vessels": len(contacts_all),
        "published_vessels": len(contacts),
        "published_positions": sum(int(contact.get("position_count") or 0) for contact in contacts),
        "processing_seconds": round((generated - started).total_seconds(), 2),
        "counters": counters,
    }
    return payload, status


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    session = build_session()
    checked_at = iso()
    try:
        source = discover_source(session)
    except Exception as exc:  # noqa: BLE001
        status = {
            "schema_version": "1.0.0",
            "generated_at": checked_at,
            "ok": False,
            "changed": False,
            "provider": "ais_dk_historical",
            "error": f"{type(exc).__name__}: {exc}",
            "last_known_good_available": OUTPUT_PATH.exists(),
            "raw_files_persisted": False,
        }
        atomic_json(STATUS_PATH, status)
        print(f"ERROR: {status['error']}", file=sys.stderr)
        return 0 if OUTPUT_PATH.exists() else 1

    state = read_json(STATE_PATH)
    if source_unchanged(source, state) and OUTPUT_PATH.exists():
        existing = read_json(OUTPUT_PATH)
        status = {
            "schema_version": "1.0.0",
            "generated_at": checked_at,
            "ok": True,
            "changed": False,
            "provider": "ais_dk_historical",
            "source": source_fingerprint(source),
            "reason": "source_unchanged",
            "last_known_good_available": True,
            "data_max_timestamp_utc": existing.get("data_max_timestamp_utc"),
            "published_vessels": existing.get("count", 0),
            "raw_files_persisted": False,
        }
        atomic_json(STATUS_PATH, status)
        print(f"Danish AIS source unchanged: {source.filename}")
        return 0

    archive_path: Path | None = None
    try:
        archive_path, sha256, download_bytes = download_archive(session, source, TEMP_ROOT)
        payload, status = process_archive(archive_path, source, sha256, download_bytes)
        atomic_json(OUTPUT_PATH, payload)
        atomic_json(STATUS_PATH, status)
        atomic_json(STATE_PATH, {
            "schema_version": "1.0.0",
            "updated_at": status["generated_at"],
            "source": source_fingerprint(source),
            "source_sha256": sha256,
            "data_max_timestamp_utc": payload.get("data_max_timestamp_utc"),
        })
        print(
            f"Danish AIS: {status['published_vessels']} vessels / "
            f"{status['published_positions']} positions from {source.filename}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        status = {
            "schema_version": "1.0.0",
            "generated_at": iso(),
            "ok": False,
            "changed": False,
            "provider": "ais_dk_historical",
            "source": source_fingerprint(source),
            "error": f"{type(exc).__name__}: {exc}",
            "last_known_good_available": OUTPUT_PATH.exists(),
            "raw_files_persisted": False,
        }
        atomic_json(STATUS_PATH, status)
        print(f"ERROR: {status['error']}", file=sys.stderr)
        return 0 if OUTPUT_PATH.exists() else 1
    finally:
        if archive_path and archive_path.exists():
            archive_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
