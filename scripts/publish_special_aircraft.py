"""Publish only generated aircraft files with bounded fast-forward/rebase retries."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from collect_special_aircraft import INPUTS, ROOT, read_json, validate_policy
from repo_storage_guard import active_vessel_bytes, check, repository_size_bytes

PREFIX = 'public/data/aircraft/'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()


def validate_outputs(root):
    policy = read_json(root / 'config/aircraft_collection.json')
    validate_policy(policy)
    out = root / PREFIX
    status = read_json(out / 'status_latest.json')
    if not status or set(status.get('input_sha256', {})) != set(INPUTS):
        raise ValueError('Missing aircraft source revision evidence')
    for name, digest in status['input_sha256'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Aircraft source changed during collection/publication; refusing stale write')
    if status['staged_path_bytes_today'] > policy['max_staged_path_bytes_per_utc_day']:
        raise ValueError('Aircraft daily budget exceeded')
    latest = read_json(out / 'aircraft_latest.json')
    if (latest.get('kind') != 'selected_aircraft_context' or latest.get('schema_version') != '1.0.0'
            or latest.get('status') not in ['fresh', 'valid_zero', 'partial', 'stale', 'error', 'unavailable']
            or latest.get('context_only') is not True or latest.get('coverage_complete') is not False
            or not isinstance(latest.get('items'), list)
            or latest['status'] == 'valid_zero' and latest['items']):
        raise ValueError('Invalid aircraft output')
    if (out / 'aircraft_latest.json').stat().st_size > policy['max_latest_bytes']:
        raise ValueError('Aircraft latest budget exceeded')
    history_bytes, total = 0, 0
    for path in out.rglob('*'):
        if path.is_symlink():
            raise ValueError('Aircraft output symlink refused')
        if not path.is_file():
            continue
        rel = path.relative_to(out).as_posix()
        if rel not in ['aircraft_latest.json', 'status_latest.json', 'history_format.json']:
            if not rel.startswith('history/') or path.suffix != '.jsonl':
                raise ValueError('Unexpected aircraft output file')
            history_bytes += path.stat().st_size
        total += path.stat().st_size
    metadata = sum((root / p).stat().st_size for p in ['config/special_aircraft_registry.json', 'config/aircraft_collection.json'])
    extra = sum(p.stat().st_size for d in [root / 'data/aircraft', root / 'public/downloads/aircraft']
                for p in d.rglob('*') if p.is_file())
    if (history_bytes > policy['max_history_bytes'] or total + metadata + extra > policy['max_total_bytes']
            or metadata + (out / 'status_latest.json').stat().st_size > policy['max_registry_status_bytes']):
        raise ValueError('Combined aircraft budget exceeded')
    return latest


def publish(root=ROOT, size_reader=repository_size_bytes, push=True):
    if os.environ.get('GITHUB_REF_NAME', 'main') != 'main':
        raise ValueError('Production aircraft writes require main')
    if not (root / PREFIX).exists():
        return 'no_aircraft_output'
    staged = git(root, 'diff', '--cached', '--name-only').splitlines()
    if any(not p.startswith(PREFIX) for p in staged):
        raise ValueError('Unrelated staged changes refused by aircraft publisher')
    git(root, 'add', '-A', '--', 'public/data/aircraft')
    paths = git(root, 'diff', '--cached', '--name-only').splitlines()
    if not paths:
        return 'no_changes'
    from runtime_data_store import ensure_no_unstaged
    ensure_no_unstaged()
    validate_outputs(root)
    policy = read_json(root / 'config/storage_policy.json')
    pending = sum(int(git(root, 'cat-file', '-s', ':' + p))
                  for p in git(root, 'diff', '--cached', '--name-only', '--diff-filter=ACMR').splitlines())
    remote = size_reader('mowlsint/VoodooWhiskers')
    check(policy, remote, pending, active_vessel_bytes(root))
    latest = validate_outputs(root)
    if remote >= policy['warning_bytes'] and latest.get('collection_code') != 'repository_warning_budget':
        raise ValueError('Repository reached warning budget; aircraft publication paused')
    if not push:
        git(root, 'commit', '-m', 'Update bounded special aircraft observations')
        return 'local_commit_only'
    subprocess.run([sys.executable, str(root / 'scripts/runtime_data_store.py'),
                    'publish', 'Update bounded special aircraft observations'],
                   cwd=root, check=True, env=os.environ.copy())
    return 'published'


def main():
    try:
        print(json.dumps({'aircraft_publication': publish()}, sort_keys=True))
    except Exception:
        print(json.dumps({'aircraft_publication': 'refused', 'ship_ingest_changed': False}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
