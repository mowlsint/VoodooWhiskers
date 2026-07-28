#!/usr/bin/env python3
"""Build the canonical harmonized maritime snapshot used by both repositories."""
from __future__ import annotations
import csv,json,math,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'data';PUBLIC=ROOT/'public'/'data'/'vessels';DOWNLOADS=ROOT/'public'/'downloads'
CONFIG_PATH=ROOT/'config'/'common_snapshot.json';HISTORY_PATH=DATA/'history'/'maritime_source_history_21d.jsonl';COVERAGE_PATH=DATA/'history'/'maritime_source_coverage_21d.jsonl'
GFW_PATH=DATA/'gfw_historical_ais_latest.json';GFW_STATUS_PATH=DATA/'gfw_historical_ais_status_latest.json';GFW_FALLBACK=DATA/'sar_gfw_latest.json'
OUT=DATA/'maritime_common_snapshot_latest.json';STATUS=DATA/'maritime_common_snapshot_status_latest.json'
def now():return datetime.now(timezone.utc).replace(microsecond=0)
def iso(v):return v.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def parse(v):
 raw=str(v or '').strip()
 if not raw:return None
 try:d=datetime.fromisoformat(raw.replace('Z','+00:00'))
 except ValueError:return None
 if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
 return d.astimezone(timezone.utc).replace(microsecond=0)
def floor_hour(v):return v.astimezone(timezone.utc).replace(minute=0,second=0,microsecond=0)
def finite(v):
 try:n=float(v)
 except (TypeError,ValueError):return None
 return n if math.isfinite(n) else None
def clean(v):return ' '.join(str(v or '').strip().split())
def digits(v):return ''.join(c for c in str(v or '') if c.isdigit())
def truthy(v):return str(v or '').strip().lower() in {'1','true','yes','y','on'}
def ident(x):
 if clean(x.get('identity_key')):return clean(x.get('identity_key'))
 imo=digits(x.get('imo'));mmsi=digits(x.get('mmsi'))
 if len(imo)==7:return f'imo:{imo}'
 if len(mmsi)==9:return f'mmsi:{mmsi}'
 c=clean(x.get('callsign')).upper()
 if c:return f'callsign:{c}'
 n=clean(x.get('name')).upper();return f'name:{n}' if n else ''
def read_json(path):
 if not path.exists():return None
 try:p=json.loads(path.read_text(encoding='utf-8'))
 except Exception:return None
 return p if isinstance(p,dict) else None
def read_jsonl(path):
 out=[]
 if not path.exists():return out
 for line in path.read_text(encoding='utf-8').splitlines():
  try:r=json.loads(line)
  except Exception:continue
  if isinstance(r,dict):out.append(r)
 return out
def atomic(path,payload,compact=False):
 path.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as h:
  json.dump(payload,h,ensure_ascii=False,separators=(',',':') if compact else None,indent=None if compact else 2);h.write('\n');tmp=h.name
 Path(tmp).replace(path)
def source_range(rows):
 vals=[parse(r.get('observed_at')) for r in rows];vals=[v for v in vals if v];return (min(vals),max(vals)) if vals else (None,None)
def normalize_gfw(x):
 t=parse(x.get('observed_at'));lat=finite(x.get('latitude'));lon=finite(x.get('longitude'));i=ident(x)
 if not t or lat is None or lon is None or not i:return None
 return {**x,'provider':'global_fishing_watch','identity_key':i,'observed_at':iso(t),'latitude':lat,'longitude':lon,'timestamp_valid':True,'timestamp_basis':'gfw_hourly_report_cell','position_is_exact':False,'historical':True,'not_current_position':True}
