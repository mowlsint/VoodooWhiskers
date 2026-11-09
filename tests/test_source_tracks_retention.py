import io
import json
import subprocess
import sys
import zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import data_retention as retention
import sar_artifact_store as artifacts
import sar_workbench as workbench
import repo_storage_guard as storage
import vessel_identity
from test_sar_workbench import request,scene,SCENE,PASSWORD
import sar_crypto


def test_identity_bridges_missing_imo_but_never_conflicting_imos():
    rows=[{'imo':'9179842','mmsi':'273123456','provider':'barentswatch'},
          {'mmsi':'273123456','provider':'openwaters'},
          {'imo':'9179842','mmsi':'273123457','provider':'fintraffic'}]
    assert {p['vessel_identity'] for p in vessel_identity.resolve(rows)}=={'imo:9179842'}
    conflict=vessel_identity.resolve(rows+[{'imo':'9915105','mmsi':'273123456'}])
    assert conflict[0]['identity_conflict'] and conflict[1]['vessel_identity']=='mmsi:273123456'
    assert len({p['vessel_identity'] for p in conflict})==3


def test_context_pruning_uses_observation_not_download_time():
    now=datetime(2026,11,9,tzinfo=timezone.utc)
    pack={'generated_at':now.isoformat(),'historical':True,'events':[
        {'id':'old','type':'LOITERING','start':(now-timedelta(days=18)).isoformat(),'end':(now-timedelta(days=15)).isoformat()},
        {'id':'overlap','type':'LOITERING','start':(now-timedelta(days=18)).isoformat(),'end':(now-timedelta(days=13)).isoformat()},
        {'id':'new','type':'LOITERING','start':(now-timedelta(days=2)).isoformat(),'end':(now-timedelta(days=1)).isoformat()}]}
    pack['events'].append(pack['events'][2].copy())
    result=retention.bound_gfw(pack,'events',now=now)
    assert [e['id'] for e in result['events']]==['overlap','new']
    assert result['retention_dropped']==1 and result['count']==2
    bounded=retention.bound_gfw(pack,'events',now=now,max_bytes=200)
    assert bounded['size_dropped']>0 and bounded['cap_applied']


def test_event_time_is_used_for_actual_catalog_query():
    now=workbench.utc();begin=now-timedelta(days=8);end=now-timedelta(days=7)
    cfg=workbench.load_config();r=workbench.validate_request(request(action='search',range_from=workbench.iso(begin),range_to=workbench.iso(end),event_id='loiter-test'),cfg)
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,*args):yield b'{"features":[]}'
    class Session:
        params=None
        def get(self,*args,**kwargs):self.params=kwargs['params'];return Response()
    session=Session();result=workbench.search_scenes(r,cfg,session=session)
    assert session.params['datetime']==workbench.iso(begin)+'/'+workbench.iso(end)
    assert result['scenes']==[] and not result['raw_raster_downloaded']
    with pytest.raises(workbench.WorkbenchError):workbench.validate_request(request(range_from=workbench.iso(now-timedelta(days=20)),range_to=workbench.iso(end),event_id='bad'),cfg)


def test_budget_guard_stops_before_repository_growth():
    policy=json.loads((ROOT/'config/storage_policy.json').read_text())
    storage.check(policy,300*1024*1024,10*1024*1024,100*1024*1024)
    for values in [(750*1024*1024,1,0),(0,140*1024*1024,0),(0,0,300*1024*1024)]:
        with pytest.raises(ValueError):storage.check(policy,*values)


def archive(data,name='workbench.enc.json',compression=zipfile.ZIP_DEFLATED):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',compression=compression) as z:z.writestr(name,data)
    return b.getvalue()


def test_artifact_restore_rejects_other_paths_and_plaintext():
    envelope=json.dumps(sar_crypto.encrypt({'v':1,'jobs':[]},PASSWORD,'workspace')).encode()
    assert artifacts.unpack(archive(envelope))==envelope
    for data in (archive(envelope,'../workbench.enc.json'),archive(b'{"v":1,"jobs":[]}')):
        with pytest.raises(ValueError):artifacts.unpack(data)


def test_artifact_budget_removes_only_older_managed_copies():
    class Response:
        def raise_for_status(self):pass
    class Session:
        deleted=[]
        def delete(self,url,**kwargs):self.deleted.append(url);return Response()
    session=Session();store=artifacts.Store('mowlsint/VoodooWhiskers','offline-test-token',session=session)
    items=[{'id':i,'name':artifacts.NAME,'expired':False,'created_at':str(i),'workflow_run':{'head_branch':'main'},'size_in_bytes':24*1024*1024} for i in range(1,5)]
    items.append({'id':99,'name':'other-workflow','created_at':'0','expired':False,'size_in_bytes':1000})
    removed=store.reserve(items,24*1024*1024)
    assert removed==[1,2] and session.deleted==[store.base+'/actions/artifacts/1',store.base+'/actions/artifacts/2']
    assert 4 not in removed and 99 not in removed


def test_browser_track_model_and_artifact_import(tmp_path):
    envelope=sar_crypto.encrypt({'v':1,'jobs':[]},PASSWORD,'workspace')
    compressed=tmp_path/'compressed.zip';compressed.write_bytes(archive(json.dumps(envelope).encode()))
    stored=tmp_path/'stored.zip';stored.write_bytes(archive(json.dumps(envelope).encode(),compression=zipfile.ZIP_STORED))
    node=r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),VM=require('./public/assets/vessel-model.js');
