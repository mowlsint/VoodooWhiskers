# Optionaler Verkehrskontext und Abdeckungshinweis

## Bedienung

Direkt auf der Karte erscheint bei jedem Seitenaufruf ein Hinweis: **„Aufgrund begrenzter Datenkapazitäten und Empfangsabdeckung wird vermutlich nicht der gesamte Schiffsverkehr angezeigt.“** Er lässt sich mit dem beschrifteten Schließen-Knopf entfernen und über **Abdeckungshinweis** wieder öffnen. Der Hinweis blockiert weder Karte noch Seitenleiste und speichert keine dauerhafte Zustimmung.

**Verkehrskontext anzeigen** lädt einen eigenen kleinen Datensatz erst bei Bedarf. Die Punkte sind kleiner und dezenter als VOIs. Sie verwenden die vorhandenen Quellenfarben; Positionen älter als sechs Stunden werden blasser, älter als 24 Stunden gar nicht angezeigt. Ein Klick zeigt MMSI, belegte IMO, Herkunft einschließlich bekanntem Open-Waters-Zulieferer, echte Beobachtungszeit und VesselFinder in einem neuen Tab. In der Übersicht werden Kontakte innerhalb eines Bildschirmrasters von 28 Pixeln je Quelle zusammengefasst. Ein Klick auf eine Gruppe zoomt hinein; ab Zoomstufe acht erscheinen Einzelkontakte. Alle dargestellten Kontakte sind nur eine empfangene Auswahl.

Die bestehenden Quellenfilter und die Suche nach MMSI/IMO gelten auch für den Hintergrund. Alle bereits überwachten Identitäten einschließlich neutralem Tankerkontext werden über IMO/MMSI aus dem Hintergrund entfernt, unabhängig von Suche und Quellenwahl. Sie erscheinen nicht doppelt. Hintergrundkontakte gelangen nicht in die Trackauswahl. **Ruhige Übersicht** schaltet den Kontext aus. Historische Zeitansichten und der historische SAR-Vergleich blenden ihn aus, damit keine aktuellen Positionen unter eine alte Szene gelegt werden.

## Erfassung und Grenzen

Die bereits erforderlichen Open-Waters-, Fintraffic- und BarentsWatch-Abrufe speisen vor der VOI-Filterung eine getrennte kompakte Ableitung. Es entstehen keine zusätzlichen Provideranfragen, Verbindungen, Workflows, Zeitpläne oder Abhängigkeiten. GFW und historisches dänisches AIS werden nicht als aktueller Verkehr beigemischt.

`public/data/vessels/traffic_context_latest.json` ist das einzige neue Positionsprodukt: höchstens **10.000 Kontakte und 1 MiB einschließlich Metadaten**, eine letzte gültige Beobachtung je belastbarer IMO/MMSI-Identität, ohne Rohtelegramme, Namen, Popup-Texte oder verschachtelte Positionen. Die spaltenorientierte Kodierung vermeidet wiederholte Feldnamen. Quelle, Zeitbasis und gültige Beobachtungszeit bleiben ausdrücklich erhalten. Die Vorstufe begrenzt außerdem jede Quelle auf 10.000 Identitäten. Bei Überhang verteilt eine deterministische Auswahl die Kapazität auf Quellen und räumliche Halbgradzellen; sie beansprucht keine Repräsentativität. Anzahl, Auslassungen, Quellgrenzen und fehlende/teilweise Quellen werden ausgewiesen. Ungültige oder über 24 Stunden alte Positionen werden verworfen.

Die Kontextdatei wird pro Lauf ersetzt, ohne Kontext-Historie, zusätzlichen Download-Klon oder neue Trackarchive. Bei Quellausfällen gibt es keinen stillschweigenden Rückgriff auf alte Hintergrunddaten: fehlende Quellen werden genannt. Ein fehlendes, abgelaufenes oder ungültiges Produkt ist kein Nullergebnis. Das Dateialter und die tatsächlichen Beobachtungszeiten werden getrennt behandelt; der Browser prüft beide. Eine abgelaufene Datei bleibt möglicherweise auf dem Host, wird aber nicht mehr als Verkehr angezeigt.

**Ersetzen ist keine Git-Bereinigung:** Ältere Versionen bleiben in der Git-Historie. Der bestehende Repositorywächter zählt auch dieses Produkt zum aktiven Schiffsbudget, prüft neue Blobdaten und das 750-MiB-Repositorybudget und stoppt vor einem zu großen Daten-Commit. Zusätzlich verwirft er eine Kontextdatei über 1 MiB. Bei sechs maximal großen Dateien täglich sind bis zu 6 MiB neue unkomprimierte Blobdaten pro Tag möglich; das ist eine Obergrenze ohne Zusage zur Git-Kompression. Bei einem später erreichten Budget ist eine ausdrückliche Speicherentscheidung erforderlich. Es gibt keinen automatischen Force-Push oder das Löschen alter Commits.

Die bestehenden AIS-/VOI-Produkte, deren 14-Tage-Historie, SAR-Analyse und Schattenbewertung bleiben unverändert. Der neue Manifestverweis ist optional und erscheint nur bei vorhandener Kontextdatei. Magic Paws erhält keine zweite Sammlung und keine geänderten bestehenden Pfade oder Datenformate; die neue Anzeige liegt zunächst auf der Voodoo-Karte.

## Aktivierung und Prüfung

Quellcodeänderung als PR; keine Action manuell gestartet, kein zusätzlicher Zeitplan aktiviert und kein Deployment durchgeführt. Nach dem Merge baut der nächste bereits geplante AIS-Lauf den Kontext. Bis dahin erklärt die Oberfläche ein fehlendes Produkt ausdrücklich.

Offline prüfen: kompakte Feldfreigabe, Herkunft/Zeiten, Identitätsabgleich, VOI-Ausschluss, Quellenfehler, Frische, Zahl- und Bytegrenzen, Quellenverteilung, normale Produzentenausgaben und Manifest. Browserprüfung mit synthetischen Daten: Schließen/Wiederöffnen, Ein-/Ausblenden, Gruppen/Einzelpunkte, Quellenfilter, historische Sperre, Popup/VesselFinder und schmale Bildschirmansicht.
