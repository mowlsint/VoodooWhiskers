"""Run the actual commit/push shell against disposable local Git repositories only."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/update-maritime-layers.yml'
ROUTE_FILES = ('data/russian_route_state.json', 'data/russian_portcall_history.jsonl')


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.PIPE).strip()


class MaritimePublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / 'origin.git'
        git(self.root, 'init', '--bare', '--initial-branch=main', str(self.origin))
        self.seed = self.root / 'seed'
        git(self.root, 'init', '--initial-branch=main', str(self.seed))
        git(self.seed, 'config', 'user.name', 'Test')
        git(self.seed, 'config', 'user.email', 'test@example.invalid')
        source = WORKFLOW.read_text()
        step = source.split('      - name: Commit updated outputs', 1)[1]
        self.script = textwrap.dedent(step.split('        run: |\n', 1)[1])
        self.required = re.search(r'required_outputs=\((.*?)\n\)', self.script, re.S)[1].split()
        # Exercise the retries without artificial twelve-second delays in the fixture.
        self.script = self.script.replace('sleep 12', 'sleep 0')

    @staticmethod
    def write(root, name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def prepare(self, route_exists=True):
        files = self.required + ['data/voi_history_14d/manifest.json', 'README.md']
        if route_exists:
            files += list(ROUTE_FILES)
        for name in files:
            self.write(self.seed, name, 'before\n')
        # Storage policy itself is tested separately; here assert that the workflow invokes it.
        self.write(self.seed, 'scripts/repo_storage_guard.py',
                   'from pathlib import Path\nPath(".storage-guard-ran").write_text("checked")\n')
        git(self.seed, 'add', '.')
        git(self.seed, 'commit', '-m', 'fixture baseline')
        git(self.seed, 'remote', 'add', 'origin', str(self.origin))
        git(self.seed, 'push', 'origin', 'main')
        self.work = self.root / 'runner'
        git(self.root, 'clone', '--depth=1', self.origin.as_uri(), str(self.work))
        for name in self.required + list(ROUTE_FILES):
            self.write(self.work, name, 'new generated value\n')
        self.write(self.work, 'public/data/manifest.json', '{}\n')

    def publish(self):
        env = {**os.environ, 'GITHUB_REF_NAME': 'main', 'NTFY_ENABLED': 'false'}
        return subprocess.run(['bash', '-c', self.script], cwd=self.work, env=env,
                              capture_output=True, text=True, timeout=20)

    def assert_published(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.work / '.storage-guard-ran').exists())
        self.assertEqual(git(self.work, 'diff', '--name-only'), '')
        for name in ROUTE_FILES:
            self.assertEqual(git(self.origin, 'show', 'main:' + name), 'new generated value')

    def test_updated_route_state_and_events_are_committed(self):
        self.prepare()
        self.assert_published(self.publish())

    def test_first_route_files_are_committed(self):
        self.prepare(route_exists=False)
        self.assert_published(self.publish())

    def test_concurrent_unrelated_upstream_commit_is_preserved(self):
        self.prepare()
        self.write(self.seed, 'README.md', 'upstream documentation update\n')
        git(self.seed, 'add', 'README.md')
        git(self.seed, 'commit', '-m', 'concurrent source change')
        git(self.seed, 'push', 'origin', 'main')
        self.assert_published(self.publish())
        self.assertEqual(git(self.origin, 'show', 'main:README.md'), 'upstream documentation update')

    def test_unexpected_tracked_change_is_not_hidden_or_published(self):
        self.prepare()
        self.write(self.work, 'README.md', 'unexpected runner change\n')
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('README.md', result.stdout)
        self.assertFalse((self.work / '.storage-guard-ran').exists())
        self.assertEqual(git(self.origin, 'show', 'main:README.md'), 'before')


if __name__ == '__main__':
    unittest.main()
