"""Additive regressions: repair binding before private reads, inherited validation intact."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from platform_supervisor import capabilities
from platform_supervisor import capability_repair_provider_outcome_diagnostics as tool
from platform_supervisor import capability_retained_provider_outcome_diagnostics as retained
from platform_supervisor.common import Hold
from platform_supervisor.tests.test_retained_provider_outcome_diagnostics import Context, stream, frame


def context(raw=None):
    ctx = Context(raw)
    ctx.packet['blocker'] = dict(id=tool.INCIDENT, source_kind='repair', source_id=tool.REPAIR)
    ctx.packet['repairs'] = [dict(id=tool.REPAIR, incident=dict(failed_turn_id=tool.DISPATCH))]
    return ctx


class RepairDiagnostics(unittest.TestCase):
    def test_literal_pack_and_bounded_private_read_only_report(self):
        path = Path(tool.__file__)
        descriptor = capabilities.descriptor(path.read_bytes(), path.name)
        self.assertEqual(descriptor['id'], 'repair-provider-outcome-diagnostics')
        self.assertEqual(descriptor['revision'], 1)
        ctx = context(); before = copy.deepcopy(ctx.packet)
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('builtins.open', side_effect=AssertionError('filesystem forbidden')):
            report = tool.collect(ctx)
        self.assertEqual(ctx.packet, before)
        self.assertEqual(len(ctx.reads), 4)
        self.assertEqual(report['incident_id'], tool.INCIDENT)
        self.assertEqual(report['repair_id'], tool.REPAIR)
        self.assertEqual(report['stream']['observation'], 'terminal-completed-observed')
        for flag in ('reconciled', 'scientific_completion', 'replacement_admitted'):
            self.assertIs(report[flag], False)
        encoded = json.dumps(report)
        self.assertLess(len(encoded), 8000)
        for private in ('PRIVATE PROMPT', 'PRIVATE RESEARCH', 'private-response', retained.INCIDENT):
            self.assertNotIn(private, encoded)
        for ref in ctx.refs:
            self.assertEqual(report['sources'][ref['name']]['sha256'], ref['sha256'])

    def test_exact_packet_identities_required_before_reads(self):
        for field in ('assessment_id', 'assessment_task_id', 'platform_task_id', 'id', 'source_id', 'source_kind'):
            ctx = context()
            target = ctx.packet if field in ('assessment_id', 'assessment_task_id', 'platform_task_id') else ctx.packet['blocker']
            target[field] = '' if field == 'platform_task_id' else 'other'
            with self.subTest(field=field):
                self.assertEqual(tool.collect(ctx)['status'], 'inconclusive')
                self.assertEqual(ctx.reads, [])

    def test_missing_duplicate_unrelated_and_prose_links_rejected_before_reads(self):
        for defect in ('missing-repairs', 'duplicate-repair', 'wrong-repair', 'missing-link', 'wrong-link', 'prose-only', 'duplicate-link', 'malformed-link', 'study', 'task'):
            ctx = context(); row = ctx.packet['repairs'][0]
            if defect == 'missing-repairs': ctx.packet.pop('repairs')
            if defect == 'duplicate-repair': ctx.packet['repairs'].append(copy.deepcopy(row))
            if defect == 'wrong-repair': row['id'] = 'other'
            if defect == 'missing-link': row['incident'] = {}
            if defect == 'wrong-link': row['incident']['failed_turn_id'] = 'other'
            if defect == 'prose-only': row['incident'] = dict(reason='failed_turn_id: ' + tool.DISPATCH)
            if defect == 'duplicate-link': ctx.packet['repairs'].append(dict(id='other', incident=dict(failed_turn_id=tool.DISPATCH)))
            if defect == 'malformed-link': row['incident'] = [tool.DISPATCH]
            if defect == 'study': row['study_id'] = 'other'
            if defect == 'task': row['task_id'] = 'other'
            with self.subTest(defect=defect):
                self.assertEqual(tool.collect(ctx)['status'], 'inconclusive')
                self.assertEqual(ctx.reads, [])

    def test_unrelated_repairs_cannot_supply_link_but_do_not_hide_valid_link(self):
        ctx = context()
        ctx.packet['repairs'].insert(0, dict(id='other', incident=dict(failed_turn_id='unrelated')))
        self.assertEqual(tool.collect(ctx)['status'], 'structural-diagnostics')
        ctx = context(); ctx.packet['repairs'][0]['incident'] = {}
        ctx.packet['repairs'].append(dict(id='other', incident=dict(failed_turn_id=tool.DISPATCH)))
        self.assertEqual(tool.collect(ctx)['status'], 'inconclusive')
        self.assertEqual(ctx.reads, [])

    def test_original_dispatch_binding_and_artifact_index_are_still_required(self):
        for defect in ('missing', 'duplicate', 'study', 'task', 'index', 'artifact-duplicate', 'alias', 'bound'):
            ctx = context(); dispatch = ctx.packet['dispatches'][0]
            if defect == 'missing': ctx.packet['dispatches'] = []
            if defect == 'duplicate': ctx.packet['dispatches'].append(copy.deepcopy(dispatch))
            if defect == 'study': dispatch['study_id'] = 'other'
            if defect == 'task': dispatch['task_id'] = 'other'
            if defect == 'index': ctx.packet['artifacts'][0]['sha256'] = 'a' * 64
            if defect == 'artifact-duplicate': ctx.refs.append(copy.deepcopy(ctx.refs[0]))
            if defect == 'alias': ctx.refs[1]['id'] = ctx.refs[0]['id']
            if defect == 'bound': ctx.refs[0]['byte_count'] = retained.MAX_BYTES + 1
            with self.subTest(defect=defect):
                self.assertEqual(tool.collect(ctx)['status'], 'inconclusive')
                self.assertEqual(ctx.reads, [])

    def test_original_hash_request_and_receipt_checks_preserved(self):
        for defect in ('hash', 'request', 'provider', 'receipt', 'provenance'):
            ctx = context()
            if defect == 'hash': ctx.values['logical.json'] += b' '
            if defect == 'request': ctx.packet['dispatches'][0]['request_sha256'] = 'a' * 64
            if defect == 'provider':
                request = json.loads(ctx.values['provider-request.json']); request['model'] = 'other'
                ctx.replace('provider-request.json', retained.canonical(request))
            if defect == 'receipt':
                receipt = json.loads(ctx.values['receipt.json']); receipt['context_transport']['original_logical_sha256'] = 'b' * 64
                ctx.replace('receipt.json', retained.canonical(receipt))
            if defect == 'provenance': ctx.packet['dispatches'][0]['provenance']['provider_calls'] = 2
            with self.subTest(defect=defect):
                self.assertEqual(tool.collect(ctx)['status'], 'inconclusive')

    def test_incomplete_failed_and_malformed_streams_never_reconcile(self):
        for raw, expected in ((stream('failed'), 'terminal-failed-observed'), (stream('incomplete'), 'inconclusive'),
                              (stream()[:-1], 'inconclusive'), (stream() + stream(), 'inconclusive'),
                              (frame('response.created', 0, dict(id='private-response')), 'inconclusive'),
                              (b'data: {broken}\n\n', 'inconclusive')):
            with self.subTest(expected=expected, size=len(raw)):
                report = tool.collect(context(raw))
                self.assertEqual(report['stream']['observation'], expected)
                self.assertFalse(report['reconciled'])
                self.assertFalse(report['replacement_admitted'])

    def test_fresh_originals_each_time_and_persistence_holds_propagate(self):
        ctx = context(); first = tool.collect(ctx)
        ctx.replace('provider-response.bin', stream('failed'))
        second = tool.collect(ctx)
        self.assertEqual(len(ctx.reads), 8)
        self.assertNotEqual(first['sources']['provider-response.bin']['sha256'], second['sources']['provider-response.bin']['sha256'])
        with patch.object(ctx, 'original', side_effect=Hold('Retain uncertainty', 'reconciling')):
            with self.assertRaises(Hold): tool.collect(ctx)
        with patch.object(ctx, 'original', side_effect=ValueError('PRIVATE ERROR')):
            report = tool.collect(ctx)
        self.assertEqual(report['reason'], 'original-unavailable')
        self.assertNotIn('PRIVATE ERROR', json.dumps(report))

    def test_predecessor_still_rejects_repair_scope(self):
        ctx = context()
        self.assertEqual(retained.collect(ctx)['reason'], 'identity-unavailable')
        self.assertEqual(ctx.reads, [])


if __name__ == '__main__':
    unittest.main()
