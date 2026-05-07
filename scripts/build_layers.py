import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

DATA_DIR = Path("data")
WATCHLIST_PATH = DATA_DIR / "watchlist_master.csv"

RUSSIAN_PORT_ALLOWLIST = {"RUKGD", "RUBLT", "RUULU"}
POLISH_PORT_DENYLIST = {"PLGDN", "PLGDY"}

LAYER_FILES = {
    "russian_mmsi": DATA_DIR / "russian_mmsi.geojson",
    "watchlist": DATA_DIR / "watchlist_live.geojson",
    "sanctions_shadowfleet": DATA_DIR / "sanctions_shadowfleet.geojson",
    "falseflag_interest": DATA_DIR / "falseflag_interest.geojson",
    "behavioral_voi": DATA_DIR / "behavioral_voi.geojson",
}

SNAPSHOT_PATH = DATA_DIR / "voi_snapshot_latest.json"
HISTORY_PATH = DATA_DIR / "voi_history.jsonl"
STATS_PATH = DATA_DIR / "voi_stats_by_slot.json"


def to_bool(v):
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


def clean_str(v):
    return "" if v is None else str(v).strip()


def clean_text(v):
    return clean_str(v)


def norm_text(v):
    s = clean_str(v).upper()
    s = re.sub(r"\s+", " ", s)
    return s


def norm_digits(v):
    return re.sub(r"\D", "", clean_str(v))


def parse_float(v):
    try:
        return float(v)
    except Exception:
        return None


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def current_slot():
    dt = datetime.now(timezone.utc)
    hour = dt.hour
    slots = [0, 5, 11, 20]
    slot = max([s for s in slots if s <= hour], default=0)
    return f"{dt.strftime('%Y-%m-%d')}T{slot:02d}:00:00Z"


def load_csv_rows(path):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_watchlist(path=WATCHLIST_PATH):
    rows = load_csv_rows(path)
    index = {"imo": {}, "mmsi": {}, "callsign": {}, "name": {}}

    for row in rows:
        imo = norm_digits(row.get("imo"))
        mmsi = norm_digits(row.get("mmsi"))
        callsign = norm_text(row.get("callsign"))
        name = norm_text(row.get("name"))

        if imo:
            index["imo"][imo] = row
        if mmsi:
            index["mmsi"][mmsi] = row
        if callsign:
            index["callsign"][callsign] = row
        if name:
            index["name"][name] = row

    return rows, index


def get_any(d, *keys):
    for k in keys:
        if k in d and clean_str(d.get(k)):
            return d.get(k)
    return ""


def match_watchlist(contact, index):
    imo = norm_digits(get_any(contact, "imo", "IMO", "ImoNumber"))
    mmsi = norm_digits(get_any(contact, "mmsi", "MMSI", "UserID"))
    callsign = norm_text(get_any(contact, "callsign", "CallSign"))

    if imo and imo in index["imo"]:
        return index["imo"][imo], "imo"

    if mmsi and mmsi in index["mmsi"]:
        return index["mmsi"][mmsi], "mmsi"

    if callsign and callsign in index["callsign"]:
        return index["callsign"][callsign], "callsign"

    return None, None


def extract_port_codes(contact):
    raw_values = [
        get_any(contact, "last_port_unlocode"),
        get_any(contact, "destination_unlocode"),
        get_any(contact, "port_unlocode"),
        get_any(contact, "port_code"),
        get_any(contact, "destination", "Destination"),
        get_any(contact, "last_port_name"),
        get_any(contact, "next_port"),
    ]

    codes = set()
    known = RUSSIAN_PORT_ALLOWLIST | POLISH_PORT_DENYLIST

    for raw in raw_values:
        txt = norm_text(raw)
        if not txt:
            continue
        for code in known:
            if code in txt:
                codes.add(code)

    return codes


def confirm_russian_port(contact):
    codes = extract_port_codes(contact)

    if codes & POLISH_PORT_DENYLIST:
        return False, sorted(codes)
    if codes & RUSSIAN_PORT_ALLOWLIST:
        return True, sorted(codes)
    return False, sorted(codes)


def build_vesselfinder_url(name="", imo="", mmsi=""):
    imo = norm_digits(imo)
    mmsi = norm_digits(mmsi)
    name = clean_text(name)

    if imo:
        return f"https://www.vesselfinder.com/vessels?name={quote_plus(imo)}"
    if mmsi:
        return f"https://www.vesselfinder.com/vessels?name={quote_plus(mmsi)}"
    if name:
        return f"https://www.vesselfinder.com/vessels?name={quote_plus(name)}"
    return ""


def add_popup_fields(properties):
    props = dict(properties or {})

    name = clean_text(
        props.get("name")
        or props.get("watch_name")
        or props.get("vessel_name")
        or props.get("ship_name")
    )
    imo = norm_digits(props.get("imo") or props.get("watch_imo"))
    mmsi = norm_digits(props.get("mmsi") or props.get("watch_mmsi"))
    vf_url = build_vesselfinder_url(name=name, imo=imo, mmsi=mmsi)

    props["name"] = name or "Unknown vessel"
    props["imo"] = imo or "—"
    props["mmsi"] = mmsi or "—"
    props["vesselfinder_url"] = vf_url

    return props


def as_feature(contact):
    lon = parse_float(get_any(contact, "longitude", "lon", "Longitude"))
    lat = parse_float(get_any(contact, "latitude", "lat", "Latitude"))
    if lon is None or lat is None:
        return None

    props = add_popup_fields(dict(contact))
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def empty_fc():
    return {"type": "FeatureCollection", "features": []}


