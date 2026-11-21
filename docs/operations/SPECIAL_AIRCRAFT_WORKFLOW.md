# Special aircraft collection and publication

This implements the owner's nominal ten-minute, registry-selected aircraft context in Voodoo Whiskers. It adds one isolated workflow, `update-special-aircraft.yml`, on main. Existing AIS/SAR workflows, their schedules and permissions, shared manifests, deployment settings, reports and scores are unchanged. No Magic Paws collector or mirror is added.

## Activation and run path

The registry's top-level `collection_enabled: true` now controls this collector. An individual aircraft must also have `enabled: true`, a consistent evidenced ICAO24 and supported roles. The collector reads `config/special_aircraft_registry.json` and `config/aircraft_collection.json`; the older `docs/planning/` JSON files are historical proposals and are not loaded at runtime.

Merging this workflow onto main makes its schedule eligible to run at UTC minutes 07/17/27/37/47/57. It does not run on PR pushes. [GitHub scheduling](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) is best-effort; delay, skipped/queued jobs, network failures and source coverage create genuine gaps. A ten-minute schedule is not a guarantee of uninterrupted sampling or enough precision to identify small circles. No Actions run was manually dispatched for this implementation.

The main-only job uses a separate concurrency group and a five-minute timeout. It validates configuration offline, samples the selected source, applies retention/budget checks, then publishes only `public/data/aircraft/`. It never makes ship collection depend on aircraft availability. The job needs the same scoped `contents: write` capability used by the repository's existing data writers, solely to publish generated aircraft files. It uses the ephemeral GitHub token for repository size/push operations; no new repository secret or external credential is required.

No pip/npm dependency is installed for production. Python is 3.11, matching the existing workflows. Both embedded Actions use Node 24 and are pinned to official stable releases checked on 2026-11-17:

| Action | Release | Commit |
| --- | --- | --- |
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| actions/setup-python | v7.1.0 | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` |

Release sources: [checkout](https://github.com/actions/checkout/releases/tag/v7.0.1), [setup-python](https://github.com/actions/setup-python/releases/tag/v7.1.0). Their pinned `action.yml` files were checked for `node24`. Node 24.22.1 was used for the local JavaScript checks; the production collector itself is standard-library Python.

## Source and quota

Use only the fixed public endpoint `https://api.adsb.lol/v2/icao/{comma-separated-hexes}`. The provider's route implementation forwards the hex filter to readsb; a live 64/41 split over the current 105 selected identities was checked. No worldwide military query, type discovery, registration-wide scrape, feeder-only API, key application, historical archive download or paid API is used.

At most two batches of 64 identities are attempted per slot. A registry larger than 128 selected identities is refused before any source request and requires explicit quota/budget review. After a slot status has been published, duplicate attempts in that ten-minute UTC slot make no extra calls. A failed publication cannot persist that marker; any separately authorized rerun still has its own two-request ceiling. Nominal usage is 144 polls / at most 288 source requests a day. This is below the provider's documented no-key allowance of 500 requests/day/IP, but GitHub runner IPs may be shared; it is not reserved capacity.

HTTP 429 stops the remaining batches and persists `Retry-After`, including an HTTP-date header. With no usable header, the fallback is 24 hours. No immediate retry and no IP/key rotation is performed. Later polls respect the persisted cooldown. The live OpenAPI describes an unkeyed fixed 24-hour quota bucket, while the website mentions midnight UTC; the code assumes neither reset time and follows the response header. Other source failures do not cause repeated retries within the job.

Expiry still runs during provider cooldowns and while collection is disabled. Turning collection off or receiving HTTP 429 must not prevent old chunks from expiring. Pause/quota/disabled reasons are shown in the map status instead of being presented as healthy zero detections.

