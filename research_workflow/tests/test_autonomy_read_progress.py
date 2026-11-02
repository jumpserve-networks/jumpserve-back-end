"""Cyclic planning regressions through the real reader/controller/repair adapter.

Exposed deterministic software fixtures, not model efficacy or paper results.
No model, database, network, host provisioning or scientific campaign calls.
"""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from test_autonomy import task, answer
import autonomy
import autonomy_evidence as evidence
import autonomy_inputs as inputs
import autonomy_model as model
import autonomy_read_progress as reading
import autonomous_repair as repair
from workflow import canonical, digest


CONTRACT = dict(schema='required scientific configuration ' * 145, output='retain missing and zero')
REFERENCE = inputs.runner_reference('fixture-runner-v1', CONTRACT)['read_request']


def page(cursor, document=None, reference=None):
    request = dict(reference or REFERENCE, cursor=cursor)
    result = evidence.document_page(CONTRACT if document is None else document, cursor)
    return dict(version=evidence.VERSION, request=request, result=result,
        result_sha256=digest(canonical(result)), requested_by_turn_id='original-turn-' + str(cursor))


def cycle(context, stage='planning'):
    cursor = 0
    while cursor is not None:
        read = page(cursor); reading.retain(context, stage, read)
        cursor = read['result']['next_cursor']


def stalled_context(stage='planning'):
    context = dict(decision_lock='frozen', builder_checkpoint=dict(id='original-checkpoint'),
        completed_job_ids=['original-failed-job'])
    cycle(context, stage)
    for cursor in (0, 1500, 3000):
        reading.retain(context, stage, page(cursor))
    return context


