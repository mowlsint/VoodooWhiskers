#!/usr/bin/env python3
"""Refresh official vessel designations, preserving every curated watchlist row.

Raw authority downloads stay in memory. Only normalized vessel records and audit
metadata are stored. No automatic deletion, name-only match or shadow-fleet inference.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT/"data/watchlist_master.csv"
SOURCES = {
    "eu": {"url": "https://www.dma.dk/growth-and-framework-conditions/maritime-sanctions/general-information/eu-vessel-designations", "max_bytes": 2*1024*1024},
    "uk": {"url": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv", "max_bytes": 64*1024*1024},
    "ofac": {"url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV", "max_bytes": 12*1024*1024},
}
STATUS = ROOT/"data/sanctions_refresh_status_latest.json"
SOURCE_DIR = ROOT/"data/sanctions_sources"


def valid_imo(value):
    text = str(value or "").strip()
    match = re.fullmatch(r"(?:IMO\s*)?(\d{7})", text, re.I)
    if not match:
        return ""
    digits = match[1]
    return digits if sum(int(n)*weight for n, weight in zip(digits[:6], range(7, 1, -1)))%10 == int(digits[-1]) else ""


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False, newline="") as out:
        out.write(text); name=out.name
    Path(name).replace(path)


def atomic_json(path, payload):
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2)+"\n")


def atomic_source(path, payload):
    meta={k:v for k,v in payload.items() if k!='vessels'}
    text=json.dumps(meta,ensure_ascii=False,indent=2).rstrip()[:-1]
    records=",\n".join(json.dumps(v,ensure_ascii=False,separators=(',',':')) for v in payload['vessels'])
    atomic_text(path,text.rstrip()+',\n"vessels":[\n'+records+'\n]}\n')


def parse_uk(text):
    lines = text.lstrip("\ufeff").splitlines()
    header = next((i for i, line in enumerate(lines[:10]) if "Unique ID" in line and "IMO number" in line), None)
    if header is None:
        raise ValueError("UK vessel CSV headers missing")
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[header:]))))
    records, ignored = [], 0
    for row in rows:
        kind = row.get("Designation Type") or row.get("Individual, Entity, Ship")
        if str(kind).strip().lower() != "ship":
            continue
        imo = valid_imo(row.get("IMO number"))
        if not imo:
            ignored += 1
            continue
        records.append({"imo": imo, "name": row.get("Name 6", "").strip(),
                        "source_tag": "UK_"+row["Unique ID"].strip(), "designation_id": row["Unique ID"].strip(),
                        "measures": row.get("Sanctions Imposed", ""), "regime": row.get("Regime Name", ""),
                        "source_updated": row.get("Last Updated", ""), "name_type": row.get("Name type", "")})
    report_date = lines[0].split(":", 1)[-1].strip() if header else None
    return records, {"source_report_date": report_date, "unresolved_vessels_without_valid_imo": ignored}


def parse_ofac(text):
    records, ignored = [], 0
    for row in csv.reader(io.StringIO(text.lstrip("\ufeff"))):
        if len(row) < 12 or row[2].strip().lower() != "vessel":
            continue
        # IMO is in the labelled remarks field of SDN.CSV, not the flag, tonnage or owner fields.
        imos = {valid_imo(m) for m in re.findall(r"IMO\s*(?:Number|No\.?|#)?\s*[:.]?\s*(\d{7})\b", row[11], re.I)}-{''}
        if len(imos) != 1:
            ignored += 1
            continue
        records.append({"imo": imos.pop(), "name": row[1].strip(), "source_tag": "OFAC_SDN_"+row[0].strip(),
                        "designation_id": row[0].strip(), "measures": "SDN listing", "regime": row[3].strip(), "source_updated": None})
    return records, {"unresolved_vessels_without_valid_imo": ignored}


class VesselTables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables=[]; self.table_contexts=[]; self.table=None; self.row=None; self.cell=None
        self.heading=None;self.last_heading=""
    def handle_starttag(self, tag, attrs):
        if tag == "h2":self.heading=[]
        if tag == "table": self.table=[];self.table_contexts.append(self.last_heading)
        if tag == "tr" and self.table is not None: self.row=[]
        if tag in {"th", "td"} and self.row is not None: self.cell=[]
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
        if self.heading is not None:self.heading.append(data)
    def handle_endtag(self, tag):
        if tag == "h2" and self.heading is not None:self.last_heading=" ".join(self.heading);self.heading=None
        if tag in {"th", "td"} and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split())); self.cell=None
        if tag == "tr" and self.row is not None:
            self.table.append(self.row);self.row=None
        if tag == "table" and self.table is not None:
            self.tables.append(self.table);self.table=None


def parse_eu(text):
    parser = VesselTables(); parser.feed(text)
    records=[]
    for table, context in zip(parser.tables, parser.table_contexts):
        if not table or "imo" not in " ".join(table[0]).lower(): continue
        dprk="DPRK" in context
        if not dprk and "date of application" not in " ".join(table[0]).lower():continue
        for row in table[1:]:
            if len(row) < 3: continue
            imo=valid_imo(row[1])
            if not imo: continue
            records.append({"imo": imo, "name": row[0], "source_tag": "EU_DPRK_Vessel" if dprk else "EU_Annex_XLII", "designation_id": imo,
                            "measures": row[3] if dprk and len(row)>3 else "Vessel access/service restrictions (Annex XLII)", "source_updated": row[2]})
    updated = re.search(r"Last updated\s*(\d{2}/\d{2}/\d{4})", text, re.I)
    return records, {"source_report_date": updated[1] if updated else None,
                     "scope": "EU Russia Annex XLII and DPRK vessel designations published by DMA, distinguished by regime; not every EU financial sanctions regime"}


PARSERS = {"eu": parse_eu, "uk": parse_uk, "ofac": parse_ofac}


def get_source(key, session, now, max_age_hours=24, local_file=None):
    config=SOURCES[key]; path=SOURCE_DIR/f"{key}_vessels.json"
    previous=json.loads(path.read_text()) if path.exists() else {}
    checked=previous.get("checked_at")
    if local_file is None and checked and (now-datetime.fromisoformat(checked)).total_seconds()<max_age_hours*3600:
        return previous, {"ok": True, "cache_hit": True, "count": len(previous.get("vessels", [])), "checked_at": checked}
    if local_file:
        content=local_file.read_bytes(); headers={}
    else:
        headers={"If-None-Match": previous["etag"]} if previous.get("etag") else {}
        with session.get(config["url"], headers=headers, timeout=(10, 60), stream=True) as response:
            if response.status_code == 304:
                previous["checked_at"]=now.isoformat(); atomic_source(path,previous)
                return previous, {"ok":True,"not_modified":True,"count":len(previous["vessels"]),"checked_at":now.isoformat()}
            response.raise_for_status()
            chunks=[]; size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>config["max_bytes"]:raise ValueError("authority download exceeds bounded size")
                chunks.append(chunk)
            content=b''.join(chunks);headers=response.headers
    if len(content)>config["max_bytes"]:raise ValueError("authority file exceeds bounded size")
    try:text=content.decode("utf-8-sig")
    except UnicodeDecodeError:text=content.decode("cp1252")
    records, meta=PARSERS[key](text)
    # Fail closed on a changed schema, HTML error page or implausibly truncated list.
    minimum={"eu":100,"uk":100,"ofac":100}[key]
    unique={r["imo"] for r in records}
    if len(unique)<minimum:raise ValueError("too few valid IMO-designated vessels; previous snapshot retained")
    if previous and len(unique)<.8*len({r['imo'] for r in previous.get('vessels', [])}):
        raise ValueError("unexpected designation count reduction; manual review required")
    snapshot={"schema_version":"1.0.0","authority":key,"source_url":config["url"],"checked_at":now.isoformat(),
              "sha256":hashlib.sha256(content).hexdigest(),"download_bytes":len(content),
              "etag":headers.get("ETag"),"last_modified":headers.get("Last-Modified"),
              "vessels":records,**meta}
    atomic_source(path,snapshot)
    return snapshot,{"ok":True,"count":len(unique),"checked_at":now.isoformat(),"download_bytes":len(content),**meta}


def merge_watchlist(rows, snapshots, checked_at):
    rows=[dict(row) for row in rows]
    index={}
    for row in rows:
        imo=valid_imo(row.get("imo"))
        if imo:index.setdefault(imo,[]).append(row)
    added=[]; verified=set(); authorities={}
    for key, snapshot in snapshots.items():
        authorities[key]={record["imo"] for record in snapshot["vessels"]}
        for record in snapshot["vessels"]:
            imo=record["imo"]; verified.add(imo)
            if imo not in index:
                row={"name":record["name"],"imo":imo,"track_sanctions":"true","track_shadowfleet":"false",
                     "track_falseflag":"false","watch_priority":"high","notes":"Official vessel designation; no automatic shadow-fleet attribution."}
                rows.append(row);index[imo]=[row];added.append(imo)
            for row in index[imo]:
                row["source_list"]=";".join(sorted(set(filter(None,(row.get("source_list","")+";"+record["source_tag"]).split(";")))))
                row["source_url"]=";".join(sorted(set(filter(None,(row.get("source_url","")+";"+snapshot["source_url"]).split(";")))))
                row["track_sanctions"]="true"
                row["source_last_checked"]=checked_at
                row["source_status"]="confirmed_current"
                row["sanctions_verification_status"]="confirmed_in_refreshed_source"
    review=[]
    for row in rows:
        imo=valid_imo(row.get("imo"))
        if not imo:continue
        if imo in verified: continue
        tags=row.get("source_list","")
        claimed={k for k, prefix in [("eu","EU_Annex_XLII"),("uk","UK_"),("ofac","OFAC_SDN_")] if prefix in tags}
        # Absence only creates a review lead after every claimed supported authority succeeded.
        if claimed and claimed <= snapshots.keys():
            row["sanctions_verification_status"]="manual_review_required"
            row["source_status"]="manual_review_required"
            review.append({"imo":imo,"name":row.get("name"),"reason":"not found in refreshed supported designations; retained for manual review"})
    return rows,{"added_imos":sorted(set(added)),"confirmed_imos":len(verified),"manual_review":review,
                 "rows_before":len(rows)-len(added),"rows_after":len(rows),"automatically_deleted":0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch",action="store_true",help="Explicitly fetch official public files")
    parser.add_argument("--input-dir",type=Path,help="Use authority files eu.html, uk.csv, ofac.csv")
    args=parser.parse_args()
    if not args.fetch and not args.input_dir:raise SystemExit("Use --fetch or --input-dir; no network performed")
    now=datetime.now(timezone.utc).replace(microsecond=0)
    snapshots,status={},{}
    session=requests.Session();session.headers["User-Agent"]="MOwlSINT-VoodooWhiskers-Sanctions/1.0"
    for key in SOURCES:
        try:
            file=args.input_dir/(key+(".html" if key=="eu" else ".csv")) if args.input_dir else None
            snapshot,state=get_source(key,session,now,local_file=file)
            snapshots[key]=snapshot;status[key]=state
        except Exception as exc:
            status[key]={"ok":False,"error":type(exc).__name__,"previous_snapshot_retained":True}
    with WATCH.open(encoding="utf-8-sig",newline="") as src:
        reader=csv.DictReader(src); fields=list(reader.fieldnames or []);rows=list(reader)
    updated,changes=merge_watchlist(rows,snapshots,now.date().isoformat())
    if snapshots:
        fields=list(dict.fromkeys(fields+[key for row in updated for key in row]))
        out=io.StringIO(newline="");writer=csv.DictWriter(out,fieldnames=fields,lineterminator="\n");writer.writeheader();writer.writerows(updated)
        atomic_text(WATCH,out.getvalue())
    atomic_json(STATUS,{"schema_version":"1.0.0","generated_at":now.isoformat(),"sources":status,**changes,
        "raw_authority_files_persisted":False,"unsupported_existing_authorities":"JP/AU and other curated sources retained; no automatic current-status claim from this updater",
        "all_sources_ok":all(v["ok"] for v in status.values())})
    print(json.dumps({"sources":status,"added":len(changes["added_imos"]),"manual_review":len(changes["manual_review"]),"deleted":0}))
    return 0 if snapshots else 1


if __name__=="__main__":raise SystemExit(main())
