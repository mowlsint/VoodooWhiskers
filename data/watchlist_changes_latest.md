# VOODOO WHISKERS Watchlist Audit

Generated: 2026-10-06T20:14:42+00:00

Rows: 1985
Unique keys: 1985
Sanctions rows: 1984
Shadow-fleet rows: 682
Duplicates: 0
Missing identity rows: 0
Test/example rows: 0

No rows were deleted automatically.

## Aktualisierung vom 6. Oktober 2026

- 682 bestehende Einträge erhalten, 1.303 direkte Schiffseinträge ergänzt: 1.985 eindeutige Einträge insgesamt. 1.984 Schiffe sind in mindestens einer der geprüften aktuellen amtlichen Listen enthalten.
- `track_behavior` und `track_russian_mmsi` entfernt. Frühere allgemeine Verhaltensbewertungen (AIS-Abschaltung, STS, dark activity) aus den Beobachtungsnotizen entfernt. MMSI als Identifikator bleibt zulässig.
- Bestehende Schattenflotten-Markierungen bleiben erhalten; neue Sanktionsschiffe werden nicht automatisch als Schattenflotte oder False-Flag-Fall eingeordnet.
- `source_status` und `source_last_checked` ergänzen den Quellenstand. Der vorhandene Generator unterstützt diese optionalen Felder bereits.
- UK-Korrekturen vom 2. Oktober übernommen: GALLE ENERGY (9344411) RUS3751 und BEBEK-E (7808401) RUS3752.
- ZANGAZUR (9420617): frühere UK-Kennzeichnung RUS2579 ist in der aktuellen UK-Datei nicht enthalten; in den übrigen geprüften direkten Schiffslisten ebenfalls keine Bestätigung. Daher `manual_watch_only`, `track_sanctions=false`; keine Behauptung einer weltweit bestätigten Entlistung. MILLEROVO bleibt über die aktuelle australische Liste erfasst.

## Quellenabdeckung

Gezählt werden eindeutige Schiffe je Quelle, mit Überschneidungen zwischen den Quellen:

| Quelle | Schiffe | Stand |
| --- | ---: | --- |
| EU Russland, Annex XLII | 671 | DMA-Veröffentlichung 24.07.2026 |
| EU DPRK-Schiffssanktionen | 58 | Aktuelle DMA-Tabelle abgerufen 06.10.2026 |
| UK Russland | 633 | Amtliche CSV Report Date 06.10.2026 |
| UK DPRK / Libyen | 37 / 1 | Dieselbe aktuelle CSV |
| OFAC SDN, Typ vessel | 1.539 | Amtliche CSV plus SDN_COMMENTS abgerufen 06.10.2026 |
| UN 1718, DPRK | 59 | Aktuell verlinktes amtliches PDF abgerufen 06.10.2026 |
| Australien Russland | 298 | DFAT-Datei, Webstand 03.10.2026, Tabellenblatt 02.10.2026 |
| Australien DPRK / Libyen | 59 / 1 | Dieselbe aktuelle DFAT-Datei |

Australien ist damit enthalten. Die australische DPRK-Schiffsklasse (Referenz 8599) ist eine allgemeine Regel und kein identifizierbares Einzelschiff; sie wird nicht als Schiff in die CSV aufgenommen. Maritime Restriktionen und gezielte Finanzsanktionen bleiben in den Notizen getrennt. Andere nationale Sanktionslisten und nur aus Eigentumsverhältnissen abgeleitete Sanktionen sind nicht Bestandteil dieses Abgleichs.

## False-Flag-Prüfung

| Schiff / IMO | Ergebnis | Begründung und Quelle |
| --- | --- | --- |
| AMELL / 9257993 | Beobachtungskandidat erhalten | GUR meldet falsche Gambia-Flaggendaten, MMSI 629009452, Rufzeichen C5J464; Quelle aktualisiert 05.06.2026. Keine unabhängige Bestätigung des aktuellen Registerstatus. https://war-sanctions.gur.gov.ua/en/transport/ships/705 |
| M SOPHIA / 9289477 | Historischen Hinweis erhalten | GUR nennt falsche Panama-Flagge / JM SOPHIA und Staatenlosigkeit bei Festsetzung am 07.01.2026; Quelle aktualisiert 14.01.2026. Aktueller Registerstatus erneut zu prüfen. https://war-sanctions.gur.gov.ua/en/transport/ships/695 |
| TOPAZ / 9292034 | Markierung entfernt | Quelle nennt russische Flagge; keine konkrete falsche Registrierung belegt. https://war-sanctions.gur.gov.ua/en/transport/ships/742 |
| TANGO / 9292058 | Markierung entfernt | Quelle nennt russische Flagge; keine konkrete falsche Registrierung belegt. https://war-sanctions.gur.gov.ua/en/transport/ships/740 |
| SIRIUS 1 / 9285847 | Markierung entfernt | Kein spezifischer Beleg gefunden; Flaggenwechsel und AIS-Hinweise allein reichen nicht. https://war-sanctions.gur.gov.ua/en/transport/ships/734 |
| LAUREN II / 9258521 | Markierung entfernt | Kein spezifischer Beleg gefunden; STS und Schattenflottenzuordnung allein reichen nicht. https://war-sanctions.gur.gov.ua/en/transport/ships/694 |

Die zwei verbliebenen Markierungen sind Prüfhinweise, keine aktuelle amtliche Bestätigung einer falschen Flagge. Geprüft wurden die sechs zuvor markierten Schiffe; dies ist keine vollständige globale False-Flag-Liste.

## Identität und Validierung

Abgleich primär nach IMO, bei fehlender IMO nach MMSI oder Rufzeichen; kein automatischer Namensabgleich. MIN NING DE YOU 078 wurde über seine übereinstimmende UN-Designation vom 30.03.2018 manuell zwischen UK DPR0120, UN 1718 und Australien DFAT 8858 zugeordnet. Dieses Schiff und BONU 5 (OFAC 23424) haben in den Quellen keinen IMO/MMSI/Rufzeichen-Identifier: Sie bleiben manuell prüfbar und erzeugen keine reinen AIS-Namenstreffer. Historische MMSIs/Rufzeichen aus Sanktionsnotizen werden bei vorhandener IMO nicht als aktuelle AIS-Identifier übernommen.

CSV-Roundtrip, eindeutige Identitätsschlüssel, IMO-Prüfziffern, vollständige Abdeckung der ausgewählten direkten Schiffseinträge, UK-Oktoberkorrekturen und Laden/Matching mit dem bestehenden `build_layers.py` erfolgreich geprüft. Fehlende entfernte Trackingfelder werden als `false` gelesen. Quellen-URLs, Abrufdatum und SHA-256 der eingelesenen Downloads stehen in `watchlist_audit.json`; Rohdownloads werden nicht eingecheckt.

Nur zentrale Watchlist und Prüfdateien geändert. Generierte AIS-/VOI-Produkte und Magic-Paws-Spiegel bleiben auf ihrem bisherigen Stand; keine Workflows gestartet, aktiviert oder umgeplant.
