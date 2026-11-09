"""Bounded encrypted SAR state in expiring Actions artifacts, outside Git history.

No provider plaintext is stored here. Only this workflow's narrowly named artifacts
on main can be restored/removed. The newest previous state is kept until upload.
"""
import argparse
import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'public/data/sar/workbench.enc.json'
NAME='sar-workbench-encrypted'
MAX_BYTES=24*1024*1024
BUDGET=96*1024*1024


def envelope(data):
    if len(data)>MAX_BYTES:raise ValueError('Encrypted state exceeds limit')
    value=json.loads(data)
    if set(value)!={'v','purpose','kdf','iterations','salt','nonce','ciphertext'} or value['purpose']!='workspace' or value['v']!=1 or value['iterations']!=600000:raise ValueError('Not an encrypted SAR workspace')
    return data


def unpack(data):
    if len(data)>MAX_BYTES:raise ValueError('Artifact archive exceeds limit')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=archive.infolist()
        if len(entries)!=1 or entries[0].filename!='workbench.enc.json' or entries[0].file_size>MAX_BYTES:raise ValueError('Unexpected artifact content')
        return envelope(archive.read(entries[0]))


class Store:
    def __init__(self,repository,token,session=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository) or not token:raise ValueError('Runner context missing')
        self.repository=repository;self.base=f'https://api.github.com/repos/{repository}';self.http=session or requests.Session()
        self.headers={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}

    def artifacts(self):
        all_items=[]
        for page in range(1,11):
            r=self.http.get(self.base+'/actions/artifacts',headers=self.headers,params={'per_page':100,'page':page},timeout=(10,45));r.raise_for_status();pack=r.json();items=pack['artifacts'];all_items.extend(items)
            if len(items)<100:return all_items
        raise ValueError('Artifact inventory exceeds safe bound')

    def managed(self,items):
        return sorted([a for a in items if a.get('name')==NAME and not a.get('expired') and a.get('workflow_run',{}).get('head_branch')=='main'],key=lambda a:a['created_at'],reverse=True)

    def restore(self,items,path=PATH):
        owned=self.managed(items)
        if not owned:return False
        # Do not silently revert to older state if the newest state cannot be read.
        a=owned[0]
        r=self.http.get(self.base+f"/actions/artifacts/{int(a['id'])}/zip",headers=self.headers,timeout=(10,60),stream=True);r.raise_for_status()
        blocks=[];size=0
        for block in r.iter_content(65536):
            size+=len(block)
            if size>MAX_BYTES:raise ValueError('Artifact response exceeds bound')
            blocks.append(block)
        raw=b''.join(blocks);digest=a.get('digest')
        if digest and digest!='sha256:'+hashlib.sha256(raw).hexdigest():raise ValueError('Artifact digest mismatch')
        content=unpack(raw);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content);return True

    def reserve(self,items,pending_bytes):
        if not 0<pending_bytes<=MAX_BYTES:raise ValueError('Artifact size bound')
        owned=self.managed(items);protected=owned[0]['id'] if owned else None
        total=sum(int(a.get('size_in_bytes',0)) for a in owned);removed=[]
        # Remove redundant older copies only; the current encrypted state already contains its own 14-day bounded job history.
        for a in reversed(owned):
            if total+pending_bytes+65536<=BUDGET:break
            if a['id']==protected:continue
            r=self.http.delete(self.base+f"/actions/artifacts/{int(a['id'])}",headers=self.headers,timeout=(10,45));r.raise_for_status();total-=int(a['size_in_bytes']);removed.append(a['id'])
        if total+pending_bytes+65536>BUDGET:raise ValueError('SAR artifact budget reached')
        # A repository-wide guard is conservative. Account usage in other repos remains outside this tool's scope.
        others=sum(int(a.get('size_in_bytes',0)) for a in items if not a.get('expired') and a['id'] not in removed)
        if others+pending_bytes+65536>400*1024*1024:raise ValueError('Repository artifact safety budget reached')
        return removed


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['restore','reserve']);args=p.parse_args()
    try:
        store=Store(os.environ.get('GITHUB_REPOSITORY',''),os.environ.get('SAR_ARTIFACT_TOKEN',''));items=store.artifacts()
        if args.action=='restore':
            if sum(int(a.get('size_in_bytes',0)) for a in items if not a.get('expired') and a.get('name')!=NAME)+MAX_BYTES>400*1024*1024:raise ValueError('Artifact space unavailable')
            store.restore(items)
            digest=hashlib.sha256(PATH.read_bytes()).hexdigest() if PATH.exists() else 'none'
            if os.environ.get('GITHUB_ENV'):
                with open(os.environ['GITHUB_ENV'],'a') as f:f.write('SAR_STATE_INPUT_SHA256='+digest+'\n')
        else:
            content=envelope(PATH.read_bytes());changed=hashlib.sha256(content).hexdigest()!=os.environ.get('SAR_STATE_INPUT_SHA256')
            if changed:store.reserve(items,len(content))
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('changed='+str(changed).lower()+'\n')
    except Exception:
        print('SAR-Artefaktspeicher konnte nicht sicher gelesen oder begrenzt werden. Keine Git-Ausweichablage.');return 1
    print('Verschlüsselte SAR-Ablage vorbereitet.');return 0

if __name__=='__main__':raise SystemExit(main())
