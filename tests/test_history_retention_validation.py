"""Offline regression tests for complete and explicitly sampled VOI histories."""
import copy
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_voi_history_retention as validator
from voi_history import DEFAULT_POLICY, history_key, write_jsonl_shards


class HistoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.kwargs = dict(manifest_filename='manifest.json', max_part_bytes=4096,
                           retention_days=14, future_tolerance_hours=24, now=self.now,
                           total_max_bytes=8192, capacity_key='total_capacity')

    def fixture(self, sampled=False, empty=False, age=1, source=False):
        entries = []
        for n in range(0 if empty else 2):
            dt = self.now - timedelta(days=age+n)
            row = dict(mmsi=str(273000001+n), observed_at=dt.isoformat(), latitude=54.0,
                       longitude=12.0, position_timestamp_valid=True, source_provider='fixture')
            entries.append((dt, history_key(row), row))
        result = write_jsonl_shards(self.directory, entries, max_part_bytes=4096,
                                   retention_days=14, generated_at=self.now.isoformat(), kind='fixture')
        count = len(entries)
        capacity = dict(selection='identity_balanced_temporal_sampling' if sampled else 'complete',
                        total_rows=count+2 if sampled else count, selected_rows=count,
                        selected_bytes=result['total_bytes'], total_identities=count,
                        covered_identities=count, complete_time_window=not sampled)
        if sampled:
            capacity.update(dropped_rows=2, identity_coverage_percent=100.0)
        key = 'capacity' if source else 'total_capacity'
        self.kwargs['capacity_key'] = key
        result.update(complete_time_window=not sampled, **{key: capacity})
        self.save(result)
        return result

    def save(self, manifest):
        (self.directory / 'manifest.json').write_text(json.dumps(manifest))

    def validate(self):
        return validator.validate_shards(self.directory, **self.kwargs)

    def test_complete_sampled_and_empty(self):
        for sampled, empty, source in [(False, False, False), (True, False, False),
                                       (True, False, True), (False, True, False)]:
            with self.subTest(sampled=sampled, empty=empty, source=source):
                self.fixture(sampled=sampled, empty=empty, source=source)
                result, keys = self.validate()
                self.assertEqual(result['complete_time_window'], not sampled)
                self.assertEqual(result['rows'], 0 if empty else 2)
                self.assertEqual(len(keys), result['rows'])

    def test_legacy_complete_without_capacity(self):
        manifest = self.fixture()
        del manifest['total_capacity']
        self.save(manifest)
        self.validate()

    def test_partial_requires_capacity(self):
        manifest = self.fixture(sampled=True)
        del manifest['total_capacity']
        self.save(manifest)
        with self.assertRaisesRegex(AssertionError, 'no capacity metadata'):
            self.validate()

    def test_contradictory_capacity_is_rejected(self):
        original = self.fixture(sampled=True)
        changes = [dict(selected_rows=1), dict(selected_bytes=1), dict(total_rows=1),
                   dict(dropped_rows=1), dict(dropped_rows=-1), dict(dropped_rows=True),
                   dict(covered_identities=1), dict(total_identities=1),
                   dict(total_identities=5), dict(identity_coverage_percent=99.0),
                   dict(identity_coverage_percent=float('nan')), dict(complete_time_window=True),
                   dict(selection='complete'), dict(selected_rows=True)]
        for change in changes:
            with self.subTest(change=change):
                manifest = copy.deepcopy(original)
                manifest['total_capacity'].update(change)
                self.save(manifest)
                with self.assertRaises(AssertionError):
                    self.validate()

    def test_missing_sampling_counts_are_rejected(self):
        original = self.fixture(sampled=True)
        for field in ['dropped_rows', 'total_rows', 'selected_bytes', 'covered_identities']:
            with self.subTest(field=field):
                manifest = copy.deepcopy(original)
                del manifest['total_capacity'][field]
                self.save(manifest)
                with self.assertRaises(AssertionError):
                    self.validate()

    def test_incomplete_cannot_be_claimed_complete(self):
        manifest = self.fixture(sampled=True)
        manifest['complete_time_window'] = True
        self.save(manifest)
        with self.assertRaises(AssertionError):
            self.validate()

    def test_unknown_completeness_is_rejected(self):
        manifest = self.fixture()
        del manifest['complete_time_window']
        self.save(manifest)
        with self.assertRaisesRegex(AssertionError, 'not explicit'):
            self.validate()

    def test_total_budget_enforced_across_parts(self):
        manifest = self.fixture()
        self.kwargs['total_max_bytes'] = manifest['total_bytes'] - 1
        with self.assertRaisesRegex(AssertionError, 'total shard bytes exceed'):
            self.validate()

    def test_file_and_retention_guards_preserved(self):
        self.fixture()
        self.kwargs['max_part_bytes'] = 1
        # Keep the declared limit consistent so the file-size check is exercised.
        manifest = json.loads((self.directory / 'manifest.json').read_text())
        manifest['max_part_bytes'] = 1
        self.save(manifest)
        with self.assertRaisesRegex(AssertionError, 'exceeds policy'):
            self.validate()
        self.kwargs['max_part_bytes'] = 4096
        for age in [20, -3]:
            with self.subTest(age=age):
                self.fixture(age=age)
                with self.assertRaisesRegex(AssertionError, 'outside policy'):
                    self.validate()

    def test_hash_counts_paths_and_unlisted_parts_preserved(self):
        original = self.fixture(sampled=True)
        for field, value in [('sha256', 'bad'), ('rows', 99), ('bytes', 99),
                             ('filename', '../escape.jsonl')]:
            with self.subTest(field=field):
                manifest = copy.deepcopy(original)
                manifest['parts'][0][field] = value
                self.save(manifest)
                with self.assertRaises(AssertionError):
                    self.validate()
        self.save(original)
        (self.directory / 'voi-history-unlisted.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(AssertionError, 'unlisted stale shards'):
            self.validate()

    def test_duplicate_keys_across_parts_rejected(self):
        manifest = self.fixture()
        payload = (self.directory / manifest['parts'][0]['filename']).read_bytes()
        second = manifest['parts'][1]
        (self.directory / second['filename']).write_bytes(payload)
        second.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        self.save(manifest)
        with self.assertRaisesRegex(AssertionError, 'multiple shards'):
            self.validate()

    def main_fixture(self, scope):
        root = self.directory
        policy = dict(DEFAULT_POLICY)
        policy.update(source_shard_max_bytes=4096, public_shard_max_bytes=4096,
                      source_max_bytes=8192, public_max_bytes=8192,
                      source_total_max_bytes=8192, public_total_max_bytes=8192)
        self.directory = root / ('data' if scope == 'source' else 'public/data/vessels') / 'voi_history_14d'
        self.directory.mkdir(parents=True)
        manifest = self.fixture(sampled=True, source=scope == 'source')
        payload = b''.join((self.directory / p['filename']).read_bytes() for p in manifest['parts'])
        parent = self.directory.parent
        if scope == 'source':
            (parent / 'voi_history.jsonl').write_bytes(payload)
            status = dict(retention_days=14, file_bytes=len(payload), full_bytes=len(payload),
                          full_rows=2, complete_time_window=False, dropped_for_size=2)
            extra = parent / 'voi_history_status.json'
        else:
            (parent / 'voi_history_14d.jsonl').write_bytes(payload)
            manifest['compatibility'] = dict(selected_rows=2, selected_bytes=len(payload), complete_time_window=False)
            self.save(manifest)
            extra = root / 'public/downloads/voi_history_14d_manifest.json'
            extra.parent.mkdir(parents=True)
            status = copy.deepcopy(manifest)
        extra.write_text(json.dumps(status))
        return root, policy, extra, status

    def run_main(self, root, policy, scope):
        with patch.object(validator, 'ROOT', root), patch.object(validator, 'load_policy', return_value=policy):
            with patch.object(validator, 'datetime') as clock, patch.object(sys, 'argv', ['validate', '--'+scope+'-only']):
                clock.now.return_value = self.now
                with contextlib.redirect_stdout(io.StringIO()):
                    return validator.main()

    def test_source_status_must_match_sampled_history(self):
        root, policy, file, original = self.main_fixture('source')
        self.assertEqual(self.run_main(root, policy, 'source'), 0)
        for change in [dict(complete_time_window=True), dict(full_bytes=1),
                       dict(full_rows=1), dict(dropped_for_size=0)]:
            with self.subTest(change=change):
                file.write_text(json.dumps({**original, **change}))
                with self.assertRaises(AssertionError):
                    self.run_main(root, policy, 'source')

    def test_download_index_must_match_sampled_history(self):
        root, policy, file, original = self.main_fixture('public')
        self.assertEqual(self.run_main(root, policy, 'public'), 0)
        changes = [dict(total_rows=1), dict(total_bytes=1), dict(part_count=1),
                   dict(complete_time_window=True), dict(total_capacity={}), dict(parts=[])]
        for change in changes:
            with self.subTest(change=change):
                file.write_text(json.dumps({**original, **change}))
                with self.assertRaises(AssertionError):
                    self.run_main(root, policy, 'public')


if __name__ == '__main__':
    unittest.main()
