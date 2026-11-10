"""Offline regression tests for the bounded AIS producer and shared contracts."""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import fetch_openwaters as ow
import fetch_regional_contacts as regional
import merge_ais_contacts as broker
import archive_maritime_snapshots as archive
import build_public_outputs as public
import copernicus_historical_context as context
import fetch_gfw_context as gfw


def event(**overrides):
    return {"type": "event", "id": "content", "time": "2026-11-01T12:00:00Z",
            "mmsi": 273123456, "msg_type": "PositionReport", "lat": 54.5, "lon": 12.1,
            "source": "digitraffic", "station": "station1", "synthesized": True,
            "message": {"Valid": True, "Sog": 102.3, "Cog": 360, "TrueHeading": 511}, **overrides}


def candidates(points=None):
    return {"type": "FeatureCollection", "metadata": {"scene": {
        "normalized_scene_id": "S1C_IW_GRDH_1SDV_20261101T120000_20261101T120025_TEST",
        "datetime": "2026-11-01T12:00:00Z"}}, "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
             "properties": {"detection_id": str(i)}} for i, (lon, lat) in enumerate(points or [(12.1, 54.5)])]}


def test_bboxes_and_limits():
    cfg = json.loads(ow.CONFIG.read_text())
    assert ow.validate_config(cfg) == [399, 400]
    cfg["streams"][0]["boxes"].append([0, 0, 1, 2])
    with pytest.raises(ValueError):
        ow.validate_config(cfg)
    cfg["streams"][0]["boxes"] = [[float("nan"), 0, 1, 1]]
    with pytest.raises(ValueError):
        ow.validate_config(cfg)


def test_native_positions_and_static_cached_coordinates():
    c = ow.normalize_event(event(), "2026-11-01T13:00:00Z")
    assert c["last_seen_utc"] == "2026-11-01T12:00:00+00:00"
    assert c["retrieved_at"] != c["last_seen_utc"]
    assert c["upstream_source"] == "digitraffic"
    assert all(c[k] is None for k in ("sog", "cog", "true_heading"))
    assert ow.normalize_event(event(msg_type="ShipStaticData"), "today") is None
    assert ow.normalize_event(event(lat=None), "today") is None
    assert ow.normalize_event(event(time=0), "today") is None
    assert ow.normalize_event(event(lat=float("nan")), "today") is None
    assert ow.normalize_event(event(message={"Latitude": 91, "Longitude": 181}), "today") is None


def test_fintraffic_uses_external_epoch_not_ais_second():
    props = {"mmsi": 273123456, "timestamp": 42, "timestampExternal": 1793534400000}
    c = regional.normalize_fintraffic({"properties": props, "geometry": {"coordinates": [12.1, 54.5]}}, {}, "2026-11-09T12:00:00Z")
    assert c["last_seen_utc"].startswith("2026-11-01T12:00:00")
    assert c["position_timestamp_valid"] is True
    del props["timestampExternal"]
    c = regional.normalize_fintraffic({"properties": props}, {}, "2026-11-09T12:00:00Z")
    assert c["last_seen_utc"] == "" and c["position_timestamp_valid"] is False
    assert archive.normalize(c, "fintraffic") is None
    assert public.normalize_item(c, datetime.now(timezone.utc))["observed_at"] is None
    assert regional.normalize_timestamp(float("inf"))[1] is False


def test_merge_observation_is_atomic():
    older = {"mmsi": "273123456", "last_seen_utc": "2026-11-01T11:00:00Z", "latitude": 1, "longitude": 2,
             "cog": 45, "source_provider": "fintraffic", "position_timestamp_valid": True, "name": "OLD NAME"}
    newer = {"mmsi": "273123456", "last_seen_utc": "2026-11-01T12:00:00Z", "latitude": 3, "longitude": 4,
             "cog": None, "source_provider": "openwaters", "upstream_source": "kystverket", "position_timestamp_valid": True}
    merged = broker.combine_contacts(older, newer)
    assert (merged["latitude"], merged["longitude"], merged["cog"]) == (3, 4, None)
    assert merged["source_provider"] == "openwaters" and merged["name"] == "OLD NAME"
    assert broker.contact_key({"imo": "9123456", "mmsi": "273123456"}) == "imo:9123456"
    newer["position_timestamp_valid"] = False
    assert broker.combine_contacts(older, newer)["latitude"] == 1


