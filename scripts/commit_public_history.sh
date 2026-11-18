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
python scripts/runtime_data_store.py ensure-no-unstaged
if git diff --cached --quiet; then
  echo "No public history changes to commit."
  exit 0
fi

python scripts/repo_storage_guard.py
python scripts/runtime_data_store.py publish "Update bounded public VOI history after current positions"
