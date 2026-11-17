# Rechercheauftrag: Special aircraft

Erstelle eine möglichst umfassende, quellenbasierte internationale Luftfahrzeug-Watchlist für die optionale Kartenebene „Special aircraft“ in MOwlSINT Voodoo Whiskers.

Mit „Luftfahrzeugmustern“ sind Flugzeug-, Hubschrauber- und Drohnentypen gemeint. Erfasse zusätzlich möglichst viele konkrete Maschinen mit belastbaren Identitäten. Die spätere Erfassung soll ausgewählte Luftfahrzeuge alle zehn Minuten abfragen und höchstens 14 Tage beziehungsweise insgesamt 30 MiB Positionsdaten vorhalten. Implementiere oder starte keine Erfassung.

Ziel und geografischer Rahmen:
Maritime Aktivitäten in Nordsee, Ostsee, Ärmelkanal, angrenzendem Nordatlantik und Nordmeer sowie im Schwarzen Meer. Weitere Chokepoints wie Hormuz als gesonderten Kontext berücksichtigen. Erfasse Luftfahrzeuge relevanter Staaten auch bei zeitweiligen Einsätzen außerhalb ihres Heimatlandes.

Priorisiere Deutschland, Dänemark, Norwegen, Schweden, Finnland, Niederlande, Belgien, Großbritannien, Irland, Frankreich, Polen, Estland, Lettland, Litauen und Island. Ergänze Russland, USA, Kanada sowie Rumänien, Bulgarien, Türkei, Ukraine und Georgien. Berücksichtige anschließend Italien, Spanien, Portugal, Griechenland, Zypern und weitere für diese Seeräume relevante Staaten. Nationale Streitkräfte und NATO-eigene Luftfahrzeuge getrennt führen.

Gesuchte Kategorien:
- Polizei, Küstenwache, Zoll, Grenzschutz und Fischereiüberwachung.
- Militärische Seefernaufklärung, maritime Patrouille, ISR, AWACS, relevante militärische Hubschrauber und sonstige maritime Unterstützungsflugzeuge.
- NATO-eigene bemannte Luftfahrzeuge und RPAS.
- Ölaufklärung, maritime Umweltüberwachung und Emissions-/„Sniffer“-Flugzeuge.
- EMSA- und Frontex-Luftfahrzeuge einschließlich konkret belegter ziviler Auftragnehmer und Drohnen.
- Maritime SAR, Offshore-SAR und Luftrettung mit nachvollziehbarem maritimem Bezug.
- Ausgewählte Plattform-, Windpark- und Schiffsversorgungshubschrauber als separate zivile Offshore-Kategorie.

