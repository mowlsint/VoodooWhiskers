from __future__ import annotations
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module

ROOT = Path(__file__).resolve().parents[1]
builder = load_module(ROOT / "scripts" / "build_common_maritime_snapshot.py", "common_builder")
gfw = load_module(ROOT / "scripts" / "fetch_gfw_common_presence.py", "common_gfw")

def test_identity_priority():
    assert builder.ident({"imo": "9123456", "mmsi": "123456789"}) == "imo:9123456"
    assert builder.ident({"mmsi": "123456789"}) == "mmsi:123456789"

def test_pick_latest_before_snapshot():
    snapshot = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    rows = [
        {"identity_key": "mmsi:123456789", "provider": "fintraffic", "observed_at": "2026-07-21T02:00:00Z", "latitude": 60, "longitude": 24},
        {"identity_key": "mmsi:123456789", "provider": "fintraffic", "observed_at": "2026-07-21T11:30:00Z", "latitude": 61, "longitude": 25},
        {"identity_key": "mmsi:123456789", "provider": "fintraffic", "observed_at": "2026-07-21T13:00:00Z", "latitude": 62, "longitude": 26},
    ]
    selected = builder.choose_at(rows, snapshot, 16)
    assert len(selected) == 1
    assert selected[0]["latitude"] == 61
    assert selected[0]["age_at_snapshot_minutes"] == 30.0
    assert selected[0]["previous_position"]["latitude"] == 60

def test_exact_source_wins_when_time_equal():
    config = {"provider_quality_rank": {"barentswatch": 1, "global_fishing_watch": 4}}
    rows = [
        {"identity_key": "imo:9123456", "provider": "global_fishing_watch", "age_at_snapshot_minutes": 30, "timestamp_valid": True, "position_is_exact": False},
        {"identity_key": "imo:9123456", "provider": "barentswatch", "age_at_snapshot_minutes": 30, "timestamp_valid": True, "position_is_exact": True},
    ]
    chosen = builder.canonical(rows, config)
    assert chosen[0]["provider"] == "barentswatch"

def test_gfw_normalize_keeps_gfw_identity_without_imo_or_mmsi():
    row = {"vesselId": "abc123", "entryTimestamp": "2026-07-21T10:00:00Z", "lat": 54.1, "lon": 7.9, "hours": 1}
    record = gfw.normalize(row, {"id": "german_bight", "name": "German Bight"})
    assert record and record["identity_key"] == "gfw:abc123"
    assert record["position_is_exact"] is False

def test_gfw_flatten_resolves_dataset():
    payload = {"entries": [{"public-global-presence:v20260720": [{"vesselId": "a"}]}]}
    resolved, rows = gfw.flatten(payload)
    assert resolved == "public-global-presence:v20260720"
    assert rows[0]["_source_dataset"] == resolved
