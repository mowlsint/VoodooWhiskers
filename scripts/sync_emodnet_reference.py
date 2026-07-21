#!/usr/bin/env python3
from __future__ import annotations
import json, re, sys
from pathlib import Path
from urllib.parse import urlencode
import requests
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/'config/emodnet_layers.json').read_text(encoding='utf-8'))
OUT=ROOT/'public/data/reference/emodnet'; OUT.mkdir(parents=True,exist_ok=True)

def txt(node, suffix):
    for el in node.iter():
        if el.tag.endswith(suffix): return (el.text or '').strip()
    return ''

def main():
    service=CFG['service_url']
    cap=requests.get(service,params={'SERVICE':'WFS','REQUEST':'GetCapabilities','VERSION':'2.0.0'},timeout=60)
    cap.raise_for_status(); root=ET.fromstring(cap.content)
    types=[]
    for ft in root.iter():
        if ft.tag.endswith('FeatureType'):
            name=txt(ft,'Name'); title=txt(ft,'Title')
            if name: types.append({'name':name,'title':title})
    manifest={'schema_version':'1.0.0','source':'EMODnet Human Activities WFS','service_url':service,'bbox':CFG['bbox'],'layers':[]}
    for layer in CFG['layers']:
        if not layer.get('enabled',True): continue
        pats=[re.compile(p,re.I) for p in layer.get('title_patterns',[])+layer.get('name_patterns',[])]
        match=next((x for x in types if any(p.search(x['title']+' '+x['name']) for p in pats)),None)
        rec={'id':layer['id'],'matched':bool(match),'feature_type':match['name'] if match else None,'title':match['title'] if match else None}
        if match:
            minx,miny,maxx,maxy=CFG['bbox']
            params={'service':'WFS','version':'1.1.0','request':'GetFeature','typeName':match['name'],'bbox':f'{minx},{miny},{maxx},{maxy},EPSG:4326','outputFormat':'application/json','srsName':'EPSG:4326'}
            r=requests.get(service,params=params,timeout=180); r.raise_for_status()
            data=r.json(); data['source_meta']={'source':'EMODnet Human Activities','feature_type':match['name'],'title':match['title']}
            (OUT/f"{layer['id']}.geojson").write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
            rec['feature_count']=len(data.get('features') or [])
        manifest['layers'].append(rec)
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__': main()
