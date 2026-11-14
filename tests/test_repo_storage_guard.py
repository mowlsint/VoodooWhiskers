"""Offline checks using a real temporary Git index and a mocked size lookup."""
import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'repo_storage_guard', Path(__file__).resolve().parents[1] / 'scripts/repo_storage_guard.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class StorageGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.policy = {'max_new_push_bytes': 100, 'repository_safety_bytes': 1000,
                       'active_vessel_products_bytes': 200}
        (self.root / 'config').mkdir()
        (self.root / 'config/storage_policy.json').write_text(json.dumps(self.policy))

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE)

    def stage(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.git('add', '--', name)

    def run_guard(self, remote=100):
        output = io.StringIO()
        with patch.object(guard, 'repository_size_bytes', return_value=remote), contextlib.redirect_stdout(output):
            code = guard.main(self.root)
        return code, json.loads(output.getvalue())

    def test_success_and_duplicate_blobs_do_not_relax_budget(self):
        self.stage('data/voi_copy.json', b'x' * 60)
        self.stage('public/downloads/copy.json', b'x' * 60)
        code, result = self.run_guard()
        self.assertEqual(code, 1)
        self.assertEqual(result['staged_blob_bytes'], 120)
        self.assertEqual(result['unique_staged_blob_bytes'], 60)
        self.assertEqual(result['duplicate_staged_path_bytes'], 60)
        self.assertEqual(result['reserved_staged_bytes'], 240)
        self.assertEqual(result['staged_headroom_bytes'], -20)
        self.assertIn('New data exceeds push safety budget', result['errors'])
        self.assertEqual(len(result['largest_staged_files']), 2)

    def test_safe_run_reports_reserves(self):
        self.stage('data/voi_latest.json', b'x' * 40)
        code, result = self.run_guard()
        self.assertEqual(code, 0)
        self.assertTrue(result['within_safety_budget'])
        self.assertEqual(result['active_vessel_bytes'], 40)
        self.assertEqual(result['repository_headroom_bytes'], 820)
        self.assertEqual(guard.inspect_staged(self.root), 40)

    def test_all_budgets_reported_on_failure(self):
        self.stage('data/voi_latest.json', b'x' * 201)
        code, result = self.run_guard(remote=900)
        self.assertEqual(code, 1)
        self.assertEqual(len(result['errors']), 3)
        self.assertEqual(result['repository_headroom_bytes'], -302)
        self.assertEqual(result['active_vessel_headroom_bytes'], -1)

    def test_exact_limits_remain_allowed(self):
        guard.check(self.policy, 800, 100, 200)
        for values in [(0, 101, 0), (801, 100, 0), (0, 0, 201)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                guard.check(self.policy, *values)

    def test_forbidden_paths_still_fail_and_show_sizes(self):
        for name in ['data/raw/a.json', 'tmp/aisdk/a.csv', 'aisdk-test.zip',
                     'public/data/sar/workbench.enc.json']:
            with self.subTest(name=name):
                self.stage(name, b'x')
                code, result = self.run_guard()
                self.assertEqual(code, 1)
                self.assertIn('Raw or permanent SAR data is staged', result['errors'])
                self.assertGreater(result['staged_blob_bytes'], 0)
        with self.assertRaises(ValueError):
            guard.inspect_staged(self.root)

    def test_traffic_context_limit(self):
        self.stage('public/data/vessels/traffic_context_latest.json', b'x' * (1024 * 1024 + 1))
        code, result = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn('Traffic context exceeds the 1 MiB latest-only budget', result['errors'])

    def test_single_file_limit(self):
        self.stage('large.json', b'x' * (50 * 1024 * 1024))
        code, result = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn('Staged file reaches the 50 MiB application guard', result['errors'])

    def test_remote_failure_blocks_without_leaking_exception(self):
        self.stage('data/voi_latest.json', b'x' * 101)
        output = io.StringIO()
        with patch.object(guard, 'repository_size_bytes', side_effect=RuntimeError('private-detail')):
            with contextlib.redirect_stdout(output):
                code = guard.main(self.root)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertIsNone(result['remote_repository_bytes'])
        self.assertIsNone(result['repository_headroom_bytes'])
        self.assertIn('Repository size unavailable; refusing commit', result['errors'])
        self.assertIn('New data exceeds push safety budget', result['errors'])
        self.assertNotIn('private-detail', output.getvalue())

    def test_empty_index(self):
        code, result = self.run_guard()
        self.assertEqual(code, 0)
        self.assertEqual(result['staged_blob_bytes'], 0)
        self.assertEqual(result['largest_staged_files'], [])

    def test_inspection_failure_blocks_without_leaking_exception(self):
        output = io.StringIO()
        with patch.object(guard, 'inspect_staged_details', side_effect=RuntimeError('private-detail')):
            with contextlib.redirect_stdout(output):
                code = guard.main(self.root)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(result['within_safety_budget'])
        self.assertIn('Storage inspection failed; refusing commit', result['errors'])
        self.assertNotIn('private-detail', output.getvalue())


if __name__ == '__main__':
    unittest.main()
