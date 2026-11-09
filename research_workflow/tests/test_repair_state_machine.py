"""Repair transitions with retained artifact bytes and synthetic persistence.

No provider request or experiment is executed. Operational validation remains
separate from scientific labels and fresh comprehension/critic reviews.
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomous_repair as repair
import autonomy
import autonomy_model as model
import experiment_builder as builder
import store
import dispatch_recovery
from workflow import canonical, digest, validate_record
from test_autonomous_repair import task


class RepairStateMachine(unittest.TestCase):
    def test_unbounded_native_diagnosis_is_refused_before_reading_turns(self):
        self.tables['builder_calls']=[dict(status='unknown')]*101
        with self.assertRaisesRegex(ValueError,'Uncertain native call coverage'):repair.diagnose(self.task,self.record)

    def test_operational_routes_without_a_candidate_or_recoverable_incident_do_not_claim_resolution(self):
        self.record['state']='diagnosing';self.assertIsNone(repair.route(self.task,'queued',copy.deepcopy(self.task['context']),'planning','Original diagnosis pending'))
        with patch.object(repair,'enqueue',return_value=None) as enqueue:self.assertIsNone(repair.route(self.task,'blocked',{},'planning','Original external requirement'))
        enqueue.assert_called_once()

    def test_active_repair_requires_validation_but_supports_an_older_candidate_without_native_handoff(self):
        context,_=self.validation_context();context.pop('repair_validation')
        with self.assertRaisesRegex(ValueError,'validation receipt required'):repair.route(self.task,'queued',context,'comprehension','Original candidate')
        context,_=self.validation_context();context.pop('native_repair_handoff');result=repair.route(self.task,'queued',context,'comprehension','Original candidate')
        self.assertEqual(result['context']['repair_progress']['state'],'resolved');self.assertEqual(self.record['state'],'resolved')

    def setUp(self):
        self.task = task(); incident = canonical({'reason': 'Synthetic original implementation failure'}); self.record = dict(id=str(uuid.uuid4()), version=repair.VERSION, state='diagnosing', category='runtime', revision=0, updated_at='2026-11-01T00:00:00Z', reason='Original failure', evidence={}, incident_raw=incident.decode(), incident_sha256=digest(incident))
        self.task['context'] = {'repair_progress': repair.progress(self.record), 'original_criterion': 'Frozen source condition', 'evidence_read': {'stale': True}}; self.artifacts = {}; self.records = []; self.operations = []; self.tables = {}; self.denied = None
        self.enterContext(patch.dict('os.environ', {'RESEARCH_AUTONOMY_REPAIR_ENABLED': 'true'})); self.enterContext(patch.object(store, 'rpc', side_effect=self.rpc)); self.enterContext(patch.object(store, 'rows', side_effect=self.rows)); self.enterContext(patch.object(store, 'artifact', side_effect=self.artifact)); self.enterContext(patch.object(store, 'append', side_effect=self.append)); self.enterContext(patch.object(store, 'artifact_bytes', side_effect=lambda study, identity, **options: self.artifacts[identity][1]))
        self.step, self.advance = MagicMock(return_value={'normal_step': True}), MagicMock(side_effect=lambda task, status, **options: dict(status=status, **options))
    def rows(self, kind, params=None, public=False):
        self.assertFalse(public); self.assertEqual(params['study_id'], 'eq.' + self.task['study_id'])
        if kind == 'autonomy_repairs': return [copy.deepcopy(self.record)]
        if kind == 'artifacts': return [copy.deepcopy(self.artifacts[params['id'][3:]][0])]
        return copy.deepcopy(self.tables.get((kind, params.get('status')), self.tables.get(kind, [])))
    def rpc(self, name, payload):
        self.operations.append((name, copy.deepcopy(payload)))
        if name == 'research_autonomy_repair_candidates': return copy.deepcopy(self.candidates)
        self.assertEqual(name, 'research_autonomy_repair'); self.assertEqual((payload['p_study'], payload['p_task'], payload['p_token']), (self.task['study_id'], self.task['id'], self.task['lease_token']))
        value = payload['p_payload']
        if payload['p_operation'] == 'transition':
            self.assertEqual(value['id'], self.record['id']); self.assertEqual(value['expected_revision'], self.record['revision'])
            if self.denied: return {'denied': self.denied}
            self.record.update(state=value['state'], reason=value['reason'], evidence=copy.deepcopy(value['evidence']), revision=self.record['revision'] + 1); return copy.deepcopy(self.record)
        if payload['p_operation'] in ['enqueue', 'adopt']: return {'task': {'id': self.task['id'], 'status': 'queued'}}
        raise AssertionError(payload['p_operation'])
    def artifact(self, study, actor, raw, identity, media, provenance):
        if identity in self.artifacts: self.assertEqual(self.artifacts[identity][1], raw, 'A retained original cannot be replaced')
        record = dict(id=identity, sha256=digest(raw), byte_count=len(raw), storage_path=study + '/' + digest(raw), media_type=media, provenance=provenance, verified_at='2026-11-01T00:00:00Z'); self.artifacts[identity] = (record, raw); return record
    def append(self, study, actor, kind, record):
        self.assertEqual(kind, 'artifacts'); validate_record(kind, record); self.records.append(copy.deepcopy(record)); return record
    def retain(self, name, raw):
        identity = str(uuid.uuid5(uuid.UUID(self.task['id']), name + ':' + digest(raw))); record = self.artifact(self.task['study_id'], self.task['actor_id'], raw, identity, 'application/json', {'kind': name}); return dict(artifact_id=identity, sha256=record['sha256'], byte_count=len(raw))
    def drive(self): return repair.drive(self.task, self.step, self.advance)

    def test_context_repair_diagnoses_verifies_exact_request_preflight_and_resumes_without_editing_original_task(self):
        original = copy.deepcopy(self.task); self.record['state'] = 'waiting'; context = {'task': 'planning', 'claim': {'description': 'Synthetic original claim'}}
        with patch.object(model, 'load_prompt', return_value=({'id': str(uuid.uuid4())}, 'Synthetic fixed instruction')), patch.object(autonomy, 'build_context', return_value=context) as build:
            expected = model.request('Synthetic fixed instruction', context); result = self.drive()
        self.assertEqual(self.task, original); self.assertEqual(result['status'], 'queued'); self.assertEqual(result['stage'], 'planning'); self.assertEqual(result['context']['original_criterion'], original['context']['original_criterion']); self.assertNotIn('evidence_read', result['context']); self.assertEqual(self.record['state'], 'resolved'); self.assertEqual(self.record['evidence']['request_sha256'], digest(canonical(expected))); self.assertEqual(self.record['evidence']['request_bytes'], len(canonical(expected))); self.assertTrue(self.record['evidence']['validated']); self.assertEqual([p['p_payload']['state'] for name, p in self.operations], ['diagnosing', 'validating', 'resuming', 'resolved']); build.assert_called_once(); self.step.assert_not_called()
        self.assertEqual(len(self.records), 1); diagnosis = json.loads(self.artifacts[self.records[0]['id']][1]); self.assertFalse(diagnosis['validated']); self.assertIn('no scientific readiness', diagnosis['scope'])

    def test_original_jobs_resume_execution_by_identity_without_another_model_request(self):
        jobs = [dict(id=str(uuid.uuid4()), status=status) for status in ['failed', 'complete']]; self.task['context']['job_ids'] = [j['id'] for j in jobs]; self.tables['queue_jobs'] = jobs
        with patch.object(model, 'load_prompt') as prompt, patch.object(model, 'request') as request: result = self.drive()
        self.assertEqual(result['stage'], 'execution'); self.assertEqual(self.record['evidence']['validator'], 'retained-experiment-identities-v1'); self.assertEqual(self.record['evidence']['original_jobs'], jobs); prompt.assert_not_called(); request.assert_not_called(); self.assertEqual(self.tables['queue_jobs'], jobs)

    def test_confirmed_dispatch_checkpoint_is_adopted_by_original_hash_without_replaying_the_provider(self):
        checkpoint=self.retain('Original verified checkpoint',b'Original immutable checkpoint');dispatch=dict(id=str(uuid.uuid4()),task_id=self.task['id'],study_id=self.task['study_id'],status='unknown',request_sha256='a'*64,provenance={});self.tables['model_dispatches']=[dispatch];verified=dict(id=dispatch['id'],checkpoint=checkpoint,checkpoint_recorded_at='2026-11-01T00:00:00Z');before=copy.deepcopy(self.task)
        with patch.object(dispatch_recovery,'queued_reconciliation',return_value=None),patch.object(dispatch_recovery,'confirmed',return_value=verified),patch.object(model,'call') as provider:diagnosis=repair.diagnose(self.task,self.record)
        self.assertEqual(diagnosis['checkpoint'],checkpoint);self.assertEqual(diagnosis['reconciled_dispatches'],[verified]);self.assertFalse(diagnosis['validated']);self.assertEqual(self.task,before);provider.assert_not_called()

    def test_implementation_repair_delivers_incident_bound_guidance_and_checkpoint_before_delegating_normal_building(self):
        self.record['category'] = 'implementation'; raw = b'Original checkpoint'; pointer = self.retain('checkpoint', raw); self.task['context'].update(builder_checkpoint=pointer, native_repair_handoff={'old': 'retained in original context'}); original = copy.deepcopy(self.task)
        result = self.drive(); self.assertEqual(result['stage'], 'building'); context = result['context']; self.assertEqual(context['builder_checkpoint'], pointer); self.assertEqual(context['repair_packet']['incident_sha256'], self.record['incident_sha256']); self.assertEqual(context['repair_directive'], repair.REPAIR_INSTRUCTION); self.assertNotIn('native_repair_handoff', context); self.assertIn('run a concrete development check', context['repair_packet']['command']); self.assertEqual(self.task, original)
        self.task['context'] = context; self.task['stage'] = 'building'; self.assertEqual(self.drive(), {'normal_step': True}); self.step.assert_called_once_with(self.task)

    def test_unknown_calls_wait_without_replacement_admission_and_revision_denial_never_advances(self):
        self.tables['builder_calls'] = [{'id': str(uuid.uuid4()), 'status': 'running'}]; result = self.drive(); self.assertEqual(result['status'], 'waiting'); self.assertEqual(result['delay'], 60); self.assertIn('no replacement call', result['reason']); self.assertEqual(self.record['state'], 'waiting'); self.step.assert_not_called()
        self.denied = 'cancelled'
        with self.assertRaisesRegex(ValueError, 'Repair admission stopped: cancelled'): self.drive()
        self.assertEqual(self.record['state'], 'waiting')

    def test_missing_wrong_version_or_unsupported_repair_state_cannot_progress(self):
        for value in [[], [dict(self.record, version='unknown')]]:
            with patch.object(store, 'rows', return_value=value), self.subTest(value=value), self.assertRaisesRegex(ValueError, 'incident identity is unavailable'): self.drive()
        self.record['state'] = 'unsupported'
        with self.assertRaisesRegex(ValueError, 'Unsupported active repair state'): self.drive()
        self.advance.assert_not_called()

    def test_discovery_adopts_only_recoverable_incidents_under_the_same_lease_and_keeps_external_blockers(self):
        self.task['reason'] = 'Synthetic controller failure'; external = dict(self.task, claim_id=None); self.candidates = [self.task, external]; result = repair.discover(); self.assertEqual([r['status'] for r in result], ['diagnosing', 'retained-blocker']); self.assertEqual(len(self.operations), 2); name, payload = self.operations[1]; self.assertEqual(payload['p_operation'], 'adopt'); incident = payload['p_payload']; self.assertEqual(incident['original_context'], self.task['context']); self.assertEqual(digest(incident['incident_raw'].encode()), incident['incident_sha256'])
        for updated in [dict(self.task, claim_id=None), dict(self.task, _repair_external=True)]: self.assertIsNone(repair.category(updated, 'Operational failure', {}))
        self.assertEqual(repair.category(self.task, 'Operational failure', {}), 'runtime')

    def test_active_repair_honors_terminal_controls_and_distinguishes_external_from_recoverable_blockers(self):
        self.record['state'] = 'repairing'; base = copy.deepcopy(self.task['context']); base['repair_progress'] = repair.progress(self.record)
        for status in ['budget', 'deadline', 'cancelled']:
            self.record['state'] = 'repairing'; context = copy.deepcopy(base); result = repair.route(self.task, status, context, 'building', 'Original control'); self.assertEqual(result['status'], status); self.assertEqual(result['context']['repair_progress']['state'], status)
        self.record['state'] = 'repairing'; context = copy.deepcopy(base); context['blockers'] = [{'kind': 'equipment'}]; result = repair.route(self.task, 'blocked', context, 'building', 'External apparatus missing'); self.assertEqual(result['status'], 'blocked'); self.assertEqual(self.record['state'], 'blocked')
        self.record['state'] = 'repairing'; result = repair.route(self.task, 'blocked', copy.deepcopy(base), 'building', 'Synthetic implementation failure'); self.assertEqual(result['status'], 'queued'); self.assertEqual(self.record['state'], 'diagnosing'); self.assertEqual(self.record['evidence']['latest_failure'], 'Synthetic implementation failure')

    def validation_context(self):
        self.record['state'] = 'repairing'; baseline = dict(artifact_id=str(uuid.uuid4()), sha256='a' * 64, version='fixture-v1'); context = dict(repair_progress=repair.progress(self.record), repair_directive=repair.REPAIR_INSTRUCTION, repair_packet={'incident_sha256': self.record['incident_sha256']}, original_criterion='Frozen criterion', proposal={'protocol': {'configurations': [{'details': {'test_config': {'private_inputs': {'project.zip': {'sha256': 'b' * 64}}}}}]}})
        context['native_repair_handoff'] = dict(builder.repair_handoff(context), baseline_checkpoint=baseline); report = dict(passed=True, incident_sha256=self.record['incident_sha256'], project_sha256='b' * 64, source_change={'changed': True, 'baseline_checkpoint': baseline}); context['repair_validation'] = self.retain('executed-development-report', canonical(report)); return context, report

    def test_executed_candidate_validation_resolves_operational_repair_but_still_requires_fresh_scientific_reviews(self):
        context, report = self.validation_context(); original = copy.deepcopy(context['proposal']); result = repair.route(self.task, 'queued', context, 'comprehension', 'Fresh reviews pending')
        self.assertEqual(result['status'], 'queued'); self.assertEqual(self.record['state'], 'resolved'); self.assertEqual([payload['p_payload']['state'] for name, payload in self.operations], ['validating', 'resuming', 'resolved']); self.assertEqual(result['context']['proposal'], original); self.assertEqual(result['context']['original_criterion'], 'Frozen criterion'); self.assertNotIn('repair_directive', result['context']); self.assertNotIn('repair_packet', result['context']); self.assertNotIn('native_repair_handoff', result['context']); self.assertEqual(self.record['evidence']['resume_stage'], 'comprehension'); self.assertIn('fresh comprehension and critic', self.record['reason'])

    def test_mismatched_incident_project_or_baseline_never_completes_an_active_repair(self):
        for change in [dict(passed=False), dict(incident_sha256='0' * 64), dict(project_sha256='0' * 64), dict(source_change={'changed': False}), dict(source_change={'changed': True, 'baseline_checkpoint': None})]:
            context, report = self.validation_context(); context['repair_validation'] = self.retain('changed-development-report', canonical(dict(report, **change)))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'validation does not match|exact retained source-change baseline'): repair.route(self.task, 'queued', context, 'comprehension', 'Fresh reviews pending')
            self.assertEqual(self.record['state'], 'repairing')

    def dispatch(self, status='failed', **values):
        return dict(dict(id=str(uuid.uuid4()), task_id=self.task['id'], study_id=self.task['study_id'], status=status, request_sha256='a' * 64, provenance={}), **values)

    def test_diagnosis_full_recent_window_includes_all_uncertain_dispatches_and_refuses_unbounded_inventory(self):
        recent = [self.dispatch('queued') for _ in range(21)]; uncertain = [recent[0], self.dispatch('queued')]; self.tables['model_dispatches'] = recent; self.tables['model_dispatches', 'in.(unknown,queued,running)'] = uncertain
        result = repair.diagnose(self.task, self.record); self.assertTrue(result['waiting']); self.assertIn('still admitted', result['reason']); self.assertTrue(any(params.get('status') == 'in.(unknown,queued,running)' and params['limit'] == 101 for call in store.rows.call_args_list for params in [call.args[1]]))
        self.tables['model_dispatches', 'in.(unknown,queued,running)'] = [uncertain[0]] * 101
        with self.assertRaisesRegex(ValueError, 'Uncertain dispatch coverage exceeds'): repair.diagnose(self.task, self.record)

    def test_diagnosis_rejects_wrong_original_artifact_turn_and_dispatch_identities(self):
        pointer = self.retain('original', b'Original input')
        with self.assertRaisesRegex(ValueError, 'original artifact identity mismatch'): repair.original(self.task, dict(pointer, sha256='0' * 64))
        turn = dict(id=str(uuid.uuid4()), task_id=self.task['id'], study_id=self.task['study_id'], request_sha256='a' * 64); dispatch = self.dispatch('unknown', id=turn['id'], request_sha256='b' * 64); self.tables['autonomy_turns', 'eq.unknown'] = [turn]; self.tables['model_dispatches'] = [dispatch]
        with self.assertRaisesRegex(ValueError, 'turn/dispatch request identity mismatch'): repair.diagnose(self.task, self.record)
        self.tables['autonomy_turns', 'eq.unknown'] = [dict(turn, study_id=str(uuid.uuid4()))]
        with self.assertRaisesRegex(ValueError, 'Repair controller turn identity mismatch'): repair.diagnose(self.task, self.record)
        self.tables['autonomy_turns', 'eq.unknown'] = []; self.tables['model_dispatches'] = [dict(dispatch, study_id=str(uuid.uuid4()))]
        with self.assertRaisesRegex(ValueError, 'Repair dispatch identity mismatch'): repair.diagnose(self.task, self.record)

    def test_operator_reconciliations_retain_redacted_log_excerpts_and_large_original_references(self):
        small = b'Synthetic command failure for person@example.test'; large = b'L' * 64001; stderr = dict(self.retain('stderr', small), name='stderr.bin'); stdout = dict(self.retain('stdout', large), name='stdout.bin'); ignored = dict(self.retain('other', b'Not a diagnostic log'), name='unrelated.bin'); dispatch = self.dispatch(provenance={'artifacts': [stderr, stdout, ignored]})
        self.tables['model_dispatches'] = [dispatch]; event = dict(id=str(uuid.uuid4()), details={'reconciled_dispatches': [dict(id=dispatch['id'], request_sha256=dispatch['request_sha256'], status=dispatch['status'], kind='confirmed-pre-provider-admission-failure')]}); self.tables['autonomy_events'] = [event]
        result = repair.diagnose(self.task, self.record); self.assertFalse(result['waiting']); self.assertEqual(result['reconciled_dispatches'][0]['kind'], 'retained-operator-reconciliation'); self.assertEqual(result['reconciled_dispatches'][0]['event_id'], event['id']); snippets = result['failure_details'][0]['original_log_excerpts']; self.assertEqual(len(snippets), 2); self.assertNotIn('person@example.test', snippets[0]['excerpt']); self.assertEqual(snippets[0]['original_bytes'], len(small)); self.assertEqual(snippets[1]['representation'], 'unreviewed-retained-original-reference'); self.assertEqual(snippets[1]['sha256'], digest(large)); self.assertEqual(self.artifacts[stderr['artifact_id']][1], small)

    def test_newest_completed_checkpoint_is_selected_by_recorded_time_not_dispatch_iteration_order(self):
        response = self.retain('response', b'{"original_response":true}'); old = self.retain('old-checkpoint', b'Old exact workspace'); new = self.retain('new-checkpoint', b'Newest exact workspace')
        def completed(pointer, when): return self.dispatch('complete', response_artifact_id=response['artifact_id'], response_sha256=response['sha256'], ended_at=when, provenance={'checkpoint': pointer})
        self.tables['model_dispatches'] = [completed(new, '2026-11-01T00:00:02Z'), completed(old, '2026-11-01T00:00:01Z')]; result = repair.diagnose(self.task, self.record); self.assertEqual(result['checkpoint'], new); self.assertEqual(len(result['reconciled_dispatches']), 2)
        self.tables['model_dispatches'].reverse(); self.assertEqual(repair.diagnose(self.task, self.record)['checkpoint'], new)

    def test_zero_transport_proof_requires_original_logical_hash_and_original_job_inventory_must_be_complete(self):
        logical = dict(self.retain('logical', b'{"original":true}'), name='logical.json'); receipt = dict(self.retain('receipt', canonical({'provider_calls': 0, 'provider_request_sent': False})), name='receipt.json'); self.tables['model_dispatches'] = [self.dispatch(provenance={'artifacts': [logical, receipt]})]
        with self.assertRaisesRegex(ValueError, 'logical request hash differs'): repair.diagnose(self.task, self.record)
        self.tables['model_dispatches'] = []; self.task['context']['job_ids'] = [str(uuid.uuid4())]
        with self.assertRaisesRegex(ValueError, 'experiment identity coverage is incomplete'): repair.diagnose(self.task, self.record)

    def test_environment_repair_waits_for_original_jobs_or_actual_diagnosis_before_any_replacement(self):
        import experiment_environment
        identity = str(uuid.uuid4()); self.task['context'].update(job_ids=[identity], experiment_environment={'original': True}); self.tables['queue_jobs'] = [{'id': identity, 'status': 'running'}]
        with patch.object(experiment_environment, 'diagnosis') as diagnosis:
            result = repair.diagnose(self.task, self.record); self.assertTrue(result['waiting']); self.assertIn('no environment replacement', result['reason']); diagnosis.assert_not_called()
        self.tables['queue_jobs'] = [{'id': identity, 'status': 'failed'}]
        with patch.object(experiment_environment, 'diagnosis', return_value={'waiting': True, 'reason': 'Original evidence not yet reconciled'}) as diagnosis:
            result = repair.diagnose(self.task, self.record); self.assertTrue(result['waiting']); self.assertFalse(result['validated']); self.assertEqual(result['original_jobs'], self.tables['queue_jobs']); diagnosis.assert_called_once_with({'original': True})
        with patch.object(experiment_environment,'diagnosis',return_value={'waiting':False,'reason':'Original environment reconciled'}):
            result=repair.diagnose(self.task,self.record);self.assertFalse(result['waiting']);self.assertFalse(result['experiment_environment']['waiting'])
        self.record.update(state='validating', evidence={'experiment_environment': {'waiting': False}})
        with patch.object(experiment_environment, 'resume_context', side_effect=lambda context: dict(context, environment_resumed=True)) as resume, patch.object(model, 'load_prompt', return_value=({}, 'Synthetic instruction')), patch.object(autonomy, 'build_context', return_value={'task': 'planning'}): result = self.drive()
        self.assertEqual(result['stage'], 'planning'); self.assertTrue(result['context']['environment_resumed']); resume.assert_called_once()

    def test_route_disabled_or_missing_current_incident_never_admits_a_new_transition(self):
        self.assertEqual(repair.category(dict(self.task, stage='building'), 'Synthetic tool failure', {}), 'implementation'); self.assertEqual(repair.category(self.task, 'Synthetic failure', {'blockers': [{'kind': 'implementation'}]}), 'implementation')
        with patch.dict('os.environ', {'RESEARCH_AUTONOMY_REPAIR_ENABLED': 'false'}): self.assertIsNone(repair.route(self.task, 'blocked', self.task['context'], 'planning', 'Synthetic failure'))
        with patch.object(store, 'rows', return_value=[]), self.assertRaisesRegex(ValueError, 'transition identity is unavailable'): repair.route(self.task, 'blocked', self.task['context'], 'planning', 'Synthetic failure')


if __name__ == '__main__': unittest.main()
