"""Synthetic qualification only; these fixtures do not prove live spool recovery."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from platform_supervisor import capabilities
from platform_supervisor.common import Hold
from platform_supervisor import capability_original_native_recovery_diagnostics as tool
from platform_supervisor.tests.test_retained_provider_outcome_diagnostics import stream


class Context:
    def __init__(self):
        self.raw = {}; self.reads = []
        self.packet = dict(platform_task_id='bound-platform-task', assessment_id=tool.ASSESSMENT,
            assessment_task_id=tool.TASK, blocker=dict(id=tool.INCIDENT, source_id=tool.DISPATCH,
            source_kind='dispatch'), artifacts=[], dispatches=[dict(id=tool.DISPATCH)],
            native_calls=[dict(id=tool.CALL, dispatch_id=tool.DISPATCH, number=79,
                              request_sha256=tool.REQUEST, request_artifact_id=None)])
        self.observation = dict(version='original-native-spool-observation-v1', **tool.identity(),
            originals={}, verified_missing=[], scan=dict(eligible=False, transport_active=False,
            cleanup_uncertain=False), last_scan=dict(kind='exception', detail='PRIVATE token=do-not-leak'))
    def add(self, name, raw):
        ident = '00000000-0000-4000-8000-%012d' % (len(self.raw)+1)
        ref = dict(id=ident, sha256=tool.sha(raw), byte_count=len(raw))
        self.raw[ident] = raw; self.packet['artifacts'].append(ref)
        return ref
    def publish(self):
        self.packet['native_recovery_diagnostics'] = self.add('observation', tool.canonical(self.observation))
    def original(self, ident):
        self.reads.append(ident)
        return self.raw[ident]
    def complete(self):
        request = tool.canonical(dict(model='gpt-6.1-sol', store=False, stream=True))
        digest = tool.sha(request)
        self.observation['request_sha256'] = digest
        self.packet['native_calls'][0]['request_sha256'] = digest
        refs = self.observation['originals']
        refs['request'] = self.add('request', request)
        self.packet['native_calls'][0]['request_artifact_id'] = refs['request']['id']
        response = stream()
        refs['response'] = self.add('response', response)
        self.receipt = dict(version='builder-native-call-reconciliation-v1', call_id=tool.CALL,
            dispatch_id=tool.DISPATCH, task_id=tool.TASK, study_id=tool.ASSESSMENT,
            request_sha256=digest, response_filename='call-79-response.bin', status='complete',
            response_received=True, http_status=200, response_sha256=tool.sha(response))
        refs['completion'] = self.add('completion', tool.canonical(self.receipt))
        return digest


class Diagnostics(unittest.TestCase):
    def test_literal_descriptor_and_missing_prerequisite(self):
        path = Path(tool.__file__)
        self.assertEqual(capabilities.descriptor(path.read_bytes(), path.name)['revision'], 1)
        ctx = Context(); before = copy.deepcopy(ctx.packet)
        report = tool.collect(ctx)
        self.assertEqual(report['reason'], 'private-spool-observation-not-in-trusted-packet')
        self.assertEqual(report['spool'], 'unavailable')
        self.assertIn('remaining_prerequisite', report)
        self.assertEqual(ctx.reads, [])
        self.assertEqual(ctx.packet, before)

    def test_complete_matching_originals_no_mutation_or_external_access(self):
        ctx = Context(); digest = ctx.complete(); ctx.publish()
        before = copy.deepcopy(ctx.packet)
        with patch.object(tool, 'REQUEST', digest), patch('socket.socket', side_effect=AssertionError('network')), patch('builtins.open', side_effect=AssertionError('filesystem')):
            report = tool.collect(ctx)
        self.assertEqual(report['status'], 'observed')
        self.assertEqual(report['completion'], 'hash-and-identity-verified')
        self.assertEqual(report['stream']['observation'], 'terminal-completed-observed')
        self.assertEqual(report['last_scan']['kind'], 'exception')
        self.assertEqual(ctx.packet, before)
        self.assertEqual(len(ctx.reads), 4)
        for key in ('reconciled', 'replacement_admitted', 'scientific_completion', 'assessment_resumed'):
            self.assertFalse(report[key])
        self.assertNotIn('PRIVATE', json.dumps(report))
        self.assertLess(len(json.dumps(report)), 8000)

    def test_missing_completion_and_response_are_not_unavailable(self):
        for component in ('completion', 'response'):
            ctx = Context(); digest = ctx.complete()
            del ctx.observation['originals'][component]
            ctx.observation['verified_missing'] = [component]
            ctx.publish()
            with patch.object(tool, 'REQUEST', digest):
                report = tool.collect(ctx)
            self.assertEqual(report[component], 'verified-missing')
            self.assertNotEqual(report['status'], 'observed')
        ctx = Context(); ctx.publish()
        self.assertEqual(tool.collect(ctx)['completion'], 'unavailable')

    def test_cross_incident_and_unrelated_report_rejected_before_reads(self):
        for field, value in (('assessment_task_id', '2d8aa97a-5c01-50ea-a32f-c5025aaf3f9c'),
                             ('assessment_id', 'other')):
            ctx = Context(); ctx.packet[field] = value
            self.assertEqual(tool.collect(ctx)['reason'], 'identity-unavailable')
            self.assertEqual(ctx.reads, [])
        ctx = Context(); ctx.observation['incident_id'] = 'ef881bcd-9310-413a-be6b-53ef75ba59dc'; ctx.publish()
        self.assertEqual(tool.collect(ctx)['reason'], 'observation-identity-mismatch')
        for key in ('call_id', 'dispatch_id', 'request_sha256'):
            ctx = Context(); ctx.observation[key] = 'other'; ctx.publish()
            self.assertEqual(tool.collect(ctx)['reason'], 'observation-identity-mismatch')

    def test_call_dispatch_hash_and_sidecar_conflicts(self):
        for key in ('dispatch_id', 'request_sha256', 'number'):
            ctx = Context(); ctx.packet['native_calls'][0][key] = 'wrong'
            self.assertEqual(tool.collect(ctx)['reason'], 'call-identity-mismatch')
        for key in ('call_id', 'dispatch_id', 'response_sha256'):
            ctx = Context(); digest = ctx.complete(); ctx.receipt[key] = 'wrong'
            ctx.observation['originals']['completion'] = ctx.add('bad', tool.canonical(ctx.receipt)); ctx.publish()
            with patch.object(tool, 'REQUEST', digest):
                self.assertEqual(tool.collect(ctx)['status'], 'mismatch')
        ctx = Context(); ctx.publish(); ident = ctx.packet['native_recovery_diagnostics']['id']
        ctx.raw[ident] += b' '
        self.assertEqual(tool.collect(ctx)['reason'], 'original-hash-or-size-mismatch')

    def test_incomplete_stream_is_not_completion_or_recovery(self):
        ctx = Context(); digest = ctx.complete()
        raw = stream()[:-1]
        ctx.observation['originals']['response'] = ctx.add('truncated', raw)
        ctx.receipt['response_sha256'] = tool.sha(raw)
        ctx.observation['originals']['completion'] = ctx.add('receipt', tool.canonical(ctx.receipt)); ctx.publish()
        with patch.object(tool, 'REQUEST', digest):
            report = tool.collect(ctx)
        self.assertEqual(report['stream']['observation'], 'inconclusive')
        self.assertEqual(report['stream']['reason'], 'truncated-frame')
        self.assertFalse(report['reconciled'])

    def test_scan_fences_source_fingerprint_and_redaction(self):
        for flag in ('transport_active', 'cleanup_uncertain'):
            ctx = Context(); ctx.observation['scan'][flag] = True
            raw = b'# exact installed source fixture only'
            ctx.observation['recovery_source'] = ctx.add('source', raw); ctx.publish()
            report = tool.collect(ctx)
            self.assertEqual(report['scan_eligibility'], 'active-transport-or-cleanup-uncertainty')
            self.assertEqual(report['recovery_source_sha256'], tool.sha(raw))
            self.assertNotIn('do-not-leak', json.dumps(report))
        ctx = Context(); ctx.observation['last_scan']['detail'] = 'x' * 8001; ctx.publish()
        self.assertEqual(tool.collect(ctx)['reason'], 'last-scan-malformed-or-bound')

    def test_size_index_ambiguity_and_collector_failure(self):
        ctx = Context(); ctx.publish()
        ctx.packet['native_recovery_diagnostics']['byte_count'] = 32001
        self.assertEqual(tool.collect(ctx)['reason'], 'original-size-bound')
        ctx = Context(); ctx.publish()
        ctx.packet['artifacts'].append(dict(ctx.packet['artifacts'][0], sha256='f'*64))
        self.assertEqual(tool.collect(ctx)['reason'], 'ambiguous-artifact-index')
        ctx = Context(); ctx.publish()
        with patch.object(ctx, 'original', side_effect=RuntimeError('private collector fault')):
            with self.assertRaises(RuntimeError): tool.collect(ctx)  # existing broker requests next revision
        with patch.object(ctx, 'original', side_effect=Hold('private uncertainty', 'reconciling')):
            with self.assertRaises(Hold): tool.collect(ctx)  # never swallow persistence uncertainty


if __name__ == '__main__':
    unittest.main()
