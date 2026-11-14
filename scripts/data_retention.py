"""Bound context observations by acquisition/event time, never by download time."""
import json
import hashlib
from datetime import datetime,timedelta,timezone


def parsed(value):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(timezone.utc)
    except (ValueError,TypeError):return None


def bound_gfw(pack,kind,now=None,max_bytes=4*1024*1024):
    now=now or datetime.now(timezone.utc);cutoff=now-timedelta(days=14)
    field='events' if kind=='events' else 'records';unique={};invalid=0
    for row in pack.get(field,[]):
        if not isinstance(row,dict):invalid+=1;continue
        start=parsed(row.get('start') if kind=='events' else row.get('interval_start'))
        end=parsed(row.get('end') if kind=='events' else row.get('interval_end'))
        if not start or not end or not start<=end or end<cutoff or start>now or end>now+timedelta(days=1):invalid+=1;continue
        if kind=='events':
            if row.get('type') not in {'LOITERING','GAP'} or not row.get('id'):invalid+=1;continue
            key=(row['type'],row['id'])
        else:key=(row.get('latitude'),row.get('longitude'),row.get('interval_start'),row.get('flag'))
        unique[key]=(end,row)
    result={**pack,field:[], 'retention_days':14,'retention_checked_at':now.isoformat(),'retention_basis':'event_overlap_or_observation_interval','retention_dropped':invalid,'size_dropped':0}
    rows=sorted(unique.values(),key=lambda item:item[0],reverse=True);selected=[];size=0
    for _time,row in rows:
        amount=len(json.dumps(row,ensure_ascii=False,separators=(',',':')).encode())+1
        if size+amount>max_bytes:result['size_dropped']+=1;continue
        selected.append(row);size+=amount
    result[field]=list(reversed(selected));result['count']=len(selected)
    result['cap_applied']=bool(pack.get('cap_applied') or result['size_dropped'])
    # Already bounded rows need no reshuffling when every row still survives.
    # Reversing equal-time cells repeatedly would otherwise change order each run.
    if (pack.get('retention_days')==14 and pack.get('retention_basis')==result['retention_basis']
            and len(result[field])==len(pack.get(field,[]))):
        result[field]=list(pack[field])
    fingerprint=hashlib.sha256(json.dumps(
        {key:value for key,value in result.items() if key not in {'retention_checked_at','retention_content_sha256'}},
        ensure_ascii=False,sort_keys=True,separators=(',',':')
    ).encode()).hexdigest()
    result['retention_content_sha256']=fingerprint
    # Recheck expiry on every call, but do not create a new multi-MiB Git blob
    # merely because the clock advanced. Keep the last material validation time
    # while the observations, provider metadata and retention result are identical.
    previous_checked=parsed(pack.get('retention_checked_at'))
    if previous_checked and previous_checked<=now and pack.get('retention_content_sha256')==fingerprint:
        result['retention_checked_at']=pack['retention_checked_at']
    return result
