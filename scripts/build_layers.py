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

# Nordsee bis Brest + südliche/mittlere Ostsee bis etwa Åland
# AISStream erwartet BoundingBoxes im Format:
# [[[lat1, lon1], [lat2, lon2]], ...]
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
    
