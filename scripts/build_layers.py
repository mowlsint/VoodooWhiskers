import csv
import json
from datetime import datetime, timezone

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

DATA_DIR = "data"


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


def load_flag_risk_reference(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


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


def build_empty_layer(layer_type):
    return []


def main():
    load_flag_risk_reference(f"{DATA_DIR}/flag_risk_reference.csv")

    false_flag_features = build_false_flag_watch()
    save_geojson(f"{DATA_DIR}/false_flag_watch.geojson", false_flag_features)

    save_geojson(f"{DATA_DIR}/sanctions_shadowfleet.geojson", build_empty_layer("sanctions"))
    save_geojson(f"{DATA_DIR}/russian_mmsi.geojson", build_empty_layer("russian_mmsi"))
    save_geojson(f"{DATA_DIR}/recent_russian_portcall_10d.geojson", build_empty_layer("ru_portcall_10d"))

    print("Layers written.")


if __name__ == "__main__":
    main()
