import csv
import json
import re
from html import escape as html_escape
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

DATA_DIR = Path("data")
WATCHLIST_PATH = DATA_DIR / "watchlist_master.csv"
PORTS_RU_PATH = DATA_DIR / "ports_ru.csv"
FLAG_RISK_PATH = DATA_DIR / "flag_risk_reference.csv"
RECENT_RU_INPUT_PATH = DATA_DIR / "recent_russian_portcall_input.csv"

RECENT_RUSSIAN_PORTCALL_DAYS = 10

# Compatibility fallbacks for older CSVs / legacy spellings.
RUSSIAN_PORT_ALLOWLIST = {"RUKGD", "RUBLT", "RUULU", "RUUST"}
POLISH_PORT_DENYLIST = {"PLGDN", "PLGDY"}
POLISH_PORT_NAMES = {"GDANSK", "GDYNIA"}

LAYER_FILES = {
    "russian_mmsi": DATA_DIR / "russian_mmsi.geojson",
    "watchlist": DATA_DIR / "watchlist_live.geojson",
    "sanctions_shadowfleet": DATA_DIR / "sanctions_shadowfleet.geojson",
    "falseflag_interest": DATA_DIR / "falseflag_interest.geojson",
    "false_flag_watch": DATA_DIR / "false_flag_watch.geojson",
    "behavioral_voi": DATA_DIR / "behavioral_voi.geojson",
    "recent_russian_portcall_10d": DATA_DIR / "recent_russian_portcall_10d.geojson",
}

SNAPSHOT_PATH = DATA_DIR / "voi_snapshot_latest.json"
HISTORY_PATH = DATA_DIR / "voi_history.jsonl"
STATS_PATH = DATA_DIR / "voi_stats_by_slot.json"

# Minimum fallback for AIS MMSI MID -> flag if flag field is absent.
# Prefer data/flag_risk_reference.csv via mmsi_mid_prefixes; this fallback keeps the script useful if the CSV is old.
FALLBACK_MID_TO_FLAG = {
    "306": "Curacao",
    "307": "Aruba",
    "312": "Belize",
    "314": "Barbados",
    "341": "Saint Kitts and Nevis",
    "351": "Panama", "352": "Panama", "353": "Panama", "354": "Panama", "355": "Panama", "356": "Panama", "357": "Panama",
    "370": "Panama", "371": "Panama", "372": "Panama", "373": "Panama",
    "511": "Palau",
    "518": "Cook Islands",
    "538": "Marshall Islands",
    "570": "Tonga",
    "607": "Gambia",
    "613": "Cameroon",
    "616": "Comoros",
    "621": "Djibouti",
    "626": "Gabon",
    "632": "Guinea",
    "636": "Liberia",
    "647": "Madagascar",
    "650": "Malawi",
    "660": "Mali",
    "667": "Sierra Leone",
    "668": "Sao Tome and Principe",
    "669": "Eswatini",
    "671": "Togo",
    "676": "Democratic Republic of the Congo",
    "677": "Tanzania",
    "679": "Zimbabwe",
    "750": "Guyana",
}

HARD_REGISTRY_STATUSES = {"no_intl_registry", "fraudulent_registry", "fraud_notice", "false_flag_confirmed"}


