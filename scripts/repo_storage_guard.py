"""Report storage diagnostics before allowing or refusing a commit; never rewrite history.

Unique blob volume is informational. Budgets still count every staged path in full.
"""
import json
import os
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def inspect_staged_details(root=ROOT):
    names = subprocess.check_output([
        'git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'
    ], cwd=root).decode().split('\0')
    entries, unique, errors = [], {}, []
    for name in filter(None, names):
        low = name.lower()
        if (name == 'public/data/sar/workbench.enc.json'
                or low.startswith(('data/raw/', 'tmp/aisdk/'))
                or (Path(low).name.startswith('aisdk-') and low.endswith(('.zip', '.csv')))):
            errors.append('Raw or permanent SAR data is staged')
        size = int(subprocess.check_output(['git', 'cat-file', '-s', ':' + name], cwd=root))
        oid = subprocess.check_output(['git', 'rev-parse', ':' + name], cwd=root).decode().strip()
        if name == 'public/data/vessels/traffic_context_latest.json' and size > 1024 * 1024:
            errors.append('Traffic context exceeds the 1 MiB latest-only budget')
        if size >= 50 * 1024 * 1024:
            errors.append('Staged file reaches the 50 MiB application guard')
        entries.append({'path': name, 'bytes': size})
        unique[oid] = size
    total = sum(entry['bytes'] for entry in entries)
    unique_bytes = sum(unique.values())
    return {
        'staged_file_count': len(entries),
        'staged_blob_bytes': total,
        'unique_staged_blob_bytes': unique_bytes,
        'duplicate_staged_path_bytes': total - unique_bytes,
        'largest_staged_files': sorted(entries, key=lambda e: (-e['bytes'], e['path']))[:10],
        'errors': list(dict.fromkeys(errors)),
    }


def inspect_staged(root=ROOT):
    """Preserve the existing integer-returning interface and rejection rules."""
    details = inspect_staged_details(root)
    if details['errors']:
        raise ValueError(details['errors'][0])
    return details['staged_blob_bytes']


def budget_errors(policy, repository_bytes, pending_bytes, active_bytes):
    errors = []
    if pending_bytes > policy['max_new_push_bytes']:
        errors.append('New data exceeds push safety budget')
    # Preserve twice the FULL staged path volume; do not assume Git compression.
    if repository_bytes + 2 * pending_bytes > policy['repository_safety_bytes']:
        errors.append('Repository safety budget would be exceeded; history review required')
    if active_bytes > policy['active_vessel_products_bytes']:
        errors.append('Active vessel data budget exceeded')
    return errors


def check(policy, repository_bytes, pending_bytes, active_bytes):
    errors = budget_errors(policy, repository_bytes, pending_bytes, active_bytes)
    if errors:
        raise ValueError(errors[0])


def repository_size_bytes(repo):
    headers = {'Accept': 'application/vnd.github+json'}
    if os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    with urlopen(Request('https://api.github.com/repos/' + repo, headers=headers), timeout=30) as response:
        return int(json.load(response)['size']) * 1024


def active_vessel_bytes(root=ROOT):
    paths = [root / 'data/history', root / 'data/voi_history_14d',
             root / 'public/data/vessels', root / 'public/downloads']
    files = {p for path in paths if path.exists() for p in path.rglob('*') if p.is_file()}
    files.update(p for pattern in ('voi*.json*', 'gfw*.json*', 'ais_contacts*.json*')
                 for p in (root / 'data').glob(pattern))
    return sum(p.stat().st_size for p in files)


def main(root=ROOT):
    report = {'history_rewritten': False, 'within_safety_budget': False, 'errors': []}
    try:
        policy = json.loads((root / 'config/storage_policy.json').read_text())
        report['limits'] = {key: policy[key] for key in (
            'max_new_push_bytes', 'repository_safety_bytes', 'active_vessel_products_bytes')}
        details = inspect_staged_details(root)
        report.update(details)
        active = active_vessel_bytes(root)
        report['active_vessel_bytes'] = active
        pending = details['staged_blob_bytes']
        report['staged_headroom_bytes'] = policy['max_new_push_bytes'] - pending
        report['active_vessel_headroom_bytes'] = policy['active_vessel_products_bytes'] - active
        try:
            remote = repository_size_bytes(os.environ.get('GITHUB_REPOSITORY', 'mowlsint/VoodooWhiskers'))
        except Exception:
            # No response bodies, credentials or exception URLs enter diagnostics.
            remote = None
            report['errors'].append('Repository size unavailable; refusing commit')
        report['remote_repository_bytes'] = remote
        report['repository_size_basis'] = 'github_reported_size_kib_estimate'
        report['reserved_staged_bytes'] = 2 * pending
        report['projected_repository_bytes'] = None if remote is None else remote + 2 * pending
        report['repository_headroom_bytes'] = (None if remote is None else
                                              policy['repository_safety_bytes'] - remote - 2 * pending)
        # Local checks remain visible even when the remote size is unavailable.
        errors = budget_errors(policy, remote or 0, pending, active)
        if remote is None:
            errors = [e for e in errors if not e.startswith('Repository safety budget')]
        report['errors'].extend(errors)
        report['within_safety_budget'] = not report['errors']
    except Exception:
        report['errors'].append('Storage inspection failed; refusing commit')
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report['within_safety_budget'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
