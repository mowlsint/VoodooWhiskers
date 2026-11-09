"""Resolve strong vessel identities across feeds without joining conflicting IMO claims."""
import re
from collections import defaultdict


def digits(value):return re.sub(r'\D','',str(value or ''))
def valid_imo(value):
    s=digits(value)
    return s if len(s)==7 and sum(int(c)*(7-i) for i,c in enumerate(s[:6]))%10==int(s[6]) else ''
def valid_mmsi(value):
    s=digits(value)
    return s if len(s)==9 and s!='000000000' else ''
def resolve(rows):
    aliases=defaultdict(set)
    for row in rows:
        i,m=valid_imo(row.get('imo')),valid_mmsi(row.get('mmsi'))
        if i and m:aliases[m].add(i)
    result=[]
    for row in rows:
        i,m=valid_imo(row.get('imo')),valid_mmsi(row.get('mmsi'));known=aliases.get(m,set())
        # Legacy malformed IMO remains a distinct key; never bridges a valid IMO claim.
        key=f'imo:{i}' if i else f'imo:{next(iter(known))}' if m and len(known)==1 else f'mmsi:{m}' if m else str(row.get('identity_key') or '')
        result.append({**row,'vessel_identity':key,'identity_conflict':bool(m and len(known)>1)})
    return result