# Layer-specific map styling. Keep this in the generator so exported GeoJSON files do
# not look identical in uMap / Leaflet viewers. Coordinates stay untouched; only
# feature properties and popup text are enriched per output layer.
LAYER_STYLE = {
    "sanctions_shadowfleet": {
        "label": "Sanctions / shadow fleet",
        "short_label": "Shadow fleet",
        "marker_color": "#7a1f1f",
        "stroke_color": "#3b0b0b",
        "marker_symbol": "danger",
        "marker_size": "medium",
        "category_rank": 100,
    },
    "russian_mmsi": {
        "label": "Russian MMSI",
        "short_label": "Russian MMSI",
        "marker_color": "#d73027",
        "stroke_color": "#7f0000",
        "marker_symbol": "ship",
        "marker_size": "medium",
        "category_rank": 80,
    },
    "recent_russian_portcall_10d": {
        "label": "Recent Russian portcall / destination",
        "short_label": "Recent RU portcall",
        "marker_color": "#fdae61",
        "stroke_color": "#a65400",
        "marker_symbol": "harbor",
        "marker_size": "medium",
        "category_rank": 70,
    },
    "watchlist": {
        "label": "Watchlist live",
        "short_label": "Watchlist",
        "marker_color": "#2b6cb0",
        "stroke_color": "#123b70",
        "marker_symbol": "star",
        "marker_size": "medium",
        "category_rank": 60,
    },
    "false_flag_watch": {
        "label": "False-flag watch (hard)",
        "short_label": "False flag hard",
        "marker_color": "#6f42c1",
        "stroke_color": "#3b1d78",
        "marker_symbol": "flag",
        "marker_size": "medium",
        "category_rank": 90,
    },
    "falseflag_interest": {
        "label": "False-flag candidate / interest",
        "short_label": "False flag candidate",
        # High-contrast magenta: deliberately distinct from sanctions red,
        # recent-RU-portcall orange and watchlist blue.
        "marker_color": "#ff00c8",
        "stroke_color": "#7a005f",
        "marker_symbol": "flag",
        "marker_size": "medium",
        "category_rank": 65,
    },
    "behavioral_voi": {
        "label": "Behavioral VOI",
        "short_label": "Behavioral",
        "marker_color": "#f59f00",
        "stroke_color": "#8a5700",
        "marker_symbol": "warning",
        "marker_size": "medium",
        "category_rank": 75,
    },
}

DEFAULT_LAYER_STYLE = {
    "label": "VOI",
    "short_label": "VOI",
    "marker_color": "#586069",
    "stroke_color": "#2f363d",
    "marker_symbol": "circle",
    "marker_size": "medium",
    "category_rank": 10,
}


def layer_style(layer_name=""):
    return dict(DEFAULT_LAYER_STYLE, **LAYER_STYLE.get(clean_str(layer_name), {}))


def primary_category_for_item(item, preferred_layer=""):
    if preferred_layer:
        return preferred_layer
    categories = item.get("categories") or []
    ranked = sorted(categories, key=lambda c: layer_style(c).get("category_rank", 0), reverse=True)
    return ranked[0] if ranked else "watchlist"



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


def norm_key(v):
    return re.sub(r"[^A-Z0-9]", "", norm_text(v))


def norm_digits(v):
    return re.sub(r"\D", "", clean_str(v))


def norm_port_code(v):
    return norm_key(v)


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


def parse_dt(v):
    s = clean_str(v)
    if not s:
        return None
    s = s.replace(" +0000 UTC", "+00:00").replace(" UTC", "+00:00")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Trim Go / AISStream nanoseconds to Python microseconds.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


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

    # Deliberately no pure-name match here: AIS names such as ATLAS, SIRIUS, MARIA, CAPELLA
    # create too many false positives. Use IMO/MMSI/callsign or add the vessel explicitly.
    return None, None


def load_russian_ports(path=PORTS_RU_PATH):
    ports = {"codes": set(RUSSIAN_PORT_ALLOWLIST), "names": set()}
    for row in load_csv_rows(path):
        code = norm_port_code(row.get("unlocode"))
        name = norm_key(row.get("port_name"))
        if code:
            ports["codes"].add(code)
        if name:
            ports["names"].add(name)
        # Optional enhanced CSV support: aliases can contain AIS destination
        # variants such as RUULU;USTLUGA;UST-LUGA. Older 3-column CSVs still work.
        for alias in split_tokens(row.get("aliases")):
            alias_code = norm_port_code(alias)
            alias_name = norm_key(alias)
            if len(alias_code) == 5 and alias_code.startswith("RU"):
                ports["codes"].add(alias_code)
            if alias_name:
                ports["names"].add(alias_name)
    # Practical aliases seen in AIS destination strings.
    aliases = [
        "UST LUGA", "UST-LUGA", "USTLUGA", "ST PETERSBURG", "SAINT PETERSBURG",
        "KALININGRAD", "BALTIYSK", "BALTISK", "PRIMORSK", "VYSOTSK", "VYBORG",
        "MURMANSK", "ARKHANGELSK", "NOVOROSSIYSK", "NOVOROSSIISK", "TUAPSE",
        "TAMAN", "KAVKAZ", "ROSTOV", "ROSTOV ON DON", "AZOV", "TAGANROG",
        "MAKHACHKALA", "VLADIVOSTOK", "NAKHODKA", "KOZMINO", "VANINO",
        "DE KASTRI", "KORSAKOV", "SEVASTOPOL", "KERCH", "FEODOSIA",
    ]
    ports["names"].update(norm_key(a) for a in aliases)
    return ports


