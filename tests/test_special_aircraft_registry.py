"""Offline integrity and selection-policy tests; no provider or workflow calls."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('aircraft_registry', ROOT / 'scripts/special_aircraft_registry.py')
REGISTRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGISTRY)


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.selected = REGISTRY.load_registry()

    def test_real_registry_is_bounded_and_retains_input_provenance(self):
        self.assertGreater(len(self.selected), 18)
        self.assertEqual(sum(p['input_records'] for p in self.data['input_provenance']), 182)
        self.assertEqual(len(self.data['aircraft']), 182 + self.data['research_summary']['new_airframe_records'])
        for item in self.data['input_provenance']:
            self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')
        self.assertLess(REGISTRY.DEFAULT_PATH.stat().st_size, REGISTRY.MAX_REGISTRY_BYTES)

    def test_single_provider_is_sufficient_but_no_mission_or_live_adsb_claim_is_made(self):
        single = [a for a in self.selected if len({e['provider'] for e in a['icao24_evidence']}) == 1]
        self.assertTrue(single)
        self.assertTrue(all(a['observed_mission'] is None and not a['adsb_position_source_verified'] for a in single))
        self.assertTrue(all(a['selection_status'] == 'registry_selected' for a in single))

    def test_identity_conflict_replaced_registration_and_missing_hex_cannot_be_enabled(self):
        for label in ['F-ZBGP', 'G-HMGB', 'PH-CGD', '57+04']:
            data = copy.deepcopy(self.data)
            row = next(a for a in data['aircraft'] if label in [a['registration'], a['military_serial']])
            self.assertFalse(row['enabled'])
            row['enabled'] = True
            row['selection_status'] = 'registry_selected'
            with self.subTest(label=label), self.assertRaises(ValueError):
                REGISTRY.validate_registry(data)

    def test_duplicate_selected_hex_and_contradictory_hex_evidence_are_rejected(self):
        data = copy.deepcopy(self.data)
        first, second = [a for a in data['aircraft'] if a['enabled']][:2]
        second['icao24'] = first['icao24']
        for item in second['icao24_evidence']:
            item['icao24'] = first['icao24']
        with self.assertRaisesRegex(ValueError, 'Duplicate enabled ICAO24'):
            REGISTRY.validate_registry(data)
        data = copy.deepcopy(self.data)
        row = next(a for a in data['aircraft'] if a['enabled'])
        row['icao24_evidence'][0]['icao24'] = 'abcdef'
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            REGISTRY.validate_registry(data)

    def test_nato_is_an_organisation_and_civil_patrol_or_sar_support_is_not_military(self):
        nato = [a for a in self.selected if a['organisation_scope'] == 'NATO']
        self.assertTrue(nato)
        self.assertTrue(all(a['country'] is None for a in nato))
        for label in ['LN-TRG', 'LN-KYV', 'G-HMGA']:
            row = next(a for a in self.selected if label in [a['registration'], a['military_serial']])
            self.assertNotIn('military_maritime_patrol', row['roles'])
        support = next(a for a in self.selected if a['registration'] == 'G-HMGA')
        self.assertIn('sar_support', support['roles'])
        self.assertNotIn('search_and_rescue', support['roles'])
        italian = next(a for a in self.selected if a['military_serial'] == 'MM62279')
        self.assertIn('military_maritime_patrol', italian['roles'])
        border = next(a for a in self.selected if a['registration'] == 'LY-HWW')
        self.assertEqual(border['roles'], ['customs_border_guard'])

    def test_every_role_is_supported_by_dashboard(self):
        import subprocess
        roles = json.loads(subprocess.check_output([
            'node', '-e', "console.log(JSON.stringify(Object.keys(require('./public/assets/special-aircraft.js').ROLES)))"
        ], cwd=ROOT))
        self.assertEqual(REGISTRY.ROLES, frozenset(roles))

    def test_source_references_and_boolean_flags_are_enforced(self):
        data = copy.deepcopy(self.data)
        data['aircraft'][0]['role_source_ids'] = ['missing-source']
        with self.assertRaisesRegex(ValueError, 'missing source'):
            REGISTRY.validate_registry(data)
        data = copy.deepcopy(self.data)
        data['aircraft'][0]['enabled'] = 'true'
        with self.assertRaisesRegex(ValueError, 'boolean'):
            REGISTRY.validate_registry(data)
        data = copy.deepcopy(self.data)
        data['collection_enabled'] = 'true'
        with self.assertRaisesRegex(ValueError, 'Collection switch'):
            REGISTRY.validate_registry(data)

    def test_oversize_registry_is_rejected_before_json_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'registry.json'
            path.write_bytes(b'x' * (REGISTRY.MAX_REGISTRY_BYTES + 1))
            with self.assertRaisesRegex(ValueError, 'budget'):
                REGISTRY.load_registry(path)


if __name__ == '__main__':
    unittest.main()
