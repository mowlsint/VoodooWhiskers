#!/usr/bin/env python3
"""Maintain bounded position and source-coverage history for the common snapshot.

Only already-filtered Voodoo products are archived. Danish raw ZIP/CSV files and
unfiltered provider payloads are never persisted by this script.
"""
from __future__ import annotations
import json, math, os, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
CONFIG_PATH=ROOT/'config'/'common_snapshot.json'
HISTORY_PATH=DATA/'history'/'maritime_source_history_21d.jsonl'
COVERAGE_PATH=DATA/'history'/'maritime_source_coverage_21d.jsonl'
STATUS_PATH=DATA/'history'/'maritime_source_history_status_latest.json'
SOURCE_FILES={
 'fintraffic':DATA/'ais_contacts_fintraffic_latest.json',
 'barentswatch':DATA/'ais_contacts_barentswatch_latest.json',
 'ais_dk_historical':DATA/'ais_contacts_aisdk_historical_latest.json',
}

def utc_now(): return datetime.now(timezone.utc).replace(microsecond=0)
def iso_z(v): return v.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def parse_dt(v):
 raw=str(v or '').strip()
 if not raw:return None
 try:d=datetime.fromisoformat(raw.replace('Z','+00:00'))
 except ValueError:return None
 if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
 return d.astimezone(timezone.utc).replace(microsecond=0)
def finite(v):
 try:n=float(v)
 except (TypeError,ValueError):return None
 return n if math.isfinite(n) else None
def digits(v):return ''.join(c for c in str(v or '') if c.isdigit())
def clean(v):return ' '.join(str(v or '').strip().split())
def identity(item):
 imo=digits(item.get('imo'));mmsi=digits(item.get('mmsi'))
 if len(imo)==7:return f'imo:{imo}'
 if len(mmsi)==9:return f'mmsi:{mmsi}'
 callsign=clean(item.get('callsign')).upper()
 if callsign:return f'callsign:{callsign}'
 name=clean(item.get('name')).upper()
 return f'name:{name}' if name else ''
def load_json(path):return json.loads(path.read_text(encoding='utf-8'))
def atomic_text(path,text):
 path.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as h:
  h.write(text);tmp=h.name
 Path(tmp).replace(path)
def atomic_json(path,payload):atomic_text(path,json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
def read_jsonl(path):
 out=[]
 if not path.exists():return out
 for line in path.read_text(encoding='utf-8').splitlines():
  if not line.strip():continue
  try:r=json.loads(line)
  except json.JSONDecodeError:continue
  if isinstance(r,dict):out.append(r)
 return out
def encode(rows):return ''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows)
def iter_positions(payload,provider):
 generated=payload.get('generated_at')
 for contact in payload.get('contacts') or []:
  if not isinstance(contact,dict):continue
  positions=contact.get('positions') if isinstance(contact.get('positions'),list) and contact.get('positions') else [contact]
  for pos in positions:
   if not isinstance(pos,dict):continue
   merged=dict(contact);merged.update(pos);merged['_generated_at']=generated;merged['provider']=provider
   yield merged
def normalize(item,provider):
 observed=parse_dt(item.get('observed_at') or item.get('last_seen_utc') or item.get('timestamp') or item.get('_generated_at'))
 lat=finite(item.get('latitude'));lon=finite(item.get('longitude'));ident=identity(item)
 if not observed or lat is None or lon is None or not ident:return None
 if not(-90<=lat<=90 and -180<=lon<=180):return None
 return {
  'record_type':'position','schema_version':'1.0.0','provider':provider,'identity_key':ident,
  'mmsi':digits(item.get('mmsi')) or None,'imo':digits(item.get('imo')) or None,
  'callsign':clean(item.get('callsign')) or None,'name':clean(item.get('name')) or None,
  'latitude':lat,'longitude':lon,'observed_at':iso_z(observed),
  'snapshot_generated_at':item.get('_generated_at'),'timestamp_valid':item.get('position_timestamp_valid') is not False,
  'timestamp_basis':clean(item.get('position_timestamp_basis')) or 'source_or_snapshot_timestamp',
  'position_is_exact':provider!='global_fishing_watch','sog':item.get('sog'),'cog':item.get('cog'),
  'true_heading':item.get('true_heading'),'navigational_status':item.get('navigational_status'),
  'destination':clean(item.get('destination')) or None,'ship_type':item.get('ship_type'),
  'source':clean(item.get('source')) or provider,
 }
def coverage_time(payload,records):
 for key in ('data_max_timestamp_utc','source_data_max_timestamp_utc','max_observed_at','generated_at'):
  d=parse_dt(payload.get(key))
  if d:return d,key
 vals=[parse_dt(r.get('observed_at')) for r in records];vals=[v for v in vals if v]
 return (max(vals),'max_normalized_observation') if vals else (None,None)