def gfw_data():
 p=read_json(GFW_PATH);s=read_json(GFW_STATUS_PATH) or {}
 if p:
  rows=[]
  for c in p.get('contacts') or []:
   if not isinstance(c,dict):continue
   for pos in c.get('positions') if isinstance(c.get('positions'),list) else [c]:
    m=dict(c);m.update(pos if isinstance(pos,dict) else {});r=normalize_gfw(m)
    if r:rows.append(r)
  return rows,{'source_path':str(GFW_PATH.relative_to(ROOT)),'coverage_scope':p.get('coverage_scope'),'coverage_complete':bool(p.get('coverage_complete') and s.get('coverage_complete')),'generated_at':p.get('generated_at'),'data_max_timestamp_utc':p.get('data_max_timestamp_utc'),'status':s.get('status'),'cap_applied':bool(s.get('cap_applied')),'queries_failed':s.get('queries_failed')}
 f=read_json(GFW_FALLBACK)
 if f:
  rows=[r for x in f.get('historical_ais_presence_records') or [] if isinstance(x,dict) and (r:=normalize_gfw(x))]
  return rows,{'source_path':str(GFW_FALLBACK.relative_to(ROOT)),'coverage_scope':'sar_matched_context_only','coverage_complete':False,'generated_at':f.get('generated_at'),'data_max_timestamp_utc':max((r['observed_at'] for r in rows),default=None),'status':'limited_scope'}
 return [],{'source_path':None,'coverage_scope':'missing','coverage_complete':False,'generated_at':None,'data_max_timestamp_utc':None,'status':'missing'}
def choose_at(rows,snapshot,max_age):
 grouped={}
 for r in rows:
  t=parse(r.get('observed_at'));i=ident(r)
  if not t or t>snapshot or not i:continue
  age=(snapshot-t).total_seconds()/3600
  if age>max_age:continue
  c=dict(r);c['_t']=t;c['identity_key']=i;c['age_at_snapshot_minutes']=round(age*60,1);grouped.setdefault(i,[]).append(c)
 out=[]
 for i,vals in grouped.items():
  vals.sort(key=lambda x:x['_t']);win=vals[-1];prev=vals[-2] if len(vals)>1 else None;win.pop('_t',None)
  win['previous_position']={'observed_at':prev.get('observed_at'),'latitude':prev.get('latitude'),'longitude':prev.get('longitude')} if prev else None;out.append(win)
 return out
def canonical(rows,cfg):
 ranks=cfg.get('provider_quality_rank') or {};groups={}
 for r in rows:
  if i:=ident(r):groups.setdefault(i,[]).append(r)
 out=[]
 for i,vals in groups.items():
  score=lambda r:(float(r.get('age_at_snapshot_minutes') or 999999),0 if r.get('timestamp_valid') is not False else 1,0 if r.get('position_is_exact') is not False else 1,int(ranks.get(str(r.get('provider')),99)))
  w=dict(min(vals,key=score));w['provider_candidates']=sorted({str(v.get('provider')) for v in vals if v.get('provider')});w['provider_candidate_count']=len(vals);out.append(w)
 return sorted(out,key=lambda r:(str(r.get('provider') or ''),str(r.get('identity_key') or '')))
def classification_index():
 idx={}
 def merge(key,props):
  if not key:return
  cur=idx.setdefault(key,{'categories':set()});cats=props.get('categories') if isinstance(props.get('categories'),list) else []
  cur['categories'].update(str(c) for c in cats if str(c).strip())
  for k in ('is_priority_voi','known_voi_match','sanctioned','shadow_fleet','false_flag','behavioral_voi','from_russia_confirmed','recent_russian_portcall_confirmed_10d','recent_russian_portcall_unconfirmed_10d','to_russia_declared','neutral_tanker_context'):
   cur[k]=bool(cur.get(k) or props.get(k) is True or truthy(props.get(k)))
 watch=DATA/'watchlist_master.csv'
 if watch.exists():
  with watch.open('r',encoding='utf-8-sig',newline='') as h:
   for row in csv.DictReader(h):
    cats=[]
    for col,cat in [('track_sanctions','sanctions_shadowfleet'),('track_shadowfleet','shadowfleet'),('track_falseflag','falseflag_interest'),('track_behavior','behavioral_voi'),('track_russian_mmsi','russian_mmsi')]:
     if truthy(row.get(col)):cats.append(cat)
    props={'categories':cats+['watchlist'],'is_priority_voi':True,'known_voi_match':True,'sanctioned':truthy(row.get('track_sanctions')),'shadow_fleet':truthy(row.get('track_shadowfleet')),'false_flag':truthy(row.get('track_falseflag')),'behavioral_voi':truthy(row.get('track_behavior'))}
    imo=digits(row.get('imo'));mmsi=digits(row.get('mmsi'))
    if len(imo)==7:merge(f'imo:{imo}',props)
    if len(mmsi)==9:merge(f'mmsi:{mmsi}',props)
 for name in ('voi_snapshot_latest.json','watchlist_live.geojson','sanctions_shadowfleet.geojson','russian_mmsi.geojson','falseflag_interest.geojson','false_flag_watch.geojson','behavioral_voi.geojson','recent_russian_portcall_10d.geojson','neutral_tanker_context.geojson'):
  p=read_json(DATA/name)
  if not p:continue
  items=p.get('contacts') or p.get('features') or []
  for item in items:
   props=item.get('properties') if isinstance(item,dict) and isinstance(item.get('properties'),dict) else item
   if not isinstance(props,dict):continue
   props=dict(props);implied={
    'watchlist_live.geojson':'watchlist','sanctions_shadowfleet.geojson':'sanctions_shadowfleet','russian_mmsi.geojson':'russian_mmsi',
    'falseflag_interest.geojson':'falseflag_interest','false_flag_watch.geojson':'false_flag_watch','behavioral_voi.geojson':'behavioral_voi',
    'recent_russian_portcall_10d.geojson':'recent_russian_portcall_10d','neutral_tanker_context.geojson':'neutral_tanker_context'}.get(name)
   if implied:props['categories']=list(props.get('categories') or [])+[implied]
   if implied=='neutral_tanker_context':props['neutral_tanker_context']=True
   if implied=='sanctions_shadowfleet':props['sanctioned']=True;props['shadow_fleet']=True
   if implied in {'falseflag_interest','false_flag_watch'}:props['false_flag']=True
   if implied=='behavioral_voi':props['behavioral_voi']=True
   if implied=='watchlist':props['known_voi_match']=True;props['is_priority_voi']=True
   keys=[];imo=digits(props.get('imo'));mmsi=digits(props.get('mmsi'))
   if len(imo)==7:keys.append(f'imo:{imo}')
   if len(mmsi)==9:keys.append(f'mmsi:{mmsi}')
   for k in keys:merge(k,props)
 for v in idx.values():v['categories']=sorted(v['categories'])
 return idx
