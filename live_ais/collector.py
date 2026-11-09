#!/usr/bin/env python3
"""Read-only AIS collector: two Open Waters streams, BarentsWatch HTTP, Fintraffic MQTT.

No GitHub Actions, commits or public outputs. Source timestamps stay explicit.
"""
from __future__ import annotations
import json
import logging
import math
import os
import queue
import random
import re
import signal
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("voodoo.live_ais")
ROOT = Path(__file__).resolve().parent
STOP = threading.Event()
POSITION_TYPES = {"PositionReport", "StandardClassBPositionReport",
                  "ExtendedClassBPositionReport", "LongRangeAisBroadcastMessage"}
PRIORITY = {"barentswatch": 3, "fintraffic": 3, "openwaters": 1}


def utc_now():
    return datetime.now(timezone.utc)


def source_time(value, unit="iso"):
    """Never replace a missing or bad source timestamp with the retrieval time."""
    try:
        if unit in ("seconds", "milliseconds"):
            number = float(value)
            if not math.isfinite(number):
                return None
            dt = datetime.fromtimestamp(number / (1000 if unit == "milliseconds" else 1),
                                        tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        if datetime(2000, 1, 1, tzinfo=timezone.utc) <= dt <= utc_now() + timedelta(hours=1):
            return dt.isoformat(timespec="microseconds")
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return None


def mmsi(value):
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{9}", text) else None


def bounded_number(value, limit=None):
    try:
        v = float(value)
        if math.isfinite(v) and v >= 0 and (limit is None or v < limit):
            return v
    except (TypeError, ValueError):
        pass
    return None


def coordinates(lat, lon):
    try:
        a, b = float(lat), float(lon)
        if math.isfinite(a) and math.isfinite(b) and -90 <= a <= 90 and -180 <= b <= 180:
            return a, b
    except (TypeError, ValueError):
        pass
    return None


def record(provider, identity, timestamp=None, **data):
    identity = mmsi(identity)
    if not identity:
        return None
    return {
        "mmsi": identity, "source_provider": provider,
        "source": data.get("source", provider),
        "source_stream": data.get("stream") or "",
        "last_seen_utc": timestamp,
        "position_timestamp_valid": bool(timestamp),
        "position_timestamp_basis": "source_timestamp" if timestamp else "missing",
        "latitude": data.get("latitude"), "longitude": data.get("longitude"),
        "sog": bounded_number(data.get("sog"), 102.2),
        "cog": bounded_number(data.get("cog"), 360),
        "true_heading": bounded_number(data.get("heading"), 511),
        "name": str(data.get("name") or "").strip(),
        "callsign": str(data.get("callsign") or "").strip(),
        "destination": str(data.get("destination") or "").strip(),
        "imo": str(data.get("imo") or "").strip(),
        "ship_type": data.get("ship_type"),
        "navigational_status": data.get("nav_status"),
        "message_type_last": data.get("message_type", ""),
    }


def from_openwaters(event):
    if event.get("type") != "event":
        return None
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    kind = str(event.get("msg_type") or "")
    # Open Waters includes cached coordinates on STATIC events. Not a new position fix!
    pos = coordinates(event.get("lat"), event.get("lon")) if kind in POSITION_TYPES else None
    return record(
        "openwaters", event.get("mmsi"),
        source_time(event.get("time")) if pos else None,
        latitude=pos[0] if pos else None, longitude=pos[1] if pos else None,
        sog=message.get("Sog"), cog=message.get("Cog"),
        heading=message.get("TrueHeading"), nav_status=message.get("NavigationalStatus"),
        name=message.get("Name") or message.get("ShipName"),
        callsign=message.get("CallSign"), destination=message.get("Destination"),
        imo=message.get("ImoNumber"), ship_type=message.get("Type"),
        message_type=kind, source="Open Waters AIS", stream=event.get("source"),
    )


def from_barentswatch(event):
    pos = coordinates(event.get("latitude"), event.get("longitude"))
    return record(
        "barentswatch", event.get("mmsi"), source_time(event.get("msgtime")) if pos else None,
        latitude=pos[0] if pos else None, longitude=pos[1] if pos else None,
        sog=event.get("speedOverGround"), cog=event.get("courseOverGround"),
        heading=event.get("trueHeading"), nav_status=event.get("navigationalStatus"),
        name=event.get("name"), callsign=event.get("callSign"),
        destination=event.get("destination"), imo=event.get("imoNumber"),
        ship_type=event.get("shipType"), message_type="RegionalLive",
        source="BarentsWatch / Norwegian Coastal Administration",
        stream=event.get("stream"),
    )


def from_fintraffic(topic, event):
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "vessels-v2" or parts[2] not in (
        "location", "locations", "metadata"
    ):
        return None
    if parts[2] == "metadata":
        return record(
            "fintraffic", parts[1], None,
            name=event.get("name"), callsign=event.get("callSign"),
            destination=event.get("destination"), imo=event.get("imo"),
            ship_type=event.get("type"), message_type="RegionalMetadata",
            source="Fintraffic Digitraffic",
        )
    pos = coordinates(event.get("lat"), event.get("lon"))
    return record(
        "fintraffic", parts[1],
        source_time(event.get("time"), "seconds") if pos else None,
        latitude=pos[0] if pos else None, longitude=pos[1] if pos else None,
        sog=event.get("sog"), cog=event.get("cog"), heading=event.get("heading"),
        nav_status=event.get("navStat"), message_type="RegionalLive",
        source="Fintraffic Digitraffic",
    )


def load_boxes(path):
    streams = json.loads(path.read_text(encoding="utf-8"))["openwaters_streams"]
    if len(streams) != 2:
        raise ValueError("Personal tier: configure exactly two Open Waters streams")
    for stream in streams:
        if not stream.get("boxes"):
            raise ValueError("Empty box subscription")
        area = 0.0
        for box in stream["boxes"]:
            if len(box) != 4:
                raise ValueError("Box must be [minLat,minLon,maxLat,maxLon]")
            south, west, north, east = box
            if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
                raise ValueError("Invalid bounding box")
            area += (north - south) * (east - west)
        if area > 400 + 1e-9:
            raise ValueError(f"Over 400 square degrees: {stream['name']} ({area})")
        stream["area_deg2"] = area
    return streams


class Store:
    """Single-writer SQLite latest and optional full-resolution tracked MMSI history."""

    def __init__(self, db_path, tracked_mmsis):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS latest (
            mmsi TEXT PRIMARY KEY, observed_at TEXT NOT NULL,
            rank INTEGER NOT NULL, payload TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS metadata (
            mmsi TEXT PRIMARY KEY, payload TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS tracked_positions (
            mmsi TEXT NOT NULL, observed_at TEXT NOT NULL,
            latitude REAL NOT NULL, longitude REAL NOT NULL,
            source_provider TEXT NOT NULL,
            PRIMARY KEY (mmsi, observed_at, latitude, longitude)
          );
          CREATE INDEX IF NOT EXISTS track_time ON tracked_positions(observed_at);
        """)
        self.tracked_mmsis = tracked_mmsis

    def apply(self, item):
        key = item["mmsi"]
        meta = {k: item[k] for k in ("name", "callsign", "destination", "imo", "ship_type")
                if item.get(k) not in (None, "")}
        if meta:
            existing = self.db.execute("SELECT payload FROM metadata WHERE mmsi=?", (key,)).fetchone()
            previous = json.loads(existing[0]) if existing else {}
            previous.update(meta)
            self.db.execute("""INSERT INTO metadata VALUES (?,?)
                ON CONFLICT(mmsi) DO UPDATE SET payload=excluded.payload""",
                            (key, json.dumps(previous)))
        timestamp = item.get("last_seen_utc")
        if not timestamp or item.get("latitude") is None or item.get("longitude") is None:
            return
        old = self.db.execute("SELECT observed_at,rank FROM latest WHERE mmsi=?", (key,)).fetchone()
        rank = PRIORITY.get(item["source_provider"], 0)
        if old is None or timestamp > old[0] or (timestamp == old[0] and rank > old[1]):
            self.db.execute("""INSERT INTO latest VALUES (?,?,?,?)
                ON CONFLICT(mmsi) DO UPDATE SET observed_at=excluded.observed_at,
                rank=excluded.rank,payload=excluded.payload""",
                            (key, timestamp, rank, json.dumps(item)))
        if key in self.tracked_mmsis:
            self.db.execute("INSERT OR IGNORE INTO tracked_positions VALUES (?,?,?,?,?)",
                            (key, timestamp, item["latitude"], item["longitude"],
                             item["source_provider"]))

    def expire(self):
        cutoff = (utc_now() - timedelta(days=14)).isoformat(timespec="microseconds")
        with self.db:
            self.db.execute("DELETE FROM tracked_positions WHERE observed_at < ?", (cutoff,))
            self.db.execute("DELETE FROM latest WHERE observed_at < ?", (cutoff,))
            self.db.execute("DELETE FROM metadata WHERE mmsi NOT IN (SELECT mmsi FROM latest)")

    def export(self, path):
        """Only private unfiltered snapshots; never publish these files directly."""
        cutoff = (utc_now() - timedelta(minutes=45)).isoformat(timespec="microseconds")
        meta = {m: json.loads(p) for m, p in self.db.execute("SELECT mmsi,payload FROM metadata")}
        groups = {"openwaters": [], "barentswatch": [], "fintraffic": []}
        for (raw,) in self.db.execute("SELECT payload FROM latest WHERE observed_at >= ?", (cutoff,)):
            item = json.loads(raw)
            for k, v in meta.get(item["mmsi"], {}).items():
                if not item.get(k) and v not in (None, ""):
                    item[k] = v
            groups[item["source_provider"]].append(item)
        path.mkdir(parents=True, exist_ok=True)
        at = utc_now().isoformat(timespec="seconds")
        for provider, contacts in groups.items():
            payload = {
                "schema_version": "1.0.0", "generated_at": at,
                "source": provider, "provider": provider, "count": len(contacts),
                "filter_mode": "Private unfiltered live snapshot; NOT for publication",
                "contacts": sorted(contacts, key=lambda it: it["mmsi"]),
            }
            dest = path / f"ais_contacts_{provider}_latest.json"
            temp = dest.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            temp.replace(dest)


def delay(attempt):
    return min(120, 2 ** min(attempt, 6)) + random.random()


def openwaters_worker(stream, token, out):
    from websockets.sync.client import connect
    headers = {"Authorization": f"Bearer {token}"}
    attempt = 0
    while not STOP.is_set():
        try:
            with connect("wss://ais.openwaters.io/v1/stream", additional_headers=headers,
                         ping_interval=20, ping_timeout=20, open_timeout=25,
                         max_size=2 ** 20) as ws:
                ws.send(json.dumps({"type": "subscribe", "bbox": stream["boxes"], "snapshot": True}))
                LOG.info("Open Waters %s connected, %.0f square degrees",
                         stream["name"], stream["area_deg2"])
                attempt = 0
                for frame in ws:
                    if STOP.is_set():
                        return
                    event = json.loads(frame)
                    if event.get("type") == "error":
                        raise RuntimeError(f"Open Waters {stream['name']}: {event.get('error')}")
                    if event.get("type") == "welcome" and event.get("role") not in (
                        "personal", "contributor", "partner", "admin"
                    ):
                        raise RuntimeError("Token not recognized as Personal tier")
                    item = from_openwaters(event)
                    if item:
                        item["stream_connection"] = stream["name"]
                        out.put(item, timeout=10)
        except Exception as exc:
            if STOP.is_set():
                return
            LOG.warning("Open Waters %s reconnect: %s", stream["name"], exc)
        STOP.wait(delay(attempt))
        attempt += 1


def barentswatch_worker(client_id, client_secret, out):
    import requests
    attempt = 0
    while not STOP.is_set():
        try:
            response = requests.post(
                "https://id.barentswatch.no/connect/token",
                data={"client_id": client_id, "client_secret": client_secret,
                      "scope": "ais", "grant_type": "client_credentials"},
                timeout=25,
            )
            response.raise_for_status()
            token = response.json()["access_token"]
            started = time.monotonic()
            with requests.get(
                "https://live.ais.barentswatch.no/v1/combined",
                params={"modelType": "Full"},
                headers={"Authorization": f"Bearer {token}"},
                stream=True, timeout=(25, 120),
            ) as feed:
                feed.raise_for_status()
                LOG.info("BarentsWatch connected")
                attempt = 0
                for line in feed.iter_lines(chunk_size=16384):
                    if STOP.is_set():
                        return
                    if time.monotonic() - started > 50 * 60:
                        break  # refresh hourly OAuth token
                    if line:
                        item = from_barentswatch(json.loads(line))
                        if item:
                            out.put(item, timeout=10)
        except Exception as exc:
            if STOP.is_set():
                return
            LOG.warning("BarentsWatch reconnect: %s", exc)
        STOP.wait(delay(attempt))
        attempt += 1


def fintraffic_worker(out):
    import paho.mqtt.client as mqtt
    attempt = 0
    while not STOP.is_set():
        client = None
        try:
            ready = threading.Event()
            client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=f"mowlsint-vw-{os.getpid()}",
                                 transport="websockets")
            client.ws_set_options(path="/mqtt")
            client.tls_set()

            def connected(cli, userdata, flags, reason_code, properties):
                if reason_code.is_failure:
                    LOG.warning("Fintraffic connection refused: %s", reason_code)
                else:
                    cli.subscribe("vessels-v2/#", qos=0)
                    ready.set()
                    LOG.info("Fintraffic MQTT connected")

            def message_received(cli, userdata, message):
                try:
                    item = from_fintraffic(message.topic, json.loads(message.payload))
                    if item:
                        out.put_nowait(item)
                except queue.Full:
                    LOG.error("Fintraffic queue full; event dropped")
                except (ValueError, TypeError):
                    LOG.warning("Fintraffic message malformed")

            client.on_connect = connected
            client.on_message = message_received
            client.connect("meri.digitraffic.fi", 443, keepalive=60)
            client.loop_start()
            if not ready.wait(20):
                raise ConnectionError("Fintraffic MQTT connection timeout")
            attempt = 0
            while not STOP.wait(1):
                if not client.is_connected():
                    raise ConnectionError("Fintraffic MQTT disconnected")
        except Exception as exc:
            if STOP.is_set():
                return
            LOG.warning("Fintraffic reconnect: %s", exc)
        finally:
            if client:
                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception:
                    pass
        STOP.wait(delay(attempt))
        attempt += 1


def run():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    token = os.getenv("OPENWATERS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing OPENWATERS_TOKEN: add it to the server's private .env")
    streams = load_boxes(Path(os.getenv("BBOX_CONFIG", str(ROOT / "bboxes.json"))))
    db = Path(os.getenv("AIS_DB_PATH", "/state/ais.sqlite3"))
    snapshots = Path(os.getenv("AIS_PRIVATE_SNAPSHOT_DIR", "/state/snapshots"))
    tracked = {x for x in os.getenv("TRACK_MMSIS", "").split(",") if mmsi(x)}
    store = Store(db, tracked)
    out = queue.Queue(maxsize=100000)
    workers = [threading.Thread(target=openwaters_worker, args=(stream, token, out),
                                daemon=True, name=stream["name"]) for stream in streams]
    bw_id, bw_secret = os.getenv("BARENTSWATCH_CLIENT_ID", ""), os.getenv(
        "BARENTSWATCH_CLIENT_SECRET", "")
    if bw_id and bw_secret:
        workers.append(threading.Thread(target=barentswatch_worker,
                                        args=(bw_id, bw_secret, out),
                                        daemon=True, name="barentswatch"))
    else:
        LOG.warning("BarentsWatch credentials missing; Norway stream disabled")
    workers.append(threading.Thread(target=fintraffic_worker, args=(out,),
                                    daemon=True, name="fintraffic"))
    for worker in workers:
        worker.start()
    counts = Counter()
    per_second = Counter()
    max_per_second = Counter()
    last_second = time.monotonic()
    last_export = last_log = last_expire = time.monotonic()
    export_interval = max(5, int(os.getenv("AIS_EXPORT_INTERVAL_SECONDS", "30")))
    try:
        while not STOP.is_set():
            batch = []
            try:
                batch.append(out.get(timeout=0.5))
                for _ in range(199):
                    try:
                        batch.append(out.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                pass
            if batch:
                with store.db:
                    for item in batch:
                        store.apply(item)
                        label = item.get("stream_connection") or item["source_provider"]
                        counts[label] += 1
                        per_second[label] += 1
            now = time.monotonic()
            if now - last_second >= 1:
                for label, amount in per_second.items():
                    max_per_second[label] = max(max_per_second[label], amount)
                per_second.clear()
                last_second = now
            if now - last_export >= export_interval:
                store.export(snapshots)
                last_export = now
            if now - last_log >= 60:
                LOG.info("AIS messages last minute: %s; peak/s: %s; queue=%d",
                         dict(counts), dict(max_per_second), out.qsize())
                counts.clear()
                max_per_second.clear()
                last_log = now
            if now - last_expire >= 3600:
                store.expire()
                last_expire = now
    finally:
        store.export(snapshots)
        store.db.close()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    run()
