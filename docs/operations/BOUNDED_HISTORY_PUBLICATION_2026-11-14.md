# Bounded history publication and storage

The existing four-hour maritime workflow publishes current AIS/VOI products first.
After that commit reaches main, it builds and validates the public history and
publishes it in a separate guarded commit. A second-phase failure leaves the
earlier current-position publication available and fails the workflow explicitly.
Schedules, permissions, destinations and application-wide budgets are unchanged.
There are two pushes in a successful collection cycle. Existing push-based Pages
automation may consequently build twice; no Pages configuration is changed.

Generated operational JSON/GeoJSON uses compact JSON without dropping fields or
changing values. This reduces checkout and staged-file volume; it does not erase
earlier Git versions or predict GitHub's eventual packed repository size.

Canonical source history remains bounded by 14 days and 45 MiB in total. The
legacy `data/voi_history.jsonl` view is capped at 8 MiB, with reduced completeness
reported in the existing status. Maintained readers prefer canonical shards;
the legacy file is a compatibility view, not the complete archive. Public history
keeps its 22 MiB total limit, existing paths, schema and explicit sampling metadata.

GFW retention is still evaluated on every build. Already bounded, unchanged rows
keep their order, including equal-time cells. `retention_content_sha256` identifies
the content and retention result. `retention_checked_at` advances when that result
changes and remains the last material validation time otherwise. Provider/source
observation times are never refreshed by this optimization. Expiry, new provider
payloads and changed size budgets are still applied.

Local checks:

```bash
python -m pytest tests
python scripts/test_voi_history_policy_unit.py
python scripts/test_voi_history_retention.py --source-only
python scripts/test_voi_history_retention.py --public-only
bash -n scripts/commit_public_history.sh
```

No generated products are included in the code change. Normal authorized runs
perform the migration; no manual collection, history or mail run is required by
this document. A first post-merge normalization can write changed blobs once even
when observations are unchanged. The storage guard remains authoritative before
each publication. Long-term Git growth and offline archival remain separate work.
