# Vessel category display and Russia voyage indications

The dashboard colours AIS vessel points by category rather than delivery provider. This is a presentation rule, not a severity score or a new analytical product. Generated AIS/VOI fields, shared schemas, Magic Paws inputs, collection schedules, retention and repository size guards remain unchanged.

## Colours and overlapping categories

The first applicable entry supplies the point fill; the next supplies one surrounding ring. Every applicable category is listed in the pop-up, including categories that do not fit on the marker. A single category has a matching outline. Historical points use the same colours, with lower opacity. Track lines use the primary category of the segment's later observation. Neutral/background traffic stays grey.

| Display order | Category | Colour |
| --- | --- | --- |
| 1 | Explicit current sanctions flag | Red `#ef5959` |
| 2 | Explicit shadow-fleet flag/list context | Violet `#ac83ff` |
| 3 | False-flag watch, stronger evidence | Pink `#ef76c8` |
| 4 | False-flag review lead | Pale pink `#d5a4cf` |
| 5 | Behavioural VOI | Turquoise `#29c8b9` |
| 6 | Other VOI/watchlist | Blue `#62a6ff` |
| 7 | Combined sanctions/shadow-fleet entry or historical sanctions entry needing review | Gold `#d7c879` |
| 8 | Russia-related voyage | Orange `#ffa84d` |
| 9 | Russian MMSI context | Grey `#a5b2bd` |
| 10 | Neutral tanker / other monitored vessel | Grey `#8d9ca8` |

A combined `sanctions_shadowfleet` category alone does not establish which status applies. Stronger explicit fields take precedence. Russian MMSI alone supplies identifier context, never voyage direction or shadow-fleet status. Existing unrecognised categories remain readable in the pop-up. The collapsed delivery-source controls still filter observations; provider colours and provider-colour legends are removed from AIS points. Sources and timestamps remain in the pop-ups.

## Direction and evidence

Both directions use the same orange voyage category:

- `→ Likely bound for Russia`: the current non-empty AIS destination explicitly identifies a Russian port, including the destination side of a two-ended route.
- `← Likely coming from Russia`: a Russian departure side or a supplied previous-port indication.
- `Russia-related — direction unclear`: retained or ambiguous evidence without an adequate current direction.

`RU ULU>NL RTM` supplies a Russian departure indication; `NL RTM>RU ULU` supplies a Russian destination indication. Both arrows can appear for a Russia-to-Russia declaration. Multi-leg/unclear strings remain unclear. Explicit FROM/TO text and the curated Russian port names/codes are supported. Negated link disclaimers are not voyage evidence. The client port aliases are taken from `data/ports_ru_enhanced_aliases.csv`; ambiguous three-letter aliases are omitted. Update the client reference and its tests when curating additional ports.

An earlier Russian destination followed by a non-Russian destination is retained context, **not proof that the vessel visited or departed Russia**. The dashboard never derives a direction from an older destination or a persisted `to_russia_declared` flag when the current destination is empty. Previous declarations remain visible as dated evidence and, where appropriate, direction-unclear context. Russian route-state continuity uses exact IMO, or exact MMSI when IMO is unavailable; names and callsigns never join route histories. Legacy state entries with multiple conflicting IMO/MMSI identities are discarded during migration. Producer field names remain unchanged for downstream compatibility.

Evidence includes the original voyage text, source and accompanying observation time, or an explicitly supplied previous-port date. An observation timestamp need not be the time of a fresh static voyage-text transmission. A position or declaration is not an independently verified port visit. No percentages, geofencing, GPS-based port confirmation or new visit detection are introduced.

## History and operational limits

The client uses at most ten days of available preceding observations relative to each displayed point for evidence and context. Future observations never backfill the classification of an older point. Historical destinations can support Russia-related context, but only the point's current destination can set a direction arrow. History joins use IMO/MMSI with conflict protection; matching names or callsigns alone never join vessels.

The existing bounded `voi_history_14d.jsonl` is loaded once in the background after the initial map appears and reused for tracks/time windows. This adds one existing public-file download on initial page load (currently capped at 22 MiB), not an extra provider request or workflow. Until it arrives, colours use loaded records. On failure, current records remain visible and a status message explains that history is unavailable. This must not be read as no earlier Russia reference. Available history is sampled and may have gaps; a fourteen-day retention ceiling does not guarantee fourteen complete days for every vessel. The ten-day display lookback does not change retention.

Local projects preserve safe category flags, voyage text and source/basis fields in their existing local V2 workflow-context observations. Files remain local and can reproduce saved context after upstream expiry. Position conflicts retain separate alternatives until the analyst chooses; dashed outlines mark unresolved alternatives. Historical project mode continues to hide live AIS; 24/48-hour search windows exclude older positions. SAR comparison continues to hide current AIS/local overlays. Undated imported route lines can retain project-identification colours and are not treated as timed vessel positions.

## Validation

Use Node 24. Core and voyage-category tests need no added dependency:

```sh
node --test tests/test_local_projects.cjs tests/test_vessel_categories.cjs
```

The optional DOM integration tests use the existing test-only `jsdom` setup (not a dashboard dependency):

```sh
VOODOO_TEST_DOM_MODULE=/path/to/test-only/node_modules/jsdom node --test tests/test_local_projects_dom.cjs
```

They exercise actual Leaflet marker options/pop-ups, provider-independent colours, all categories, direction arrows, safe text insertion, search windows, project overlays/offline context and SAR isolation. Browser pixel/layout review remains a separate check.

## Primary AIS references

- [IMO SN/Circ.244: AIS destination-field UN/LOCODE guidance](https://www.navcen.uscg.gov/sites/default/files/pdf/NAIS/IMO_SN_Circ244_AIS_UNLOCODE.pdf)
- [USCG AIS encoding guide](https://navcen.uscg.gov/sites/default/files/pdf/AIS/AISGuide.pdf)
- [IMO A.1106(29): operational use of AIS](https://www.navcen.uscg.gov/sites/default/files/pdf/ais/references/IMO_A1106_29_Revised_guidelines.pdf)
