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
AISSTREAM_WINDOW_SECONDS = 90

BOUNDING_BOXES = [
    [[47.0, -5.5], [60.8, 25.5]]
]

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

    for key in ["id", "vesselId", "vessel_id"]:
        value = entry.get(key)
        if value:
            return value

    for group_key in ["selfReportedInfo", "combinedSourcesInfo", "registryInfo"]:
        group = entry.get(group_key)
        if isinstance(group, list):
            for item in group:
                if not isinstance(item, dict):
                    continue
                for key in ["id", "vesselId", "vessel_id"]:
                    value = item.get(key)
                    if value:
                        return value

    for nested_key in ["identity", "vessel", "profile"]:
        nested = entry.get(nested_key)
        if isinstance(nested, dict):
            for key in ["id", "vesselId", "vessel_id"]:
                value = nested.get(key)
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
        "BoundingBoxes": BOUNDING_BOXES,
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
            log(f"AISStream: using bounding boxes {BOUNDING_BOXES}")

            deadline = datetime.now(timezone.utc) + timedelta(seconds=AISSTREAM_WINDOW_SECONDS)
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
            "manager": "",
            "sanctioned": to_bool(row.get("track_sanctions", "")),
            "sanction_regime": "",
            "sanction_program": "",
            "listing_date": "",
            "shadowfleet_flag": to_bool(row.get("track_shadowfleet", "")),
            "shadow_reason": "Tracked via watchlist_master.csv",
            "risk_level": "high" if to_bool(row.get("track_sanctions", "")) else "medium",
            "notes": "",
            "source": "AISStream + watchlist",
            "source_url": "https://aisstream.io/documentation",
            "last_seen": pos["timestamp"],
            "last_updated": NOW,
            "layer_type": "sanctions_shadowfleet",
            "ais_msg_type": pos.get("msg_type", "")
        }
        features.append(feature(float(pos["lon"]), float(pos["lat"]), props))

    log(f"sanctions_shadowfleet layer features: {len(features)}")
    return features


def build_recent_russian_portcall():
    watchlist = load_watchlist()
    ru_ports = load_ru_ports()
    features = []

    for row in watchlist:
        name = row.get("name", "")
        result, matched_on = gfw_search_vessel(row)

        if not result:
            log(f"ru_portcall: no GFW vessel found for {name}")
            continue

        vessel_id = result.get("_resolved_vessel_id") or extract_gfw_vessel_id(result)
        if not vessel_id:
            log(f"ru_portcall: no vessel_id found for {name} (matched on {matched_on})")
            continue

        port_visits = gfw_get_port_visits(vessel_id)
        matched = False

        for event in port_visits:
            port_code = (
                event.get("portCode")
                or event.get("visit", {}).get("portCode")
                or ""
            )
            port_name = (
                event.get("portName")
                or event.get("visit", {}).get("portName")
                or ""
            )
            end_date = (
                event.get("end")
                or event.get("endDate")
                or event.get("timestamp")
                or ""
            )

            dt = parse_dt(end_date)
            if dt is None:
                continue

            days_since = (NOW_DT - dt).days
            if days_since < 0 or days_since > PORTCALL_WINDOW_DAYS:
                continue

            port_code_norm = str(port_code).strip().upper()
            is_ru = port_code_norm.startswith("RU ") or port_code_norm in ru_ports or "RUSSIA" in str(port_name).upper()
            if not is_ru:
                continue

            lat = None
            lon = None
            if isinstance(event.get("position"), dict):
                lat = event["position"].get("lat")
                lon = event["position"].get("lon")

            if lat is None or lon is None:
                log(f"ru_portcall: matched event but missing position for {name}")
                continue

            props = {
                "name": name,
                "imo": row.get("imo", ""),
                "mmsi": row.get("mmsi", ""),
                "callsign": row.get("callsign", ""),
                "flag": "",
                "ship_type": "",
                "owner": "",
                "manager": "",
                "last_ru_port": port_name,
                "last_ru_port_unlocode": port_code_norm,
                "last_ru_port_date": end_date,
                "days_since_ru_port": days_since,
                "ru_port_source": "Global Fishing Watch Events API",
                "source": "Global Fishing Watch",
                "source_url": "https://globalfishingwatch.org/our-apis/documentation",
                "last_updated": NOW,
                "layer_type": "ru_portcall_10d",
                "risk_level": "medium",
                "reason_text": f"Russian port call within the last {PORTCALL_WINDOW_DAYS} days.",
                "matched_on": matched_on,
                "gfw_vessel_id": vessel_id
            }
            features.append(feature(float(lon), float(lat), props))
            matched = True
            log(f"ru_portcall: matched {name} using {matched_on} at {port_name} ({port_code_norm})")
            break

        if not matched:
            log(f"ru_portcall: no recent Russian port call found for {name}")

    log(f"recent_russian_portcall layer features: {len(features)}")
    return features


def main():
    log("=== START ===")
    log(f"GFW token present: {'yes' if GFW_TOKEN else 'no'}")
    log(f"AISStream key present: {'yes' if AISSTREAM_API_KEY else 'no'}")
    log(f"AISStream window seconds: {AISSTREAM_WINDOW_SECONDS}")
    log(f"AISStream bounding boxes: {BOUNDING_BOXES}")

    load_flag_risk_reference()

    watchlist = load_watchlist()
    mmsis = [str(r.get("mmsi", "")).strip() for r in watchlist if str(r.get("mmsi", "")).strip()]
    log(f"watchlist MMSI count: {len(mmsis)}")

    position_map = asyncio.run(aisstream_positions_once(mmsis))
    log(f"AISStream matched positions: {len(position_map)}")

    false_flag_features = build_false_flag_watch()
    russian_mmsi_features = build_russian_mmsi(position_map)
    sanctions_features = build_sanctions_shadowfleet(position_map)
    recent_portcall_features = build_recent_russian_portcall()

    save_geojson(f"{DATA_DIR}/false_flag_watch.geojson", false_flag_features)
    save_geojson(f"{DATA_DIR}/russian_mmsi.geojson", russian_mmsi_features)
    save_geojson(f"{DATA_DIR}/sanctions_shadowfleet.geojson", sanctions_features)
    save_geojson(f"{DATA_DIR}/recent_russian_portcall_10d.geojson", recent_portcall_features)

    log("=== DONE ===")


if __name__ == "__main__":
    main()
