# Special aircraft layer

The main Voodoo dashboard has a directly visible **Special aircraft** card immediately below the time selector. The layer is off by default; its checkbox remains usable when data are absent. Enabling it fetches only the same-origin `data/aircraft/aircraft_latest.json`. No provider API, collector or workflow is started by this control.

Aircraft have a dedicated aircraft-symbol marker and a separate result list. Pop-ups show all supplied roles, registration, ICAO24, operator, commissioning authority, source and original observation time. Symbols do not indicate heading; the supplied track is shown as text in the pop-up. Routine offshore transport is a separate, default-off civil-context option; offshore SAR remains part of rescue context. The vessel search remains vessel-only.

## Current availability

This PR supplies the presentation consumer, static registry and offline checks. The curated selection is maintained in `config/special_aircraft_registry.json`; see `docs/research/SPECIAL_AIRCRAFT_REGISTRY_REVIEW_20261117.md` for evidence, selection decisions and remaining coverage gaps. An aircraft's `enabled: true` means selected for the future collector. It does not start collection or establish a current mission. There is no provisional-assignment display tier.

No production position dataset or collector is included. Source/API retrieval, publication terms and measured collection/storage budgets remain to be validated. With no generated aircraft file, the enabled control reports unavailable data rather than a healthy zero result. Do not publish a synthetic empty file to conceal missing collection.

The planned producer contract below is new and isolated; existing vessel, SAR, manifest and Magic Paws schemas are unchanged. A later collector must be reviewed and tested against this consumer before aircraft collection is described as integrated.

## Consumer contract

The latest selected-aircraft JSON must fit within **1 MiB**, a component of the planned **30 MiB total / 14-day aircraft budget**. The reader enforces the byte cap while streaming, accepts at most 5,000 input rows and has a ten-second request timeout. It loads only when enabled or explicitly refreshed. Full history is not fetched and no observations are written by the browser.

Required top-level fields:

| Field | Value |
| --- | --- |
| `kind` | `selected_aircraft_context` |
| `schema_version` | `1.0.0` |
| `context_only` | `true` |
| `coverage_complete` | `false` |
| `status` | `fresh`, `valid_zero`, `partial`, `stale`, `unavailable` or `error` |
| `generated_at` | Valid UTC product timestamp |
| `items` | Selected observation objects; empty only with truthful source state |

Each usable observation needs a six-hex-digit `icao24`, finite numeric `latitude`/`longitude`, original `observed_at`, `position_timestamp_valid: true`, `selection_status: registry_selected` and at least one supported `roles` value. The earlier `verified_registry` value remains accepted for compatibility. Neither value asserts official register verification or a verified current mission. Missing/unselected identities, explicit identity conflicts, invalid positions, future/over-fourteen-day times and unsupported roles are excluded. A later producer must join observations to an enabled, non-conflicting registry identity; a client flag is not independent verification. Same-identity observations at one time are deduplicated; conflicting coordinates at the same identity/time are withheld.

Supported roles extend the earlier planning vocabulary: `police`, `coast_guard`, `customs_border_guard`, `military_maritime_patrol`, `military_isr`, `military_other`, `nato_owned`, `maritime_surveillance`, `sar_support`, `environmental_monitoring`, `fisheries_monitoring`, `pollution_monitoring`, `emissions_monitoring`, `rpas`, `search_and_rescue`, `hems_maritime_context`, `offshore_sar`, `offshore_transport`. Civil surveillance does not become military patrol; SAR support/capability alone does not become a rescue event. Source research roles are retained separately in the registry.

Optional display fields: `registration`, `callsign`, `aircraft_type`, `operator`, `commissioning_authority`, `country`, `source`, `position_source`, `position_timestamp_basis`, `identity_source_url`, `role_source_url`, `identity_verified_at` (legacy field name, labelled Registry reviewed), `identity_review_note` (English), numeric `altitude_ft`, `ground_speed_kn` and `track_deg`. External strings are inserted as text, including evidence URLs. No external aircraft-lookup request is made. The static registry and its research notes are not automatically fetched by the dashboard.

Every pop-up says: “Independently verify whether this is an authority or military aircraft and what its flight activity or flight pattern represents.” It also identifies the possible inclusion of specialist civil aircraft and explains the limits of sampled positions. This notice does not replace exclusion of known identity contradictions or invalid positions.

## Time and analytical limits

Latest mode shows the latest usable observation per ICAO24 from the last thirty minutes. Historical 24/48/120-hour and fourteen-day modes filter the available product by the actual observation time; the product does not promise historical completeness. This presentation does not reconstruct tracks or infer circles between samples. Future positions are never shown. A product older than thirty minutes is labelled stale. Partial, valid-zero and unavailable/error states remain distinct; an unavailable/error product supplies no map points.

Aircraft are hidden during historical SAR comparison and local project-period views, including when an in-flight request finishes after switching views. Clearing the overview also switches aircraft and optional offshore transport off. No aircraft data are merged into vessel tracks, vessel projects, SAR association, government/Hybrid scores or report generation.

## Local validation

```sh
node --check public/assets/special-aircraft.js
node --check public/assets/infrastructure-watch.js
python scripts/special_aircraft_registry.py
python -m unittest discover -s tests -p 'test_special_aircraft_registry.py'
node --test tests/test_special_aircraft.cjs tests/test_local_projects.cjs tests/test_vessel_categories.cjs
VOODOO_TEST_DOM_MODULE=/path/to/test-only/node_modules/jsdom node --test tests/test_local_projects_dom.cjs
```

DOM integration checks use real Leaflet controls/markers and mocked same-origin products. They cover exposed/default-off controls, absence of startup aircraft requests, missing data, safe role/source pop-ups, strict windows, offshore context, reset, SAR races and project isolation. Test-only sample positions are not shipped as production data. No new production dependency is added.
