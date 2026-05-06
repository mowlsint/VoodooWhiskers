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
OUTPUT_PATH = DATA_DIR / "ais_contacts_latest.json"

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"

MESSAGE_TYPES = [
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
    "StaticDataReport",
]

FALLBACK_BOUNDING_BOXES = [
    [[53.0, 3.0], [60.8, 30.5]],   # North Sea + Baltic + Danish Straits
    [[41.0, 26.0], [47.5, 42.5]],  # Black Sea
]

def clean_str(v):
    return "" if v is None else str(v).strip()

def norm_text(v):
    s = clean_str(v).upper()
    s = re.sub(r"\s+", " ", s)
    return s

def digits(v):
    return re.sub(r"\D", "", clean_str(v))

def load_watchlist_mmsi():
    if not WATCHLIST_PATH.exists():
        return []
    with open(WATCHLIST_PATH, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    vals = []
    for row in rows:
        m = digits(row.get("mmsi"))
        if m:
            vals.append(m)
    return sorted(set(vals))

def merge_contact(dst, src):
    for k, v in src.items():
        if v in ("", None, [], {}):
            continue
        dst[k] = v

def contact_key(d):
    return (
        digits(d.get("mmsi"))
        or digits(d.get("imo"))
        or norm_text(d.get("callsign"))
        or norm_text(d.get("name"))
    )

def extract_contact(msg):
    md = msg.get("MetaData") or msg.get("Metadata") or {}
    mt = msg.get("MessageType", "")
    body = (msg.get("Message") or {}).get(mt, {}) if mt else {}

    out = {
        "mmsi": "",
        "imo": "",
        "callsign": "",
        "name": "",
        "latitude": "",
        "longitude": "",
        "destination": "",
        "ship_type": "",
        "navigational_status": "",
        "sog": "",
        "cog": "",
        "true_heading": "",
        "source": "AISStream",
        "message_type_last": mt,
        "last_seen_utc": md.get("time_utc") or datetime.now(timezone.utc).isoformat(),
    }

    if md:
        if md.get("MMSI") is not None:
            out["mmsi"] = str(md.get("MMSI"))
        if md.get("ShipName"):
            out["name"] = clean_str(md.get("ShipName"))
        if md.get("latitude") is not None:
            out["latitude"] = md.get("latitude")
        if md.get("longitude") is not None:
            out["longitude"] = md.get("longitude")
        if md.get("Latitude") is not None and out["latitude"] == "":
            out["latitude"] = md.get("Latitude")
        if md.get("Longitude") is not None and out["longitude"] == "":
            out["longitude"] = md.get("Longitude")

    if mt in {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}:
        if body.get("UserID") is not None:
            out["mmsi"] = str(body.get("UserID"))
        if body.get("Latitude") is not None:
            out["latitude"] = body.get("Latitude")
        if body.get("Longitude") is not None:
            out["longitude"] = body.get("Longitude")
        out["navigational_status"] = body.get("NavigationalStatus", "")
        out["sog"] = body.get("Sog", "")
        out["cog"] = body.get("Cog", "")
        out["true_heading"] = body.get("TrueHeading", "")
        if body.get("Name") and not out["name"]:
            out["name"] = clean_str(body.get("Name"))

    elif mt == "ShipStaticData":
        if body.get("UserID") is not None:
            out["mmsi"] = str(body.get("UserID"))
        if body.get("ImoNumber") is not None:
            out["imo"] = str(body.get("ImoNumber"))
        if body.get("CallSign"):
            out["callsign"] = clean_str(body.get("CallSign"))
        if body.get("Name"):
            out["name"] = clean_str(body.get("Name"))
        if body.get("Destination"):
            out["destination"] = clean_str(body.get("Destination"))
        if body.get("Type") is not None:
            out["ship_type"] = body.get("Type")

    elif mt == "StaticDataReport":
        if body.get("UserID") is not None:
            out["mmsi"] = str(body.get("UserID"))
        report_a = body.get("ReportA") or {}
        report_b = body.get("ReportB") or {}
        if report_a.get("Name"):
            out["name"] = clean_str(report_a.get("Name"))
        if report_b.get("CallSign"):
            out["callsign"] = clean_str(report_b.get("CallSign"))
        if report_b.get("ShipType") is not None:
            out["ship_type"] = report_b.get("ShipType")

    return out

async def collect_once(subscription, duration_seconds):
    contacts = {}
    async with websockets.connect(
        AISSTREAM_URL,
        ping_interval=20,
        ping_timeout=20,
        max_size=2**22,
        close_timeout=5,
    ) as ws:
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

    watch_mmsi = load_watchlist_mmsi()
    all_contacts = {}

    if watch_mmsi:
        for i in range(0, len(watch_mmsi), 50):
            chunk = watch_mmsi[i:i+50]
            subscription = {
                "APIKey": api_key,
                "BoundingBoxes": [[[-90, -180], [90, 180]]],
                "FiltersShipMMSI": chunk,
                "FilterMessageTypes": MESSAGE_TYPES,
            }
            chunk_contacts = await collect_once(subscription, 25)
            for c in chunk_contacts:
                key = contact_key(c)
                if key not in all_contacts:
                    all_contacts[key] = {}
                merge_contact(all_contacts[key], c)

    if not all_contacts:
        subscription = {
            "APIKey": api_key,
            "BoundingBoxes": FALLBACK_BOUNDING_BOXES,
            "FilterMessageTypes": MESSAGE_TYPES,
        }
        fallback_contacts = await collect_once(subscription, 90)
        for c in fallback_contacts:
            key = contact_key(c)
            if key not in all_contacts:
                all_contacts[key] = {}
            merge_contact(all_contacts[key], c)

    contacts_out = list(all_contacts.values())

    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "AISStream",
        "count": len(contacts_out),
        "contacts": contacts_out,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
