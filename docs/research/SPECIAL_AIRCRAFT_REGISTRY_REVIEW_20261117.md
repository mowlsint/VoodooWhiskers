# Special aircraft registry review — 2026-11-17

The owner-approved selection now uses `enabled: true` for suitably evidenced authority, military or specialist maritime aircraft. A single published registration/ICAO24 mapping may suffice when it is consistent with the aircraft/operator context. A second database remains desirable, rather than a mandatory selection gate. Good role suitability does not supply a missing hex code. Callsign/type alone, known identity conflicts and obsolete registrations do not qualify.

The map has one selected-aircraft tier, with an explicit independent-verification notice in every pop-up. It does not display a provisional-assignment label. Selection is a research decision, not official identity verification, proof of live ADS-B reception or current tasking. `collection_enabled` remains false. No Actions workflow, schedule, provider call, secret or permission is added or changed.

## Current inventory

`config/special_aircraft_registry.json` contains **211 records: 105 enabled, 106 excluded**. It retains the 18 reviewed input records and 164 input candidates, adds **29 newly researched individual airframes**, and supplies **12 previously missing ICAO24 mappings** (one remains excluded because its operator assignment conflicts). It uses 260 referenced sources. The 105 selections cover 15 countries and NATO; the full inventory is not a claim of comprehensive international fleet coverage.

| Country / organisation | Enabled |
| --- | ---: |
| Norway | 25 |
| United Kingdom | 16 |
| Germany | 11 |
| NATO | 11 |
| Ireland | 9 |
| Sweden | 6 |
| Netherlands | 5 |
| Estonia | 4 |
| Denmark | 3 |
| Iceland | 4 |
| Lithuania | 3 |
| Finland | 2 |
| France | 2 |
| Latvia | 2 |
| Belgium | 1 |
| Italy | 1 |

The inputs' filenames, record counts and SHA-256 hashes are recorded in `input_provenance`. Original files remain unchanged. Normalization centralizes sources, keeps original record IDs, registration/serial/MSN, identity evidence, source roles, confidence ratings, service status and open questions. The previous two-provider activation note is superseded by this owner's policy; confidence letters are inherited research ratings, not newly issued official verification. NATO is an organisation scope with a null country, not an ISO country code. No airframe is merged using a name or callsign alone.

## New research and decisions