def pos_key(r):return (r.get('provider'),r.get('identity_key'),r.get('observed_at'),round(float(r['latitude']),6),round(float(r['longitude']),6))
def cov_key(r):return (r.get('provider'),r.get('coverage_at'),r.get('source_generated_at'))

def main():
 cfg=json.loads(CONFIG_PATH.read_text(encoding='utf-8')) if CONFIG_PATH.exists() else {}
 keep=max(7,int(os.environ.get('COMMON_HISTORY_RETENTION_DAYS',cfg.get('history_retention_days',21))))
 max_bytes=max(1_000_000,int(os.environ.get('COMMON_HISTORY_MAX_BYTES',cfg.get('history_max_bytes',23068672))))
 now=utc_now();cutoff=now-timedelta(days=keep)
 positions=read_jsonl(HISTORY_PATH);coverages=read_jsonl(COVERAGE_PATH);source_status={};added=0
 for provider,path in SOURCE_FILES.items():
  if not path.exists():source_status[provider]={'ok':False,'reason':'source_file_missing','path':str(path.relative_to(ROOT))};continue
  try:
   payload=load_json(path);records=[r for item in iter_positions(payload,provider) if (r:=normalize(item,provider))]
   positions.extend(records);added+=len(records);cov,cov_basis=coverage_time(payload,records)
   if cov:
    coverages.append({'record_type':'source_coverage','schema_version':'1.0.0','provider':provider,
      'coverage_at':iso_z(cov),'coverage_basis':cov_basis,'archived_at':iso_z(now),
      'source_generated_at':payload.get('generated_at'),'contact_count':len(records),'source_path':str(path.relative_to(ROOT))})
   source_status[provider]={'ok':True,'path':str(path.relative_to(ROOT)),'source_generated_at':payload.get('generated_at'),
      'coverage_at':iso_z(cov) if cov else None,'coverage_basis':cov_basis,'records_seen':len(records)}
  except Exception as exc:source_status[provider]={'ok':False,'reason':f'{type(exc).__name__}: {exc}','path':str(path.relative_to(ROOT))}
 pos_dedup={};dropped_old=0
 for r in positions:
  d=parse_dt(r.get('observed_at'))
  if not d or d<cutoff or d>now+timedelta(days=1):dropped_old+=1;continue
  pos_dedup[pos_key(r)]=r
 rows=sorted(pos_dedup.values(),key=lambda r:str(r.get('observed_at') or ''))
 text=encode(rows);dropped_size=0
 while len(text.encode())>max_bytes and rows:
  n=max(1,len(rows)//20);rows=rows[n:];dropped_size+=n;text=encode(rows)
 cov_dedup={}
 for r in coverages:
  d=parse_dt(r.get('coverage_at'))
  if d and cutoff<=d<=now+timedelta(days=1):cov_dedup[cov_key(r)]=r
 cov_rows=sorted(cov_dedup.values(),key=lambda r:str(r.get('coverage_at') or ''))
 atomic_text(HISTORY_PATH,text);atomic_text(COVERAGE_PATH,encode(cov_rows))
 provider_counts={};ranges={};coverage_ranges={}
 for r in rows:
  p=str(r.get('provider') or 'unknown');provider_counts[p]=provider_counts.get(p,0)+1
  e=ranges.setdefault(p,{'min':None,'max':None});o=str(r.get('observed_at') or '')
  e['min']=o if e['min'] is None or o<e['min'] else e['min'];e['max']=o if e['max'] is None or o>e['max'] else e['max']
 for r in cov_rows:
  p=str(r.get('provider') or 'unknown');e=coverage_ranges.setdefault(p,{'min':None,'max':None});o=str(r.get('coverage_at') or '')
  e['min']=o if e['min'] is None or o<e['min'] else e['min'];e['max']=o if e['max'] is None or o>e['max'] else e['max']
 status={'schema_version':'1.0.0','generated_at':iso_z(now),'retention_days':keep,'max_bytes':max_bytes,
  'position_history_path':str(HISTORY_PATH.relative_to(ROOT)),'coverage_history_path':str(COVERAGE_PATH.relative_to(ROOT)),
  'position_records':len(rows),'coverage_records':len(cov_rows),'records_added_from_current_snapshots':added,
  'dropped_old_or_invalid':dropped_old,'dropped_for_size':dropped_size,'provider_counts':provider_counts,
  'provider_position_ranges':ranges,'provider_coverage_ranges':coverage_ranges,'sources':source_status,'raw_provider_files_persisted':False}
 atomic_json(STATUS_PATH,status);print(json.dumps(status,ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
