# Aircraft context: 10-minute polling and bounded registry plan

Status: planning draft, 2026-11-16. These documents are not consumed by production code. They do not enable a collector, map layer, workflow or new source. The owner selected a 10-minute interval, a maximum of 14 days and a 30 MiB aircraft budget. Implementing those choices remains incomplete.

Companion planning data:

- `aircraft_context_policy.json`: owner decisions, proposed sub-budgets and activation checks.
- `aircraft_registry_candidates.json`: source-backed operator profiles, a small registration seed and explicit coverage gaps. It is not a complete or operational ICAO24 watchlist.

## Scope and intended behaviour

Voodoo Whiskers would produce selected aircraft context alongside its existing vessel products. The aircraft layer would be optional and hidden initially. Magic Paws could consume a separately reviewed product later. No existing ship schema, analytical score, Hybrid Index or Government Weirdness calculation changes in this preparation.

Retrieve the latest available positions by curated identity in short requests every ten minutes, nominally 144 polls per UTC day. A short API request does not require a twenty-minute listening session. A request returns positions last received by the provider; it does not guarantee a new transmission, complete coverage or a historical trace.

## Coverage requirements

| Family | Required coverage and distinction |
| --- | --- |
| Police | National and relevant coastal/regional police aviation; training is not an active police mission. |
| Coastguard, customs and border guard | Include state-owned and specifically documented contracted aircraft. Keep operator and commissioning authority separate. |
| Military | Maritime patrol, reconnaissance/ISR, relevant helicopters and other military context in the selected regions. Nationality alone is not evidence of military status. |
| NATO | NATO-owned AWACS/RPAS separately from national allied aircraft operating under NATO command. |
| Environmental monitoring | Oil-spill, maritime pollution and ship-emissions/sniffer aircraft. Role does not establish that pollution was detected on a given flight. |
| EMSA/Frontex RPAS and contractors | Verify each airframe, contract/deployment dates and public ADS-B visibility. Do not select every aircraft of a contractor as an agency aircraft. |
| Rescue | Maritime SAR, offshore SAR and HEMS with maritime context. An operator's rescue role does not prove an emergency is in progress. |
| Offshore transport | Selected platform, wind-farm and vessel-support helicopters as optional civil context; disabled by default and not labelled authority or suspicious activity. |

The core country coverage requirement includes DE, DK, NO, SE, FI, PL, EE, LV, LT, GB, IE, NL, BE, FR, IS and RU, plus visiting allied aircraft including the US and Canada. The Black Sea requirement also includes RO, BG, TR, UA and GE. IT, ES, PT, GR and CY are additional maritime-context coverage candidates. NATO, EMSA and Frontex are agency/operator contexts, not country codes. This is a curation scope, not a claim that the aircraft identifiers for all these countries have already been researched.

Use the established maritime focus: North/Baltic Seas, Channel and adjoining northeastern Atlantic/northern waters; retain the Black Sea context and separately configured chokepoints such as Hormuz. Source coverage and geographic activation must be reviewed for each area. Do not expand to a worldwide position archive.

## Registry evidence and current research status

The initial research file has 13 operator/role profiles and five registration candidates. Three candidate registrations are PH-CGD, PH-CGE and OO-MMM; two NPAS candidates, G-NPAA/G-NPAS, are explicitly associated with a training/modification programme. None has a verified ICAO24 mapping in this preparation. All automatic-selection flags remain false. Agency fleet numbers, registrations and ICAO24 addresses are different identifiers.

Official primary sources are recorded per profile/candidate. Older announcements are dated programme evidence, not confirmation of current airworthiness, deployment or operator assignment. Examples are the Finnish Dornier/Challenger transition and the dated EMSA contractor deployment.

Before enabling automatic collection, populate and cross-check current ICAO24, registration, operator, commissioning authority, role, country, evidence URL, verification date and validity/review dates. A changed or conflicting mapping must stop automatic joins. Callsign alone and aircraft type alone are insufficient. A provider military flag is database context, not proof of a current military task.

Complete the explicit coverage backlog in the research file before describing the registry as comprehensive. Add individual aircraft only with adequate identity/role evidence. Do not invent hex addresses, infer them from nationality or convert fleet numbers into transponder identities. Periodic bounded discovery can identify candidates for review; it must not silently promote weak matches into verified roles.

## Budget and storage design

| Aircraft component, including all persisted copies | Proposed share |
| --- | ---: |
| Selected position history | 27 MiB |
| Latest observations | 1 MiB |
| Registry, status, review-event metadata | 1 MiB |
| Reserve | 1 MiB |
| Total owner-selected hard ceiling | 30 MiB |

Fourteen days is a maximum retention period within the byte ceiling, not a promise of complete history for every aircraft. Count source, public, download and any future mirror copies; avoid storing multiple equivalent JSON/GeoJSON/history copies. Store only selected normalized positions with a compact encoding, references to shared identity metadata and the original observation time/quality. Raw provider responses are temporary.

At 55 simultaneously received selected aircraft, 144 polls/day and an assumed 250 bytes per stored position: 7,920 positions/day, about 1.89 MiB/day or 26.44 MiB over fourteen days, before shared metadata. The 250-byte assumption is a sizing model, not a measured collector output. At 100 simultaneous aircraft the same model needs about 48.1 MiB in fourteen days, so 30 MiB still requires explicit capacity handling.

