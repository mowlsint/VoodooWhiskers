"""Offline workbench tests: dynamic scenes, real CFAR, authenticated encryption and bounds."""
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'tests'))
import sar_workbench as workbench
import sar_crypto
from test_copernicus_sar_cfar import synthetic_geotiff,FakeSession,FIXTURE

PASSWORD='offline-ONLY-6fcN7k'
SCENE='S1D_IW_GRDH_1SDV_20261101T052402_20261101T052427_009016_011E61_438E'


def request(action='analyse',**changes):
    return {'v':1,'id':str(uuid.uuid4()),'created_at':workbench.iso(workbench.utc()),'action':action,
            'area_id':'kadetrinne','days':14,'scene_id':SCENE,'gfw_context':False,'danish_ais':False,**changes}


def scene():
    return {'item_id':SCENE+'_COG','scene_id':SCENE,'datetime':'2026-11-01T05:24:02Z',
            'platform':'sentinel-1d','orbit_direction':'descending','processable':True,'covers_aoi':True,
            'footprint':{'type':'Polygon','coordinates':[[[11,54],[13,54],[13,56],[11,56],[11,54]]]}}


def land_archive():
    import shapefile
    shp,shx,dbf=io.BytesIO(),io.BytesIO(),io.BytesIO()
    writer=shapefile.Writer(shp=shp,shx=shx,dbf=dbf,shapeType=shapefile.POLYGON)
    writer.field('name','C');writer.poly([[[0,0],[0,1],[1,1],[1,0],[0,0]]]);writer.record('fixture land');writer.close()
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:
        for suffix,value in [('shp',shp),('shx',shx),('dbf',dbf)]:z.writestr('land.'+suffix,value.getvalue())
    return archive.getvalue()


def synthetic_job():
    cfg=workbench.load_config();raw=request();r=workbench.validate_request(raw,cfg)
    # Small raster fixture has the same geographic bounds and explicit GeoTIFF transform.
    r['width_px']=r['height_px']=128
    archive=land_archive();cfg['land_archive_sha256']=hashlib.sha256(archive).hexdigest()
    fixture=copy.deepcopy(FIXTURE)
    fixture['sentinel_hub']['features'][0]['id']=SCENE
    fixture['sentinel_hub']['features'][0]['properties']['datetime']=scene()['datetime']
    fake=FakeSession(fixture,synthetic_geotiff())
    def detect(r,s,c,client_id,client_secret):
        return workbench.acquire_and_detect(r,s,c,'fixture-client','fixture-secret',binary_session=fake,land_archive=archive)
    workspace={'v':1,'jobs':[],'analysis_ledger':{}}
    job=workbench.run_task(r,cfg,workspace,search=lambda *args:{'scenes':[scene()]},detector=detect,history=[])
    job['is_fixture']=True
    return cfg,workspace,job,fake


def test_dynamic_pipeline_runs_actual_cfar_without_public_plaintext(tmp_path):
    cfg,workspace,job,fake=synthetic_job()
    assert job['state']=='ready' and job['scene']['platform']=='sentinel-1d'
    assert job['processing_performed'] is True and job['context']['features']
    assert all(f['properties']['platform']=='sentinel-1d' for f in job['context']['features'])
    assert all(f['properties']['ais_match_status']=='NO_AIS_COVERAGE' for f in job['context']['features'])
    assert not job['candidates']['metadata']['raw_raster_retained']
    assert len(fake.calls)==3
    path=tmp_path/'workbench.enc.json'
    workbench.save_workspace(workspace,job,PASSWORD,cfg,path)
    ciphertext=path.read_text()
    for secret in (SCENE,'kadetrinne','NO_AIS_COVERAGE','fixture-secret','fixture-client','data:image/png'):
        assert secret not in ciphertext
    decoded=sar_crypto.decrypt(json.loads(ciphertext),PASSWORD,'workspace')
    assert decoded['jobs'][0]['images']['quicklook'].startswith('data:image/png;base64,')


@pytest.mark.parametrize('change',[
    {'bbox':[11,54,30,70],'area_id':'custom'}, {'bbox':[float('nan'),54,12,55],'area_id':'custom'},
    {'bbox':[12,55,11,54],'area_id':'custom'}, {'days':True},{'days':31},{'scene_id':'../../secrets'},
    {'action':'shell'},{'id':'$(env)'},{'danish_ais':'true'}, {'bbox':[0,0,1,1]},
    {'created_at':'2020-01-01T00:00:00Z'},{'area_id':'custom','bbox':[-70,50,-69.9,50.1]},
])
def test_invalid_requests_rejected_before_provider_access(change):
    with pytest.raises(workbench.WorkbenchError):workbench.validate_request(request(**change),workbench.load_config())


