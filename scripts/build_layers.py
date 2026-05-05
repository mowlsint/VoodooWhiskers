import csv
import json
from datetime import datetime, timezone

NOW_DT = datetime.now(timezone.utc)
NOW = NOW_DT.strftime("%Y-%m-%dT%H:%M:%SZ")

DATA_DIR = "data"
RUSSIAN_MID = "273"
PORTCALL_WINDOW_DAYS = 10


def feature(lon, lat, props):
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat]
        },
        "properties": props
    }


def save_geojson(path, features):
    fc = {
        "type": "FeatureCollection",
        "features": features
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2)


def load_csv(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def to_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_dt(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_flag_risk_reference(path):
    return load_csv(path)


def load_ru_ports(path):
    rows = load_csv(path)
    ports = {}
    for row in rows:
        code = (row.get("unlocode") or "").strip().upper()
        if code:
            ports[code] = row
    return ports


def build_false_flag_watch():
    features = []

    sample_vessels = [
        {
            "name": "EXAMPLE TANKER 1",
            "imo": "9000001",
            "mmsi": "273123456",
            "callsign": "UBCD1",
            "flag": "Sint Maarten",
            "claimed_flag": "Sint Maarten",
            "registry_state": "Sint Maarten",
            "registry_status": "fraud_notice",
            "ship_type": "Tanker",
            "owner": "",
            "manager": "",
            "risk_level": "B",
            "reason_code": "FRAUDULENT_REGISTRY_NOTICE",
            "reason_text": "Flag/genutztes Register gehört zu einer Jurisdiktion mit dokumentierten False-Flag- bzw. Fraud-Registry-Fällen.",
            "equasis_checked": False,
            "equasis_note": "",
            "gisis_checked": False,
            "historical_issue": True,
            "evidence_level": "reported",
            "source": "Manual watchlist",
            "source_url": "",
            "last_checked": NOW,
            "last_updated": NOW,
            "layer_type": "false_flag"
        }
    ]

    for i, vessel in enumerate(sample_vessels):
        lon = 10.0 + (i * 0.2)
        lat = 54.0 + (i * 0.2)
        features.append(feature(lon, lat, vessel))

    return features


def build_russian_mmsi():
    rows = load_csv(f"{DATA_DIR}/russian_mmsi_input.csv")
    features = []

    for row in rows:
        mmsi = (row.get("mmsi") or "").strip()
        if not mmsi.startswith(RUSSIAN_MID):
            continue

        try:
            lon = float(row["lon"])
            lat = float(row["lat"])
        except Exception:
            continue

        props = {
            "name": row.get("name", ""),
            "imo": row.get("imo", ""),
            "mmsi": mmsi,
            "callsign": row.get("callsign", ""),
            "flag": row.get("flag", ""),
            "ship_type": row.get("ship_type", ""),
            "owner": row.get("owner", ""),
            "manager": row.get("manager", ""),
            "source": row.get("source", "Manual input"),
            "source_url": row.get("source_url", ""),
            "last_seen": row.get("last_seen", ""),
            "last_updated": NOW,
            "layer_type": "russian_mmsi",
            "mmsi_prefix": mmsi[:3],
            "mid_state": "Russian Federation",
            "mid_confidence": "high",
            "identity_note": "MMSI begins with 273, the MID allocated to the Russian Federation."
        }

        features.append(feature(lon, lat, props))

    return features


def build_sanctions_shadowfleet():
    rows = load_csv(f"{DATA_DIR}/sanctions_shadowfleet_input.csv")
    features = []

    for row in rows:
        try:
            lon = float(row["lon"])
            lat = float(row["lat"])
        except Exception:
            continue

        props = {
            "name": row.get("name", ""),
            "imo": row.get("imo", ""),
            "mmsi": row.get("mmsi", ""),
            "callsign": row.get("callsign", ""),
            "flag": row.get("flag", ""),
            "ship_type": row.get("ship_type", ""),
            "owner": row.get("owner", ""),
            "manager": row.get("manager", ""),
            "sanctioned": to_bool(row.get("sanctioned", "")),
            "sanction_regime": row.get("sanction_regime", ""),
            "sanction_program": row.get("sanction_program", ""),
            "listing_date": row.get("listing_date", ""),
            "shadowfleet_flag": to_bool(row.get("shadowfleet_flag", "")),
            "shadow_reason": row.get("shadow_reason", ""),
            "risk_level": row.get("risk_level", ""),
            "notes": row.get("notes", ""),
            "source": row.get("source", "Manual input"),
            "source_url": row.get("source_url", ""),
            "last_seen": row.get("last_seen", ""),
            "last_updated": NOW,
            "layer_type": "sanctions_shadowfleet"
        }

        features.append(feature(lon, lat, props))

    return features


def build_recent_russian_portcall():
    ru_ports = load_ru_ports(f"{DATA_DIR}/ports_ru.csv")
    rows = load_csv(f"{DATA_DIR}/recent_russian_portcall_input.csv")
    features = []

    for row in rows:
        port_date = parse_dt(row.get("last_ru_port_date", ""))
        if port_date is None:
            continue

        days_since = (NOW_DT - port_date).days
        if days_since < 0 or days_since > PORTCALL_WINDOW_DAYS:
            continue

        unlocode = (row.get("last_ru_port_unlocode") or "").strip().upper()
        if unlocode not in ru_ports and not unlocode.startswith("RU "):
            continue

        try:
            lon = float(row["lon"])
            lat = float(row["lat"])
        except Exception:
            continue

        props = {
            "name": row.get("name", ""),
            "imo": row.get("imo", ""),
            "mmsi": row.get("mmsi", ""),
            "callsign": row.get("callsign", ""),
            "flag": row.get("flag", ""),
            "ship_type": row.get("ship_type", ""),
            "owner": row.get("owner", ""),
            "manager": row.get("manager", ""),
            "last_ru_port": row.get("last_ru_port", ""),
            "last_ru_port_unlocode": unlocode,
            "last_ru_port_date": row.get("last_ru_port_date", ""),
            "days_since_ru_port": days_since,
            "ru_port_source": row.get("source", "Manual input"),
            "source": row.get("source", "Manual input"),
            "source_url": row.get("source_url", ""),
            "last_updated": NOW,
            "layer_type": "ru_portcall_10d",
            "risk_level": "medium",
            "reason_text": f"Russian port call within the last {PORTCALL_WINDOW_DAYS} days."
        }

        features.append(feature(lon, lat, props))

    return features


def main():
    load_flag_risk_reference(f"{DATA_DIR}/flag_risk_reference.csv")

    false_flag_features = build_false_flag_watch()
    russian_mmsi_features = build_russian_mmsi()
    sanctions_features = build_sanctions_shadowfleet()
    recent_portcall_features = build_recent_russian_portcall()

    save_geojson(f"{DATA_DIR}/false_flag_watch.geojson", false_flag_features)
    save_geojson(f"{DATA_DIR}/russian_mmsi.geojson", russian_mmsi_features)
    save_geojson(f"{DATA_DIR}/sanctions_shadowfleet.geojson", sanctions_features)
    save_geojson(f"{DATA_DIR}/recent_russian_portcall_10d.geojson", recent_portcall_features)

    print("Layers written.")


if __name__ == "__main__":
    main()
