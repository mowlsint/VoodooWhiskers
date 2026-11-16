# Local vessel tracks and projects

The existing Voodoo map can load analyst-supplied AIS observations and routes. Processing takes place in the browser. Imports, project files and conflict choices are never uploaded, committed to Git, sent to a provider or included in Magic Paws products. There is no automatic persistence in browser storage. Reloading or closing the page loses unsaved work; download a project file to retain a session.

## Find a vessel and jump to its position

1. Select the time window at the top of the dashboard.
2. Enter a vessel name, IMO, MMSI or callsign under **Find a vessel in this time window**.
3. Select a result, or press Enter for the first result. The map jumps to that vessel's latest accepted observation inside the selected window and opens its popup.

Search covers loaded workflow AIS, available VOI history and enabled local projects. Background AIS contacts can be found without enabling the traffic-context layer. Every candidate must have a usable position and observation time. **A ship observed 48 hours ago is absent from a 24-hour search.** Changing the window clears the previous search marker and rebuilds the result list; no out-of-window fallback is used. The latest/default view uses the existing bounded 14-day observation window. Historical workflow rows are fetched on demand from Voodoo's existing public history file. If history cannot be read, search uses the loaded observations and does not infer that a vessel is absent from the sea.

Names are search terms only, never identity keys. Search results are ordered by observation time and capped at 50 displayed entries. Narrow the query when necessary. Source toggles apply to workflow observations; local projects have separate visibility switches.

## Import observations

Open **Local vessel projects → Import CSV or KML**. Select a file, review optional identity fields, then choose **Preview import**. The preview reports accepted observations, undated routes and rejected rows/geometries, with up to ten error details. **Add to active project** imports the accepted content only. Review rejection counts before proceeding; the script does not silently claim that every source row was usable. A local session is created if none is active.

The import is treated as analyst-supplied AIS material; Voodoo does not make an external verification call. Validation covers structure, identity fields, time and coordinates, not the truth of the original AIS transmission. Original provider observations remain unchanged.

### CSV

Comma, semicolon and tab separators, UTF-8 BOMs, quoted fields and an introductory header section of up to 24 rows are supported. Latitude and longitude headers are required. Supported names include `lat`/`latitude`/`Breite`, `lon`/`longitude`/`Länge`, `timestamp`/`datetime`/`time`/`observed_at`/`last_seen_utc`, `MMSI`, `IMO`, `Callsign`/`Rufzeichen` and `name`/`Ship Name`/`Vessel Name`. Separate `date` and `time` columns are accepted. Decimal commas work with semicolon/tab CSV files.

```csv
mmsi,imo,name,latitude,longitude,timestamp
273123456,9198226,Example ship,55.1000,10.2000,2026-11-16T11:00:00Z
273123456,9198226,Example ship,55.1100,10.2200,2026-11-16T11:10:00Z
```

Use ISO date/time with `Z` or an explicit offset. Unix seconds/milliseconds are also recognized. A header explicitly containing `UTC`, or the checked **Timestamps without a timezone are UTC** option, allows timezone-free ISO timestamps to be read as UTC. Otherwise such rows are rejected rather than assigned an invented timezone. Ambiguous day/month formats are rejected. Optional fallback identity fields fill missing values; they do not overwrite identifiers already present in the file. A name without MMSI, valid IMO or callsign is insufficient.

### KML

- Timestamped `Point` Placemarks are supported with `TimeStamp/when` or time in `ExtendedData`.
- `gx:Track` pairs each `when` with its corresponding `gx:coord`; mismatched lengths are rejected.
- `ExtendedData/Data` and `SimpleData` can supply the same identity fields as CSV. Otherwise use the optional identity inputs.
- A `LineString` without per-point times remains an **undated route**. It is never converted into a series of invented timed AIS positions, connected to the timed workflow track or returned by time-scoped vessel search. **Show undated routes separately** displays it with a dashed line and an explicit popup label.
- Linked KML, external entities/DTDs and malformed XML are rejected. KMZ, GPX and provider-specific timestamp arrays are outside this first implementation.

## Identity and position conflicts

