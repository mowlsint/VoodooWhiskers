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

The owner requested AIS collection at the six nautical watch changes and a sensible GFW refresh interval. The proposed initial Open Waters collection window is twenty minutes. Only these two schedules are restored:

| Workflow | Cron (UTC) | Purpose |
|---|---|---|
| `update-maritime-layers.yml` | `0 0,4,8,12,16,20 * * *` | Twenty minutes of Open Waters plus Finland/Norway snapshots, VOI, sanctions refresh at most once/day, public products |
| `probe-gfw-common-presence.yml` | `40 4 * * *` | Once-daily delayed GFW presence and Loitering/Gap context |

The schedules take effect when this change reaches the default branch. They do not request an immediate manual run. Both workflows retain manual dispatch, the existing shared concurrency group and pre-commit storage guards. The AIS job timeout is thirty-five minutes, allowing twenty minutes of reception plus setup and publishing; GFW retains its fifteen-minute timeout. Actual GitHub scheduling may be delayed; 04:40 is a stagger, not a dependency guarantee.

`config/openwaters.json` sets `sample_seconds` to 1200. The collector accepts at most twenty minutes, preserves its 100,000-unique-event memory guard per stream, and still retains only the latest two positions per vessel per collection window. A busy snapshot or provider error can end a stream early; the status records this and last-known-good output is preserved. Finland and Norway are REST snapshots collected once per job, not twenty-minute regional streams. Six twenty-minute windows total two hours per day and leave three-hour-forty-minute nominal gaps between windows; the map is not a continuous live service. The longer window does not retain every position from those twenty minutes or guarantee complete tracks. Published updates follow completion of reception and product generation. Existing byte budgets, provider-area caps, filtering, deduplication and fourteen-day retention remain unchanged.

Standard GitHub-hosted runners in a public repository do not draw from included private-repository compute usage (see [GitHub billing](https://docs.github.com/en/actions/concepts/billing-and-usage)). This does not remove repository or artifact storage limits. Magic Paws is private and is not given extra scheduled runs by this change.

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

