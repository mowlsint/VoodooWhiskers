# AIS, VOI, Sanktionen und historische SAR-Kontexte

Stand: 9. November 2026. Implementiert und lokal geprüft; keine Actions ausgelöst,
keine Zeitpläne aktiviert, kein Merge oder Deployment durchgeführt.

## Bestandsaufnahme und Entscheidung

- Voodoo produziert AIS-/VOI-/Infrastruktur-/SAR-Produkte. Magic Paws konsumiert
  die bestehenden öffentlichen JSON-/GeoJSON-Produkte über seinen Mirror.
- Die AIS-, Regional-, Public- und GFW-Probe-Workflows enthielten nur manuelle
  Trigger. Die letzten gelesenen AIS-Läufe waren aus August. Ein regionaler Lauf
  vom 26. August dauerte etwa 74 Sekunden; das alte AISStream-Aufnahmefenster
  war auf 1.800 Sekunden eingestellt. Kontingent, Rechnung und Abrechnungsminuten
  des Kontos waren über die verfügbaren Schnittstellen nicht feststellbar.
- Bestehende Python-Filter, Broker, VOI-Historie, öffentliche Generatoren und die
  SAR-Projektions-/Matching-Funktionen werden weiterverwendet. Kein Server,
  Docker-Dauerbetrieb oder Überflug-Trigger; Draft PR #3 wird nicht übernommen.
- Die regionalen REST-Snapshots bleiben: vier begrenzte HTTP-Anfragen liefern
  bereits den letzten bekannten Zustand. Zusätzliche MQTT-/Live-Verbindungen
  würden für drei kurze tägliche Fenster wenig bringen und mehr Code benötigen.
- Die historische SAR-Pilotkette ist auf eine feste August-Szene kalibriert.
  Sie bleibt erhalten. Das neue Kontextwerkzeug liest Kandidaten mit ihrer
  tatsächlichen Szenenkennung/Aufnahmezeit und nutzt denselben Matcher.

## AIS-Erfassung

`python scripts/collect_ais.py` sammelt zwei native Open-Waters-WebSockets parallel
mit den vorhandenen regionalen Snapshots in einem Job. `config/openwaters.json`
enthält die vorgegebenen 399/400 Quadratgrad und zunächst 180 Sekunden.
120 bis 300 Sekunden sind zulässig; das Zeitfenster wird nach einem Pilotlauf
anhand von Kontaktzahl, Herkunft, Meldungsrate und Laufzeit angepasst.

Der initiale Snapshot erhält die ursprünglichen Anbieterzeiten. Statische
Meldungen aktualisieren Metadaten, keine Position oder Positionszeit. Open Waters
liefert einen Anbieterzeitstempel: Originalzeit bei höchstens 30 Sekunden
Abweichung, andernfalls Anbieterempfangszeit. Das ist ausdrücklich keine
Garantie einer GPS-genauen Sendezeit. `retrieved_at` bleibt getrennt.

`source_provider=openwaters` und `upstream_source`/`upstream_station` bleiben
erhalten. Zwei gefilterte Positionen pro Schiff und Aufnahmefenster können in
die bestehende begrenzte 21-Tage-Quellhistorie eingehen. Keine Rohframes/NMEA.
Die öffentliche VOI-Historie bleibt bei 14 Tagen.

Die Statistik erfasst Ereignisse, Duplikate, Verbindungsfehler, Empfangsraten,
Ruhezeiten und mögliche Ausdünnung. Vor dem Fenster datierte Ereignisse werden
für die geschätzte Live-Ratenprüfung ausgeschlossen. Nicht jede Ausdünnung ist
sichtbar; ein stiller Stream belegt keine Verkehrsfreiheit. Bei Ausfällen bleiben
vorherige Quelldateien erhalten. Der Broker prüft zusätzlich das tatsächliche
Positionsalter, damit frisch gespeicherte Dateien alte Positionen nicht verjüngen.

Fintraffic verwendet `timestampExternal` (REST), nicht das AIS-Sekundenfeld
`timestamp`. Ungültige Zeiten werden nicht durch Abrufzeiten ersetzt.

## VOI und Sanktionen

Die bestehende `data/watchlist_master.csv` bleibt maßgeblich. Open Waters nutzt
dieselben VOI-, Flag-Risk-, Zielhafen- und Tankerkontextfilter. Der neue
`refresh_sanctions.py --fetch` gleicht EU/DMA, UKSL und OFAC SDN ab, höchstens
einmal täglich. Vorher gültige Quellen bleiben bei Fehlern erhalten. Downloads
werden nicht im Repository archiviert, nur normalisierte Schiffsbenennungen
mit Abrufzeit, Quellenstand und SHA-256.

