"""Synthetic evidence acquisition tests; fixtures are never production qualification."""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
from platform_supervisor import capabilities
from platform_supervisor import capability_failed_builder_call_evidence as tool
from platform_supervisor.common import Hold


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def checkpoint(records):
    sink = io.BytesIO()
    with zipfile.ZipFile(sink, 'w') as archive:
        archive.writestr('native-tool-history.json', encoded(records))
    return sink.getvalue()


class Context:
    def __init__(self):
        self.reads = []
        self.prior = dict(type='function_call', call_id='private-tool-id', name='exec', arguments='PRIVATE PROMPT /private/host sk-credential')
        self.request = encoded(dict(model='gpt-6.1-sol', input=[self.prior]))
        self.response = encoded(dict(error=dict(code='subscription_sharing_user_unavailable', message='PRIVATE RESEARCH secret')))
        self.request_sha = tool.sha(self.request); self.response_sha = tool.sha(self.response)
        self.receipt = dict(status='failed', http_status=503, response_received=True, provider_request_sent=True,
                            provider_calls=8, native_tool_cleanup=True, container_cleanup='stopped')
        self.side = dict(version='builder-native-call-reconciliation-v1', call_id=tool.CALL,
                         dispatch_id=tool.DISPATCH, study_id=tool.ASSESSMENT, task_id=tool.TASK,
                         request_sha256=self.request_sha, response_sha256=self.response_sha,
                         response_filename='call-8-response.bin', status='failed', http_status=503, response_received=True)
        self.values = {}; self.refs = []
        self.add('request', tool.REQUEST_ID, self.request)
        self.add('response', tool.RESPONSE_ID, self.response)
        self.add('call-8-completion.json', '00000000-0000-4000-8000-000000000001', encoded(self.side))
        self.add('checkpoint', '00000000-0000-4000-8000-000000000002', checkpoint([self.prior]))
        pointer = dict(artifact_id=self.refs[-1]['id'], sha256=self.refs[-1]['sha256'])
        self.receipt['checkpoint'] = pointer
        self.add('receipt.json', '00000000-0000-4000-8000-000000000003', encoded(self.receipt))
        self.add('completion.json', '00000000-0000-4000-8000-000000000004', encoded(dict(answer=None, receipt=self.receipt)))
        provenance = dict(self.receipt, artifacts=copy.deepcopy([r for r in self.refs if r['name'].endswith('.json')]),
                          completion_sha256=self.refs[-1]['sha256'])
        self.packet = dict(platform_task_id='synthetic-task', assessment_id=tool.ASSESSMENT,
                           assessment_task_id=tool.TASK, artifacts=copy.deepcopy(self.refs),
                           dispatches=[dict(id=tool.DISPATCH, provenance=provenance)], native_calls=[
                               dict(id=tool.CALL, dispatch_id=tool.DISPATCH, number=155, status='failed',
                                    http_status=503, response_received=True, request_artifact_id=tool.REQUEST_ID,
                                    request_sha256=self.request_sha, response_artifact_id=tool.RESPONSE_ID,
                                    response_sha256=self.response_sha)])
    def add(self, name, ident, raw):
        self.values[ident] = raw
        self.refs.append(dict(name=name, id=ident, sha256=tool.sha(raw), byte_count=len(raw)))
    def original(self, ident):
        self.reads.append(ident)
        return self.values[ident]
    def replace(self, name, raw):
        ref = next(r for r in self.refs if r['name'] == name)
        ident = ref['id']; self.values[ident] = raw
        for rows in (self.refs, self.packet['artifacts'], self.packet['dispatches'][0]['provenance']['artifacts']):
            for r in rows:
                if r['id'] == ident: r.update(sha256=tool.sha(raw), byte_count=len(raw))
        if name == 'checkpoint':
            self.packet['dispatches'][0]['provenance']['checkpoint']['sha256'] = tool.sha(raw)


