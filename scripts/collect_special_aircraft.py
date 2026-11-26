"""Bounded, optional aircraft sampling. Network access requires --fetch. No vessel writes."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from special_aircraft_registry import load_registry

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = 'https://api.adsb.lol/v2/icao/'
LICENSE = {'name': 'ODbL-1.0', 'url': 'https://opendatacommons.org/licenses/odbl/1-0/',
           'attribution': 'Aircraft position data: adsb.lol. Derived aircraft position database under ODbL 1.0.'}
HISTORY_FIELDS = ['icao24', 'observed_unix_seconds', 'latitude', 'longitude', 'altitude_ft',
                  'ground_speed_kn', 'track_deg', 'position_source', 'registration', 'callsign']
INPUTS = ['config/aircraft_collection.json', 'config/special_aircraft_registry.json',
          'scripts/collect_special_aircraft.py', 'scripts/special_aircraft_registry.py',
          'scripts/repo_storage_guard.py']


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def label(value):
    return re.sub('[^A-Z0-9]', '', str(value or '').upper())


class FetchFailure(Exception):
    def __init__(self, code, retry_at=0):
        self.code, self.retry_at = code, retry_at


def fetch_batch(hexes, limit):
    # Fixed public origin; no credentials, discovery, raw payload persistence or automatic retries.
    req = Request(PROVIDER + ','.join(hexes), headers={'User-Agent': 'MOwlSINT-VoodooWhiskers/1.0',
                                                      'Accept': 'application/json'})
    try:
        with urlopen(req, timeout=15) as response:
            if response.geturl() != req.full_url:
                raise FetchFailure('unexpected_redirect')
            body = response.read(limit + 1)
        if len(body) > limit:
            raise FetchFailure('oversize_response')
        return json.loads(body)
    except HTTPError as exc:
        retry_at = 0
        if exc.code == 429:
            value = exc.headers.get('Retry-After', '')
            try:
                retry_at = time.time() + max(1, int(value))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value).timestamp()
                except (TypeError, ValueError, OverflowError):
                    retry_at = time.time() + 86400
        raise FetchFailure('rate_limited' if exc.code == 429 else 'http_' + str(exc.code), retry_at) from None
    except FetchFailure:
        raise
    except Exception:
        # Exception messages/response bodies are deliberately not propagated to logs or public files.
        raise FetchFailure('source_unavailable') from None


def normalize(payload, selected, policy, now):
    """Preserve provider position time. Never use request time, receiver estimates or callsign joins."""
    if (not isinstance(payload, dict) or payload.get('msg') != 'No error'
            or not isinstance(payload.get('ac'), list) or type(payload.get('total')) is not int
            or payload['total'] != len(payload['ac']) or len(payload['ac']) > len(selected)):
        raise FetchFailure('invalid_response')
    generated = payload.get('now')
    if not finite(generated) or not 1000000000000 < generated < 10000000000000:
        raise FetchFailure('invalid_provider_time')
    generated /= 1000
    if generated > now + 60 or now - generated > policy['max_position_age_seconds']:
        raise FetchFailure('stale_source')
    result, rejected, conflicts = {}, Counter(), set()
    for raw in payload['ac']:
        if not isinstance(raw, dict):
            rejected['invalid_row'] += 1
            continue
        hexcode = str(raw.get('hex', '')).lower()
        aircraft = selected.get(hexcode)
        if not aircraft:
            rejected['unselected_identity'] += 1
            continue
        known = {label(aircraft['registration']), label(aircraft['military_serial'])}
        known.update(label(a) for a in aircraft.get('aliases', []) if isinstance(a, str))
        if raw.get('r') and label(raw['r']) not in known:
            rejected['identity_conflict'] += 1
            continue
        lat, lon, age = raw.get('lat'), raw.get('lon'), raw.get('seen_pos')
        if (not finite(lat) or not finite(lon) or abs(lat) > 90 or abs(lon) > 180
                or not finite(age) or not 0 <= age <= policy['max_position_age_seconds']):
            rejected['missing_invalid_or_old_position'] += 1
            continue
        at = math.floor(generated - age)
        if at > now or now - at > policy['max_position_age_seconds']:
            rejected['old_position'] += 1
            continue
        if not any(w <= lon <= e and s <= lat <= n for w, s, e, n in [r['bbox'] for r in policy['regions']]):
            rejected['outside_regions'] += 1
            continue
        typ = raw.get('type')
        mlat, tisb = raw.get('mlat', []), raw.get('tisb', [])
        if not isinstance(mlat, list) or not isinstance(tisb, list) or not all(isinstance(v, str) for v in mlat + tisb):
            rejected['invalid_position_source'] += 1
            continue
        position_source = ('mlat' if typ == 'mlat' or {'lat', 'lon'} & set(mlat) else
                           'tisb' if str(typ).startswith('tisb') or {'lat', 'lon'} & set(tisb) else
                           'adsb' if str(typ).startswith('adsb_') else None)
        if position_source is None:
            rejected['unknown_position_source'] += 1
            continue
        row = {'icao24': hexcode, 'latitude': lat, 'longitude': lon, 'observed_at': iso(at),
               'position_timestamp_valid': True, 'position_timestamp_basis': 'provider_now_ms_minus_seen_pos_seconds',
               'selection_status': 'registry_selected', 'roles': aircraft['roles'],
               'registration': aircraft['registration'] or aircraft['military_serial'],
               'operator': aircraft['operator'], 'country': aircraft['country'] or aircraft['organisation_scope'],
               'aircraft_type': aircraft['aircraft_type'], 'source': 'adsb.lol', 'position_source': position_source,
               'identity_verified_at': aircraft['reviewed_at'],
               'identity_review_note': 'Curated registry selection. Independently verify identity, current role and flight activity.',
               'callsign': str(raw.get('flight') or '').strip()[:16]}
        for source_key, target, low, high in [('alt_baro', 'altitude_ft', -2000, 80000),
                                              ('gs', 'ground_speed_kn', 0, 1500), ('track', 'track_deg', 0, 360)]:
            if finite(raw.get(source_key)) and low <= raw[source_key] <= high:
                row[target] = raw[source_key]
        key = (hexcode, at)
        if key in conflicts:
            continue
        prev = result.get(key)
        if prev and (prev['latitude'], prev['longitude']) != (lat, lon):
            del result[key]
            conflicts.add(key)
            rejected['conflicting_positions'] += 2
        else:
            result[key] = row
    return list(result.values()), dict(rejected)


def read_json(path, default=None):
    return json.loads(path.read_bytes()) if path.exists() else default


def validate_policy(policy):
    limits = {'poll_interval_seconds': 600, 'retention_days': 14, 'max_total_bytes': 30 * 1048576,
              'max_history_bytes': 27 * 1048576, 'max_latest_bytes': 1048576,
              'max_registry_status_bytes': 1048576, 'max_staged_path_bytes_per_utc_day': 4 * 1048576,
              'max_batches_per_poll': 2, 'batch_size': 64, 'max_response_bytes': 2 * 1048576,
              'max_position_age_seconds': 300}
    if policy.get('schema') != 'VOODOO_AIRCRAFT_COLLECTION_V1' or policy.get('provider') != 'adsb.lol':
        raise ValueError('Invalid aircraft collection policy')
    for key, ceiling in limits.items():
        if type(policy.get(key)) is not int or not 0 < policy[key] <= ceiling:
            raise ValueError('Invalid aircraft budget: ' + key)
    if policy['poll_interval_seconds'] != 600 or not policy.get('regions'):
        raise ValueError('Invalid aircraft polling/scope policy')
    for region in policy['regions']:
        w, s, e, n = region['bbox']
        if not all(finite(v) for v in [w, s, e, n]) or not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise ValueError('Invalid aircraft region')


def atomic_write(path, body):
    if path.is_symlink():
        raise ValueError('Aircraft output must not be a symlink')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(body)
    tmp.replace(path)


ROLLING_FIELDS = ['icao24','observed_unix_seconds','latitude','longitude','altitude_ft',
                  'ground_speed_kn','track_deg','position_source','registration','callsign',
                  'roles','country','operator','aircraft_type','commissioning_authority','identity_verified_at']
def rolling_aircraft_24h(files, observations, selected, policy, now):
    """Last received position per currently selected identity; no additional provider calls."""
    latest, conflicts, samples = {}, set(), {}
    def accept(row):
        if not isinstance(row,list) or len(row)!=10:
            return
        hx,t,lat,lon,alt,gs,track,source,registration,call=row
        air=selected.get(hx) if isinstance(hx,str) else None
        if not air or not finite(t) or not now-86400<=t<=now:
            return
        if not finite(lat) or not finite(lon) or abs(lat)>90 or abs(lon)>180:
            return
        if not any(w<=lon<=e and s<=lat<=n for w,s,e,n in [r['bbox'] for r in policy['regions']]):
            return
        name=air['registration'] or air['military_serial']
        if not isinstance(registration,str) or label(registration)!=label(name):
            return
        if source not in ('adsb','mlat','tisb'):
            return
        key=(hx,t)
        point=[int(t),round(lat,5),round(lon,5)]
        previous_point=samples.get(key)
        if previous_point is not None and previous_point!=point:
            conflicts.add(key);samples.pop(key,None)
            if latest.get(hx,[None,None])[1]==t:latest.pop(hx,None)
            return
        if key in conflicts:return
        samples[key]=point
        old=latest.get(hx)
        if key in conflicts:
            return
        if old and old[1]>t:
            return
        if old and old[1]==t and old[2:4]!=[lat,lon]:
            conflicts.add(key);latest.pop(hx,None);return
        if old and old[1]==t:
            return
        vals=[hx,t,lat,lon,alt if finite(alt) else None,gs if finite(gs) else None,
              track if finite(track) else None,source,name,str(call or '')[:16],
              air['roles'],air['country'] or air['organisation_scope'],air['operator'],
              air['aircraft_type'],air.get('commissioning_authority') or '',air['reviewed_at']]
        latest[hx]=vals
    # History files are immutable ten-minute chunks, so only inspect recent slots.
    for path in files:
        stamp=datetime.strptime(path.stem,'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
        if stamp<now-86400-600 or path.stat().st_size>1048576:continue
        for line in path.read_text(encoding='utf-8').splitlines():
            try:accept(json.loads(line))
            except (ValueError,TypeError,KeyError):continue
    for r in observations:
        t=int(datetime.fromisoformat(r['observed_at'].replace('Z','+00:00')).timestamp())
        accept([r['icao24'],t,r['latitude'],r['longitude'],r.get('altitude_ft'),
                r.get('ground_speed_kn'),r.get('track_deg'),r['position_source'],r['registration'],r['callsign']])
    # Compact optional track segments. Retain the newest connected pair for each
    # identity first, then distribute older pairs round-robin. Never bridge gaps.
    tracks={}
    for (hx,t),point in sorted(samples.items()):
        if (hx,t) not in conflicts and hx in latest:tracks.setdefault(hx,[]).append(point)
    pairs={}
    for hx,points in tracks.items():
        valid=[]
        for a,b in zip(points,points[1:]):
            dt=b[0]-a[0]
            if not 0<dt<=900 or abs(b[2]-a[2])>=180:continue
            rad=math.pi/180
            hav=math.sin((b[1]-a[1])*rad/2)**2+math.cos(a[1]*rad)*math.cos(b[1]*rad)*math.sin((b[2]-a[2])*rad/2)**2
            nm=3440.1*2*math.asin(math.sqrt(min(1,hav)))
            if nm*3600/dt<=750:valid.append([hx,*a,*b])
        if valid:pairs[hx]=valid[::-1]
    total=sum(map(len,pairs.values()));segments=[];size=0
    while pairs:
        for hx in list(sorted(pairs)):
            row=pairs[hx].pop(0);n=len(encoded(row))+1
            if size+n>8192:
                pairs.clear();break
            segments.append(row);size+=n
            if not pairs[hx]:del pairs[hx]
    return dict(schema='VOODOO_AIRCRAFT_ROLLING_24H_V1',as_of=iso(now),fields=ROLLING_FIELDS,
                items=sorted(latest.values(),key=lambda r:r[0]),coverage_complete=False,
                track_segments=segments,track_segments_total=total,track_segments_truncated=len(segments)<total)

def collect(root=ROOT, now=None, fetcher=fetch_batch, repository_bytes=None):
    live_clock = now is None
    now = math.floor(time.time() if now is None else now)
    policy_path = root / 'config/aircraft_collection.json'
    policy = read_json(policy_path)
    validate_policy(policy)
    registry, selected_rows = load_registry(root / 'config/special_aircraft_registry.json')
    selected = {a['icao24']: a for a in selected_rows}
    if len(selected) > policy['batch_size'] * policy['max_batches_per_poll']:
        raise ValueError('Registry exceeds bounded batch capacity; explicit policy review required')
    out = root / 'public/data/aircraft'
    if out.is_symlink() or any(p.is_symlink() for p in out.rglob('*')):
        raise ValueError('Aircraft directory must not contain symlinks')
    status_path = out / 'status_latest.json'
    previous = read_json(status_path, {})
    today = iso(now)[:10]
    slot = now // 600 * 600
    same_day = previous.get('day') == today
    used = previous.get('staged_path_bytes_today', 0) if same_day else 0
    pause_reserve = min(65536, policy['max_staged_path_bytes_per_utc_day'] // 8)
    if type(used) is not int or used < 0 or used > policy['max_staged_path_bytes_per_utc_day']:
        raise ValueError('Invalid daily aircraft byte ledger')
    if previous.get('slot', 0) >= slot:
        return {'code': 'already_attempted', 'requests': 0}
    history = out / 'history'
    files = sorted(history.glob('*/*.jsonl'))
    # Conservative whole-chunk expiry: no observations older than 14 days remain.
    cutoff = now - policy['retention_days'] * 86400 + 600
    for path in files:
        try:
            stamp = datetime.strptime(path.stem, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            raise ValueError('Unexpected aircraft history filename') from None
        if stamp < cutoff:
            path.unlink()
    files = [p for p in files if p.exists()]
    if previous.get('retry_at', 0) > now:
        return {'code': 'provider_backoff', 'requests': 0}
    storage_policy = read_json(root / 'config/storage_policy.json')
    paused = ('collection_disabled' if not registry['collection_enabled'] else
              'empty_selection' if not selected else
              'repository_size_unavailable' if repository_bytes is None else
              'repository_warning_budget' if repository_bytes >= storage_policy['warning_bytes'] else
              'daily_version_budget' if (used >= policy['max_staged_path_bytes_per_utc_day'] - pause_reserve
                                         or same_day and previous.get('code') == 'daily_version_budget') else None)
    if paused and same_day and previous.get('code') == paused:
        return {'code': paused, 'requests': 0}
    observations, rejected, failures = [], Counter(), []
    requests, successes, retry_at = 0, 0, 0
    if not paused:
        ids = sorted(selected)
        for offset in range(0, len(ids), policy['batch_size']):
            batch = ids[offset:offset + policy['batch_size']]
            requests += 1
            try:
                payload = fetcher(batch, policy['max_response_bytes'])
                if live_clock:
                    now = math.floor(time.time())
                rows, bad = normalize(payload, {h: selected[h] for h in batch}, policy, now)
                observations.extend(rows)
                rejected.update(bad)
                successes += 1
            except FetchFailure as exc:
                failures.append(exc.code)
                retry_at = exc.retry_at
                break
    elif paused:
        failures.append(paused)
    invalid = sum(v for k, v in rejected.items() if k != 'outside_regions')
    sources = {s['id']: s['url'] for s in registry['sources']}
    for row in observations:
        aircraft = selected[row['icao24']]
        row['commissioning_authority'] = aircraft.get('commissioning_authority') or ''
        row['identity_source_url'] = sources[aircraft['icao24_evidence'][0]['source_id']]
        row['role_source_url'] = sources[aircraft['role_source_ids'][0]]
    state = ('unavailable' if paused in ['collection_disabled', 'empty_selection'] else
             'partial' if successes and (failures or invalid) else
             'stale' if failures == ['stale_source'] else 'error' if failures else
             'fresh' if observations else 'valid_zero')
    code = paused or (failures[0] if failures else 'rejected_rows' if invalid else 'ok')
    old_latest = read_json(out / 'aircraft_latest.json', {})
    items = observations if successes else old_latest.get('items', [])
    pack = dict(kind='selected_aircraft_context', schema_version='1.0.0', context_only=True,
                coverage_complete=False, status=state, generated_at=iso(now), items=items,
                license=LICENSE, collection_code=code,
                position_data_at=max((r['observed_at'] for r in observations), default=None) if successes else old_latest.get('position_data_at'),
                last_success_at=iso(now) if not failures and not invalid else previous.get('last_success_at'))
    pack['rolling_24h'] = rolling_aircraft_24h(files, observations, selected, policy, now)
    # A stale/error result must not relabel cached positions as fresh.
    if state == 'stale':
        pack['generated_at'] = old_latest.get('generated_at', iso(now - 1801))
    last_seen = previous.get('last_observed_at_by_hex', {})
    if not isinstance(last_seen, dict):
        raise ValueError('Invalid aircraft deduplication state')
    last_seen = {h: t for h, t in last_seen.items() if h in selected and finite(t) and 0 <= now - t < 14 * 86400}
    new_rows = []
    for row in observations:
        at = int(datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00')).timestamp())
        if at <= last_seen.get(row['icao24'], 0):
            continue
        last_seen[row['icao24']] = at
        new_rows.append([row['icao24'], at, row['latitude'], row['longitude'], row.get('altitude_ft'),
                         row.get('ground_speed_kn'), row.get('track_deg'), row['position_source'],
                         row['registration'], row['callsign']])
    chunk = b''.join(encoded(row) for row in new_rows)
    chunk_path = history / today / (datetime.fromtimestamp(slot, timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.jsonl')
    status = dict(schema='VOODOO_AIRCRAFT_STATUS_V1', day=today, slot=slot, checked_at=iso(now), code=code,
                  source_status=state, selected=len(selected), requests=requests, successful_batches=successes,
                  rejected=rejected, retry_at=retry_at, last_success_at=pack['last_success_at'],
                  last_observed_at_by_hex=last_seen, staged_path_bytes_today=used)
    status['input_sha256'] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in INPUTS}
    writes = {out / 'aircraft_latest.json': encoded(pack), status_path: encoded(status),
              out / 'history_format.json': encoded(dict(schema='VOODOO_AIRCRAFT_HISTORY_V1', fields=HISTORY_FIELDS,
                                                       license=LICENSE, complete=False, retention_days=14))}
    if chunk:
        if chunk_path.exists():
            raise ValueError('Refusing to overwrite an immutable aircraft slot')
        writes[chunk_path] = chunk
    metadata_bytes = (root / 'config/special_aircraft_registry.json').stat().st_size + policy_path.stat().st_size
    if len(writes[out / 'aircraft_latest.json']) > policy['max_latest_bytes']:
        raise ValueError('Aircraft latest budget exceeded')
    if metadata_bytes + len(writes[status_path]) > policy['max_registry_status_bytes']:
        raise ValueError('Aircraft registry/status budget exceeded')
    # Keep optional track lines within the existing daily limit; omit them first if needed.
    if used + sum(len(b) for b in writes.values()) > policy['max_staged_path_bytes_per_utc_day'] - pause_reserve:
        rolling=pack.get('rolling_24h',{})
        rolling['track_segments']=[]
        rolling['track_segments_truncated']=bool(rolling.get('track_segments_total'))
        writes[out / 'aircraft_latest.json']=encoded(pack)
    # Count full changed paths, never assume compression. Reserve a small status-only pause at the daily cap.
    changed = lambda: {p: b for p, b in writes.items() if not p.exists() or p.read_bytes() != b}
    for _ in range(4):
        status['staged_path_bytes_today'] = used + sum(len(b) for b in changed().values())
        writes[status_path] = encoded(status)
    if status['staged_path_bytes_today'] > policy['max_staged_path_bytes_per_utc_day'] - pause_reserve and not paused:
        # Discard this sample before publication and preserve its previous deduplication state.
        pack.update(status='error', collection_code='daily_version_budget', items=old_latest.get('items', []),
                    position_data_at=old_latest.get('position_data_at'), last_success_at=previous.get('last_success_at'))
        status.update(code='daily_version_budget', source_status='error',
                      last_observed_at_by_hex=previous.get('last_observed_at_by_hex', {}),
                      last_success_at=previous.get('last_success_at'))
        writes.pop(chunk_path, None)
        # Do not publish positions from the discarded sample; keep the prior bounded view.
        if 'rolling_24h' in old_latest:
            pack['rolling_24h'] = old_latest['rolling_24h']
        else:
            pack.pop('rolling_24h', None)
        writes[out / 'aircraft_latest.json'] = encoded(pack)
        for _ in range(4):
            writes[status_path] = encoded(status)
            status['staged_path_bytes_today'] = used + sum(len(b) for b in changed().values())
        writes[status_path] = encoded(status)
    if status['staged_path_bytes_today'] > policy['max_staged_path_bytes_per_utc_day']:
        raise ValueError('Daily aircraft publication budget exhausted')
    history_bytes = sum(p.stat().st_size for p in files) + len(writes.get(chunk_path, b''))
    while files and history_bytes > policy['max_history_bytes']:
        path = files.pop(0)
        history_bytes -= path.stat().st_size
        path.unlink()
    if history_bytes > policy['max_history_bytes']:
        raise ValueError('Aircraft history chunk exceeds budget')
    extra_copies = sum(p.stat().st_size for directory in [root / 'data/aircraft', root / 'public/downloads/aircraft']
                       for p in directory.rglob('*') if p.is_file())
    existing = sum(p.stat().st_size for p in out.rglob('*') if p.is_file() and p not in writes)
    if metadata_bytes + extra_copies + existing + sum(len(b) for b in writes.values()) > policy['max_total_bytes']:
        raise ValueError('Combined aircraft working set exceeds 30 MiB')
    for path, body in changed().items():
        atomic_write(path, body)
    return {k: status[k] for k in ['code', 'source_status', 'selected', 'requests', 'successful_batches', 'staged_path_bytes_today']}


def repository_size():
    # Same repository only; never print token, response body or exception details.
    repo = os.environ.get('GITHUB_REPOSITORY', 'mowlsint/VoodooWhiskers')
    if repo != 'mowlsint/VoodooWhiskers':
        return None
    headers = {'Accept': 'application/vnd.github+json'}
    if os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    try:
        with urlopen(Request('https://api.github.com/repos/' + repo, headers=headers), timeout=15) as response:
            return int(json.loads(response.read(1048576))['size']) * 1024
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    try:
        if args.fetch:
            print(json.dumps(collect(repository_bytes=repository_size()), sort_keys=True))
        else:
            validate_policy(read_json(ROOT / 'config/aircraft_collection.json'))
            _, selected = load_registry()
            print(json.dumps({'selected': len(selected), 'network_requests': 0, 'validation': 'ok'}))
    except Exception:
        print(json.dumps({'status': 'error', 'code': 'aircraft_validation_or_storage_failure', 'publication': 'refused'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
