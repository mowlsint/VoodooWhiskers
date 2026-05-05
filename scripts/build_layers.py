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
AISSTREAM_WINDOW_SECONDS = 1800  # 30 Minuten

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
    if not GFW_TOKEN or not vessel_id:
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
        r = requests.get(url, headers=gfw_headers(), params=params, timeout=90)
        log(f"GFW port visits [{vessel_id}] -> HTTP {r.status_code}")
        if r.status_code != 200:
            return []

        data = r.json()
        entries = data.get("entries") or []
        log(f"GFW port visits [{vessel_id}] -> {len(entries)} event(s)")
        return entries
    except Exception as e:
        log(f"GFW port visits [{vessel_id}] failed: {e}")
        return []


def extract_position_payload(msg):
    metadata = msg.get("MetaData") or msg.get("Metadata") or {}
    body = msg.get("Message") or {}
    message_type = msg.get("MessageType") or ""

    candidate_keys = [
        "PositionReport",
        "StandardClassBPositionReport",
        "ExtendedClassBPositionReport",
    ]

    for key in candidate_keys:
        payload = body.get(key)
        if isinstance(payload, dict):
            lat = payload.get("Latitude")
            lon = payload.get("Longitude")
            mmsi = str(payload.get("UserID") or metadata.get("MMSI") or "").strip()
            name = metadata.get("ShipName") or payload.get("Name") or ""
            if lat is None or lon is None or not mmsi:
                return None
            return {
                "message_type": key,
                "mmsi": mmsi,
                "lat": lat,
                "lon": lon,
                "name": name,
                "metadata": metadata,
                "payload": payload,
            }

    return None


def build_watchlist_indexes(rows):
    by_mmsi = {}
    by_imo = {}
    by_callsign = {}
    by_name = {}

    for row in rows:
        mmsi = (row.get("mmsi") or "").strip()
        imo = (row.get("imo") or "").strip()
        callsign = (row.get("callsign") or "").strip().upper()
        name = (row.get("name") or "").strip().upper()

        if mmsi:
            by_mmsi[mmsi] = row
        if imo:
            by_imo[imo] = row
        if callsign:
            by_callsign[callsign] = row
        if name:
            by_name[name] = row

    return {
        "mmsi": by_mmsi,
        "imo": by_imo,
        "callsign": by_callsign,
        "name": by_name,
    }


def classify_live_contact(pos, watch_indexes):
    mmsi = pos["mmsi"]
    name = (pos.get("name") or "").strip().upper()
    metadata = pos.get("metadata") or {}

    matched_row = None
    categories = set()

    if mmsi.startswith(RUSSIAN_MID):
        categories.add("russian_mmsi")

    if mmsi in watch_indexes["mmsi"]:
        matched_row = watch_indexes["mmsi"][mmsi]
        categories.add("watchlist")

    meta_shipname = (metadata.get("ShipName") or "").strip().upper()
    if not matched_row and name and name in watch_indexes["name"]:
        matched_row = watch_indexes["name"][name]
        categories.add("watchlist")
    if not matched_row and meta_shipname and meta_shipname in watch_indexes["name"]:
        matched_row = watch_indexes["name"][meta_shipname]
        categories.add("watchlist")

    if matched_row:
        if to_bool(matched_row.get("sanctioned")) or to_bool(matched_row.get("shadow_fleet")):
            categories.add("sanctions_shadowfleet")

    return matched_row, categories