def extract_port_hits(contact, russian_ports):
    raw_values = [
        get_any(contact, "last_port_unlocode"),
        get_any(contact, "destination_unlocode"),
        get_any(contact, "port_unlocode"),
        get_any(contact, "port_code"),
        get_any(contact, "destination", "Destination"),
        get_any(contact, "last_port_name"),
        get_any(contact, "last_ru_port"),
        get_any(contact, "last_ru_port_unlocode"),
        get_any(contact, "next_port"),
    ]

    hits = set()
    deny_hits = set()

    for raw in raw_values:
        txt = clean_str(raw)
        if not txt:
            continue
        compact = norm_key(txt)
        tokens = re.findall(r"[A-Z]{2}\s?[A-Z0-9]{3}", norm_text(txt))
        for token in tokens:
            code = norm_port_code(token)
            if code in POLISH_PORT_DENYLIST:
                deny_hits.add(code)
            if code in russian_ports["codes"]:
                hits.add(code)
        for code in russian_ports["codes"]:
            if len(code) >= 5 and code in compact:
                hits.add(code)
        for name in POLISH_PORT_NAMES:
            if norm_key(name) in compact:
                deny_hits.add(name)
        for name in russian_ports["names"]:
            if len(name) >= 4 and name in compact:
                hits.add(name)

    if deny_hits and not hits:
        return False, sorted(deny_hits)
    return bool(hits), sorted(hits | deny_hits)


def confirm_russian_port(contact, russian_ports):
    return extract_port_hits(contact, russian_ports)


def split_tokens(v):
    return [t.strip() for t in re.split(r"[;,|]", clean_str(v)) if t.strip()]


def load_flag_risk(path=FLAG_RISK_PATH):
    rows = []
    by_name = {}
    by_iso = {}
    by_mid = {}
    if path.exists():
        for row in load_csv_rows(path):
            if not to_bool(row.get("active", "true")):
                continue
            state_name = clean_str(row.get("state_name"))
            iso = norm_text(row.get("iso_code"))
            row = dict(row)
            row["state_name"] = state_name
            row["iso_code"] = iso
            rows.append(row)
            if state_name:
                by_name[norm_key(state_name)] = row
            if iso:
                by_iso[iso] = row
            for mid in split_tokens(row.get("mmsi_mid_prefixes")):
                mid_digits = norm_digits(mid)
                if mid_digits:
                    by_mid[mid_digits[:3]] = row
    for mid, flag in FALLBACK_MID_TO_FLAG.items():
        by_mid.setdefault(mid, by_name.get(norm_key(flag), {"state_name": flag, "iso_code": "", "risk_category": "B", "registry_status": "fallback_mid", "issue_summary": "Fallback-MMSI-MID-Prüfansatz; Flagge aus MMSI-Präfix abgeleitet.", "source_primary": "Fallback", "source_url": "", "last_verified": "", "active": "true"}))
    return {"rows": rows, "by_name": by_name, "by_iso": by_iso, "by_mid": by_mid}


def detect_flag(contact, flag_ref):
    raw_flag = get_any(contact, "flag", "Flag", "flag_state", "FlagState", "registry_flag")
    if raw_flag:
        key = norm_key(raw_flag)
        if key in flag_ref["by_name"]:
            return clean_str(flag_ref["by_name"][key].get("state_name")), "explicit_flag"
        iso = norm_text(raw_flag)
        if iso in flag_ref["by_iso"]:
            return clean_str(flag_ref["by_iso"][iso].get("state_name")), "explicit_iso"
        return clean_str(raw_flag), "explicit_flag"

    mmsi = norm_digits(get_any(contact, "mmsi", "MMSI", "UserID"))
    if len(mmsi) >= 3:
        mid = mmsi[:3]
        if mid in flag_ref["by_mid"]:
            return clean_str(flag_ref["by_mid"][mid].get("state_name")), f"mmsi_mid:{mid}"
        if mid in FALLBACK_MID_TO_FLAG:
            return FALLBACK_MID_TO_FLAG[mid], f"mmsi_mid:{mid}"
    return "", ""