| Group | Individually researched additions / resolved identities | Decision and evidence |
| --- | --- | --- |
| German Navy P-8A | 63+01 → 3ea427; 63+03 → 3f7efd | Enable selection. [Planespotters 63+01](https://www.planespotters.net/hex/3EA427), [63+03](https://www.planespotters.net/hex/3F7EFD), plus the input Navy/manufacturer role sources. Hex facts were readable in indexed provider results; direct pages returned 403 during this review. No claim of operational readiness. |
| German Federal Police | D-HVBA → 3e0a2e; D-HVBC → 3e0a30 | Enable police/border context. Individual FR24 identity pages and the [official flight service](https://bundespolizei.de/die-bundespolizei/einsatzkraefte/bundespolizei-flugdienst), which includes North/Baltic Sea borders. No specific maritime task or equipment assumed. |
| Northern Helicopter H155 | D-HNHB/F/G identities resolved; D-HNHD/E/H/I added | Enable specialist civil offshore rescue/HEMS context. Each hex was read on its own provider page. [Operator H155 datasheet](https://northernhelicopter.de/wp-content/uploads/2025/01/Data-Sheet-H155.pdf) supports fleet/type suitability, not each flight's task, contract or equipment. These are civil aircraft. |
| Norwegian SAR Queen | 0262, 0264, 0265, 0268, 0270, 0273, 0275–0283 | Add 15 individually read [Plane Finder identities](https://planefinder.net/data/airline/NOW/fleet). [Norwegian Armed Forces](https://www.forsvaret.no/aktuelt-og-presse/aktuelt/pa-sok-og-redningsoppdrag-med-nye-sar-queen) supports AW101/330 Squadron SAR context. No extrapolation of missing serials or hex codes. |
| Norwegian Police AW169 | LN-ORA/B/C | Add all three evidenced identities. FR24 pages and [manufacturer fleet/role account](https://helicopters.leonardo.com/documents/16114711/0/AWNL_%2339.pdf?t=1659448833660). General police role; maritime tasking not established. |
| Netherlands Police AW139 | PH-PXY → 484ac1; PH-PXZ → 484ac0 | Enable police context using individual FR24 mappings. [Kustwacht SAR plan, version 2.5](https://kustwacht.nl/download/10040/?tmstv=1705071582) contains inconsistent AW139/AW135 and PH-RXY/PH-PXY labels. Do not use those inconsistencies to assign hoist capability or an individual SAR role. |
| Danish Challenger | C-080 → 45f422; C-168 → 45f424; C-172 → 45f425 | Enable the three previously listed military patrol identities; individual FR24 mappings supplement the input primary role sources. |
| French Customs Beechcraft | F-ZBGK → 3b7b80; F-ZBGL → 3b7b40 | Enable Customs context. Individual provider mappings and the [official Customs fleet/role description](https://www.douane.gouv.fr/fiche/pilote-davion-ou-dhelicoptere-categorie). |
| F-ZBGP | 3b7b21 found | Keep excluded: the input Customs assignment conflicts with the provider's Sécurité Civile label. A known hex does not resolve the operator conflict. |
| Bristow UK AW189 | G-MCGT/U/V | Add individual FR24 identity mappings and [HM Coastguard programme context](https://www.gov.uk/government/collections/uk-second-generation-search-and-rescue-aviation-programme-uksar2g). Individual current contract/readiness remains unproven; a Bristow point alone is not a rescue. |
| Baltic border/police helicopters | Existing Estonian, Latvian and Lithuanian identity mappings | Enable the evidenced agency role without asserting a maritime mission or converting SAR capability/candidate tags to rescue activity. Lithuania's rail/radiation task context remains documented. |
| Icelandic helicopters | TF-EIR, TF-GNA and TF-GRO | Enable agency/fleet context with registration/MSN caveats. The [current Coast Guard SAR page](https://www.lhg.is/en/search-and-rescue/) identifies all three H225s. TF-GRO's missing hex is resolved as 4cc531 on its individual FR24 page. Use the primary H225 identification for TF-GNA, while retaining the provider H215 subtype disagreement. Reused registrations require care. |

Every aircraft's exact source URLs, individual hex evidence and review date are in the registry. Operator/fleet role evidence and a database identity are recorded separately. No tracker history proves that a particular position came from ADS-B rather than MLAT, radar or another method. All `adsb_position_source_verified` values therefore remain false.

## Remaining research

The excluded 106 records comprise 104 without an evidenced usable hex, the replaced G-HMGB alias and the F-ZBGP operator conflict. PH-CGD/PH-CGE also retain explicit registration conflicts. Do not guess these identities. Several German oil-surveillance aircraft, additional French/Naval aircraft, Polish maritime helicopters, Canadian/US aircraft and other national inventories still need individual evidence.

The five NATO RQ-4D airframes remain in research: the fleet source establishes their existence, but individual lookup pages redirected to generic production lists and supplied no usable hex mappings. EMSA/Frontex RPAS and contractor identities likewise remain research targets; neither a generic drone type nor a service contract supplies an individual transponder identity. This PR does not claim drones are being collected.

## Storage and activation limits

The registry is **442,143 bytes (~0.42 MiB)**, below the planned 1 MiB registry/status component, leaving 606,433 bytes within that component. It is static metadata, with no copied histories or raw provider payloads. Do not rewrite it on every poll or copy it into public/download/mirror products. The total aircraft working-set ceiling remains 30 MiB / 14 days; repository guards and ship-product budgets are unchanged.

Watchlist size does not itself determine observation storage. At 105 aircraft, 144 polls/day and 14 days, a hypothetical continuous observation of every aircraft would produce 211,680 samples; the planned 27 MiB history component then permits only about 134 bytes per sample. Actual duty cycles, geography, encoding and copies must be measured before collection is implemented. Registry growth must not silently increase retention, API batches, quota use or repository limits.

Source batching, usage/publication terms, actual positioning fields, geographic gate, writer concurrency and measured daily Git growth remain activation prerequisites. Research pages were used for manual metadata verification, not made into an automated scraping feed. The earlier planning files remain historical design inputs; this registry and review are the current selection inventory. No collector is wired to either inventory yet.

## Offline checks

```sh
python scripts/special_aircraft_registry.py
python -m py_compile scripts/special_aircraft_registry.py tests/test_special_aircraft_registry.py
python -m unittest discover -s tests -p 'test_special_aircraft_registry.py'
node --test tests/test_special_aircraft.cjs tests/test_local_projects.cjs tests/test_vessel_categories.cjs
VOODOO_TEST_DOM_MODULE=/path/to/test-only/node_modules/jsdom node --test tests/test_local_projects_dom.cjs
```

The validator checks source references, public evidence URLs, typed booleans, evidenced/non-conflicting selected hexes, duplicate identities, retained exclusion reasons and bounded metadata. It performs no network or collection work. The budget check is for this static file; a later collector must enforce the combined registry/status and total-position budgets.
