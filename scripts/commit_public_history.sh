#!/usr/bin/env bash
# Second publication phase only: the current AIS commit is already on main.
set -euo pipefail

if [ "${GITHUB_REF_NAME:-}" != "main" ]; then
  echo "Refusing production writes from a non-main branch."
  exit 1
fi

for path in public/data/vessels/voi_history_14d.jsonl public/data/vessels/voi_history_14d/manifest.json public/downloads/voi_history_14d_manifest.json; do
  if [ ! -f "$path" ]; then
    echo "ERROR: required public history output missing: $path"
    exit 1
  fi
done

# The existing builder may also expire delayed GFW context; keep its mirrors
# and source consistent. Stage generated products only, never source code.
for path in public/data public/downloads data/gfw_presence_cells_latest.json data/gfw_events_latest.json; do
  [ ! -e "$path" ] || git add -- "$path"
done
if ! git diff --quiet; then
  echo "::error::Tracked changes remain unstaged; refusing to hide or discard them."
  git diff --name-only
  exit 1
fi
if git diff --cached --quiet; then
  echo "No public history changes to commit."
  exit 0
fi

python scripts/repo_storage_guard.py
git commit -m "Update bounded public VOI history after current positions"
for attempt in 1 2 3; do
  if git fetch --deepen=1 origin main && git rebase FETCH_HEAD && git push origin HEAD:main; then
    echo "Public history push succeeded."
    exit 0
  fi
  git rebase --abort 2>/dev/null || true
  if [ "$attempt" -lt 3 ]; then sleep 12; fi
done
echo "::error::Public history push failed; the earlier current-position publication remains available."
exit 1