class FailedBuilderEvidence(unittest.TestCase):
    def collect(self, ctx):
        with patch.multiple(tool, REQUEST_SHA=ctx.request_sha, RESPONSE_SHA=ctx.response_sha):
            return tool.collect(ctx)

    def test_literal_contract_and_pinned_target(self):
        path = Path(tool.__file__)
        pack = capabilities.descriptor(path.read_bytes(), path.name)
        self.assertEqual(pack['id'], 'failed-builder-call-evidence')
        self.assertEqual(pack['revision'], 1)
        self.assertEqual(tool.REQUEST_SHA, '1c62acb564fbfc93047b0a2742587855eed7df729af56fc420d91ff0c348fced')
        self.assertEqual(tool.RESPONSE_SHA, '26f8dd29ba2931fa849e1f2fe7d0e63f9aeb03f1286d9e10642cfb70e0ac1ce0')

    def test_read_only_bounded_redacted_503_is_not_nonexecution(self):
        ctx = Context(); before = copy.deepcopy(ctx.packet)
        with patch('socket.socket', side_effect=AssertionError('no network')), patch('builtins.open', side_effect=AssertionError('no filesystem')):
            report = self.collect(ctx)
        self.assertEqual(ctx.packet, before)
        self.assertEqual(len(ctx.reads), 6)
        self.assertEqual(report['status'], 'structural-diagnostics')
        self.assertEqual(report['ledger_call_number'], 155)
        self.assertEqual(report['dispatch_local_call_number'], 8)
        self.assertEqual(report['completion_transport']['http_status'], 503)
        self.assertTrue(report['completion_matches_ledger'])
        self.assertTrue(report['parent_completion_matches_receipt'])
        self.assertEqual(report['capture_ordering']['observation'], 'prefix-consistent')
        for key in ('non_execution_proven', 'reconciled', 'replacement_admitted', 'assessment_resumed', 'scientific_completion'):
            self.assertFalse(report[key])
        self.assertFalse(report['failed_transport_accounting_contract']['available'])
        raw = json.dumps(report)
        self.assertLess(len(raw.encode()), tool.MAX_OUTPUT)
        for text in ('PRIVATE', '/private/host', 'sk-credential', 'private-tool-id', 'subscription_sharing_user_unavailable'):
            self.assertNotIn(text, raw)

    def test_no_production_hash_bypass(self):
        ctx = Context()
        self.assertEqual(tool.collect(ctx)['reason'], 'call-hash-or-identity-conflict')
        self.assertEqual(ctx.reads, [])

    def test_missing_original_is_explicit_and_other_evidence_continues(self):
        ctx = Context(); del ctx.values[tool.REQUEST_ID]
        report = self.collect(ctx)
        self.assertIn(dict(evidence='request', reason='original-unavailable'), report['missing'])
        self.assertIn('response', report['sources'])
        self.assertEqual(report['capture_ordering']['observation'], 'unavailable')

    def test_unindexed_sidecar_is_not_read_from_provenance(self):
        ctx = Context(); ident = ctx.refs[2]['id']
        ctx.packet['artifacts'] = [r for r in ctx.packet['artifacts'] if r['id'] != ident]
        report = self.collect(ctx)
        self.assertNotIn(ident, ctx.reads)
        self.assertIn(dict(evidence='call-8-completion.json', reason='indexed-original-absent'), report['missing'])

    def test_out_of_scope_rejected_before_reads(self):
        for change in ('task', 'call', 'dispatch', 'number'):
            ctx = Context()
            if change == 'task': ctx.packet['assessment_task_id'] = 'other'
            elif change == 'call': ctx.packet['native_calls'][0]['call_id'] = 'other'
            elif change == 'dispatch': ctx.packet['native_calls'][0]['dispatch_id'] = 'other'
            else: ctx.packet['native_calls'][0]['number'] = 8
            self.assertEqual(self.collect(ctx)['status'], 'inconclusive')
            self.assertEqual(ctx.reads, [])

    def test_conflicting_index_and_ambiguous_named_artifacts(self):
        for kind in ('hash', 'name', 'call', 'duplicate-index'):
            ctx = Context()
            if kind == 'hash': ctx.packet['artifacts'].append(dict(ctx.refs[0], sha256='0'*64))
            elif kind == 'name': ctx.packet['dispatches'][0]['provenance']['artifacts'].append(copy.deepcopy(ctx.refs[2]))
            elif kind == 'call': ctx.packet['native_calls'].append(copy.deepcopy(ctx.packet['native_calls'][0]))
            else: ctx.packet['artifacts'].append(copy.deepcopy(ctx.refs[2]))
            self.assertEqual(self.collect(ctx)['status'], 'inconclusive')
            self.assertEqual(ctx.reads, [])

    def test_hash_mismatch_and_oversize_original_rejected(self):
        for raw in (b'changed', b'x'*(tool.MAX_BYTES+1)):
            ctx = Context(); ctx.values[tool.REQUEST_ID] = raw
            self.assertEqual(self.collect(ctx)['status'], 'inconclusive')

    def test_advertised_oversize_ref_not_read(self):
        ctx = Context(); ident = ctx.refs[2]['id']
        ctx.packet['dispatches'][0]['provenance']['artifacts'][0]['byte_count'] = tool.MAX_BYTES+1
        self.assertEqual(self.collect(ctx)['status'], 'inconclusive')
        self.assertNotIn(ident, ctx.reads)

    def test_malformed_sidecar_and_duplicate_keys(self):
        for raw in (b'{', b'[]', b'{"status":"failed","status":"complete"}'):
            ctx = Context(); ctx.replace('call-8-completion.json', raw)
            report = self.collect(ctx)
            self.assertNotIn('completion_transport', report)
            self.assertIn(dict(evidence='call-8-completion.json', reason='malformed-or-conflicting-document'), report['missing'])

    def test_conflicting_sidecar_binding_and_status(self):
        for key, value in (('call_id', 'other'), ('request_sha256', '0'*64), ('response_filename', 'call-155-response.bin'), ('status', 'PRIVATE')):
            ctx = Context(); ctx.replace('call-8-completion.json', encoded(dict(ctx.side, **{key: value})))
            report = self.collect(ctx)
            self.assertNotIn('completion_transport', report)
            self.assertNotIn('PRIVATE', json.dumps(report))

    def test_unknown_transport_and_incomplete_cleanup(self):
        ctx = Context(); ctx.packet['native_calls'][0].update(response_received=None, http_status=None, status='unknown')
        ctx.replace('receipt.json', encoded(dict(ctx.receipt, native_tool_cleanup=False, container_cleanup='unknown')))
        report = self.collect(ctx)
        self.assertNotIn('response_received', report['ledger_transport'])
        self.assertFalse(report['completion_matches_ledger'])
        self.assertFalse(report['cleanup_confirmed_in_receipt'])
        self.assertFalse(report['receipt_matches_parent'])
        self.assertFalse(report['non_execution_proven'])

    def test_checkpoint_ordering_mismatch_and_malformed_zip(self):
        for raw, expected in ((checkpoint([dict(type='function_call', call_id='different')]), 'checkpoint-ordering-mismatch'),
                              (b'not zip', 'checkpoint-history-unavailable-or-malformed')):
            ctx = Context(); ctx.replace('checkpoint', raw)
            report = self.collect(ctx)
            self.assertEqual(report['capture_ordering']['observation'], expected)
            self.assertFalse(report['capture_ordering']['temporal_order_verified'])

    def test_zip_unsafe_member_rejected(self):
        sink = io.BytesIO()
        with zipfile.ZipFile(sink, 'w') as archive:
            archive.writestr('../escape', b'private')
            archive.writestr('native-tool-history.json', b'[]')
        with self.assertRaises(ValueError): tool.history(sink.getvalue())

    def test_ambiguous_terminal_outcomes_never_settle(self):
        terminal = dict(id='private-response', model='gpt-6.1-sol', status='failed')
        event = dict(type='response.failed', response=terminal)
        raw = b'data: '+encoded(event)+b'\n\n'
        result = tool.stream_facts(raw+raw, dict(model='gpt-6.1-sol'))
        self.assertEqual(result['observation'], 'inconclusive')
        ctx = Context(); ctx.response = raw+raw; ctx.response_sha = tool.sha(ctx.response)
        ctx.replace('response', ctx.response)
        ctx.packet['native_calls'][0]['response_sha256'] = ctx.response_sha
        ctx.replace('call-8-completion.json', encoded(dict(ctx.side, response_sha256=ctx.response_sha)))
        report = self.collect(ctx)
        self.assertEqual(report['response']['observation'], 'inconclusive')
        self.assertFalse(report['reconciled'])

    def test_output_bounded_with_hostile_free_text_and_packet_bounds(self):
        ctx = Context(); ctx.packet['native_calls'][0]['reason'] = 'PRIVATE' * 100000
        ctx.packet['dispatches'][0]['provenance']['error_code'] = '/host/secret' * 100000
        report = self.collect(ctx)
        self.assertLess(len(encoded(report)), tool.MAX_OUTPUT)
        self.assertNotIn('PRIVATE', json.dumps(report))
        ctx.packet['artifacts'] *= 1000
        self.assertEqual(self.collect(ctx)['reason'], 'packet-bound')

    def test_contract_only_reports_supplied_data_not_installation(self):
        ctx = Context(); ctx.packet['recovery_contract'] = dict(failed_transport_accounting=dict(secret='PRIVATE'))
        contract = self.collect(ctx)['failed_transport_accounting_contract']
        self.assertTrue(contract['available']); self.assertFalse(contract['installed_verified'])
        self.assertNotIn('PRIVATE', json.dumps(contract))

    def test_uncertain_persistence_hold_propagates(self):
        ctx = Context()
        with patch.object(ctx, 'original', side_effect=Hold('unconfirmed', 'reconciling')):
            with self.assertRaises(Hold): self.collect(ctx)

    def test_existing_installed_scope_unchanged(self):
        from platform_supervisor import capability_retained_provider_outcome_diagnostics as previous
        self.assertEqual(previous.TASK, '2d8aa97a-5c01-50ea-a32f-c5025aaf3f9c')
        self.assertEqual(previous.DISPATCH, '59d0c555-871e-5971-9461-cdea2f0714cc')
        self.assertEqual(previous.CAPABILITY['revision'], 1)


if __name__ == '__main__': unittest.main()