def test_footprint_and_platform_are_checked():
    feature={'id':SCENE+'_COG','properties':{'sar:instrument_mode':'IW','sar:polarizations':['VV','VH'],
        'start_datetime':scene()['datetime'],'platform':'sentinel-1d','sat:orbit_state':'descending'},'geometry':scene()['footprint']}
    normalized=workbench.normalize_scene(feature,[11.95,54.48,12.25,54.66])
    assert normalized['processable'] and normalized['platform']=='sentinel-1d'
    assert not workbench.normalize_scene(feature,[11.95,54.48,13.25,54.66])['processable']
    feature['properties']['start_datetime']='2026-11-02T05:24:02Z'
    assert workbench.normalize_scene(feature,[11.95,54.48,12.25,54.66]) is None


def test_daily_budget_and_replay_guard():
    cfg=workbench.load_config();r=workbench.validate_request(request(),cfg)
    ws={'jobs':[],'analysis_ledger':{workbench.utc().date().isoformat():3}}
    with pytest.raises(workbench.WorkbenchError,match='DAILY_ANALYSIS_LIMIT'):
        workbench.run_task(r,cfg,ws,search=lambda *args:{'scenes':[scene()]})
    ws={'jobs':[{'request_id':r['id']}],'analysis_ledger':{}}
    with pytest.raises(workbench.WorkbenchError,match='REQUEST_ALREADY_COMPLETED'):workbench.run_task(r,cfg,ws)


def test_missing_credentials_do_not_spend_local_budget(monkeypatch):
    monkeypatch.delenv('CDSE_SH_CLIENT_ID',raising=False);monkeypatch.delenv('CDSE_SH_CLIENT_SECRET',raising=False)
    cfg=workbench.load_config();r=workbench.validate_request(request(),cfg);ws={'jobs':[],'analysis_ledger':{}}
    with pytest.raises(workbench.WorkbenchError,match='COPERNICUS_CREDENTIALS_MISSING'):
        workbench.run_task(r,cfg,ws,search=lambda *args:{'scenes':[scene()]})
    assert ws['analysis_ledger']=={}


def test_context_refresh_does_not_download_new_raster():
    cfg,ws,job,_=synthetic_job();ws['jobs']=[job]
    r=workbench.validate_request(request(action='refresh_context'),cfg)
    def forbidden(*args):raise AssertionError('Raster or catalogue unexpectedly called')
    refreshed=workbench.run_task(r,cfg,ws,search=forbidden,detector=forbidden,history=[])
    assert refreshed['state']=='ready' and not refreshed['processing_performed']


def test_optional_context_failure_keeps_radar_result():
    cfg=workbench.load_config();r=workbench.validate_request(request(),cfg)
    payload={'type':'FeatureCollection','metadata':{'scene':{'normalized_scene_id':SCENE,'datetime':scene()['datetime']}},'features':[]}
    def failed(*args):raise workbench.WorkbenchError('DANISH_ARCHIVE_UNAVAILABLE')
    job=workbench.run_task(r,cfg,{'jobs':[]},search=lambda *args:{'scenes':[scene()]},detector=lambda *args:(payload,{}),matcher=failed,history=[])
    assert job['state']=='ready' and job['context']['metadata']['optional_context_error']=='DANISH_ARCHIVE_UNAVAILABLE'


def test_authenticated_encryption_rejects_wrong_password_tampering_and_purpose():
    value=sar_crypto.encrypt({'bbox':[11.95,54.48,12.25,54.66],'scene':SCENE},PASSWORD,'request')
    assert sar_crypto.decrypt(value,PASSWORD,'request')['scene']==SCENE
    for pwd,purpose in [('wrong-password-123','request'),(PASSWORD,'workspace')]:
        with pytest.raises(Exception):sar_crypto.decrypt(value,pwd,purpose)
    changed=copy.deepcopy(value);changed['ciphertext']='A'+changed['ciphertext'][1:]
    if changed['ciphertext']==value['ciphertext']:changed['ciphertext']='B'+changed['ciphertext'][1:]
    with pytest.raises(Exception):sar_crypto.decrypt(changed,PASSWORD,'request')
    downgraded={**value,'iterations':1}
    with pytest.raises(ValueError):sar_crypto.decrypt(downgraded,PASSWORD,'request')
    assert sar_crypto.encrypt({'x':1},PASSWORD,'request')!=sar_crypto.encrypt({'x':1},PASSWORD,'request')