async def collect_filtered_ais_positions(watch_indexes):
    if not AISSTREAM_API_KEY:
        log("AISSTREAM_API_KEY missing")
        return {}

    subscription = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": BOUNDING_BOXES,
        "FilterMessageTypes": [
            "PositionReport",
            "StandardClassBPositionReport",
            "ExtendedClassBPositionReport"
        ]
    }

    deadline = datetime.now(timezone.utc) + timedelta(seconds=AISSTREAM_WINDOW_SECONDS)
    collected = {}

    total_messages = 0
    parsed_positions = 0
    matched_positions = 0
    last_status = datetime.now(timezone.utc)

    log(f"AISStream: connecting for {AISSTREAM_WINDOW_SECONDS} seconds")
    log(f"AISStream: using bounding boxes {BOUNDING_BOXES}")

    async with websockets.connect(AISSTREAM_URL, open_timeout=20, close_timeout=10, max_size=None) as ws:
        await ws.send(json.dumps(subscription))
        log("AISStream: subscription sent")

        while datetime.now(timezone.utc) < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                log(
                    f"AISStream: heartbeat total_messages={total_messages} "
                    f"parsed_positions={parsed_positions} matched_positions={matched_positions}"
                )
                continue
            except Exception as e:
                log(f"AISStream: websocket receive failed: {e}")
                break

            total_messages += 1

            try:
                msg = json.loads(raw)
            except Exception:
                continue

            if isinstance(msg, dict) and msg.get("error"):
                log(f"AISStream error: {msg.get('error')}")
                continue

            pos = extract_position_payload(msg)
            if not pos:
                continue

            parsed_positions += 1
            matched_row, categories = classify_live_contact(pos, watch_indexes)

            if not categories:
                continue

            matched_positions += 1
            mmsi = pos["mmsi"]

            record = {
                "mmsi": mmsi,
                "name": pos.get("name") or "",
                "lat": pos["lat"],
                "lon": pos["lon"],
                "message_type": pos["message_type"],
                "last_seen_utc": NOW,
                "categories": sorted(categories),
                "matched_watchlist": bool(matched_row),
                "watch_name": (matched_row.get("name") if matched_row else ""),
                "watch_imo": (matched_row.get("imo") if matched_row else ""),
                "watch_callsign": (matched_row.get("callsign") if matched_row else ""),
                "sanctioned": to_bool(matched_row.get("sanctioned")) if matched_row else False,
                "shadow_fleet": to_bool(matched_row.get("shadow_fleet")) if matched_row else False,
                "notes": (matched_row.get("notes") if matched_row else ""),
            }

            collected[mmsi] = record

            now_dt = datetime.now(timezone.utc)
            if (now_dt - last_status).total_seconds() >= 60:
                log(
                    f"AISStream: status total_messages={total_messages} "
                    f"parsed_positions={parsed_positions} matched_positions={matched_positions} "
                    f"unique_matched_mmsi={len(collected)}"
                )
                last_status = now_dt

    log(
        f"AISStream: done total_messages={total_messages} "
        f"parsed_positions={parsed_positions} matched_positions={matched_positions} "
        f"unique_matched_mmsi={len(collected)}"
    )
    return collected


def build_false_flag_watch(flag_rows):
    features = []
    for row in flag_rows:
        name = (row.get("flag") or "").strip()
        risk = (row.get("risk_level") or "").strip()
        if not name:
            continue
        if risk.lower() in {"high", "very high"}:
            features.append(
                feature(
                    0.0,
                    0.0,
                    {
                        "flag": name,
                        "risk_level": risk,
                        "source": "flag_risk_reference",
                        "generated_at": NOW,
                    },
                )
            )
            break

    if not features:
        features.append(
            feature(
                0.0,
                0.0,
                {
                    "flag": "example",
                    "risk_level": "reference",
                    "source": "flag_risk_reference",
                    "generated_at": NOW,
                },
            )
        )

    return features


def build_live_category_layers(collected):
    russian_features = []
    watchlist_features = []
    sanctions_features = []

    for mmsi, rec in sorted(collected.items()):
        props = {
            "mmsi": rec["mmsi"],
            "name": rec["name"],
            "message_type": rec["message_type"],
            "last_seen_utc": rec["last_seen_utc"],
            "matched_watchlist": rec["matched_watchlist"],
            "watch_name": rec["watch_name"],
            "watch_imo": rec["watch_imo"],
            "watch_callsign": rec["watch_callsign"],
            "sanctioned": rec["sanctioned"],
            "shadow_fleet": rec["shadow_fleet"],
            "notes": rec["notes"],
            "categories": rec["categories"],
            "source": "AISStream",
        }

        feat = feature(rec["lon"], rec["lat"], props)

        if "russian_mmsi" in rec["categories"]:
            russian_features.append(feat)
        if "watchlist" in rec["categories"]:
            watchlist_features.append(feat)
        if "sanctions_shadowfleet" in rec["categories"]:
            sanctions_features.append(feat)

    log(f"russian_mmsi layer features: {len(russian_features)}")
    log(f"watchlist_live layer features: {len(watchlist_features)}")
    log(f"sanctions_shadowfleet layer features: {len(sanctions_features)}")

    return russian_features, watchlist_features, sanctions_features