def assess_flag_risk(contact, flag_ref):
    flag, flag_source = detect_flag(contact, flag_ref)
    if not flag:
        return None
    row = flag_ref["by_name"].get(norm_key(flag))
    if not row:
        iso = norm_text(flag)
        row = flag_ref["by_iso"].get(iso)
    if not row:
        return None

    risk_category = clean_str(row.get("risk_category")).upper()
    registry_status = clean_str(row.get("registry_status"))
    is_hard = risk_category == "A" or registry_status in HARD_REGISTRY_STATUSES
    if is_hard:
        band = "hard"
    elif risk_category == "C" or registry_status in {"open_registry_psc_grey", "open_registry_watch", "psc_context"}:
        # Category C is context-only. It should enrich popups/statistics,
        # but must not create a false-flag candidate layer by itself.
        band = "context"
    else:
        band = "soft"
    score = {"A": 90, "B": 70, "C": 45}.get(risk_category, 50)
    return {
        "flag_detected": clean_str(row.get("state_name")) or flag,
        "flag_detected_source": flag_source,
        "flag_iso_code": clean_str(row.get("iso_code")),
        "flag_risk_category": risk_category,
        "flag_registry_status": registry_status,
        "flag_risk_band": band,
        "flag_risk_score": score,
        "flag_risk_reason": clean_str(row.get("issue_summary")),
        "flag_risk_source": clean_str(row.get("source_primary")),
        "flag_risk_url": clean_str(row.get("source_url")),
        "flag_risk_last_verified": clean_str(row.get("last_verified")),
    }


def build_vesselfinder_url(name="", imo="", mmsi=""):
    """
    Robust VesselFinder handoff link. The search endpoint is preferred because it
    works for IMO, MMSI and name without needing VesselFinder's internal slug.
    """
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


def display_value(v, fallback="—"):
    s = clean_text(v)
    return s if s else fallback


def html_row(label, value):
    value = display_value(value)
    return f"<tr><th>{html_escape(clean_text(label))}</th><td>{html_escape(value)}</td></tr>"


def link_html(label, url):
    url = clean_text(url)
    if not url:
        return ""
    safe_url = html_escape(url, quote=True)
    safe_label = html_escape(clean_text(label))
    return f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_label}</a>'


def build_popup_html(props, layer_name=""):
    style = layer_style(layer_name or props.get("primary_category"))
    name = display_value(props.get("name") or props.get("watch_name") or props.get("vessel_name") or props.get("ship_name"), "Unknown vessel")
    imo = display_value(props.get("imo") if props.get("imo") != "—" else props.get("watch_imo"))
    mmsi = display_value(props.get("mmsi") if props.get("mmsi") != "—" else props.get("watch_mmsi"))
    vf = clean_text(props.get("vesselfinder_url"))
    source_url = clean_text(props.get("source_url"))
    categories = props.get("categories") or []
    if isinstance(categories, str):
        categories = [categories]
    chips = " ".join(
        f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:2px 6px;border-radius:999px;background:#eef2f7;border:1px solid #cbd5e1;color:#1f2937;font-size:11px;">{html_escape(clean_text(c))}</span>'
        for c in categories
    )
    links = [link_html("VesselFinder", vf), link_html("Source", source_url)]
    links = " · ".join([x for x in links if x])
    rows = [
        html_row("Layer", style.get("label")),
        html_row("IMO", imo),
        html_row("MMSI", mmsi),
        html_row("Callsign", props.get("callsign") or props.get("watch_callsign")),
        html_row("Destination", props.get("destination")),
        html_row("SOG / COG", f"{display_value(props.get('sog'))} kn / {display_value(props.get('cog'))}°"),
        html_row("Last seen", props.get("last_seen_utc")),
        html_row("Watchlist", props.get("source_list")),
        html_row("Notes", props.get("notes")),
    ]
    return f"""
<div class="mp-voi-popup" style="font-family:Inter,Arial,sans-serif;font-size:12px;line-height:1.35;min-width:230px;max-width:340px;">
  <div style="font-weight:700;font-size:15px;margin-bottom:3px;color:#111827;">{html_escape(name)}</div>
  <div style="display:inline-block;margin:0 0 7px 0;padding:2px 7px;border-radius:999px;background:{html_escape(style.get('marker_color'))};color:#fff;font-size:11px;">{html_escape(style.get('short_label') or style.get('label'))}</div>
  <table style="border-collapse:collapse;width:100%;margin:2px 0 6px 0;">
    {''.join(rows)}
  </table>
  <div style="margin:5px 0;">{chips}</div>
  <div style="margin-top:6px;">{links}</div>
</div>
""".strip()


