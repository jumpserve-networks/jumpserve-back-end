"""Compatibility checks for retained historical evidence, separate from the controller."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legacy_evidence import evidence_records
import workflow

class LegacyEvidence(unittest.TestCase):
    def test_historical_evidence_decodes_exactly_without_changing_null_zero_precision_or_original_fields(self):
        cases = json.loads((Path(__file__).parent / 'fixtures/legacy-evidence-columns-v1.json').read_bytes())['cases']
        for name, case in cases.items():
            with self.subTest(name=name):
                before = workflow.canonical(case['packet'])
                original = workflow.canonical(case['records'])
                decoded = evidence_records(case['packet'])
                self.assertEqual(workflow.canonical(decoded), original)
                self.assertEqual(case['packet']['original_records_sha256'], workflow.digest(original))
                self.assertEqual(workflow.canonical(case['packet']), before)
        self.assertEqual(cases['scientific-values']['records'][0]['value'], '0')
        self.assertIsNone(cases['scientific-values']['records'][1]['value'])

    def test_historical_transport_rejects_invalid_dictionary_indices(self):
        packet = json.loads((Path(__file__).parent / 'fixtures/legacy-evidence-columns-v1.json').read_bytes())['cases']['dictionary']['packet']
        self.assertTrue(packet['dictionaries'])
        column = next(iter(packet['dictionaries']))
        packet['rows'][0][packet['columns'].index(column)] = True
        with self.assertRaises(ValueError):
            evidence_records(packet)

    def test_lossless_evidence_decoder_refuses_incomplete_rows_and_details(self):
        packet = json.loads((Path(__file__).parent / 'fixtures/legacy-evidence-columns-v1.json').read_bytes())['cases']['details']['packet']
        for defect in ['representation', 'count', 'row', 'details']:
            broken = copy.deepcopy(packet)
            if defect == 'representation': broken['representation'] = 'unknown'
            elif defect == 'count': broken['record_count'] += 1
            elif defect == 'row': broken['rows'][0].append('unbound value')
            else: broken['rows'][0][broken['columns'].index('details')] = []
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                evidence_records(broken)
