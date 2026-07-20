import asyncio
import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

DATA_DIR = Path("data")
WATCHLIST_PATH = DATA_DIR / "watchlist_master.csv"
FLAG_RISK_PATH = DATA_DIR / "flag_risk_reference.csv"
PORTS_RU_PATH = DATA_DIR / "ports_ru.csv"
OUTPUT_PATH = DATA_DIR / "ais_contacts_aisstream_latest.json"

# Context-only tanker retention zones. These mirror build_layers.py and allow
# neutral tankers to be visible as a grey, non-VOI context layer.
TANKER_CONTEXT_ZONES = [
    {"id": "kaliningrad_baltiysk_approaches", "min_lat": 54.15, "max_lat": 56.20, "min_lon": 18.20, "max_lon": 22.90},
    {"id": "gulf_of_gdansk", "min_lat": 53.95, "max_lat": 55.85, "min_lon": 17.35, "max_lon": 20.80},
    {"id": "gulf_of_finland_ru_approaches", "min_lat": 58.40, "max_lat": 60.85, "min_lon": 23.40, "max_lon": 30.70},
    {"id": "danish_straits_kattegat", "min_lat": 54.30, "max_lat": 58.50, "min_lon": 8.00, "max_lon": 13.10},
    {"id": "skagen_waiting_area", "min_lat": 56.80, "max_lat": 58.50, "min_lon": 8.10, "max_lon": 12.30},
    {"id": "german_bight", "min_lat": 53.00, "max_lat": 56.25, "min_lon": 4.70, "max_lon": 9.40},
    {"id": "dover_channel_gateway", "min_lat": 50.70, "max_lat": 51.75, "min_lon": -0.60, "max_lon": 2.30},
    {"id": "gibraltar_west_med_gateway", "min_lat": 35.10, "max_lat": 37.30, "min_lon": -6.20, "max_lon": -2.50},
]

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"

MESSAGE_TYPES = [
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
    "StaticDataReport",
]

BOUNDING_BOXES = [
    [[53.0, 3.0], [60.8, 30.5]],    # North Sea + Baltic + Danish Straits
    [[49.5, -6.5], [53.5, 3.5]],    # English Channel / Dover / Western approaches gateway
    [[35.0, -6.5], [38.8, 16.5]],   # Gibraltar / western Mediterranean shadow-fleet gateway
    [[41.0, 26.0], [47.5, 42.5]],   # Black Sea
]

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

def clean_str(v):
    return "" if v is None else str(v).strip()

def norm_text(v):
    s = clean_str(v).upper()
    s = re.sub(r"\s+", " ", s)
    return s

def norm_key(v):
    return re.sub(r"[^A-Z0-9]", "", norm_text(v))

def digits(v):
    return re.sub(r"\D", "", clean_str(v))

def merge_contact(dst, src):
    for k, v in src.items():
        if v in ("", None, [], {}):
            continue
        dst[k] = v

def contact_key(d):
    return digits(d.get("mmsi")) or digits(d.get("imo")) or norm_text(d.get("callsign")) or norm_text(d.get("name"))

def is_russian_mmsi_prefix(d):
    return digits(d.get("mmsi")).startswith("273")

def load_csv_rows(path):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))

def load_watchlist_index():
    idx = {"mmsi": set(), "imo": set(), "callsign": set(), "name": set()}
    for row in load_csv_rows(WATCHLIST_PATH):
        mmsi = digits(row.get("mmsi")); imo = digits(row.get("imo")); callsign = norm_text(row.get("callsign")); name = norm_text(row.get("name"))
        if mmsi: idx["mmsi"].add(mmsi)
        if imo: idx["imo"].add(imo)
        if callsign: idx["callsign"].add(callsign)
        if name: idx["name"].add(name)
    return idx

