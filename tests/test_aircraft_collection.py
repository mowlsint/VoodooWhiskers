"""Local provider, storage, producer/consumer and publication tests; never dispatch Actions."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from email.utils import formatdate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import collect_special_aircraft as C
import publish_special_aircraft as P
import runtime_data_store as R

NOW = int(datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp())


def payload(now, rows):
    return dict(ac=rows, msg='No error', now=now * 1000, total=len(rows), ctime=now * 1000, ptime=1)


def provider_row(hexcode, **extra):
    return dict(hex=hexcode, lat=55, lon=4, seen_pos=1, type='adsb_icao', mlat=[], tisb=[],
                gs=110, alt_baro=1500, track=45, flight='EXAMPLE', **extra)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in C.INPUTS + ['scripts/publish_special_aircraft.py', 'scripts/runtime_data_store.py', 'config/storage_policy.json']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.policy = C.read_json(self.root / 'config/aircraft_collection.json')
        _, rows = C.load_registry(self.root / 'config/special_aircraft_registry.json')
        self.selected = {r['icao24']: r for r in rows}
        self.hex = next(iter(self.selected))
        self.calls = []

    def fetcher(self, now, mode='positions'):
        def fetch(batch, limit):
            self.calls.append(list(batch))
            rows = [provider_row(self.hex)] if self.hex in batch and mode == 'positions' else []
            return payload(now, rows)
        return fetch

    def run_collection(self, now=NOW, fetcher=None, repository_bytes=1):
        return C.collect(self.root, now, fetcher or self.fetcher(now), repository_bytes)

    def latest(self):
        return C.read_json(self.root / 'public/data/aircraft/aircraft_latest.json')

    def test_real_selection_is_batched_once_and_only_aircraft_files_are_written(self):
        report = self.run_collection()
        self.assertEqual(report['requests'], 2)
        self.assertEqual([len(b) for b in self.calls], [64, 41])
        self.assertEqual(set(sum(self.calls, [])), set(self.selected))
        item = self.latest()['items'][0]
        self.assertEqual(item['observed_at'], C.iso(NOW - 1))
        self.assertEqual(item['position_source'], 'adsb')
        self.assertEqual(self.latest()['status'], 'fresh')
        chunk = next((self.root / 'public/data/aircraft/history').rglob('*.jsonl'))
        row = json.loads(chunk.read_text().splitlines()[0])
        self.assertEqual(row[:2], [self.hex, NOW - 1])
        self.assertEqual(len(row), len(C.HISTORY_FIELDS))
        self.assertEqual(P.validate_outputs(self.root)['status'], 'fresh')
        actual_bytes = sum(p.stat().st_size for p in (self.root / 'public/data/aircraft').rglob('*') if p.is_file())
        self.assertEqual(report['staged_path_bytes_today'], actual_bytes)
        again = self.run_collection(NOW + 10)
        self.assertEqual(again['requests'], 0)
        self.assertEqual(len(self.calls), 2)
        # Full integration with the actual JavaScript consumer.
        js = "const a=require('./public/assets/special-aircraft.js');const p=a.decode(JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')),Number(process.argv[2]));if(p.items.length!==1)throw Error('producer/consumer mismatch');"
        subprocess.run(['node', '-e', js, str(self.root / 'public/data/aircraft/aircraft_latest.json'), str(NOW * 1000)], cwd=ROOT, check=True)

    def test_zero_partial_error_and_last_good_are_distinct(self):
        self.run_collection(fetcher=self.fetcher(NOW, 'zero'))
        self.assertEqual(self.latest()['status'], 'valid_zero')
        self.run_collection(NOW + 600)
        before = self.latest()['items']
        def fail(batch, limit):
            raise C.FetchFailure('http_503')
        self.run_collection(NOW + 1200, fail)
        self.assertEqual(self.latest()['status'], 'error')
        self.assertEqual(self.latest()['items'], before)
        self.assertEqual(self.latest()['position_data_at'], before[0]['observed_at'])
        self.assertEqual(len(list((self.root / 'public/data/aircraft/history').rglob('*.jsonl'))), 1)

    def test_rolling_last_position_survives_empty_and_failed_polls_then_expires(self):
        self.run_collection()
        initial=self.latest()['rolling_24h']['items'][0]
        self.run_collection(NOW+600,self.fetcher(NOW+600,'zero'))
        self.assertEqual(self.latest()['status'],'valid_zero')
        self.assertEqual(self.latest()['items'],[])
        self.assertEqual(self.latest()['rolling_24h']['items'],[initial])
        def fail(batch,limit):raise C.FetchFailure('http_503')
        self.run_collection(NOW+1200,fail)
        self.assertEqual(self.latest()['rolling_24h']['items'],[initial])
        self.run_collection(NOW+86400,self.fetcher(NOW+86400,'zero'))
        self.assertEqual(self.latest()['rolling_24h']['items'],[])

    def test_rolling_backfill_and_new_position_replaces_old(self):
        self.run_collection()
        p=self.root/'public/data/aircraft/aircraft_latest.json'
        old=self.latest();old.pop('rolling_24h');p.write_bytes(C.encoded(old))
        self.run_collection(NOW+600)
        rows=self.latest()['rolling_24h']['items']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][1],NOW+599)

    def test_network_latency_does_not_make_a_real_provider_position_future_dated(self):
        def fetch(batch, limit):
            return payload(NOW + 1, [provider_row(self.hex)] if self.hex in batch else [])
        with patch.object(C.time, 'time', side_effect=[NOW, NOW + 2, NOW + 3]):
            report = C.collect(self.root, fetcher=fetch, repository_bytes=1)
        self.assertEqual(report['source_status'], 'fresh')
        self.assertEqual(self.latest()['items'][0]['observed_at'], C.iso(NOW))
        self.assertEqual(self.latest()['generated_at'], C.iso(NOW + 3))

    def test_second_batch_failure_is_partial_and_rate_limit_prevents_followup_requests(self):
        calls = []
        def fetch(batch, limit):
            calls.append(batch)
            if len(calls) == 2:
                raise C.FetchFailure('rate_limited', NOW + 86400)
            return payload(NOW, [provider_row(batch[0])])
        self.run_collection(fetcher=fetch)
        self.assertEqual(self.latest()['status'], 'partial')
        self.assertEqual(len(self.latest()['items']), 1)
        self.assertEqual(self.run_collection(NOW + 600, fetch)['requests'], 0)
        self.assertEqual(len(calls), 2)

    def test_rate_limit_headers_and_oversize_http_body_are_handled_without_retries(self):
        from unittest.mock import MagicMock
        for header in ['3600', formatdate(NOW + 3600, usegmt=True), 'invalid']:
            exc = HTTPError(C.PROVIDER + self.hex, 429, 'private body not propagated', {'Retry-After': header}, None)
            with patch.object(C.time, 'time', return_value=NOW), patch.object(C, 'urlopen', side_effect=exc) as http:
                with self.assertRaises(C.FetchFailure) as caught:
                    C.fetch_batch([self.hex], 1024)
                self.assertEqual(caught.exception.code, 'rate_limited')
                self.assertEqual(caught.exception.retry_at, NOW + (86400 if header == 'invalid' else 3600))
                self.assertEqual(http.call_count, 1)
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = C.PROVIDER + self.hex
        response.read.return_value = b'x' * 1025
        with patch.object(C, 'urlopen', return_value=response):
            with self.assertRaises(C.FetchFailure) as caught:
                C.fetch_batch([self.hex], 1024)
            self.assertEqual(caught.exception.code, 'oversize_response')

    def test_retention_runs_even_when_source_is_disabled_or_in_provider_cooldown(self):
        self.run_collection()
        out = self.root / 'public/data/aircraft'
        expired = out / 'history/2026-10-01/20261001T000000Z.jsonl'
        expired.parent.mkdir(parents=True)
        expired.write_text('[]\n')
        status_path = out / 'status_latest.json'
        status = C.read_json(status_path)
        status['retry_at'] = NOW + 86400
        status_path.write_bytes(C.encoded(status))
        before = len(self.calls)
        self.assertEqual(self.run_collection(NOW + 600)['code'], 'provider_backoff')
        self.assertFalse(expired.exists())
        self.assertEqual(len(self.calls), before)
        status['retry_at'] = 0
        status_path.write_bytes(C.encoded(status))
        reg_path = self.root / 'config/special_aircraft_registry.json'
        reg = C.read_json(reg_path)
        reg['collection_enabled'] = False
        reg_path.write_bytes(C.encoded(reg))
        expired.write_text('[]\n')
        self.assertEqual(self.run_collection(NOW + 1200)['code'], 'collection_disabled')
        self.assertFalse(expired.exists())
        self.assertEqual(self.latest()['status'], 'unavailable')
        self.assertEqual(len(self.calls), before)

    def test_invalid_times_positions_and_identity_conflicts_do_not_become_zero_results(self):
        for row in [provider_row(self.hex, r='UNRELATED-REG'),
                    {**provider_row(self.hex), 'lat': None},
                    {**provider_row(self.hex), 'seen_pos': -1},
                    {**provider_row(self.hex), 'seen_pos': 301},
                    {**provider_row(self.hex), 'lat': float('nan')},
                    {**provider_row(self.hex), 'type': 'mode_s'}]:
            with self.subTest(row=row):
                items, rejected = C.normalize(payload(NOW, [row]), self.selected, self.policy, NOW)
                self.assertEqual(items, [])
                self.assertTrue(rejected)
        stale = payload(NOW - 400, [])
        with self.assertRaises(C.FetchFailure):
            C.normalize(stale, self.selected, self.policy, NOW)
        with self.assertRaises(C.FetchFailure):
            C.normalize({'ac': []}, self.selected, self.policy, NOW)

    def test_geography_mlat_and_tisb_are_explicit_and_receiver_estimates_not_used(self):
        items, rejected = C.normalize(payload(NOW, [{**provider_row(self.hex), 'lat': 0, 'lon': 0}]), self.selected, self.policy, NOW)
        self.assertEqual(items, [])
        self.assertEqual(rejected, {'outside_regions': 1})
        for typ, expected in [('mlat', 'mlat'), ('tisb_icao', 'tisb'), ('adsb_icao', 'adsb')]:
            items, _ = C.normalize(payload(NOW, [{**provider_row(self.hex), 'type': typ}]), self.selected, self.policy, NOW)
            self.assertEqual(items[0]['position_source'], expected)
        raw = {**provider_row(self.hex), 'lat': None, 'rr_lat': 55, 'rr_lon': 4,
               'lastPosition': {'lat': 55, 'lon': 4, 'seen_pos': 800}}
        self.assertEqual(C.normalize(payload(NOW, [raw]), self.selected, self.policy, NOW)[0], [])

    def test_slot_history_is_immutable_deduplicated_and_expired_chunks_are_pruned(self):
        def duplicates(batch, limit):
            return payload(NOW, [provider_row(self.hex), provider_row(self.hex)] if self.hex in batch else [])
        self.run_collection(fetcher=duplicates)
        old = next((self.root / 'public/data/aircraft/history').rglob('*.jsonl'))
        before = old.read_bytes()
        self.assertEqual(len(before.splitlines()), 1)
        def same_time(batch, limit):
            return payload(NOW + 600, [{**provider_row(self.hex), 'seen_pos': 1}] if self.hex in batch else [])
        self.run_collection(NOW + 600, same_time)
        self.assertEqual(old.read_bytes(), before)
        expired = old.parent / '20261001T000000Z.jsonl'
        expired.write_bytes(before)
        self.run_collection(NOW + 1200, self.fetcher(NOW + 1200, 'zero'))
        self.assertFalse(expired.exists())
        self.assertEqual(old.read_bytes(), before)

    def test_daily_growth_cap_pauses_without_more_provider_calls(self):
        self.policy['max_staged_path_bytes_per_utc_day'] = 32768
        (self.root / 'config/aircraft_collection.json').write_bytes(C.encoded(self.policy))
        report = None
        for i in range(30):
            report = self.run_collection(NOW + i * 600)
            if report['code'] == 'daily_version_budget':
                break
        self.assertEqual(report['code'], 'daily_version_budget')
        calls = len(self.calls)
        again = self.run_collection(NOW + 31 * 600)
        self.assertEqual(again['code'], 'daily_version_budget')
        self.assertEqual(len(self.calls), calls)
        status = C.read_json(self.root / 'public/data/aircraft/status_latest.json')
        self.assertLessEqual(status['staged_path_bytes_today'], 32768)
        self.assertEqual(self.latest()['status'], 'error')

    def test_repository_warning_or_unknown_size_makes_no_provider_call(self):
        for size, code in [(None, 'repository_size_unavailable'), (600 * 1048576, 'repository_warning_budget')]:
            report = self.run_collection(NOW + (600 if size else 0), repository_bytes=size)
            self.assertEqual(report['requests'], 0)
            self.assertEqual(report['code'], code)
            self.assertEqual(self.latest()['status'], 'error')
        self.assertEqual(self.calls, [])

    def test_batch_overflow_and_corrupt_daily_ledger_fail_closed(self):
        self.policy['max_batches_per_poll'] = 1
        path = self.root / 'config/aircraft_collection.json'
        path.write_bytes(C.encoded(self.policy))
        with self.assertRaisesRegex(ValueError, 'batch capacity'):
            self.run_collection()
        self.assertEqual(self.calls, [])
        self.policy['max_batches_per_poll'] = 2
        path.write_bytes(C.encoded(self.policy))
        self.run_collection()
        s = self.root / 'public/data/aircraft/status_latest.json'
        status = C.read_json(s)
        status['staged_path_bytes_today'] = -1
        s.write_bytes(C.encoded(status))
        with self.assertRaisesRegex(ValueError, 'ledger'):
            self.run_collection(NOW + 600)

    def test_publisher_rejects_changed_source_and_unrelated_working_tree_files(self):
        self.run_collection()
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=self.root, check=True)
        for key, value in [('user.name', 'Local test'), ('user.email', 'test@example.invalid')]:
            P.git(self.root, 'config', key, value)
        P.git(self.root, 'add', 'config', 'scripts')
        P.git(self.root, 'commit', '-m', 'Local source fixture')
        unrelated = self.root / 'ship.json'
        unrelated.write_text('{}')
        base=P.git(self.root,'rev-parse','HEAD')
        with patch.object(R,'ROOT',self.root), patch.dict(os.environ,{R.BASE_ENV:base}):
            with self.assertRaises(SystemExit):
                P.publish(self.root, lambda repo: 1, push=False)
            unrelated.unlink()
            self.assertEqual(P.publish(self.root, lambda repo: 1, push=False), 'local_commit_only')
        source = self.root / 'config/aircraft_collection.json'
        source.write_bytes(source.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            P.validate_outputs(self.root)

    def test_publisher_merges_runtime_snapshot_without_replacing_concurrent_ship_products(self):
        self.run_collection()
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=self.root, check=True)
        for key, value in [('user.name', 'Local test'), ('user.email', 'test@example.invalid')]:
            P.git(self.root, 'config', key, value)
        P.git(self.root, 'add', 'config', 'scripts')
        P.git(self.root, 'commit', '-m', 'Local source fixture')
        with tempfile.TemporaryDirectory() as other:
            remote, writer = Path(other) / 'origin.git', Path(other) / 'writer'
            subprocess.run(['git', 'clone', '-q', '--bare', str(self.root), str(remote)], check=True)
            P.git(self.root, 'remote', 'add', 'origin', str(remote))
            base=P.git(self.root,'rev-parse','HEAD')
            P.git(remote,'update-ref','refs/heads/runtime-data',base)
            subprocess.run(['git', 'clone', '-q', '-b', 'runtime-data', str(remote), str(writer)], check=True)
            for key, value in [('user.name', 'Local ship fixture'), ('user.email', 'test@example.invalid')]:
                P.git(writer, 'config', key, value)
            (writer / 'data').mkdir()
            (writer / 'data/ship-fixture.json').write_text('{"retained":true}')
            P.git(writer, 'add', 'data/ship-fixture.json')
            P.git(writer, 'commit', '-m', 'Concurrent local ship fixture')
            P.git(writer, 'push', 'origin', 'runtime-data')
            with patch.object(R,'ROOT',self.root), patch.dict(os.environ,{R.BASE_ENV:base,'RUNTIME_DATA_BRANCH':'runtime-data'}):
                self.assertEqual(P.publish(self.root, lambda repo: 1), 'published')
            self.assertEqual(P.git(remote, 'show', 'runtime-data:data/ship-fixture.json'), '{"retained":true}')
            self.assertEqual(P.git(remote, 'show', 'runtime-data:public/data/aircraft/aircraft_latest.json'), json.dumps(self.latest(),ensure_ascii=False,separators=(',',':'),allow_nan=False))
            self.assertEqual(P.git(self.root, 'rev-parse', 'HEAD'), P.git(remote, 'rev-parse', 'main'))
            self.assertEqual(P.git(remote,'rev-list','--count','runtime-data'),'1')

    def test_history_size_cap_deletes_oldest_chunk_and_combined_copy_budget_is_enforced(self):
        self.policy['max_history_bytes'] = 1000
        path = self.root / 'config/aircraft_collection.json'
        path.write_bytes(C.encoded(self.policy))
        directory = self.root / 'public/data/aircraft/history/2026-11-16'
        directory.mkdir(parents=True)
        old = directory / '20261116T120000Z.jsonl'
        old.write_bytes(b' ' * 1500)
        self.run_collection()
        self.assertFalse(old.exists())
        copydir = self.root / 'public/downloads/aircraft'
        copydir.mkdir(parents=True)
        # Sparse test-only file: no raw provider download and no production artifact.
        with (copydir / 'copy.bin').open('wb') as f:
            f.truncate(30 * 1048576)
        with self.assertRaisesRegex(ValueError, 'Combined aircraft'):
            self.run_collection(NOW + 600)


if __name__ == '__main__':
    unittest.main()
