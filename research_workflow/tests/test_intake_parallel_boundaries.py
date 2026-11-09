"""Source-bound chunk completion and metadata writes without inference calls."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomy as a
import autonomy_model as model
import intake_parallel as parallel
from workflow import canonical, digest
from test_autonomy import task, answer

TITLE = 'Cache observations in controlled networks'
TEXT = TITLE + '\nJane Doe and John Smith\nColumbia University\nAbstract\nLatency is 10 ms.'


class IntakeParallelBoundaries(unittest.TestCase):
    def setUp(self):
        self.task = task('intake'); self.task.update(intake_parent_id=str(uuid.uuid4()), intake_chunk_index=0)
        self.packet = dict(pages=[dict(page=1, text=TEXT), dict(page=2, text='The observed throughput is 0 Mbps.')], document_metadata={'title': TITLE})
        self.chunks = parallel.sources.chunks(self.packet); self.task['context'] = dict(source_id=str(uuid.uuid4()), text_artifact_id=str(uuid.uuid4()), intake_chunk_sha256=digest(canonical(self.chunks[0])))
        self.output = dict(answer('discover'), claims=[dict(page=1, quote='Latency is 10 ms.', claim_type='numerical')], paper_metadata=dict(title={'text': TITLE, 'quote': TITLE, 'page': 1}, domain={'text': 'Computer networking', 'quote': 'Latency is 10 ms.', 'page': 1}))
        self.turn = dict(id=str(uuid.uuid4()), status='complete', output=self.output); self.study = dict(id=self.task['study_id']); self.order = []
        self.artifact = self.enterContext(patch.object(parallel.store, 'artifact_bytes', return_value=canonical(self.packet))); self.one = self.enterContext(patch.object(a, 'one', return_value={'id': self.task['context']['source_id'], 'citation': 'Original source'})); self.rows = self.enterContext(patch.object(a, 'rows', return_value=[])); self.recorded = self.enterContext(patch.object(parallel, 'recorded_call', return_value=self.turn)); self.advance = self.enterContext(patch.object(a, 'advance', side_effect=lambda *args, **kwargs: self.order.append('advance') or dict(status=args[1], context=kwargs.get('context'), reason=kwargs.get('reason')))); self.blocked = self.enterContext(patch.object(a, 'blocked', return_value={'status': 'blocked'})); self.rpc = self.enterContext(patch.object(parallel.store, 'rpc', side_effect=lambda *args: self.order.append('metadata') or {'recorded': True})); self.append = self.enterContext(patch.object(parallel.store, 'append_bundle', side_effect=lambda *args: self.order.append('claims'))); self.seed = self.enterContext(patch.object(a, 'seed', side_effect=lambda *args: self.order.append('seed')))
    def extract(self): return parallel.extract(self.task, self.study)

    def test_first_chunk_validates_original_quotes_and_saves_metadata_before_claim_seeding(self):
        original = copy.deepcopy(self.task); result = self.extract(); self.assertEqual(self.task, original); self.assertEqual(self.order, ['metadata', 'claims', 'seed', 'advance']); self.artifact.assert_called_once_with(self.study['id'], self.task['context']['text_artifact_id'], maximum=10_000_000); request = self.recorded.call_args.args[1]; self.assertTrue(request['paper_metadata_requested']); self.assertEqual(request['source_chunk'], self.chunks[0]); self.assertEqual(request['total_chunks'], 2); self.assertEqual(request['prior_attempt'], 0)
        name, payload = self.rpc.call_args.args; self.assertEqual(name, 'research_paper_review'); self.assertEqual(payload['p_operation'], 'metadata'); self.assertEqual(payload['p_actor'], self.task['actor_id']); metadata = payload['p_payload']; self.assertEqual(metadata['title'], TITLE); self.assertEqual(metadata['domain'], 'Computer networking'); self.assertEqual(metadata['token'], self.task['lease_token']); self.assertEqual(metadata['metadata']['turn_id'], self.turn['id']); self.assertEqual(metadata['metadata']['authors']['names'], ['Jane Doe', 'John Smith']); self.assertEqual(metadata['metadata']['suggestions'], self.output['paper_metadata']); self.assertEqual(result['status'], 'finished'); self.assertTrue(result['context']['quote_page_validated']); self.assertEqual(result['context']['last_turn_id'], self.turn['id'])
        claim = self.append.call_args.args[2][0]['record']; self.assertEqual(claim['description'], 'Latency is 10 ms.'); self.assertEqual(claim['location'], 'PDF page 1'); self.assertEqual(claim['scope']['source_text_offset'], 0); self.assertEqual(self.seed.call_args.args[1], [claim])

    def test_later_chunks_or_examined_metadata_never_race_the_first_chunk_metadata_write(self):
        for later in [False, True]:
            with self.subTest(later=later):
                self.rpc.reset_mock(); self.append.reset_mock(); self.task['context']['metadata_examined'] = not later; self.task['intake_chunk_index'] = int(later); chunk = self.chunks[int(later)]; self.task['context']['intake_chunk_sha256'] = digest(canonical(chunk)); self.output['claims'] = [dict(page=chunk['page'], quote='The observed throughput is 0 Mbps.' if later else 'Latency is 10 ms.', claim_type='numerical')]; self.extract(); self.rpc.assert_not_called(); self.assertNotIn('paper_metadata_requested', self.recorded.call_args.args[1]); self.assertEqual(self.append.call_count, 1)

    def test_complete_empty_chunk_stays_finished_without_invented_claims_or_seeds(self):
        self.output['claims'] = []; self.task['context']['metadata_examined'] = True; result = self.extract(); self.assertEqual(result['status'], 'finished'); self.rpc.assert_not_called(); self.append.assert_not_called(); self.seed.assert_not_called()

    def test_denied_invalid_and_blocked_answers_do_not_create_claims_or_metadata(self):
        for response, expected in [(dict(denied='budget'), 'budget'), (dict(status='invalid', reason='Original malformed answer'), 'queued'), (dict(status='complete', output=answer('blocked')), 'blocked')]:
            with self.subTest(response=response):
                self.recorded.return_value = response; result = self.extract(); self.assertEqual(result['status'], expected)
                if expected == 'queued': self.assertEqual(result['context']['last_failure'], 'Original malformed answer')
        self.rpc.assert_not_called(); self.append.assert_not_called(); self.seed.assert_not_called(); self.blocked.assert_called_once_with(self.task, answer('blocked'))

    def test_changed_source_identity_wrong_quote_or_invalid_metadata_fail_before_any_write(self):
        original = self.task['context']['intake_chunk_sha256']; self.task['context']['intake_chunk_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'chunk identity changed'): self.extract()
        self.recorded.assert_not_called(); self.task['context']['intake_chunk_sha256'] = original; self.output['claims'][0]['page'] = 2
        with self.assertRaisesRegex(ValueError, 'exact supplied source'): self.extract()
        self.output['claims'][0]['page'] = 1; self.output['paper_metadata']['title']['text'] = 'An invented replacement title'
        with self.assertRaisesRegex(ValueError, 'faithfully match'): self.extract()
        self.rpc.assert_not_called(); self.append.assert_not_called(); self.seed.assert_not_called()

    def test_complete_source_inventory_cannot_be_truncated_to_fit_the_claim_limit(self):
        self.rows.return_value = [dict(id=str(uuid.uuid4())) for _ in range(200)]
        with self.assertRaisesRegex(ValueError, 'exceeds 200'): self.extract()
        self.rpc.assert_not_called(); self.append.assert_not_called(); self.seed.assert_not_called()

    def test_repeated_source_bound_claim_does_not_consume_another_inventory_slot(self):
        expected = parallel.sources.candidates(self.study['id'], self.task['context']['source_id'], self.chunks[0], self.output['claims'])[0]; self.rows.return_value = [dict(id=expected['id'])] + [dict(id=str(uuid.uuid4())) for _ in range(199)]; self.assertEqual(self.extract()['status'], 'finished'); self.assertEqual(self.append.call_args.args[2][0]['record']['id'], expected['id'])

    def test_unknown_title_and_byline_remain_missing_in_the_retained_metadata(self):
        self.packet = dict(pages=[dict(page=1, text='')], document_metadata={}); chunk = dict(page=1, text='Original unstructured scientific condition.', offset=0); self.packet['pages'][0]['text'] = chunk['text']; self.artifact.return_value = canonical(self.packet); self.task['context']['intake_chunk_sha256'] = digest(canonical(chunk)); self.output['claims'] = []; self.output['paper_metadata'] = dict(title=None, domain=None); self.extract(); metadata = self.rpc.call_args.args[1]['p_payload']; self.assertIsNone(metadata['title']); self.assertIsNone(metadata['domain']); self.assertIsNone(metadata['metadata']['authors']); self.assertIsNone(metadata['metadata']['title_fallback'])

    def test_all_parallel_chunks_complete_into_one_exact_coverage_record(self):
        with patch.object(parallel, 'rpc', return_value=dict(complete=2, remaining=0)) as rpc:
            result = parallel.coordinate(self.task, dict(chunk_index=0), self.chunks)
        self.assertEqual(result['status'], 'queued'); self.assertEqual(result['context']['chunk_index'], 2); self.assertEqual(result['context']['text_chunks_examined'], 2); self.assertEqual(result['context']['text_chunks_total'], 2); self.assertTrue(result['context']['metadata_examined']); self.assertEqual(rpc.call_args.args[1]['token'], self.task['lease_token'])

    def test_control_rpc_and_controller_slot_never_process_without_a_retained_lease(self):
        with patch.object(parallel.store, 'rpc', return_value=None) as rpc, patch.object(a, 'process') as process:
            self.assertEqual(parallel.rpc('inspect'), None); rpc.assert_called_with('research_intake_control', dict(p_operation='inspect', p_payload={})); self.assertEqual(parallel.controller_slot(), {'status': 'idle'}); process.assert_not_called()
            rpc.return_value = self.task; process.return_value = {'status': 'finished'}; self.assertEqual(parallel.controller_slot(), {'status': 'finished'}); process.assert_called_once_with(self.task)

    def test_captured_complete_answer_with_missing_output_uses_the_durable_call_admission_path(self):
        # setUp isolates extraction at the model boundary; here exercise the
        # production resume helper through a separate original reference.
        self.recorded.side_effect = None
        context = dict(task='discover', source_chunk=self.chunks[0], prior_attempt=1); raw = canonical(model.request('Policy', dict(context, prior_attempt=0))).decode(); turn = dict(id='retained-turn', status='complete', request_raw=raw, request_sha256=digest(raw.encode()), output=None)
        with patch.object(a, 'rows', return_value=[turn]), patch.object(model, 'call', return_value={'denied': 'in-flight'}) as infer:
            result = REAL_RECORDED_CALL(self.task, context)
        self.assertEqual(result, {'denied': 'in-flight'}); infer.assert_called_once_with(self.task, context, a.control)


REAL_RECORDED_CALL = parallel.recorded_call
if __name__ == '__main__': unittest.main()
