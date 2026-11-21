"""Lossless output formatting, stable context and canonical-history coverage."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import data_retention
import fetch_contacts
import fetch_regional_contacts
import merge_ais_contacts
import build_layers
import build_public_outputs
import fetch_gfw_common_presence
import analyze_infrastructure_proximity
import voi_history


def test_compact_writers_preserve_every_json_value(tmp_path):
    payload = {'name': 'Ægir', 'unknown': None, 'valid': False, 'lat': 54.125,
               'aliases': ['a', 'b'], 'nested': {'empty': {}, 'list': []}}
    writers = [fetch_contacts.atomic_json_write, fetch_regional_contacts.atomic_json_write,
               merge_ais_contacts.atomic_json_write, build_public_outputs.atomic_json,
               fetch_gfw_common_presence.atomic_json, analyze_infrastructure_proximity.atomic_json]
    for n, writer in enumerate(writers):
        file = tmp_path / f'output-{n}.json'
        writer(file, payload)
        text = file.read_text()
        assert json.loads(text) == payload
        assert '\n  ' not in text
    file = tmp_path / 'layer.geojson'
    features = [{'type': 'Feature', 'geometry': None, 'properties': payload}]
    build_layers.write_geojson(file, features)
    assert json.loads(file.read_text()) == {'type': 'FeatureCollection', 'features': features}
    assert '\n  ' not in file.read_text()


def test_rechecking_unchanged_context_does_not_change_payload():
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    pack = {'historical': True, 'generated_at': now.isoformat(), 'records': [
        {'latitude': 54.0, 'longitude': 12.0, 'flag': 'DEU',
         'interval_start': (now-timedelta(days=2)).isoformat(),
         'interval_end': (now-timedelta(days=1)).isoformat()}]}
    first = data_retention.bound_gfw(pack, 'presence', now=now)
    second = data_retention.bound_gfw(first, 'presence', now=now+timedelta(hours=4))
    assert second == first
    assert second['generated_at'] == pack['generated_at']
    expired = data_retention.bound_gfw(second, 'presence', now=now+timedelta(days=14))
    assert expired['records'] == []
    assert expired['retention_dropped'] == 1
    assert expired['retention_checked_at'] != first['retention_checked_at']
    assert expired['generated_at'] == pack['generated_at']


def test_changed_provider_metadata_and_new_rows_are_not_hidden():
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    event = {'id': 'one', 'type': 'LOITERING', 'start': (now-timedelta(days=2)).isoformat(),
             'end': (now-timedelta(days=1)).isoformat()}
    first = data_retention.bound_gfw({'historical': True, 'events': [event]}, 'events', now=now)
    changed = {**first, 'events': [event, {**event, 'id': 'two'}], 'provider_note': 'updated'}
    second = data_retention.bound_gfw(changed, 'events', now=now+timedelta(hours=4))
    assert second['count'] == 2
    assert second['provider_note'] == 'updated'
    assert second['retention_checked_at'] != first['retention_checked_at']
    metadata_only = data_retention.bound_gfw({**first, 'provider_note': 'changed'}, 'events', now=now+timedelta(hours=4))
    assert metadata_only['retention_checked_at'] != first['retention_checked_at']
    same_count = data_retention.bound_gfw({**first, 'events': [{**event, 'id': 'replacement'}]}, 'events', now=now+timedelta(hours=4))
    assert same_count['events'][0]['id'] == 'replacement'
    assert same_count['retention_checked_at'] != first['retention_checked_at']
    limited = data_retention.bound_gfw(first, 'events', now=now+timedelta(hours=4), max_bytes=1)
    assert limited['events'] == [] and limited['cap_applied'] and limited['size_dropped'] == 1


def test_equal_time_cells_keep_order_when_rechecked():
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    rows = [{'latitude': 54.0, 'longitude': lon, 'flag': 'DEU',
             'interval_start': (now-timedelta(hours=2)).isoformat(),
             'interval_end': (now-timedelta(hours=1)).isoformat()} for lon in [12.0, 12.1, 12.2]]
    first = data_retention.bound_gfw({'records': rows, 'historical': True}, 'presence', now=now)
    second = data_retention.bound_gfw(first, 'presence', now=now+timedelta(hours=4))
    assert second == first


def test_smaller_legacy_view_does_not_remove_canonical_observations(tmp_path):
    policy = voi_history.load_policy(Path(__file__).resolve().parents[1])
    assert policy['source_max_bytes'] == 8*1024*1024
    assert policy['source_total_max_bytes'] == 45*1024*1024
    assert policy['public_total_max_bytes'] == 22*1024*1024
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = [{'mmsi': '273123456', 'observed_at': (now-timedelta(minutes=i)).isoformat(),
             'latitude': 54.0, 'longitude': 12.0, 'position_timestamp_valid': True,
             'notes': 'fixture '*4000} for i in range(300)]
    file = tmp_path / 'data/voi_history.jsonl'
    result = voi_history.compact_source_history(file, rows, policy, file.parent/'voi_history_status.json', now=now)
    canonical = voi_history.retained_rows(file, policy, now=now)
    assert {voi_history.history_key(r) for r in canonical} == {voi_history.history_key(r) for r in rows}
    assert result['full_rows'] == len(rows)
    assert result['file_bytes'] <= policy['source_max_bytes']
    assert result['kept_rows'] < result['full_rows']
    assert result['complete_time_window'] is True and result['legacy_complete_time_window'] is False
