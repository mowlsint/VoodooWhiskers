# Voodoo Whiskers — Maritime Infrastructure Watch

**Stand:** 2026-07-21  
**Phase:** Phase 2 — öffentliche Karten-, Download- und Analyseebene

## Zweck

Voodoo Whiskers veröffentlicht seine zusammengeführten AIS-, VOI- und Infrastrukturprodukte unter `public/`. Die Anwendung ist mit relativen Pfaden aufgebaut und kann später unverändert aus Cloudflare Pages bereitgestellt werden.

## Öffentliche Anwendung

```text
public/index.html
public/infrastructure-watch.html
public/assets/infrastructure-watch.css
public/assets/infrastructure-watch.js
```

Die Leaflet-Karte bietet umschaltbare Layer für:

- alle aktuellen zusammengeführten AIS-Kontakte,
- prioritäre VOI,
- neutrale Tankerkontexte,
- Sanktionen/Schattenflotte,
- False-Flag-Interesse,
- russische MMSI,
- kürzlichen Russlandbezug,
- Telekommunikations- und Stromkabel,
- Kabelanlandestationen,
- Pipelines,
- Offshore-Windparks und weitere Energieanlagen,
- kombinierte Infrastruktur-Review-Ereignisse.

## Datenprodukte

Alle öffentlichen Outputs liegen unter `public/data/` und `public/downloads/`. Provider werden in Status- und Produktbezeichnungen als **AIS** zusammengefasst. Konkrete Anbieter bleiben in technischen Detailfeldern erhalten.

Die Analyse ist ein Analystenhinweis. Sie erzeugt keine Aussage zu Sabotage, Spionage, Attribution oder Rechtswidrigkeit und verändert keinen Magic-Paws-Score.

## Workflows

- `build-public-products.yml`: erzeugt öffentliche AIS-/VOI-Produkte, Analyse und Manifeste.
- `sync-emodnet-reference.yml`: synchronisiert wöchentlich EMODnet Human Activities und baut anschließend Analyse und Downloads neu.

Vor der ersten produktiven Nutzung ist `Sync EMODnet infrastructure reference` einmal manuell auszulösen. Bis dahin bleiben die Referenz-GeoJSONs gültige leere FeatureCollections und die Analyse meldet `reference_ready: false`.

## Lokaler Test

```bash
python scripts/build_public_outputs.py
python scripts/analyze_infrastructure_proximity.py
python scripts/build_public_manifest.py
python -m http.server 8000 --directory public
```

Anschließend `http://localhost:8000/` öffnen.