def test_broker_deduplicates_mmsi_with_missing_imo_and_checks_observation_age(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    base = {"mmsi": "273123456", "last_seen_utc": now.isoformat(), "latitude": 54.5, "longitude": 12.1,
            "position_timestamp_valid": True}
    sources = []
    for name, contacts in [("openwaters", [{**base, "imo": "9123456"}]), ("fintraffic", [base,
            {**base, "mmsi": "273123457", "last_seen_utc": (now-timedelta(days=20)).isoformat()}])]:
        path = tmp_path/f"{name}.json"
        path.write_text(json.dumps({"generated_at": now.isoformat(), "contacts": contacts}))
        sources.append({"id": name, "path": path, "soft_ttl_hours": 12, "hard_ttl_hours": 72})
    monkeypatch.setattr(broker, "PROVIDERS", sources)
    monkeypatch.setattr(broker, "OUTPUT_PATH", tmp_path/"merged.json")
    assert broker.main() == 0
    pack = json.loads((tmp_path/"merged.json").read_text())
    assert pack["count"] == 1 and pack["coverage_mode"] == "mixed_regional_and_primary"


def test_socket_duplicate_key_includes_time_and_static_never_moves_position():
    class Socket:
        def __init__(self):
            self.frames = iter([{"type": "welcome", "limits": {"area": 400, "rate": 50}}, event(), event(),
                event(time="2026-11-01T12:00:10Z"), event(msg_type="ShipStaticData", time="2026-11-01T13:00:00Z", message={"Name": "TEST"})])
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def send(self, message):
            assert json.loads(message)["snapshot"] is True
        async def recv(self):
            try: return json.dumps(next(self.frames))
            except StopIteration:
                await asyncio.sleep(1)
    def connect(url, **kwargs):
        assert "key=" not in url and kwargs["additional_headers"]["Authorization"] == "Bearer fake-secret"
        return Socket()
    rows, stats = asyncio.run(ow.collect_stream({"id": "test", "boxes": [[0, 0, 1, 1]]}, "fake-secret", .03, connect=connect))
    assert stats["duplicates"] == 1 and stats["ok"]
    assert len(rows) == 1 and len(rows[0]["positions"]) == 2
    assert rows[0]["name"] == "TEST"
    assert rows[0]["last_seen_utc"].endswith("12:00:10+00:00")


def test_socket_errors_do_not_leak_secrets():
    def connect(*args, **kwargs):
        raise RuntimeError("fake-secret")
    rows, status = asyncio.run(ow.collect_stream({"id": "test", "boxes": [[0, 0, 1, 1]]}, "fake-secret", .01, connect=connect))
    assert not rows and status["errors"] == ["RuntimeError"]
    assert "fake-secret" not in json.dumps(status)


def test_failure_keeps_provider_lkg(tmp_path):
    path = tmp_path/"provider.json"
    raw = '{"contacts": [], "generated_at": "2026-11-01T12:00:00Z"}'
    path.write_text(raw)
    assert ow.publish([([], {"id": "stream_1", "ok": False, "no_events": True})], output=path, status_path=tmp_path/"status.json") == 1
    assert path.read_text() == raw


def test_partial_success_preserves_other_stream_and_static_identity(tmp_path, monkeypatch):
    path=tmp_path/'provider.json'
    first={"mmsi":"273123456","imo":"9179842","name":"TEST","stream_id":"stream_1","last_seen_utc":"2026-11-01T10:00:00Z"}
    second={"mmsi":"273123457","stream_id":"stream_2","last_seen_utc":"2026-11-01T11:00:00Z"}
    path.write_text(json.dumps({"contacts":[first,second]}))
    monkeypatch.setattr(ow,'filter_contacts',lambda rows,provider:list(rows))
    incoming={"mmsi":"273123456","stream_id":"stream_1","last_seen_utc":"2026-11-01T12:00:00Z"}
    result=ow.publish([([incoming],{"id":"stream_1","ok":True,"no_events":False}),([],{"id":"stream_2","ok":False,"no_events":True})],output=path,status_path=tmp_path/'status.json')
    assert result==1
    rows=json.loads(path.read_text())['contacts']
    by_mmsi={row['mmsi']:row for row in rows}
    assert len(rows)==2 and by_mmsi[first['mmsi']]['imo']=='9179842'
    assert by_mmsi[first['mmsi']]['last_seen_utc']==incoming['last_seen_utc']
    assert by_mmsi[second['mmsi']]['last_seen_utc']==second['last_seen_utc']


def test_late_websocket_disconnect_keeps_valid_bounded_sample():
    class ConnectionClosedError(Exception):
        pass

    class Socket:
        def __init__(self):
            self.frames = iter([{"type": "welcome", "limits": {"area": 400, "rate": 50}},
                                event(id="fresh", time=datetime.now(timezone.utc).isoformat())])

        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def send(self, message): pass

        async def recv(self):
            try:
                return json.dumps(next(self.frames))
            except StopIteration:
                await asyncio.sleep(.25)
                raise ConnectionClosedError("never persist provider exception detail")

    rows, status = asyncio.run(ow.collect_stream({"id": "stream_1", "boxes": [[0, 0, 1, 1]]},
                                                   "test-token", .4, connect=lambda *a, **k: Socket()))
    assert len(rows) == 1
    assert status["errors"] == ["ConnectionClosedError"]
    assert not status["ok"] and status["usable_partial"]
    assert ow.stream_publishable(rows, status)
    assert "never persist" not in json.dumps(status)


def test_partial_disconnect_publishes_but_keeps_health_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(ow, "filter_contacts", lambda rows, provider: list(rows))
    now = datetime.now(timezone.utc).isoformat()
    row = {"mmsi": "273123456", "latitude": 54.5, "longitude": 12.1,
           "last_seen_utc": now, "position_timestamp_valid": True, "stream_id": "stream_1"}
    status1 = {"id": "stream_1", "ok": False, "no_events": False,
               "usable_partial": True, "errors": ["ConnectionClosedError"]}
    status2 = {"id": "stream_2", "ok": False, "no_events": True, "errors": ["RuntimeError"]}
    output, status_path = tmp_path / "ais.json", tmp_path / "status.json"
    assert ow.publish([([row], status1), ([], status2)], output=output, status_path=status_path) == 1
    assert json.loads(output.read_text())["contacts"][0]["mmsi"] == row["mmsi"]
    health = json.loads(status_path.read_text())
    assert not health["ok"] and health["partial"] and health["retained_previous_on_failure"]
    assert health["published_streams"] == ["stream_1"]
    assert health["degraded_streams"] == ["stream_1"]

    # Even a nonempty row must not bypass a fatal provider error.
    status1["usable_partial"] = False
    status1["errors"] = ["provider_error_frame"]
    original = output.read_text()
    assert ow.publish([([row], status1), ([], status2)], output=output, status_path=status_path) == 1
    assert output.read_text() == original


def test_sar_sparse_history_and_gfw_cannot_establish_full_coverage():
    payload = candidates()
    grid = {"records": [{"record_type": "ais_presence_grid", "latitude": 54.5, "longitude": 12.1,
            "interval_start": "2026-11-01T12:00:00Z", "interval_end": "2026-11-01T13:00:00Z", "spatial_resolution_degrees": .01}]}
    result, queue = context.assess(payload, [], grid)
    props = result["features"][0]["properties"]
    assert props["ais_match_status"] == "NO_AIS_COVERAGE"
    assert props["gfw_historical_context"]["presence_state"] == "CONTEXT_AVAILABLE"
    assert props["gfw_historical_context"]["grid_identity_match_permitted"] is False
    assert queue["items"][0]["downstream_eligible"] is False
    record = {"provider": "openwaters", "timestamp_valid": True, "position_is_exact": True,
              "mmsi": "273123456", "observed_at": "2026-11-01T12:00:00Z", "latitude": 54.5, "longitude": 12.1}
    result, _ = context.assess(payload, [record], grid)
    assert result["features"][0]["properties"]["ais_match_status"] == "POSSIBLE_MATCH"
    assert result["features"][0]["properties"]["historical_ais_context"]["selected_match"]["source_provider"] == "openwaters"
    result, _ = context.assess(candidates([(12.1, 54.5), (12.101, 54.5)]), [record])
    assert result["metadata"]["match_counts"]["AMBIGUOUS"] == 2
    record["timestamp_valid"] = False
    result, _ = context.assess(payload, [record])
    assert result["metadata"]["match_counts"]["NO_AIS_COVERAGE"] == 1


def test_wrong_scene_time_rejected():
    payload = candidates()
    payload["metadata"]["scene"]["datetime"] = "2026-11-09T12:00:00Z"
    with pytest.raises(ValueError):
        context.assess(payload, [])


def test_gfw_grid_and_events_are_distinct():
    cell = gfw.normalize_cell({"lat": 54.5, "lon": 12.1, "entryTimestamp": "2026-11-01T12:00:00Z", "hours": .2}, "baltic")
    assert cell["vessel_identity_available"] is False and cell["position_is_exact"] is False
    assert cell["temporal_resolution_seconds"] == 3600
    event_row = gfw.normalize_event({"id": "gap", "start": "2026-11-01T12:00:00Z", "end": "2026-11-01T13:00:00Z",
             "position": {"lat": 54.5, "lon": 12.1}}, "GAP", "baltic")
    assert event_row["record_type"] == "gfw_behavior_event" and event_row["not_current_position"]


def test_gfw_query_uses_scene_time_and_footprint_not_execution_day(tmp_path,monkeypatch):
    path=tmp_path/'candidates.json';path.write_text(json.dumps(candidates()))
    monkeypatch.setenv('GFW_TOKEN','offline-fixture-token')
    monkeypatch.setattr(gfw,'ROOT',tmp_path)
    monkeypatch.setattr(gfw,'GRID',tmp_path/'grid.json')
    monkeypatch.setattr(gfw,'EVENTS',tmp_path/'events.json')
    monkeypatch.setattr(gfw,'STATUS',tmp_path/'status.json')
    seen=[]
    def fake_fetch(session,region,day,include_events):
        seen.append((region,day.isoformat()))
        return [],[],[{'kind':'presence_grid','region_id':region['id'],'ok':True}]
    monkeypatch.setattr(gfw,'fetch_region',fake_fetch)
    assert gfw.main(['--candidates',str(path),'--bbox','12','54','13','55'])==0
    assert len(seen)==1 and seen[0][1]=='2026-11-01'
    assert seen[0][0]['geometry']['coordinates'][0][0]==[12,54]
    assert json.loads((tmp_path/'grid.json').read_text())['observation_day']=='2026-11-01'
