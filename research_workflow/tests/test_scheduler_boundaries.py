"""Queue admission and shared-history contracts with synthetic retained records."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomy
import autonomous_repair
import scheduler
import store
from workflow import canonical, digest
from test_public_queue import fixture as public_fixture, STUDY, TASK, CLAIM, JOB, OTHER, STAMP
from test_workflow import fixture as numerical_fixture, ACTOR


def uid(number): return '70000000-0000-4000-8000-' + str(number).zfill(12)
REAL_WAKE_WORKER = scheduler.wake_worker


class SchedulerBoundaries(unittest.TestCase):
    def setUp(self):
        self.data = public_fixture(); self.raw, self.protocol, self.campaign = numerical_fixture(); self.protocol['study_id'] = STUDY; self.campaign['study_id'] = STUDY; self.data['campaigns'] = [self.campaign]; self.data['protocols'] = [self.protocol]; self.calls = []; self.order = []; self.page_cap = 200
        self.body = dict(request_id=uid(90), campaign_id=self.campaign['id'], execution_mode='automatic', priority=3, dependencies=[], exclusive_resources=['machine:z', 'machine:a'], raw_input=self.raw.decode(), rationale='Synthetic prospective queue control', followup_of=None)
        self.rows = self.enterContext(patch.object(store, 'rows', side_effect=self.read)); self.rpc = self.enterContext(patch.object(store, 'rpc', side_effect=self.write)); self.artifact = self.enterContext(patch.object(store, 'artifact', side_effect=self.capture)); self.append = self.enterContext(patch.object(store, 'append', side_effect=lambda *args: self.order.append('append'))); self.wake = self.enterContext(patch.object(scheduler, 'wake_worker', side_effect=lambda value: self.order.append('notify') or {'status': 'deferred'})); self.validate_network = self.enterContext(patch.object(scheduler.network_execution, 'validate_input'))
        self.enterContext(patch.object(autonomy, 'runtime_health', return_value={'runtime': 'synthetic-runtime', 'status': 'ready', 'private': 'not projected'})); self.enterContext(patch.object(autonomy, 'current_defaults', return_value={'maximum_spend_usd': None})); self.enterContext(patch.dict(os.environ, RESEARCH_BUILDER_ENABLED='false'))
    def read(self, kind, params, **options):
        self.calls.append((kind, copy.deepcopy(params))); rows = copy.deepcopy(self.data.get(kind, []))
        for key in ['id', 'study_id', 'task_id', 'owner_id', 'number']:
            if key not in params: continue
            op, value = params[key].split('.', 1)
            if op == 'in': rows = [row for row in rows if row.get(key) in value.strip('()').split(',')]
            else:
                value = int(value) if key == 'number' else value
                rows = [row for row in rows if row.get(key) == value] if op == 'eq' else [row for row in rows if row.get(key) > value] if op == 'gt' else [row for row in rows if row.get(key) < value]
        if params.get('order'):
            key, direction = params['order'].split('.', 1); rows.sort(key=lambda row: row[key], reverse=direction == 'desc')
        offset = params.get('offset', 0); return rows[offset:offset + min(params.get('limit', 1000), self.page_cap)]
    def capture(self, study, actor, raw, ident, media, provenance):
        self.order.append('capture'); self.assertEqual((study, actor), (STUDY, ACTOR)); self.assertEqual(raw, self.raw); self.assertEqual(media, 'application/json'); return dict(id=ident, sha256=digest(raw), byte_count=len(raw), provenance=provenance)
    def write(self, operation, payload):
        self.order.append(operation)
        if operation == 'research_queue_enqueue': return dict(id=payload['p_id'], study_id=payload['p_study'], status='queued', **payload['p_request'])
        if operation == 'research_queue_action': return {'recorded': True}
        if operation == 'research_queue_snapshot': return copy.deepcopy(self.data['snapshot'])
        raise AssertionError(operation)
    def enqueue(self): return scheduler.enqueue(STUDY, ACTOR, copy.deepcopy(self.body))

    def test_numeric_enqueue_preserves_original_bytes_and_definition_before_notification(self):
        original = copy.deepcopy(self.body); result = self.enqueue(); self.assertEqual(self.body, original); self.assertFalse(result['replayed']); self.assertEqual(result['job']['input_sha256'], digest(self.raw)); self.assertEqual(result['job']['exclusive_resources'], ['machine:a', 'machine:z']); self.assertEqual(self.order, ['capture', 'append', 'research_queue_enqueue', 'notify']); self.assertEqual(self.artifact.call_args.args[2], self.raw); self.assertEqual(self.rpc.call_args.args[1]['p_sha256'], digest(canonical(self.rpc.call_args.args[1]['p_request']))); self.assertEqual(result['dispatch'], {'status': 'deferred'})

    def test_missing_campaign_protocol_wrong_adapter_and_unsupported_method_reject_before_capture(self):
        for defect in ['campaign', 'protocol', 'adapter', 'method']:
            with self.subTest(defect=defect):
                campaign = copy.deepcopy(self.campaign); self.data['campaigns'] = [] if defect == 'campaign' else [campaign]; self.data['protocols'] = [] if defect == 'protocol' else [self.protocol]
                if defect == 'adapter': campaign['adapter'] = 'unimplemented-domain'
                if defect == 'method': campaign['experiment_type'] = 'source-review'
                with self.assertRaises(ValueError): self.enqueue()
        self.artifact.assert_not_called(); self.rpc.assert_not_called(); self.wake.assert_not_called()

    def test_frozen_observation_time_byte_and_input_hash_limits_are_not_reset_to_admit_a_job(self):
        original = copy.deepcopy(self.protocol['document'])
        for defect in ['observations', 'wall', 'bytes', 'hash']:
            with self.subTest(defect=defect):
                document = self.protocol['document'] = copy.deepcopy(original)
                if defect == 'observations': document['resource_limits']['max_observations'] = 1001
                elif defect == 'wall': document['resource_limits']['wall_seconds'] = 11
                elif defect == 'bytes': document['resource_limits']['max_input_bytes'] = len(self.raw) - 1
                else: document['input_versions'][0]['sha256'] = 'e' * 64
                with self.assertRaises(ValueError): self.enqueue()
        self.artifact.assert_not_called(); self.rpc.assert_not_called()

    def test_network_enqueue_delegates_exact_input_validation_and_requires_the_reviewed_resource_locks(self):
        self.campaign.update(adapter='network-execution-v1', experiment_type='new-measurement'); document = self.protocol['document']; document['execution'] = {'exclusive_resources': ['machine:z', 'machine:a']}; document['resource_limits'].update(max_observations=1001, wall_seconds=60)
        result = self.enqueue(); self.assertFalse(result['replayed']); self.validate_network.assert_called_once_with(STUDY, self.protocol, self.raw); self.assertEqual(result['job']['exclusive_resources'], ['machine:a', 'machine:z'])
        self.artifact.reset_mock(); self.rpc.reset_mock(); self.body['exclusive_resources'] = []
        with self.assertRaisesRegex(ValueError, 'resource locks differ'): self.enqueue()
        self.campaign['experiment_type'] = 'reanalysis'
        with self.assertRaisesRegex(ValueError, 'applicable experiment method'): self.enqueue()
        self.artifact.assert_not_called(); self.rpc.assert_not_called()

    def test_manual_tasks_and_followups_keep_typed_dependencies_without_creating_automatic_input(self):
        self.body.update(execution_mode='manual', raw_input='', followup_of=OTHER, dependencies=[dict(job_id=JOB, requirement='reviewed-evidence')]); result = self.enqueue(); self.assertIsNone(result['job']['input_artifact_id']); self.assertIsNone(result['job']['input_sha256']); self.assertEqual(result['job']['followup_of'], OTHER); self.assertEqual(result['job']['dependencies'], self.body['dependencies']); self.assertEqual([kind for kind, _ in self.calls], ['queue_jobs']); self.artifact.assert_not_called()
        self.assertIn('unavailable', scheduler.blockers(dict(execution_mode='automatic', dependencies=[dict(job_id=JOB, requirement='complete-run')]), {})[0])

    def test_definition_rejects_malformed_shapes_unbounded_dependencies_and_nonoriginal_inputs(self):
        for key, value in [('execution_mode', 'other'), ('priority', 0), ('dependencies', None), ('dependencies', [{}] * 33), ('exclusive_resources', [str(n) for n in range(17)]), ('exclusive_resources', [1]), ('raw_input', None)]:
            invalid = dict(self.body, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError): scheduler.definition(invalid)
        for invalid in [None, dict(self.body, unexpected='field'), dict(self.body, dependencies=[dict(job_id=JOB, requirement='complete-run')] * 2)]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): scheduler.definition(invalid)
        self.rpc.assert_not_called()

    def test_shared_workflow_pagination_uses_offset_and_does_not_claim_a_cursor_for_one_record(self):
        result = scheduler.public_records(STUDY, 'autonomy_workflows'); self.assertEqual(result['coverage'], {'returned': 1}); self.assertIsNone(result['next_after']); self.assertEqual([params.get('offset', 0) for kind, params in self.calls if kind == 'autonomy_workflows'], [0, 1]); self.rpc.assert_not_called()

    def test_shared_record_paging_rejects_repeated_and_misordered_rows_instead_of_silently_dropping_them(self):
        pages = [[dict(id=uid(1), study_id=STUDY, citation='Original first')], [dict(id=uid(1), study_id=STUDY, citation='Repeated first')]]
        def rows(kind, params): return [dict(id=STUDY, study_kind='paper')] if kind == 'studies' else pages.pop(0)
        with patch.object(store, 'rows', side_effect=rows), self.assertRaisesRegex(store.StoreError, 'pagination or identities'): scheduler.public_records(STUDY, 'sources')
        with patch.object(store, 'rows', side_effect=[[dict(id=STUDY, study_kind='paper')], [dict(id=uid(3), study_id=STUDY), dict(id=uid(2), study_id=STUDY)]]), self.assertRaises(store.StoreError): scheduler.public_records(STUDY, 'sources')
        self.rpc.assert_not_called()

    def test_record_and_history_limits_reject_nonascii_and_out_of_range_cursors(self):
        for limit in ['0', '101', '١', '2.0', '']:
            with self.subTest(limit=limit), self.assertRaises(ValueError): scheduler.public_records(STUDY, 'sources', limit=limit)
        self.data['autonomy_tasks'][0]['attempts'] = 3
        for before in ['0', '5', '٢', '1.5', '']:
            with self.subTest(before=before), self.assertRaises(ValueError): scheduler.public_history(STUDY, TASK, before)
        for attempts in [True, -1, None]:
            self.data['autonomy_tasks'][0]['attempts'] = attempts
            with self.subTest(attempts=attempts), self.assertRaisesRegex(store.StoreError, 'attempt coverage'): scheduler.public_history(STUDY, TASK)

    def test_building_tasks_delegate_history_even_if_the_general_builder_flag_is_disabled(self):
        original = dict(version='owner-history-v1', access='owner-private', study_id=STUDY, task_id=TASK, turns=[dict(request={'system': ['Frozen policy'], 'api_key': 'private-token'}, output={'zero': 0, 'missing': None})])
        for context in [dict(stage='building'), dict(stage='planning', context={'builder_checkpoint': {'artifact_id': OTHER}})]:
            self.data['autonomy_tasks'][0].update(context)
            with patch.object(autonomy, 'history', return_value=copy.deepcopy(original)) as history:
                result = scheduler.public_history(STUDY, TASK, '2'); history.assert_called_once_with(STUDY, TASK, '2')
            self.assertEqual(result['version'], 'research-shared-turns-v1'); self.assertEqual(result['access'], 'public-read-only'); self.assertEqual(result['turns'][0]['output'], {'zero': 0, 'missing': None}); self.assertEqual(result['turns'][0]['request']['api_key'], '[redacted]')

    def test_shared_history_returns_none_for_missing_tasks_and_rejects_nonmonotone_attempt_pages(self):
        self.data['autonomy_tasks'] = []; self.assertIsNone(scheduler.public_history(STUDY, TASK)); self.data['autonomy_tasks'] = [dict(id=TASK, study_id=STUDY, attempts=3)]; self.data['autonomy_turns'] = [dict(id=uid(1), study_id=STUDY, task_id=TASK, number=2), dict(id=uid(2), study_id=STUDY, task_id=TASK, number=2)]
        with self.assertRaisesRegex(store.StoreError, 'turn coverage'): scheduler.public_history(STUDY, TASK)
        self.data['autonomy_turns'] = [dict(id=uid(1), study_id=STUDY, task_id=TASK, number=3)]
        self.assertEqual(scheduler.public_history(STUDY, TASK, '3')['turns'], [])

    def test_shared_requirements_and_documents_label_invalid_types_without_disclosing_original_private_values(self):
        self.assertIn('invalid format', scheduler.public_reason(None, requirements={'private': 'value'})); result, metadata = scheduler.shared_document(dict(source_url='https://[invalid', retrieved_url='http://192.0.2.1/paper', prompt='Compare original latency in ms', output={'zero': 0, 'missing': None})); self.assertEqual(result['output'], {'zero': 0, 'missing': None}); self.assertEqual(result['prompt'], 'Compare original latency in ms'); self.assertNotIn('192.0.2.1', result['retrieved_url']); self.assertGreater(metadata['redacted'], 0)
        with self.assertRaisesRegex(store.StoreError, 'unsupported value type'): scheduler.shared_document({'unexpected': b'non-JSON original'})
        with self.assertRaisesRegex(store.StoreError, 'byte budget'): scheduler.shared_document(['scientific condition. ' * 4200] * 50)

    def test_public_repair_progress_requires_a_valid_identity_version_state_category_revision_and_timestamp(self):
        original = dict(id=OTHER, version=autonomous_repair.VERSION, state='waiting', category='implementation', revision=0, updated_at=STAMP); self.data['autonomy_tasks'][0]['repair_progress'] = original; before = copy.deepcopy(self.data); result = scheduler.public_snapshot(STUDY); self.assertEqual(result['autonomy']['tasks'][0]['repair_progress'], original); self.assertEqual(self.data, before)
        for key, value in [('version', 'wrong'), ('state', 'done'), ('category', 'other'), ('revision', True), ('revision', -1), ('id', 'not-a-uuid'), ('updated_at', 'not-a-date')]:
            self.data['autonomy_tasks'][0]['repair_progress'] = dict(original, **{key: value})
            with self.subTest(key=key), self.assertRaises((store.StoreError, ValueError)): scheduler.public_snapshot(STUDY)
        for value in [[], dict(original, unexpected=True)]:
            self.data['autonomy_tasks'][0]['repair_progress'] = value
            with self.assertRaises(store.StoreError): scheduler.public_snapshot(STUDY)

    def test_nonobject_or_impossible_recorded_check_coverage_is_a_typed_public_read_failure(self):
        for value in [[], 'untrusted', True, dict(required_checks=True, completed_checks=0), dict(required_checks=1, completed_checks=2), dict(required_checks=1001, completed_checks=0)]:
            self.data['autonomy_tasks'][0]['evidence_progress'] = value
            with self.subTest(value=value), self.assertRaisesRegex(store.StoreError, 'check coverage'): scheduler.public_snapshot(STUDY)

    def test_shared_queue_bytes_are_bounded_without_returning_partial_task_coverage(self):
        original = self.data['autonomy_tasks'][0]; self.data['autonomy_tasks'] = [dict(original, id=uid(index + 100), reason='Recorded scientific diagnostic. ' * 150) for index in range(1000)]
        with self.assertRaisesRegex(store.StoreError, 'byte budget'): scheduler.public_snapshot(STUDY)
        self.rpc.assert_not_called(); self.wake.assert_not_called()

    def test_owner_queue_snapshot_and_overview_reject_oversized_bytes_without_truncating_history(self):
        self.data['snapshot'] = dict(jobs=[], events=[dict(details={'original': 'x' * 4000000})])
        with self.assertRaisesRegex(store.StoreError, 'byte budget'): scheduler.snapshot(STUDY, ACTOR)
        self.data['study_owners'] = [dict(owner_id=ACTOR, study_id=STUDY)]; self.data['studies'] = [dict(id=STUDY, study_kind='paper', title='Original paper')]; self.data['queue_jobs'] = []
        with patch.object(autonomy, 'overview', return_value={'large': 'x' * 4000000}), self.assertRaisesRegex(store.StoreError, 'byte budget'): scheduler.overview(ACTOR)

    def test_owner_actions_preserve_request_identity_recorded_reason_and_linked_assessments(self):
        body = dict(request_id=uid(80), job_id=JOB, action='attach-run', reason='Attach the retained measurement run', assessment_ids=[CLAIM], run_id=OTHER); original = copy.deepcopy(body); self.assertEqual(scheduler.action(STUDY, ACTOR, body), {'recorded': True}); self.assertEqual(body, original); self.assertEqual(self.rpc.call_args.args, ('research_queue_action', dict(p_study=STUDY, p_actor=ACTOR, p_job=JOB, p_id=uid(80), p_action='attach-run', p_reason=body['reason'], p_assessments=[CLAIM], p_run=OTHER)))
        for patch_value in [dict(unexpected=True), dict(assessment_ids=None), dict(assessment_ids=[CLAIM] * 1001), dict(action='erase')]:
            with self.subTest(patch=patch_value), self.assertRaises(ValueError): scheduler.action(STUDY, ACTOR, dict(body, **patch_value))
        body.update(action='cancel', run_id=None, assessment_ids=[]); scheduler.action(STUDY, ACTOR, body); self.assertIsNone(self.rpc.call_args.args[1]['p_run'])

    def test_enabled_builder_history_uses_the_shared_projection_before_loading_legacy_turns(self):
        value = dict(version='owner-private', access='owner-private', task_id=TASK, turns=[dict(output={'zero': 0, 'missing': None}, api_key='original-private-value')])
        with patch.dict(os.environ, RESEARCH_BUILDER_ENABLED='true'), patch.object(autonomy, 'history', return_value=value) as history:
            result = scheduler.public_history(STUDY, TASK, '3'); history.assert_called_once_with(STUDY, TASK, '3')
        self.assertEqual(result['access'], 'public-read-only'); self.assertEqual(result['version'], 'research-shared-turns-v1'); self.assertEqual(result['turns'][0]['output'], {'zero': 0, 'missing': None}); self.assertEqual(result['turns'][0]['api_key'], '[redacted]'); self.assertEqual([kind for kind, _ in self.calls], ['studies'])

    def test_shared_study_and_task_identity_mismatches_are_not_silently_accepted(self):
        for rows in [[dict(id=STUDY, study_kind='paper')] * 2, [dict(id=OTHER, study_kind='paper')]]:
            with self.subTest(rows=rows), patch.object(store, 'rows', return_value=rows), self.assertRaisesRegex(store.StoreError, 'assessment identity'): scheduler.shared_paper(STUDY)
        for task in [[dict(id=OTHER, study_id=STUDY)], [dict(id=TASK, study_id=OTHER)], [dict(id=TASK, study_id=STUDY)] * 2]:
            with self.subTest(task=task), patch.object(store, 'rows', side_effect=[[dict(id=STUDY, study_kind='paper')], task]), self.assertRaisesRegex(store.StoreError, 'task identity'): scheduler.public_history(STUDY, TASK)

    def test_owner_exports_reject_coverage_overflows_and_missing_campaigns(self):
        for jobs, events in [([{}] * 1001, []), ([], [{}] * 5001)]:
            self.data['snapshot'] = dict(jobs=jobs, events=events)
            with self.assertRaisesRegex(store.StoreError, 'interactive budget'): scheduler.snapshot(STUDY, ACTOR)
        self.data['study_owners'] = [dict(owner_id=ACTOR, study_id=STUDY)]; self.data['queue_jobs'] = [dict(study_id=STUDY)] * 1001; self.page_cap = 1001
        with self.assertRaisesRegex(store.StoreError, 'job coverage'): scheduler.overview(ACTOR)
        self.data['queue_jobs'] = [dict(id=JOB, study_id=STUDY, campaign_id=OTHER)]; self.data['campaigns'] = []
        with self.assertRaisesRegex(store.StoreError, 'campaign coverage'): scheduler.overview(ACTOR)

    def test_shared_queue_rejects_duplicate_or_unrequested_linked_ids_and_preserves_absent_progress(self):
        self.data['queue_jobs'].append(copy.deepcopy(self.data['queue_jobs'][0]))
        with self.assertRaisesRegex(store.StoreError, 'identities changed'): scheduler.public_snapshot(STUDY)
        self.data['queue_jobs'].pop()
        def rows(kind, params, **options):
            if kind == 'runs':
                return [dict(id=OTHER, study_id=STUDY, status='complete')] if params.get('offset', 0) == 0 else []
            return self.read(kind, params, **options)
        with patch.object(store, 'rows', side_effect=rows), self.assertRaisesRegex(store.StoreError, 'linked identities'): scheduler.public_snapshot(STUDY)
        self.data['autonomy_tasks'][0].pop('evidence_progress'); result = scheduler.public_snapshot(STUDY); self.assertNotIn('evidence_progress', result['autonomy']['tasks'][0]); self.assertNotIn('repair_progress', result['autonomy']['tasks'][0])

    def test_worker_notification_uses_a_bounded_async_lambda_client_and_defers_nonaccepted_responses(self):
        target = 'arn:aws:lambda:us-east-1:395567831870:function:synthetic-queue-worker'; client = Mock(); client.invoke.return_value = {'StatusCode': 202}
        with patch.dict(os.environ, RESEARCH_QUEUE_WORKERS_ENABLED='true', RESEARCH_QUEUE_WORKER_FUNCTION=target), patch('boto3.client', return_value=client) as factory:
            job = dict(execution_mode='automatic', status='queued'); self.assertEqual(REAL_WAKE_WORKER(job)['status'], 'requested'); self.assertEqual(factory.call_args.args, ('lambda',)); self.assertEqual(factory.call_args.kwargs['region_name'], 'us-east-1'); config = factory.call_args.kwargs['config']; self.assertEqual(config.connect_timeout, 1); self.assertEqual(config.read_timeout, 2); self.assertEqual(config.retries, {'total_max_attempts': 1}); self.assertEqual(client.invoke.call_args.kwargs, dict(FunctionName=target, InvocationType='Event', Payload=canonical(dict(invocation='durable queue enqueue; claim through existing capacity gates'))))
            client.invoke.return_value = {'StatusCode': 200}; self.assertEqual(REAL_WAKE_WORKER(job)['status'], 'deferred')


if __name__ == '__main__': unittest.main()