def load_flag_risk_mids():
    mids = set(FALLBACK_FLAG_RISK_MIDS)
    for row in load_csv_rows(FLAG_RISK_PATH):
        if str(row.get("active", "true")).strip().lower() not in {"1", "true", "yes", "y"}:
            continue
        for token in re.split(r"[;,|]", clean_str(row.get("mmsi_mid_prefixes"))):
            mid = digits(token)[:3]
            if mid:
                mids.add(mid)
    return mids

def load_russian_port_terms():
    codes = set(RUSSIAN_PORT_ALLOWLIST)
    names = set(RUSSIAN_PORT_NAME_ALIASES)
    for row in load_csv_rows(PORTS_RU_PATH):
        code = norm_key(row.get("unlocode")); name = norm_key(row.get("port_name"))
        if code: codes.add(code)
        if name: names.add(name)
    return codes, names

def is_flag_risk_mid(d, risk_mids):
    mmsi = digits(d.get("mmsi"))
    return len(mmsi) >= 3 and mmsi[:3] in risk_mids

def has_russian_destination_or_port(d, ru_codes, ru_names):
    raw = " ".join(clean_str(d.get(k)) for k in ["destination", "Destination", "last_port_name", "last_port_unlocode", "port_unlocode", "next_port"] if clean_str(d.get(k)))
    if not raw:
        return False
    compact = norm_key(raw)
    if any(code in compact for code in ru_codes):
        return True
    return any(len(name) >= 4 and name in compact for name in ru_names)


def parse_float(v):
    try:
        return float(v)
    except Exception:
        return None


def is_tanker_contact(d):
    raw_type = clean_str(d.get("ship_type") or d.get("ShipType") or d.get("type") or d.get("Type"))
    try:
        ship_type = int(float(raw_type))
        if 80 <= ship_type <= 89:
            return True
    except Exception:
        pass
    txt = norm_text(" ".join(clean_str(d.get(k)) for k in ["ship_type_text", "vessel_type", "destination", "Destination"] if clean_str(d.get(k))))
    return bool(re.search(r"\b(TANKER|OIL\s*TANKER|PRODUCT\s*TANKER|CHEMICAL\s*TANKER|CRUDE\s*TANKER|LNG\s*CARRIER|LPG\s*CARRIER|VLCC|SUEZMAX|AFRAMAX)\b", txt))


def tanker_context_zone_ids(d):
    if not is_tanker_contact(d):
        return []
    lat = parse_float(d.get("latitude") or d.get("Latitude"))
    lon = parse_float(d.get("longitude") or d.get("Longitude"))
    if lat is None or lon is None:
        return []
    return [z["id"] for z in TANKER_CONTEXT_ZONES if z["min_lat"] <= lat <= z["max_lat"] and z["min_lon"] <= lon <= z["max_lon"]]


def is_neutral_tanker_context_candidate(d):
    return bool(tanker_context_zone_ids(d))


def is_watchlist_match(d, idx):
    mmsi = digits(d.get("mmsi")); imo = digits(d.get("imo")); callsign = norm_text(d.get("callsign"))
    # Deliberately no pure-name match: common AIS names create false positives.
    return ((mmsi and mmsi in idx["mmsi"]) or (imo and imo in idx["imo"]) or (callsign and callsign in idx["callsign"]))

def keep_contact(d, idx, risk_mids, ru_codes, ru_names):
    return (
        is_russian_mmsi_prefix(d)
        or is_watchlist_match(d, idx)
        or is_flag_risk_mid(d, risk_mids)
        or has_russian_destination_or_port(d, ru_codes, ru_names)
        or is_neutral_tanker_context_candidate(d)
    )

