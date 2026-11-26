"""Regression coverage for restoring live data over a source checkout."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import runtime_data_store as store


class RuntimeDataRestoreTests(unittest.TestCase):
    def test_restores_live_products_and_preserves_curated_flag_inputs(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ):
            root = Path(directory)
            with patch.object(store, "ROOT", root), patch.object(store, "BRANCH", "runtime-data"):
                self.check_restore(root)

    def check_restore(self, root):
        curated = {
            "data/false_flag_watchlist.csv": "imo,identity_status\n1234567,review\n",
            "data/flag_risk_reference.csv": "iso_code,review_class\nDE,B\n",
        }
        runtime = {
            "data/false_flag_watchlist.csv": "imo\n1234567\n",
            "data/flag_risk_reference.csv": "iso_code\nDE\n",
            "data/current.json": '{"live":true}',
            "public/data/current.json": '{"live":true}',
            "public/downloads/current.json": '{"live":true}',
        }
        git = store.git
        git("init", "--quiet", "-b", "runtime-data")
        git("config", "user.name", "Local test")
        git("config", "user.email", "test@example.invalid")
        for filename, content in runtime.items():
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git("add", "--all")
        git("commit", "--quiet", "-m", "Old runtime snapshot")
        base = git("rev-parse", "HEAD").stdout.decode().strip()
        git("remote", "add", "origin", str(root))
        git("switch", "--quiet", "-c", "main")
        for filename, content in curated.items():
            (root / filename).write_text(content, encoding="utf-8")
        for filename in runtime.keys() - curated.keys():
            (root / filename).write_text('{}', encoding="utf-8")
        git("add", "--all")
        git("commit", "--quiet", "-m", "Current source and seed products")

        store.restore()

        for filename, expected in (runtime | curated).items():
            self.assertEqual((root / filename).read_text(encoding="utf-8"), expected, filename)
        self.assertEqual(os.environ[store.BASE_ENV], base)
        self.assertEqual(git("diff", "--cached", "--name-only").stdout, b"")
        store.ensure_no_unstaged()
