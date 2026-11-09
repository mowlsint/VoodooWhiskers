# Automatic Actions Pause — 2026-08-27

## Purpose

This document records the pause introduced on 2026-08-27. The owner-authorized partial resumption on 2026-11-09 is listed below; all other automatic triggers remain paused. No workflow file is deleted or moved. Every affected workflow remains available through `workflow_dispatch` for a future, explicitly authorized manual run.

This change does not run any workflow.

## Runtime baseline

- Node workflow runtime: **Node.js 24 LTS**, matching `.node-version`.
- Node workflows use `actions/setup-node@v5`.
- Existing `actions/checkout@v5` and `actions/setup-python@v6` remain unchanged.
- Runtime modernization should remain in place when automatic triggers are restored.

## Paused automatic triggers

| Workflow | Original automatic trigger(s) before 2026-08-27 |
|---|---|
| `build-public-history.yml` | `50 2 * * *` |
| `build-public-products.yml` | `workflow_run` after `Update maritime layers`, `Update regional AIS layers`, `Update Danish historical AIS`, or `Update GFW SAR and historical AIS context` |
| `sync-emodnet-reference.yml` | `25 0 * * 1` |
| `update-danish-historical-ais.yml` | `35 0 * * *` |
| `update-maritime-layers.yml` | `10 1 * * *` |
| `update-regional-ais.yml` | `10 2,14 * * *` |
| `update-sar-detections.yml` | `35 5 * * *` |

## Owner-authorized partial resumption — 2026-11-09

The owner requested three daily AIS collection windows and a sensible GFW refresh interval. Only these two schedules are restored:

| Workflow | Cron (UTC) | Purpose |
|---|---|---|
| `update-maritime-layers.yml` | `0 4,12,20 * * *` | Open Waters plus Finland/Norway snapshots, VOI, sanctions refresh at most once/day, public products |
| `probe-gfw-common-presence.yml` | `20 4 * * *` | Once-daily delayed GFW presence and Loitering/Gap context |

The schedules take effect when this change reaches the default branch. They do not request an immediate manual run. Both workflows retain manual dispatch, the existing shared concurrency group, fifteen-minute timeouts and pre-commit storage guards. Actual GitHub scheduling may be delayed; 04:20 is a stagger, not a dependency guarantee.

GFW is deliberately not fetched three times daily: the collector defaults to the UTC observation day five days ago, with a rolling event-query window. More same-day runs usually repeat the same historical query. This does not guarantee that the provider has completed that day or that every late-arriving observation is recovered.

All other Voodoo automatic triggers remain paused. In particular, `sar-workbench.yml` stays manual and password-protected; no scheduled authenticated radar processing, Danish archive downloads or reference rebuilds are enabled. The separate Magic Paws mirror schedule and deployments are not changed here. Fourteen-day data retention and size limits remain in force; pruning current files does not delete earlier Git history.

The maritime producer already builds public products inside its own job, so the old `build-public-products.yml` fan-in is not needed for this resumption.

## Safe return to full mode

1. Confirm Actions capacity, provider access, secrets, storage limits, and current data contracts.
2. Restore slow reference and source workflows first: EMODnet, Danish historical AIS, maritime layers, regional AIS, and SAR context.
3. Validate current outputs and manifests.
4. Restore `build-public-history.yml`.
5. Restore the `build-public-products.yml` `workflow_run` fan-in only after the producer workflow names still match.
6. Confirm Magic Paws mirror consumers remain compatible before restoring their VOI fetch schedules.
7. Keep Node.js 24 and `actions/setup-node@v5`.
8. Validate YAML locally and inspect the complete trigger inventory.
9. Run workflows only after explicit authorization; `workflow_dispatch` availability alone is not authorization.

The exact repository state immediately before this mode was commit `391be22ad364d2079eccf62e48a8d149d9ea73e2`. The table above is an additional, history-independent record of every removed automatic trigger.

## Files and prompts

No workflow, script, data file, or prompt was deleted. No production prompt was changed in this repository for this mode. If a later restoration requires prompt changes, archive the exact prior prompt in a dated `docs/prompt_archive/` directory before editing it.

