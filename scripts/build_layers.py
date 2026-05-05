import asyncio
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
import websockets


DATA_DIR = "data"

FALSE_FLAG_GEOJSON = f"{DATA_DIR}/false_flag_watch.geojson"
RUSSIAN_GEOJSON = f"{DATA_DIR}/russian_mmsi.geojson"
WATCHLIST_GEOJSON = f"{DATA_DIR}/watchlist_live.geojson"
SANCTIONS_GEOJSON = f"{DATA_DIR}/sanctions_shadowfleet.geojson"

SNAPSHOT_JSON = f"{DATA_DIR}/voi_snapshot_latest.json"
HISTORY_JSONL = f"{DATA_DIR}/voi_history.jsonl"
STATS_JSON = f"{DATA_DIR}/voi_stats_by_slot.json"

FLAG_RISK_CSV = f"{DATA_DIR}/flag_risk_reference.csv"
WATCHLIST_CSV = f"{DATA_DIR}/watchlist_master.csv"

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
AISSTREAM_API_KEY = os.getenv("AISSTREAM_API_KEY", "").strip()
AISSTREAM_WINDOW_SECONDS = 1800  # 30 minutes
RUSSIAN_MID = "273"

GFW_TOKEN = os.getenv("GFW_TOKEN", "").strip()
GFW_API = "https://gateway.api.globalfishingwatch.org/v3"
GFW_LOOKBACK_DAYS = 30

# Mehrere schmale See-Boxen statt einer großen Gesamtbox.
# Das nähert deine maritime AOI besser an als eine einzige breite Box.
BOUNDING_BOXES = [
    [[48.2, -6.2], [50.7, 1.8]],    # western Channel
    [[50.5, -4.8], [53.6, 3.8]],    # southern North Sea
    [[53.2, -2.8], [56.8, 5.5]],    # central North Sea
    [[56.0, -2.5], [61.8, 2.5]],    # northern North Sea incl. Shetland
    [[56.5, 7.0], [58.9, 13.6]],    # Skagerrak / Kattegat
    [[54.2, 8.0], [56.6, 15.8]],    # Danish straits / western Baltic approaches
    [[54.0, 12.0], [58.8, 21.0]],   # central Baltic
    [[58.2, 19.5], [60.8, 30.8]],   # Gulf of Finland
]


def log(msg):
    print(f"[build_layers] {msg}")


def utcnow():
    return datetime.now(timezone.utc)


