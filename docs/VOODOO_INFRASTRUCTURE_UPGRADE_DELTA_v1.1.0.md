# Voodoo Whiskers Infrastructure Watch — Upgrade Delta v1.1.0

**Date:** 2026-07-21  
**Base expected:** infrastructure watch v1.0.1 (including CSV hotfix) already installed.

## Purpose

This is a source-only upgrade. It deliberately contains no generated files from `public/data/` or `public/downloads/`, no EMODnet placeholders and no copied VOI history.

## Main improvements

- Provider-neutral public label `AIS`.
- Public layer wording changed to `Current monitored AIS contacts`; it does not claim complete traffic coverage.
- Fintraffic/BarentsWatch invalid epoch timestamps receive explicit quality fields:
  - `position_timestamp_valid`
  - `position_timestamp_basis`
- AIS sentinel values are removed before downstream analysis.
- EMODnet WFS discovery, pagination and last-known-good preservation are more robust.
- Distance is calculated against line segments, not only geometry vertices.
- The complete 120-hour track is searched for infrastructure proximity. A vessel may already have moved away when an event is generated.
- Event geometry points to the closest observed position, while the latest position is retained separately.
- Shadow score remains disabled for active scoring.
- Bounded public history is rebuilt once daily or manually, not on every public-product build.

## Workflows

- `build-public-products.yml`: lightweight; does not rewrite the 14-day history.
- `build-public-history.yml`: once daily at 02:50 UTC and manually; rebuilds the bounded history and derived analysis.
- `sync-emodnet-reference.yml`: weekly and manually.

## Cleanup

Delete this accidentally committed old bytecode file if it exists:

```text
scripts/__pycache__/build_public_outputs.cpython-313.pyc
```

The new `.gitignore` prevents future Python bytecode commits.

## First run after applying

1. Commit the source changes.
2. Run `Build bounded public VOI history` manually once.
3. Run `Sync EMODnet infrastructure reference` manually if the existing EMODnet layers are missing or stale.
4. Verify `public/data/manifest.json` and `public/downloads/manifest.json`.
