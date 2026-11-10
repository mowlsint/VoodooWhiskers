"""Bounded non-VOI background data and browser model; no external provider calls."""
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import traffic_context as traffic
import collect_ais
import fetch_regional_contacts as regional

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def row(n=0, **changes):
    return {"mmsi": str(200000000+n), "latitude": 55, "longitude": 12,
            "position_timestamp_valid": True, "position_timestamp_basis": "provider_receive_time",
            "last_seen_utc": NOW.isoformat(), **changes}


def unpack(pack):
    return [dict(zip(pack["columns"], r)) for r in pack["contacts"]]


def test_field_whitelist_times_sources_and_exclusion():
    context = traffic.TrafficContext(NOW)
    context.add_source("openwaters", [row(1, imo="9179842", upstream_source="digitraffic", raw="DO NOT STORE", positions=[{}]),
        row(2), row(3, position_timestamp_valid=False), row(4, latitude=None),
        row(5, longitude=float("nan")), row(6, last_seen_utc=(NOW-timedelta(hours=25)).isoformat()),
        row(7, last_seen_utc=(NOW+timedelta(minutes=6)).isoformat()), row(8, latitude=True)])
    context.add_source("fintraffic", [row(2, last_seen_utc=(NOW-timedelta(minutes=2)).isoformat()), row(9)])
    pack, raw = context.build([{"mmsi":"200000001"}])
    points = unpack(pack)
    assert {p["mmsi"] for p in points} == {"200000002", "200000009"}
    assert next(p for p in points if p["mmsi"]=="200000002")["source_provider"] == "openwaters"
    assert b'DO NOT STORE' not in raw and b'positions' not in raw
    assert pack["providers"]["barentswatch"]["state"] == "unavailable"
    assert not pack["coverage_complete"] and pack["context_only"] and pack["position_timestamp_valid"]
    assert pack["max_age_hours"]==24 and "history" not in pack


def test_imo_alias_and_priority_exclusion_with_other_mmsi():
    context=traffic.TrafficContext(NOW)
    context.add_source("openwaters",[row(1,imo="9179842"),row(2,imo="9179842"),row(3)])
    context.add_source("barentswatch",[row(1,last_seen_utc=(NOW-timedelta(minutes=1)).isoformat())])
    assert context.build()[0]["count"] == 2
    pack,_=context.build([{"mmsi":"200000099","imo":"9179842"}])
    assert [r["mmsi"] for r in unpack(pack)]==["200000003"]


def test_contact_and_utf8_byte_budgets_are_enforced():
    context=traffic.TrafficContext(NOW)
    context.add_source("openwaters",[row(n,upstream_source="ö"*64,position_timestamp_basis="x"*64,latitude=50+n%60/10) for n in range(12000)])
    context.add_source("fintraffic",[row(20000+n,latitude=65) for n in range(4000)])
    pack,raw=context.build()
    assert len(raw)<=1024*1024 and pack["count"]<=10000
    assert pack["cap_applied"] and pack["source_cap_applied"] and pack["omitted_count"]>0
    assert pack["providers"]["openwaters"]["unique_before_cap"]==12000
    assert {r["source_provider"] for r in unpack(pack)}=={"openwaters","fintraffic"}
    small,raw=context.build(max_contacts=2,max_bytes=4096)
    assert len(raw)<=4096 and small["count"]==2
    assert {r["source_provider"] for r in unpack(small)}=={"openwaters","fintraffic"}
    with pytest.raises(ValueError):context.build(max_bytes=1024*1024+1)


def test_twenty_minute_collection_uses_finish_clock(monkeypatch):
    original=traffic.utc
    current=[NOW]
    monkeypatch.setattr(traffic,"utc",lambda value=None: original(value) if value is not None else current[0])
    context=traffic.TrafficContext()
    current[0]+=timedelta(minutes=20)
    context.add_source("openwaters",[row(1,last_seen_utc=current[0].isoformat())])
    pack,_=context.build()
    assert pack["count"]==1 and pack["generated_at"]==current[0].isoformat()


def test_latest_only_replaces_file_and_failure_is_not_healthy_zero(tmp_path):
    path=tmp_path/"traffic.json"
    context=traffic.TrafficContext(NOW);context.add_source("fintraffic",[row(1)])
    context.publish(path=path)
    empty=traffic.TrafficContext(NOW+timedelta(hours=4));empty.publish(path=path)
    pack=json.loads(path.read_text())
    assert pack["count"]==0 and all(p["state"]=="unavailable" for p in pack["providers"].values())
    assert list(tmp_path.iterdir())==[path]