Arbeitsweise:
1. Recherchiere aktiv im Internet. Nutze vorrangig Behörden, Streitkräfte, zivile Luftfahrtregister, Betreiberflottenseiten, NATO, EMSA, Frontex und öffentliche Vergabeunterlagen.
2. Erstelle zuerst eine Länder-/Behörden-/Betreiberübersicht, danach die Liste konkreter Luftfahrzeuge. Weise pro Land überprüfte und noch offene Kategorien aus.
3. Verknüpfe die Betreiber-/Aufgabenbelege mit den Identitätsbelegen für jede einzelne Maschine. Unterscheide Eigentümer, tatsächlichen Betreiber und auftraggebende Behörde.
4. Nutze öffentliche Luftfahrzeugdatenbanken bei Bedarf ergänzend für ICAO24. Kennzeichne diese Herkunft und prüfe die Zuordnung möglichst unabhängig. Leite ICAO24 nicht aus Nationalität, Rufzeichen oder Muster ab.
5. Prüfe Aktualität, Ausmusterung, Betreiberwechsel, Schulungsstatus sowie befristete Aufträge und Stationierungen. Historische EMSA-Verträge belegen keinen aktuellen Einsatz. Ein ziviler Auftragnehmer macht nicht seine gesamte Flotte zur Behördenflotte.
6. Erfasse verfügbare Kennungen als Zeichenketten mit erhaltenen führenden Nullen. ICAO24: genau sechs Hexadezimalzeichen. Rufzeichen sind veränderliche Zusatzinformationen und kein alleiniger Identitätsschlüssel.
7. Kennzeichne ADS-B-Sichtbarkeit als „nachgewiesen“, „nicht nachgewiesen“ oder „unbekannt“, mit Datum und Quelle. Ein bekannter Drohnentyp ist kein Beleg, dass seine Position öffentlich abrufbar ist.
8. Bei fehlender ICAO24 bleibt die Maschine ein Recherchekandidat. Erfinde keine Kennungen. Prüfe widersprüchliche Zuordnungen; führe sie nicht automatisch zusammen.
9. Trenne bekannte Rolle und beobachteten Einsatzzweck. Rettungskapazität beweist keinen Rettungseinsatz; Ölüberwachungsrolle keinen Ölunfall; Routine-Offshore-Transport ist ziviler Kontext. Aus Zehn-Minuten-Positionen dürfen kleine Kreise oder eine Überwachung bestimmter Schiffe nicht als gesichert abgeleitet werden.
10. Auswahlentscheidung des Auftraggebers: Gut belegte oder gut geeignete konkrete Maschinen mit einer belegten, widerspruchsfreien ICAO24 dürfen `enabled: true` erhalten. Ein einzelner nachvollziehbarer Datenbankanbieter kann dafür ausreichen; ein zweiter ist erwünscht, aber keine Pflicht. Fehlende individuelle ADS-B-Positionsnachweise allein sperren die Watchlist-Auswahl nicht. Fehlende Kennungen, echte Identitätskonflikte und ersetzte Registrierungen bleiben ausgeschlossen. `enabled` startet keine Erfassung. Keine Darstellung als „vorläufig zugeordnet“: Im englischen Pop-up soll zur eigenen Prüfung der Behörden-/Militärzuordnung und des Flugmusters aufgefordert werden. Quellenqualität und Unsicherheiten trotzdem erhalten.

Gewünschtes Ergebnis:
A. Länder-/Betreibermatrix mit Kategorien, Musterfamilien, Quellen und Abdeckungslücken.
B. Typenkatalog mit genauer Variante, typischen belegten Aufgaben und Betreiberstaaten.
C. Maschinenliste als Tabelle sowie importierbare CSV- und JSON-Fassung. Je Maschine möglichst:
Land; Organisation; Eigentümer; Betreiber; auftraggebende Behörde; Rollen; genauer Typ/Variante; zivile Registrierung; militärische Kennung; ICAO24; belegte Rufzeichenbeispiele; aktueller Dienststatus; Vertrags-/Zuordnungsgültigkeit; Identitätsquelle; Rollenquelle; Quelldatum; Prüfdatum; Identitäts-Verlässlichkeitsstufe; Rollen-Verlässlichkeitsstufe; ADS-B-Sichtbarkeitsstatus; Konflikte/offene Fragen; `enabled` als boolesches true/false; Begründung der Auswahl oder des Ausschlusses.
D. Separate Kandidatenliste für unvollständige oder widersprüchliche Identitäten.
E. Qualitätsbericht mit Zahl der konkreten Maschinen, Zahl verifizierter ICAO24-Zuordnungen, Länder-/Rollenabdeckung und den wichtigsten Lücken.

Belege wesentliche Angaben durch direkt aufrufbare Originalquellen. Prüfe insbesondere doppelt vergebene oder widersprüchliche Kennungen und zeitliche Betreiberwechsel. Wähle keine künstlich kleine Obergrenze; liefere nötigenfalls priorisierte, zusammenführbare Teillisten. Behaupte keine weltweite Vollständigkeit. Schließe mit einer klaren Trennung zwischen direkt für eine geprüfte Watchlist geeigneten Datensätzen und weiter zu recherchierenden Kandidaten.

