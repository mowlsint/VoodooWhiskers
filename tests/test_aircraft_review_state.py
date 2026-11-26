import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from aircraft_review_state import compare_sources
class SourceReviewTests(unittest.TestCase):
    def test_change_failure_and_recovery(self):
        old={"a":{"sha256":"old","checked_at":"yesterday"},"b":{"sha256":"b","checked_at":"yesterday"}}
        rows,state=compare_sources([{"url":"a","ok":True,"sha256":"new"},{"url":"b","ok":False},{"url":"c","ok":True,"sha256":"c"}],old,"today")
        self.assertEqual([r["change_status"] for r in rows],["changed","unavailable","baseline_created"])
        self.assertEqual(state["b"],old["b"])
        rows,state=compare_sources([{"url":"a","ok":True,"sha256":"new"}],state,"tomorrow")
        self.assertEqual(rows[0]["change_status"],"unchanged")
        self.assertEqual(rows[0]["previous_success_at"],"today")
if __name__=="__main__":unittest.main()
