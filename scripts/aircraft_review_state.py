"""Bounded source digest state. A changed digest is a review lead, not a fleet change."""
import json
from pathlib import Path
LIMIT=256*1024
def read_baseline(path):
    if not path.is_file() or path.stat().st_size>LIMIT:return {}
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (ValueError,OSError):return {}
def compare_sources(sources,previous,checked_at):
    current=dict(previous)
    out=[]
    for source in sources:
        row=dict(source)
        url=row["url"]
        old=previous.get(url,{})
        row["previous_success_at"]=old.get("checked_at")
        row["previous_sha256"]=old.get("sha256")
        if not row.get("ok"):
            row["change_status"]="unavailable"
        elif not old.get("sha256"):
            row["change_status"]="baseline_created"
        elif old["sha256"]==row["sha256"]:
            row["change_status"]="unchanged"
        else:
            row["change_status"]="changed"
        if row.get("ok"):
            current[url]={"checked_at":checked_at,"sha256":row["sha256"]}
        out.append(row)
    return out,current
def save_baseline(path,data):
    body=json.dumps(data,ensure_ascii=False,separators=(",",":"))
    if len(body.encode())>LIMIT:raise ValueError("Source baseline exceeds budget")
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(body,encoding="utf-8")