def add_popup_fields(properties, layer_name=""):
    props = dict(properties or {})
    name = clean_text(props.get("name") or props.get("watch_name") or props.get("vessel_name") or props.get("ship_name"))
    imo = norm_digits(props.get("imo") or props.get("watch_imo"))
    mmsi = norm_digits(props.get("mmsi") or props.get("watch_mmsi"))
    primary = primary_category_for_item(props, layer_name)
    style = layer_style(primary)
    vf_url = build_vesselfinder_url(name=name, imo=imo, mmsi=mmsi)

    props["name"] = name or "Unknown vessel"
    props["imo"] = imo or "—"
    props["mmsi"] = mmsi or "—"
    props["primary_category"] = primary
    props["layer_name"] = primary
    props["layer_label"] = style.get("label")
    props["display_category"] = style.get("short_label") or style.get("label")
    props["vesselfinder_url"] = vf_url
    props["vesselfinder_label"] = "VesselFinder"

    # SimpleStyle + pragmatic uMap/Leaflet hints. Viewers may ignore some keys,
    # but keeping several common names makes the exported layers portable.
    props["marker-color"] = style.get("marker_color")
    props["marker-symbol"] = style.get("marker_symbol")
    props["marker-size"] = style.get("marker_size")
    props["stroke"] = style.get("stroke_color")
    props["stroke-width"] = 2
    props["stroke-opacity"] = 0.95
    props["fill"] = style.get("marker_color")
    props["fillColor"] = style.get("marker_color")
    props["fill-opacity"] = 0.82
    props["color"] = style.get("marker_color")
    props["_umap_options"] = {
        "color": style.get("marker_color"),
        "fillColor": style.get("marker_color"),
        "weight": 2,
        "opacity": 0.95,
        "fillOpacity": 0.82,
    }

    popup = build_popup_html(props, primary)
    props["popup_html"] = popup
    props["popupContent"] = popup
    props["description"] = popup
    return props


def as_feature(contact, layer_name=""):
    lon = parse_float(get_any(contact, "longitude", "lon", "Longitude"))
    lat = parse_float(get_any(contact, "latitude", "lat", "Latitude"))
    if lon is None or lat is None:
        return None
    props = add_popup_fields(dict(contact), layer_name=layer_name)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def write_geojson(path, features):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, indent=2)


def load_contacts():
    candidates = [DATA_DIR / "ais_contacts_latest.json", DATA_DIR / "aisstream_contacts_latest.json", DATA_DIR / "contacts_latest.json", DATA_DIR / "vessels_latest.json"]
    for path in candidates:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("contacts", "vessels", "data"):
                    if isinstance(data.get(key), list):
                        return data[key]
    return []


