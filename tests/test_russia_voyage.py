"""Ten-day Russian-voyage classification: dated port record or current exact destination."""
from datetime import datetime, timedelta, timezone
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from russia_voyage import classify, destination_target

NOW = datetime(2026, 11, 23, 7, tzinfo=timezone.utc)
PORTS = {"codes": {"RUULU", "RUKGD", "RUMMK"},
         "names": {"USTLUGA", "KALININGRAD", "MURMANSK"}}


def at(days=0, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).isoformat()


class RussiaVoyagePolicyTests(unittest.TestCase):
    def source(self, **changes):
        return {"imo":"9198226", "mmsi":"273111222", "destination":"NL RTM",
                "observed_at":at(), "position_timestamp_valid":True, **changes}

    def test_exact_destination_matches_not_vague_mentions(self):
        for destination in ("RU ULU", "TO UST-LUGA", "NL RTM > RU ULU",
                            "FROM NL RTM TO MURMANSK"):
            with self.subTest(destination=destination):
                self.assertEqual(destination_target(destination, PORTS),
                                 "MURMANSK" if "MURMANSK" in destination else "RUULU" if "RU ULU" in destination else "USTLUGA")
        for invalid in ("RU", "RUSSIA", "OPERATING NEAR RUSSIA", "RUULU>NLRTM",
                        "RUULU>NLRTM>RUULU", "NO RUSSIAN LINK", "RUST",
                        "RUULU DELAYED", "NL RTM"):
            self.assertIsNone(destination_target(invalid, PORTS), invalid)

    def test_current_explicit_russian_destination_is_only_current_intent(self):
        value = classify(self.source(destination="NL RTM > RU ULU"), PORTS, NOW)
        self.assertTrue(value["bound"])
        self.assertFalse(value["visit"])
        self.assertEqual(value["destination_port"], "RUULU")

    def test_old_declared_destination_and_new_destination_are_not_voyages(self):
        self.assertFalse(classify(self.source(destination="RU ULU",observed_at=at(days=4)),PORTS,NOW)["bound"])
        self.assertFalse(classify(self.source(destination="NL RTM",
                  recent_russian_portcall_unconfirmed_10d=True,
                  to_russia_declared=True),PORTS,NOW)["bound"])
        self.assertFalse(classify(self.source(destination="",to_russia_declared=True),PORTS,NOW)["bound"])

    def test_port_name_without_independently_dated_visit_is_not_evidence(self):
        contact=self.source(last_port_name="Ust-Luga",from_russia_confirmed=True,
                            recent_russian_portcall_confirmed_10d=True)
        self.assertFalse(classify(contact,PORTS,NOW)["visit"])
        self.assertTrue(classify(self.source(last_port_name="Ust-Luga",
                              last_ru_port_date=at(days=9)),PORTS,NOW)["visit"])
        self.assertFalse(classify(self.source(last_port_name="Ust-Luga",
                               last_ru_port_date=at(days=11)),PORTS,NOW)["visit"])
        self.assertFalse(classify(self.source(last_port_name="Rotterdam",
                               last_ru_port_date=at(days=1)),PORTS,NOW)["visit"])

    def test_unreliable_position_never_proves_a_current_destination(self):
        self.assertFalse(classify(self.source(destination="RU ULU",
                               position_timestamp_valid=False),PORTS,NOW)["bound"])
        self.assertFalse(classify(self.source(destination="RU ULU",
                               observed_at=at(hours=-1)),PORTS,NOW)["bound"])


if __name__ == '__main__':
    unittest.main()