Am 9. November wurden tatsächlich die amtlichen Dateien heruntergeladen und
lokal verarbeitet: EU/DMA 729 eindeutige IMO, UKSL 682, OFAC 1.555. Das sind
Quellzahlen mit Überschneidungen, keine addierbare Gesamtzahl. Elf Schiffe wurden
ergänzt; die Watchlist umfasst jetzt 2.017 Einträge. Ein bestehender Eintrag,
FU YUAN YU 8683 (IMO 9894387), ist zur manuellen Prüfung markiert. Nichts wurde
gelöscht. 1 UK- und 15 OFAC-Schiffseinträge ohne eindeutig verwertbare gültige IMO
bleiben ungelöst; es erfolgt kein Namensabgleich als Ersatz.

EU-Russland (Annex XLII) und EU/DPRK werden getrennt bezeichnet. Die DMA-Seite
nennt als Quellenstände Juli 2026, UKSL den 3. November. Abrufdatum und
Veröffentlichungsstand werden nicht gleichgesetzt. JP/AU und andere kuratierte
Quellen bleiben erhalten; dieser Importer bestätigt deren Aktualität nicht neu.
Eigene Notizen, Namen, Prioritäten und Shadow-Fleet-/False-Flag-Kennzeichen werden
nicht überschrieben. Eine neue Sanktionsbenennung erzeugt keine automatische
Shadow-Fleet-Einstufung. Fehlende Bestätigung führt zur Prüfung, nicht zur Löschung.

## GFW und SAR

GFW änderte am 3. November 2026 die 4Wings-Berichte: `VESSEL_ID`/`MMSI` mit
`spatial-aggregation=false` werden derzeit abgewiesen. Regionale Schiffsaggregate
mit `spatial-aggregation=true` liefern keine Positionen und ersetzen keine Tracks.
Der bisherige identitätsbezogene Export bleibt erhalten, wird aber nicht mit
anonymen Rasterdaten überschrieben oder fälschlich als aktuell markiert.

`python scripts/fetch_gfw_context.py` lädt manuell begrenzte historische
Präsenzraster und Loitering-/Gap-Ereignisse. Standardtag: fünf Tage zurück.
Raster: HIGH / 0,01 Grad, HOURLY, gruppiert nach FLAG, keine Schiffsidentität.
GFW beschreibt ungefähr 96 Stunden AIS-Verzug und fünf Tage SAR-Verzug; das
ist keine Liefergarantie. Pagination, Fehler und Obergrenzen bleiben sichtbar.
Der unveränderte alte Einstieg `fetch_gfw_common_presence.py` delegiert an den
unterstützten Rastermodus. Der GFW-SAR-Importer verweigert die aktuell unzulässige
identitätsbezogene Rasterabfrage, erhält aber seine SAR-Erfassung.

`python scripts/copernicus_historical_context.py` vergleicht vorhandene
CFAR-Kandidaten mit der gespeicherten Quellhistorie. Er prüft Szenenkennung gegen
Aufnahmezeit und verwendet ein Fenster von ±30 Minuten. Optional kann ein bereits
beschafftes dänisches Tagesarchiv mit `--danish-archive PATH --bbox W S E N`
hinzugegeben werden; Roharchive werden nicht in Git gespeichert. Die bisherigen
Katalog-, Chip-, CFAR- und visuellen Review-Werkzeuge bleiben unverändert nutzbar.

Die Ausgaben `data/sar_historical_context_latest.geojson` und
`data/sar_historical_review_queue_latest.json` bleiben Analystenprodukte. Der neue
manuelle Workflow erstellt daraus ein Artefakt mit drei Tagen Aufbewahrung. Mit
seiner optionalen GFW-Abfrage werden der tatsächliche Aufnahmetag und das Szenen-/
AOI-Footprint verwendet; standardmäßig bleibt diese Abfrage ausgeschaltet. Für
weitere Szenen muss ihr Footprint im Kandidatenprodukt oder per --bbox vorliegen.
Keine öffentliche Freigabe und keine automatische maritime/feindliche Klassifikation.
Ergebniszustände: MATCHED, POSSIBLE_MATCH, AMBIGUOUS,
UNMATCHED_IN_AVAILABLE_AIS, NO_AIS_COVERAGE. Gefilterte Stichproben allein belegen
keine vollständige AIS-Abdeckung. GFW-Präsenz erhöht keine Match-Gewissheit;
GFW-Ereignisse bleiben separater Kontext. GFW-SAR aus derselben Sentinel-1-Szene
zählt nicht als unabhängige Bestätigung.

## Lokal geprüfter Stand