def classify_contact(contact, watch_index, russian_ports, flag_ref):
    merged = dict(contact)
    matched_row, matched_on = match_watchlist(contact, watch_index)
    categories = set()

    mmsi = norm_digits(get_any(contact, "mmsi", "MMSI", "UserID"))
    if mmsi.startswith("273"):
        categories.add("russian_mmsi")

    from_russia_confirmed, port_hits = confirm_russian_port(contact, russian_ports)
    merged["from_russia_confirmed"] = from_russia_confirmed
    merged["port_codes_seen"] = ";".join(port_hits)
    if from_russia_confirmed:
        categories.add("recent_russian_portcall_10d")
        merged["recent_ru_portcall_basis"] = "current_ais_destination_or_port_field"

    flag_risk = assess_flag_risk(contact, flag_ref)
    if flag_risk:
        merged.update(flag_risk)
        is_context_only = flag_risk.get("flag_risk_band") == "context"
        merged["flag_watch_context"] = is_context_only
        merged["false_flag_candidate"] = not is_context_only
        if not is_context_only:
            categories.add("falseflag_interest")
        if flag_risk.get("flag_risk_band") == "hard":
            categories.add("false_flag_watch")
    else:
        merged.setdefault("false_flag_candidate", False)
        merged.setdefault("flag_watch_context", False)
        for key in ["flag_detected", "flag_detected_source", "flag_iso_code", "flag_risk_category", "flag_registry_status", "flag_risk_band", "flag_risk_score", "flag_risk_reason", "flag_risk_source", "flag_risk_url", "flag_risk_last_verified"]:
            merged.setdefault(key, "")

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
        merged["false_flag"] = track_falseflag or bool(flag_risk and flag_risk.get("flag_risk_band") != "context")
        merged["behavioral_voi"] = track_behavior

        if track_sanctions or track_shadowfleet:
            categories.add("sanctions_shadowfleet")
        if track_falseflag:
            categories.add("falseflag_interest")
            # Manual watchlist flagging is intentionally broad; hard/soft remains in flag_risk_band if known.
        if track_behavior:
            categories.add("behavioral_voi")
        if track_russian_mmsi and mmsi.startswith("273"):
            categories.add("russian_mmsi")
    else:
        merged.setdefault("watch_matched_on", "")
        merged.setdefault("watch_name", "")
        merged.setdefault("watch_imo", "")
        merged.setdefault("watch_mmsi", "")
        merged.setdefault("watch_callsign", "")
        merged.setdefault("source_list", "")
        merged.setdefault("source_url", "")
        merged.setdefault("watch_priority", "")
        merged.setdefault("notes", "")
        merged.setdefault("sanctioned", False)
        merged.setdefault("shadow_fleet", False)
        merged["false_flag"] = bool(flag_risk and flag_risk.get("flag_risk_band") != "context")
        merged.setdefault("behavioral_voi", False)

    merged["categories"] = sorted(categories)
    return merged


def dedupe_identity(contact):
    return (
        norm_digits(get_any(contact, "imo", "IMO", "ImoNumber"))
        or norm_digits(get_any(contact, "mmsi", "MMSI", "UserID"))
        or norm_text(get_any(contact, "callsign", "CallSign"))
        or norm_text(get_any(contact, "name", "Name"))
    )


def dedupe_key(contact, slot):
    mmsi = norm_digits(get_any(contact, "mmsi", "MMSI", "UserID"))
    imo = norm_digits(get_any(contact, "imo", "IMO", "ImoNumber"))
    cats = ",".join(sorted(contact.get("categories", [])))
    return f"{slot}|{mmsi}|{imo}|{cats}"


def read_history_rows():
    rows = []
    if not HISTORY_PATH.exists():
        return rows
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def update_history(snapshot_items, slot):
    existing_keys = {row.get("_history_key") for row in read_history_rows() if row.get("_history_key")}
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


def build_recent_ru_input_items(russian_ports, flag_ref):
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_RUSSIAN_PORTCALL_DAYS)
    items = []
    for row in load_csv_rows(RECENT_RU_INPUT_PATH):
        dt = parse_dt(row.get("last_ru_port_date"))
        if not dt or dt < cutoff:
            continue
        item = dict(row)
        item["latitude"] = row.get("lat") or row.get("latitude")
        item["longitude"] = row.get("lon") or row.get("longitude")
        item["from_russia_confirmed"] = True
        item["port_codes_seen"] = norm_port_code(row.get("last_ru_port_unlocode")) or norm_key(row.get("last_ru_port"))
        item["recent_ru_portcall_basis"] = "manual_recent_russian_portcall_input"
        item["categories"] = ["recent_russian_portcall_10d"]
        flag_risk = assess_flag_risk(item, flag_ref)
        if flag_risk:
            item.update(flag_risk)
            is_context_only = flag_risk.get("flag_risk_band") == "context"
            item["flag_watch_context"] = is_context_only
            item["false_flag_candidate"] = not is_context_only
            item["false_flag"] = not is_context_only
            if not is_context_only:
                item["categories"].append("falseflag_interest")
            if flag_risk.get("flag_risk_band") == "hard":
                item["categories"].append("false_flag_watch")
        item["categories"] = sorted(set(item["categories"]))
        items.append(item)
    return items


