"""Focused offline tests for bounded SAR Actions artifact handling."""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests  # noqa: F401
except ImportError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = lambda: None
    sys.modules["requests"] = requests_stub

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sar_artifact_store as store_module


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, runs):
        self.runs = runs
        self.deleted = []
        self.run_queries = []

    def get(self, url, **kwargs):
        if url.endswith("/actions/runs"):
            self.run_queries.append(kwargs["params"])
            return FakeResponse({"workflow_runs": self.runs})
        raise AssertionError(f"Unexpected GET: {url}")

    def delete(self, url, **kwargs):
        self.deleted.append(url)
        return FakeResponse(status_code=204)


def run_record(run_id, *, status="completed", conclusion="success",
               name="pages build and deployment",
               path="dynamic/pages/pages-build-deployment", branch="main"):
    return {
        "id": run_id,
        "name": name,
        "path": path,
        "status": status,
        "conclusion": conclusion,
        "head_branch": branch,
        "head_sha": f"sha-{run_id}",
    }


def artifact(artifact_id, age_minutes, *, run_id=None, name="github-pages",
             branch="main", repository_id=1135880899, head_repository_id=1135880899,
             with_created=True, expired=False):
    created = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc) - timedelta(
        minutes=age_minutes
    )
    value = {
        "id": artifact_id,
        "name": name,
        "size_in_bytes": 1024,
        "expired": expired,
        "workflow_run": {
            "id": run_id if run_id is not None else artifact_id,
            "repository_id": repository_id,
            "head_repository_id": head_repository_id,
            "head_branch": branch,
            "head_sha": f"sha-{run_id if run_id is not None else artifact_id}",
        },
    }
    if with_created:
        value["created_at"] = created.isoformat().replace("+00:00", "Z")
    return value


class ArtifactStoreTests(unittest.TestCase):
    def test_prunes_only_old_successful_main_pages_artifacts_and_preserves_latest_three(self):
        items = [
            artifact(101, 0),
            artifact(102, 1),
            artifact(103, 2),
            artifact(104, 3),
            artifact(105, 4),
            artifact(106, 5),
            artifact(107, 6),
            artifact(108, 7),
            artifact(109, 8, with_created=False),
            artifact(110, 9, name="sar-workbench-encrypted"),
            artifact(111, 10, head_repository_id=42),
            artifact(112, 11, expired=True),
        ]
        runs = [
            run_record(101),
            run_record(102),
            run_record(103),
            run_record(104),
            run_record(105, conclusion="cancelled"),
            run_record(106, status="in_progress", conclusion=None),
            run_record(107, path="other/workflow.yml"),
            run_record(108, branch="feature"),
            run_record(110),
            run_record(111),
            run_record(112),
            # Artifact 109 deliberately has no matching workflow run.
        ]
        session = FakeSession(runs)
        store = store_module.Store(
            "mowlsint/VoodooWhiskers", "token", session=session
        )

        removed = store.prune_old_pages_artifacts(items)

        self.assertEqual(removed, [104])
        self.assertEqual(
            session.deleted,
            [
                "https://api.github.com/repos/mowlsint/VoodooWhiskers"
                "/actions/artifacts/104"
            ],
        )
        self.assertEqual(session.run_queries, [{"per_page": 100, "page": 1}])

    def test_repository_artifact_budget_counts_sar_reserve_and_excludes_existing_sar_copy(self):
        mib = store_module.MIB
        items = [
            {"name": "github-pages", "size_in_bytes": 376 * mib, "expired": False},
            {
                "name": "sar-workbench-encrypted",
                "size_in_bytes": 9 * mib,
                "expired": False,
            },
        ]

        projected = store_module.projected_repository_artifact_bytes(items)
        self.assertEqual(projected, 400 * mib)
        store_module.ensure_repository_artifact_budget(projected, "Not processed.")

        items.append({"name": "other", "size_in_bytes": 1, "expired": False})
        projected = store_module.projected_repository_artifact_bytes(items)
        with self.assertRaisesRegex(
            store_module.ArtifactStorageBudgetExceeded,
            "Artifact storage budget exceeded",
        ):
            store_module.ensure_repository_artifact_budget(projected, "Not processed.")


if __name__ == "__main__":
    unittest.main()
