"""Validate the static aircraft selection registry offline; never collect positions."""
import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'config/special_aircraft_registry.json'
MAX_REGISTRY_BYTES = 1024 * 1024
ROLES = frozenset('police coast_guard customs_border_guard military_maritime_patrol military_isr '
                  'military_other nato_owned maritime_surveillance sar_support environmental_monitoring '
                  'fisheries_monitoring pollution_monitoring emissions_monitoring rpas search_and_rescue '
                  'hems_maritime_context offshore_sar offshore_transport'.split())


def validate_registry(registry):
    """Return selected records or reject an inconsistent registry, including excluded evidence."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(registry.get('schema') == 'VOODOO_SPECIAL_AIRCRAFT_REGISTRY_V1', 'Unsupported registry schema')
    require(type(registry.get('collection_enabled')) is bool, 'Collection switch must be boolean')
    require(registry.get('complete') is False, 'Registry must not claim complete coverage')
    policy = registry.get('selection_policy', {})
    require(policy.get('independent_user_verification_required') is True, 'Independent verification notice required')
    require(policy.get('geographic_gate_required_before_collection') is True, 'Geographic gate required')
    require(policy.get('callsign_or_type_alone_sufficient') is False, 'Type/callsign must not select identities')
    sources = registry.get('sources')
    require(isinstance(sources, list), 'Source catalogue required')
    source_ids = set()
    for source in sources:
        sid = source.get('id')
        require(isinstance(sid, str) and sid and sid not in source_ids, 'Invalid or duplicate source ID')
        url = urlsplit(source.get('url', ''))
        require(url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password,
                'Public HTTPS evidence URL required')
        source_ids.add(sid)
    rows = registry.get('aircraft')
    require(isinstance(rows, list), 'Aircraft list required')
    ids, selected_hex, selected = set(), set(), []
    for row in rows:
        rid = row.get('record_id')
        require(isinstance(rid, str) and rid and rid not in ids, 'Duplicate or invalid aircraft ID')
        ids.add(rid)
        require(type(row.get('enabled')) is bool, f'{rid}: enabled must be boolean')
        require(type(row.get('identity_conflict')) is bool, f'{rid}: identity conflict flag required')
        country = row.get('country')
        require(country is None or bool(re.fullmatch('[A-Z]{2}', country)), f'{rid}: invalid country code')
        require(country is not None or row.get('organisation_scope') == 'NATO', f'{rid}: missing country/scope')
        roles = row.get('roles')
        require(isinstance(roles, list) and len(set(roles)) == len(roles) and all(r in ROLES for r in roles),
                f'{rid}: unsupported/duplicate role')
        evidence = row.get('icao24_evidence', [])
        refs = row.get('identity_source_ids', []) + row.get('role_source_ids', [])
        refs += [item['source_id'] for item in evidence]
        require(all(sid in source_ids for sid in refs), f'{rid}: missing source')
        date.fromisoformat(row.get('reviewed_at', ''))
        require(row.get('adsb_position_source_verified') is False, f'{rid}: live ADS-B not demonstrated')
        require(row.get('observed_mission') is None, f'{rid}: static registry cannot establish a mission')
        if not row['enabled']:
            require(row.get('selection_status') == 'excluded' and bool(row.get('selection_reason')),
                    f'{rid}: excluded selection needs reason')
            continue
        hexcode = row.get('icao24')
        require(isinstance(hexcode, str) and bool(re.fullmatch('[0-9a-f]{6}', hexcode))
                and hexcode not in {'000000', 'ffffff'}, f'{rid}: invalid ICAO24')
        require(hexcode not in selected_hex, 'Duplicate enabled ICAO24')
        require(row.get('selection_status') == 'registry_selected' and row['identity_conflict'] is False,
                f'{rid}: identity conflict or wrong selection status')
        require(row.get('service_status') != 'historical_registration_replaced', f'{rid}: replaced identity')
        require(bool(row.get('registration') or row.get('military_serial')) and bool(row.get('operator')),
                f'{rid}: identity/operator mapping required')
        require(bool(roles) and bool(row.get('identity_source_ids')) and bool(row.get('role_source_ids')),
                f'{rid}: evidenced identity and role required')
        require(bool(evidence) and all(e.get('icao24') == hexcode and bool(e.get('provider')) for e in evidence),
                f'{rid}: contradictory or missing ICAO24 evidence')
        # One evidenced provider is deliberately sufficient under the owner's selection policy.
        selected_hex.add(hexcode)
        selected.append(row)
    return selected


def load_registry(path=DEFAULT_PATH):
    payload = Path(path).read_bytes()
    if len(payload) > MAX_REGISTRY_BYTES:
        raise ValueError('Registry exceeds 1 MiB registry/status component budget')
    registry = json.loads(payload)
    return registry, validate_registry(registry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    registry, selected = load_registry(args.path)
    print(json.dumps({'records': len(registry['aircraft']), 'selected': len(selected),
                      'excluded': len(registry['aircraft']) - len(selected),
                      'bytes': args.path.stat().st_size,
                      'selected_by_country_or_scope': dict(sorted(Counter(
                          a['country'] or a['organisation_scope'] for a in selected).items())),
                      'collection_started': False}, sort_keys=True))


if __name__ == '__main__':
    main()
