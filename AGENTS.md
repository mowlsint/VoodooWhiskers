# Repository guidance for Codex

## Scope and communication

This file applies to the entire repository.

- Communicate task summaries and decisions to the repository owner in German unless the owner requests another language.
- Keep code identifiers, schemas, filenames, and existing technical terminology in their established language.
- Prefer small, reviewable changes. Do not refactor unrelated areas.

## Repository role

Voodoo Whiskers is the authoritative producer of harmonized maritime vessel, VOI, AIS-context, infrastructure, and SAR products. Public products belong under `public/`.

Magic Paws is the consuming dashboard, event and report intelligence, archive, mail, snapshot, and SITREP layer. Keep the integration producer-to-consumer; do not move Magic Paws presentation or scoring responsibilities into this repository.

## Read before changing code

1. Inspect the current files under `config/`, `scripts/`, `tests/`, and `.github/workflows/` that govern the requested area.
2. Read the relevant document under `docs/`, especially `docs/VOODOO_INFRASTRUCTURE_UPGRADE_DELTA_v1.1.0.md` for public-product integration.
3. Treat current code, config, and workflows as the technical source of truth. Dated patch notes and patch manifests are historical context and may be stale.
4. Inspect the consuming Magic Paws paths before making a breaking public-data change.

## Cross-repository contract

The intended data flow is:

`Voodoo source data and analysis -> Voodoo public products -> Magic Paws mirrors -> Magic Paws presentation and reporting`.

Contract-sensitive outputs include:

- `public/data/manifest.json`
- `public/downloads/manifest.json`
- `public/data/vessels/voi_snapshot_latest.json`
- `public/data/vessels/voi_history_14d.jsonl`
- `public/data/vessels/ais_contacts_latest.json`
- `public/data/vessels/ais_contacts_latest.geojson`
- `public/data/vessels/maritime_common_snapshot_latest.json`
- `public/data/vessels/maritime_common_snapshot_latest.geojson`
- infrastructure and SAR products referenced by the manifests

Preserve stable paths and schemas. Before changing a shared field, category, filename, retention rule, or manifest entry, search both repositories and update producer, consumer, validation, and documentation together.

Keep infrastructure `score_integration` disabled until the owner explicitly approves active scoring in Magic Paws.

## Maritime and analytical invariants

- Public wording must use provider-neutral `AIS` or `Current monitored AIS contacts`; do not claim complete traffic coverage.
- Prefer `IMO -> MMSI -> callsign -> vessel name` for identity matching; a vessel name alone is a weak alias.
- Preserve explicit timestamp-validity and timestamp-basis fields. Never invent precision from an invalid or provider-default epoch.
- Low speed is not proof of drift. A cluster is not proof of coordination.
- A Russian MMSI or declared Russian destination is not proof of shadow-fleet status or a confirmed Russian port call.
- Neutral tanker context is non-suspicious context.
- SAR candidates remain candidates until the relevant quality, visual-review, and AIS-association gates are satisfied. Do not label an unmatched bright return a dark vessel by default.
- Do not claim sabotage, hostile activity, state control, or attribution from OSINT indicators alone.

## Data and source constraints

- Do not introduce paid AIS APIs. AIS Hub, supplied by the owner's receiver, is the chosen long-term AIS approach. AISStream, Fintraffic, and BarentsWatch are transitional/free sources.
- Never commit raw Danish AIS archives, provider downloads, credentials, API keys, tokens, or private endpoints.
- Preserve the bounded 14-day public VOI history and repository size guards unless the owner explicitly changes the policy.
- Prefer changing generators and configuration over hand-editing generated files under `public/`.
- Keep public manifests internally consistent: referenced files must exist, be non-empty, use safe relative paths, and report correct sizes.

## Runtime

- Node.js: 24, matching `.node-version`.
- Python: 3.11 in the production workflows.
- Do not add a new production dependency without explaining why existing standard-library or installed options are insufficient.

## GitHub Actions and cost guard

- Do not trigger, rerun, enable, or reschedule GitHub Actions unless the owner explicitly asks in the current task.
- The presence of `workflow_dispatch` is not permission to run a workflow.
- Prefer local validation because Actions capacity and operating cost are constrained.
- Do not change workflow permissions, repository secrets, schedules, deployment settings, or notification targets without explicit approval.

## Validation

Run the smallest relevant local checks, expanding them when the changed surface is broad.

For changed Python files:

```bash
python -m py_compile path/to/changed_file.py
```

For VOI history or retention changes:

```bash
python scripts/test_voi_history_policy_unit.py
python scripts/test_voi_history_retention.py --source-only
```

For SAR or common-snapshot changes, run the affected tests under `tests/`. When pytest is available:

```bash
python -m pytest tests
```

For public-product changes, normally run:

```bash
python scripts/build_public_outputs.py
python scripts/analyze_infrastructure_proximity.py
python scripts/build_public_manifest.py
```

Then verify:

- changed JSON and GeoJSON parse successfully;
- JSONL files parse line by line;
- manifest links are safe, relative, present, and size-consistent;
- public files remain below repository size guards;
- no raw Danish AIS archive or temporary provider data was introduced;
- Magic Paws' mirrored consumer paths remain compatible.

Do not use a remote workflow merely to perform checks that can be run locally.

## Definition of done

A change is complete only when:

- the requested behavior is implemented with minimal unrelated churn;
- relevant local checks pass, or unavailable checks are explicitly reported;
- public manifests and generated-product contracts remain valid;
- Magic Paws compatibility is preserved;
- analytical claim limits and data-quality semantics remain intact;
- no secret, raw provider archive, or oversized file is added;
- user-facing behavior or operational steps are documented when they changed.

## Code review rules

Flag changes that:

- silently change public filenames, manifest entries, schemas, categories, or retention;
- weaken timestamp quality, identity priority, SAR review gates, or no-attribution safeguards;
- activate score integration without explicit owner approval;
- describe monitored AIS as complete traffic coverage;
- commit raw provider data or expose secrets;
- trigger or expand paid/exhaustible workflows without explicit approval.