def enrich(rows,snapshot):
 idx=classification_index();out=[]
 for r in rows:
  c=dict(r);cl=idx.get(ident(c),{});cats=sorted(set((c.get('categories') or [])+(cl.get('categories') or [])))
  c.update({k:cl.get(k,c.get(k,False)) for k in ('is_priority_voi','known_voi_match','sanctioned','shadow_fleet','false_flag','behavioral_voi','from_russia_confirmed','recent_russian_portcall_confirmed_10d','recent_russian_portcall_unconfirmed_10d','to_russia_declared','neutral_tanker_context')});c['categories']=cats;c['snapshot_at']=iso(snapshot);out.append(c)
 return out
def coverage_ready(provider,snapshot,coverages,cfg):
 max_age=float((cfg.get('source_max_age_at_snapshot_hours') or {}).get(provider,12));future=float((cfg.get('coverage_future_tolerance_hours') or {}).get(provider,0))
 vals=[]
 for r in coverages:
  if r.get('provider')!=provider:continue
  if t:=parse(r.get('coverage_at')):vals.append(t)
 eligible=[t for t in vals if t<=snapshot+timedelta(hours=future) and t>=snapshot-timedelta(hours=max_age)]
 return (max(eligible) if eligible else None,min(vals) if vals else None,max(vals) if vals else None)
def voi_date():
 for p in (DATA/'watchlist_audit.json',DATA/'voi_snapshot_latest.json'):
  x=read_json(p) or {}
  for k in ('generated_at','updated_at','ts'):
   if d:=parse(x.get(k)):return iso(d)
 return None
def geojson(rows,meta):
 fs=[]
 for r in rows:
  lat=finite(r.get('latitude'));lon=finite(r.get('longitude'))
  if lat is None or lon is None:continue
  props={k:v for k,v in r.items() if k not in {'latitude','longitude'}};fs.append({'type':'Feature','id':f"{r.get('provider','source')}|{r.get('identity_key','unknown')}",'geometry':{'type':'Point','coordinates':[lon,lat]},'properties':props})
 return {'type':'FeatureCollection','name':'Voodoo Whiskers harmonized common maritime snapshot',**meta,'features':fs}