def build_recent_russian_portcall(rows, ru_ports):
    features = []

    for row in rows:
        matched, matched_on = gfw_search_vessel(row)
        if not matched:
            log(f"ru_portcall: no GFW match for {(row.get('name') or '').strip()}")
            continue

        vessel_id = matched.get("_resolved_vessel_id")
        if not vessel_id:
            log(
                f"ru_portcall: no vessel_id found for {(row.get('name') or '').strip()} "
                f"(matched on {matched_on})"
            )
            continue

        events = gfw_get_port_visits(vessel_id)
        if not events:
            continue

        for ev in events:
            port_code = (
                (ev.get("port") or {}).get("unlocode")
                or (ev.get("portVisit") or {}).get("unlocode")
                or (ev.get("port_visit") or {}).get("unlocode")
                or ""
            ).strip().upper()

            if not port_code or port_code not in ru_ports:
                continue

            pos = ev.get("position") or {}
            lat = pos.get("lat")
            lon = pos.get("lon")
            if lat is None or lon is None:
                continue

            features.append(
                feature(
                    lon,
                    lat,
                    {
                        "name": row.get("name"),
                        "mmsi": row.get("mmsi"),
                        "imo": row.get("imo"),
                        "callsign": row.get("callsign"),
                        "matched_on": matched_on,
                        "gfw_vessel_id": vessel_id,
                        "event_id": ev.get("id"),
                        "event_type": ev.get("type"),
                        "event_start": ev.get("start"),
                        "event_end": ev.get("end"),
                        "ru_port_unlocode": port_code,
                        "ru_port_name": ru_ports[port_code].get("port_name"),
                        "generated_at": NOW,
                        "source": "Global Fishing Watch Events API",
                    },
                )
            )

    log(f"recent_russian_portcall layer features: {len(features)}")
    return features


async def async_main():
    log("=== START ===")
    log(f"GFW token present: {'yes' if bool(GFW_TOKEN) else 'no'}")
    log(f"AISStream key present: {'yes' if bool(AISSTREAM_API_KEY) else 'no'}")
    log(f"AISStream window seconds: {AISSTREAM_WINDOW_SECONDS}")
    log(f"AISStream bounding boxes: {BOUNDING_BOXES}")

    flag_rows = load_flag_risk_reference()
    watch_rows = load_watchlist()
    ru_ports = load_ru_ports()

    false_flag_features = build_false_flag_watch(flag_rows)
    watch_indexes = build_watchlist_indexes(watch_rows)

    collected = await collect_filtered_ais_positions(watch_indexes)
    russian_features, watchlist_live_features, sanctions_features = build_live_category_layers(collected)
    recent_ru_portcalls = build_recent_russian_portcall(watch_rows, ru_ports)

    save_geojson(f"{DATA_DIR}/false_flag_watch.geojson", false_flag_features)
    save_geojson(f"{DATA_DIR}/russian_mmsi.geojson", russian_features)
    save_geojson(f"{DATA_DIR}/watchlist_live.geojson", watchlist_live_features)
    save_geojson(f"{DATA_DIR}/sanctions_shadowfleet.geojson", sanctions_features)
    save_geojson(f"{DATA_DIR}/recent_russian_portcall_10d.geojson", recent_ru_portcalls)

    log("=== DONE ===")


if __name__ == "__main__":
    asyncio.run(async_main())
