"""Daily source review and delayed aggregate history. No new aircraft collection."""
import hashlib,json,sys
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.request import Request,urlopen
from aircraft_directory import build
ROOT=Path(__file__).resolve().parents[1]
def main():
    catalog=build()
    now=datetime.now(timezone.utc)
    sources=[]
    # Curated programme pages are checked daily; rotate existing primary role sources
    # across a seven-day cycle. No aircraft tracker queries.
    registry=json.loads((ROOT/"config/special_aircraft_registry.json").read_text())
    primary=sorted((s for s in registry["sources"] if s["source_class"].startswith("primary_")),key=lambda s:s["url"])
    offset=now.date().toordinal()%7
    targets=list(catalog["programmes"])+[dict(name=s["title"],url=s["url"]) for i,s in enumerate(primary) if i%7==offset]
    seen=set()
    for item in targets:
        url=item["url"]
        if url in seen:continue
        seen.add(url)
        try:
            with urlopen(Request(url,headers={"User-Agent":"VoodooWhiskers registry source audit"}),timeout=15) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError("source size limit")
            sources.append(dict(name=item["name"],url=url,ok=True,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
        except Exception as e:sources.append(dict(name=item["name"],url=url,ok=False,error=str(e)[:200]))
    history=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"public/data/aircraft/history"
    days=Counter();invalid=0;slots=Counter();cutoff=(now-timedelta(days=1)).timestamp();lower=(now-timedelta(days=14)).timestamp()
    for path in sorted(history.glob("*/*.jsonl")):
        if path.stat().st_size>1048576:invalid+=1;continue
        for line in path.read_text().splitlines():
            try:
                row=json.loads(line);at=row[1]
                if not isinstance(at,(int,float)) or not lower<=at<cutoff:continue
                day=datetime.fromtimestamp(at,timezone.utc).date().isoformat()
                days[day]+=1;slots[(day,path.name)]+=1
            except (ValueError,TypeError,IndexError):invalid+=1
    summary=dict(checked_at=now.isoformat(),available=history.is_dir(),coverage_complete=False,invalid_rows=invalid,days=[dict(date=d,samples=n,slots=sum(k[0]==d for k in slots)) for d,n in sorted(days.items())],note="Delayed aggregate counts only; no flight paths or current missions inferred. Days are partial and missing days are not zero activity.")
    queue=[r for r in catalog["aircraft"] if not r["selected"]]
    report=dict(checked_at=now.isoformat(),sources=sources,review_queue=queue,history_summary=summary,policy="No automatic activation. Hash changes are review leads, not verified changes of assignment. No source failure implies absence.")
    (ROOT/"aircraft-directory-review.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (ROOT/"aircraft-history-summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({"sources_ok":sum(s["ok"] for s in sources),"sources_total":len(sources),"queue":len(queue),"days":len(days)}))
if __name__=="__main__":main()
