"""Export already collected, delayed aircraft samples for local historical review.
No new collection or committed position copies. The report is incomplete by design.
"""
import json, math, sys
from pathlib import Path
from datetime import datetime, timezone
from special_aircraft_registry import load_registry
ROOT=Path(__file__).resolve().parents[1]
MAX_ROWS=30000
MAX_BYTES=4*1024*1024
DAY=86400
def build(history, registry, now):
    selected={r["icao24"]:r for r in registry if r["enabled"] and not r["identity_conflict"]}
    unique={}; conflicts=set(); rejected=0
    for p in sorted(history.glob("*/*.jsonl")):
        if p.stat().st_size>1048576:
            rejected+=1
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row=json.loads(line)
                if not isinstance(row,list) or len(row)!=10:raise ValueError()
                hx,t,lat,lon,alt,gs,track,source,registration,call=row
                air=selected.get(hx)
                if not air:continue
                if type(t) not in (int,float) or not math.isfinite(t):raise ValueError()
                if not now-14*DAY<=t<=now-DAY:continue
                name=air.get("registration") or air.get("military_serial")
                if not name or not isinstance(registration,str) or registration.upper()!=name.upper():raise ValueError()
                if any(type(x) not in (int,float) or not math.isfinite(x) for x in (lat,lon)) or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError()
                if source not in ("adsb","mlat","tisb","ADS-B","MLAT","TIS-B"):raise ValueError()
                ident=air["record_id"]; key=(ident,t)
                value=[ident,name,t,round(lat,5),round(lon,5),alt if type(alt) in (int,float) and math.isfinite(alt) else None,source]
                if key in conflicts:continue
                if key in unique and unique[key][3:5]!=value[3:5]:
                    del unique[key];conflicts.add(key);rejected+=1;continue
                unique.setdefault(key,value)
            except (ValueError,TypeError,KeyError,IndexError):rejected+=1
    items=sorted(unique.values(),key=lambda x:(x[2],x[0]))
    total=len(items)
    # Even temporal sampling instead of silently retaining only recent hours.
    if total>MAX_ROWS:items=[items[i*total//MAX_ROWS] for i in range(MAX_ROWS)]
    out=dict(schema="VOODOO_AIRCRAFT_DELAYED_HISTORY_V1",generated_at=datetime.fromtimestamp(now,timezone.utc).isoformat(),available=history.is_dir(),coverage_complete=False,min_delay_hours=24,retention_days=14,source="adsb.lol",license="ODbL-1.0",fields=["record_id","registration","observed_unix_seconds","latitude","longitude","altitude_ft","position_source"],total_valid_samples=total,rejected_rows=rejected,sampled=total>len(items),items=items,note="Received positions, not complete flights. Dashed connectors are visual aids, not observed paths. No inference of rescue or current mission.")
    def size():return len(json.dumps(out,ensure_ascii=False,separators=(",",":")).encode())
    while size()>MAX_BYTES and len(out["items"])>1:
        out["items"]=out["items"][::2];out["sampled"]=True
    if size()>MAX_BYTES:raise ValueError("history output exceeds cap")
    return out
def main():
    history=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"public/data/aircraft/history"
    registry,_=load_registry()
    pack=build(history,registry["aircraft"],datetime.now(timezone.utc).timestamp())
    (ROOT/"aircraft-delayed-history.json").write_text(json.dumps(pack,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    print(json.dumps({k:pack[k] for k in ("available","total_valid_samples","rejected_rows","sampled")}))
if __name__=="__main__":main()
