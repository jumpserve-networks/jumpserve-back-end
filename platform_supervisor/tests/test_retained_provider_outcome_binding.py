"""Revision-2 task binding and native evidence availability regressions."""
import copy
import json
import unittest
from unittest.mock import patch

from platform_supervisor import capability_retained_provider_outcome_diagnostics as tool
from platform_supervisor.common import Hold
from platform_supervisor.tests.test_retained_provider_outcome_diagnostics import Context


class NativeBinding(unittest.TestCase):
    def context(self):
        ctx = Context()
        packet = ctx.packet
        packet['assessment_task_id'] = '2277f9be-ce58-5e9b-b383-b282c7913e45'
        packet['blocker'] = dict(id='1e128010-73b2-4e93-961f-1e0dda97e1bd',
                                source_id='7e8ff650-f7c5-5400-8469-1c36100fa01b', source_kind='repair')
        dispatch = packet['dispatches'][0]
        dispatch.update(study_id=packet['assessment_id'], task_id=packet['assessment_task_id'])
        request = ctx.refs[0]
        packet['native_calls'] = [dict(id='e1dd7f5c-ab3b-44e1-a8e7-0129968f2f7a',
            dispatch_id=dispatch['id'], number=79, status='unknown',
            request_artifact_id=request['id'], request_sha256=request['sha256'],
            response_artifact_id=None, response_sha256=None)]
        return ctx

    def test_repair_source_uses_current_packet_not_historical_incident(self):
        ctx = self.context(); before = copy.deepcopy(ctx.packet)
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('builtins.open', side_effect=AssertionError('filesystem forbidden')):
            report = tool.collect(ctx)
        self.assertEqual(before, ctx.packet)
        self.assertEqual(report['assessment_task_id'], ctx.packet['assessment_task_id'])
        self.assertEqual(report['incident_id'], ctx.packet['blocker']['id'])
        call = report['calls'][0]
        self.assertTrue(call['evidence']['request']['available'])
        self.assertFalse(call['evidence']['response']['available'])
        self.assertFalse(call['terminal_completion_proven'])
        self.assertFalse(call['cleanup_proven'])
        self.assertFalse(report['reconciled'])
        self.assertFalse(report['replacement_admitted'])
        self.assertFalse(report['scientific_completion'])
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_stale_diagnostics_are_not_evidence(self):
        ctx = self.context()
        ctx.packet['diagnostics'] = dict(assessment_task_id='other', incident_id='other',
                                      reconciled=True, terminal_completion_proven=True)
        report = tool.collect(ctx)
        self.assertFalse(report['reconciled'])
        self.assertEqual(report['assessment_task_id'], ctx.packet['assessment_task_id'])
        for field in ('study_id', 'task_id'):
            ctx = self.context(); ctx.packet['dispatches'][0][field] = 'other'
            report = tool.collect(ctx)
            self.assertEqual(report['calls'][0]['reason'], 'dispatch-identity-mismatch')
            self.assertEqual(ctx.reads, [])

    def test_other_valid_dispatch_identity_is_supported(self):
        ctx = Context()
        ctx.packet['assessment_id'] = '00000000-0000-4000-8000-000000000091'
        ctx.packet['assessment_task_id'] = '00000000-0000-4000-8000-000000000092'
        ctx.packet['blocker']['id'] = '00000000-0000-4000-8000-000000000093'
        report = tool.collect(ctx)
        self.assertEqual(report['status'], 'structural-diagnostics')
        self.assertEqual(report['assessment_id'], ctx.packet['assessment_id'])

    def test_absent_identity_reads_nothing(self):
        for key in ('assessment_id', 'assessment_task_id', 'platform_task_id'):
            ctx = self.context(); del ctx.packet[key]
            report = tool.collect(ctx)
            self.assertEqual(report['reason'], 'identity-unavailable')
            self.assertEqual(ctx.reads, [])
        ctx = self.context(); ctx.packet['blocker'].pop('id')
        self.assertEqual(tool.collect(ctx)['reason'], 'identity-unavailable')

    def test_multiple_calls_are_bounded_and_do_not_borrow_success(self):
        ctx = self.context(); call = ctx.packet['native_calls'][0]
        ctx.packet['native_calls'] = [dict(call, id='00000000-0000-4000-8000-%012d' % i) for i in range(20)]
        report = tool.collect(ctx)
        self.assertEqual(report['unresolved_calls'], 20)
        self.assertTrue(report['truncated'])
        self.assertEqual(len(report['calls']), 16)
        self.assertLessEqual(len(ctx.reads), 64)
        for item in report['calls']:
            self.assertFalse(item['evidence']['response']['available'])
            self.assertFalse(item['terminal_completion_proven'])
        self.assertLess(len(json.dumps(report)), 40000)

    def test_original_unavailable_hash_and_index_conflict(self):
        ctx = self.context()
        with patch.object(ctx, 'original', side_effect=ValueError('PRIVATE')):
            fact = tool.collect(ctx)['calls'][0]['evidence']['request']
        self.assertTrue(fact['indexed'])
        self.assertFalse(fact['available'])
        self.assertEqual(fact['reason'], 'original-unavailable')
        ctx = self.context(); ctx.values['logical.json'] += b' '
        fact = tool.collect(ctx)['calls'][0]['evidence']['request']
        self.assertEqual(fact['reason'], 'original-hash-mismatch')
        ctx = self.context(); ctx.packet['artifacts'][0]['sha256'] = '0' * 64
        fact = tool.collect(ctx)['calls'][0]['evidence']['request']
        self.assertEqual(fact['reason'], 'index-conflict')
        self.assertNotIn(ctx.refs[0]['id'], ctx.reads)

    def test_direct_native_index_and_sidecars_are_availability_only(self):
        ctx = self.context(); ctx.packet['artifacts'] = []
        # Direct request pointers are allowed by Evidence even without artifact metadata.
        report = tool.collect(ctx)
        self.assertTrue(report['calls'][0]['evidence']['request']['available'])
        self.assertFalse(report['calls'][0]['evidence']['cleanup']['indexed'])
        ctx = self.context()
        ref = dict(ctx.refs[1], name='call-79-completion.json')
        ctx.packet['dispatches'][0]['provenance']['artifacts'].append(ref)
        report = tool.collect(ctx)
        self.assertTrue(report['calls'][0]['evidence']['completion']['available'])
        self.assertFalse(report['calls'][0]['terminal_completion_proven'])
        # Even retained terminal bytes cannot reconcile an unknown call in this collector.
        self.assertFalse(report['reconciled'])

    def test_persistence_hold_propagates(self):
        ctx = self.context()
        with patch.object(ctx, 'original', side_effect=Hold('unconfirmed', 'reconciling')):
            with self.assertRaises(Hold): tool.collect(ctx)


if __name__ == '__main__':
    unittest.main()
