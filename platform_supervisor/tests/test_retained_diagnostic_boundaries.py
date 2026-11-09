"""Malformed evidence stays diagnostic-only and cannot settle uncertain calls."""
import copy
import json
import types
import unittest
from unittest.mock import patch

from platform_supervisor import capability_retained_provider_outcome_diagnostics as tool
from platform_supervisor.common import Hold
from platform_supervisor.tests.test_retained_provider_outcome_diagnostics import Context, frame, stream


def event(value): return b'data: ' + tool.canonical(value) + b'\n\n'
def response(status='completed', **changes): return dict(dict(id='synthetic-response', model='gpt-6.1-sol', status=status), **changes)


class DiagnosticBoundaries(unittest.TestCase):
    def facts(self, raw): return tool.stream_facts(raw, {'model': 'gpt-6.1-sol'})
    def uncertain(self, result):
        self.assertFalse(result['reconciled']); self.assertFalse(result['replacement_admitted']); self.assertFalse(result['scientific_completion'])

    def test_json_duplicate_keys_nonfinite_values_and_unsupported_logical_messages_are_rejected(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError): tool.strict_json(raw)
        for change in [dict(modelId='other-model'), dict(builder={}), dict(messages=[dict(role='system', content=[dict(text='Untrusted')])]), dict(messages=[dict(role='user', content=[dict(image='forbidden')])])]:
            logical = dict(Context().logical, **change)
            with self.subTest(change=change), self.assertRaises(ValueError): tool.provider_request(logical)

    def test_stream_bounds_are_explicit_and_do_not_turn_missing_or_invalid_frames_into_terminal_proof(self):
        self.assertEqual(self.facts(b'x' * (tool.MAX_BYTES + 1))['reason'], 'byte-bound-exceeded')
        self.assertEqual(self.facts(b': comment\n\n' * (tool.MAX_EVENTS + 1))['reason'], 'event-bound-exceeded')
        for raw in [b'data: {}\r\n', b'data: {}\r\n\n', b'data: {}\r\n\r', b'not-a-field\n\n', b'unsupported: value\n\n', b'event: response.created\n\n', b'data: [DONE]\n\n', b'data: []\n\n']:
            with self.subTest(raw=raw): self.assertEqual(self.facts(raw)['observation'], 'inconclusive')

    def test_done_requires_a_terminal_and_prohibits_later_data_while_comment_and_metadata_only_frames_are_safe(self):
        raw = b': heartbeat\nid: original-sse-id\nretry: 1000\n\n' + stream() + b'data: [DONE]\n\n'; facts = self.facts(raw); self.assertEqual(facts['observation'], 'terminal-completed-observed'); self.assertEqual(facts['events'], 2); self.assertTrue(facts['frame_complete'])
        for bad in [raw + event({'type': 'response.created'}), event({'type': 'response.created'}) + b'data: [DONE]\n\n', b'event: one\nevent: two\ndata: {"type":"one"}\n\n']:
            with self.subTest(raw=bad[-80:]): self.assertEqual(self.facts(bad)['observation'], 'inconclusive')

    def test_sequences_response_types_and_identity_conflicts_remain_inconclusive_without_private_values_in_reports(self):
        initial = frame('response.created', 0, response('in_progress'))
        variants = [event(dict(type='response.created', sequence_number=True)), event(dict(type='response.created', sequence_number=-1)), frame('response.created', 1, response('in_progress')), initial + event(dict(type='response.completed', response=response())), event(dict(type='response.created', response=[])), event(dict(type='response.created', response={'id': ''})), event(dict(type='response.created', response={'id': 42})), event(dict(type='response.created', response={'id': 'x' * 201})), initial + frame('response.completed', 1, response(id='different')), initial + frame('response.completed', 1, response(model='different-model')), event(dict(type=42)), frame('response.completed', 0, response(status='failed'))]
        for raw in variants:
            with self.subTest(raw=raw[:100]):
                facts = self.facts(raw); self.assertEqual(facts['observation'], 'inconclusive'); self.assertFalse(facts['frame_complete']); self.assertNotIn('different-model', json.dumps(facts))

    def test_error_terminals_and_missing_response_identity_are_observations_never_completion_or_reconciliation(self):
        without = self.facts(event(dict(type='error', error={'code': 'original-unavailable'}))); self.assertEqual(without['reason'], 'response-identity-unavailable'); self.assertTrue(without['frame_complete'])
        with_id = self.facts(event(dict(type='error', response_id='synthetic-response', error={'code': 'original-unavailable'}))); self.assertEqual(with_id['reason'], 'incomplete-or-error-terminal'); self.assertEqual(with_id['observation'], 'inconclusive')
        terminal_missing = self.facts(event(dict(type='response.created', response_id='synthetic-response'))); self.assertEqual(terminal_missing['reason'], 'terminal-unavailable')

    def test_output_items_and_text_must_match_terminal_identity_and_complete_content(self):
        item = dict(id='synthetic-item', type='message', content=[dict(type='output_text', text='Original retained text')]); initial = frame('response.created', 0, response('in_progress')); added = frame('response.output_item.added', 1, output_index=0, item=item); text = frame('response.output_text.delta', 2, output_index=0, item_id=item['id'], delta='Original retained text'); done = frame('response.output_item.done', 3, output_index=0, item=item); complete = frame('response.completed', 4, response(output=[item]))
        facts = self.facts(initial + added + text + done + complete); self.assertEqual(facts['observation'], 'terminal-completed-observed'); self.assertEqual(facts['terminal_events'], 1)
        variants = [frame('response.output_item.done', 0, output_index=True, item=item), frame('response.output_item.done', 0, output_index=0, item=[]), initial + added + frame('response.output_item.done', 2, output_index=0, item=dict(item, id='changed')), frame('response.output_item.done', 0, output_index=0, item=item) + frame('response.output_item.done', 1, output_index=0, item=item), frame('response.output_text.done', 0, output_index=0, item_id='unindexed', text='private'), initial + added + done.replace(b'"sequence_number":3', b'"sequence_number":2') + frame('response.completed', 3, response(output=[dict(item, content=[])])), initial + added + frame('response.completed', 2, response(output={})), initial + added + frame('response.completed', 2, response(output=[]))]
        for raw in variants:
            with self.subTest(raw=raw[:100]): self.assertEqual(self.facts(raw)['observation'], 'inconclusive')

    def test_original_availability_requires_indexed_hash_verified_bounded_bytes_and_propagates_persistence_holds(self):
        ctx = Context(); ref = ctx.refs[0]; original = tool.original_fact(ctx, ctx.packet, ref['id'], ref['sha256']); self.assertTrue(original['available']); self.assertEqual(original['reason'], 'hash-verified-bytes-only')
        self.assertEqual(tool.original_fact(ctx, ctx.packet, None, None)['reason'], 'reference-unavailable')
        self.assertEqual(tool.original_fact(ctx, ctx.packet, 'invalid', ref['sha256'])['reason'], 'invalid-reference')
        self.assertEqual(tool.original_fact(ctx, {}, ref['id'], ref['sha256'])['reason'], 'original-not-indexed')
        for raw in ['not bytes', b'x' * (tool.MAX_BYTES + 1)]:
            with patch.object(ctx, 'original', return_value=raw): self.assertEqual(tool.original_fact(ctx, ctx.packet, ref['id'], ref['sha256'])['reason'], 'original-byte-bound')
        failure = Hold('Original retained read remains uncertain', 'reconciling')
        with patch.object(ctx, 'original', side_effect=failure), self.assertRaises(Hold) as caught: tool.original_fact(ctx, ctx.packet, ref['id'], ref['sha256'])
        self.assertIs(caught.exception, failure)

    def native(self):
        ctx = Context(); dispatch = ctx.packet['dispatches'][0]; ref = ctx.refs[0]
        ctx.packet['native_calls'] = [dict(id='00000000-0000-4000-8000-000000000079', dispatch_id=dispatch['id'], number=1, status='unknown', request_artifact_id=ref['id'], request_sha256=ref['sha256'], response_artifact_id=None, response_sha256=None)]
        return ctx

    def test_native_calls_reject_mismatched_dispatch_before_reading_originals_and_sidecar_names_are_exact(self):
        for case in ['invalid-call', 'missing-dispatch', 'wrong-study']:
            with self.subTest(case=case):
                ctx = self.native(); call = ctx.packet['native_calls'][0]
                if case == 'invalid-call': call['id'] = 'invalid'
                if case == 'missing-dispatch': call['dispatch_id'] = '00000000-0000-4000-8000-000000000999'
                if case == 'wrong-study': call['study_id'] = 'different-assessment'
                report = tool.collect(ctx); self.assertEqual(report['calls'][0]['reason'], 'call-or-dispatch-identity-mismatch'); self.assertEqual(ctx.reads, []); self.uncertain(report)
        ctx = self.native(); ctx.packet['native_calls'][0]['number'] = True; report = tool.collect(ctx); self.assertFalse(report['calls'][0]['evidence']['cleanup']['available']); self.uncertain(report)

    def test_collection_missing_alias_conflicting_indexes_and_bad_transport_receipts_never_admit_replacements(self):
        for case, reason in [('source-kind', 'unresolved-call-or-dispatch-unavailable'), ('missing-dispatch', 'dispatch-ambiguous-or-missing'), ('ignored-ref', 'original-unavailable'), ('index-conflict', 'index-conflict'), ('alias', 'artifact-alias-conflict'), ('byte-bound', 'original-byte-bound'), ('receipt-mismatch', 'receipt-provenance-mismatch'), ('receipt-transport', 'receipt-transport-unconfirmed'), ('malformed', 'malformed-evidence')]:
            with self.subTest(case=case):
                ctx = Context()
                if case == 'source-kind': ctx.packet['blocker']['source_kind'] = 'repair'
                if case == 'missing-dispatch': ctx.packet['dispatches'] = []
                if case == 'ignored-ref': ctx.refs[0]['name'] = 'unrelated-original.json'
                if case == 'index-conflict': ctx.packet['artifacts'].append(dict(ctx.packet['artifacts'][0], sha256='0' * 64))
                if case == 'alias': ctx.refs[1]['id'] = ctx.refs[0]['id']
                if case == 'byte-bound': ctx.refs[0]['byte_count'] = True
                if case == 'receipt-mismatch': ctx.packet['dispatches'][0]['provenance']['provider_calls'] = 2
                if case == 'receipt-transport':
                    receipt = json.loads(ctx.values['receipt.json']); receipt['provider_calls'] = 0; ctx.replace('receipt.json', tool.canonical(receipt)); ctx.packet['dispatches'][0]['provenance']['provider_calls'] = 0
                if case == 'malformed': ctx.replace('receipt.json', b'[]')
                report = tool.collect(ctx); self.assertEqual(report['reason'], reason); self.uncertain(report)

    def test_valid_nonplanning_assistant_messages_can_be_inspected_without_exposing_original_prompt_or_content(self):
        ctx = Context(); ctx.logical['messages'].append(dict(role='assistant', content=[dict(text='Synthetic prior assistant response')])); logical = tool.canonical(ctx.logical); ctx.replace('logical.json', logical); ctx.replace('provider-request.json', tool.canonical(tool.provider_request(ctx.logical))); ctx.packet['dispatches'][0]['request_sha256'] = tool.sha(logical)
        receipt = json.loads(ctx.values['receipt.json']); receipt['context_transport']['original_logical_sha256'] = tool.sha(logical); ctx.replace('receipt.json', tool.canonical(receipt)); report = tool.collect(ctx); self.assertEqual(report['status'], 'structural-diagnostics'); self.assertNotIn('Synthetic prior assistant response', json.dumps(report)); self.uncertain(report)

    def test_malformed_outer_evidence_and_nested_holds_are_not_turned_into_successful_diagnostics(self):
        result = tool.collect(types.SimpleNamespace(packet=None)); self.assertEqual(result['reason'], 'malformed-evidence'); self.uncertain(result)
        ctx = Context(); failure = Hold('Original persistence outcome unconfirmed', 'reconciling')
        with patch.object(ctx, 'original', side_effect=failure), self.assertRaises(Hold) as caught: tool.collect(ctx)
        self.assertIs(caught.exception, failure)


if __name__ == '__main__': unittest.main()
