# Bounded runtime data publication

## What is published where

The GitHub Pages application and its source code stay on `main`. Generated live products continue to use the same repository-relative paths and JSON/GeoJSON schemas, but workflows now publish those products to the `runtime-data` branch.

The web interface reads current products from the raw `runtime-data/public/` tree. Magic Paws mirrors the same public paths from that branch. SAR encrypted workbench state remains on its existing artifact-backed path.

## How updates are bounded

Data-producing workflows restore the latest runtime snapshot before processing. They keep their current product validation, retention rules and storage guards. The publisher stages only declared generated paths, checks that no unexpected workspace changes remain, and compares each staged file with the restored snapshot.

A successful publication replaces the branch with a parentless snapshot. A force-with-lease update protects against concurrent writers; on a race, the publisher fetches the newest snapshot and reapplies only files changed by its job. The branch therefore exposes one current tree rather than a growing sequence of data commits. The first publishing run also compacts the initial seed, even when no product content changed.

The existing Git history on `main` is not rewritten. Moving future updates to `runtime-data` stops routine collection runs from adding data commits to `main`; it does not immediately erase old objects or shrink the repository’s already accumulated history. GitHub may reclaim unreachable objects later.

## Operations and limits

- The same `data/`, `public/data/`, and `public/downloads/` product paths remain available on the runtime branch.
- Existing 14-day retention, product-size guards, workflows, schedules and permission scopes remain in place.
- Pages deployment still builds the interface from `main`; data-only updates do not need a Pages rebuild.
- The SAR artifact guard continues to prune only older confirmed successful Pages build artifacts, protecting the newest three and unknown or active runs.
- If a runtime publication fails, the prior snapshot remains available. The workflow reports the failure instead of falling back to a Git commit on `main`.

Do not delete the `runtime-data` branch or reset it manually: it is now the live data source for the interface and the Magic Paws mirror.
