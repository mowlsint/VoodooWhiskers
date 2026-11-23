"""Conservative, date-anchored Russian-voyage evidence for VoodooWhiskers.

A historical destination declaration is NOT proof of a Russian port visit.
A last-port field without its own visit date is NOT fresh evidence.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

RECENT_PORT_DAYS = 10              # Never extend beyond the operational 14-day ceiling.
ACTIVE_DESTINATION_HOURS = 72       # Require a recent observation for current intent.
ALLOWED_FUTURE = timedelta(minutes=5)


def normalized(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def recent(value, now, max_age):
    dt = timestamp(value)
    return dt if dt and now - max_age <= dt <= now + ALLOWED_FUTURE else None


def known_port(value, ports):
    """Match an entire port/coded destination, never a substring or a vague RU mention."""
    target = normalized(value)
    if not target or target in {"RU", "RUS", "RUSSIA"}:
        return None
    codes = ports["codes"]
    names = ports["names"]
    valid_codes = {normalized(p) for p in codes if len(normalized(p)) == 5 and normalized(p).startswith("RU")}
    valid_names = {normalized(p) for p in names if len(normalized(p)) >= 4}
    return target if target in valid_codes or target in valid_names else None


def destination_target(raw, ports):
    value = str(raw or "").strip()
    if not value:
        return None
    value = re.sub(r"\s*(?:→|->|=>)\s*", ">", value)
    if ">" in value:
        parts = [p.strip() for p in value.split(">")]
        if len(parts) != 2 or not all(parts):
            return None
        value = parts[1]
    else:
        match = re.fullmatch(r"FROM\s+.+?\s+TO\s+(.+)", value, flags=re.I)
        if match:
            value = match.group(1).strip()
        elif re.match(r"^FROM\b", value, flags=re.I):
            return None
        else:
            match = re.fullmatch(r"TO\s+(.+)", value, flags=re.I)
            if match:
                value = match.group(1).strip()
    return known_port(value, ports)


def classify(contact, ports, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if contact.get("position_timestamp_valid") is False or contact.get("timestamp_valid") is False:
        observed = None
    else:
        observed = recent(contact.get("observed_at") or contact.get("last_seen_utc"),
                          now, timedelta(days=RECENT_PORT_DAYS))
    result = {"visit": False, "bound": False, "visit_at": None,
              "destination_at": None, "visit_port": None, "destination_port": None}

    # The real port-call date must be supplied independently of the current AIS sample.
    visited_date = recent(contact.get("last_ru_port_date") or
                          contact.get("last_port_date") or
                          contact.get("port_observed_at") or
                          contact.get("port_call_at"), now, timedelta(days=RECENT_PORT_DAYS))
    previous = next((port for key in ("last_ru_port_unlocode", "last_ru_port",
                                     "last_port_unlocode", "last_port_name",
                                     "port_unlocode", "port_name")
                     if (port := known_port(contact.get(key), ports))), None)
    if visited_date and previous and (not observed or visited_date <= observed + ALLOWED_FUTURE):
        # A dated provider/manual port record is an evidence lead, not independent
        # legal verification of a port arrival.
        result.update(visit=True, visit_at=visited_date.isoformat(), visit_port=previous)

    if observed and observed >= now - timedelta(hours=ACTIVE_DESTINATION_HOURS):
        current = (contact.get("destination") or contact.get("Destination") or
                   contact.get("destination_unlocode") or contact.get("next_port"))
        dest = destination_target(current, ports)
        if dest:
            result.update(bound=True, destination_at=observed.isoformat(), destination_port=dest)
    return result