def iso_z(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_parent(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def load_csv(path):
    rows = []
    if not os.path.exists(path):
        log(f"missing csv: {path}")
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    log(f"loaded {len(rows)} rows from {path}")
    return rows


def save_json(path, obj):
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def save_geojson(path, features):
    save_json(path, {"type": "FeatureCollection", "features": features})
    log(f"saved {path} with {len(features)} features")


def feature(lon, lat, props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def to_bool(v):
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


def clean_str(v):
    return str(v).strip() if v is not None else ""


def norm_upper(v):
    return clean_str(v).upper()


def next_standard_slot_end(start_dt):
    candidates = []
    base = start_dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for delta_day in [0, 1]:
        d = (base + timedelta(days=delta_day)).date()
        for h in [0, 6, 12, 18]:
            candidates.append(datetime(d.year, d.month, d.day, h, 0, 0, tzinfo=timezone.utc))
    for c in candidates:
        if c > start_dt:
            return c
    return candidates[-1]


def build_false_flag_watch(flag_rows, generated_at):
    feats = []
    for row in flag_rows:
        risk = clean_str(row.get("risk_level"))
        flag = clean_str(row.get("flag"))
        if risk.lower() in {"high", "very high"} and flag:
            feats.append(
                feature(
                    0.0,
                    0.0,
                    {
                        "flag": flag,
                        "risk_level": risk,
                        "source": "flag_risk_reference",
                        "generated_at": generated_at,
                    },
                )
            )
            break

    if not feats:
        feats.append(
            feature(
                0.0,
                0.0,
                {
                    "flag": "reference",
                    "risk_level": "reference",
                    "source": "flag_risk_reference",
                    "generated_at": generated_at,
                },
            )
        )
    return feats


def build_watchlist_indexes(rows):
    idx = {
        "mmsi": {},
        "imo": {},
        "callsign": {},
        "name": {},
    }
    for row in rows:
        mmsi = clean_str(row.get("mmsi"))
        imo = clean_str(row.get("imo"))
        callsign = norm_upper(row.get("callsign"))
        name = norm_upper(row.get("name"))

        if mmsi:
            idx["mmsi"][mmsi] = row
        if imo:
            idx["imo"][imo] = row
        if callsign:
            idx["callsign"][callsign] = row
        if name:
            idx["name"][name] = row
    return idx


def update_static_state(msg, vessel_states):
    metadata = msg.get("MetaData") or msg.get("Metadata") or {}
    body = msg.get("Message") or {}

    def get_state(mmsi):
        if mmsi not in vessel_states:
            vessel_states[mmsi] = {
                "mmsi": mmsi,
                "name": "",
                "callsign": "",
                "imo": "",
            }
        return vessel_states[mmsi]

    if isinstance(body.get("ShipStaticData"), dict):
        p = body["ShipStaticData"]
        mmsi = clean_str(p.get("UserID") or metadata.get("MMSI"))
        if not mmsi:
            return
        state = get_state(mmsi)
        if clean_str(p.get("Name")):
            state["name"] = clean_str(p.get("Name"))
        elif clean_str(metadata.get("ShipName")):
            state["name"] = clean_str(metadata.get("ShipName"))
        if clean_str(p.get("CallSign")):
            state["callsign"] = clean_str(p.get("CallSign"))
        imo = clean_str(p.get("ImoNumber"))
        if imo and imo != "0":
            state["imo"] = imo

    if isinstance(body.get("StaticDataReport"), dict):
        p = body["StaticDataReport"]
        mmsi = clean_str(p.get("UserID") or metadata.get("MMSI"))
        if not mmsi:
            return
        state = get_state(mmsi)
        report_a = p.get("ReportA") or {}
        report_b = p.get("ReportB") or {}
        if clean_str(report_a.get("Name")):
            state["name"] = clean_str(report_a.get("Name"))
        elif clean_str(metadata.get("ShipName")):
            state["name"] = clean_str(metadata.get("ShipName"))
        if clean_str(report_b.get("CallSign")):
            state["callsign"] = clean_str(report_b.get("CallSign"))


def extract_position(msg):
    metadata = msg.get("MetaData") or msg.get("Metadata") or {}
    body = msg.get("Message") or {}

    for key in ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"]:
        p = body.get(key)
        if not isinstance(p, dict):
            continue

        mmsi = clean_str(p.get("UserID") or metadata.get("MMSI"))
        lat = p.get("Latitude")
        lon = p.get("Longitude")
        if not mmsi or lat is None or lon is None:
            return None

        return {
            "message_type": key,
            "mmsi": mmsi,
            "lat": lat,
            "lon": lon,
            "sog": p.get("Sog"),
            "cog": p.get("Cog"),
            "heading": p.get("TrueHeading"),
            "nav_status": p.get("NavigationalStatus"),
            "shipname_meta": clean_str(metadata.get("ShipName")),
            "time_utc": clean_str(metadata.get("time_utc")),
        }
    return None


def classify_contact(record, watch_idx):
    categories = set()
    matched = None

    mmsi = clean_str(record.get("mmsi"))
    imo = clean_str(record.get("imo"))
    callsign = norm_upper(record.get("callsign"))
    name = norm_upper(record.get("name"))

    if mmsi.startswith(RUSSIAN_MID):
        categories.add("russian_mmsi")

    if mmsi and mmsi in watch_idx["mmsi"]:
        matched = watch_idx["mmsi"][mmsi]
    elif imo and imo in watch_idx["imo"]:
        matched = watch_idx["imo"][imo]
    elif callsign and callsign in watch_idx["callsign"]:
        matched = watch_idx["callsign"][callsign]
    elif name and name in watch_idx["name"]:
        matched = watch_idx["name"][name]

    if matched:
        categories.add("watchlist")
        if to_bool(matched.get("sanctioned")) or to_bool(matched.get("shadow_fleet")):
            categories.add("sanctions_shadowfleet")

    return matched, sorted(categories)


def gfw_headers():
    return {"Authorization": f"Bearer {GFW_TOKEN}"} if GFW_TOKEN else {}


def extract_gfw_vessel_id(entry):
    if not isinstance(entry, dict):
        return None

    for key in ["id", "vesselId", "vessel_id"]:
        if clean_str(entry.get(key)):
            return clean_str(entry.get(key))

    for bucket in ["combinedSourcesInfo", "selfReportedInfo", "registryInfo"]:
        items = entry.get(bucket)
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ["id", "vesselId", "vessel_id"]:
                    if clean_str(item.get(key)):
                        return clean_str(item.get(key))
    return None


def gfw_search_vessel_id(rec):
    if not GFW_TOKEN:
        return None, None

    queries = [
        clean_str(rec.get("watch_imo")),
        clean_str(rec.get("mmsi")),
        clean_str(rec.get("watch_callsign")),
        clean_str(rec.get("watch_name")),
        clean_str(rec.get("name")),
    ]
    seen = set()

    for query in queries:
        if not query or query in seen:
            continue
        seen.add(query)

        try:
            r = requests.get(
                f"{GFW_API}/vessels/search",
                headers=gfw_headers(),
                params={
                    "query": query,
                    "datasets[0]": "public-global-vessel-identity:latest",
                },
                timeout=60,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            entries = data.get("entries") or []
            if not entries:
                continue
            vessel_id = extract_gfw_vessel_id(entries[0])
            if vessel_id:
                return vessel_id, query
        except Exception:
            continue

    return None, None


def gfw_recent_events(vessel_id, lookback_days=30):
    if not GFW_TOKEN or not vessel_id:
        return []

    start_date = (utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end_date = utcnow().strftime("%Y-%m-%d")

    try:
        r = requests.get(
            f"{GFW_API}/events",
            headers=gfw_headers(),
            params={
                "vessels[0]": vessel_id,
                "datasets[0]": "public-global-fishing-events:latest",
                "start-date": start_date,
                "end-date": end_date,
                "limit": 200,
                "offset": 0,
            },
            timeout=90,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("entries") or []
    except Exception:
        return []


def enrich_with_gfw(collected):
    for mmsi, rec in collected.items():
        rec["gfw_vessel_id"] = ""
        rec["gfw_match_query"] = ""
        rec["gfw_loitering_30d"] = 0
        rec["gfw_port_visits_30d"] = 0
        rec["gfw_event_types_30d"] = []

        if not rec.get("matched_watchlist"):
            continue

        vessel_id, matched_on = gfw_search_vessel_id(rec)
        if not vessel_id:
            continue

        rec["gfw_vessel_id"] = vessel_id
        rec["gfw_match_query"] = matched_on

        events = gfw_recent_events(vessel_id, lookback_days=GFW_LOOKBACK_DAYS)
        event_types = []
        loitering = 0
        port_visits = 0

        for ev in events:
            ev_type = clean_str(ev.get("type")).lower()
            if not ev_type:
                continue
            event_types.append(ev_type)
            if "loiter" in ev_type:
                loitering += 1
            if "port" in ev_type:
                port_visits += 1

        rec["gfw_loitering_30d"] = loitering
        rec["gfw_port_visits_30d"] = port_visits
        rec["gfw_event_types_30d"] = sorted(set(event_types))


async def collect_filtered_contacts(slot_start_utc, slot_end_utc, watch_idx):
    if not AISSTREAM_API_KEY:
        raise RuntimeError("AISSTREAM_API_KEY missing")

    subscription = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": BOUNDING_BOXES,
        "FilterMessageTypes": [
            "PositionReport",
            "StandardClassBPositionReport",
            "ExtendedClassBPositionReport",
            "ShipStaticData",
            "StaticDataReport",
        ],
    }

    vessel_states = {}
    collected = {}

    total_messages = 0
    position_messages = 0
    matched_positions = 0
    deadline = utcnow() + timedelta(seconds=AISSTREAM_WINDOW_SECONDS)
    last_status = utcnow()

    log(f"AISStream window seconds: {AISSTREAM_WINDOW_SECONDS}")
    log(f"AISStream bounding boxes: {BOUNDING_BOXES}")

    async with websockets.connect(AISSTREAM_URL, open_timeout=20, close_timeout=10, max_size=None) as ws:
        await ws.send(json.dumps(subscription))
        log("AISStream subscription sent")

        while utcnow() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                now = utcnow()
                if (now - last_status).total_seconds() >= 60:
                    log(
                        f"AISStream status total_messages={total_messages} "
                        f"position_messages={position_messages} matched_positions={matched_positions} "
                        f"unique_matched_mmsi={len(collected)}"
                    )
                    last_status = now
                continue

            total_messages += 1

            try:
                msg = json.loads(raw)
            except Exception:
                continue

            if isinstance(msg, dict) and msg.get("error"):
                log(f"AISStream error: {msg.get('error')}")
                continue

            update_static_state(msg, vessel_states)

            pos = extract_position(msg)
            if not pos:
                continue

            position_messages += 1

            mmsi = pos["mmsi"]
            state = vessel_states.get(mmsi, {"mmsi": mmsi, "name": "", "callsign": "", "imo": ""})

            merged = {
                "mmsi": mmsi,
                "name": clean_str(state.get("name")) or clean_str(pos.get("shipname_meta")),
                "callsign": clean_str(state.get("callsign")),
                "imo": clean_str(state.get("imo")),
                "lat": pos["lat"],
                "lon": pos["lon"],
                "sog": pos.get("sog"),
                "cog": pos.get("cog"),
                "heading": pos.get("heading"),
                "nav_status": pos.get("nav_status"),
                "message_type": pos["message_type"],
                "last_seen_utc": clean_str(pos.get("time_utc")) or iso_z(utcnow()),
                "collection_start_utc": slot_start_utc,
                "collection_end_utc": slot_end_utc,
                "slot_end_utc": slot_end_utc,
            }

            matched_row, categories = classify_contact(merged, watch_idx)
            if not categories:
                continue

            matched_positions += 1

            merged["categories"] = categories
            merged["matched_watchlist"] = bool(matched_row)
            merged["watch_name"] = clean_str(matched_row.get("name")) if matched_row else ""
            merged["watch_imo"] = clean_str(matched_row.get("imo")) if matched_row else ""
            merged["watch_callsign"] = clean_str(matched_row.get("callsign")) if matched_row else ""
            merged["sanctioned"] = to_bool(matched_row.get("sanctioned")) if matched_row else False
            merged["shadow_fleet"] = to_bool(matched_row.get("shadow_fleet")) if matched_row else False
            merged["notes"] = clean_str(matched_row.get("notes")) if matched_row else ""

            collected[mmsi] = merged

            now = utcnow()
            if (now - last_status).total_seconds() >= 60:
                log(
                    f"AISStream status total_messages={total_messages} "
                    f"position_messages={position_messages} matched_positions={matched_positions} "
                    f"unique_matched_mmsi={len(collected)}"
                )
                last_status = now

    log(
        f"AISStream done total_messages={total_messages} "
        f"position_messages={position_messages} matched_positions={matched_positions} "
        f"unique_matched_mmsi={len(collected)}"
    )

    return collected


def build_layers(collected):
    russian_features = []
    watchlist_features = []
    sanctions_features = []

    for rec in sorted(collected.values(), key=lambda x: (x["mmsi"], x["name"])):
        props = {
            "mmsi": rec["mmsi"],
            "name": rec["name"],
            "callsign": rec["callsign"],
            "imo": rec["imo"],
            "message_type": rec["message_type"],
            "last_seen_utc": rec["last_seen_utc"],
            "slot_end_utc": rec["slot_end_utc"],
            "sog": rec["sog"],
            "cog": rec["cog"],
            "heading": rec["heading"],
            "matched_watchlist": rec["matched_watchlist"],
            "watch_name": rec["watch_name"],
            "watch_imo": rec["watch_imo"],
            "watch_callsign": rec["watch_callsign"],
            "sanctioned": rec["sanctioned"],
            "shadow_fleet": rec["shadow_fleet"],
            "notes": rec["notes"],
            "gfw_vessel_id": rec.get("gfw_vessel_id", ""),
            "gfw_loitering_30d": rec.get("gfw_loitering_30d", 0),
            "gfw_port_visits_30d": rec.get("gfw_port_visits_30d", 0),
            "gfw_event_types_30d": rec.get("gfw_event_types_30d", []),
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

    return russian_features, watchlist_features, sanctions_features


def build_snapshot(collected, generated_at, slot_start_utc, slot_end_utc):
    vessels = sorted(collected.values(), key=lambda x: (x["mmsi"], x["name"]))
    summary = {
        "unique_matched_mmsi": len(vessels),
        "russian_mmsi": sum(1 for v in vessels if "russian_mmsi" in v["categories"]),
        "watchlist": sum(1 for v in vessels if "watchlist" in v["categories"]),
        "sanctions_shadowfleet": sum(1 for v in vessels if "sanctions_shadowfleet" in v["categories"]),
    }

    return {
        "generated_at": generated_at,
        "collection_start_utc": slot_start_utc,
        "collection_end_utc": slot_end_utc,
        "slot_end_utc": slot_end_utc,
        "bounding_boxes": BOUNDING_BOXES,
        "summary": summary,
        "vessels": vessels,
    }


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def append_history_unique(collected):
    existing = read_jsonl(HISTORY_JSONL)
    existing_keys = {
        f"{row.get('slot_end_utc')}|{row.get('category')}|{row.get('mmsi')}"
        for row in existing
    }

    appended = 0
    ensure_parent(HISTORY_JSONL)
    with open(HISTORY_JSONL, "a", encoding="utf-8") as f:
        for rec in sorted(collected.values(), key=lambda x: (x["mmsi"], x["name"])):
            for category in rec["categories"]:
                item = {
                    "run_ts_utc": iso_z(utcnow()),
                    "slot_end_utc": rec["slot_end_utc"],
                    "collection_start_utc": rec["collection_start_utc"],
                    "collection_end_utc": rec["collection_end_utc"],
                    "category": category,
                    "mmsi": rec["mmsi"],
                    "name": rec["name"],
                    "imo": rec["imo"],
                    "callsign": rec["callsign"],
                    "lat": rec["lat"],
                    "lon": rec["lon"],
                    "matched_watchlist": rec["matched_watchlist"],
                    "sanctioned": rec["sanctioned"],
                    "shadow_fleet": rec["shadow_fleet"],
                    "gfw_vessel_id": rec.get("gfw_vessel_id", ""),
                    "gfw_loitering_30d": rec.get("gfw_loitering_30d", 0),
                    "gfw_port_visits_30d": rec.get("gfw_port_visits_30d", 0),
                }
                key = f"{item['slot_end_utc']}|{item['category']}|{item['mmsi']}"
                if key in existing_keys:
                    continue
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                existing_keys.add(key)
                appended += 1

    log(f"history appended rows: {appended}")


def build_stats():
    rows = read_jsonl(HISTORY_JSONL)

    by_slot = defaultdict(lambda: {"categories": defaultdict(set), "all_mmsi": set()})
    totals_by_category = defaultdict(set)

    for row in rows:
        slot = clean_str(row.get("slot_end_utc"))
        cat = clean_str(row.get("category"))
        mmsi = clean_str(row.get("mmsi"))
        if not slot or not cat or not mmsi:
            continue
        by_slot[slot]["categories"][cat].add(mmsi)
        by_slot[slot]["all_mmsi"].add(mmsi)
        totals_by_category[cat].add(mmsi)

    slots = []
    for slot in sorted(by_slot.keys()):
        counts = {
            cat: len(sorted(mmsis))
            for cat, mmsis in sorted(by_slot[slot]["categories"].items())
        }
        slots.append(
            {
                "slot_end_utc": slot,
                "unique_mmsi_total": len(by_slot[slot]["all_mmsi"]),
                "counts": counts,
            }
        )

    obj = {
        "generated_at": iso_z(utcnow()),
        "totals_by_category_unique_mmsi": {
            cat: len(mmsis) for cat, mmsis in sorted(totals_by_category.items())
        },
        "slots": slots,
    }
    save_json(STATS_JSON, obj)


async def async_main():
    generated_at = iso_z(utcnow())
    run_started = utcnow()
    slot_end_dt = next_standard_slot_end(run_started)
    slot_start_dt = slot_end_dt - timedelta(minutes=30)

    slot_start_utc = iso_z(slot_start_dt)
    slot_end_utc = iso_z(slot_end_dt)

    log("=== START ===")
    log(f"GFW token present: {'yes' if GFW_TOKEN else 'no'}")
    log(f"AISStream key present: {'yes' if AISSTREAM_API_KEY else 'no'}")
    log(f"slot_start_utc: {slot_start_utc}")
    log(f"slot_end_utc: {slot_end_utc}")

    flag_rows = load_csv(FLAG_RISK_CSV)
    watch_rows = load_csv(WATCHLIST_CSV)
    watch_idx = build_watchlist_indexes(watch_rows)

    false_flag_features = build_false_flag_watch(flag_rows, generated_at)

    collected = await collect_filtered_contacts(slot_start_utc, slot_end_utc, watch_idx)
    enrich_with_gfw(collected)

    russian_features, watchlist_features, sanctions_features = build_layers(collected)
    snapshot = build_snapshot(collected, generated_at, slot_start_utc, slot_end_utc)

    save_geojson(FALSE_FLAG_GEOJSON, false_flag_features)
    save_geojson(RUSSIAN_GEOJSON, russian_features)
    save_geojson(WATCHLIST_GEOJSON, watchlist_features)
    save_geojson(SANCTIONS_GEOJSON, sanctions_features)
    save_json(SNAPSHOT_JSON, snapshot)

    append_history_unique(collected)
    build_stats()

    log(f"russian_mmsi layer features: {len(russian_features)}")
    log(f"watchlist_live layer features: {len(watchlist_features)}")
    log(f"sanctions_shadowfleet layer features: {len(sanctions_features)}")
    log("=== DONE ===")


if __name__ == "__main__":
    asyncio.run(async_main())