def test_python_browser_crypto_interoperability():
    node=r'''import {encryptObject,decryptObject} from './public/assets/sar-crypto.mjs';
const input=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.setEncoding('utf8');process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>resolve(s));}));
const p=await decryptObject(input.envelope,input.password,'request');
if(p.scene!==input.scene)throw new Error('Python→browser mismatch');
console.log(JSON.stringify(await encryptObject(p,input.password,'workspace')));'''
    value={'scene':SCENE,'note':'Prüfung mit Umlauten ✓'}
    envelope=sar_crypto.encrypt(value,PASSWORD,'request')
    result=subprocess.run(['node','--input-type=module','-e',node],input=json.dumps({'password':PASSWORD,'envelope':envelope,'scene':SCENE}),text=True,capture_output=True,cwd=ROOT)
    assert result.returncode==0,result.stderr
    assert sar_crypto.decrypt(json.loads(result.stdout),PASSWORD,'workspace')==value


def test_new_workflow_only_publishes_ciphertext_and_has_no_schedule():
    import yaml
    path=ROOT/'.github/workflows/sar-workbench.yml'
    workflow=yaml.load(path.read_text(),Loader=yaml.BaseLoader)
    assert set(workflow['on'])=={'workflow_dispatch'}
    text=path.read_text()
    assert "github.actor == github.repository_owner" in text
    assert 'git add -- "$result"' in text and 'git add public' not in text
    assert 'actions/upload-artifact' not in text
    assert 'SAR_WORKBENCH_PASSWORD: ${{ secrets.SAR_WORKBENCH_PASSWORD }}' in text


def test_ui_has_no_external_scripts_password_storage_or_plaintext_dispatch():
    html=(ROOT/'public/sar.html').read_text();js=(ROOT/'public/assets/sar-workbench.mjs').read_text()
    assert '<script src="http' not in html
    for forbidden in ('localStorage','sessionStorage','?password=','Authorization:'):
        assert forbidden not in js
    assert "encryptObject(request,state.password,'request')" in js
    assert "state.password=''" in js


def test_coast_buffer_includes_land_outside_chip():
    import numpy as np
    import shapefile
    shp,shx,dbf=io.BytesIO(),io.BytesIO(),io.BytesIO()
    writer=shapefile.Writer(shp=shp,shx=shx,dbf=dbf,shapeType=shapefile.POLYGON)
    writer.field('name','C')
    # Land starts 500 m east of the chip; its 1 km buffer must reach into it.
    writer.poly([[[.105,0],[.105,.1],[.2,.1],[.2,0],[.105,0]]]);writer.record('outside');writer.close()
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:
        for suffix,value in [('shp',shp),('shx',shx),('dbf',dbf)]:z.writestr('land.'+suffix,value.getvalue())
    data=archive.getvalue();cfg=workbench.load_config();cfg['land_archive_sha256']=hashlib.sha256(data).hexdigest()
    valid,meta=workbench.land_mask(np.ones((100,100),bool),[0,0,.1,.1],cfg,archive_bytes=data,pixel_m=100)
    assert valid[:,0].all() and not valid[:,99].any()
    assert meta['land_and_buffer_pixels']>0


def test_browser_geometry_and_image_guards():
    node=r'''import assert from 'node:assert/strict';
import {inspectBbox,normalizeScene,safeImage} from './public/assets/sar-workbench-core.mjs';
const input=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.setEncoding('utf8');process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>resolve(s));}));
assert(inspectBbox(input.bbox,input.config).area<500);
assert.throws(()=>inspectBbox([-1,50,2,55],input.config));
assert(normalizeScene(input.feature,input.bbox).processable);
assert.equal(normalizeScene(input.feature,[11,54,15,57]).processable,false);
assert.equal(safeImage('https://example.org/track.png'),'');
assert.equal(safeImage('data:image/svg+xml;base64,AAAA'),'');
assert.equal(safeImage('data:image/png;base64,AAAA'),'data:image/png;base64,AAAA');'''
    feature={'id':SCENE+'_COG','properties':{'sar:instrument_mode':'IW','sar:polarizations':['VV','VH'],
        'start_datetime':scene()['datetime'],'platform':'sentinel-1d','sat:orbit_state':'descending'},'geometry':scene()['footprint']}
    result=subprocess.run(['node','--input-type=module','-e',node],input=json.dumps({'config':workbench.load_config(),'bbox':[11.95,54.48,12.25,54.66],'feature':feature}),text=True,capture_output=True,cwd=ROOT)
    assert result.returncode==0,result.stderr