- Python-Syntax und Workflow-YAML/JSON-Konfigurationen.
- 64 Pytest-Fälle inklusive vorhandener SAR-/CFAR-/Review-Regressionen und neuer
  Zeitstempel-, Deduplikations-, Quellen-, Fehler-, Matching- und Sanktionsprüfungen.
- Offline-Gesamtdurchlauf mit nativen AIS-Testereignissen: Broker, Quellarchiv,
  VOI-Layer, 14-Tage-Retention, öffentliche Generatoren, Infrastruktur und Manifeste.
  Zwölf erzeugte Downloadprodukte waren vorhanden, parsebar und größenkonsistent;
  der Magic-Verbraucher übernahm dieselben Beobachtungszeiten und Herkunftsfelder.
- Autoritative Sanktionsdateien tatsächlich abgerufen und geparst.
- Kein Open-Waters-/BarentsWatch-/GFW-/Copernicus-Live-Integrationstest. Der Nutzer
  bestätigt `OPENWATERS_TOKEN` als eingerichtet; der Secret-Wert wurde nicht gelesen.
  Laufzeit hier: Python 3.12; Workflows verwenden weiterhin 3.11.

## Verbrauch und Inbetriebnahme

Nach Freigabe vorgesehener UTC-Cron: `0 4,12,20 * * *`. Er ist in diesem PR nicht
aktiv. Bei 180 Sekunden paralleler Aufnahme werden etwa 4–7 Linux-Runner-Minuten
pro Voodoo-Lauf erwartet: 12–21 pro Tag bzw. 360–630 je 30 Tage. Einschließlich
Magic-Mirror/Verarbeitung (etwa 1–3 Minuten pro Lauf): insgesamt ungefähr 15–30
Minuten täglich, 450–900 monatlich. Dies sind Schätzungen, keine Messung oder
Abrechnung. Es entstehen keine doppelten 180-Sekunden-Fenster für die zwei Streams.
Sanktionsabrufe laufen dank 24-Stunden-Cache höchstens einmal täglich.
Manuelle GFW- und SAR-Sonderläufe sind zusätzlich; kein GFW-Backfill bei jedem AIS-Lauf.

1. PRs prüfen, Voodoo zuerst, Magic anschließend zusammenführen.
2. Secret-Namen prüfen: OPENWATERS_TOKEN, BARENTSWATCH_CLIENT_ID,
   BARENTSWATCH_CLIENT_SECRET; GFW_TOKEN nur für gesonderte historische Abfragen.
3. Einen ausdrücklich freigegebenen manuellen Voodoo-Pilotlauf auf main ausführen;
   beide Streamstatus, Raten, Beobachtungsalter und Quellen prüfen.
4. Den vorhandenen Magic-Mirrorlauf freigeben; AIS-Übernahme, gültige Nullergebnisse,
   veraltete Daten und AUX/FULL-Anzeigen prüfen. Keine separate AIS-Sammlung dort.
5. Erst danach die gewünschten drei Zeitpläne ausdrücklich aktivieren. GFW-/SAR-
   Workflows bleiben manuell; Katalog-/Chip-/CFAR-Piloten sind weiterhin begrenzt.

Restrisiko: Drei kurze Stichproben täglich bilden keine durchgehende Trackhistorie.
Für viele Satellitenzeiten werden trotz fünf Tagen Wartezeit keine exakten AIS-
Beobachtungen vorliegen. Ein Rasterbericht beseitigt diese Lücke nicht.

## Offizielle technische Quellen

- https://openwaters.io/api/ais/
- https://www.digitraffic.fi/en/marine-traffic/
- https://developer.barentswatch.no/docs/AIS/live-ais-api/
- https://developer.barentswatch.no/docs/AIS/examples/
- https://globalfishingwatch.org/our-apis/documentation/docs/release-notes
- https://globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/report
- https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/key-concepts
- https://globalfishingwatch.org/our-apis/documentation/docs/v3/events/get-all-events
- https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits
- https://www.gov.uk/government/publications/the-uk-sanctions-list
- https://www.gov.uk/guidance/format-guide-for-the-uk-sanctions-list
- https://ofac.treasury.gov/sanctions-list-service
- https://www.dma.dk/growth-and-framework-conditions/maritime-sanctions/general-information/eu-vessel-designations

GFW bleibt CC BY-NC 4.0 mit Zuschreibung „Powered by Global Fishing Watch.“;
Open-Waters-Personal-Nutzung folgt der vom Nutzer bestätigten nichtkommerziellen
Freigabe. Diese Quellen dürfen nicht stillschweigend als kommerziell freigegeben
behandelt werden.
