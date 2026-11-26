#!/usr/bin/env python3
"""Read-only weekly source audit. Never promotes a lead to a confirmed vessel."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from flag_integrity import read_rows, valid_imo

ROOT=Path(__file__).resolve().parents[1]
IMO_URL="https://www.imo.org/en/ourwork/legal/pages/fraudulent-registration-and-fraudulent-registries-of-ships.aspx"
OFAC_URL="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
TREASURY_URL="https://home.treasury.gov/news/press-releases/sb0681/"
ITU_URL="https://www.itu.int/gladapp/Allocation/MIDs"

def fetch(url, limit=32*1024*1024):
    req=Request(url,headers={"User-Agent":"VoodooWhiskers-FlagIntegrity/1.0"})
    with urlopen(req,timeout=45) as response:
        payload=response.read(limit+1)
    if len(payload)>limit:
        raise ValueError("Source exceeds bounded fetch size")
    return payload

class CircularLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links=[]
        self.current=None
    def handle_starttag(self,tag,attrs):
        href=dict(attrs).get("href","")
        if tag=="a" and href.startswith("https://docs.imo.org/"):
            self.current={"url":href,"label":""}
    def handle_data(self,data):
        if self.current is not None:self.current["label"]+=data
    def handle_endtag(self,tag):
        if tag=="a" and self.current is not None:
            self.current["label"]=" ".join(self.current["label"].split())
            self.links.append(self.current)
            self.current=None

def ofac_candidates(payload, known):
    result=[]
    for row in csv.reader(io.StringIO(payload.decode("utf-8-sig",errors="replace"))):
        if len(row)<3 or row[2].strip().lower()!="vessel":continue
        # The SDN remarks may contain current or historical flag labels.
        text=" ".join(row[3:])
        if not re.search(r"\b(?:vessel\s+flag|flag)\b[^;\n]{0,80}\bfalse\b",text,re.I):continue
        ids={m for m in re.findall(r"\bIMO\s*(\d{7})\b",text,re.I) if valid_imo(m)}
        for imo in sorted(ids):
            result.append({"imo":imo,"name":row[1],"sdn_id":row[0],"already_in_reference":imo in known,"source_url":OFAC_URL,"status":"manual_review_required","reason":"Explicit false-flag text in SDN vessel record; date, flag and scope require manual review."})
    return result

def main():
    known={row["imo"] for row in read_rows(ROOT/"data/false_flag_watchlist.csv")}
    sources={}
    candidates=[]
    circulars=[]
    for label,url in (("imo",IMO_URL),("ofac",OFAC_URL),("treasury",TREASURY_URL),("itu",ITU_URL)):
        try:
            raw=fetch(url)
            info={"url":url,"ok":True,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
            if label=="imo":
                parser=CircularLinks();parser.feed(raw.decode("utf-8",errors="replace"));circulars=parser.links
                info["circular_count"]=len(circulars)
                if not circulars:raise ValueError("IMO circular links unavailable")
            if label=="ofac":
                candidates=ofac_candidates(raw,known)
                info["candidate_count"]=len(candidates)
            sources[label]=info
        except Exception as exc:
            sources[label]={"url":url,"ok":False,"error":str(exc)}
    out={"checked_at":datetime.now(timezone.utc).isoformat(),"policy":"Manual review only; failed sources do not indicate absence of evidence. No automatic changes to canonical records.","sources":sources,"imo_circular_index":circulars,"ofac_false_flag_candidates":candidates}
    path=ROOT/"flag-integrity-review.json"
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"sources":sources,"new_ofac_leads":sum(not r["already_in_reference"] for r in candidates)},indent=2))
    if not all(s["ok"] for s in sources.values()):return 1
    return 0
if __name__=="__main__":raise SystemExit(main())