def test_regional_callback_sees_unfiltered_contacts_without_extra_requests(monkeypatch):
    class Response:
        def __init__(self,value):self.value=value
        def json(self):return self.value
        def raise_for_status(self):pass
    class Session:
        calls=0
        def get(self,url,**kwargs):
            self.calls+=1
            if url==regional.FINTRAFFIC_VESSELS_URL:return Response([])
            return Response({"type":"FeatureCollection","features":[{"type":"Feature","properties":{"mmsi":200000001,"timestampExternal":int(NOW.timestamp()*1000)},"geometry":{"type":"Point","coordinates":[12,55]}}]})
    seen=[];session=Session()
    monkeypatch.setattr(regional,"filter_contacts",lambda rows,provider:[])
    payload,_=regional.fetch_fintraffic(session,context_sink=lambda provider,rows:seen.append((provider,rows)))
    assert session.calls==2 and payload["contacts"]==[]
    assert seen[0][0]=="fintraffic" and seen[0][1][0]["mmsi"]=="200000001"


def test_combined_job_keeps_background_out_of_monitored_contacts(tmp_path,monkeypatch):
    context=traffic.TrafficContext(NOW)
    target=tmp_path/"traffic.json"
    original_publish=context.publish
    context.publish=lambda monitored:original_publish(monitored,path=target)
    monkeypatch.setattr(collect_ais,"TrafficContext",lambda:context)
    monkeypatch.setenv("OPENWATERS_TOKEN","offline-token")
    async def collect(cfg,token):
        return [([row(1),row(2)],{"id":"stream_1","ok":True,"no_events":False}),([],{"id":"stream_2","ok":False,"no_events":True})]
    def regional_main(context_sink=None):context_sink("fintraffic",[row(3)]);return 0
    monkeypatch.setattr(collect_ais.ow,"collect",collect)
    monkeypatch.setattr(collect_ais.ow,"publish",lambda results:0)
    monkeypatch.setattr(collect_ais.regional,"main",regional_main)
    import merge_ais_contacts as broker
    output=tmp_path/"monitored.json";output.write_text(json.dumps({"contacts":[row(1)]}))
    monkeypatch.setattr(broker,"OUTPUT_PATH",output);monkeypatch.setattr(broker,"main",lambda:0)
    assert asyncio.run(collect_ais.run())==0
    assert {r["mmsi"] for r in unpack(json.loads(target.read_text()))}=={"200000002","200000003"}
    assert len(json.loads(output.read_text())["contacts"])==1
    assert json.loads(target.read_text())["providers"]["openwaters"]["state"]=="partial"


def test_browser_context_model_guards_and_clustering(tmp_path):
    context=traffic.TrafficContext(NOW)
    context.add_source("openwaters",[row(1,imo="9179842",upstream_source="<img src=x onerror=alert(1)>"),row(2)])
    path=tmp_path/"traffic.json";context.publish(path=path)
    script=r'''
const assert=require('node:assert/strict'),fs=require('node:fs');
const api=require('./public/assets/traffic-context.js'),vm=require('./public/assets/vessel-model.js');
const now=Date.parse('2026-11-10T12:00:00Z'),pack=JSON.parse(fs.readFileSync(process.argv[1]));
const rows=api.decode(pack,now);assert.equal(rows.length,2);
assert.equal(api.visibleRows(rows,[{imo:'9179842',mmsi:'200000099'}],vm,now).length,1);
assert.equal(api.visibleRows(rows,[{mmsi:'200000002'}],vm,now).length,1);
assert.equal(api.aggregate(rows,()=>({x:1,y:1}),5).length,1);
assert.equal(api.aggregate(rows,()=>({x:1,y:1}),8).length,2);
assert.equal(api.aggregate([rows[0],{...rows[1],source_provider:'fintraffic'}],()=>({x:1,y:1}),5).length,2);
assert.throws(()=>api.decode(pack,now+25*3600000));
assert.throws(()=>api.decode({...pack,context_only:false},now));
assert.equal(api.decode({...pack,contacts:[['000000000',...pack.contacts[0].slice(1)]]},now).length,0);
const html=api.popup(rows.find(p=>p.upstream_source),vm);assert(!html.includes('<img'));assert(html.includes('&lt;img'));
assert(html.includes('target="_blank"')&&html.includes('noopener noreferrer'));
assert.equal(api.visibleRows(rows,[],vm,now+25*3600000).length,0);
console.log('browser traffic model passed');
'''
    result=subprocess.run(["node","-e",script,str(path)],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_manifest_only_links_existing_context_and_guard_enforces_byte_limit(tmp_path,monkeypatch):
    import build_public_manifest as manifest
    import repo_storage_guard as guard
    monkeypatch.setattr(manifest,"PUBLIC",tmp_path/"public")
    manifest.main()
    target=tmp_path/"public/data/vessels/traffic_context_latest.json"
    assert "traffic_context" not in json.loads((tmp_path/"public/data/manifest.json").read_text())
    traffic.TrafficContext(NOW).publish(path=target);manifest.main()
    assert json.loads((tmp_path/"public/data/manifest.json").read_text())["traffic_context"].endswith("traffic_context_latest.json")
    def staged(args,**kwargs):
        return b'public/data/vessels/traffic_context_latest.json\0' if args[1]=='diff' else str(1024*1024+1).encode()
    monkeypatch.setattr(guard.subprocess,"check_output",staged)
    with pytest.raises(ValueError,match='Traffic context'):guard.inspect_staged(tmp_path)
