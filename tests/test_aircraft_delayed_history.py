import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from build_aircraft_delayed_history import build,DAY
class HistoryTests(unittest.TestCase):
    def test_age_identity_conflicts_and_bounds(self):
        now=1800000000
        air=[dict(enabled=True,identity_conflict=False,icao24="123abc",record_id="test",registration="TEST")]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);day=root/"day";day.mkdir()
            rows=[["123abc",now-2*DAY,51,4,1000,100,50,"adsb","TEST",""],["123abc",now-20,51,4,1000,100,50,"adsb","TEST",""],["123abc",now-3*DAY,51,4,1000,100,50,"adsb","WRONG",""],["123abc",now-4*DAY,51,4,1000,100,50,"adsb","TEST",""],["123abc",now-4*DAY,52,4,1000,100,50,"adsb","TEST",""]]
            (day/"test.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
            pack=build(root,air,now)
            self.assertEqual(len(pack["items"]),1)
            self.assertEqual(pack["items"][0][2],now-2*DAY)
            self.assertFalse(pack["coverage_complete"])
if __name__=="__main__":unittest.main()
