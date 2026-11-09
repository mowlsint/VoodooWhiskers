"""Fail before commit/push when application budgets would be exceeded; never rewrite Git history."""
import json
import os
import subprocess
from pathlib import Path

from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]


def inspect_staged(root=ROOT):
    names=subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR','-z'],cwd=root).decode().split('\0')
    pending=0
    for name in filter(None,names):
        low=name.lower()
        if name=='public/data/sar/workbench.enc.json' or (low.startswith(('data/raw/','tmp/aisdk/'))) or (Path(low).name.startswith('aisdk-') and low.endswith(('.zip','.csv'))):raise ValueError('Raw or permanent SAR data is staged')
        size=int(subprocess.check_output(['git','cat-file','-s',':'+name],cwd=root))
        if size>=50*1024*1024:raise ValueError('Staged file reaches the 50 MiB application guard')
        pending+=size
    return pending


def check(policy,repository_bytes,pending_bytes,active_bytes):
    if pending_bytes>policy['max_new_push_bytes']:raise ValueError('New data exceeds push safety budget')
    # Reserve twice the full staged blob volume, without relying on delta compression.
    if repository_bytes+2*pending_bytes>policy['repository_safety_bytes']:raise ValueError('Repository safety budget would be exceeded; history review required')
    if active_bytes>policy['active_vessel_products_bytes']:raise ValueError('Active vessel data budget exceeded')


def main():
    cfg=json.loads((ROOT/'config/storage_policy.json').read_text());pending=inspect_staged();repo=os.environ.get('GITHUB_REPOSITORY','mowlsint/VoodooWhiskers')
    headers={'Accept':'application/vnd.github+json'}
    if os.environ.get('GITHUB_TOKEN'):headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
    with urlopen(Request('https://api.github.com/repos/'+repo,headers=headers),timeout=30) as response:remote_bytes=int(json.load(response)['size'])*1024
    paths=[ROOT/'data/history',ROOT/'data/voi_history_14d',ROOT/'public/data/vessels',ROOT/'public/downloads']
    files={p for path in paths if path.exists() for p in path.rglob('*') if p.is_file()}
    files.update(p for pattern in ('voi*.json*','gfw*.json*','ais_contacts*.json*') for p in (ROOT/'data').glob(pattern))
    active=sum(p.stat().st_size for p in files)
    check(cfg,remote_bytes,pending,active)
    print(json.dumps({'remote_repository_bytes':remote_bytes,'staged_blob_bytes':pending,'active_vessel_bytes':active,'history_rewritten':False,'within_safety_budget':True}))
    return 0

if __name__=='__main__':raise SystemExit(main())