class ReadingProgress(unittest.TestCase):
    def test_reproduces_recorded_four_page_loop_and_detects_cycles_not_just_consecutive_ids(self):
        context = {}; cycle(context)
        for i, cursor in enumerate((0, 1500, 3000)):
            stalled = reading.retain(context, 'planning', page(cursor))
            self.assertEqual(bool(stalled), i == 2)
        self.assertEqual(stalled['repeated_reads'], 3)
        self.assertFalse(stalled['repeated_recovery'])
        self.assertEqual(context['evidence_read_progress']['new_pages'], 4)

    def test_every_needed_fragment_remains_together_in_prompt_and_terminal_cursor_stays_null(self):
        context = {}; cycle(context)
        history = reading.prompt(context, 'planning')
        pages = sorted(history['retained_pages'], key=lambda p: p['read']['request']['cursor'])
        text = ''.join(p['read']['result']['fragment'] for p in pages)
        self.assertEqual(text, canonical(CONTRACT).decode())
        self.assertEqual(digest(text.encode()), pages[0]['read']['result']['document_sha256'])
        self.assertIsNone(pages[-1]['read']['result']['next_cursor'])
        self.assertIn('Delivery does not establish', history['instruction'])
        self.assertNotIn('coverage_complete', history)

    def test_new_page_or_changed_document_is_progress_but_new_turn_id_is_not(self):
        context = {}; read = page(0); reading.retain(context, 'planning', read)
        read['requested_by_turn_id'] = 'next-numbered-turn'
        reading.retain(context, 'planning', read)
        self.assertEqual(context['evidence_read_progress']['repeated_reads'], 1)
        reading.retain(context, 'planning', page(1500))
        self.assertEqual(context['evidence_read_progress']['repeated_reads'], 0)
        reading.retain(context, 'planning', page(0, dict(CONTRACT, changed=True)))
        self.assertEqual(context['evidence_read_progress']['new_pages'], 3)

    def test_source_delivery_counter_churn_is_not_new_evidence(self):
        context = {}; request = dict(REFERENCE, kind='source')
        for counter in range(4):
            result = evidence.page([dict(id='source-fragment', text='unchanged')], 0)
            result['source_material'] = dict(delivery_progress=counter)
            read = dict(request=request, result=result, result_sha256=digest(canonical(result)))
            stalled = reading.retain(context, 'planning', read)
        self.assertTrue(stalled)
        self.assertEqual(context['evidence_read_progress']['new_pages'], 1)

    def test_phase_and_candidate_changes_start_fresh_delivery_windows(self):
        context = {}; cycle(context)
        self.assertIsNone(reading.prompt(context, 'critic'))
        reading.retain(context, 'critic', page(0))
        self.assertEqual(context['evidence_read_progress']['new_pages'], 1)
        context['proposal'] = dict(protocol=dict(hypothesis='new candidate'))
        self.assertIsNone(reading.prompt(context, 'critic'))
        reading.retain(context, 'critic', page(1500))
        self.assertEqual(context['evidence_read_progress']['new_pages'], 1)

    def test_long_forward_reads_stay_bounded_with_explicit_omissions_and_no_false_stall(self):
        context = {}; document = dict(text='海🌍' * 35000)
        cursor = 0
        while cursor is not None:
            read = page(cursor, document); stalled = reading.retain(context, 'planning', read)
            self.assertIsNone(stalled); cursor = read['result']['next_cursor']
        value = context['evidence_read_progress']
        self.assertLessEqual(len(value['receipts']), reading.WINDOW)
        self.assertLessEqual(len(canonical(value['retained_pages'])), reading.VIEW_BYTES)
        self.assertGreater(value['omitted_receipts'], 0)
        self.assertGreater(value['omitted_page_views'], 0)

    def test_tampered_result_fragment_and_record_pages_fail_closed(self):
        for altered in ('result', 'fragment', 'records'):
            read = page(0)
            if altered == 'records':
                read['result'] = evidence.page([dict(id='retained')], 0)
                read['result']['records'][0]['id'] = 'changed'
            else: read['result']['fragment'] = 'changed'
            if altered != 'result': read['result_sha256'] = digest(canonical(read['result']))
            with self.assertRaisesRegex(ValueError, 'hash differs'):
                reading.retain({}, 'planning', read)

    def test_real_controller_retains_pages_then_routes_stall_into_durable_repair_without_launch(self):
        t = task(); original = copy.deepcopy(t); captured = []
        cursors = (0, 1500, 3000, 4500, 0, 1500, 3000)
        def control(task, operation, payload):
            self.assertEqual(operation, 'advance'); captured.append(payload)
            task['context'] = copy.deepcopy(payload['context'])
            return dict(status=payload['status'])
        def rpc(task, operation, payload):
            self.assertEqual(operation, 'enqueue')
            self.assertIn('reading_stall', json.loads(payload['incident_raw']))
            self.assertEqual(payload['category'], 'runtime')
            self.assertEqual(json.loads(payload['incident_raw'])['failed_turn_id'], 'turn-6')
            return dict(task=dict(status='queued', stage='repair'))
        with ExitStack() as stack:
            stack.enter_context(patch.dict('os.environ', RESEARCH_AUTONOMY_REPAIR_ENABLED='true'))
            stack.enter_context(patch.object(autonomy, 'build_context', return_value={}))
            stack.enter_context(patch.object(autonomy, 'one', return_value={}))
            stack.enter_context(patch.object(autonomy, 'inventory', return_value=[]))
            stack.enter_context(patch.object(autonomy, 'retained_jobs', return_value=[]))
            stack.enter_context(patch.object(inputs, 'runner_contracts', return_value={'fixture-runner-v1': CONTRACT}))
            stack.enter_context(patch.object(autonomy, 'control', side_effect=control))
            enqueue = stack.enter_context(patch.object(repair, 'rpc', side_effect=rpc))
            launch = stack.enter_context(patch.object(autonomy, 'enqueue'))
            invoke = stack.enter_context(patch.object(model, 'call'))
            for i, cursor in enumerate(cursors):
                output = dict(answer('inspect'), read_request=dict(REFERENCE, cursor=cursor))
                invoke.return_value = dict(id='turn-' + str(i), status='complete', output=output)
                result = autonomy.step(t)
            self.assertEqual(result['stage'], 'repair'); enqueue.assert_called_once(); launch.assert_not_called()
        self.assertEqual(len(captured), len(cursors) - 1)
        self.assertEqual(len(captured[-1]['context']['evidence_read_progress']['retained_pages']), 4)
        for key in ('id', 'study_id', 'actor_id', 'claim_id', 'workflow', 'attempts'):
            self.assertEqual(t[key], original[key])

    def test_recovery_preserves_constraints_and_stops_unchanged_second_correction(self):
        context = stalled_context(); original = copy.deepcopy(context)
        corrected = reading.recover(context, 'planning')
        self.assertEqual(context, original)
        for key in ('decision_lock', 'builder_checkpoint', 'completed_job_ids'):
            self.assertEqual(corrected[key], original[key])
        for cursor in (0, 1500, 3000):
            incident = reading.retain(corrected, 'planning', page(cursor))
        self.assertTrue(incident['repeated_recovery'])
        self.assertEqual(repair.category(task(), 'Evidence reading stalled', corrected), 'runtime')
        with self.assertRaisesRegex(ValueError, 'same correction'):
            reading.recover(corrected, 'planning')

    def test_qualifying_new_adapter_or_new_page_allows_materially_changed_recovery(self):
        for changed_adapter in (False, True):
            corrected = reading.recover(stalled_context(), 'planning')
            if changed_adapter:
                corrected['reading_recovery']['adapter_sha256'] = 'old-adapter'
            else:
                reading.retain(corrected, 'planning', page(0, dict(CONTRACT, additional='new information')))
            for cursor in (0, 1500, 3000):
                incident = reading.retain(corrected, 'planning', page(cursor))
            self.assertFalse(incident['repeated_recovery'])

    def test_real_prompt_build_keeps_history_and_separates_fresh_review(self):
        t = task(); t['context'] = stalled_context(); claim = dict(source_id='source')
        with patch.object(autonomy, 'one', side_effect=[claim, dict(id='source')]), patch.object(autonomy, 'rows', return_value=[]), patch.object(autonomy, 'retained_jobs', return_value=[]):
            context = autonomy.build_context(t)
        self.assertEqual(context['evidence_read_history']['new_pages'], 4)
        self.assertEqual(len(context['evidence_read_history']['retained_pages']), 4)
        t['stage'] = 'critic'
        with patch.object(autonomy, 'one', side_effect=[claim, dict(id='source')]), patch.object(autonomy, 'rows', return_value=[]):
            fresh = autonomy.build_context(t)
        self.assertNotIn('evidence_read_history', fresh)

    def test_exact_openclaw_wire_fits_with_all_fragments_and_hash_bound_contracts(self):
        context = dict(task='planning', runner_contracts=inputs.runner_contracts(), prior_attempts=[],
            source_material=dict(filler='z' * 45000), evidence_read_history=reading.prompt(stalled_context(), 'planning'))
        with patch.object(model, 'OPENCLAW', True), patch.object(model, 'MODEL', 'gpt-6.1-sol'):
            request = model.request('Policy ' * 2000, context)
            self.assertTrue(model.request_fits(request))
        delivered = json.loads(request['messages'][0]['content'][0]['text'])
        self.assertEqual(delivered['evidence_read_history'], context['evidence_read_history'])

    def test_repair_adapter_validates_corrected_request_and_resumes_exact_original_phase(self):
        for stage in ('planning', 'comprehension', 'critic', 'assessment'):
            t = task(stage); t['context'] = stalled_context(stage)
            t['context']['repair_progress'] = dict(id='incident', state='diagnosing')
            record = dict(id='incident', version=repair.VERSION, state='diagnosing', category='runtime',
                evidence={}, reason='Reading stall', original_stage=stage)
            transitions = []
            def transition(task, current, state, reason, evidence):
                transitions.append(state)
                return dict(current, state=state, reason=reason, evidence=evidence, revision=len(transitions), updated_at='retained')
            with patch.object(repair, 'rows', return_value=[record]), \
                    patch.object(repair, 'diagnose', return_value=dict(waiting=False, native_repair=False, original_jobs=[dict(id='original', status='awaiting-review')])), \
                    patch.object(repair, 'save_diagnosis', return_value=dict(id='diagnosis')), \
                    patch.object(repair, 'transition', side_effect=transition), \
                    patch.object(model, 'load_prompt', return_value=({}, 'Policy')), \
                    patch.object(autonomy, 'build_context', side_effect=lambda task:dict(task=task['stage'], evidence_read_history=reading.prompt(task['context'],task['stage']))) as build, \
                    patch.object(model, 'call') as invoke:
                result = repair.drive(t, lambda task:self.fail('No provider work during diagnosis'), lambda task,status,**kwargs:dict(status=status, **kwargs))
            self.assertEqual(result['stage'], stage)
            self.assertEqual(result['context']['reading_recovery']['new_pages'], 4)
            self.assertEqual(transitions, ['validating', 'resuming', 'resolved'])
            self.assertEqual(build.call_args.args[0]['stage'], stage); invoke.assert_not_called()

    def test_uncertain_call_fences_reading_repair_without_clearing_incident_or_admitting_model(self):
        t = task(); t['context'] = stalled_context(); original = copy.deepcopy(t['context'])
        t['context']['repair_progress'] = dict(id='incident', state='diagnosing')
        record = dict(id='incident', version=repair.VERSION, category='runtime', state='diagnosing', revision=1, updated_at='retained')
        def rows(task, kind, **kwargs):
            return [record] if kind == 'autonomy_repairs' else [dict(id='live-call', status='running')] if kind == 'builder_calls' else []
        with patch.object(repair, 'rows', side_effect=rows), patch.object(repair, 'save_diagnosis', return_value=dict(id='diagnosis')), \
                patch.object(repair, 'transition', side_effect=lambda task,record,state,reason,evidence:dict(record,state=state,reason=reason,evidence=evidence)), \
                patch.object(model, 'request') as request:
            result = repair.drive(t, lambda _:self.fail('No replacement call'), lambda task,status,**kwargs:dict(status=status,**kwargs))
        self.assertEqual(result['status'], 'waiting'); request.assert_not_called()
        self.assertEqual(result['context']['reading_stall'], original['reading_stall'])

    def test_recurrent_stall_waits_quietly_then_changed_adapter_reconciles_and_resumes(self):
        t = task(); context = reading.recover(stalled_context(), 'planning')
        for cursor in (0, 1500, 3000): reading.retain(context, 'planning', page(cursor))
        context['repair_progress'] = dict(id='second-incident', state='diagnosing')
        t['context'] = context
        record = dict(id='second-incident', version=repair.VERSION, state='diagnosing', category='runtime',
            reason='Recurrent reading stall', evidence={}, revision=0, updated_at='retained')
        def transition(task, previous, state, reason, evidence):
            record.update(state=state, reason=reason, evidence=evidence, revision=record['revision'] + 1)
            return copy.deepcopy(record)
        def advance(task, status, **kwargs): return dict(status=status, **kwargs)
        with patch.object(repair, 'rows', side_effect=lambda *a,**kw:[copy.deepcopy(record)]), \
                patch.object(repair, 'save_diagnosis', return_value=dict(id='retained-diagnosis')) as save, \
                patch.object(repair, 'diagnose', return_value=dict(waiting=False, native_repair=False, original_jobs=[])) as diagnose, \
                patch.object(repair, 'transition', side_effect=transition) as transitioned, \
                patch.object(model, 'load_prompt', return_value=({}, 'Policy')), \
                patch.object(autonomy, 'build_context', side_effect=lambda task:dict(task=task['stage'], evidence_read_history=reading.prompt(task['context'],task['stage']))), \
                patch.object(model, 'call') as invoke:
            waiting = repair.drive(t, lambda _:self.fail('No model work'), advance)
            self.assertEqual(waiting['status'], 'waiting'); self.assertEqual(waiting['delay'], 300)
            t['context'] = waiting['context']
            repair.drive(t, lambda _:self.fail('No unchanged retry'), advance)
            save.assert_called_once(); transitioned.assert_called_once(); diagnose.assert_not_called(); invoke.assert_not_called()
            with patch.object(reading, 'identity', return_value='new-qualified-adapter'):
                resumed = repair.drive(t, lambda _:self.fail('Reconcile first'), advance)
            self.assertEqual(resumed['stage'], 'planning'); self.assertEqual(resumed['status'], 'queued')
            self.assertEqual(resumed['context']['reading_recovery']['adapter_sha256'], 'new-qualified-adapter')
            diagnose.assert_called_once(); invoke.assert_not_called()

    def test_failed_request_preflight_cannot_mark_reading_repair_resolved(self):
        t = task(); t['context'] = stalled_context()
        t['context']['repair_progress'] = dict(id='incident', state='validating')
        record = dict(id='incident', version=repair.VERSION, state='validating', category='runtime', evidence={})
        with patch.object(repair, 'rows', return_value=[record]), patch.object(model, 'load_prompt', return_value=({},'Policy')), \
                patch.object(autonomy, 'build_context', return_value=dict(task='planning',initial_prompt='x'*100000)), \
                patch.object(repair, 'transition') as transition:
            with self.assertRaisesRegex(ValueError, '96 KB'):
                repair.drive(t, lambda _:self.fail('No model work'), lambda *a,**kw:self.fail('No resumption'))
        transition.assert_not_called()
