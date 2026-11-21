# Voodoo Whiskers – VOI History 20-Day Retention Patch

Version: 1.0.0  
Date: 2026-08-16

## Purpose

This patch permanently prevents `data/voi_history.jsonl` from growing beyond
GitHub's single-file push limit. The first patched layer build automatically
migrates the existing file to a bounded, deduplicated 20-day history.

## Binding policy

- Retention window: 20 days.
- Canonical source history guard: 45 MiB.
- Public history guard: 40 MiB.
- Complete JSONL rows only; no byte-level truncation.
- Malformed, timestamp-less, expired and implausibly future-dated rows are removed.
- Duplicate `_history_key` values are collapsed, with the newest occurrence winning.
- The newest complete rows are retained if the size guard is reached.
- `data/voi_history_status.json` records migration and retention statistics.

The public filename `voi_history_14d.jsonl` is deliberately retained for
backward compatibility. Its content and manifest metadata follow the new
20-day policy. No viewer URL has to change.

## Installation

Extract the ZIP into the root of the `VoodooWhiskers` repository and preserve
all paths. Commit every supplied file in one commit.

The patch does not contain or replace the existing `data/voi_history.jsonl`.
Migration happens safely during the first layer build.

Suggested commit message:

```text
Bound VOI history to 20 days and add size guards
```

## First production run

1. Run `Update regional AIS layers` manually. It is the shorter of the two layer workflows.
2. Confirm `Validate bounded 20-day VOI history` is green.
3. Confirm `Commit updated outputs` succeeds.
4. Run `Build bounded public VOI history` manually.
5. Confirm its 20-day validation and commit steps are green.
6. Allow or run `Build public Voodoo products`.

Expected new production file:

```text
data/voi_history_status.json
```

Important status fields:

- `retention_days`: must be `20`.
- `file_bytes`: must be below `47185920`.
- `kept_rows`: retained JSONL records.
- `outside_window_rows`: records older than 20 days removed during migration.
- `duplicate_rows`: duplicate history keys collapsed.
- `dropped_for_size`: oldest in-window rows removed only if the 45 MiB guard was reached.
- `complete_time_window`: `true` when all valid in-window rows fitted.

## Data-retention note

Rows older than 20 days are intentionally removed from the current branch on
the first successful run. Earlier versions remain recoverable from existing Git
commits unless repository history is separately rewritten later.

If `complete_time_window` becomes `false`, the workflow still remains safe and
pushable, but the traffic volume has exceeded the reserved 45 MiB source budget.
That status is the trigger for a later sharded/object-storage design; it is not
silently presented as a complete 20-day record.

## Rollback

Revert the patch commit and restore `data/voi_history.jsonl` from the last known
good commit. Do not delete Git history merely to roll back this patch.

