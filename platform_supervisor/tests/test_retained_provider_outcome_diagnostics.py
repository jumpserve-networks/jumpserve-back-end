"""Synthetic, private-content-free regressions for the installed collector contract."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from platform_supervisor import capability_retained_provider_outcome_diagnostics as tool
from platform_supervisor import capabilities


def frame(kind, sequence, response=None, **extra):
    value = dict(type=kind, sequence_number=sequence, **extra)
    if response is not None: value['response'] = response
    return b'event: ' + kind.encode() + b'\ndata: ' + tool.canonical(value) + b'\n\n'


def stream(status='completed'):
    initial = dict(id='private-response', model='gpt-6.1-sol', status='in_progress')
    terminal = dict(initial, status=status)
    return frame('response.created', 0, initial) + frame('response.' + status, 1, terminal)


class Context:
    def __init__(self, raw=None):
        self.reads = []
        self.logical = dict(modelId='gpt-6.1-sol', system=[dict(text='PRIVATE PROMPT')],
            messages=[dict(role='user', content=[dict(text='PRIVATE RESEARCH')])],
            toolConfig=dict(tools=[dict(toolSpec=dict(inputSchema=dict(json={}))) ]))
        logical = tool.canonical(self.logical)
        receipt = dict(model_id='gpt-6.1-sol', provider_calls=1, response_received=True,
                       context_transport=dict(original_logical_sha256=tool.sha(logical)))
        self.values = {'logical.json': logical,
                       'provider-request.json': tool.canonical(tool.provider_request(self.logical)),
                       'provider-response.bin': stream() if raw is None else raw,
                       'receipt.json': tool.canonical(receipt)}
        self.refs = [dict(name=name, id='00000000-0000-4000-8000-%012d' % i,
                         sha256=tool.sha(value), byte_count=len(value))
                     for i, (name, value) in enumerate(self.values.items(), 1)]
        self.packet = dict(platform_task_id='bound-platform-task', assessment_id=tool.ASSESSMENT,
            assessment_task_id=tool.TASK, blocker=dict(id=tool.INCIDENT, source_id=tool.DISPATCH, source_kind='dispatch'),
            artifacts=copy.deepcopy(self.refs), dispatches=[dict(id=tool.DISPATCH, request_sha256=tool.sha(logical),
            provenance=dict(artifacts=self.refs, **receipt))])
    def original(self, ident):
        self.reads.append(ident)
        ref = next(r for r in self.refs if r['id'] == ident)
        return self.values[ref['name']]
    def replace(self, name, value):
        self.values[name] = value
        for refs in (self.refs, self.packet['artifacts']):
            ref = next(r for r in refs if r['name'] == name)
            ref.update(sha256=tool.sha(value), byte_count=len(value))


class Diagnostics(unittest.TestCase):
    def test_literal_contract_is_valid_and_read_only(self):
        path = Path(tool.__file__)
        pack = capabilities.descriptor(path.read_bytes(), path.name)
        self.assertEqual(pack['revision'], 1)
        ctx = Context(); before = copy.deepcopy(ctx.packet)
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('builtins.open', side_effect=AssertionError('filesystem forbidden')):
            report = tool.collect(ctx)
        self.assertEqual(ctx.packet, before)
        self.assertEqual(len(ctx.reads), 4)
        self.assertEqual(report['stream']['observation'], 'terminal-completed-observed')
        self.assertFalse(report['reconciled'])
        self.assertEqual(report['sources']['provider-response.bin']['sha256'], tool.sha(ctx.values['provider-response.bin']))
        encoded = json.dumps(report)
        self.assertLess(len(encoded), 8000)
        for secret in ('PRIVATE PROMPT', 'PRIVATE RESEARCH', 'private-response'):
            self.assertNotIn(secret, encoded)

    def test_terminal_failure_and_incomplete_are_distinct(self):
        failed = tool.collect(Context(stream('failed')))['stream']
        self.assertEqual(failed['observation'], 'terminal-failed-observed')
        incomplete = tool.collect(Context(stream('incomplete')))['stream']
        self.assertEqual(incomplete['observation'], 'inconclusive')
        self.assertTrue(incomplete['frame_complete'])

    def test_overload_label_and_http_200_do_not_settle(self):
        ctx = Context(frame('response.created', 0, dict(id='private-response', model='gpt-6.1-sol')))
        receipt = json.loads(ctx.values['receipt.json']); receipt.update(error_code='server_is_overloaded', http_status=200)
        ctx.replace('receipt.json', tool.canonical(receipt))
        report = tool.collect(ctx)
        self.assertEqual(report['stream']['observation'], 'inconclusive')
        self.assertEqual(report['stream']['reason'], 'terminal-unavailable')

    def test_malformed_truncated_conflicting_and_out_of_order(self):
        variants = [b'data: {broken}\n\n', stream()[:-1], stream() + stream('failed'),
            frame('response.created', 0, dict(id='one')) + frame('response.completed', 2, dict(id='one', model='gpt-6.1-sol', status='completed')),
            frame('response.created', 0, dict(id='one')) + frame('response.completed', 1, dict(id='two', model='gpt-6.1-sol', status='completed')),
            b'event: response.failed\ndata: {"type":"response.completed"}\n\n',
            b'data: {"type":"error","type":"response.completed"}\n\n']
        for raw in variants:
            with self.subTest(raw=raw[:50]):
                self.assertEqual(tool.collect(Context(raw))['stream']['observation'], 'inconclusive')

    def test_bounds_and_missing_originals(self):
        for raw in (b'x' * (tool.MAX_BYTES + 1), b':comment\n\n' * (tool.MAX_EVENTS + 1)):
            report = tool.collect(Context(raw))
            self.assertNotIn('terminal-completed-observed', json.dumps(report))
        ctx = Context()
        with patch.object(ctx, 'original', side_effect=ValueError('PRIVATE FAILURE')):
            report = tool.collect(ctx)
        self.assertEqual(report['reason'], 'original-unavailable')
        self.assertNotIn('PRIVATE FAILURE', json.dumps(report))

    def test_identity_hash_and_index_conflicts(self):
        for defect in ('task', 'dispatch', 'duplicate', 'index', 'hash', 'request', 'receipt', 'provider'):
            ctx = Context()
            if defect == 'task': ctx.packet['assessment_task_id'] = 'other'
            if defect == 'dispatch': ctx.packet['dispatches'][0]['task_id'] = 'other'
            if defect == 'duplicate': ctx.refs.append(copy.deepcopy(ctx.refs[0]))
            if defect == 'index': ctx.packet['artifacts'][0]['sha256'] = 'b' * 64
            if defect == 'hash': ctx.values['logical.json'] += b' '
            if defect == 'request': ctx.packet['dispatches'][0]['request_sha256'] = 'c' * 64
            if defect == 'receipt':
                r = json.loads(ctx.values['receipt.json']); r['context_transport']['original_logical_sha256'] = 'd' * 64
                ctx.replace('receipt.json', tool.canonical(r))
            if defect == 'provider':
                r = json.loads(ctx.values['provider-request.json']); r['input'][1]['content'] = 'different'
                ctx.replace('provider-request.json', tool.canonical(r))
            with self.subTest(defect=defect): self.assertEqual(tool.collect(ctx)['status'], 'inconclusive')

    def test_output_item_identity_and_terminal_consistency(self):
        initial = frame('response.created', 0, dict(id='private-response', model='gpt-6.1-sol'))
        item = dict(id='private-item', type='message', content=[])
        added = frame('response.output_item.added', 1, output_index=0, item=item)
        bad_text = frame('response.output_text.delta', 2, output_index=0, item_id='other', delta='PRIVATE TEXT')
        terminal = frame('response.completed', 2, dict(id='private-response', model='gpt-6.1-sol', status='completed', output=[dict(item, id='other')]))
        for raw in (initial + added + bad_text, initial + added + terminal):
            self.assertEqual(tool.collect(Context(raw))['stream']['observation'], 'inconclusive')

    def test_persistence_hold_is_not_swallowed_as_diagnostic_success(self):
        from platform_supervisor.common import Hold
        with patch.object(Context, 'original', side_effect=Hold('Retained uncertainty', 'reconciling')):
            with self.assertRaises(Hold): tool.collect(Context())


if __name__ == '__main__': unittest.main()
