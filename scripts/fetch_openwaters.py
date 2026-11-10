#!/usr/bin/env python3
"""Bounded native Open Waters collection. No raw frames or credentials persisted."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fetch_regional_contacts import (
    atomic_json_write, filter_contacts, normalize_timestamp, normalize_sog,
    normalize_cog, normalize_heading, utc_now_iso,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/openwaters.json"
OUTPUT = ROOT / "data/ais_contacts_openwaters_latest.json"
STATUS = ROOT / "data/ais_openwaters_fetch_status_latest.json"
URL = "wss://ais.openwaters.io/v1/stream"
POSITION_TYPES = {"PositionReport", "StandardClassBPositionReport",
                  "ExtendedClassBPositionReport", "LongRangeAisBroadcastMessage"}


def validate_config(cfg):
    streams = cfg.get("streams")
    if not isinstance(streams, list) or len(streams) != 2:
        raise ValueError("exactly two streams required")
    if not 120 <= float(cfg.get("sample_seconds", 0)) <= 1200:
        raise ValueError("sample_seconds must be between 120 and 1200")
    areas = []
    for stream in streams:
        boxes = stream.get("boxes")
        if not isinstance(boxes, list) or not boxes:
            raise ValueError("nonempty boxes required")
        area = 0
        for box in boxes:
            if not isinstance(box, list) or len(box) != 4:
                raise ValueError("box must be [south, west, north, east]")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
                raise ValueError("finite numeric coordinates required")
            south, west, north, east = box
            if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
                raise ValueError("box outside WGS84 bounds")
            area += (north-south)*(east-west)
        if area > 400:
            raise ValueError("subscription exceeds 400 square degrees")
        areas.append(area)
    if len({s.get("id") for s in streams}) != 2:
        raise ValueError("unique stream IDs required")
    return areas


def finite(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def normalize_event(event, retrieved_at):
    """Static-message cache coordinates never advance the position timestamp."""
    if event.get("type") != "event" or event.get("msg_type") not in POSITION_TYPES:
        return None
    msg = event.get("message") or {}
    if not isinstance(msg, dict) or msg.get("Valid") is False:
        return None
    mmsi = str(event.get("mmsi", ""))
    lat = finite(msg.get("Latitude", event.get("lat")))
    lon = finite(msg.get("Longitude", event.get("lon")))
    observed, valid, basis = normalize_timestamp(event.get("time"))
    if not (len(mmsi) == 9 and mmsi.isdigit() and valid and lat is not None and lon is not None
            and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {
        "mmsi": mmsi, "latitude": lat, "longitude": lon,
        "last_seen_utc": observed, "retrieved_at": retrieved_at,
        "position_timestamp_valid": True, "position_timestamp_basis": basis,
        "provider_timestamp_semantics": "source_time_within_30s_else_provider_receive_time",
        "sog": normalize_sog(msg.get("Sog")), "cog": normalize_cog(msg.get("Cog")),
        "true_heading": normalize_heading(msg.get("TrueHeading")),
        "navigational_status": msg.get("NavigationalStatus"),
        "source": "Open Waters", "source_provider": "openwaters",
        "upstream_source": str(event.get("source") or "unknown")[:160],
        "upstream_station": str(event.get("station") or "")[:160],
        "synthesized": event.get("synthesized") is True,
        "message_type_last": event.get("msg_type"),
    }


def static_fields(event):
    msg = event.get("message") or {}
    if not isinstance(msg, dict):
        return {}
    # go-ais Class B splits static information across ReportA and ReportB.
    fields = {**msg, **(msg.get("ReportA") or {}), **(msg.get("ReportB") or {})}
    mapping = {"name": "Name", "callsign": "CallSign", "imo": "ImoNumber",
               "destination": "Destination", "ship_type": "Type"}
    out = {key: fields[name] for key, name in mapping.items() if fields.get(name) not in (None, "", 0)}
    if fields.get("ShipType"):
        out["ship_type"] = fields["ShipType"]
    return out


async def collect_stream(stream, token, seconds, snapshot=True, connect=None):
    if connect is None:
        from websockets.asyncio.client import connect
    started = time.monotonic()
    start_utc = datetime.now(timezone.utc)
    stats = {"id": stream["id"], "ok": False, "events": 0, "duplicates": 0,
             "invalid_positions": 0, "older_or_replayed_events": 0,
             "observed_rate_near_limit": False, "thinning_possible": False,
             "errors": [], "disconnects": 0, "idle_gaps_over_30s": 0}
    positions, metadata, seen, buckets = {}, {}, set(), Counter()
    last_event = time.monotonic()
    try:
        # Authorization header avoids tokens in URLs, exception messages and request logs.
        async with connect(URL, additional_headers={"Authorization": f"Bearer {token}"},
                           open_timeout=min(20, seconds), close_timeout=2,
                           max_size=1_048_576, max_queue=64) as ws:
            await ws.send(json.dumps({"type": "subscribe", "bbox": stream["boxes"], "snapshot": snapshot}))
            deadline = started + seconds
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), min(10, max(.01, deadline-time.monotonic())))
                except asyncio.TimeoutError:
                    continue
                try:
                    event = json.loads(raw)
                except (ValueError, TypeError):
                    stats["errors"].append("invalid_json")
                    break
                if not isinstance(event, dict):
                    stats["errors"].append("invalid_frame")
                    break
                kind = event.get("type")
                if kind == "welcome":
                    limits = event.get("limits") or {}
                    stats["limits"] = {k: limits[k] for k in ("rate", "area", "conns") if k in limits}
                    if limits.get("area", 400) < sum((b[2]-b[0])*(b[3]-b[1]) for b in stream["boxes"]):
                        stats["errors"].append("insufficient_area_limit")
                        break
                    continue
                if kind == "error":
                    # Never echo server messages: they may contain authentication material.
                    stats["errors"].append("provider_error_frame")
                    stats["thinning_possible"] = True
                    break
                if kind != "event":
                    continue
                stats["events"] += 1
                now_mono = time.monotonic()
                if now_mono-last_event > 30:
                    stats["idle_gaps_over_30s"] += 1
                last_event = now_mono
                event_time, valid, _ = normalize_timestamp(event.get("time"))
                if valid and datetime.fromisoformat(event_time) >= start_utc:
                    buckets[int(now_mono-started)] += 1
                else:
                    stats["older_or_replayed_events"] += 1
                key = (event.get("id"), event.get("time")) if event.get("id") else (
                    event.get("mmsi"), event.get("msg_type"), event.get("time"), event.get("lat"), event.get("lon"))
                if key in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(key)
                if len(seen) > 100000:
                    stats["errors"].append("event_memory_guard")
                    stats["thinning_possible"] = True
                    break
                mmsi = str(event.get("mmsi") or "")
                if len(mmsi) == 9 and mmsi.isdigit():
                    metadata.setdefault(mmsi, {}).update(static_fields(event))
                record = normalize_event(event, utc_now_iso())
                if record is None:
                    if kind == "event" and event.get("msg_type") in POSITION_TYPES:
                        stats["invalid_positions"] += 1
                    continue
                vals = positions.setdefault(mmsi, {})
                vals[(record["last_seen_utc"], record["latitude"], record["longitude"])] = record
                for old in sorted(vals)[:-2]:
                    del vals[old]
            stats["ok"] = not stats["errors"]
    except Exception as exc:
        stats["errors"].append(type(exc).__name__)
        stats["disconnects"] += 1
        stats["thinning_possible"] = True
    elapsed = max(.001, time.monotonic()-started)
    stats.update({"elapsed_seconds": round(elapsed, 2),
                  "messages_per_second": round(stats["events"]/elapsed, 2),
                  "max_live_events_per_receive_second": max(buckets.values(), default=0),
                  "rate_measurement_basis": "receive_second_buckets_excluding_pre_sample_provider_times",
                  "no_events": stats["events"] == 0,
                  "tail_idle_seconds": round(time.monotonic()-last_event, 1)})
    limit = (stats.get("limits") or {}).get("rate", 50)
    stats["observed_rate_near_limit"] = bool(limit and max(buckets.values(), default=0) >= .9*limit)
    stats["thinning_possible"] |= stats["observed_rate_near_limit"]
    stats["coverage_limit"] = "Reception-dependent sample; no traffic absence or full-coverage claim. Server thinning may be invisible."
    records = []
    for mmsi, vals in positions.items():
        ordered = [vals[k] for k in sorted(vals)]
        record = {**metadata.get(mmsi, {}), **ordered[-1], "stream_id": stream["id"]}
        record["positions"] = ordered
        records.append(record)
    stats["contacts_before_filter"] = len(records)
    return records, stats


async def collect(cfg, token, connect=None):
    validate_config(cfg)
    return await asyncio.gather(*(collect_stream(s, token, cfg["sample_seconds"],
                              cfg.get("snapshot", True), connect) for s in cfg["streams"]))


def publish(results, generated_at=None, output=OUTPUT, status_path=STATUS):
    statuses = [stats for _, stats in results]
    # Partial success refreshes only the successful stream; preserve the other stream's LKG.
    previous = {}
    if output.exists():
        try:
            previous = json.loads(output.read_text())
        except (ValueError, OSError):
            pass
    success = {s["id"] for s in statuses if s["ok"] and not s["no_events"]}
    static_cache = {c["mmsi"]: c for c in previous.get("contacts", []) if isinstance(c, dict) and c.get("mmsi")}
    rows = [c for c in previous.get("contacts", []) if c.get("stream_id") not in success]
    for records, stats in results:
        if stats["id"] in success:
            for incoming in records:
                record = dict(incoming)
                old = static_cache.get(record["mmsi"], {})
                for key in ("imo", "name", "callsign", "ship_type"):
                    if record.get(key) in (None, "") and old.get(key) not in (None, ""):
                        record[key] = old[key]
                        record["static_metadata_basis"] = "includes_last_known_identity_fields"
                rows.append(record)
    if success:
        dedup = {}
        for record in rows:
            old = dedup.get(record["mmsi"])
            if old is None or record["last_seen_utc"] > old["last_seen_utc"]:
                dedup[record["mmsi"]] = record
        kept = filter_contacts(dedup.values(), "openwaters")
        atomic_json_write(output, {"schema_version": "1.0.0", "generated_at": generated_at or utc_now_iso(),
            "provider": "openwaters", "source": "Open Waters", "count": len(kept), "contacts": kept,
            "coverage": "Reception-dependent short samples; filtered monitored contacts only",
            "coverage_complete": False, "stream_status": statuses,
            "attribution": "Open Waters; original source and station retained per position",
            "source_terms": "https://openwaters.io/api/ais/"})
    atomic_json_write(status_path, {"generated_at": generated_at or utc_now_iso(), "streams": statuses,
        "ok": len(success) == 2, "partial": len(success) == 1, "retained_previous_on_failure": len(success) < 2})
    return 0 if len(success) == 2 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-config", action="store_true")
    args = parser.parse_args()
    cfg = json.loads(CONFIG.read_text())
    if args.validate_config:
        print(json.dumps({"areas_square_degrees": validate_config(cfg), "automatic_activation": False}))
        return 0
    token = os.environ.get("OPENWATERS_TOKEN", "").strip()
    if not token:
        atomic_json_write(STATUS, {"generated_at": utc_now_iso(), "ok": False, "error": "OPENWATERS_TOKEN missing", "live_test_performed": False})
        return 1
    return publish(asyncio.run(collect(cfg, token)))


if __name__ == "__main__":
    raise SystemExit(main())