def main():
 cfg=json.loads(CONFIG_PATH.read_text(encoding='utf-8'));generated=now();hist=read_jsonl(HISTORY_PATH);cov=read_jsonl(COVERAGE_PATH);grows,gmeta=gfw_data();gmax=source_range(grows)[1] or parse(gmeta.get('data_max_timestamp_utc'));snapshot=floor_hour(gmax) if gmax else None
 sources={'fintraffic':[r for r in hist if r.get('provider')=='fintraffic'],'barentswatch':[r for r in hist if r.get('provider')=='barentswatch'],'ais_dk_historical':[r for r in hist if r.get('provider')=='ais_dk_historical'],'global_fishing_watch':grows}
 mandatory=[str(x) for x in cfg.get('mandatory_sources') or []];coverage={};candidates=[];complete=bool(snapshot and gmeta.get('coverage_complete'))
 for provider in mandatory:
  rows=sources.get(provider,[]);mn,mx=source_range(rows);selected=choose_at(rows,snapshot,float((cfg.get('source_max_age_at_snapshot_hours') or {}).get(provider,12))) if snapshot else []
  state='ready';reason=None;coverage_point=None
  if provider=='global_fishing_watch':
   coverage_point=gmax
   if not grows:state='missing';reason='full_GFW_common_presence_product_missing'
   elif not gmeta.get('coverage_complete'):state='limited_or_degraded';reason='GFW_region_failure_or_cap_or_SAR_only_scope'
  elif snapshot:
   coverage_point,cmin,cmax=coverage_ready(provider,snapshot,cov,cfg)
   if not coverage_point:
    state='warming_up' if cmax and cmin and cmin>snapshot else 'missing_at_watermark';reason='coverage_archive_does_not_reach_common_watermark'
   elif not selected:state='ready_no_retained_contacts';reason='source_covered_watermark_but_no_retained_contact_within_position_tolerance'
  else:state='missing';reason='no_GFW_watermark'
  if state not in {'ready','ready_no_retained_contacts'}:complete=False
  candidates.extend(selected);coverage[provider]={'state':state,'reason':reason,'coverage_at':iso(coverage_point) if coverage_point else None,'position_history_min':iso(mn) if mn else None,'position_history_max':iso(mx) if mx else None,'selected_contacts':len(selected),'max_age_at_snapshot_hours':(cfg.get('source_max_age_at_snapshot_hours') or {}).get(provider)}
 canon=enrich(canonical(candidates,cfg),snapshot) if snapshot else [];sid=f"maritime-{snapshot.strftime('%Y%m%dT%H%M%SZ')}" if snapshot else None
 if not snapshot:overall,color='unavailable','red'
 elif complete:overall,color='current','green'
 else:overall,color='warming_up','orange'
 expected_refresh_hours=float(cfg.get('snapshot_expected_refresh_hours',30));hard_stale_hours=float(cfg.get('snapshot_hard_stale_hours',max(72,expected_refresh_hours*2)))
 meta={'schema_version':'1.0.0','snapshot_id':sid,'generated_at':iso(generated),'snapshot_at':iso(snapshot) if snapshot else None,'snapshot_age_hours':round((generated-snapshot).total_seconds()/3600,2) if snapshot else None,'snapshot_complete':complete,'snapshot_current':complete,'expected_refresh_hours':expected_refresh_hours,'hard_stale_hours':hard_stale_hours,'status':overall,'ais_status_color':color,'coverage_mode':'harmonized_common_watermark','voi_list_updated_at':voi_date(),'gfw_source':gmeta,'source_coverage':coverage,'assessment_limit':'The common snapshot is a harmonized delayed observation product, not a live traffic picture.'}
 output={**meta,'count':len(canon),'contacts':canon};status={**meta,'count':len(canon),'mandatory_sources':mandatory,'green_rule':'Green means the newest expected common snapshot was successfully built from every mandatory source. It does not mean live AIS.'};gj=geojson(canon,meta)
 mapping={OUT:output,STATUS:status,PUBLIC/'maritime_common_snapshot_latest.json':output,PUBLIC/'maritime_common_snapshot_latest.geojson':gj,PUBLIC/'maritime_common_snapshot_status.json':status,DOWNLOADS/'maritime_common_snapshot_latest.json':output,DOWNLOADS/'maritime_common_snapshot_latest.geojson':gj,DOWNLOADS/'maritime_common_snapshot_status.json':status}
 for p,x in mapping.items():atomic(p,x,p.suffix=='.geojson');
 for p in mapping:
  if p.stat().st_size>25*1024*1024:raise SystemExit(f'common snapshot output exceeds 25 MiB: {p}')
 print(json.dumps({'status':overall,'ais_status_color':color,'snapshot_id':sid,'snapshot_at':meta['snapshot_at'],'count':len(canon),'coverage':{k:v['state'] for k,v in coverage.items()}},ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
