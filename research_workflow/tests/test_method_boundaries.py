"""Frozen decision and evidence rejection paths, using local synthetic records.

These exercise the real comparator and retained-byte readers, not a model's
opinion. Synthetic observations here are never paper findings.
"""
import copy
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomy_methods as methods
import source_arithmetic
import store
from workflow import canonical, digest
from test_autonomy_methods import Recorded, uid


class DecisionBoundaries(unittest.TestCase):
    def test_published_policy_identity_binds_the_complete_unattended_method_instruction(self):
        policy = methods.policy(); self.assertEqual(policy['version'], 'autonomy-methods-v1'); self.assertEqual(policy['sha256'], digest(policy['instruction'].encode())); self.assertIn('Never silently repair an old protocol', policy['instruction'])
    def test_decimal_bounds_preserve_zero_and_exact_values_and_reject_boolean_missing_and_nonfinite_operands(self):
        self.assertEqual(methods.decimal('0'), Decimal(0)); self.assertEqual(methods.decimal(Decimal('1.000000000000000000001')), Decimal('1.000000000000000000001'))
        for value in [None, True, False, [], {}, 'not numeric', 'NaN', 'Infinity', '1e101', '1e-101', '1' * 101]:
            with self.subTest(value=value), self.assertRaises(ValueError): methods.decimal(value)

    def test_historical_protocol_stays_readable_but_incomplete_or_cross_scope_decisions_cannot_be_admitted(self):
        db = Recorded(); original = copy.deepcopy(db.protocol); original.pop('decision'); self.assertIsNone(methods.validate(original, db.claim))
        changes = [lambda d: d.update(decision=[]), lambda d: d['decision'].update(version='unknown'), lambda d: d['decision'].update(coverage='unbounded'), lambda d: d['decision'].update(rationale=''), lambda d: d['decision'].update(groups=[{}]), lambda d: d['execution'].update(runner='netem-iperf-v1')]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                document = copy.deepcopy(db.protocol); change(document)
                with self.assertRaises(ValueError): methods.validate(document, db.claim)
        numerical = Recorded(False); numerical.protocol['decision']['kind'] = 'generated-expression'
        with self.assertRaisesRegex(ValueError, 'applicable recorded decision'): methods.validate(numerical.protocol, numerical.claim)

    def test_groups_must_have_bounded_unique_ids_exact_schemas_and_real_output_keys(self):
        cases = ['empty', 'schema', 'id-empty', 'id-duplicate', 'keys-empty', 'keys-too-long', 'keys-not-list', 'more-than-planned', 'invented-output']
        for case in cases:
            with self.subTest(case=case):
                db = Recorded(False); decision = db.protocol['decision']; group = decision['groups'][0]
                if case == 'empty': decision['groups'] = []
                if case == 'schema': group['generated_code'] = 'never execute'
                if case == 'id-empty': group['id'] = ''
                if case == 'id-duplicate': decision['groups'].append(copy.deepcopy(group))
                if case == 'keys-empty': group['observation_ids'] = []
                if case == 'keys-too-long': group['observation_ids'] = ['x' * 301]
                if case == 'keys-not-list': group['observation_ids'] = 'loss:0'
                if case == 'more-than-planned': group['observation_ids'] = ['loss:' + str(i) for i in range(6)]
                if case == 'invented-output': group['observation_ids'] = ['invented-observation']
                with self.assertRaises(ValueError): methods.validate(db.protocol, db.claim)

    def test_rules_cannot_change_arity_mix_units_fit_boolean_targets_or_leave_unused_groups(self):
        for case in ['schema', 'id-empty', 'id-duplicate', 'value-arity', 'binary-arity', 'boolean-arity', 'mixed-units', 'boolean-target', 'unused-group']:
            with self.subTest(case=case):
                db = Recorded(False); decision = db.protocol['decision']; rule = decision['rules'][0]
                if case == 'schema': rule.pop('target')
                if case == 'id-empty': rule['id'] = ''
                if case == 'id-duplicate': decision['rules'].append(copy.deepcopy(rule))
                if case == 'value-arity': rule['operation'] = 'value'
                if case == 'binary-arity': rule['groups'] = rule['groups'][:1]
                if case == 'boolean-arity': decision['rules'][1]['groups'] = ['loss0']
                if case == 'boolean-target': decision['rules'][1]['target'] = '0'
                if case == 'mixed-units':
                    # Each group is independently valid; comparing Mbps and ms is not.
                    group = decision['groups'][1]; group.update(metric='idle_rtt_ms', units='ms')
                if case == 'unused-group': decision['rules'] = decision['rules'][:1]
                with self.assertRaises(ValueError): methods.validate(db.protocol, db.claim)

    def test_exact_comparators_use_baseline_then_treatment_and_do_not_round_tolerance_or_mutate_criteria(self):
        rules = [dict(id='absolute', operation='value', groups=['baseline'], target='10', tolerance_absolute='0'), dict(id='difference', operation='difference', groups=['baseline', 'treatment'], target='-2', tolerance_absolute='0'), dict(id='percent', operation='relative-percent-change', groups=['baseline', 'treatment'], target='-20', tolerance_absolute='0')]
        decision = dict(rules=rules); before = copy.deepcopy(decision); result = methods.compare(decision, {'baseline': Decimal(10), 'treatment': Decimal(8)})
        self.assertEqual([Decimal(item['value']) for item in result], [Decimal(10), Decimal(-2), Decimal(-20)]); self.assertTrue(all(item['matched'] for item in result)); self.assertEqual(decision, before)
        result = methods.compare(decision, {'baseline': Decimal(10), 'treatment': Decimal('8.0000000000000000001')}); self.assertFalse(result[1]['matched']); self.assertFalse(result[2]['matched'])

    def test_source_arithmetic_noncontrast_operations_do_not_invent_a_baseline_order(self):
        db = Recorded(); db.protocol['configurations'][0]['details']['test_config']['calculations'].insert(0, dict(operation='strict-increase', inputs=['control', 'treatment']))
        self.assertEqual(methods.validate(db.protocol, db.claim)['kind'], 'source-arithmetic')

    def test_recorded_package_comparison_uses_its_declared_outputs_instead_of_netem_output_identities(self):
        db = Recorded(False); db.protocol['execution']['runner'] = 'built-package-v1'; db.protocol['decision']['groups'][0]['observation_ids'] = ['package-specific-original-output']
        self.assertEqual(methods.validate(db.protocol, db.claim)['kind'], 'recorded-comparisons')


