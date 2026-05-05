import os
import json
import asyncio
from datetime import datetime, timezone, timedelta

import websockets

AISSTREAM_API_KEY = os.getenv("AISSTREAM_API_KEY", "").strip()
AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"

BOUNDING_BOXES = [
    [[47.0, -5.5], [60.8, 25.5]]
]

TEST_WINDOW_SECONDS = 1800  # 30 Minuten
USE_MMSI_FILTER = False

MMSI_FILTERS = [
    "273111111",
    "538222222",
    "273111999",
    "273123456"
]


def log(msg):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {msg}")


def extract_position(msg):
    metadata = msg.get("MetaData") or msg.get("Metadata") or {}
    body = msg.get("Message") or {}
    msg_type = msg.get("MessageType")

    candidate_keys = [
        "PositionReport",
        "StandardClassBPositionReport",
        "ExtendedClassBPositionReport",
        "ShipStaticData",
        "StaticDataReport"
    ]

    for key in candidate_keys:
        payload = body.get(key)
        if isinstance(payload, dict):
            mmsi = str(payload.get("UserID") or metadata.get("MMSI") or "").strip()
            lat = payload.get("Latitude")
            lon = payload.get("Longitude")
            shipname = metadata.get("ShipName") or payload.get("Name") or ""
            return {
                "mmsi": mmsi,
                "lat": lat,
                "lon": lon,
                "shipname": shipname,
                "msg_type": key,
            }

    mmsi = str(metadata.get("MMSI") or "").strip()
    lat = metadata.get("latitude") or metadata.get("Latitude")
    lon = metadata.get("longitude") or metadata.get("Longitude")
    shipname = metadata.get("ShipName") or ""
    if mmsi:
        return {
            "mmsi": mmsi,
            "lat": lat,
            "lon": lon,
            "shipname": shipname,
            "msg_type": msg_type or "Unknown"
        }

    return None


async def main():
    if not AISSTREAM_API_KEY:
        raise RuntimeError("AISSTREAM_API_KEY fehlt in den Umgebungsvariablen.")

    subscription = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": BOUNDING_BOXES,
        "FilterMessageTypes": [
            "PositionReport",
            "StandardClassBPositionReport",
            "ExtendedClassBPositionReport",
            "ShipStaticData",
            "StaticDataReport"
        ]
    }

    if USE_MMSI_FILTER:
        subscription["FiltersShipMMSI"] = MMSI_FILTERS

    total_messages = 0
    parsed_messages = 0
    unique_mmsi = set()

    log(f"Connecting to {AISSTREAM_URL}")
    log(f"BoundingBoxes = {BOUNDING_BOXES}")
    log(f"USE_MMSI_FILTER = {USE_MMSI_FILTER}")
    if USE_MMSI_FILTER:
        log(f"MMSI filters = {MMSI_FILTERS}")
    log(f"Window = {TEST_WINDOW_SECONDS} seconds")

    async with websockets.connect(AISSTREAM_URL, open_timeout=20, close_timeout=10) as ws:
        await ws.send(json.dumps(subscription))
        log("Subscription sent")

        deadline = datetime.now(timezone.utc) + timedelta(seconds=TEST_WINDOW_SECONDS)
        last_status = datetime.now(timezone.utc)

        while datetime.now(timezone.utc) < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
            except asyncio.TimeoutError:
                now = datetime.now(timezone.utc)
                log(f"No message in last 15s | total_messages={total_messages} unique_mmsi={len(unique_mmsi)}")
                last_status = now
                continue

            total_messages += 1

            try:
                msg = json.loads(raw)
            except Exception as e:
                log(f"JSON decode failed: {e}")
                continue

            if "error" in msg:
                log(f"Service error: {msg['error']}")
                continue

            parsed = extract_position(msg)
            if not parsed:
                continue

            parsed_messages += 1
            mmsi = parsed["mmsi"]
            if mmsi:
                unique_mmsi.add(mmsi)

            log(
                f"MSG #{total_messages} | parsed={parsed_messages} | "
                f"type={parsed['msg_type']} | mmsi={parsed['mmsi']} | "
                f"name={parsed['shipname']} | lat={parsed['lat']} | lon={parsed['lon']}"
            )

        log("Test finished")
        log(f"total_messages={total_messages}")
        log(f"parsed_messages={parsed_messages}")
        log(f"unique_mmsi={len(unique_mmsi)}")


if __name__ == "__main__":
    asyncio.run(main())
