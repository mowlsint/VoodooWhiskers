import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from aircraft_directory import build
class DirectoryTests(unittest.TestCase):
    def test_inventory(self):
        d=build()
        self.assertEqual(len(d["aircraft"]),211)
        self.assertEqual(sum(r["selected"] for r in d["aircraft"]),105)
        self.assertEqual(sum(o["records"] for o in d["organisations"]),211)
        self.assertEqual(sum(not r["selected"] for r in d["aircraft"]),106)
    def test_no_mission_or_new_selection(self):
        d=build()
        self.assertFalse(d["complete"])
        self.assertTrue(all("icao24" not in r and "latitude" not in r for r in d["aircraft"]))
        self.assertTrue(all(p["url"].startswith("https://") for p in d["programmes"]))
        self.assertTrue(any("SAR primary" in r["sar_classes"] for r in d["aircraft"]))
if __name__=="__main__":unittest.main()