def build_recent_ru_history_items(current_items):
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_RUSSIAN_PORTCALL_DAYS)
    items = []
    for row in read_history_rows():
        if not row.get("from_russia_confirmed"):
            continue
        dt = None
        for key in ("last_seen_utc", "last_ru_port_date", "generated_at", "slot"):
            dt = parse_dt(row.get(key))
            if dt:
                break
        if not dt or dt < cutoff:
            continue
        item = dict(row)
        cats = set(item.get("categories") or [])
        cats.add("recent_russian_portcall_10d")
        item["categories"] = sorted(cats)
        item.setdefault("recent_ru_portcall_basis", "voi_history_from_russia_confirmed")
        items.append(item)

    for row in current_items:
        if row.get("from_russia_confirmed"):
            item = dict(row)
            cats = set(item.get("categories") or [])
            cats.add("recent_russian_portcall_10d")
            item["categories"] = sorted(cats)
            item.setdefault("recent_ru_portcall_basis", "current_ais_destination_or_port_field")
            items.append(item)
    return items


def merge_unique_items(*groups):
    out = []
    seen = set()
    for group in groups:
        for item in group:
            key = dedupe_identity(item)
            if not key:
                # Geometry-only manual entries still need to survive.
                key = f"{get_any(item, 'name')}|{get_any(item, 'latitude', 'lat')}|{get_any(item, 'longitude', 'lon')}"
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def update_stats(snapshot_items, slot):
    stats = {
        "generated_at": now_iso(),
        "slot": slot,
        "total_items": len(snapshot_items),
        "by_category": dict(Counter(cat for item in snapshot_items for cat in item.get("categories", []))),
        "by_priority": dict(Counter(item.get("watch_priority", "") for item in snapshot_items if item.get("watch_priority"))),
        "from_russia_confirmed": sum(1 for item in snapshot_items if item.get("from_russia_confirmed")),
        "falseflag_interest": sum(1 for item in snapshot_items if "falseflag_interest" in item.get("categories", [])),
        "false_flag_watch_hard": sum(1 for item in snapshot_items if "false_flag_watch" in item.get("categories", [])),
        "recent_russian_portcall_10d": sum(1 for item in snapshot_items if "recent_russian_portcall_10d" in item.get("categories", [])),
    }
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _, watch_index = load_watchlist()
    russian_ports = load_russian_ports()
    flag_ref = load_flag_risk()
    contacts = load_contacts()

    classified = [classify_contact(c, watch_index, russian_ports, flag_ref) for c in contacts]
    snapshot_items = [c for c in classified if c.get("categories")]

    recent_ru_items = merge_unique_items(
        build_recent_ru_history_items(snapshot_items),
        build_recent_ru_input_items(russian_ports, flag_ref),
    )

    # Include recent Russian-portcall items in snapshot if they were not already live VOIs.
    snapshot_items = merge_unique_items(snapshot_items, recent_ru_items)

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": now_iso(), "slot": current_slot(), "total_contacts_seen": len(contacts), "total_voi": len(snapshot_items), "items": snapshot_items}, f, ensure_ascii=False, indent=2)

    layer_buckets = defaultdict(list)
    for item in snapshot_items:
        for cat in item.get("categories", []):
            if cat in LAYER_FILES:
                feat = as_feature(item, layer_name=cat)
                if feat:
                    layer_buckets[cat].append(feat)

    for layer_name, path in LAYER_FILES.items():
        write_geojson(path, layer_buckets.get(layer_name, []))

    slot = current_slot()
    update_history(snapshot_items, slot)
    update_stats(snapshot_items, slot)


if __name__ == "__main__":
    main()
