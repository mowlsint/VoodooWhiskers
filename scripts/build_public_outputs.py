#!/usr/bin/env python3
from __future__ import annotations
import csv, json, math
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data'; PUB=ROOT/'public'
CFG=json.loads((ROOT/'config/infrastructure_watch.json').read_text(encoding='utf-8'))

def iso_now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def num(v):
    try: return float(v)
    except: return None
def valid_sog(v):
    x=num(v); return x if x is not None and 0<=x<=60 else None
def valid_time(v):
    s=str(v or '')
    try:
        d=datetime.fromisoformat(s.replace(' +0000 UTC','+00:00').replace(' UTC','+00:00').replace('Z','+00:00'))
        if d.year<2000: return None
        return d.astimezone(timezone.utc).isoformat()
    except: return None
def clean_item(x):
    y=dict(x); y['sog']=valid_sog(x.get('sog')); y['last_seen_utc']=valid_time(x.get('last_seen_utc'))
    q=[]
    if x.get('last_seen_utc') and not y['last_seen_utc']: q.append('invalid_source_timestamp')
    if x.get('sog') not in (None,'') and y['sog'] is None: q.append('invalid_sog')
    y['data_quality_flags']=q
    return y
def point_feature(x):
    lat=num(x.get('latitude')); lon=num(x.get('longitude'))
    if lat is None or lon is None or not(-90<=lat<=90 and -180<=lon<=180): return None
    props={k:v for k,v in x.items() if k not in ('latitude','longitude')}
    return {'type':'Feature','geometry':{'type':'Point','coordinates':[lon,lat]},'properties':props}
def iter_coords(g):
    if not g: return
    t=g.get('type'); c=g.get('coordinates')
    if t=='Point': yield c
    elif t in ('LineString','MultiPoint'):
        for p in c or []: yield p
    elif t in ('Polygon','MultiLineString'):
        for a in c or []:
            for p in a or []: yield p
    elif t=='MultiPolygon':
        for a in c or []:
            for b in a or []:
                for p in b or []: yield p
    elif t=='GeometryCollection':
        for z in g.get('geometries') or []: yield from iter_coords(z)
def hav_nm(lat1,lon1,lat2,lon2):
    r=3440.065; p1=math.radians(lat1); p2=math.radians(lat2); dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1,math.sqrt(a)))
def nearest(lat,lon,refs):
    best=None
    for lid,feat in refs:
        for p in iter_coords(feat.get('geometry')):
            if not p or len(p)<2: continue
            d=hav_nm(lat,lon,float(p[1]),float(p[0]))
            if best is None or d<best[0]: best=(d,lid,feat)
    return best

