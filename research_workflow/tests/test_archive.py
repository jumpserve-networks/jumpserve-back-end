"""Development regressions for historical evidence boundaries and crosswalks."""
import copy
import sys
import tempfile
from pathlib import Path
import unittest
from decimal import Decimal
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archive
from workflow import FIELDS, canonical, digest, validate_record

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='archive-unit-'); self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)
        self.enterContext(patch.object(archive, 'now', return_value='2026-11-01T00:00:00Z'))

    def original(self, module):
        claim = dict(claim_id='shift', campaign_id='original-campaign', description='Synthetic retained claim', location='Page 3', figure='Figure 1', limitation='Only the recorded finite apparatus', coverage='Original finite coverage', published_values={'baseline_ms': 0, 'share': None, 'sensitivity': '0.5'})
        if module not in ('propagation-delay-study', 'leo-emergency-failover'): claim.update(assessment='untested', evidence='No matched experiment recorded')
        if module == 'leo-emergency-failover': claim['claim_id'] = 'capacity'
        native = dict(campaigns=[dict(id='original-campaign', seed=0)], protocols=[dict(id='original-protocol', frozen_at='2020-01-01')], configurations=[dict(id='configuration-1', requested_resources={'recorded': 'original resource request'})], claims=[claim],
            sources=[dict(reference_number=0, citation='Synthetic main assessed paper', source_url='https://example.test/paper', retrieved_url='https://example.test/version', kind='research', review_status='partial-review', access_status='available', findings='Original recorded review', limitations='Figures were unexamined', sha256='a' * 64, byte_count=123)],
            controls=[dict(status='failed', reason='Retained original failure')], meta=[dict(review_definition='Synthetic full review definition')], published=[dict(id='pub-1', configuration_id='configuration-1', metric='DNS success', value=0, location='Table 1')])
        if module == 'ipv6-dns-study':
            native['summaries'] = [dict(configuration_id='configuration-1', statistics={'DNS': dict(units='percent', median=0, planned=3, n=2, missing=1, invalid=0, interval_kind='descriptive'), 'packet': dict(units='percent', median=None, planned=3, n=1, missing=1, invalid=1, interval_kind='descriptive')})]
            native['comparisons'] = [dict(id='comparison-1', configuration_id='configuration-1', metric='DNS success', reproduced=0, published_id='pub-1', published=0, difference_pp=0, assessment='reproduced', valid_days=2, planned_days=3, epoch='Original epoch')]
        elif module == 'http2-compliance-study':
            native['summaries'] = [dict(configuration_id='configuration-1', author_counts={'accepted': 0, 'rejected': None}, published_counts={'accepted': 0, 'rejected': 1}, published_source='Original Table 5', assessment='inconclusive', planned=5, recorded=4, missing=1, unknown=1), dict(configuration_id='configuration-1', author_counts={'accepted': 1}, assessment='inconclusive', planned=1, recorded=1, missing=0, unknown=0)]
        elif module == 'leo-emergency-failover':
            native['summaries'] = [dict(campaign_id='original-campaign', country='Synthetic country', published_gbps='2.5', mean_gbps='2.3', min_gbps='2.1', max_gbps='2.4', snapshots=3, assessment='discrepant')]
        elif module == 'propagation-delay-study':
            native['summaries'] = [dict(family='shift', cca_group='bbr', flow_index=1, baseline_delta='0.5', treatment_delta='0.2', improvement='0.3', baseline_ci_low='0.4', baseline_ci_high='0.6', treatment_ci_low='0.1', treatment_ci_high='0.3', improvement_ci_low='0.2', improvement_ci_high='0.4', expected_pairs=5, matched_pairs=4)]
        else:
            native['summaries'] = [dict(configuration_id='configuration-1', statistics={'CPU latency': dict(units='ms', median=0, n=2, min=0, max=1)}, planned=5, recorded=2, failed=1, excluded=1)]
        return dict(module=module, data=native, original_relation_counts={module + '_claims': 1, module + '_configurations': 1})

    def build(self, module, original=None):
        exported = original or self.original(module); path = self.root / (module + '.json'); path.write_bytes(canonical(exported))
        definition = dict(source='sources', title='Synthetic archived assessment', doi='10.0000/synthetic', domain='Synthetic networking study', prefix=module + '_', export='/api/results')
        return archive.build(path, definition, self.root, 'b' * 64), path

    def test_all_five_historical_transfers_validate_real_schema_and_keep_original_bytes_and_scientific_scope(self):
        modules = ['ipv6-dns-study', 'http2-compliance-study', 'leo-emergency-failover', 'propagation-delay-study', 'reliable-sketch-study']
        for module in modules:
            with self.subTest(module=module):
                original = self.original(module); raw = canonical(original); result, path = self.build(module, original)
                self.assertEqual(path.read_bytes(), raw); self.assertEqual(result['input_sha256'], digest(raw)); self.assertEqual(result['provenance']['original_export_sha256'], digest(raw)); self.assertEqual(result['provenance']['scientific_execution'], 'None; transfer only')
                self.assertEqual(result['provenance']['native_counts'], original['original_relation_counts']); self.assertIn('No historical experiments were rerun', result['paper']['scope'])
                kinds = [row['kind'] for row in result['records']]; self.assertEqual(kinds, sorted(kinds, key=lambda value: list(FIELDS).index(value)))
                for row in result['records']: self.assertEqual(row['record'], validate_record(row['kind'], row['record']))
                again, _ = self.build(module, original); self.assertEqual(result, again)
                run = next(row['record'] for row in result['records'] if row['kind'] == 'runs'); self.assertIsNone(run['usage']['measured_charges_usd']); self.assertIsNone(run['usage']['model_tokens']); self.assertEqual(run['usage']['logical_input_bytes'], len(raw)); self.assertEqual(run['provenance']['data_origin'], 'archived-data')
                protocol = next(row['record'] for row in result['records'] if row['kind'] == 'protocols'); self.assertEqual(protocol['document']['native_campaigns'], original['data']['campaigns']); self.assertEqual(protocol['registration_kind'], 'retrospective-import'); self.assertEqual(protocol['sha256'], digest(canonical(protocol['document'])))

    def test_metric_specific_coverage_retains_original_denominators_zeros_missingness_and_dependence(self):
        ipv6, _ = self.build('ipv6-dns-study'); summaries = [r['record'] for r in ipv6['records'] if r['kind'] == 'summaries']; self.assertEqual(len(summaries), 2)
        self.assertEqual([(s['planned'], s['recorded'], s['missing'], s['invalid']) for s in summaries], [(3, 2, 1, 0), (3, 1, 1, 1)])
        for summary in summaries:
            self.assertEqual(summary['interval_kind'], 'descriptive'); self.assertIn('correlated', summary['uncertainty_method'])
            for values in summary['statistics']['native_aggregate']['statistics'].values(): self.assertFalse({'planned', 'n', 'missing', 'invalid', 'interval_kind'} & set(values))
        measure = next(r['record'] for r in ipv6['records'] if r['kind'] == 'measurements'); published = next(r['record'] for r in ipv6['records'] if r['kind'] == 'published_values'); self.assertEqual((measure['value'], measure['status'], published['value']), (0, 'recorded', 0)); self.assertEqual(measure['published_id'], published['id']); self.assertEqual(measure['details']['tolerance_absolute'], .0051)
        protocol = next(r['record'] for r in ipv6['records'] if r['kind'] == 'protocols'); self.assertNotIn('published', protocol['document']['native_miscellaneous']); self.assertIn('follow-ups remain separate', ipv6['paper']['scope'])
        http2, _ = self.build('http2-compliance-study'); summaries = [r['record'] for r in http2['records'] if r['kind'] == 'summaries']; self.assertEqual((summaries[0]['recorded'], summaries[0]['ambiguous'], summaries[0]['missing']), (3, 1, 1)); self.assertEqual(summaries[0]['interval_kind'], 'none')
        missing = next(r['record'] for r in http2['records'] if r['kind'] == 'measurements' and r['record']['status'] == 'missing'); self.assertIsNone(missing['value']); self.assertIn('not a recorded zero', missing['reason']); self.assertEqual(missing['details']['published'], 1); self.assertIsNone(missing['details']['difference'])
        unmatched = next(r['record'] for r in http2['records'] if r['kind'] == 'measurements' and r['record']['published_id'] is None); self.assertIsNone(unmatched['details']['published']); self.assertEqual(unmatched['details']['label'], 'unmatched historical aggregate')

    def test_module_projections_do_not_compare_different_metrics_or_upgrade_inconclusive_claims(self):
        leo, _ = self.build('leo-emergency-failover'); observed = next(r['record'] for r in leo['records'] if r['kind'] == 'measurements'); published = next(r['record'] for r in leo['records'] if r['kind'] == 'published_values')
        self.assertEqual((observed['metric'], observed['value'], observed['published_id']), ('fixed-window mean capacity', 2.3, None)); self.assertEqual((published['metric'], published['value']), ('published maximum downlink capacity', 2.5)); self.assertEqual(observed['details']['interval_kind'], 'descriptive')
        delay, _ = self.build('propagation-delay-study'); measures = [r['record'] for r in delay['records'] if r['kind'] == 'measurements']; self.assertEqual(len(measures), 3)
        for row in measures: self.assertEqual(row['units'], 'log2(max/min)'); self.assertEqual(row['details']['interval_kind'], 'confidence'); self.assertIn('whole-trial paired-bootstrap', row['details']['uncertainty_method'])
        values = [r['record'] for r in delay['records'] if r['kind'] == 'published_values']; self.assertEqual({r['metric']: r['units'] for r in values}, {'baseline_ms': 'ms', 'share': 'fraction', 'sensitivity': 'log2(max/min)'})
        missing = next(row for row in values if row['metric'] == 'share'); self.assertIsNone(missing['value']); self.assertEqual(missing['status'], 'missing')
        reliable, _ = self.build('reliable-sketch-study'); summary = next(r['record'] for r in reliable['records'] if r['kind'] == 'summaries'); self.assertEqual((summary['planned'], summary['recorded'], summary['missing'], summary['invalid'], summary['excluded']), (5, 2, 1, 1, 1))
        gaps = [r['record'] for r in reliable['records'] if r['kind'] == 'gaps']; self.assertEqual(len(gaps), 1); self.assertEqual(gaps[0]['status'], 'open'); self.assertIsNone(gaps[0]['estimated_cost_usd']); self.assertEqual(reliable['claim_labels'], {'untested': 1})

    def test_source_deduplication_review_crosswalks_and_nonpublic_locations_preserve_exact_originals(self):
        row = self.original('http2-compliance-study')['data']['sources'][0]; native = {'sources': [row, copy.deepcopy(row)], 'meta': [{'review_definition': 'Explicit review definition'}]}
        for index, (status, sha, expected) in enumerate([('reviewed', None, 'partial-review'), ('unknown', 'a' * 64, 'retrieved-unreviewed'), ('unknown', None, 'unavailable-full-text'), ('complete', 'a' * 64, 'complete-review')], 1):
            native['sources'].append(dict(row, reference_number=index, citation='Synthetic citation ' + str(index), review_status=status, sha256=sha, source_url='file:///private/original.pdf', retrieved_url=None, role=None, findings=None))
        original = canonical(native); sources, main = archive.source_rows(native, 'sources', str(archive.NAMESPACE), self.root); self.assertEqual(len(sources), 5); self.assertEqual(main, sources[0]['id']); self.assertEqual(canonical(native), original)
        self.assertEqual([s['review_status'] for s in sources[1:]], ['partial-review', 'retrieved-unreviewed', 'unavailable-full-text', 'complete-review'])
        for row in sources[1:]: self.assertIsNone(row['source_url']); self.assertEqual(row['review_definition'], 'Explicit review definition'); self.assertEqual(row['retrieval_attempts'][-1]['nonpublic_location_fields'], ['source_url'])
        self.assertEqual(sources[-1]['unexamined'], 'none')

    def test_missing_delay_main_source_binds_actual_local_bytes_without_inventing_a_full_review(self):
        folder = self.root / 'experiments/nines2026_delay_equalization'; (folder / 'literature').mkdir(parents=True); (folder / 'literature/primary.pdf').write_bytes(b'Synthetic original PDF bytes'); (folder / 'PRIMARY_READING.md').write_bytes(b'Synthetic original reading notes')
        sources, main = archive.source_rows({'sources': []}, 'sources', str(archive.NAMESPACE), self.root); self.assertEqual(main, sources[0]['id']); self.assertEqual(sources[0]['sha256'], digest(b'Synthetic original PDF bytes')); self.assertEqual(sources[0]['byte_count'], len(b'Synthetic original PDF bytes')); self.assertEqual(sources[0]['review_status'], 'partial-review'); self.assertEqual(sources[0]['retrieval_attempts'][0]['reading_notes_sha256'], digest(b'Synthetic original reading notes'))

    def test_projection_stops_at_record_and_byte_bounds_without_truncating_native_evidence(self):
        for kind in ['records', 'bytes']:
            original = self.original('http2-compliance-study')
            if kind == 'records': original['data']['configurations'] = [dict(id='configuration-' + str(i)) for i in range(5001)]
            else: original['data']['campaigns'][0]['original_text'] = 'x' * 3000001
            raw = canonical(original)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'do not truncate'): self.build('http2-compliance-study', original)
            self.assertEqual((self.root / 'http2-compliance-study.json').read_bytes(), raw)

    def test_missing_and_recorded_zero_remain_distinct(self):
        self.assertIsNone(archive.number(None))
        self.assertEqual(archive.number('0.000'), 0)
        self.assertEqual(Decimal(str(archive.number('0.0051'))), Decimal('0.0051'))

    def test_unknown_module_cannot_be_imported_as_a_supported_historical_study(self):
        original=self.original('reliable-sketch-study');original['module']='unregistered-module'
        with patch.object(archive,'source_rows') as sources,self.assertRaisesRegex(ValueError,'Unknown historical assessment module'):
            self.build('unregistered-module',original)
        sources.assert_not_called()

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
