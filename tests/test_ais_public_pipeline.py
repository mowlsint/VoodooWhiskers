"""Reproducible offline producer build; generated products stay in pytest's temp dir."""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def test_native_sample_to_public_products_and_retention(tmp_path):
    source=Path(__file__).resolve().parents[1]
    root=tmp_path/'VoodooWhiskers'
    for folder in ('scripts','config'):
        shutil.copytree(source/folder,root/folder,ignore=shutil.ignore_patterns('__pycache__'))
    data=root/'data';data.mkdir()
    now=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    (data/'watchlist_master.csv').write_text('name,imo,mmsi,track_sanctions,track_shadowfleet\nOFFLINE FIXTURE,9179842,273123456,true,false\n')
    script='''import json,sys
from pathlib import Path
sys.path.insert(0,'scripts')
import fetch_openwaters as ow
now=sys.argv[1]
event={'type':'event','id':'fixture','time':now,'mmsi':273123456,'msg_type':'PositionReport','source':'kystverket','station':'fixture-station','lat':54.5,'lon':12.1,'message':{'Sog':5.1,'Cog':90,'TrueHeading':90,'Valid':True}}
row=ow.normalize_event(event,now)
row.update({'name':'OFFLINE FIXTURE','imo':'9179842','stream_id':'stream_1','positions':[row.copy()]})
assert ow.publish([([row],{'id':'stream_1','ok':True,'no_events':False}),([] ,{'id':'stream_2','ok':True,'no_events':False})])==0
Path('data/ais_contacts_fintraffic_latest.json').write_text(json.dumps({'generated_at':now,'source':'Fintraffic Digitraffic','contacts':[{**row,'source_provider':'fintraffic','imo':''}]}))
'''
    result=subprocess.run([sys.executable,'-c',script,now],cwd=root,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    commands=[['merge_ais_contacts.py'],['archive_maritime_snapshots.py'],['build_layers.py'],
              ['test_voi_history_retention.py','--source-only'],['build_public_outputs.py'],
              ['analyze_infrastructure_proximity.py'],['build_common_maritime_snapshot.py'],['build_public_manifest.py']]
    for script,*args in commands:
        result=subprocess.run([sys.executable,f'scripts/{script}',*args],cwd=root,capture_output=True,text=True)
        assert result.returncode==0,f'{script}: {result.stderr}'
    manifest=root/'public/downloads/manifest.json'
    pack=json.loads(manifest.read_text())
    assert pack['products']
    for product in pack['products']:
        href=product['href'];target=(manifest.parent/href).resolve()
        assert '://' not in href and not href.startswith('/') and target.parent==manifest.parent.resolve()
        assert target.exists() and target.stat().st_size==product['size_bytes']>0
        if target.suffix in ('.json','.geojson'):json.loads(target.read_text())
    public=json.loads((root/'public/data/vessels/ais_contacts_latest.json').read_text())
    assert len(public['contacts'])==1
    contact=public['contacts'][0]
    assert contact['position_timestamp_valid'] and contact['observed_at']==now
    assert contact['upstream_source']=='kystverket'
