"""Grid research helpers preserve anonymous identity and the reported UTC interval."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GfwGridLookupTests(unittest.TestCase):
    def node(self, script):
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_coordinates_bounds_and_hour_can_be_copied_without_claiming_identity(self):
        self.node(r'''
const assert=require('node:assert/strict'),grid=require('./public/assets/gfw-grid.js');
const c=grid.details({latitude:54.6,longitude:12.1,interval_start:'2026-11-06T05:00:00Z',interval_end:'2026-11-06T06:00:00Z',spatial_resolution_degrees:.01,flag:'PAN',mmsi:'211111111',name:'Not supplied by this grid'});
assert.deepEqual(c.bbox,[12.095,54.595,12.105,54.605]);
assert.equal(c.from,'2026-11-06 05:00:00 UTC');assert.equal(c.to,'2026-11-06 06:00:00 UTC');
assert.equal(c.coordinateText,'54.60000, 12.10000');assert.equal(c.bboxText,'12.09500, 54.59500, 12.10500, 54.60500');
assert(c.lookupText.includes('UTC from: 2026-11-06 05:00:00 UTC'));assert(c.lookupText.includes('Reported flag group: PAN'));
assert(c.lookupText.includes('No individual vessel identity'));assert(!c.lookupText.includes('211111111'));assert(!c.lookupText.includes('Not supplied by this grid'));
assert(grid.tooltip(c).includes('2026-11-06'));assert(grid.tooltip(c).includes('click for coordinates'));
''')

    def test_invalid_coordinates_and_intervals_do_not_generate_lookup_locations(self):
        self.node(r'''
const assert=require('node:assert/strict'),grid=require('./public/assets/gfw-grid.js');
const row={latitude:54.6,longitude:12.1,interval_start:'2026-11-06T05:00:00Z',interval_end:'2026-11-06T06:00:00Z'};
for(const change of [{latitude:null},{latitude:true},{latitude:100},{longitude:''},{longitude:200},{interval_start:'unknown'},{interval_end:row.interval_start},{spatial_resolution_degrees:0}])assert.equal(grid.details({...row,...change}),null);
assert.equal(grid.details(null),null);
''')

    def test_age_is_measured_from_the_reported_interval_end(self):
        self.node(r'''
const assert=require('node:assert/strict'),grid=require('./public/assets/gfw-grid.js');
const c=grid.details({latitude:54.6,longitude:12.1,interval_start:'2026-11-06T07:00:00+02:00',interval_end:'2026-11-06T08:00:00+02:00'});
assert.equal(grid.age(c,Date.parse('2026-11-11T10:00:00Z')),'5 d 4 h ago');
assert.equal(grid.age(c,c.end+2*3600000+3*60000),'2 h 3 min ago');
assert.equal(grid.age(c,c.end+5*60000),'5 min ago');assert.equal(grid.age(c,c.end),'Less than 1 min ago');
assert(grid.tooltip(c).includes('interval ended:'));
''')

    def test_future_or_unknown_intervals_do_not_look_fresh(self):
        self.node(r'''
const assert=require('node:assert/strict'),grid=require('./public/assets/gfw-grid.js');
const c=grid.details({latitude:54.6,longitude:12.1,interval_start:'2026-11-06T05:00:00Z',interval_end:'2026-11-06T06:00:00Z'});
assert.equal(grid.age(c,c.start-1),'Future interval — check timestamp');
assert.equal(grid.age(c,c.start+1),'Reporting interval still open');
assert.equal(grid.age(null),'Age unknown');assert.equal(grid.age(c,NaN),'Age unknown');
''')


if __name__ == '__main__':
    unittest.main()