class RetainedFindingBoundaries(unittest.TestCase):
    def freeze_reviews(self, db):
        for review in db.task['context']['reviews']:
            db.data['autonomy_turns', review['turn_id']]['request']['messages'][0]['content'][0]['text'] = canonical(dict(candidate=dict(protocol=db.protocol))).decode()

    def update_input(self, db, change):
        job = db.jobs[0]; spec = db.input_spec(job); change(spec); db.save_input(job, spec); sha = digest(canonical(spec)); db.data['runs', job['id']]['input_sha256'] = sha
        acquisition = db.data['protocols', db.data['campaigns', job['campaign_id']]['protocol_id']]['document']; acquisition['input_versions'].append({'identity': 'synthetic-retained-input', 'sha256': sha}); self.freeze_reviews(db); return spec

    def test_missing_proposals_reviews_protocol_bindings_and_job_inventories_remain_no_finding(self):
        for case in ['proposal', 'decision', 'review-count', 'review-coverage', 'review-request', 'reference-hash', 'binding', 'binding-bytes', 'job-ids', 'duplicate-job-ids', 'job-inventory', 'job-state', 'duplicate-configuration']:
            with self.subTest(case=case):
                db = Recorded(); context = db.task['context']; review = context['reviews'][0]; turn = db.data['autonomy_turns', review['turn_id']]
                if case == 'proposal': context.pop('proposal')
                if case == 'decision': db.protocol.pop('decision')
                if case == 'review-count': context['reviews'].pop()
                if case == 'review-coverage': review['coverage_complete'] = False
                if case == 'review-request': turn.pop('request')
                if case == 'reference-hash': turn['request']['messages'][0]['content'][0]['text'] = canonical(dict(candidate=dict(protocol={'representation': 'retained-protocol-reference', 'sha256': '0' * 64}))).decode()
                if case == 'binding': context['acquisition_reuse'].pop('claim_protocol_id')
                if case == 'binding-bytes':
                    key = context['acquisition_reuse']['claim_protocol_id']; db.data['protocols', key] = dict(document=dict(db.protocol, hypotheses='Changed after review'))
                if case == 'job-ids': context['job_ids'] = []
                if case == 'duplicate-job-ids': context['job_ids'] *= 2
                if case == 'job-inventory': context['job_ids'].append(uid())
                if case == 'job-state': db.jobs[0]['status'] = 'running'
                if case == 'duplicate-configuration':
                    db.protocol['configurations'].append(copy.deepcopy(db.protocol['configurations'][0])); self.freeze_reviews(db)
                self.assertIsNone(db.finding())

    def test_hash_bound_review_reference_is_accepted_only_for_the_exact_frozen_candidate(self):
        db = Recorded()
        for review in db.task['context']['reviews']:
            turn = db.data['autonomy_turns', review['turn_id']]; turn['request']['messages'][0]['content'][0]['text'] = canonical(dict(candidate=dict(protocol={'representation': 'retained-protocol-reference', 'sha256': digest(canonical(db.protocol))}))).decode()
        self.assertEqual(db.finding()[0], 'reproduced')

    def test_acquisition_replication_source_limits_and_metric_contract_mismatches_cannot_produce_verdicts(self):
        for case in ['replica-outside', 'replica-boolean', 'runner', 'acquisition-execution', 'input-version', 'spec-limits', 'metric-set', 'metric-units', 'spec-version', 'planned-count']:
            with self.subTest(case=case):
                db = Recorded(); job = db.jobs[0]
                if case == 'replica-outside': self.update_input(db, lambda spec: spec.update(replication=1))
                if case == 'replica-boolean': self.update_input(db, lambda spec: spec.update(replication=True))
                if case == 'runner': self.update_input(db, lambda spec: spec.update(runner='other-runner'))
                if case == 'acquisition-execution':
                    acquisition = copy.deepcopy(db.protocol); acquisition['execution']['limits']['wall_seconds'] += 1; db.data['protocols', 'other-acquisition'] = {'document': acquisition}; db.data['campaigns', job['campaign_id']]['protocol_id'] = 'other-acquisition'
                if case == 'input-version': db.protocol['input_versions'] = []; self.freeze_reviews(db)
                if case == 'spec-limits': self.update_input(db, lambda spec: spec['limits'].update(wall_seconds=1))
                if case == 'metric-set':
                    artifact = str(uuid.uuid5(uuid.UUID(job['id']), 'network-output')); output = json.loads(db.bytes[artifact]); output['observations'] = output['observations'][:1]; db.bytes[artifact] = canonical(output); db.data['runs', job['id']]['output_sha256'] = digest(canonical(output)); db.measurements = db.measurements[:1]
                if case == 'metric-units': self.update_input(db, lambda spec: spec['metrics'][0].update(units='wrong'))
                if case == 'spec-version': self.update_input(db, lambda spec: spec.update(version='unknown'))
                if case == 'planned-count': db.protocol['metrics'][0]['planned_observations'] += 1; self.freeze_reviews(db)
                self.assertIsNone(db.finding())

    def test_source_recalculation_requires_matching_cells_published_links_and_exact_original_source(self):
        for case in ['source-identity', 'unmatched-calculation', 'missing-published']:
            with self.subTest(case=case):
                db = Recorded(); source = db.data['sources', db.claim['source_id']]
                if case == 'source-identity': source['id'] = uid()
                if case == 'missing-published': db.measurements[0]['published_id'] = None
                if case == 'unmatched-calculation':
                    with patch.object(source_arithmetic, 'evaluate', return_value=[dict(id='not-recorded', status='invalid')]): self.assertIsNone(db.finding())
                else: self.assertIsNone(db.finding())

    def prior(self, db):
        prior = copy.deepcopy(db.jobs[0]); prior['id'], prior['run_id'] = uid(), uid(); db.data['queue_jobs', prior['id']] = prior; db.data['runs', prior['run_id']] = dict(id=prior['run_id'], status='complete'); db.task['context']['completed_job_ids'] = [prior['id']]; return prior

    def test_earlier_unavailable_inputs_and_missing_run_identities_remain_a_coverage_gap(self):
        db = Recorded(); prior = self.prior(db); prior['run_id'] = None; self.assertIsNone(db.finding())
        db = Recorded(); prior = self.prior(db); original = store.queue_input_bytes
        def read(study, job):
            if job['id'] == prior['id']: raise store.StoreError('Earlier original unavailable')
            return original(study, job)
        with patch.object(store, 'queue_input_bytes', side_effect=read): self.assertIsNone(db.finding())

    def test_complete_job_inventory_with_fewer_hosts_than_the_frozen_design_is_still_incomplete_evidence(self):
        db = Recorded(False, 2); removed = db.jobs.pop(); db.task['context']['job_ids'].remove(removed['id'])
        self.assertIsNone(db.finding())

    def test_zero_baseline_missing_group_observations_and_diagnostics_never_become_a_conclusive_finding(self):
        db = Recorded(False)
        with patch.object(methods, 'compare', return_value=None): self.assertIsNone(db.finding())
        # Models cannot introduce a missing output key after review and ask the
        # controller to silently skip it, even if validation is upstream.
        db.protocol['decision']['groups'][0]['observation_ids'] = ['missing']; self.freeze_reviews(db)
        with patch.object(methods, 'validate', return_value=db.protocol['decision']): self.assertIsNone(db.finding())
        db = Recorded(False); db.protocol['decision']['coverage'] = 'diagnostic-only'; self.freeze_reviews(db); self.assertIsNone(db.finding())


if __name__ == '__main__': unittest.main()