Valid IMO takes priority. MMSI can link to a single compatible IMO. Callsign-only observations can link to a strong identity only when the callsign has one unambiguous candidate. Names never merge records. Conflicting IMO/MMSI aliases block automatic track continuity.

Within each matched vessel, positions more than **one nautical mile apart within five minutes** form a review conflict. At exactly the same timestamp, a difference greater than one metre already triggers review. These are display/reconciliation tolerances, not proof of spoofing or a claim that either source is wrong. The **Position conflicts** section lists the alternatives with origin, time and coordinates. Select the accepted observation for that conflict group. The choice affects the local composite track and search jump; original observations are retained in the project file. Unresolved alternatives stay visible separately and are not joined by a track segment. If the latest search position is unresolved, the alternatives are listed explicitly rather than silently choosing one. When several projects load incompatible choices for the same group, the conflict remains unresolved until a new choice is made.

Matched workflow and local observations appear in one local overlay, retaining their origins. Their normal workflow markers are suppressed only while that project overlay is active; disabling or removing the project restores the workflow view. Connections over gaps exceeding 12 hours, conflicting identities, non-increasing times or implied speeds above 60 knots are not drawn. These are the existing conservative continuity limits, not a new source-verification process.

All timed local points obey the current dashboard window. Older points remain saved in the project file. Local project layers and vessel search pause during the legacy synchronized SAR comparison so current positions cannot be mistaken for the historical SAR/AIS product. Infrastructure layers and existing research links remain available.

## Project files and multiple sessions

Use **New session** to create a separate project and choose the active project for further imports. **Download project** writes a `.vwproject.json` file using the `VOODOO_LOCAL_PROJECT_V1` schema. It contains the project ID/name, creation time, import names and formats, normalized observations, undated route geometries and local conflict choices. It does not contain an account token, a copy of all workflow data or the original raw upload. A conflict decision referring to a workflow observation can only be reapplied if that same observation is available when the project is reopened.

Use **Open project files** to load one or several projects. Each project has its own visibility switch; imported vessel identities can be compared across enabled projects. Loading the same project ID twice is refused; remove the existing copy before loading a different saved version. **Remove from session** changes only the current page and never deletes a file on disk. The page warns before closing while a project has unsaved changes. A requested download cannot prove that the browser saved the file successfully; check your Downloads folder.

Limits protect browser responsiveness: 10 MiB per CSV/KML, 20 MiB per project file, eight loaded projects and 50,000 total imported observation/route vertices per session. All accepted project points are retained in the saved file. Map drawing samples up to 300 points per vessel and caps the overlay at 5,000 elements; the status explicitly reports that cap. Split very large or densely overlapping input into separate sessions.

## Storage guard and unchanged contracts

`repo_storage_guard.py` now counts `data/russian_route_state.json` and `data/russian_portcall_history.jsonl` in the existing active-vessel budget. The 128 MiB push, 250 MiB active-data and 750 MiB repository safety budgets are unchanged. No files are pruned and no retention or deletion policy is added. The append-only portcall history and long-term Git growth remain separate follow-up decisions; this change makes their active data visible to the guard.

No workflow, schedule, secret, permission, published product schema, retention period or analytical score is changed. Runtime UI modules use existing Leaflet and browser APIs without an added production package. The optional DOM test dependency is used only for local validation.

## Local checks

```sh
node --check public/assets/local-project-core.js
node --check public/assets/local-project-ui.js
node --check public/assets/infrastructure-watch.js
node --test tests/test_local_projects.cjs
python -m unittest discover -s tests -p test_repo_storage_guard.py
```

DOM interaction checks use a test-only installation of `jsdom` outside the repository:

```sh
npm install --prefix /tmp/voodoo-local-test-deps --no-save --no-package-lock jsdom
VOODOO_TEST_DOM_MODULE=/tmp/voodoo-local-test-deps/node_modules/jsdom node --test tests/test_local_projects_dom.cjs
```

These tests run the existing Leaflet map and the actual dashboard scripts with synthetic workflow fixtures and intercepted reads. They exercise time-scoped search/jumps, CSV preview/import, conflict decisions, project download/overlay, KML parsing, text-safe names and SAR separation. They neither call providers nor launch Actions. A visual browser review remains useful after merge; a full Chromium rendering check was unavailable in the implementation environment.
