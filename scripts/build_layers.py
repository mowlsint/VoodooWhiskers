import csv
import re

WATCHLIST_PATH = "data/watchlist_master.csv"

RUSSIAN_PORT_ALLOWLIST = {"RUKGD", "RUBLT", "RUULU"}
POLISH_PORT_DENYLIST = {"PLGDN", "PLGDY"}

def to_bool(v):
    return str(v).strip().lower() in {"1", "true", "yes", "y"}

def clean_str(v):
    return "" if v is None else str(v).strip()

def norm_text(v):
    s = clean_str(v).upper()
    s = re.sub(r"\s+", " ", s)
    return s

def norm_digits(v):
    return re.sub(r"\D", "", clean_str(v))

def get_any(d, *keys):
    for k in keys:
        if k in d and clean_str(d.get(k)):
            return d.get(k)
    return ""

def load_watchlist(path=WATCHLIST_PATH):
    with open(path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

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

def match_watchlist(contact, index):
    imo = norm_digits(get_any(contact, "imo", "IMO", "ImoNumber"))
    mmsi = norm_digits(get_any(contact, "mmsi", "MMSI", "UserID"))
    callsign = norm_text(get_any(contact, "callsign", "CallSign"))
    name = norm_text(get_any(contact, "name", "ship_name", "ShipName"))

    if imo and imo in index["imo"]:
        return index["imo"][imo], "imo"
    if mmsi and mmsi in index["mmsi"]:
        return index["mmsi"][mmsi], "mmsi"
    if callsign and callsign in index["callsign"]:
        return index["callsign"][callsign], "callsign"
    if name and name in index["name"]:
        return index["name"][name], "name"
    return None, None

def extract_port_codes(contact):
    raw_values = [
        get_any(contact, "last_port_unlocode"),
        get_any(contact, "destination_unlocode"),
        get_any(contact, "port_unlocode"),
        get_any(contact, "port_code"),
        get_any(contact, "destination", "Destination"),
        get_any(contact, "last_port_name")
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

        merged["sanctioned"] = track_sanctions
        merged["shadow_fleet"] = track_shadowfleet
        merged["false_flag"] = track_falseflag

        if track_sanctions or track_shadowfleet:
            categories.add("sanctions_shadowfleet")
        if track_falseflag:
            categories.add("falseflag_interest")
    else:
        merged["sanctioned"] = False
        merged["shadow_fleet"] = False
        merged["false_flag"] = False
        merged["watch_matched_on"] = ""
        merged["watch_name"] = ""
        merged["watch_imo"] = ""
        merged["watch_mmsi"] = ""
        merged["watch_callsign"] = ""
        merged["source_list"] = ""
        merged["source_url"] = ""
        merged["watch_priority"] = ""
        merged["notes"] = ""

    merged["categories"] = sorted(categories)
    return merged
