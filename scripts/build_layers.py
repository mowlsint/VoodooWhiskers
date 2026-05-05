import csv
import json
import os
import asyncio
from datetime import datetime, timezone, timedelta

import requests
import websockets

NOW_DT = datetime.now(timezone.utc)
NOW = NOW_DT.strftime("%Y-%m-%dT%H:%M:%SZ")

DATA_DIR = "data"
RUSSIAN_MID = "273"
PORTCALL_WINDOW_DAYS = 10

GFW_TOKEN = os.getenv("GFW_TOKEN", "").strip()
AISSTREAM_API_KEY = os.getenv("AISSTREAM_API_KEY", "").strip()

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
GFW_API = "https://gateway.api.globalfishingwatch.org/v3"


def log(msg):
    print(f"[build_layers] {msg}")


def feature(lon, lat, props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def save_geojson(path, features):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, indent=2)
    log(f"saved {path} with {len(features)} features")


def load_csv(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    log(f"loaded {len(rows)} rows from {path}")
    return rows


def to_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_dt(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_flag_risk_reference():
    return load_csv(f"{DATA_DIR}/flag_risk_reference.csv")


def load_ru_ports():
    rows = load_csv(f"{DATA_DIR}/ports_ru.csv")
    ports = {(r.get("unlocode") or "").strip().upper(): r for r in rows if r.get("unlocode")}
    log(f"indexed {len(ports)} Russian ports")
    return ports


def load_watchlist():
    rows = load_csv(f"{DATA_DIR}/watchlist_master.csv")
    log(f"watchlist rows: {len(rows)}")
    return rows


def gfw_headers():
    return {"Authorization": f"Bearer {GFW_TOKEN}"} if GFW_TOKEN else {}


def extract_gfw_vessel_id(entry):
    if not isinstance(entry, dict):
        return None

    direct_keys = ["id", "vesselId", "vessel_id"]
    for key in direct_keys:
        value = entry.get(key)
        if value:
            return value

    nested_keys = ["identity", "vessel", "profile"]
    for nk in nested_keys:
        obj = entry.get(nk)
        if isinstance(obj, dict):
            for key in direct_keys:
                value = obj.get(key)
                if value:
                    return value

    return None


def gfw_search_once(query):
    if not GFW_TOKEN or not query:
        return None

    url = f"{GFW_API}/vessels/search"
    params = {
        "query": query,
        "datasets[0]": "public-global-vessel-identity:latest"
    }

    try:
        r = requests.get(url, headers=gfw_headers(), params=params, timeout=60)
        log(f"GFW vessel search [{query}] -> HTTP {r.status_code}")
        if r.status_code != 200:
            return None

        data = r.json()
        entries = data.get("entries") or data.get("data") or data.get("results") or []
        log(f"GFW vessel search [{query}] -> {len(entries)} candidate(s)")

        if not entries:
            return None

        first = entries[0]
        vessel_id = extract_gfw_vessel_id(first)
        log(f"GFW first candidate [{query}] -> vessel_id={vessel_id}")

        if vessel_id:
            first["_resolved_vessel_id"] = vessel_id
        return first

    except Exception as e:
        log(f"GFW vessel search [{query}] failed: {e}")
        return None


def gfw_search_vessel(row):
    candidates = [
        (row.get("imo") or "").strip(),
        (row.get("mmsi") or "").strip(),
        (row.get("callsign") or "").strip(),
        (row.get("name") or "").strip(),
    ]

    seen = set()
    for query in candidates:
        if not query or query in seen:
            continue
        seen.add(query)

        result = gfw_search_once(query)
        if result:
            return result, query

    return None, None


def gfw_get_port_visits(vessel_id):
    if not GFW_TOKEN:
        log("GFW token missing for port visits")
        return []

    if not vessel_id:
        return []

    start_date = (NOW_DT - timedelta(days=PORTCALL_WINDOW_DAYS)).strftime("%Y-%m-%d")
    end_date = NOW_DT.strftime("%Y-%m-%d")
    url = f"{GFW_API}/events"
    params = {
        "vessels[0]": vessel_id,
        "datasets[0]": "public-global-fishing-events:latest",
        "start-date": start_date,
        "end-date": end_date,
        "limit": 100,
        "offset": 0,
        "types[0]": "PORT_VISIT"
    }

    try:
        r = requests.get(url, headers=gfw_headers(), params=params, timeout=60)
        log(f"GFW port visits [{vessel_id}] -> HTTP {r.status_code}")
        if r.status_code != 200:
            return []
        data = r.json()
        entries = data.get("entries") or data.get("events") or data.get("data") or data.get("results") or []
        log(f"GFW port visits [{vessel_id}] -> {len(entries)} event(s)")
        return entries
    except Exception as e:
        log(f"GFW port visits [{vessel_id}] failed: {e}")
        return []


def extract_position_from_ais_message(msg):
    msg_type = msg.get("MessageType")
    metadata = msg.get("MetaData") or msg.get("Metadata") or {}
    body = msg.get("Message") or {}

    candidate_keys = [
        "PositionReport",
        "StandardClassBPositionReport",
        "ExtendedClassBPositionReport",
    ]

    for key in candidate_keys:
        payload = body.get(key)
        if isinstance(payload, dict):
            mmsi = str(payload.get("UserID") or metadata.get("MMSI") or "").strip()
            lat = payload.get("Latitude")
            lon = payload.get("Longitude")
            if mmsi and lat is not None and lon is not None:
                return {
                    "mmsi": mmsi,
                    "lat": lat,
                    "lon": lon,
                    "msg_type": key,
                    "timestamp": metadata.get("time_utc") or NOW
                }

    mmsi = str(metadata.get("MMSI") or "").strip()
    lat = metadata.get("latitude") or metadata.get("Latitude")
    lon = metadata.get("longitude") or metadata.get("Longitude")
    if mmsi and lat is not None and lon is not None:
        return {
            "mmsi": mmsi,
            "lat": lat,
            "lon": lon,
            "msg_type": msg_type or "MetaDataOnly",
            "timestamp": metadata.get("time_utc") or NOW
        }

    return None


async def aisstream_positions_once(mmsi_filters):
    if not AISSTREAM_API_KEY:
        log("AISStream API key missing")
        return {}

    mmsi_filters = [m for m in mmsi_filters if m]
    if not mmsi_filters:
        log("AISStream: no MMSI filters provided")
        return {}

    results = {}
    log(f"AISStream: requesting positions for {len(mmsi_filters)} MMSI")

    subscription = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": [[[-90, -180], [90, 180]]],
        "FiltersShipMMSI": mmsi_filters[:50],
        "FilterMessageTypes": [
            "PositionReport",
            "StandardClassBPositionReport",
            "ExtendedClassBPositionReport"
        ]
    }

    try:
        async with websockets.connect(AISSTREAM_URL, open_timeout=20, close_timeout=10) as ws:
            await ws.send(json.dumps(subscription))
            log("AISStream: subscription sent")

            deadline = datetime.now(timezone.utc) + timedelta(seconds=90)
            received_messages = 0

            while datetime.now(timezone.utc) < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                except asyncio.TimeoutError:
                    log("AISStream: waiting for messages...")
                    continue

                received_messages += 1
                msg = json.loads(raw)

                if "error" in msg:
                    log(f"AISStream error: {msg['error']}")
                    continue

                parsed = extract_position_from_ais_message(msg)
                if not parsed:
                    continue

                mmsi = parsed["mmsi"]
                if mmsi not in mmsi_filters:
                    continue

                results[mmsi] = {
                    "lat": parsed["lat"],
                    "lon": parsed["lon"],
                    "timestamp": parsed["timestamp"],
                    "msg_type": parsed["msg_type"]
                }

                log(f"AISStream hit: MMSI {mmsi} via {parsed['msg_type']} @ {parsed['lat']}, {parsed['lon']}")

                if len(results) >= len(mmsi_filters):
                    break

            log(f"AISStream: received {received_messages} websocket message(s), matched {len(results)} vessel(s)")
            return results

    except Exception as e:
        log(f"AISStream failed: {e}")
        return {}


def build_false_flag_watch():
    features = []
    sample_vessels = [
        {
            "name": "EXAMPLE TANKER 1",
            "imo": "9000001",
            "mmsi": "273123456",
            "callsign": "UBCD1",
            "flag": "Sint Maarten",
            "claimed_flag": "Sint Maarten",
            "registry_state": "Sint Maarten",
            "registry_status": "fraud_notice",
            "ship_type": "Tanker",
            "owner": "",
            "manager": "",
            "risk_level": "B",
            "reason_code": "FRAUDULENT_REGISTRY_NOTICE",
            "reason_text": "Flag/genutztes Register gehört zu einer Jurisdiktion mit dokumentierten False-Flag- bzw. Fraud-Registry-Fällen.",
            "equasis_checked": False,
            "equasis_note": "",
            "gisis_checked": False,
            "historical_issue": True,
            "evidence_level": "reported",
            "source": "Manual watchlist",
            "source_url": "",
            "last_checked": NOW,
            "last_updated": NOW,
            "layer_type": "false_flag"
        }
    ]
    for i, vessel in enumerate(sample_vessels):
        features.append(feature(10.0 + i * 0.2, 54.0 + i * 0.2, vessel))
    return features


def build_russian_mmsi(position_map):
    watchlist = load_watchlist()
    features = []

    for row in watchlist:
        if not to_bool(row.get("track_russian_mmsi", "")):
            continue

        mmsi = (row.get("mmsi") or "").strip()
        if not mmsi.startswith(RUSSIAN_MID):
            continue

        pos = position_map.get(mmsi)
        if not pos:
            log(f"russian_mmsi: no live AIS position for {mmsi}")
            continue

        props = {
            "name": row.get("name", ""),
            "imo": row.get("imo", ""),
            "mmsi": mmsi,
            "callsign": row.get("callsign", ""),
            "flag": "",
            "source": "AISStream",
            "source_url": "https://aisstream.io/documentation",
            "last_seen": pos["timestamp"],
            "last_updated": NOW,
            "layer_type": "russian_mmsi",
            "mmsi_prefix": mmsi[:3],
            "mid_state": "Russian Federation",
            "mid_confidence": "high",
            "identity_note": "MMSI begins with 273, the MID allocated to the Russian Federation.",
            "ais_msg_type": pos.get("msg_type", "")
        }
        features.append(feature(float(pos["lon"]), float(pos["lat"]), props))

    log(f"russian_mmsi layer features: {len(features)}")
    return features


def build_sanctions_shadowfleet(position_map):
    watchlist = load_watchlist()
    features = []

    for row in watchlist:
        if not (to_bool(row.get("track_sanctions", "")) or to_bool(row.get("track_shadowfleet", ""))):
            continue

        mmsi = (row.get("mmsi") or "").strip()
        pos = position_map.get(mmsi)
        if not pos:
            log(f"sanctions_shadowfleet: no live AIS position for {mmsi or row.get('name','')}")
            continue

        props = {
            "name": row.get("name", ""),
            "imo": row.get("imo", ""),
            "mmsi": mmsi,
            "callsign": row.get("callsign", ""),
            "flag": "",
            "ship_type": "",
            "owner": "",
            