Primary sources: [API and current allowance](https://www.adsb.lol/docs/open-data/api), [live schema](https://api.adsb.lol/api/openapi.json), [provider route source](https://github.com/adsblol/api/blob/main/src/adsb_api/utils/api_v2.py), [provider data models](https://github.com/adsblol/api/blob/main/src/adsb_api/utils/models.py). Aircraft positions and the derived history carry adsb.lol attribution and the [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) notice in the product and history format. The popup also shows the attribution link. Publication does not claim source accuracy, completeness or a proven mission.

## Positions and states

The selected hex must match the returned hex. A supplied provider registration must agree with the curated registration/serial or a documented string alias; otherwise that row is withheld. Callsigns and provider military flags never select aircraft. A missing provider registration does not manufacture a mismatch. Database/operator context and identity evidence remain available in the popup.

The position time is the provider's `now` in Unix milliseconds minus `seen_pos` in seconds. It is never replaced by request time. Provider cache time and each fix must be at most five minutes old; future/invalid positions are withheld. Network receipt time is refreshed after each request so response latency does not incorrectly classify a newly received fix as future-dated. Missing `lat/lon`, receiver-derived rough positions and `lastPosition` fallbacks are not plotted. ADS-B, MLAT and TIS-B are labelled separately. Message-only Mode-S detection is not a usable position.

Broad geographic boxes cover North Sea, Baltic, Channel, adjoining North Atlantic/Nordic seas, Black Sea and Hormuz context. They deliberately include coast and some land approaches; geography alone establishes no maritime task. Known authorities can be relevant without being proven on a maritime mission. Out-of-area positions are normally excluded, rather than treated as a fetch error.

| State | Meaning |
| --- | --- |
| fresh | Successful batches with usable selected positions and no unusable returned rows |
| valid_zero | Successful in-scope query with no usable positions and no unusable returned rows; not proof that aircraft are absent or grounded |
| partial | A successful batch/response is incomplete because another failed or returned unusable identities/positions |
| stale | Provider cache is outdated; old point times remain old |
| error | Fetch/schema, quota or budget failure; cached points are retained in the product but not shown as current |
| unavailable | File/source unavailable to the map, including before the first scheduled publication |

Errors and zero results are never interchanged. `status_latest.json` records the reason, batch count, rejection counters, cooldown, most recent successful query and input hashes. `position_data_at` refers to an actual observation time, while checked/generated times refer to collection/product operations. Logs contain short codes, never raw responses or exception bodies. The static registry still makes no blanket claim of demonstrated ADS-B visibility; each actual position has its provider position-source label.

## Storage and Git growth

| Limit | Enforcement |
| --- | --- |
| Total working set | 30 MiB, including registry/config/status, history, latest and any matching source/download copies |
| History | At most 27 MiB and fourteen days; immutable compact ten-minute JSONL chunks |
| Latest product | At most 1 MiB |
| Registry/config/status | At most 1 MiB together |
| Full new staged path content | At most 4 MiB per UTC day, including repeated latest/status versions; compression is not assumed |
| Existing repository warning | Pause aircraft fetching/publication of fresh samples at 500 MiB reported repository size |
| Existing global guard | Existing 750 MiB safety, push and active-vessel guards remain in place |

The history layout is `public/data/aircraft/history/YYYY-MM-DD/YYYYMMDDTHHMMSSZ.jsonl`. Each line is an array whose fields and license are in `history_format.json`. Registrations are retained beside hex/time/position/source for later offline work. No raw payload or second history copy is committed. A row is deduplicated by identity and provider position time. Duplicate conflicting coordinates within a response are withheld.

Whole chunks expire conservatively a few minutes before the fourteen-day boundary, preventing older fixes within a poll from surviving beyond the limit. The oldest chunks are removed earlier if the 27 MiB budget is reached. Capacity/retention does not promise a complete fourteen-day trace. Deleting files bounds the working tree; it does not shrink old Git commits.

The daily ledger counts full changed file contents, including each rewritten latest/status file, and reserves room for an explicit pause status. When it is exhausted, new samples are not published and subsequent slots that day make no source calls. Fetching resumes in the next UTC day if the other limits permit it. At the global warning threshold only small pause-state maintenance and expiry can proceed; the optional aircraft stream must not consume remaining ship capacity. A later increase in list size, copies, retention or request count is not implicit permission to raise a limit.

## Publication isolation

`publish_special_aircraft.py` rejects unrelated staged/working-tree changes. It checks aircraft output contracts and source hashes, runs the existing global storage checks, commits only the aircraft directory, fetches/rebases main, then repeats source/budget checks before a fast-forward push. Up to three Git transport retries are bounded; a conflict or a changed collection input is never resolved by overwriting remote data. No force push or history rewrite is used. A local bare-remote test verified preservation of a concurrent ship-product commit.

Existing shared data/download manifests were inspected: the builder uses explicit product specifications and does not ingest or copy this new directory. No manifest or ship schema is changed. The aircraft consumer already addresses its isolated same-origin product directly. The current map loads the latest aircraft product, applies strict observation windows and leaves full-history flight reconstruction to manual research; it does not silently fetch all retained chunks.

## Research links

Each popup links to Flightradar24's aircraft history page using a safely encoded registration/serial, with a generic registration/ICAO24 search fallback. It also offers an adsb.lol trace link for the actual observation's UTC day using its ICAO24. No flight-trace endpoint is called by the collector or automatically by the dashboard. Both sites open only on user action in a separate tab with opener/referrer protections.

Flightradar24 documents seven days of free history; older history/downloads can require a plan. [Provider feature documentation](https://www.flightradar24.com/blog/product-features/) and [history/playback help](https://www.flightradar24.com/about_mobile.php?device=ios). The open-map trace parameters are documented by [tar1090](https://github.com/wiedehopf/tar1090/blob/master/README-query.md). Availability for an individual government/military aircraft is not guaranteed, and a link does not prove that a historical track exists.

## Validation and first scheduled run

Local tests exercise source times, identity/position failures, ADS-B/MLAT/TIS-B, missing versus valid-zero, cooldown, batching, independent quotas, retention/size caps, counted Git bytes, source changes, safe research links, actual Leaflet controls and concurrent publication. Test provider fixtures and live smoke products were written only in temporary directories and are not shipped as production data.

The live smoke test returned four in-scope positions, all accepted by the actual JavaScript consumer, with zero consumer exclusions and both research-link destinations. It used two real API batches, without a GitHub Actions run. This demonstrates the API/producer/consumer path at that time; it is not a guarantee of future reception or a successful GitHub-hosted run.

```sh
python scripts/collect_special_aircraft.py  # validation only; no requests
python -m unittest discover -s tests -p 'test_aircraft_collection.py'
python -m unittest discover -s tests -p 'test_special_aircraft_registry.py'
python -m py_compile scripts/collect_special_aircraft.py scripts/publish_special_aircraft.py
node --test tests/test_special_aircraft.cjs tests/test_local_projects.cjs tests/test_vessel_categories.cjs
VOODOO_TEST_DOM_MODULE=/path/to/test-only/node_modules/jsdom node --test tests/test_local_projects_dom.cjs
```

After owner merge, the next regular scheduled run must establish its real GitHub runner/source availability and publish `aircraft_latest.json`, `status_latest.json`, `history_format.json`, and a history chunk only when a usable new position exists. Check the source state and UTC time, then enable **Special aircraft** on the map. Do not create a synthetic healthy empty product or manually dispatch a run without an explicit request.