def extract_contact(msg):
    md = msg.get("MetaData") or msg.get("Metadata") or {}
    mt = msg.get("MessageType", "")
    body = (msg.get("Message") or {}).get(mt, {}) if mt else {}
    out = {"mmsi": "", "imo": "", "callsign": "", "name": "", "latitude": "", "longitude": "", "destination": "", "ship_type": "", "navigational_status": "", "sog": "", "cog": "", "true_heading": "", "source": "AISStream", "message_type_last": mt, "last_seen_utc": md.get("time_utc") or datetime.now(timezone.utc).isoformat()}
    if md:
        if md.get("MMSI") is not None: out["mmsi"] = str(md.get("MMSI"))
        if md.get("ShipName"): out["name"] = clean_str(md.get("ShipName"))
        if md.get("latitude") is not None: out["latitude"] = md.get("latitude")
        if md.get("longitude") is not None: out["longitude"] = md.get("longitude")
        if md.get("Latitude") is not None and out["latitude"] == "": out["latitude"] = md.get("Latitude")
        if md.get("Longitude") is not None and out["longitude"] == "": out["longitude"] = md.get("Longitude")
    if mt in {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}:
        if body.get("UserID") is not None: out["mmsi"] = str(body.get("UserID"))
        if body.get("Latitude") is not None: out["latitude"] = body.get("Latitude")
        if body.get("Longitude") is not None: out["longitude"] = body.get("Longitude")
        out["navigational_status"] = body.get("NavigationalStatus", ""); out["sog"] = body.get("Sog", ""); out["cog"] = body.get("Cog", ""); out["true_heading"] = body.get("TrueHeading", "")
        if body.get("Name") and not out["name"]: out["name"] = clean_str(body.get("Name"))
    elif mt == "ShipStaticData":
        if body.get("UserID") is not None: out["mmsi"] = str(body.get("UserID"))
        if body.get("ImoNumber") is not None: out["imo"] = str(body.get("ImoNumber"))
        if body.get("CallSign"): out["callsign"] = clean_str(body.get("CallSign"))
        if body.get("Name"): out["name"] = clean_str(body.get("Name"))
        if body.get("Destination"): out["destination"] = clean_str(body.get("Destination"))
        if body.get("Type") is not None: out["ship_type"] = body.get("Type")
    elif mt == "StaticDataReport":
        if body.get("UserID") is not None: out["mmsi"] = str(body.get("UserID"))
        report_a = body.get("ReportA") or {}; report_b = body.get("ReportB") or {}
        if report_a.get("Name"): out["name"] = clean_str(report_a.get("Name"))
        if report_b.get("CallSign"): out["callsign"] = clean_str(report_b.get("CallSign"))
        if report_b.get("ShipType") is not None: out["ship_type"] = report_b.get("ShipType")
    return out

async def collect_once(subscription, duration_seconds):
    contacts = {}
    async with websockets.connect(AISSTREAM_URL, ping_interval=20, ping_timeout=20, max_size=2**22, close_timeout=5) as ws:
        await ws.send(json.dumps(subscription))
        end_time = time.monotonic() + duration_seconds
        while time.monotonic() < end_time:
            remaining = max(0.1, end_time - time.monotonic())
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(5, remaining))
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            if isinstance(msg, dict) and msg.get("error"):
                raise RuntimeError(msg["error"])
            c = extract_contact(msg)
            key = contact_key(c)
            if not key:
                continue
            if key not in contacts:
                contacts[key] = {}
            merge_contact(contacts[key], c)
    return list(contacts.values())

async def main():
    api_key = clean_str(os.getenv("AISSTREAM_API_KEY"))
    if not api_key:
        raise SystemExit("AISSTREAM_API_KEY missing")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    watch_idx = load_watchlist_index()
    risk_mids = load_flag_risk_mids()
    ru_codes, ru_names = load_russian_port_terms()
    subscription = {"APIKey": api_key, "BoundingBoxes": BOUNDING_BOXES, "FilterMessageTypes": MESSAGE_TYPES}
    contacts_raw = await collect_once(subscription, 1800)
    contacts_out = [c for c in contacts_raw if keep_contact(c, watch_idx, risk_mids, ru_codes, ru_names)]
    payload = {"schema_version": "1.0.0", "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "source": "AISStream", "provider": "aisstream", "license": "AISstream terms", "filter_mode": "BoundingBoxes + Russian MMSI + watchlist + flag-risk MMSI prefixes + Russian destination/port terms + neutral tanker context zones", "count": len(contacts_out), "contacts": contacts_out}
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
