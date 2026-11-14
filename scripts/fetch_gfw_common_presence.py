#!/usr/bin/env python3
"""Manual/probe-first fetch of delayed GFW AIS Vessel Presence for common regions.

The query retains all identifiable vessel rows returned by GFW, then reduces them
to at most two hourly report cells per identity. A cap or failed region query marks
the product incomplete, so it can never turn the common snapshot green.
"""
from __future__ import annotations
import json,math,os,tempfile,time
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from typing import Any
import requests
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'data'
CONFIG_PATH=ROOT/'config'/'common_snapshot.json';REGION_PATH=DATA/'common_snapshot_regions.geojson'
OUTPUT_PATH=DATA/'gfw_historical_ais_latest.json';STATUS_PATH=DATA/'gfw_historical_ais_status_latest.json'
API_REPORT='https://gateway.api.globalfishingwatch.org/v3/4wings/report';API_LAST_REPORT='https://gateway.api.globalfishingwatch.org/v3/4wings/last-report'
DATASET='public-global-presence:latest';ATTRIBUTION='Powered by Global Fishing Watch.'
def utc_now():return datetime.now(timezone.utc).replace(microsecond=0)
def iso_z(v):return v.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def clean(v):return ' '.join(str(v or '').strip().split())
def digits(v):return ''.join(c for c in str(v or '') if c.isdigit())
def finite(v):
 try:n=float(v)
 except (TypeError,ValueError):return None
 return n if math.isfinite(n) else None
def parse_dt(v):
 raw=clean(v)
 if not raw:return None
 try:d=datetime.fromisoformat(raw.replace('Z','+00:00'))
 except ValueError:return None
 if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
 return d.astimezone(timezone.utc).replace(microsecond=0)
def observed(row):
 for k in ('entryTimestamp','exitTimestamp','observed_at','timestamp','date'):
  if d:=parse_dt(row.get(k)):return d
 return None
def atomic_json(path,payload):
 path.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as h:
  json.dump(payload,h,ensure_ascii=False,separators=(',',':'));h.write('\n');tmp=h.name
 Path(tmp).replace(path)
def regions():
 p=json.loads(REGION_PATH.read_text(encoding='utf-8'));out=[]
 for i,f in enumerate(p.get('features') or []):
  g=f.get('geometry') or {};pr=f.get('properties') or {}
  if g.get('type') in {'Polygon','MultiPolygon'}:out.append({'id':clean(pr.get('id')) or f'region_{i+1}','name':clean(pr.get('name')) or f'region_{i+1}','geometry':g})
 if not out:raise RuntimeError('no common snapshot polygons')
 return out
def flatten(payload):
 resolved=None;rows=[]
 for entry in payload.get('entries') or []:
  if not isinstance(entry,dict):continue
  for key,val in entry.items():
   if not isinstance(val,list):continue
   if str(key).startswith('public-global-presence:'):resolved=str(key)
   for r in val:
    if isinstance(r,dict):c=dict(r);c['_source_dataset']=str(key);rows.append(c)
 return resolved,rows
def finished(p):return isinstance(p,dict) and isinstance(p.get('entries'),list)
class Client:
 def __init__(self,token,request_timeout,poll_timeout,poll_interval):
  self.rt=request_timeout;self.pt=poll_timeout;self.pi=poll_interval;self.s=requests.Session();self.s.headers.update({'Authorization':f'Bearer {token}','Content-Type':'application/json','User-Agent':'VoodooWhiskers-CommonSnapshot/0.1.1'})
 def poll(self):
  deadline=time.monotonic()+self.pt
  while time.monotonic()<deadline:
   r=self.s.get(API_LAST_REPORT,timeout=min(self.rt,60))
   if r.status_code==404:time.sleep(self.pi);continue
   r.raise_for_status();p=r.json()
   if finished(p):return p
   if isinstance(p,dict) and p.get('status') not in {None,'running'}:raise RuntimeError(f'GFW report failed: {p}')
   time.sleep(self.pi)
  raise TimeoutError('GFW common presence report did not finish')
 def report(self,geometry,start,end):
  params=[('spatial-resolution','HIGH'),('temporal-resolution','HOURLY'),('spatial-aggregation','false'),('datasets[0]',DATASET),('date-range',f'{start.isoformat()},{end.isoformat()}'),('format','JSON'),('group-by','FLAG')]
  for attempt in range(2):
   try:r=self.s.post(API_REPORT,params=params,json={'geojson':geometry},timeout=self.rt)
   except requests.Timeout:return self.poll()
   if r.status_code==524:return self.poll()
   if r.status_code==429:
    if attempt==0:
     try:self.poll()
     except Exception:pass
     time.sleep(5);continue
    raise RuntimeError('GFW report busy (HTTP 429)')
   r.raise_for_status();p=r.json()
   if finished(p):return p
   if isinstance(p,dict) and p.get('status')=='running':return self.poll()
   raise RuntimeError(f'unexpected GFW response: {p}')
  raise RuntimeError('GFW report retry exhausted')
def normalize(row,region):
 t=observed(row);lat=finite(row.get('lat'));lon=finite(row.get('lon'));vid=clean(row.get('vesselId') or row.get('vessel_id'))
 imo=digits(row.get('imo'));mmsi=digits(row.get('mmsi'))
 ident=f'imo:{imo}' if len(imo)==7 else f'mmsi:{mmsi}' if len(mmsi)==9 else f'gfw:{vid}' if vid else ''
 if not t or lat is None or lon is None or not ident or not(-90<=lat<=90 and -180<=lon<=180):return None
 return {'provider':'global_fishing_watch','identity_key':ident,'gfw_vessel_id':vid or None,'imo':imo if len(imo)==7 else None,'mmsi':mmsi if len(mmsi)==9 else None,
  'callsign':clean(row.get('callsign')) or None,'name':clean(row.get('shipName') or row.get('ship_name')) or None,'flag':clean(row.get('flag')) or None,
  'vessel_type':clean(row.get('vesselType') or row.get('vessel_type')) or None,'latitude':lat,'longitude':lon,'observed_at':iso_z(t),
  'presence_hours':finite(row.get('hours')),'source_dataset':clean(row.get('_source_dataset')) or DATASET,'region_id':region['id'],'region_name':region['name'],
  'historical':True,'not_current_position':True,'position_is_exact':False,'location_representation':'0.01_degree_grid_cell_center'}
def main():
 # Preserve the legacy entry point while using the supported anonymous grid query.
 from fetch_gfw_context import main as context_main
 return context_main(['--presence-only'])
if __name__=='__main__':raise SystemExit(main())