(async()=>{
const now=Date.parse('2026-11-09T12:00:00Z');const row=(provider,t,lon,extra={})=>({provider,mmsi:'273123456',latitude:55,longitude:lon,observed_at:t,position_timestamp_valid:true,...extra});
const rows=[row('barentswatch','2026-11-09T00:00:00Z',12,{imo:'9179842'}),row('openwaters','2026-11-09T04:00:00Z',12.2),row('fintraffic','2026-11-09T08:00:00Z',12.4,{imo:'9179842'})];
const tracks=VM.track(rows,{now});assert.equal(tracks.length,1);assert.equal(tracks[0].segments.length,2);assert.equal(tracks[0].identity,'imo:9179842');assert.equal(tracks[0].segments[0].source,'openwaters');
assert.equal(VM.source({source_provider:'openwaters',sources:['Fintraffic']}),'openwaters');
assert(VM.sourceLabel({source_provider:'openwaters',upstream_source:'digitraffic'}).includes('digitraffic'));
assert.notEqual(VM.SOURCES.openwaters.color,VM.SOURCES.fintraffic.color);assert.notEqual(VM.SOURCES.fintraffic.color,VM.SOURCES.barentswatch.color);
const duplicate=VM.track([...rows,rows[1]],{now});assert.equal(duplicate[0].points.length,3);
const conflict=VM.track([...rows,row('openwaters','2026-11-09T10:00:00Z',12.5,{imo:'9915105'})],{now});assert(conflict.length>1);assert(conflict.every(m=>m.segments.length===0));
const gap=VM.track([rows[0],row('openwaters','2026-11-09T15:00:00Z',12.4)],{now:now+4*3600000});assert.equal(gap[0].segments.length,0);assert.equal(gap[0].gaps[0].reason,'observation_gap');
assert.equal(VM.track([row('global_fishing_watch','2026-11-09T04:00:00Z',12)],{now}).length,0);
assert.equal(VM.coordinates({latitude:null,longitude:12}),null);
assert.equal(VM.vesselFinder({imo:'9179842'}),'https://www.vesselfinder.com/vessels/details/9179842');
const event=VM.eventContext({id:'loiter',type:'LOITERING',position:{lat:55,lon:12},start:'2026-11-01T00:00:00Z',end:'2026-11-02T00:00:00Z',vessel:{name:'TEST',ssvid:'273123456'}},now);assert.equal(event.mmsi,'273123456');
const {eventFromHash}=await import('./public/assets/sar-workbench-core.mjs');const cfg=JSON.parse(fs.readFileSync('public/assets/sar-workbench-config.json'));
assert.equal(eventFromHash('#event='+encodeURIComponent(JSON.stringify(event)),cfg,now).event_id,'loiter');
const {readWorkspaceFile}=await import('./public/assets/sar-import.mjs');
for(const path of process.argv.slice(1)){const b=fs.readFileSync(path);const value=await readWorkspaceFile(new File([b],'artifact.zip'));assert.equal(value.purpose,'workspace');}
console.log('source, identity, gap, event and ZIP import checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result=subprocess.run(['node','-e',node,str(compressed),str(stored)],cwd=ROOT,text=True,capture_output=True)
    assert result.returncode==0,result.stderr


def test_source_history_total_limit_and_duplicate_slots(tmp_path):
    import voi_history
    now=datetime.now(timezone.utc)
    cfg=voi_history.load_policy(ROOT);cfg.update({'source_total_max_bytes':1800,'source_max_bytes':1800,'source_shard_max_bytes':1200})
    rows=[{'mmsi':'273123456','imo':'9179842','source_provider':'openwaters','latitude':55+i*.001,'longitude':12,'last_seen_utc':(now-timedelta(hours=i)).isoformat(),'position_timestamp_valid':True,'positions':[{'raw':'unneeded'}],'raw':'unneeded','_history_key':'old-slot-'+str(i)} for i in range(20)]
    rows.append({**rows[0],'_history_key':'other-slot-same-observation'})
    result=voi_history.compact_source_history(tmp_path/'voi_history.jsonl',rows,cfg,tmp_path/'status.json',now=now)
    manifest=json.loads((tmp_path/'voi_history_14d/manifest.json').read_text())
    assert manifest['total_bytes']<=1800 and not manifest['complete_time_window']
    assert result['duplicate_rows']==1 and result['dropped_for_size']>0
    for part in (tmp_path/'voi_history_14d').glob('*.jsonl'):
        for line in part.read_text().splitlines():
            row=json.loads(line);assert 'positions' not in row and 'raw' not in row


def test_old_gfw_comparison_drops_nested_and_expired_positions(tmp_path,monkeypatch):
    import build_common_maritime_snapshot as common
    monkeypatch.setattr(common,'ROOT',tmp_path)
    path=tmp_path/'gfw.json';status=tmp_path/'status.json'
    monkeypatch.setattr(common,'GFW_PATH',path);monkeypatch.setattr(common,'GFW_STATUS_PATH',status)
    now=common.now()
    path.write_text(json.dumps({'contacts':[{'mmsi':'273123456','positions':[
        {'observed_at':common.iso(now-timedelta(days=15)),'latitude':55,'longitude':12},
        {'observed_at':common.iso(now-timedelta(days=2)),'latitude':56,'longitude':13}]}],'coverage_complete':True}))
    rows,meta=common.gfw_data()
    assert len(rows)==1 and rows[0]['latitude']==56 and 'positions' not in rows[0]
    assert json.loads(path.read_text())['retention_days']==14
