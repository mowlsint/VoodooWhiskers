# Voodoo Whiskers — EMODnet Reference Size Hotfix v1.1.1

**Date:** 2026-07-21  
**Applies after:** Infrastructure Watch / safe delta v1.1.0

## Problem corrected

The EMODnet synchronisation itself completed and produced usable reference data and
12 analyst-review events. The subsequent Git push failed because the WFS returned
very detailed pan-European geometries. The first implementation only tested whether
a feature intersected the Voodoo area; it then retained the complete original
geometry. Pretty-printed GeoJSON also placed millions of coordinates on separate
lines.

Observed failed blobs:

- `pipelines.geojson`: approximately 94.99 MB
- `power_cables.geojson`: approximately 128.65 MB

GitHub rejects individual files above 100 MB.

## Changes

`sync_emodnet_reference.py` now:

1. keeps the WFS bbox request;
2. clips every geometry exactly to `[-6, 50, 32, 72.5]`;
3. preserves only the configured geometry class;
4. performs topology-preserving simplification;
5. rounds coordinates to five decimal places;
6. retains only useful scalar identification/context properties;
7. writes compact UTF-8 GeoJSON instead of pretty-printed coordinate arrays;
8. adapts simplification when a layer exceeds its target size;
9. preserves the last-known-good file instead of writing an oversized replacement;
10. writes byte counts, simplification tolerance and processing details to the sync status.

The workflow installs Shapely and validates all EMODnet GeoJSON files before the
analysis or Git commit starts.

## Size policy

- target for line layers: 12 MiB per file;
- target for wind farms: 8 MiB;
- target for point/mixed support layers: 5 MiB;
- hard limit: 40 MiB per file;
- hard total limit: 60 MiB for all EMODnet GeoJSON reference files.

A run that exceeds a hard limit exits before the rebuild and `git commit` steps. Last-known-good reference files remain untouched.

## Analytical limitation

The published geometry remains suitable for regional visualisation and analyst-lead
proximity screening. It is clipped and simplified and therefore is not a
navigational chart, engineering survey or precise cable/pipeline position product.
The infrastructure event feed remains:

```text
score_integration: false
```

## Installation

Copy the patch into the Voodoo Whiskers repository root and commit the three runtime
files:

```text
.github/workflows/sync-emodnet-reference.yml
config/emodnet_layers.json
scripts/sync_emodnet_reference.py
```

The documentation file may also be committed.

Then manually run:

```text
Sync EMODnet infrastructure reference
```

No Git reset is required for the previous failed action. Its commit was never pushed
to the remote repository.

After a successful EMODnet push, run the existing Magic Paws VOI/Voodoo fetch so the
new reference and derived products are mirrored.

## Expected log additions

The sync summary should now include values similar to:

```json
{
  "successful_layers": 4,
  "configured_layers": 6,
  "total_reference_bytes": 12345678,
  "largest_reference_file_bytes": 4567890
}
```

`4 / 6` is not the cause of the original failure. It means four configured logical
layers found matching current WFS feature types; missing optional layer matches are
recorded in `sync_status.json` and do not invalidate the four successful reference
layers.
