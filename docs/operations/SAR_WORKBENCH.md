# Geschützter SAR-Arbeitsplatz

Stand: 11. November 2026. Einstieg: `public/sar.html`, verlinkt von der Voodoo-Startseite. Die Oberfläche ist Englisch.

Der Arbeitsplatz verarbeitet einen kleinen Ausschnitt einer vorhandenen Sentinel-1-Aufnahme, sucht Radarkandidaten mit drei CFAR-Profilen und vergleicht sie mit zeitlich passenden historischen AIS-Beobachtungen. Vorschauen, Kandidaten und Auftragsdetails werden verschlüsselt als GitHub-Artefakt gespeichert. Ungeprüfte Kandidaten werden nicht in Magic Paws veröffentlicht.

## Einrichtung

1. Frontend, Konfiguration und `.github/workflows/sar-workbench.yml` gemeinsam auf `main` übernehmen und über den vorhandenen Pages-Weg veröffentlichen.
2. Repository-Secret `SAR_WORKBENCH_PASSWORD` setzen: 12–128 druckbare ASCII-Zeichen, vorzugsweise ein zufälliges Passwort. Im Arbeitsplatz dasselbe Passwort verwenden. Es schützt die Verschlüsselung und erteilt keine GitHub-Ausführungsrechte. Ohne geladene verschlüsselte Ergebnisse kann es erst beim ersten verarbeiteten Auftrag geprüft werden.
3. Für Radarverarbeitung werden `CDSE_SH_CLIENT_ID` und `CDSE_SH_CLIENT_SECRET` benötigt. Optional liefert `GFW_TOKEN` verzögerten Kontext. Es wird kein zusätzlicher AIS-Stream gestartet.
4. Optionaler Direktstart: Im GitHub-Inhaberkonto `mowlsint` einen kurz gültigen **Fine-grained personal access token** erstellen, ausschließlich für `VoodooWhiskers`, mit **Actions: Read and write**. Keine Code-Schreibrechte erforderlich. Im geöffneten Arbeitsplatz unter **Connect GitHub for direct start & latest results** eingeben und **Connect for this tab** wählen. Der Token wird ausschließlich an `api.github.com` gesendet; weder Browser-Speicher noch Backups oder Repository enthalten ihn. **Lock**, Neuladen oder **Disconnect GitHub** entfernen die Verbindung. Der manuelle Weg benötigt keinen Browser-Token.

Der Workflow bleibt ausschließlich manuell und auf `main` sowie den Repository-Inhaber beschränkt. Die optionale `request_id` dient dazu, den zugehörigen Lauf eindeutig wiederzufinden. Zeitpläne, Workflow-Rechte, Secrets und Benachrichtigungsziele werden nicht geändert.

## Bedienung

1. **Gebiet wählen:** 38 kleine Gebietsvorlagen für Nord-/Ostsee, Meerengen, britische und norwegische Gewässer, Nordatlantik und Arktis. Es sind Orientierungsgebiete, keine flächendeckende Überwachung der genannten Meeresräume. **Choose a 20 × 20 km area on the map** erlaubt einen beliebigen Mittelpunkt innerhalb 25° W–35° E und 48–80° N. Alternativ **Choose a custom area** und vier Koordinaten eingeben. **Escape** beendet den Karten-Auswahlmodus. Für ein anderes Gebiet den geladenen Loitering-Zeitraum zunächst verlassen.
2. **Find acquisitions:** lädt nur öffentliche Katalogmetadaten mit UTC-Aufnahmezeiten. Eine Aufnahme muss das gesamte Gebiet abdecken. Bei einem GFW-Ereignis gilt dessen Zeitraum. Die Suche kann kein vergangenes Satellitenbild erzeugen oder Überdeckung garantieren.
3. **Analyze this acquisition:** bereitet einen verschlüsselten Auftrag vor und zeigt Gebiet und Verarbeitungstyp an. Es wird noch nichts gestartet. Mit GitHub-Verbindung startet **Start workflow** diesen Auftrag und zeigt den Laufstatus. Alternativ **Manual start without a browser token** öffnen: Code kopieren, **Open GitHub**, **Run workflow**, Branch `main`, Code in `encrypted_request` einfügen und dort den grünen **Run workflow**-Knopf drücken. `request_id` darf beim manuellen Start leer bleiben. Erzeugen oder Kopieren eines Codes startet keinen Workflow.
4. **Ergebnisse laden:** Nach Abschluss **Download encrypted results ZIP** wählen und die heruntergeladene ZIP über **Open results ZIP** oder **Open backup** öffnen. Ergebnisse werden nicht automatisch aus GitHub-Artefakten übertragen. Für bestehende Läufe: Mit GitHub-Verbindung **Latest results** wählen, sonst **Open GitHub results**. Der Arbeitsplatz kann leer aussehen, obwohl ein erfolgreicher Lauf existiert; zunächst dessen ZIP laden.
5. **Prüfen und sichern:** Vorschau, Detailausschnitt, Profilübereinstimmung, VH-Bestätigung, Zeitbezug und AIS-Unsicherheit gemeinsam bewerten. Ein heller Rückstreuer ist ein Kandidat, kein bestätigtes Schiff. Fehlendes AIS belegt keine absichtliche Abschaltung. Notizen bleiben zunächst im Tab. **Save encrypted session** sichert sie mit den Ergebnissen. **Download GeoJSON** erzeugt bewusst eine entschlüsselte lokale Datei.
6. **Recheck AIS context:** verwendet gespeicherte Kandidaten ohne neues Radarbild. GFW ist bei frischen Aufnahmen häufig erst nach etwa fünf Tagen verfügbar.

