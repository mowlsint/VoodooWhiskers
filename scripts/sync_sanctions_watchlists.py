#!/usr/bin/env python3
"""
VOODOO WHISKERS // Sanctions watchlist audit helper

Conservative by design: this script does not delete local watchlist rows. It creates
an audit sidecar and may be extended to merge official EU/UK/OFAC CSV/JSON exports
when those files are supplied in data/sanctions_sources/.
"""
import csv, json
from pathlib import Path
from datetime import datetime, timezone

DATA = Path("data")
WATCH = DATA / "watchlist_master.csv"
AUDIT = DATA / "watchlist_audit.json"
CHANGES = DATA / "watchlist_changes_latest.md"

def clean(v): return "" if v is None else str(v).strip()
def digits(v): return "".join(ch for ch in clean(v) if ch.isdigit())

def read_rows(path):
    if not path.exists(): return [], []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return list(r), list(r.fieldnames or [])

def row_key(r):
    return digits(r.get("imo")) or digits(r.get("mmsi")) or clean(r.get("callsign")).upper() or clean(r.get("name")).upper()

def main():
    rows, fields = read_rows(WATCH)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    seen = {}
    duplicates = []
    test_rows = []
    missing_ids = []
    sanctions = 0
    shadow = 0
    for i, r in enumerate(rows, start=2):
        key = row_key(r)
        if not key: missing_ids.append(i)
        if key and key in seen: duplicates.append({"line": i, "duplicates_line": seen[key], "key": key, "name": clean(r.get("name"))})
        if key and key not in seen: seen[key] = i
        hay = " ".join(clean(r.get(k)) for k in ["name","notes","source_list","source_url"]).lower()
        if "example" in hay or "test_only" in hay or "dummy" in hay or "sample" in hay:
            test_rows.append({"line": i, "key": key, "name": clean(r.get("name"))})
        if clean(r.get("track_sanctions")).lower() in {"1","true","yes","y"}: sanctions += 1
        if clean(r.get("track_shadowfleet")).lower() in {"1","true","yes","y"}: shadow += 1
    audit = {
        "generated_at": now,
        "rows": len(rows),
        "unique_identity_keys": len(seen),
        "track_sanctions_rows": sanctions,
        "track_shadowfleet_rows": shadow,
        "duplicates": duplicates,
        "missing_identity_rows": missing_ids,
        "test_or_example_rows": test_rows,
        "policy": "No automatic deletion. Missing official confirmation should become source_status=source_missing/manual_watch_only, not deletion."
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    lines = ["# VOODOO WHISKERS Watchlist Audit", "", f"Generated: {now}", "", f"Rows: {len(rows)}", f"Unique keys: {len(seen)}", f"Sanctions rows: {sanctions}", f"Shadow-fleet rows: {shadow}", f"Duplicates: {len(duplicates)}", f"Missing identity rows: {len(missing_ids)}", f"Test/example rows: {len(test_rows)}", "", "No rows were deleted automatically."]
    CHANGES.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(audit, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
