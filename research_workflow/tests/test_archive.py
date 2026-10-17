"""Development regressions for historical evidence boundaries and crosswalks."""
import sys
from pathlib import Path
import unittest
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archive

class ArchiveTests(unittest.TestCase):
    def test_missing_and_recorded_zero_remain_distinct(self):
        self.assertIsNone(archive.number(None))
        self.assertEqual(archive.number('0.000'), 0)
        self.assertEqual(Decimal(str(archive.number('0.0051'))), Decimal('0.0051'))

    def test_unrepresentable_numbers_are_not_rounded_into_evidence(self):
        for value in ('1.234567890123456789', 'NaN', 'Infinity', True):
            with self.assertRaises(ValueError):
                archive.number(value)

    def test_original_explicit_labels_take_precedence(self):
        original = dict(assessment='inconclusive', evidence='Finite control only')
        label, evidence, _ = archive.claim_finding('http2-compliance-study', original, {})
        self.assertEqual(label, 'inconclusive')
        self.assertEqual(evidence, original['evidence'])

    def test_delay_directional_and_threshold_findings_are_separate(self):
        self.assertEqual(archive.claim_finding('propagation-delay-study', {'claim_id':'shift'}, {})[0], 'reproduced')
        self.assertEqual(archive.claim_finding('propagation-delay-study', {'claim_id':'narrow'}, {})[0], 'discrepant')
        self.assertEqual(archive.claim_finding('propagation-delay-study', {'claim_id':'gcp'}, {})[0], 'untested')

    def test_partial_simulation_coverage_does_not_validate_operation(self):
        self.assertEqual(archive.claim_finding('leo-emergency-failover', {'claim_id':'rates','coverage':'Ku sensitivity'}, {})[0], 'inconclusive')
        self.assertEqual(archive.claim_finding('leo-emergency-failover', {'claim_id':'routing','coverage':'Not reproduced'}, {})[0], 'untested')

    def test_unknown_coverage_cannot_be_silently_upgraded(self):
        with self.assertRaises(ValueError):
            archive.claim_finding('unregistered-domain', {'claim_id':'arbitrary'}, {})

    def test_metric_specific_missing_days_are_not_conflated(self):
        rows = archive.split_ipv6_coverage([dict(id='configuration-day-grid', statistics={
            'DNS':dict(planned=21,n=21,missing=0,invalid=0),
            'packet':dict(planned=21,n=20,missing=1,invalid=0)})])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['statistics']['DNS']['n'], 21)
        self.assertEqual(rows[1]['statistics']['packet']['missing'], 1)
        with self.assertRaises(ValueError):
            archive.split_ipv6_coverage([dict(statistics={'DNS':dict(planned=21,n=20,missing=0,invalid=0)})])

    def test_main_source_identity_uses_reference_zero(self):
        row=dict(reference_number=0,citation='Actual assessed paper',source_url='https://example.org/main.pdf',
                 kind='research',role='target',access_status='retrieved',review_status='partial-review',
                 sha256='a'*64,byte_count=123,findings='Partial original review',limitations='Figures unexamined')
        sources, main=archive.source_rows({'sources':[row]}, 'sources', str(archive.NAMESPACE), Path('/nonexistent'))
        self.assertEqual(len(sources), 1)
        self.assertEqual(main, sources[0]['id'])
        self.assertEqual(sources[0]['source_url'], row['source_url'])

if __name__ == '__main__':
    unittest.main()