## Grenzen und Datenschutz

- Jede Verarbeitung bleibt auf 500 km² und 2.500 Pixel pro Seite begrenzt, mit mindestens 1 km Seitenlänge. Kartenauswahl verwendet ungefähr 20 × 20 km.
- Unverändert höchstens drei neue Bildanalysen pro UTC-Tag, sechs gespeicherte Aufträge und 14 Tage Aufbewahrung. Mehr Gebietsvorlagen starten keine zusätzlichen Abrufe und vergrößern das Datenbudget nicht.
- Ergebnisse bleiben außerhalb der Git-Historie; Rohbilder sind temporär. Die Artefaktverwaltung hält das bestehende Speicherbudget ein.
- Passwort und optionaler GitHub-Token werden nicht in Local Storage, Session Storage, Cookies oder Sicherungen gespeichert. **Lock** entfernt außerdem geladene Ergebnisse und ungesicherte Notizen. Eine bereits gestartete Action läuft nach dem Sperren weiter.
- Bei unklarer Antwort auf den Direktstart zuerst den Workflow auf GitHub prüfen; kein automatischer Wiederholungsstart. Statusabfragen sind lesend und enden nach höchstens etwa 20 Minuten.
- Der Schutz setzt vertrauenswürdigen ausgelieferten Code und Browser voraus. Er schützt keine zuvor veröffentlichten Daten rückwirkend.

## Kartenstart und GFW-Kontext

Die Hauptkarte zeigt standardmäßig priorisierte VOI einschließlich Schattenflotte und Listenschiffen. Neutraler Tankerkontext, sonstiger empfangener Verkehr, GFW-Ringe/-Raster und Infrastrukturereignisse sind Zusatzebenen. **Clear overview** stellt diesen Ausgangszustand wieder her. Neutrale Tracks liegen ausschließlich in der optionalen neutralen Ebene.

GFW-Kästen sind historische AIS-Präsenzzellen mit 0,01° Kantenlänge, keine einzelnen Schiffe oder SAR-Bilder. Ihr Popup zeigt den Berichtszeitraum. Sie dienen dem Verkehrskontext. Für einen schiffsbezogenen SAR-Abgleich stattdessen **Add loitering events** einschalten, ein Ereignis öffnen und **Search SAR acquisitions for this period** wählen. Identität, Zeitraum und Dauer werden nur angezeigt, soweit die Quelle sie liefert.

## Verschlüsselung und Passwortwechsel

- Weil jeder den Chiffretext herunterladen kann, sind Offline-Passwortversuche möglich. Ein bloßes HTML-Passwort oder eine kurze PIN wäre dafür unzureichend. Passwortverlust bedeutet Verlust des Zugriffs. Passwortwechsel erfordert vorheriges Entschlüsseln und erneutes Verschlüsseln der aktuellen Ablage. Alte Git-Versionen bleiben mit dem alten Passwort lesbar; allein ein Secret-Wechsel schützt die Vergangenheit nicht neu. Ein unangekündigter Secret-Wechsel führt bewusst zum Abbruch statt zum Überschreiben bestehender Ergebnisse.

## Verarbeitung, Grenzen und Betrieb

