"""Text-only provider transport and retained stream consistency regressions.

All wire bytes are local fixtures. No credential, provider or network call runs.
"""
import copy
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomy_model as model
import siwc_protocol as wire
from workflow import canonical
from test_autonomy import answer as base_answer


def answer(): return base_answer('assess')
def parsed_answer(): return dict(answer(), reason=model.REASONS['evidence-incomplete'])
def frame(value): return b'data: ' + canonical(value) + b'\n\n'
def message(text=None): return dict(id='original-message', type='message', role='assistant', status='completed', content=[dict(type='output_text', text=text if text is not None else canonical(answer()).decode())])
def terminal(output=None): return dict(model=wire.MODEL, status='completed', usage=dict(input_tokens=0, output_tokens=2, total_tokens=2), output=[message()] if output is None else output)
def completed(value): return dict(type='response.completed', response=value)


class ProtocolBoundaries(unittest.TestCase):
    def setUp(self): self.enterContext(patch.object(wire.time, 'monotonic', return_value=100))

    def read(self, raw, transport=wire.read_stream, maximum=500000):
        source, sink = io.BytesIO(raw), io.BytesIO()
        try: return transport(source, sink, 200, maximum)
        finally: self.retained = sink.getvalue()

    def test_logical_requests_remain_text_only_and_do_not_mutate_frozen_controller_context_or_schema(self):
        request = model.request('Original server-owned policy', dict(task='planning', initial_prompt='Original question')); request['modelId'] = wire.MODEL; request['messages'].append(dict(role='assistant', content=[{'text': 'Original prior reply'}])); original = copy.deepcopy(request)
        value = wire.response_request(request); self.assertEqual(request, original); self.assertFalse(value['store']); self.assertTrue(value['stream']); self.assertEqual(value['tools'], []); self.assertEqual(value['input'][0]['role'], 'developer'); self.assertEqual(value['input'][-1], dict(role='assistant', content='Original prior reply')); self.assertIn('Original server-owned policy', value['instructions'])
        request['modelId'] = 'silent-substitution'
        with self.assertRaisesRegex(ValueError, 'Unexpected logical model'): wire.response_request(request)
        request = copy.deepcopy(original); request['messages'][0]['role'] = 'developer'
        with self.assertRaisesRegex(ValueError, 'Only text'): wire.response_request(request)
        request = copy.deepcopy(original); request['messages'][0]['content'] = [dict(image='forbidden')]
        with self.assertRaisesRegex(ValueError, 'Only text'): wire.response_request(request)

    def test_usage_preserves_zero_and_missing_fields_without_accepting_boolean_negative_or_string_counters(self):
        self.assertEqual(wire.usage({'usage': dict(input_tokens=0, output_tokens=None, total_tokens=True)}), dict(inputTokens=0, outputTokens=None, totalTokens=None))
        self.assertEqual(wire.usage({'usage': dict(input_tokens='1', output_tokens=-1)}), dict(inputTokens=None, outputTokens=None, totalTokens=None)); self.assertIsNone(wire.usage([])); self.assertIsNone(wire.usage({}))
        for code, status, expected in [('rate_limit_exceeded', None, 'paused-quota'), ('other', 429, 'paused-quota'), ('subscription_sharing_invalid_user', None, 'paused-auth'), ('other', 401, 'paused-auth'), (None, None, 'failed')]:
            failure = wire.failure(code, status, {'inputTokens': 0}); self.assertEqual(failure.status, expected); self.assertEqual(failure.usage['inputTokens'], 0); self.assertTrue(failure.code)

    def test_complete_terminal_bytes_survive_fragmentation_keepalive_crlf_and_transport_timeout_updates(self):
        raw = b': keepalive\r\n\r\n' + frame(completed(terminal())).replace(b'\n', b'\r\n') + b'data: [DONE]\n\n'
        source = io.BytesIO(raw); socket = Mock(); response = types.SimpleNamespace(fp=types.SimpleNamespace(raw=types.SimpleNamespace(_sock=socket)), read1=lambda count: source.read1(min(count, 7))); sink = io.BytesIO()
        result = wire.read_terminal_stream(response, sink, 200, 500000); self.assertEqual(result, terminal()); self.assertEqual(sink.getvalue(), raw); socket.settimeout.assert_called_with(65)

    def test_terminal_is_required_and_received_original_bytes_are_retained_before_parse_or_size_failures(self):
        cases = [(b'data: broken JSON\n\n', 'malformed-provider-stream'), (frame(dict(type='response.created')), 'incomplete-provider-stream'), (b'data: {"type":"response.created"}', 'incomplete-provider-stream'), (b'data: {"type":"response.created"}\n', 'incomplete-provider-stream'), (frame(completed(dict(terminal(), status='in_progress'))), 'invalid-terminal-response'), (frame(completed(terminal())) * 2, 'invalid-terminal-response')]
        for raw, code in cases:
            with self.subTest(code=code), self.assertRaises(wire.StreamFailure) as caught: self.read(raw, wire.read_terminal_stream)
            self.assertEqual(caught.exception.code, code); self.assertEqual(self.retained, raw)
        raw = frame(completed(terminal()))
        with self.assertRaises(wire.StreamFailure) as caught: self.read(raw, wire.read_terminal_stream, maximum=10)
        self.assertEqual(caught.exception.code, 'provider-response-byte-limit'); self.assertEqual(self.retained, raw[:11])
        with self.assertRaises(wire.StreamFailure) as caught: wire.read_terminal_stream(io.BytesIO(raw), io.BytesIO(), 99, 500000)
        self.assertEqual(caught.exception.code, 'provider-wall-limit')

    def test_terminal_failure_and_incomplete_events_keep_observed_usage_and_never_infer_success_from_partial_text(self):
        for kind, code, status in [('response.failed', 'rate_limit_exceeded', 'paused-quota'), ('error', 'subscription_sharing_invalid_user', 'paused-auth'), ('response.incomplete', 'response.incomplete', 'unknown')]:
            with self.subTest(kind=kind):
                value = dict(usage={'input_tokens': 0}); event = dict(type=kind, response=value)
                if kind != 'response.incomplete': value['error'] = {'code': code}
                raw = frame(event)
                with self.assertRaises(wire.StreamFailure) as caught: self.read(raw)
                self.assertEqual(caught.exception.code, code); self.assertEqual(caught.exception.status, status); self.assertEqual(caught.exception.usage['inputTokens'], 0); self.assertEqual(self.retained, raw)

    def test_malformed_event_shapes_are_typed_uncertain_failures_and_retain_original_bytes(self):
        for event in [[], ['not an event'], dict(type='response.completed', response=['not an object']), dict(type='error', error='unstructured error')]:
            raw = frame(event)
            with self.subTest(event=event), self.assertRaises(wire.StreamFailure) as caught: self.read(raw)
            self.assertEqual(caught.exception.code, 'malformed-provider-stream'); self.assertEqual(caught.exception.status, 'unknown'); self.assertEqual(self.retained, raw)

    def test_structured_response_accepts_one_bounded_assistant_decision_and_rejects_substitution_tools_refusal_and_extra_text(self):
        value = terminal(); value['output'].insert(0, dict(type='reasoning')); original = copy.deepcopy(value); response = wire.structured_response(value); self.assertEqual(model.parse(response), parsed_answer()); self.assertEqual(value, original); self.assertEqual(response['usage']['inputTokens'], 0)
        cases = [dict(value, model='other-model'), dict(value, output=[dict(type='function_call')]), dict(value, output=[dict(type='message', role='user')]), dict(value, output=[dict(message(), content=[dict(type='refusal')])]), dict(value, output=[]), dict(value, output=[message(), message()]), dict(value, output=[message('x' * 100001)])]
        for candidate in cases:
            with self.subTest(candidate=str(candidate)[:100]), self.assertRaises(ValueError): wire.structured_response(candidate)

    def events(self):
        item = message(); text = item['content'][0]['text']; split = len(text) // 2
        return [dict(type='response.output_item.added', output_index=0, item=dict(item, status='in_progress')), dict(type='response.output_text.delta', output_index=0, item_id=item['id'], content_index=0, delta=text[:split]), dict(type='response.output_text.delta', output_index=0, item_id=item['id'], content_index=0, delta=text[split:]), dict(type='response.output_text.done', output_index=0, item_id=item['id'], content_index=0, text=text), dict(type='response.output_item.done', output_index=0, item=item)]

    def test_stream_reconstructs_the_exact_completed_items_and_crosschecks_terminal_output_and_each_delta(self):
        events = self.events(); raw = b'\n' + b''.join(map(frame, events + [completed(terminal([]))])) + b'data: [DONE]\n\n'; value = self.read(raw); self.assertEqual(value, terminal()); self.assertEqual(self.retained, raw); self.assertEqual(model.parse(wire.structured_response(value)), parsed_answer())
        raw = b''.join(map(frame, events + [completed(terminal())])); self.assertEqual(self.read(raw), terminal())
        # Older compatible streams may supply the complete terminal output only.
        self.assertEqual(self.read(frame(completed(terminal()))), terminal())

    def test_output_identity_order_completion_and_content_disagreements_cannot_be_reconciled_as_a_success(self):
        for case, code in [('index', 'ambiguous-stream-output-index'), ('identity', 'ambiguous-stream-output-identity'), ('duplicate', 'duplicate-or-incomplete-stream-item'), ('incomplete', 'duplicate-or-incomplete-stream-item'), ('content-index', 'ambiguous-stream-content-identity'), ('delta-type', 'invalid-stream-text-delta'), ('delta-bound', 'stream-text-byte-limit'), ('text-duplicate', 'ambiguous-final-stream-text'), ('delta-disagrees', 'stream-delta-final-text-disagreement'), ('item-gap', 'missing-stream-output-item'), ('item-text', 'stream-item-text-disagreement'), ('unmatched-text', 'unmatched-final-stream-text'), ('terminal-text', 'terminal-stream-output-disagreement'), ('missing-items', 'missing-completed-stream-items')]:
            with self.subTest(case=case):
                events = self.events(); end = terminal([])
                if case == 'index': events[0]['output_index'] = True
                if case == 'identity': events[-1]['item']['id'] = 'different-message'
                if case == 'duplicate': events.append(copy.deepcopy(events[-1]))
                if case == 'incomplete': events[-1]['item']['status'] = 'in_progress'
                if case == 'content-index': events[1]['content_index'] = -1
                if case == 'delta-type': events[1]['delta'] = None
                if case == 'delta-bound': events[1]['delta'] = 'x' * 100001
                if case == 'text-duplicate': events.insert(4, copy.deepcopy(events[3]))
                if case == 'delta-disagrees': events[3]['text'] += 'changed'
                if case == 'item-gap':
                    for event in events: event['output_index'] = 1
                if case == 'item-text': events[-1]['item']['content'][0]['text'] += 'changed'
                if case == 'unmatched-text': events = [events[3]]
                if case == 'terminal-text': end = terminal([message('changed terminal decision')])
                if case == 'missing-items': events = []
                raw = b''.join(map(frame, events + [completed(end)]))
                with self.assertRaises(wire.StreamFailure) as caught: self.read(raw)
                self.assertEqual(caught.exception.code, code); self.assertEqual(self.retained, raw)

    def test_nontext_reasoning_items_are_retained_in_order_without_being_interpreted_as_assessment_text(self):
        reason = dict(id='reasoning-original', type='reasoning', status='completed', content=[dict(type='summary_text', text='Non-decision reasoning')]); item = message(); events = [dict(type='response.output_item.done', output_index=0, item=reason), dict(type='response.output_item.done', output_index=1, item=item), completed(terminal([]))]
        result = self.read(b''.join(map(frame, events))); self.assertEqual(result['output'], [reason, item]); self.assertEqual(model.parse(wire.structured_response(result)), parsed_answer())


if __name__ == '__main__': unittest.main()