def load_existing_features(path):
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("features", [])
    except Exception:
        return []


def write_geojson(path, features):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"type": "FeatureCollection", "features": features},
            f,
            ensure_ascii=False,
            indent=2,
        )


def load_contacts():
    candidates = [
        DATA_DIR / "ais_contacts_latest.json",
        DATA_DIR / "aisstream_contacts_latest.json",
        DATA_DIR / "contacts_latest.json",
        DATA_DIR / "vessels_latest.json",
    ]
    for path in candidates:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                if isinstance(data.get("contacts"), list):
                    return data["contacts"]
                if isinstance(data.get("vessels"), list):
                    return data["vessels"]
                if isinstance(data.get("data"), list):
                    return data["data"]
    return []


def classify_contact(contact, watch_index):
    merged = dict(contact)
    matched_row, matched_on = match_watchlist(contact, watch_index)
    categories = set()

    mmsi = norm_digits(get_any(contact, "mmsi", "MMSI", "UserID"))
    if mmsi.startswith("273"):
        categories.add("russian_mmsi")

    from_russia_confirmed, port_codes = confirm_russian_port(contact)
    merged["from_russia_confirmed"] = from_russia_confirmed
    merged["port_codes_seen"] = ";".join(port_codes)

    if matched_row:
        categories.add("watchlist")
        merged["watch_matched_on"] = matched_on
        merged["watch_name"] = clean_str(matched_row.get("name"))
        merged["watch_imo"] = norm_digits(matched_row.get("imo"))
        merged["watch_mmsi"] = norm_digits(matched_row.get("mmsi"))
        merged["watch_callsign"] = clean_str(matched_row.get("callsign"))
        merged["source_list"] = clean_str(matched_row.get("source_list"))
        merged["source_url"] = clean_str(matched_row.get("source_url"))
        merged["watch_priority"] = clean_str(matched_row.get("watch_priority"))
        merged["notes"] = clean_str(matched_row.get("notes"))

        track_sanctions = to_bool(matched_row.get("track_sanctions"))
        track_shadowfleet = to_bool(matched_row.get("track_shadowfleet"))
        track_falseflag = to_bool(matched_row.get("track_falseflag"))
        track_behavior = to_bool(matched_row.get("track_behavior"))
        track_russian_mmsi = to_bool(matched_row.get("track_russian_mmsi"))

        merged["sanctioned"] = track_sanctions
        merged["shadow_fleet"] = track_shadowfleet
        merged["false_flag"] = track_falseflag
        merged["behavioral_voi"] = track_behavior

        if track_sanctions or track_shadowfleet:
            categories.add("sanctions_shadowfleet")
        if track_falseflag:
            categories.add("falseflag_interest")
        if track_behavior:
            categories.add("behavioral_voi")
        if track_russian_mmsi and mmsi.startswith("273"):
            categories.add("russian_mmsi")
    else:
        merged["watch_matched_on"] = ""
        merged["watch_name"] = ""
        merged["watch_imo"] = ""
        merged["watch_mmsi"] = ""
        merged["watch_callsign"] = ""
        merged["source_list"] = ""
        merged["source_url"] = ""
        merged["watch_priority"] = ""
        merged["notes"] = ""
        merged["sanctioned"] = False
        merged["shadow_fleet"] = False
        merged["false_flag"] = False
        merged["behavioral_voi"] = False

    merged["categories"] = sorted(categories)
    return merged


def dedupe_key(contact, slot):
    mmsi = norm_digits(get_any(contact, "mmsi", "MMSI", "UserID"))
    imo = norm_digits(get_any(contact, "imo", "IMO", "ImoNumber"))
    cats = ",".join(sorted(contact.get("categories", [])))
    return f"{slot}|{mmsi}|{imo}|{cats}"


def update_history(snapshot_items, slot):
    existing_keys = set()
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    existing_keys.add(row.get("_history_key"))
                except Exception:
                    continue

    new_rows = []
    for item in snapshot_items:
        row = dict(item)
        hk = dedupe_key(row, slot)
        row["_history_key"] = hk
        if hk not in existing_keys:
            new_rows.append(row)
            existing_keys.add(hk)

    if new_rows:
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            for row in new_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def update_stats(snapshot_items, slot):
    stats = {
        "generated_at": now_iso(),
        "slot": slot,
        "total_items": len(snapshot_items),
        "by_category": dict(Counter(cat for item in snapshot_items for cat in item.get("categories", []))),
        "by_priority": dict(Counter(item.get("watch_priority", "") for item in snapshot_items if item.get("watch_priority"))),
        "from_russia_confirmed": sum(1 for item in snapshot_items if item.get("from_russia_confirmed")),
    }
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    _, watch_index = load_watchlist()
    contacts = load_contacts()

    classified = [classify_contact(c, watch_index) for c in contacts]
    snapshot_items = [c for c in classified if c.get("categories")]

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": now_iso(),
                "slot": current_slot(),
                "total_contacts_seen": len(contacts),
                "total_voi": len(snapshot_items),
                "items": snapshot_items,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    layer_buckets = defaultdict(list)
    for item in snapshot_items:
        feat = as_feature(item)
        if not feat:
            continue
        for cat in item.get("categories", []):
            if cat in LAYER_FILES:
                layer_buckets[cat].append(feat)

    for layer_name, path in LAYER_FILES.items():
        write_geojson(path, layer_buckets.get(layer_name, []))

    slot = current_slot()
    update_history(snapshot_items, slot)
    update_stats(snapshot_items, slot)


if __name__ == "__main__":
    main()