Pro Analyse wird die gewählte Szene erneut im authentifizierten Sentinel-Hub-Katalog bestätigt. Mehrere Szenen im engen Zeitfenster werden abgelehnt. Genau ein Process-API-Aufruf lädt einen kleinen Float32-Ausschnitt mit VV, VH und Datenmaske. Form, Georeferenz, Datenqualität, Landmaske einschließlich eines 1-km-Küstenpuffers sowie Kandidatenzahl werden geprüft. Land außerhalb des Bildrandes wird für den Küstenpuffer berücksichtigt. Die drei bestehenden CA-CFAR-Profile und die historischen Zuordnungsfunktionen werden wiederverwendet. Radar-Rohdaten bleiben temporär; nur abgeleitete Vorschau und Detailausschnitte gelangen in die verschlüsselte Ablage.

Die globale Natural-Earth-Landmaske ist per SHA256 festgelegt. Sie ist kartografisch generalisiert: kleine Objekte, Hafenbauten und neue Offshore-Anlagen können fehlen. Die Orientierungskarte ersetzt weder die Analysemaske noch eine Seekarte. Neue Kandidaten bleiben ungeprüft und für automatische nachgelagerte Wertungen gesperrt. Die Methode ist ein experimenteller Analystenarbeitsplatz; Radarqualität, Detektionsparameter und Laufzeit müssen für das jeweilige Gebiet geprüft werden.

Maximal drei neue Bildanalysen pro UTC-Tag werden im verschlüsselten Arbeitsverlauf gezählt. Ein bereits begonnener, später fehlgeschlagener Bildversuch zählt konservativ mit. Katalogsuche und Kontextnachprüfung benötigen keine neue Radar-Verarbeitung. Der lokale Zähler begrenzt weder andere Workflows noch das gesamte Copernicus-Konto; ein Runner-Abbruch vor dem Speichern kann den Zähler verlieren. Er ist ein Betriebswächter, keine kontoweite harte Abrechnungsgrenze. Ein maximaler Ausschnitt liegt nach bestehender Schätzung bei rund 64 Processing Units, die Kadetrinne-Vorlage bei rund 40; maßgeblich sind Anbieterabrechnung und tatsächliches Kontingent.

Die aktuelle Datei enthält höchstens sechs Aufträge aus 14 Tagen und maximal 16 MiB Klartext vor Verschlüsselung. Etwaige frühere verschlüsselte Git-Commits bleiben Teil der öffentlichen Repository-Historie; neue Arbeitsstände werden dort nicht hinzugefügt. Der Workflow hat 20 Minuten Zeitlimit und läuft serialisiert. Er lädt nur den verschlüsselten Arbeitsstand als befristetes Actions-Artefakt hoch; keine Rohdaten oder entschlüsselten Artefakte werden veröffentlicht. Ohne Auftrag läuft keine neue SAR-Action. Bestehende Kalibrierungsworkflows und AIS-Zeitpläne werden durch diese Änderung nicht aktiviert.

## Wartung und lokale Prüfung

`requirements-sar.txt` verwendet die bisherigen Radar-Abhängigkeiten. Zusätzlich liest `pyshp` die globale Küsten-/Landmaske; die Standardbibliothek kann das Shapefile-Format nicht verlässlich auswerten. `cryptography` stellt interoperables authentifiziertes AES-GCM bereit; eigene Kryptografie wird vermieden. Leaflet 1.9.4 liegt mit BSD-Lizenz lokal bei. Es werden keine externen Skripte oder Schriftarten geladen.

`python scripts/sar_workbench.py --export-config` erzeugt die öffentliche Gebietskonfiguration. `tools/build_sar_basemap.py PFAD_ZUM_GEPINNTEN_NATURAL_EARTH_ZIP` erzeugt die lokale Übersicht. Nur dieser Entwickler-Bauschritt benötigt zusätzlich Shapely 2; der produktive Workflow benötigt es nicht. Natural Earth stellt seine Kartendaten gemeinfrei bereit.

## Prüfung der Bedienungsänderung vom 11. November

Lokale Python-Tests einschließlich Web-Crypto-Interoperabilität und synthetischer Radarverarbeitung; zusätzliche Prüfungen für alle 38 Gebietsvorlagen, Servergrenzen und den GitHub-Client mit simulierten API-Antworten. DOM-Funktionsprüfung mit echtem Leaflet: Startansicht, Kontextumschaltung, neutrale Tracks, Rücksetzen, Gebietswahl, Katalogauswahl, Direktstart, passender Ergebnislink, verschlüsselter Ergebnisimport und Sperren. Die DOM-Prüfung ersetzt keine visuelle Browserabnahme. Es wurden dabei keine Actions oder authentifizierten Radarabrufe gestartet.
