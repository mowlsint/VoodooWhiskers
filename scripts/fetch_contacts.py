#!/usr/bin/env python3
"""Fetch AISstream contacts with a time-bounded certificate-pinned emergency mode.

Normal behavior remains strict TLS verification. If, and only if, strict TLS fails
because the server certificate is expired, the client may reconnect with CA/date
verification disabled and then authenticate the peer by an exact SHA-256 leaf
certificate pin before sending the AISstream API key.

The emergency mode has three independent kill switches:
- an exact certificate fingerprint must match AISSTREAM_PINNED_CERT_SHA256;
- the certificate must still identify stream.aisstream.io;
- AISSTREAM_EMERGENCY_PIN_UNTIL must not have passed.

No output file is replaced unless at least one valid AIS message and at least one
kept contact were collected.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import socket
import ssl
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import websockets
from cryptography import x509
from cryptography.x509.oid import NameOID

DATA_DIR = Path("data")
WATCHLIST_PATH = DATA_DIR / "watchlist_master.csv"
FLAG_RISK_PATH = DATA_DIR / "flag_risk_reference.csv"
PORTS_RU_PATH = DATA_DIR / "ports_ru.csv"
OUTPUT_PATH = DATA_DIR / "ais_contacts_aisstream_latest.json"

# Context-only tanker retention zones. These mirror build_layers.py and allow
# neutral tankers to be visible as a grey, non-VOI context layer.
TANKER_CONTEXT_ZONES = [
    {"id": "kaliningrad_baltiysk_approaches", "min_lat": 54.15, "max_lat": 56.20, "min_lon": 18.20, "max_lon": 22.90},
    {"id": "gulf_of_gdansk", "min_lat": 53.95, "max_lat": 55.85, "min_lon": 17.35, "max_lon": 20.80},
    {"id": "gulf_of_finland_ru_approaches", "min_lat": 58.40, "max_lat": 60.85, "min_lon": 23.40, "max_lon": 30.70},
    {"id": "danish_straits_kattegat", "min_lat": 54.30, "max_lat": 58.50, "min_lon": 8.00, "max_lon": 13.10},
    {"id": "skagen_waiting_area", "min_lat": 56.80, "max_lat": 58.50, "min_lon": 8.10, "max_lon": 12.30},
    {"id": "german_bight", "min_lat": 53.00, "max_lat": 56.25, "min_lon": 4.70, "max_lon": 9.40},
    {"id": "dover_channel_gateway", "min_lat": 50.70, "max_lat": 51.75, "min_lon": -0.60, "max_lon": 2.30},
    {"id": "gibraltar_west_med_gateway", "min_lat": 35.10, "max_lat": 37.30, "min_lon": -6.20, "max_lon": -2.50},
]

AISSTREAM_URL = os.getenv("AISSTREAM_URL", "wss://stream.aisstream.io/v0/stream").strip()

MESSAGE_TYPES = [
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
    "StaticDataReport",
]

BOUNDING_BOXES = [
    [[53.0, 3.0], [60.8, 30.5]],    # North Sea + Baltic + Danish Straits
    [[49.5, -6.5], [53.5, 3.5]],    # English Channel / Dover / Western approaches gateway
    [[35.0, -6.5], [38.8, 16.5]],   # Gibraltar / western Mediterranean shadow-fleet gateway
    [[41.0, 26.0], [47.5, 42.5]],   # Black Sea
]

FALLBACK_FLAG_RISK_MIDS = {
    "306", "307", "312", "314", "341", "351", "352", "353", "354", "355", "356", "357",
    "370", "371", "372", "373", "511", "518", "538", "570", "607", "613", "616", "621",
    "626", "632", "636", "647", "650", "660", "667", "668", "669", "671", "676", "677", "679", "750",
}

RUSSIAN_PORT_ALLOWLIST = {"RUKGD", "RUBLT", "RUULU", "RUUST"}
RUSSIAN_PORT_NAME_ALIASES = {
    "USTLUGA", "STPETERSBURG", "SAINTPETERSBURG", "KALININGRAD", "BALTIYSK", "BALTISK",
    "PRIMORSK", "VYSOTSK", "VYBORG", "MURMANSK", "ARKHANGELSK", "NOVOROSSIYSK",
    "NOVOROSSIISK", "TUAPSE", "TAMAN", "KAVKAZ", "ROSTOV", "ROSTOVONDON", "AZOV",
    "TAGANROG", "MAKHACHKALA", "VLADIVOSTOK", "NAKHODKA", "KOZMINO", "VANINO",
    "DEKASTRI", "KORSAKOV", "SEVASTOPOL", "KERCH", "FEODOSIA",
}


def clean_str(v: Any) -> str:
    return "" if v is None else str(v).strip()


def norm_text(v: Any) -> str:
    s = clean_str(v).upper()
    return re.sub(r"\s+", " ", s)


def norm_key(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", norm_text(v))


def digits(v: Any) -> str:
    return re.sub(r"\D", "", clean_str(v))


def merge_contact(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if v in ("", None, [], {}):
            continue
        dst[k] = v


def contact_key(d: dict[str, Any]) -> str:
    return digits(d.get("mmsi")) or digits(d.get("imo")) or norm_text(d.get("callsign")) or norm_text(d.get("name"))


def is_russian_mmsi_prefix(d: dict[str, Any]) -> bool:
    return digits(d.get("mmsi")).startswith("273")


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_watchlist_index() -> dict[str, set[str]]:
    idx = {"mmsi": set(), "imo": set(), "callsign": set(), "name": set()}
    for row in load_csv_rows(WATCHLIST_PATH):
        mmsi = digits(row.get("mmsi"))
        imo = digits(row.get("imo"))
        callsign = norm_text(row.get("callsign"))
        name = norm_text(row.get("name"))
        if mmsi:
            idx["mmsi"].add(mmsi)
        if imo:
            idx["imo"].add(imo)
        if callsign:
            idx["callsign"].add(callsign)
        if name:
            idx["name"].add(name)
    return idx


def load_flag_risk_mids() -> set[str]:
    mids = set(FALLBACK_FLAG_RISK_MIDS)
    for row in load_csv_rows(FLAG_RISK_PATH):
        if str(row.get("active", "true")).strip().lower() not in {"1", "true", "yes", "y"}:
            continue
        for token in re.split(r"[;,|]", clean_str(row.get("mmsi_mid_prefixes"))):
            mid = digits(token)[:3]
            if mid:
                mids.add(mid)
    return mids


def load_russian_port_terms() -> tuple[set[str], set[str]]:
    codes = set(RUSSIAN_PORT_ALLOWLIST)
    names = set(RUSSIAN_PORT_NAME_ALIASES)
    for row in load_csv_rows(PORTS_RU_PATH):
        code = norm_key(row.get("unlocode"))
        name = norm_key(row.get("port_name"))
        if code:
            codes.add(code)
        if name:
            names.add(name)
    return codes, names


def is_flag_risk_mid(d: dict[str, Any], risk_mids: set[str]) -> bool:
    mmsi = digits(d.get("mmsi"))
    return len(mmsi) >= 3 and mmsi[:3] in risk_mids


def has_russian_destination_or_port(d: dict[str, Any], ru_codes: set[str], ru_names: set[str]) -> bool:
    raw = " ".join(
        clean_str(d.get(k))
        for k in ["destination", "Destination", "last_port_name", "last_port_unlocode", "port_unlocode", "next_port"]
        if clean_str(d.get(k))
    )
    if not raw:
        return False
    compact = norm_key(raw)
    if any(code in compact for code in ru_codes):
        return True
    return any(len(name) >= 4 and name in compact for name in ru_names)


def parse_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_tanker_contact(d: dict[str, Any]) -> bool:
    raw_type = clean_str(d.get("ship_type") or d.get("ShipType") or d.get("type") or d.get("Type"))
    try:
        ship_type = int(float(raw_type))
        if 80 <= ship_type <= 89:
            return True
    except (TypeError, ValueError):
        pass
    txt = norm_text(
        " ".join(
            clean_str(d.get(k))
            for k in ["ship_type_text", "vessel_type", "destination", "Destination"]
            if clean_str(d.get(k))
        )
    )
    return bool(re.search(r"\b(TANKER|OIL\s*TANKER|PRODUCT\s*TANKER|CHEMICAL\s*TANKER|CRUDE\s*TANKER|LNG\s*CARRIER|LPG\s*CARRIER|VLCC|SUEZMAX|AFRAMAX)\b", txt))


def tanker_context_zone_ids(d: dict[str, Any]) -> list[str]:
    if not is_tanker_contact(d):
        return []
    lat = parse_float(d.get("latitude") or d.get("Latitude"))
    lon = parse_float(d.get("longitude") or d.get("Longitude"))
    if lat is None or lon is None:
        return []
    return [
        z["id"]
        for z in TANKER_CONTEXT_ZONES
        if z["min_lat"] <= lat <= z["max_lat"] and z["min_lon"] <= lon <= z["max_lon"]
    ]


def is_neutral_tanker_context_candidate(d: dict[str, Any]) -> bool:
    return bool(tanker_context_zone_ids(d))


def is_watchlist_match(d: dict[str, Any], idx: dict[str, set[str]]) -> bool:
    mmsi = digits(d.get("mmsi"))
    imo = digits(d.get("imo"))
    callsign = norm_text(d.get("callsign"))
    # Deliberately no pure-name match: common AIS names create false positives.
    return bool(
        (mmsi and mmsi in idx["mmsi"])
        or (imo and imo in idx["imo"])
        or (callsign and callsign in idx["callsign"])
    )


def keep_contact(
    d: dict[str, Any],
    idx: dict[str, set[str]],
    risk_mids: set[str],
    ru_codes: set[str],
    ru_names: set[str],
) -> bool:
    return (
        is_russian_mmsi_prefix(d)
        or is_watchlist_match(d, idx)
        or is_flag_risk_mid(d, risk_mids)
        or has_russian_destination_or_port(d, ru_codes, ru_names)
        or is_neutral_tanker_context_candidate(d)
    )


def extract_contact(msg: dict[str, Any]) -> dict[str, Any]:
    md = msg.get("MetaData") or msg.get("Metadata") or {}
    mt = msg.get("MessageType", "")
    body = (msg.get("Message") or {}).get(mt, {}) if mt else {}
    out: dict[str, Any] = {
        "mmsi": "",
        "imo": "",
        "callsign": "",
        "name": "",
        "latitude": "",
        "longitude": "",
        "destination": "",
        "ship_type": "",
        "navigational_status": "",
        "sog": "",
        "cog": "",
        "true_heading": "",
        "source": "AISStream",
        "message_type_last": mt,
        "last_seen_utc": md.get("time_utc") or datetime.now(timezone.utc).isoformat(),
    }
    if md:
        if md.get("MMSI") is not None:
            out["mmsi"] = str(md.get("MMSI"))
        if md.get("ShipName"):
            out["name"] = clean_str(md.get("ShipName"))
        if md.get("latitude") is not None:
            out["latitude"] = md.get("latitude")
        if md.get("longitude") is not None:
            out["longitude"] = md.get("longitude")
        if md.get("Latitude") is not None and out["latitude"] == "":
            out["latitude"] = md.get("Latitude")
        if md.get("Longitude") is not None and out["longitude"] == "":
            out["longitude"] = md.get("Longitude")
    if mt in {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}:
        if body.get("UserID") is not None:
            out["mmsi"] = str(body.get("UserID"))
        if body.get("Latitude") is not None:
            out["latitude"] = body.get("Latitude")
        if body.get("Longitude") is not None:
            out["longitude"] = body.get("Longitude")
        out["navigational_status"] = body.get("NavigationalStatus", "")
        out["sog"] = body.get("Sog", "")
        out["cog"] = body.get("Cog", "")
        out["true_heading"] = body.get("TrueHeading", "")
        if body.get("Name") and not out["name"]:
            out["name"] = clean_str(body.get("Name"))
    elif mt == "ShipStaticData":
        if body.get("UserID") is not None:
            out["mmsi"] = str(body.get("UserID"))
        if body.get("ImoNumber") is not None:
            out["imo"] = str(body.get("ImoNumber"))
        if body.get("CallSign"):
            out["callsign"] = clean_str(body.get("CallSign"))
        if body.get("Name"):
            out["name"] = clean_str(body.get("Name"))
        if body.get("Destination"):
            out["destination"] = clean_str(body.get("Destination"))
        if body.get("Type") is not None:
            out["ship_type"] = body.get("Type")
    elif mt == "StaticDataReport":
        if body.get("UserID") is not None:
            out["mmsi"] = str(body.get("UserID"))
        report_a = body.get("ReportA") or {}
        report_b = body.get("ReportB") or {}
        if report_a.get("Name"):
            out["name"] = clean_str(report_a.get("Name"))
        if report_b.get("CallSign"):
            out["callsign"] = clean_str(report_b.get("CallSign"))
        if report_b.get("ShipType") is not None:
            out["ship_type"] = report_b.get("ShipType")
    return out


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_fingerprint(value: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", value).lower()


def configured_pins() -> set[str]:
    raw = clean_str(os.getenv("AISSTREAM_PINNED_CERT_SHA256"))
    pins = {
        normalize_fingerprint(token)
        for token in re.split(r"[,;\s]+", raw)
        if normalize_fingerprint(token)
    }
    invalid = [pin for pin in pins if len(pin) != 64]
    if invalid:
        raise RuntimeError("AISSTREAM_PINNED_CERT_SHA256 contains a value that isn't a 64-character SHA-256 fingerprint")
    return pins


def cert_datetime(cert: x509.Certificate, attr: str) -> datetime:
    utc_attr = f"{attr}_utc"
    value = getattr(cert, utc_attr, None)
    if value is not None:
        return value.astimezone(timezone.utc)
    legacy = getattr(cert, attr)
    if legacy.tzinfo is None:
        legacy = legacy.replace(tzinfo=timezone.utc)
    return legacy.astimezone(timezone.utc)


def certificate_metadata(der: bytes) -> dict[str, Any]:
    cert = x509.load_der_x509_certificate(der)
    fingerprint = hashlib.sha256(der).hexdigest().lower()
    try:
        san_names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        san_names = []
    common_names = [attr.value for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    return {
        "fingerprint_sha256": fingerprint,
        "fingerprint_sha256_display": ":".join(fingerprint[i:i + 2] for i in range(0, 64, 2)).upper(),
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "serial_number_hex": format(cert.serial_number, "X"),
        "not_before": cert_datetime(cert, "not_valid_before").replace(microsecond=0).isoformat(),
        "not_after": cert_datetime(cert, "not_valid_after").replace(microsecond=0).isoformat(),
        "dns_names": san_names,
        "common_names": common_names,
    }


def dns_pattern_matches(pattern: str, hostname: str) -> bool:
    pattern = pattern.rstrip(".").lower()
    hostname = hostname.rstrip(".").lower()
    if pattern == hostname:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return hostname.endswith(suffix) and hostname.count(".") == pattern.count(".")
    return False


def validate_pinned_certificate(der: bytes, hostname: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    pins = configured_pins()
    if not pins:
        raise RuntimeError(
            "Pinned emergency mode was required, but AISSTREAM_PINNED_CERT_SHA256 is missing. "
            "Run this workflow with certificate_probe_only=true, verify the reported fingerprint, "
            "store it as a repository secret, and rerun."
        )

    pin_until_raw = clean_str(os.getenv("AISSTREAM_EMERGENCY_PIN_UNTIL"))
    if not pin_until_raw:
        raise RuntimeError("AISSTREAM_EMERGENCY_PIN_UNTIL is required for pinned emergency mode")
    pin_until = parse_datetime(pin_until_raw)
    if now > pin_until:
        raise RuntimeError(f"Pinned emergency mode expired at {pin_until.isoformat()}")

    metadata = certificate_metadata(der)
    actual = metadata["fingerprint_sha256"]
    if actual not in pins:
        raise RuntimeError(
            "AISstream certificate pin mismatch. "
            f"Presented SHA-256: {metadata['fingerprint_sha256_display']}"
        )

    names = list(metadata["dns_names"]) or list(metadata["common_names"])
    if not any(dns_pattern_matches(name, hostname) for name in names):
        raise RuntimeError(f"Pinned certificate doesn't identify {hostname}; names={names}")

    not_before = parse_datetime(metadata["not_before"])
    not_after = parse_datetime(metadata["not_after"])
    if not_before > now + timedelta(minutes=5):
        raise RuntimeError(f"Pinned certificate isn't valid yet: {not_before.isoformat()}")

    max_expired_days = float(os.getenv("AISSTREAM_PIN_MAX_EXPIRED_DAYS", "60"))
    expired_days = max(0.0, (now - not_after).total_seconds() / 86400.0)
    if expired_days > max_expired_days:
        raise RuntimeError(
            f"Pinned certificate expired {expired_days:.1f} days ago, beyond the allowed {max_expired_days:.1f} days"
        )

    metadata.update({
        "mode": "pinned_leaf_certificate_expiry_ignored",
        "pin_verified": True,
        "hostname_verified_against_certificate": True,
        "certificate_expired": now > not_after,
        "certificate_expired_days": round(expired_days, 2),
        "emergency_pin_until": pin_until.replace(microsecond=0).isoformat(),
    })
    return metadata


def peer_certificate_der(websocket: Any) -> bytes:
    transport = getattr(websocket, "transport", None)
    if transport is None:
        raise RuntimeError("WebSocket transport is unavailable; cannot verify certificate pin")
    ssl_object = transport.get_extra_info("ssl_object")
    if ssl_object is None:
        raise RuntimeError("TLS object is unavailable; refusing to send API key")
    der = ssl_object.getpeercert(binary_form=True)
    if not der:
        raise RuntimeError("Peer certificate is unavailable; refusing to send API key")
    return der


def expired_certificate_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        detail = " ".join(
            clean_str(value)
            for value in [getattr(exc, "verify_message", ""), str(exc)]
        ).lower()
        return "expired" in detail
    return False


def strict_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def emergency_ssl_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def open_websocket_with_verified_transport() -> tuple[Any, dict[str, Any]]:
    parsed = urlparse(AISSTREAM_URL)
    hostname = parsed.hostname
    if not hostname:
        raise RuntimeError(f"Invalid AISSTREAM_URL: {AISSTREAM_URL}")

    common_kwargs = {
        "ping_interval": 20,
        "ping_timeout": 20,
        "max_size": 2 ** 22,
        "close_timeout": 5,
        "open_timeout": float(os.getenv("AISSTREAM_OPEN_TIMEOUT_SECONDS", "25")),
    }

    try:
        websocket = await websockets.connect(AISSTREAM_URL, ssl=strict_ssl_context(), **common_kwargs)
        metadata = certificate_metadata(peer_certificate_der(websocket))
        metadata.update({
            "mode": "strict_ca_and_hostname_verification",
            "pin_verified": False,
            "hostname_verified_against_certificate": True,
            "certificate_expired": False,
        })
        return websocket, metadata
    except ssl.SSLCertVerificationError as exc:
        if not expired_certificate_error(exc):
            raise RuntimeError(f"Strict TLS failed for a reason other than certificate expiry: {exc}") from exc
        if os.getenv("AISSTREAM_TLS_MODE", "strict").strip().lower() not in {"auto_pinned_emergency", "pinned_emergency"}:
            raise
        print(f"WARNING: strict TLS rejected an expired AISstream certificate: {exc}")

    websocket = await websockets.connect(AISSTREAM_URL, ssl=emergency_ssl_context(), **common_kwargs)
    try:
        metadata = validate_pinned_certificate(peer_certificate_der(websocket), hostname)
    except Exception:
        await websocket.close()
        raise
    print(
        "WARNING: AISstream emergency transport active; exact leaf certificate pin verified, "
        "certificate date validity intentionally ignored."
    )
    return websocket, metadata


async def collect_stream(subscription: dict[str, Any], duration_seconds: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contacts: dict[str, dict[str, Any]] = {}
    accepted_messages = 0
    invalid_json_messages = 0
    connection_attempts = 0
    connection_errors: list[str] = []
    transport_modes: set[str] = set()
    last_security: dict[str, Any] = {}
    deadline = time.monotonic() + duration_seconds
    max_attempts = max(1, int(os.getenv("AISSTREAM_MAX_CONNECTION_ATTEMPTS", "3")))

    while time.monotonic() < deadline and connection_attempts < max_attempts:
        connection_attempts += 1
        websocket = None
        try:
            websocket, security = await open_websocket_with_verified_transport()
            last_security = security
            transport_modes.add(str(security.get("mode")))
            # The API key is sent only after strict TLS verification or exact pin validation.
            await websocket.send(json.dumps(subscription))

            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=min(5.0, remaining))
                except asyncio.TimeoutError:
                    continue

                try:
                    msg = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    invalid_json_messages += 1
                    continue

                if isinstance(msg, dict) and msg.get("error"):
                    raise RuntimeError(f"AISstream API error: {msg['error']}")
                if not isinstance(msg, dict) or not msg.get("MessageType"):
                    continue

                accepted_messages += 1
                contact = extract_contact(msg)
                key = contact_key(contact)
                if not key:
                    continue
                if key not in contacts:
                    contacts[key] = {}
                merge_contact(contacts[key], contact)

            break
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            connection_errors.append(error_text)
            print(f"AISstream connection attempt {connection_attempts}/{max_attempts} failed: {error_text}")
            if connection_attempts >= max_attempts or time.monotonic() >= deadline:
                break
            backoff = min(30, 10 * connection_attempts)
            await asyncio.sleep(min(backoff, max(0.0, deadline - time.monotonic())))
        finally:
            if websocket is not None:
                try:
                    await websocket.close()
                except Exception:
                    pass

    if accepted_messages < int(os.getenv("AISSTREAM_MIN_MESSAGES", "1")):
        raise RuntimeError(
            "AISstream delivered no usable AIS messages; preserving the previous provider snapshot. "
            f"Connection errors: {connection_errors or ['none; stream remained silent']}"
        )

    stats = {
        "requested_duration_seconds": duration_seconds,
        "connection_attempts": connection_attempts,
        "accepted_ais_messages": accepted_messages,
        "invalid_json_messages": invalid_json_messages,
        "unique_contacts_before_filter": len(contacts),
        "transport_modes_seen": sorted(transport_modes),
        "transport_security": last_security,
        "connection_errors": connection_errors,
    }
    return list(contacts.values()), stats


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


async def probe_one_address(hostname: str, address: str, family: int, port: int) -> dict[str, Any]:
    context = emergency_ssl_context()
    started = time.monotonic()
    writer = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=address,
                port=port,
                family=family,
                ssl=context,
                server_hostname=hostname,
            ),
            timeout=float(os.getenv("AISSTREAM_OPEN_TIMEOUT_SECONDS", "25")),
        )
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is None:
            raise RuntimeError("No TLS object returned")
        der = ssl_object.getpeercert(binary_form=True)
        if not der:
            raise RuntimeError("No peer certificate returned")
        result = certificate_metadata(der)
        result.update({
            "address": address,
            "family": "IPv6" if family == socket.AF_INET6 else "IPv4",
            "tls_version": ssl_object.version(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "ok": True,
        })
        return result
    except Exception as exc:
        return {
            "address": address,
            "family": "IPv6" if family == socket.AF_INET6 else "IPv4",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def probe_certificate() -> int:
    parsed = urlparse(AISSTREAM_URL)
    hostname = parsed.hostname
    if not hostname:
        raise RuntimeError(f"Invalid AISSTREAM_URL: {AISSTREAM_URL}")
    port = parsed.port or 443

    loop = asyncio.get_running_loop()
    addrinfo = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    addresses: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in addrinfo:
        address = sockaddr[0]
        key = (family, address)
        if key not in seen:
            seen.add(key)
            addresses.append(key)

    results = [await probe_one_address(hostname, address, family, port) for family, address in addresses]
    print(json.dumps({"hostname": hostname, "port": port, "results": results}, indent=2))

    successful = [result for result in results if result.get("ok")]
    fingerprints = sorted({result["fingerprint_sha256"] for result in successful})
    for fingerprint in fingerprints:
        display = ":".join(fingerprint[i:i + 2] for i in range(0, 64, 2)).upper()
        print(f"::notice title=AISstream SHA-256 certificate pin::{display}")

    if not successful:
        print("ERROR: no AISstream endpoint returned a TLS certificate")
        return 1
    if len(fingerprints) > 1:
        print("ERROR: AISstream backends presented different leaf certificates; do not enable pinned emergency mode yet")
        return 2
    return 0


async def main() -> int:
    api_key = clean_str(os.getenv("AISSTREAM_API_KEY"))
    if not api_key:
        raise SystemExit("AISSTREAM_API_KEY missing")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    watch_idx = load_watchlist_index()
    risk_mids = load_flag_risk_mids()
    ru_codes, ru_names = load_russian_port_terms()
    subscription = {
        "APIKey": api_key,
        "BoundingBoxes": BOUNDING_BOXES,
        "FilterMessageTypes": MESSAGE_TYPES,
    }

    duration_seconds = max(30, int(os.getenv("AISSTREAM_SAMPLE_SECONDS", "1800")))
    contacts_raw, fetch_stats = await collect_stream(subscription, duration_seconds)
    contacts_out = [
        contact
        for contact in contacts_raw
        if keep_contact(contact, watch_idx, risk_mids, ru_codes, ru_names)
    ]
    min_kept = max(0, int(os.getenv("AISSTREAM_MIN_KEPT_CONTACTS", "1")))
    if len(contacts_out) < min_kept:
        raise RuntimeError(
            f"AISstream returned {len(contacts_raw)} unique contacts but only {len(contacts_out)} passed filters; "
            "preserving the previous provider snapshot instead of writing an unexpectedly empty result."
        )

    transport_mode = clean_str(fetch_stats.get("transport_security", {}).get("mode"))
    for contact in contacts_out:
        contact["transport_security_mode"] = transport_mode

    payload = {
        "schema_version": "1.1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "AISStream",
        "filter_mode": "BoundingBoxes + Russian MMSI + watchlist + flag-risk MMSI prefixes + Russian destination/port terms + neutral tanker context zones",
        "coverage_mode": "global_selected_regions",
        "transport_security": fetch_stats["transport_security"],
        "fetch_stats": fetch_stats,
        "count": len(contacts_out),
        "contacts": contacts_out,
    }
    atomic_json_write(OUTPUT_PATH, payload)
    print(
        f"AISstream fetch complete: {fetch_stats['accepted_ais_messages']} AIS messages, "
        f"{len(contacts_raw)} unique contacts, {len(contacts_out)} kept; transport={transport_mode}"
    )
    return 0


def cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--probe-certificate",
        action="store_true",
        help="Print current AISstream endpoint certificate metadata and SHA-256 fingerprint without sending an API key.",
    )
    args = parser.parse_args()
    if args.probe_certificate:
        return asyncio.run(probe_certificate())
    return asyncio.run(main())


if __name__ == "__main__":
    raise SystemExit(cli())
