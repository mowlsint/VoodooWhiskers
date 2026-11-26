"""Publish curated flag-integrity references. No new AIS polling."""
import csv,json,shutil,tempfile
from pathlib import Path
def read_rows(p):
    with p.open(encoding="utf-8",newline="") as f: return list(csv.DictReader(f))
def valid_imo(i):
    return len(i)==7 and i.isdigit() and sum(int(d)*w for d,w in zip(i[:6],range(7,1,-1)))%10==int(i[-1])
def publish_catalog(root):
    vessels=read_rows(root/"data/false_flag_watchlist.csv")
    flags=read_rows(root/"data/flag_risk_reference.csv")
    assert len({r["imo"] for r in vessels})==len(vessels)
    assert len({r["iso_code"] for r in flags})==len(flags)
    for r in vessels:
        assert valid_imo(r["imo"]) or r["identity_status"]=="invalid_checksum_review",r
        assert r["source_url"].startswith("https://"),r
        assert r["false_flag_temporal_status"] in {"current_confirmed","current_suspected","historical_unresolved","historical_reflagged","historical_inactive"},r
    for r in flags:
        assert r["review_class"] in {"A","B","C","D"} and r["source_url"].startswith("https://"),r
    directory=root/"public/downloads"
    directory.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=directory,delete=False) as f:
        json.dump({"schema_version":"1.0","assessment_limit":"Flag claims and MID context do not prove nationality, statelessness, sanctions or illegality.","vessels":vessels,"jurisdictions":flags},f,ensure_ascii=False,separators=(",",":"))
    Path(f.name).replace(directory/"flag_integrity.json")
    for n in ("false_flag_watchlist.csv","flag_risk_reference.csv"): shutil.copyfile(root/"data"/n,directory/n)
if __name__=="__main__":publish_catalog(Path(__file__).resolve().parents[1])
