#!/usr/bin/env python3
"""Restore and publish bounded live data without adding each update to main history."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANCH = os.environ.get("RUNTIME_DATA_BRANCH", "runtime-data")
PREFIXES = ("data/", "public/data/", "public/downloads/")
BASE_ENV = "RUNTIME_DATA_BASE"


def git(*args, env=None, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, env=env, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def set_base(sha):
    env_file = os.environ.get("GITHUB_ENV")
    if env_file:
        with open(env_file, "a", encoding="utf-8") as stream:
            stream.write(f"{BASE_ENV}={sha}\n")
    os.environ[BASE_ENV] = sha


def tree_entries(revision):
    raw = git("ls-tree", "-rz", "--full-tree", revision).stdout
    result = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        if kind == "blob":
            result[path.decode("utf-8", "surrogateescape")] = (mode, oid)
    return result


def restore():
    git("fetch", "--no-tags", "origin",
        f"refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    base = git("rev-parse", f"refs/remotes/origin/{BRANCH}").stdout.decode().strip()
    git("checkout", base, "--", "data", "public/data", "public/downloads")
    # Keep main's index clean. Workflows stage only their declared outputs.
    git("reset", "--quiet", "HEAD", "--", "data", "public/data", "public/downloads")
    set_base(base)
    print(f"Restored live data snapshot {base[:12]} from {BRANCH}.")


def ensure_no_unstaged():
    base = os.environ.get(BASE_ENV)
    if not base:
        raise ValueError("Runtime data baseline is missing")
    baseline = tree_entries(base)
    names = git("diff", "--name-only", "-z").stdout.split(b"\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").stdout.split(b"\0")
    unexpected = []
    for raw_name in filter(None, names + untracked):
        name = raw_name.decode("utf-8", "surrogateescape")
        if not name.startswith(PREFIXES):
            unexpected.append(name)
            continue
        old = baseline.get(name)
        path = ROOT / name
        if not path.exists():
            current = None
        else:
            current_oid = git("hash-object", "--", name).stdout.decode().strip()
            current_mode = "100755" if os.access(path, os.X_OK) else "100644"
            current = (current_mode, current_oid)
        if current != old:
            unexpected.append(name)
    if unexpected:
        print("::error::Tracked output changes remain unstaged; refusing publication.")
        for name in unexpected[:30]:
            print(name)
        raise SystemExit(1)
    print("No generated output changes remain unstaged.")


def staged_entries():
    raw = git("ls-files", "--stage", "-z").stdout
    entries = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, oid, stage = meta.decode("ascii").split()
        if stage == "0":
            entries[path.decode("utf-8", "surrogateescape")] = (mode, oid)
    return entries


def staged_paths():
    raw = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRD").stdout
    return [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]


def publish(message):
    base = os.environ.get(BASE_ENV)
    if not base:
        raise ValueError("Runtime data baseline is missing")
    base_entries = tree_entries(base)
    staged = staged_entries()
    changes = {}
    for name in staged_paths():
        if not name.startswith(PREFIXES):
            raise ValueError(f"Refusing to publish outside live data paths: {name}")
        old = base_entries.get(name)
        new = staged.get(name)
        if new != old:
            changes[name] = new
    if not changes:
        print("No live data changes to publish.")
    for attempt in range(1, 6):
        git("fetch", "--no-tags", "origin",
            f"refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
        latest = git("rev-parse", f"refs/remotes/origin/{BRANCH}").stdout.decode().strip()
        temp_name, temp_index = tempfile.mkstemp(prefix="runtime-index-")
        os.close(temp_name)
        try:
            os.unlink(temp_index)
            index_env = os.environ.copy()
            index_env["GIT_INDEX_FILE"] = temp_index
            git("read-tree", latest, env=index_env)
            for name, entry in changes.items():
                if entry is None:
                    git("update-index", "--force-remove", "--", name, env=index_env)
                else:
                    mode, oid = entry
                    git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{name}",
                        env=index_env)
            tree = git("write-tree", env=index_env).stdout.decode().strip()
        finally:
            try:
                os.unlink(temp_index)
            except FileNotFoundError:
                pass
        latest_tree = git("rev-parse", f"{latest}^{{tree}}").stdout.decode().strip()
        if tree == latest_tree:
            set_base(latest)
            print("Runtime data branch already contains these products.")
            return
        # A parentless snapshot prevents this high-churn branch from retaining
        # old data commits. The explicit lease protects concurrent publishers.
        commit = git("-c", "user.name=github-actions[bot]",
                     "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                     "commit-tree", tree, "-m", message).stdout.decode().strip()
        pushed = git("push", f"--force-with-lease=refs/heads/{BRANCH}:{latest}",
                     "origin", f"{commit}:refs/heads/{BRANCH}", check=False)
        if pushed.returncode == 0:
            set_base(commit)
            print(f"Published {len(changes)} changed live data file(s) to {BRANCH} ({commit[:12]}).")
            return
        if attempt == 5:
            raise ValueError("Runtime data branch changed concurrently; publication failed after five retries")
    raise ValueError("Runtime data publication failed")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"restore", "ensure-no-unstaged", "publish"}:
        raise SystemExit("Usage: runtime_data_store.py restore|ensure-no-unstaged|publish [message]")
    try:
        if sys.argv[1] == "restore":
            restore()
        elif sys.argv[1] == "ensure-no-unstaged":
            ensure_no_unstaged()
        else:
            publish(" ".join(sys.argv[2:]) or "Update bounded runtime data")
    except Exception as exc:
        print(f"::error::Runtime data storage operation failed: {type(exc).__name__}. No data was published.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