Deduplicate repeated observations by stable identity and provider position time. Prefer small immutable time-slot chunks over rewriting the whole rolling history every ten minutes. Keep indexes compact; an ever-growing manifest rewritten on every poll can itself dominate version volume. Record omission/sampling and coverage metadata whenever the byte cap forces selection. Preserve enough original points for every published pattern claim; if evidence no longer supports the claim, downgrade or omit the pattern label.

Deleting expired files does not remove their older Git objects. The earlier 1 MiB/day version-volume suggestion was not an implemented policy. This plan proposes a separate ceiling of 4 MiB/day for all newly staged aircraft path content, subject to measured validation before activation; this could still add up to 120 MiB over thirty days before compression. A 30 MiB working set alone does not guarantee bounded repository history.

Existing repository limits remain unchanged: 250 MiB active monitored products, 500 MiB warning, 750 MiB repository safety ceiling, and the existing staged-data checks. The future guard must explicitly count every new aircraft path: the existing `active_vessel_bytes` directory selection does not automatically include a new aircraft directory. Aircraft optional writes should pause at the repository warning threshold before reducing ship-ingest headroom. No cleanup of Git history, force-push, secret changes or changes to existing vessel retention are included.

## Source and request budget

adsb.lol is a candidate, not an integrated source. Its official API page documents ODbL 1.0 and a no-key allowance of 500 requests per IP per day. Two successful batch requests per poll would require 288 requests/day; up to 24 additional discovery requests would make 312, leaving headroom for limited failures. Eight separate geographic requests on every ten-minute poll would require 1,152 requests/day and does not fit that allowance. Actual batch endpoint semantics, maximum batch size, shared-IP quota behaviour and response freshness must be tested.

Public API/schema retrieval could not be demonstrated in this preparation: the documentation/schema endpoints timed out. Complete the licensing/publication-obligation review before source selection. Do not rotate IPs to evade quotas. Honour HTTP 429/Retry-After and use bounded backoff; avoid repeated invalid requests. No account, key, subscription or secret is created. adsb.fi is not automatically reused for customer-related/publication purposes because its documented permission is personal/non-commercial without a separate agreement.

References: [adsb.lol API](https://www.adsb.lol/docs/open-data/api), [adsb.lol privacy and license](https://www.adsb.lol/docs/privacy-license), [adsb.fi terms/API](https://github.com/adsbfi/opendata/blob/main/README.md). The researched operator/airframe sources are listed in `aircraft_registry_candidates.json`.

## What ten-minute observations can show

Suitable initial outputs are observed presence, broad sampled flight paths and repeated occupation of an area when supported by enough distinct valid observations. At 240 knots an aircraft covers 40 nautical miles in ten minutes. It may complete small circles between samples; line segments are observation connectors, not an exact flown path. Do not promise reliable small-orbit detection or infer hovering from repeated similar sampled positions. A coarse repeated-area indication requires continuity and quality checks.

Use original provider position times, receive age and source/accuracy fields. Never give a cached position a fresh position timestamp. Break connections across excessive gaps, implausible jumps or identity conflicts. Missing reception is not proof of landing or absence. Fresh, valid-zero, partial, stale, unavailable and error states must remain distinct.

Only associate aircraft and vessels when their original observation times and position quality support the association. A four-hour-old vessel point cannot establish that a currently observed aircraft is following the vessel. Publish separation/time difference and uncertainty as context; do not assert surveillance, pollution, a rescue or hostile intent from proximity or a flight shape alone.

## Operational isolation and release checks

A future ten-minute workflow must be short and independent of the twenty-minute AIS reception window. Test writer contention, stale queued starts, retries and shared budgets so aircraft failures or the 144 nominal daily starts cannot block ship products or existing reports. Respect actual scheduled-run delays and report observed cadence. Do not invent observations for missed slots or dispatch a workflow to compensate without owner instruction.

Before collector activation:

1. Prove the source response schema, batched retrieval, freshness and permissions with bounded read-only checks.
2. Resolve/verify individual ICAO24 identities and complete the required registry coverage, documenting remaining gaps.
3. Implement and locally test collection, deduplication, timestamp quality, partial/error/zero handling, immutable publication, fourteen-day expiry and total/version byte caps.
4. Test aircraft-write isolation and current ship/map/SAR regressions. Enforce global storage checks before aircraft promotion without failing existing AIS collection.
5. Add the optional map layer and verify sampled paths, category/source pop-ups and strict search time windows.
6. Put the production workflow and source choice through owner review. No manual dispatch or rerun is included.

## Validation performed for this planning draft

The two JSON documents were parsed, and cadence, budget totals, profile/role/source references, candidate uniqueness and disabled-selection flags were checked in the available JavaScript tool runtime. The repository diff is restricted to this planning directory.

No execution environment was available for Node/Python or browser tests. No collector implementation, live-source proof, scheduled workflow, generated products, map change or retention enforcement is delivered by this draft. No Actions were started or rerun. The existing system stays unchanged.
