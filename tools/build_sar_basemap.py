"""Build an approximate local coast overview; never use it as the detection land mask.

Developer-only: pip install 'shapely>=2,<3' (production workflow does not need it).
Input is the SHA-pinned Natural Earth archive also used by the real land-mask code.
"""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import shapefile
from shapely.geometry import shape, mapping, box

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / 'config/sar_workbench.json').read_text())
data = Path(sys.argv[1]).read_bytes()
assert hashlib.sha256(data).hexdigest() == config['land_archive_sha256']
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    names = archive.namelist()
    reader = shapefile.Reader(**{ext: io.BytesIO(archive.read(next(n for n in names if n.endswith('.' + ext)))) for ext in ('shp', 'shx', 'dbf')})
    extent = box(-26, 47, 36, 81)
    features = []
    for item in reader.iterShapes(bbox=(-26, 47, 36, 81)):
        geometry = shape(item.__geo_interface__).intersection(extent).simplify(.002, preserve_topology=True)
        if not geometry.is_empty and geometry.area >= .0001:
            features.append({'type': 'Feature', 'properties': {}, 'geometry': mapping(geometry)})
output = {'type': 'FeatureCollection', 'metadata': {'source': config['land_archive_url'], 'source_sha256': config['land_archive_sha256'], 'license': 'Natural Earth public domain', 'purpose': 'Approximate orientation only; not a nautical chart or analysis mask'}, 'features': features}
# Millidegrees are adequate for this explicitly approximate overview.
output = json.loads(json.dumps(output), parse_float=lambda n: round(float(n), 3))
(ROOT / 'public/assets/sar-basemap.geojson').write_text(json.dumps(output, separators=(',', ':')) + '\n')
print(f'Wrote {len(features)} local land features')