def main():
    snap=json.loads((DATA/'voi_snapshot_latest.json').read_text(encoding='utf-8')); items=[clean_item(x) for x in snap.get('items') or []]
    generated=iso_now(); vessel_geo={'type':'FeatureCollection','name':'Voodoo Whiskers vessel positions','generated_at':generated,'features':[f for x in items if (f:=point_feature(x))]}
    (PUB/'data/vessels/voi_snapshot_latest.json').write_text(json.dumps({**snap,'items':items},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (PUB/'data/vessels/vessel_positions_latest.geojson').write_text(json.dumps(vessel_geo,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    fields=['name','imo','mmsi','callsign','latitude','longitude','last_seen_utc','source','destination','sog','categories','watch_priority','sanctioned','shadow_fleet','false_flag','from_russia_confirmed','data_quality_flags']
    rows=[]
    for x in items:
        if x.get('neutral_tanker_context') and not any(x.get(k) for k in ['sanctioned','shadow_fleet','false_flag','from_russia_confirmed']): continue
        row={k:x.get(k,'') for k in fields}; row['categories']=';'.join(x.get('categories') or []); row['data_quality_flags']=';'.join(x.get('data_quality_flags') or []); rows.append(row)
    dl=PUB/'downloads'; dl.mkdir(parents=True,exist_ok=True)
    (dl/'voi_list_latest.json').write_text(json.dumps({'schema_version':'1.0.0','generated_at':generated,'count':len(rows),'items':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (dl/'voi_list_latest.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    md=['# Voodoo Whiskers – Current VOI List','',f'Generated: {generated}',f'Entries: {len(rows)}','', '| Vessel | IMO | MMSI | Categories | Last position | Source |','|---|---:|---:|---|---|---|']
    for r in rows: md.append(f"| {r['name']} | {r['imo']} | {r['mmsi']} | {r['categories']} | {r['last_seen_utc'] or 'source time invalid'} | {r['source']} |")
    (dl/'voi_list_latest.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    refs=[]
    for p in (PUB/'data/reference/emodnet').glob('*.geojson'):
        try:
            j=json.loads(p.read_text(encoding='utf-8'))
            for ft in j.get('features') or []: refs.append((p.stem,ft))
        except: pass
    events=[]
    for x in items:
        lat=num(x.get('latitude')); lon=num(x.get('longitude'))
        if lat is None or lon is None or not refs: continue
        n=nearest(lat,lon,refs)
        if not n or n[0]>CFG['max_distance_nm']: continue
        signals=['close_proximity'] if n[0]<=CFG['close_distance_nm'] else ['contextual_proximity']
        if x.get('sog') is not None and x['sog']<=CFG['low_speed_knots']: signals.append('low_speed')
        if x.get('sanctioned') or x.get('shadow_fleet') or x.get('false_flag') or x.get('watch_priority'): signals.append('watchlist_context')
        if len(signals)<CFG['minimum_signals_for_review']: continue
        events.append({'event_id':f"VWI-{x.get('mmsi') or x.get('imo')}-{n[1]}",'event_type':'critical_infrastructure_proximity','level':'review','confidence':'low_medium','mmsi':x.get('mmsi'),'imo':x.get('imo'),'vessel_name':x.get('name'),'latitude':lat,'longitude':lon,'infrastructure_type':n[1],'minimum_distance_nm':round(n[0],3),'signals':signals,'statement':'Behaviour warrants analyst review. Proximity alone does not indicate hostile intent.'})
    out={'schema_version':'1.0.0','generated_at':generated,'source':'Voodoo Whiskers','mode':'analyst_review','event_count':len(events),'events':events,'score_integration':False}
    (PUB/'data/analysis/infrastructure_events_latest.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    eg={'type':'FeatureCollection','generated_at':generated,'features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[e['longitude'],e['latitude']]},'properties':{k:v for k,v in e.items() if k not in ('latitude','longitude')}} for e in events]}
    (PUB/'data/analysis/infrastructure_events_latest.geojson').write_text(json.dumps(eg,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    (dl/'infrastructure_watch_latest.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (dl/'infrastructure_watch_latest.csv').open('w',newline='',encoding='utf-8') as f:
        fs=[
            'event_id','event_type','level','confidence','vessel_name','imo','mmsi',
            'latitude','longitude','infrastructure_type','minimum_distance_nm',
            'signals','statement'
        ]
        w=csv.DictWriter(f,fieldnames=fs)
        w.writeheader()
        for e in events:
            row={key:e.get(key,'') for key in fs}
            row['signals']=';'.join(e.get('signals') or [])
            w.writerow(row)
    (dl/'infrastructure_watch_latest.md').write_text('# Voodoo Whiskers – Critical Infrastructure Watch\n\nGenerated: '+generated+f'\n\nReview events: {len(events)}\n\nProximity alone does not indicate hostile intent.\n',encoding='utf-8')
    manifest={'schema_version':'1.0.0','generated_at':generated,'map':'./infrastructure-watch.html','products':{
      'voi_json':'./downloads/voi_list_latest.json','voi_csv':'./downloads/voi_list_latest.csv','voi_markdown':'./downloads/voi_list_latest.md',
      'infrastructure_json':'./downloads/infrastructure_watch_latest.json','infrastructure_csv':'./downloads/infrastructure_watch_latest.csv','infrastructure_markdown':'./downloads/infrastructure_watch_latest.md','infrastructure_geojson':'./data/analysis/infrastructure_events_latest.geojson'}}
    (PUB/'data/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'voi_rows':len(rows),'events':len(events),'references':len(refs)},indent=2))
if __name__=='__main__': main()
