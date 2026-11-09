"""Durable controller boundaries with retained identities and synthetic adapters."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomy
import autonomy_model as model
import autonomous_repair
import budget_policy
import model_dispatch
import runtime_retry
import shared_campaigns
import intake_parallel
import experiment_builder
import experiment_environment
import autonomy_read_progress
import store
from workflow import canonical, digest
from test_autonomy import task
from test_autonomy_methods import Recorded


class ControllerOperations(unittest.TestCase):
    def test_control_and_seed_bind_lease_actor_claims_and_independent_context_copies(self):
        item = task(); context = {'source': ['retained']}; claims = [{'id': str(uuid.uuid4())}, {'id': str(uuid.uuid4())}]
        with patch.object(store, 'rpc', return_value={'recorded': True}) as rpc:
            self.assertEqual(autonomy.control(item, 'advance', {'status': 'queued', 'token': 'untrusted'}), {'recorded': True})
            arguments = rpc.call_args.args[1]
            self.assertEqual(arguments['p_actor'], item['actor_id']); self.assertEqual(arguments['p_payload']['token'], item['lease_token'])
            self.assertEqual(arguments['p_payload']['task_id'], item['id'])
            autonomy.seed(item, claims, context); seeds = rpc.call_args.args[1]['p_payload']['claims']
            self.assertEqual(len({seed['id'] for seed in seeds}), 2)
            seeds[0]['context']['source'].append('changed'); self.assertEqual(seeds[1]['context'], context)

    def test_notification_only_dispatches_to_the_enabled_fixed_account_worker_and_never_retries_uncertain_calls(self):
        target = 'arn:aws:lambda:us-east-1:395567831870:function:fixture-controller'
        for enabled, destination, status in [('false', target, 'deferred'), ('true', target.replace('395567831870', '000000000000'), 'deferred'), ('true', target, 'requested')]:
            with self.subTest(enabled=enabled, destination=destination), patch.dict(os.environ, {'RESEARCH_AUTONOMY_ENABLED': enabled, 'RESEARCH_AUTONOMY_WORKER_FUNCTION': destination}), patch.object(autonomy.scheduler, 'invoke_worker', return_value={'StatusCode': 202}) as invoke:
                self.assertEqual(autonomy.wake()['status'], status)
                self.assertEqual(invoke.call_count, int(status == 'requested'))
        for result in [{'StatusCode': 500}, TimeoutError('unconfirmed')]:
            with patch.dict(os.environ, {'RESEARCH_AUTONOMY_ENABLED': 'true', 'RESEARCH_AUTONOMY_WORKER_FUNCTION': target}), patch.object(autonomy.scheduler, 'invoke_worker', **({'side_effect': result} if isinstance(result, Exception) else {'return_value': result})) as invoke:
                self.assertEqual(autonomy.wake()['status'], 'deferred'); invoke.assert_called_once_with(target)

    def test_runtime_and_defaults_come_from_the_selected_server_policy(self):
        with patch.dict(os.environ, {'RESEARCH_CATEGORY_BUDGETS_ENABLED': 'true'}), patch.object(budget_policy, 'assessment_defaults', return_value={'maximum_spend_usd': None}) as defaults:
            self.assertEqual(autonomy.current_defaults(), {'maximum_spend_usd': None}); defaults.assert_called_once_with(autonomy.DEFAULTS)
        with patch.object(model, 'OPENCLAW', True), patch.object(model_dispatch, 'health', return_value={'status': 'ready'}) as health:
            self.assertEqual(autonomy.runtime_health(), {'status': 'ready'}); health.assert_called_once()
        with patch.object(model, 'OPENCLAW', False): self.assertEqual(autonomy.runtime_health()['runtime'], 'bedrock')

    def test_initial_admission_denial_never_wakes_a_worker_and_nonpaper_requests_are_rejected(self):
        body = dict(request_id=str(uuid.uuid4()), autonomous=True, paper=dict(title='Synthetic paper', paper_url='https://example.test/paper.pdf', domain='Networking', scope='Exact declared scope'))
        with patch.object(store, 'rpc', return_value={'denied': 'budget'}), patch.object(autonomy, 'wake') as wake:
            with self.assertRaisesRegex(ValueError, 'cannot admit'): autonomy.start(str(uuid.uuid4()), body)
            wake.assert_not_called()
        with patch.object(autonomy, 'paper_details', return_value={'study_kind': 'idea'}), patch.object(store, 'rpc') as rpc:
            with self.assertRaisesRegex(ValueError, 'published paper identity'): autonomy.start(str(uuid.uuid4()), body)
            rpc.assert_not_called()

    def test_retained_plan_choices_include_only_applicable_checks_for_the_exact_claim(self):
        study, claim, plan = (str(uuid.uuid4()) for _ in range(3))
        value = dict(records=[dict(kind='claim_checks', record=dict(claim_id=claim, applicability='applicable', campaign_id='matched')),
                              dict(kind='claim_checks', record=dict(claim_id=claim, applicability='not-applicable', campaign_id='excluded')),
                              dict(kind='claim_checks', record=dict(claim_id='other', applicability='applicable', campaign_id='other'))],
                     jobs=[dict(id='first', campaign_id='matched'), dict(id='second', campaign_id='excluded'), dict(id='third', campaign_id='other')])
        with patch.object(autonomy, 'one', return_value={'id': plan}) as one, patch.object(autonomy.preparation, 'compiled', return_value=value):
            original, choices = autonomy.choices(study, claim, plan)
            self.assertIs(original, value); self.assertEqual(choices, [value['jobs'][0]])
            one.assert_called_once_with('prepared_plans', study, plan)

    def test_retained_record_lookup_uses_exact_study_identity_and_rejects_missing_or_ambiguous_results(self):
        study, identity = str(uuid.uuid4()), str(uuid.uuid4())
        with patch.object(store, 'rows', return_value=[{'id': identity}]) as rows:
            self.assertEqual(autonomy.one('studies', study, identity), {'id': identity})
            rows.assert_called_once_with('studies', {'id': 'eq.' + identity, 'limit': 1})
            autonomy.one('claims', study, identity); self.assertEqual(rows.call_args.args[1]['study_id'], 'eq.' + study)
        for found in [[], [{}, {}]]:
            with patch.object(store, 'rows', return_value=found), self.assertRaisesRegex(ValueError, 'retained claims identity'): autonomy.one('claims', study, identity)

    def test_overview_loads_no_full_context_and_refuses_incomplete_or_truncated_claim_identity_coverage(self):
        study, claim = str(uuid.uuid4()), str(uuid.uuid4())
        def rows(kind, query):
            if kind == 'autonomy_workflows': return [{'study_id': study}]
            if kind == 'autonomy_tasks':
                self.assertNotIn('context,', query['select']); return [dict(id='task', claim_id=claim, context={'private_prompt': 'must disappear'})]
            self.assertEqual(kind, 'claims'); return [dict(id=claim, description='Recorded exact claim')]
        with patch.object(store, 'rows', side_effect=rows):
            result = autonomy.overview([{'id': study}]); self.assertNotIn('context', result['tasks'][0])
            self.assertEqual(result['tasks'][0]['claim_title'], 'Recorded exact claim')
        for workflows, tasks in [([{}] * 101, []), ([{}], [{}] * 20001), ([{}], [dict(claim_id=claim)])]:
            with patch.object(store, 'rows', side_effect=[workflows, tasks, []]), self.assertRaises(store.StoreError): autonomy.overview([{'id': study}])
        with patch.object(store, 'rows') as rows:
            self.assertEqual(autonomy.overview([])['coverage'], {'workflows': 0, 'tasks': 0}); rows.assert_not_called()

    def test_builder_evidence_is_bound_to_task_original_hash_and_valid_cursor_and_missing_is_explicit(self):
        item = task(); call_id, artifact_id = str(uuid.uuid4()), str(uuid.uuid4()); raw = 'Original π response'.encode()
        call = dict(task_id=item['id'], response_artifact_id=artifact_id, response_sha256=digest(raw))
        with patch.object(autonomy, 'one', side_effect=lambda kind, *args: item if kind == 'autonomy_tasks' else call), patch.object(store, 'artifact_bytes', return_value=raw) as read:
            result = autonomy.builder_evidence(item['study_id'], item['id'], call_id, 'response', 0)
            self.assertEqual(result['original_sha256'], digest(raw)); self.assertEqual(result['status'], 'recorded')
            read.assert_called_once_with(item['study_id'], artifact_id, maximum=1048577)
            for cursor in ['-1', 'x', '１２']:
                with self.assertRaisesRegex(ValueError, 'cursor is invalid'): autonomy.builder_evidence(item['study_id'], item['id'], call_id, 'response', cursor)
            call['response_sha256'] = 'a' * 64
            with self.assertRaisesRegex(ValueError, 'hash differs'): autonomy.builder_evidence(item['study_id'], item['id'], call_id, 'response', 0)
            call['response_artifact_id'] = None
            self.assertEqual(autonomy.builder_evidence(item['study_id'], item['id'], call_id, 'response', 0)['status'], 'missing')
            call['task_id'] = str(uuid.uuid4())
            with self.assertRaisesRegex(ValueError, 'outside the selected claim'): autonomy.builder_evidence(item['study_id'], item['id'], call_id, 'response', 0)

    def test_history_merges_native_and_controller_turn_numbers_with_a_bounded_cursor(self):
        item = task('building'); item['attempts'] = 40
        turns = [{'number': n} for n in range(40, 30, -1)]; calls = [{'number': n} for n in range(35, 25, -1)]
        with patch.object(autonomy, 'one', return_value=item), patch.object(autonomy, 'rows', side_effect=lambda kind, *args, **kwargs: turns if kind == 'autonomy_turns' else calls) as rows:
            result = autonomy.history(item['study_id'], item['id'], before='41')
            self.assertEqual(result['next_before'], 31); self.assertEqual(result['coverage']['returned'], 10)
            self.assertEqual(result['coverage']['builder_calls_returned'], 5)
            self.assertTrue(all(call.kwargs['number'] == 'lt.41' for call in rows.call_args_list))
            for cursor in ['0', '42', '-1', 'text', '１２']:
                with self.assertRaisesRegex(ValueError, 'retained attempt number'): autonomy.history(item['study_id'], item['id'], cursor)

    def test_advance_respects_repair_routing_and_counts_completed_checks_without_claiming_scientific_completion(self):
        item = task(); item['_tick'] = 10; item['context'] = {'prepared_id': 'plan', 'completed_job_ids': ['first'], 'job_ids': ['second']}
        with patch.object(autonomous_repair, 'route', return_value=None), patch.object(autonomy, 'inventory', return_value=[{'id': name} for name in ['first', 'second', 'third']]), patch.object(autonomy.time, 'monotonic', return_value=13.5), patch.object(autonomy, 'control', return_value={'recorded': True}) as control:
            autonomy.advance(item, 'queued'); queued = control.call_args.args[2]
            self.assertEqual(queued['context']['evidence_progress']['completed_checks'], 1)
            self.assertEqual(queued['usage']['controller_wall_seconds'], 3.5); self.assertIsNone(queued['usage']['measured_charges_usd'])
            autonomy.advance(item, 'resolved'); self.assertEqual(control.call_args.args[2]['context']['evidence_progress']['completed_checks'], 2)
        with patch.object(autonomous_repair, 'route', return_value={'result': {'status': 'repair-active'}}), patch.object(autonomy, 'control') as control:
            self.assertEqual(autonomy.advance(item, 'blocked'), {'status': 'repair-active'}); control.assert_not_called()
        with patch.object(autonomous_repair, 'route', return_value={'status': 'queued', 'context': {'retained': True}, 'reason': 'repair resumed'}), patch.object(autonomy, 'control') as control:
            autonomy.advance(item, 'blocked', stage='building'); self.assertEqual(control.call_args.args[2]['reason'], 'repair resumed')

    def test_pricing_requires_server_owned_live_resource_bounds_and_valid_integer_ceiling(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(autonomy, '__file__', str(Path(folder) / 'autonomy.py')), patch.object(autonomy.autonomy_costs, 'builtin_quote', return_value=None), patch.object(autonomy.autonomy_costs, 'bounded_quote', return_value=None):
            path = Path(folder) / 'policies/autonomy-experiment-costs-v2.json'; path.parent.mkdir()
            job = {'raw_input': '{"fixture":true}'}; protocol = {'document': {'execution': {'runner': 'fixture'}}}
            valid = dict(spec_sha256=digest(job['raw_input'].encode()), currency='USD', hard_resource_bounds_verified=True, valid_until='2100-01-01T00:00:00Z', maximum_cents=25)
            path.write_bytes(canonical({'quotes': [valid]})); self.assertEqual(autonomy.price(job, protocol), {'cents': 25, 'quote': valid})
            for change in [dict(spec_sha256='wrong'), dict(currency='EUR'), dict(hard_resource_bounds_verified=False), dict(valid_until='2000-01-01T00:00:00Z')]:
                path.write_bytes(canonical({'quotes': [dict(valid, **change)]}))
                with self.assertRaisesRegex(ValueError, 'server-approved cost envelope'): autonomy.price(job, protocol)
            for ceiling in [True, 1.5, 0, 10001]:
                path.write_bytes(canonical({'quotes': [dict(valid, maximum_cents=ceiling)]}))
                with self.assertRaisesRegex(ValueError, 'price ceiling'): autonomy.price(job, protocol)
        self.assertEqual(autonomy.price({}, {'document': {}})['cents'], 1)
        with patch.object(autonomy.autonomy_costs, 'builtin_quote', return_value={'cents': 7}), patch.object(autonomy.autonomy_costs, 'bounded_quote') as bounded:
            self.assertEqual(autonomy.price({'raw_input': '{}'}, {'document': {'execution': {}}}), {'cents': 7}); bounded.assert_not_called()

    def test_retained_check_dependency_closure_is_ordered_and_shared_dependencies_appear_once(self):
        item = task(); item['context'] = {'prepared_id': 'retained-plan', 'selected_check_id': 'last'}
        first = dict(id='first', request={'dependencies': []}); middle = dict(id='middle', request={'dependencies': [{'job_id': 'first'}]})
        last = dict(id='last', request={'dependencies': [{'job_id': 'middle'}, {'job_id': 'first'}]})
        with patch.object(autonomy, 'choices', return_value=({'jobs': [last, middle, first]}, [last])):
            self.assertEqual([job['id'] for job in autonomy.frozen_jobs(item)], ['first', 'middle', 'last'])

    def jobs(self):
        return [dict(id=name, campaign_id='campaign', execution_mode='automatic', request=dict(request_id=name, dependencies=[], raw_input='{}')) for name in ['first', 'second']]

    def test_queueing_prices_the_entire_closure_before_reservation_then_retains_job_and_decision_identities(self):
        item = task(); decision = {'tolerance': .05}; item['context'] = {'proposal': {'protocol': {'decision': decision}}}
        jobs = self.jobs(); events = []
        def price(job, protocol): events.append('price:' + job['request_id']); return {'cents': 7, 'quote': {'version': 'fixture'}}
        def control(task, operation, payload): events.append('reserve:' + payload['job_id']); return {}
        def one(kind, *args): return {'protocol_id': 'protocol'} if kind == 'campaigns' else {'document': {'resource_limits': {'wall_seconds': 60}, 'execution': {}}}
        with patch.object(autonomy, 'frozen_jobs', return_value=jobs), patch.object(autonomy, 'one', side_effect=one), patch.object(autonomy, 'price', side_effect=price), patch.object(autonomy, 'control', side_effect=control) as reservation, patch.object(autonomy.scheduler, 'definition', side_effect=lambda value: (None, value, None)), patch.object(autonomy.scheduler, 'enqueue') as enqueue, patch.object(autonomy, 'advance', return_value={'status': 'waiting'}) as advance:
            self.assertEqual(autonomy.enqueue(item), {'status': 'waiting'})
            self.assertEqual(events, ['price:first', 'price:second', 'reserve:first', 'reserve:second']); self.assertEqual(enqueue.call_count, 2)
            self.assertEqual(reservation.call_args.args[2]['required_seconds'], 240)
            context = advance.call_args.kwargs['context']; self.assertEqual(context['job_ids'], ['first', 'second']); self.assertEqual(context['campaign_ids'], ['campaign'])
            self.assertEqual(context['decision_lock'], digest(canonical(decision))); self.assertEqual(advance.call_args.kwargs['delay'], 30)

    def test_unreviewed_prerequisites_manual_execution_cost_failure_or_denied_reservation_prevent_dispatch(self):
        for case in ['review', 'manual', 'price', 'denied']:
            item = task(); jobs = self.jobs()
            if case == 'review': jobs[0]['request']['dependencies'] = [{'job_id': 'prerequisite', 'requirement': 'reviewed-evidence'}]
            if case == 'manual': jobs[0]['execution_mode'] = 'manual'
            def one(kind, *args): return {'protocol_id': 'protocol'} if kind == 'campaigns' else {'document': {'resource_limits': {'wall_seconds': 60}}}
            with self.subTest(case=case), patch.object(autonomy, 'frozen_jobs', return_value=jobs), patch.object(autonomy, 'rows', return_value=[{'status': 'complete'}]), patch.object(autonomy, 'one', side_effect=one), patch.object(autonomy, 'price', side_effect=ValueError('cost envelope unavailable') if case == 'price' else lambda *args: {'cents': 1, 'quote': {}}), patch.object(autonomy, 'control', return_value={'denied': 'budget'}) as control, patch.object(autonomy.scheduler, 'definition', return_value=(None, {}, None)), patch.object(autonomy.scheduler, 'enqueue') as enqueue, patch.object(autonomy, 'advance', return_value={'status': 'budget'}):
                if case == 'denied': self.assertEqual(autonomy.enqueue(item), {'status': 'budget'})
                else:
                    with self.assertRaises(ValueError): autonomy.enqueue(item)
                    control.assert_not_called()
                enqueue.assert_not_called()

    def test_process_preserves_native_checkpoint_and_handoff_when_a_failed_turn_cannot_be_retried(self):
        item = task('building'); provenance = {'checkpoint': {'sha256': 'retained'}, 'feedback': 'retained feedback', 'native_repair_handoff': {'id': 'handoff'}}
        failure = model.ModelFailure('confirmed failure', 'failed-turn', provenance)
        with patch.object(autonomous_repair, 'enabled', return_value=False), patch.object(autonomy, 'step', side_effect=failure), patch.object(runtime_retry, 'decision', return_value=None), patch.object(autonomy, 'advance') as advance:
            autonomy.process(item); context = advance.call_args.kwargs['context']
            self.assertEqual(advance.call_args.args[1], 'blocked'); self.assertEqual(context['builder_checkpoint'], provenance['checkpoint'])
            self.assertEqual(context['native_repair_handoff'], {'id': 'handoff'}); self.assertEqual(context['repair_failure_turn_id'], 'failed-turn')
        retry = {'reason': 'confirmed transient', 'delay_seconds': 30}
        with patch.object(autonomous_repair, 'enabled', return_value=False), patch.object(autonomy, 'step', side_effect=failure), patch.object(runtime_retry, 'decision', return_value=retry), patch.object(autonomy, 'advance') as advance:
            autonomy.process(item); self.assertEqual(advance.call_args.args[1], 'queued'); self.assertEqual(advance.call_args.kwargs['delay'], 30)

    def test_process_reconciles_unconfirmed_persistence_and_never_replays_the_operation(self):
        for error in [ValueError('invalid retained specification'), RuntimeError('private diagnostic must not be echoed')]:
            item = task()
            with patch.object(autonomous_repair, 'enabled', return_value=False), patch.object(autonomy, 'step', side_effect=error) as step, patch.object(autonomy, 'advance', side_effect=store.StoreError('unconfirmed write')):
                result = autonomy.process(item); self.assertEqual(result['status'], 'unconfirmed'); self.assertNotIn('private diagnostic', result['reason']); step.assert_called_once_with(item)
        item['context'] = {'repair_progress': {'state': next(iter(autonomous_repair.ACTIVE))}}
        with patch.object(autonomous_repair, 'enabled', return_value=True), patch.object(autonomous_repair, 'drive', return_value={'status': 'repairing'}) as drive:
            self.assertEqual(autonomy.process(item), {'status': 'repairing'}); drive.assert_called_once_with(item, autonomy.step, autonomy.advance)

    def test_handler_paused_native_runtime_never_loads_a_prompt_or_leases_work(self):
        with patch.object(model, 'OPENCLAW', True), patch.object(autonomy, 'runtime_health', return_value={'status': 'paused'}), patch.object(model, 'load_prompt') as prompt, patch.object(store, 'rpc') as rpc:
            result = autonomy.handler({}); self.assertEqual(result['slots'], 0); prompt.assert_not_called(); rpc.assert_not_called()

    def test_handler_recovers_only_hash_bound_current_context_and_retains_failed_candidates(self):
        item = task(); candidates = [dict(id=item['id'], study_id=item['study_id'], owner_id=item['actor_id']), dict(id=str(uuid.uuid4()), study_id=item['study_id'], owner_id=item['actor_id'])]
        request = {'fixture': 'current request'}; operations = []
        def rpc(operation, payload):
            operations.append((operation, payload))
            if operation in ['research_autonomy_resume_turns', 'research_autonomy_runtime_observe']: return []
            if operation == 'research_autonomy_budget_candidates': return candidates
            if operation == 'research_autonomy_resume_budget': return {'status': 'queued'}
            if operation == 'research_autonomy_control': return None
            raise AssertionError(operation)
        with patch.dict(os.environ, {'RESEARCH_AUTONOMY_CONTEXT_RECOVERY': model.CONTEXT_BUDGET_VERSION}), patch.object(model, 'OPENCLAW', False), patch.object(model, 'load_prompt', return_value=({}, 'Server policy')), patch.object(model, 'request', return_value=request), patch.object(autonomous_repair, 'discover', return_value=[]), patch.object(autonomy, 'one', side_effect=[item, ValueError('retained source unavailable')]), patch.object(autonomy, 'rows', return_value=[{'deadline_at': '2100-01-01T00:00:00Z'}]), patch.object(autonomy, 'build_context', return_value={'fixture': True}), patch.object(store, 'rpc', side_effect=rpc):
            result = autonomy.handler({})
        self.assertEqual(result['context_recovery'], [dict(task_id=item['id'], status='queued'), dict(task_id=candidates[1]['id'], status='retained-blocker')])
        resume = next(payload for operation, payload in operations if operation == 'research_autonomy_resume_budget')
        self.assertEqual(resume['p_request_sha256'], digest(canonical(request))); self.assertEqual(resume['p_request_bytes'], len(canonical(request)))
        self.assertEqual([outcome['status'] for outcome in result['outcomes']], ['idle', 'idle'])

    def test_handler_records_optional_maintenance_failures_and_never_replaces_a_failed_slot_or_recovery(self):
        item = task(); recovery = [dict(id=str(uuid.uuid4()), study_id=item['study_id'], owner_id=item['actor_id']) for _ in range(2)]
        recoveries = iter([{'status': 'queued'}, store.StoreError('retained failure')])
        def rpc(operation, payload):
            if operation == 'research_autonomy_context_candidates': return recovery
            if operation == 'research_autonomy_resume_context':
                result = next(recoveries)
                if isinstance(result, Exception): raise result
                return result
            if operation in ['research_autonomy_resume_turns', 'research_autonomy_runtime_observe']: raise store.StoreError('fixture maintenance unavailable')
            if operation == 'research_autonomy_control': return copy.deepcopy(item)
            raise AssertionError(operation)
        with patch.dict(os.environ, {'RESEARCH_AUTONOMY_CONTEXT_RECOVERY': autonomy.evidence.VERSION}), patch.object(model, 'OPENCLAW', False), patch.object(model, 'load_prompt', return_value=({}, 'Server policy')), patch.object(autonomous_repair, 'discover', side_effect=ValueError('fixture discovery unavailable')), patch.object(autonomy, 'process', side_effect=[dict(id=item['id'], status='queued', reason='retained'), TimeoutError('unconfirmed slot')]) as process, patch.object(store, 'rpc', side_effect=rpc):
            result = autonomy.handler({})
        self.assertEqual(len(result['maintenance_faults']), 2); self.assertEqual(process.call_count, 2)
        self.assertEqual(result['outcomes'][1], {'status': 'unconfirmed', 'error_type': 'TimeoutError'})
        self.assertEqual(result['context_recovery'][1]['status'], 'retained-blocker')


class ProposedExperiments(unittest.TestCase):
    def setUp(self):
        self.db = Recorded(); self.db.task['context'] = {}; self.artifacts = [{'sha256': digest(raw)} for raw in self.db.bytes.values()]
        self.enterContext(patch.object(autonomy, 'one', side_effect=self.db.one))
        self.enterContext(patch.object(autonomy, 'rows', side_effect=lambda kind, *args, **kwargs: self.artifacts if kind == 'artifacts' else []))
        def artifact(study, actor, raw, id, media, provenance):
            value = {'id': id, 'sha256': digest(raw), 'byte_count': len(raw)}; self.artifacts.append(value); return value
        self.upload = self.enterContext(patch.object(store, 'artifact', side_effect=artifact))
        self.append = self.enterContext(patch.object(store, 'append'))

    def test_valid_source_arithmetic_binds_actual_implementation_bytes_and_preserves_the_original_decision(self):
        decision = copy.deepcopy(self.db.proposal['protocol']['decision'])
        result = autonomy.validate_proposal(self.db.task, self.db.proposal)
        self.assertEqual(result['protocol']['decision'], decision); self.assertEqual(result['experiment_type'], 'independent-check')
        self.upload.assert_called_once(); raw = self.upload.call_args.args[2]
        self.assertIn({'identity': 'source-arithmetic-v1:implementation', 'sha256': digest(raw)}, result['protocol']['input_versions'])
        self.assertEqual(self.append.call_args.args[2], 'artifacts')

    def test_validation_rejects_changed_claim_frozen_decision_source_or_unknown_input_hash(self):
        for case in ['fields', 'claim', 'missing-decision', 'lock', 'origin', 'stage', 'method', 'source', 'unknown-input']:
            proposal = copy.deepcopy(self.db.proposal); self.db.task['context'] = {}
            if case == 'fields': proposal['invented_values'] = [1]
            elif case == 'claim': proposal['protocol']['hypotheses'] = 'Weakened replacement claim'
            elif case == 'missing-decision': proposal['protocol'].pop('decision')
            elif case == 'lock': self.db.task['context']['decision_lock'] = 'f' * 64
            elif case == 'origin': proposal['protocol']['data_origin'] = 'new-empirical'
            elif case == 'stage': proposal['protocol']['stage'] = 'confirmatory'
            elif case == 'method': proposal['experiment_type'] = 'new-measurement'
            elif case == 'source': self.db.data['sources', self.db.claim['source_id']]['sha256'] = 'b' * 64
            else: proposal['protocol']['input_versions'].append({'identity': 'unavailable', 'sha256': 'f' * 64})
            with self.subTest(case=case), self.assertRaises(ValueError): autonomy.validate_proposal(self.db.task, proposal)
            self.db.data['sources', self.db.claim['source_id']]['sha256'] = 'a' * 64

    def test_decision_lock_is_recovered_from_retained_claim_protocol_after_an_enqueue_acknowledgement_loss(self):
        protocol_id = next(id for kind, id in self.db.data if kind == 'protocols')
        self.db.task['context'] = {'acquisition_reuse': {'claim_protocol_id': protocol_id}}
        self.assertEqual(autonomy.validate_proposal(self.db.task, self.db.proposal)['protocol']['decision'], self.db.protocol['decision'])
        self.db.data['protocols', protocol_id]['document'] = dict(self.db.protocol, decision={'changed': True})
        with self.assertRaisesRegex(ValueError, 'decision criterion is frozen'): autonomy.validate_proposal(self.db.task, self.db.proposal)

    def test_frozen_new_and_shared_jobs_keep_claim_specific_protocol_and_same_observation_mapping(self):
        proposal = self.db.proposal; item = self.db.task
        for reused in [False, True]:
            item['context'] = {'proposal': copy.deepcopy(proposal)}
            campaign_id = next(id for kind, id in self.db.data if kind == 'campaigns')
            with self.subTest(reused=reused), patch.object(shared_campaigns, 'register', return_value={'campaign_id': campaign_id, 'shared': reused}) as register:
                jobs = autonomy.frozen_jobs(item)
            self.assertGreater(len(jobs), 0); self.assertEqual(len({job['id'] for job in jobs}), len(jobs))
            self.assertTrue(all(job['campaign_id'] == campaign_id for job in jobs))
            reuse = item['context']['acquisition_reuse']; self.assertEqual(reuse['reused'], reused)
            self.assertEqual(reuse['configuration_mapping'], [{'claim_configuration': 'grid', 'acquisition_configurations': ['grid']}])
            self.assertIn('not independent replications', reuse['limitation'])
            records = register.call_args.args[2]; self.assertEqual({row['kind'] for row in records}, {'protocols', 'configurations', 'campaigns', 'claim_checks'})


class IntakeController(unittest.TestCase):
    def setUp(self):
        self.item = task('intake'); self.item['claim_id'] = None
        self.study = {'id': self.item['study_id'], 'title': 'Synthetic paper'}
        self.source = {'id': str(uuid.uuid4()), 'sha256': 'a' * 64}
        self.artifact = str(uuid.uuid4()); self.packet = {'pages': [{'page': 1, 'text': 'Synthetic exact source quote.'}, {'page': 2, 'text': ' '}]}
        self.context = {'source_id': self.source['id'], 'text_artifact_id': self.artifact, 'chunk_index': 0, 'last_failure': 'previous invalid quote'}
        self.chunk = {'pages': [1], 'text': self.packet['pages'][0]['text']}
        self.claims = [{'id': str(uuid.uuid4()), 'source_id': self.source['id'], 'description': 'Exact contribution'}]
        self.turn = {'id': str(uuid.uuid4()), 'status': 'complete', 'output': {'action': 'discover', 'claims': [{'quote': 'Synthetic exact source quote.', 'page': 1}]}}
        self.one = self.enterContext(patch.object(autonomy, 'one', side_effect=lambda kind, *args: self.study if kind == 'studies' else self.source))
        self.rows = self.enterContext(patch.object(autonomy, 'rows', side_effect=lambda kind, *args, **kwargs: []))
        self.read = self.enterContext(patch.object(store, 'artifact_bytes', return_value=canonical(self.packet)))
        self.chunks = self.enterContext(patch.object(autonomy.sources, 'chunks', return_value=[self.chunk]))
        self.candidates = self.enterContext(patch.object(autonomy.sources, 'candidates', return_value=self.claims))
        self.model = self.enterContext(patch.object(model, 'call', return_value=self.turn))
        self.advance = self.enterContext(patch.object(autonomy, 'advance', side_effect=lambda item, status, **values: dict(status=status, **values)))
        self.seed = self.enterContext(patch.object(autonomy, 'seed'))
        self.append = self.enterContext(patch.object(store, 'append_bundle'))
        self.item['context'] = copy.deepcopy(self.context)

    def test_child_and_parallel_intakes_delegate_before_any_extra_model_turn(self):
        self.item['intake_parent_id'] = str(uuid.uuid4())
        with patch.object(intake_parallel, 'extract', return_value={'status': 'child'}) as extract:
            self.assertEqual(autonomy.intake(self.item), {'status': 'child'}); extract.assert_called_once_with(self.item, self.study)
        del self.item['intake_parent_id']; self.item['intake_pool'] = True
        with patch.object(intake_parallel, 'coordinate', return_value={'status': 'parallel'}) as coordinate:
            self.assertEqual(autonomy.intake(self.item), {'status': 'parallel'})
            self.assertEqual(coordinate.call_args.args[1], self.context)
        self.model.assert_not_called()

    def test_registered_source_grounded_plan_seeds_existing_claims_without_new_extraction(self):
        self.item['context'] = {'intake_pool_version': 'warm-intake-v1'}
        self.rows.side_effect = lambda kind, *args, **kwargs: self.claims if kind == 'claims' else []
        with patch.object(autonomy.preparation, 'registered', return_value=({'id': 'plan'}, b'original')), patch.object(autonomy.preparation, 'prepare', return_value={'prepared': {'id': 'prepared'}}) as prepare:
            result = autonomy.intake(self.item)
            self.assertEqual(result['status'], 'finished'); self.assertEqual(result['context']['prepared_id'], 'prepared')
            self.seed.assert_called_once_with(self.item, self.claims, {'prepared_id': 'prepared'})
            self.assertEqual(prepare.call_args.args[2]['action'], 'prepare'); self.model.assert_not_called(); self.read.assert_not_called()

    def test_registered_inventory_above_two_hundred_claims_is_not_reported_complete(self):
        self.item['context'] = {}
        for review in [[], [{'status': 'pending'}]]:
            self.rows.side_effect = lambda kind, *args, **kwargs: [{}] * 201 if kind == 'claims' else review
            with patch.object(autonomy.preparation, 'registered', return_value=({'id': 'plan'}, b'original')), patch.object(autonomy.preparation, 'prepare', return_value={'prepared': {'id': 'prepared'}}), patch.object(autonomy.sources, 'original', return_value=self.context), self.assertRaisesRegex(ValueError, 'Claim inventory exceeds 200'):
                autonomy.intake(self.item)
        self.seed.assert_not_called(); self.model.assert_not_called()

    def test_new_source_and_pending_registered_review_retain_originals_before_queueing_metadata(self):
        for plan, review in [(None, []), ({'id': 'plan'}, [{'status': 'pending'}])]:
            self.item['context'] = {}; self.rows.side_effect = lambda kind, *args, **kwargs: self.claims if kind == 'claims' else review
            with patch.object(autonomy.preparation, 'registered', return_value=(plan, b'original')), patch.object(autonomy.preparation, 'prepare', return_value={'prepared': {'id': 'prepared'}}), patch.object(autonomy.sources, 'original', return_value=self.context) as original:
                result = autonomy.intake(self.item); self.assertEqual(result['status'], 'queued'); original.assert_called_once_with(self.study, self.item['actor_id'])
                self.assertEqual(result['context']['text_artifact_id'], self.artifact)
                self.assertEqual(result['context'].get('prepared_id'), 'prepared' if plan else None)
        self.model.assert_not_called()

    def test_successful_quotes_append_and_seed_exact_claims_and_advance_only_the_examined_chunk(self):
        result = autonomy.intake(self.item)
        self.assertEqual(result['status'], 'queued'); self.assertEqual(result['context']['chunk_index'], 1)
        self.assertNotIn('last_failure', result['context']); self.assertEqual(result['context']['last_turn_id'], self.turn['id'])
        self.append.assert_called_once_with(self.study['id'], self.item['actor_id'], [{'kind': 'claims', 'record': self.claims[0]}])
        self.assertEqual(self.model.call_args.args[1]['previous_feedback'], 'previous invalid quote')
        self.assertEqual(self.item['context'], self.context)

    def test_denied_or_unconfirmed_model_responses_retain_feedback_and_never_append_claims(self):
        for answer, status in [({'denied': 'budget'}, 'budget'), ({'status': 'unknown', 'reason': 'unconfirmed dispatch'}, 'queued')]:
            self.model.return_value = answer; result = autonomy.intake(self.item)
            self.assertEqual(result['status'], status); self.assertEqual(result['context']['chunk_index'], 0)
            if status == 'queued': self.assertEqual(result['context']['last_failure'], 'unconfirmed dispatch')
        self.append.assert_not_called(); self.seed.assert_not_called()

    def test_wrong_action_or_invalid_grounding_keeps_the_rejected_turn_and_corrective_feedback(self):
        self.turn['output']['action'] = 'choose-check'
        result = autonomy.intake(self.item); self.assertIn('candidate quotes', result['context']['last_failure'])
        self.candidates.assert_not_called(); self.turn['output']['action'] = 'discover'
        self.candidates.side_effect = ValueError('Exact quote absent from page 1')
        result = autonomy.intake(self.item); self.assertEqual(result['context']['last_failure'], 'Exact quote absent from page 1')
        self.assertEqual(result['context']['last_turn_id'], self.turn['id']); self.append.assert_not_called()
        self.turn['output']['action'] = 'blocked'
        with patch.object(autonomy, 'blocked', return_value={'status': 'external'}) as blocked:
            self.assertEqual(autonomy.intake(self.item), {'status': 'external'}); blocked.assert_called_once_with(self.item, self.turn['output'])

    def test_candidate_limit_retains_unadmitted_response_and_does_not_invent_complete_paper_coverage(self):
        self.rows.side_effect = lambda kind, *args, **kwargs: [{'id': str(uuid.uuid5(uuid.NAMESPACE_OID, str(n)))} for n in range(200)] if kind == 'claims' else []
        result = autonomy.intake(self.item)
        self.assertEqual(result['status'], 'blocked'); self.assertEqual(result['context']['unadmitted_candidates'], 1)
        self.assertEqual(result['context']['last_turn_id'], self.turn['id']); self.assertEqual(result['context']['text_chunks_examined'], 0)
        self.append.assert_not_called(); self.seed.assert_not_called()

    def test_pending_review_metadata_records_authors_and_source_turn_provenance_without_selecting_claims(self):
        self.rows.side_effect = lambda kind, *args, **kwargs: [{'status': 'pending'}] if kind == 'paper_reviews' else []
        self.turn['output']['paper_metadata'] = {'title': {'text': 'Synthetic title'}}
        metadata = dict(title='Synthetic title', domain='Networking', suggestions={'title': 'source-backed'}, title_fallback=False)
        with patch.object(autonomy.sources, 'paper_metadata', return_value=metadata), patch.object(autonomy.paper_titles, 'extract_authors', return_value=['Fixture Author']), patch.object(store, 'rpc') as rpc:
            result = autonomy.intake(self.item); payload = rpc.call_args.args[1]['p_payload']
            self.assertTrue(result['context']['metadata_examined']); self.assertEqual(payload['metadata']['authors'], ['Fixture Author'])
            self.assertEqual(payload['metadata']['turn_id'], self.turn['id']); self.assertEqual(payload['metadata']['text_artifact_id'], self.artifact)
            self.assertTrue(self.model.call_args.args[1]['paper_metadata_requested'])

    def test_prepared_inventory_skips_new_candidate_creation_and_examines_one_metadata_chunk_before_organization(self):
        self.item['context']['prepared_id'] = 'prepared'; self.item['intake_pool'] = True
        self.rows.side_effect = lambda kind, *args, **kwargs: [{'status': 'pending'}] if kind == 'paper_reviews' else self.claims
        self.turn['output']['paper_metadata'] = {}
        with patch.object(autonomy.sources, 'paper_metadata', return_value=dict(title=None, domain=None, suggestions={}, title_fallback=True)), patch.object(autonomy.paper_titles, 'extract_authors', return_value=[]), patch.object(store, 'rpc'):
            result = autonomy.intake(self.item)
            self.assertEqual(result['context']['text_chunks_examined'], 1); self.assertEqual(result['context']['chunk_index'], 1)
            self.assertEqual(result['context']['prepared_id'], 'prepared'); self.candidates.assert_not_called(); self.append.assert_not_called()

    def test_end_of_text_marks_empty_inventory_blocked_and_preserves_missing_pages_and_unexamined_visuals(self):
        self.item['context']['chunk_index'] = 1
        for claims, status in [([], 'blocked'), (self.claims, 'finished')]:
            self.rows.side_effect = lambda kind, *args, **kwargs: claims if kind == 'claims' else []
            result = autonomy.intake(self.item); self.assertEqual(result['status'], status)
            self.assertEqual(result['context']['missing_text_pages'], [2]); self.assertIn('figures, tables', result['context']['coverage'])
        self.model.assert_not_called()

    def test_pending_reviews_organize_retained_candidates_without_reextracting_or_claiming_scientific_completion(self):
        review = {'status': 'pending'}; self.rows.side_effect = lambda kind, *args, **kwargs: self.claims if kind == 'claims' else [review]
        with patch.object(autonomy, 'organize_review', return_value={'status': 'organized'}) as organize:
            self.item['context']['organize_existing_only'] = True
            self.assertEqual(autonomy.intake(self.item), {'status': 'organized'})
            del self.item['context']['organize_existing_only']; self.item['context']['chunk_index'] = 1
            self.assertEqual(autonomy.intake(self.item), {'status': 'organized'}); self.assertEqual(organize.call_count, 2)
        self.model.assert_not_called()


class ScopedEvidence(unittest.TestCase):
    def setUp(self):
        self.item = task(); self.source_id = str(uuid.uuid4()); self.run_id = str(uuid.uuid4()); self.protocol_id = str(uuid.uuid4())
        self.claim = dict(id=self.item['claim_id'], source_id=self.source_id)
        self.job = dict(id=str(uuid.uuid4()), title='Retained check', execution_mode='automatic', campaign_id=str(uuid.uuid4()))
        self.records = {('claims', self.claim['id']): self.claim, ('sources', self.source_id): {'id': self.source_id},
                        ('campaigns', self.job['campaign_id']): {'protocol_id': self.protocol_id}, ('protocols', self.protocol_id): {'document': {'frozen': True}}}
        self.enterContext(patch.object(autonomy, 'one', side_effect=lambda kind, study, id: self.records[kind, id]))
        self.enterContext(patch.object(autonomy, 'inventory', return_value=[self.job]))
        self.enterContext(patch.object(autonomy, 'retained_jobs', return_value=[{'run_id': self.run_id}]))

    def read(self, kind, id, cursor=0): return autonomy.inspect_evidence(self.item, dict(kind=kind, id=id, cursor=cursor))

    def test_numbered_turn_reads_are_claim_bound_and_fresh_reviews_cannot_inspect_previous_judgments(self):
        id = str(uuid.uuid4()); self.records['autonomy_turns', id] = dict(id=id, task_id=self.item['id'], number=1, output={'retained': True})
        result = self.read('controller-turn', id); self.assertEqual(result['result_sha256'], digest(canonical(result['result'])))
        self.assertIn('"retained":true', result['result']['fragment'])
        self.records['autonomy_turns', id]['task_id'] = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, 'another claim'): self.read('controller-turn', id)
        self.item['stage'] = 'critic'
        with self.assertRaisesRegex(ValueError, 'earlier model judgments'): self.read('controller-turn', id)

    def test_runtime_contract_reference_requires_the_current_exact_contract_identity_and_allowed_phase(self):
        id = str(uuid.uuid4()); value = {'version': 'fixture-runtime-contract'}
        with patch.object(autonomy.inputs, 'runner_contracts', return_value={'runner': value}), patch.object(autonomy.inputs, 'runner_reference', return_value={'read_request': {'id': id}}):
            result = self.read('runtime-contract', id); self.assertEqual(result['result']['document_sha256'], digest(canonical(value)))
            with self.assertRaisesRegex(ValueError, 'identity/version is unavailable'): self.read('runtime-contract', str(uuid.uuid4()))
            self.item['stage'] = 'assessment'
            with self.assertRaisesRegex(ValueError, 'only readable for method'): self.read('runtime-contract', id)

    def test_shared_acquisition_reads_are_planning_only_and_require_the_same_original_source(self):
        id = str(uuid.uuid4()); self.records['shared_campaigns', id] = {'acquisition': {'source_id': self.source_id}}
        self.assertIn(self.source_id, self.read('acquisition', id)['result']['fragment'])
        self.records['shared_campaigns', id]['acquisition']['source_id'] = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, 'different source'): self.read('acquisition', id)
        self.item['stage'] = 'critic'
        with self.assertRaisesRegex(ValueError, 'planning-only'): self.read('acquisition', id)

    def test_built_packages_are_bound_to_the_source_or_exact_candidate_and_original_artifact(self):
        id = str(uuid.uuid4()); artifact = str(uuid.uuid4()); self.records['builder_packages', id] = dict(source_id=self.source_id, candidate_artifact_id=artifact)
        document = {'archive': {'artifact_id': artifact}, 'source': 'exact code bytes'}
        with patch.object(store, 'artifact_bytes', return_value=b'{"fixture":true}') as read, patch.object(experiment_builder, 'project_document', return_value=document):
            self.assertIn('fresh claim-specific reviews', self.read('package', id)['result']['fragment']); read.assert_called_once_with(self.item['study_id'], artifact, maximum=100000)
            self.records['builder_packages', id]['source_id'] = str(uuid.uuid4())
            with self.assertRaisesRegex(ValueError, 'another source'): self.read('package', id)
            self.item['stage'] = 'critic'; self.item['context']['proposal'] = {'protocol': {}}
            self.assertIn('exact code bytes', self.read('package', artifact)['result']['fragment'])
            with self.assertRaisesRegex(ValueError, 'outside this exact candidate'): self.read('package', id)
        with patch.object(experiment_builder, 'project_document', return_value=None), self.assertRaisesRegex(ValueError, 'outside this exact candidate'): self.read('package', artifact)

    def test_check_inventories_and_protocol_reads_are_restricted_to_the_selected_claim_and_review_candidate(self):
        self.assertEqual(self.read('checks', self.claim['id'])['result']['records'][0]['id'], self.job['id'])
        with self.assertRaisesRegex(ValueError, 'outside this claim'): self.read('checks', str(uuid.uuid4()))
        with patch.object(autonomy, 'check_context', return_value={'id': self.job['id'], 'details': 'small'}):
            self.assertEqual(self.read('check', self.job['id'])['result']['records'][0]['details'], 'small')
        with patch.object(autonomy, 'check_context', return_value={'id': self.job['id'], 'details': 'x' * 9000}):
            self.assertIsNotNone(self.read('check', self.job['id'])['result']['next_cursor'])
        self.assertEqual(json.loads(self.read('protocol', self.protocol_id)['result']['fragment']), {'frozen': True})
        with self.assertRaisesRegex(ValueError, 'outside the authorized'): self.read('protocol', str(uuid.uuid4()))
        self.item['stage'] = 'critic'; self.item['context']['selected_check_id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'outside the authorized'): self.read('check', self.job['id'])
        self.item['context']['proposal'] = {'protocol': {'exact': True}}
        self.assertEqual(json.loads(self.read('protocol', self.item['id'])['result']['fragment']), {'exact': True})
        with self.assertRaisesRegex(ValueError, 'outside this exact candidate'): self.read('protocol', self.protocol_id)

    def test_measurement_and_published_value_reads_keep_missing_zero_and_run_scope_without_earlier_verdicts(self):
        published = str(uuid.uuid4()); self.records['published_values', published] = {'id': published, 'value': '0'}
        rows = [{'id': str(uuid.uuid4()), 'value': None, 'published_id': None}, {'id': str(uuid.uuid4()), 'value': '0', 'published_id': published}]
        with patch.object(autonomy.evidence, 'collect', return_value=rows) as collect:
            result = self.read('measurements', self.run_id)['result']['records']; self.assertIsNone(result[0]['value']); self.assertEqual(result[1]['value'], '0')
            self.assertEqual(self.read('published-values', self.run_id)['result']['records'], [self.records['published_values', published]])
            self.assertEqual(collect.call_args.kwargs, {'run_id': 'eq.' + self.run_id})
            with self.assertRaisesRegex(ValueError, 'Run is outside'): self.read('measurements', str(uuid.uuid4()))
            self.item['stage'] = 'comprehension'
            with self.assertRaisesRegex(ValueError, 'earlier experiment verdicts'): self.read('published-values', self.run_id)
        with self.assertRaisesRegex(ValueError, 'Unsupported read-only evidence'): self.read('unregistered', self.run_id)

    def test_source_reads_distinguish_metadata_only_and_extracted_text_and_never_allow_another_source(self):
        with patch.object(autonomy.inputs, 'source_text', return_value=({'representation': 'metadata-only', 'inline_passage': 'not repeated'}, [])):
            result = self.read('source', self.source_id)['result']; self.assertEqual(result['record_kind'], 'source-metadata-only')
            self.assertNotIn('inline_passage', result['source_material'])
        with patch.object(autonomy.inputs, 'source_text', return_value=({'representation': 'extracted-text'}, [{'text': 'Original passage'}])):
            result = self.read('source', self.source_id)['result']; self.assertEqual(result['records'], [{'text': 'Original passage'}])
        with self.assertRaisesRegex(ValueError, 'outside this claim'): self.read('source', str(uuid.uuid4()))


class PromptContext(unittest.TestCase):
    def setUp(self):
        self.item = task(); self.claim = {'id': self.item['claim_id'], 'source_id': str(uuid.uuid4())}; self.source = {'id': self.claim['source_id'], 'sha256': 'a' * 64}
        self.enterContext(patch.object(autonomy, 'one', side_effect=lambda kind, *args: self.claim if kind == 'claims' else self.source))
        self.enterContext(patch.object(autonomy, 'rows', return_value=[dict(id='turn', number=1, output={'prior_verdict': 'inconclusive'})]))
        self.enterContext(patch.object(autonomy.inputs, 'source_text', return_value=({'representation': 'metadata-only'}, [])))
        self.enterContext(patch.object(autonomy.inputs, 'runner_contracts', return_value={'netem-iperf-v1': {'version': 'fixture'}}))
        self.enterContext(patch.object(autonomy.inputs, 'protocol_contract', return_value={'version': 'fixture'}))
        self.enterContext(patch.object(autonomy, 'coverage_manifest', return_value={'environment_failure_job_ids': []}))
        self.enterContext(patch.object(shared_campaigns, 'available', return_value=[]))
        self.enterContext(patch.object(experiment_builder, 'action_policy', return_value={'version': 'fixture'}))
        self.enterContext(patch.object(experiment_builder, 'enabled', return_value=False))
        self.enterContext(patch.object(experiment_builder, 'project_document', return_value=None))
        self.enterContext(patch.object(autonomy_read_progress, 'prompt', return_value=None))

    def test_category_policy_overrides_legacy_limits_without_resetting_commitments_or_inventing_charges(self):
        for ceiling, remaining in [(-1, None), (1, 0), (1000, 500)]:
            policy = {'max_spend_cents': ceiling, 'version': 'retained-policy'}
            with patch.dict(os.environ, {'RESEARCH_CATEGORY_BUDGETS_ENABLED': 'true'}), patch.object(autonomy, 'current_defaults', return_value={'budget_policy': policy}):
                result = autonomy.build_context(self.item); self.assertEqual(result['remaining_commitment_cents'], remaining)
                self.assertEqual(result['resource_authorization']['committed_cents'], 500); self.assertEqual(result['resource_authorization']['budget_policy'], policy)
        self.item['workflow']['max_spend_cents'] = None
        with patch.dict(os.environ, {'RESEARCH_CATEGORY_BUDGETS_ENABLED': 'false'}): self.assertIsNone(autonomy.build_context(self.item)['remaining_commitment_cents'])

    def test_builder_context_references_prior_outputs_and_preserves_exact_operational_feedback_and_handoff(self):
        self.item['stage'] = 'building'; context = dict(builder_feedback='Exact package failure', runtime_retry={'failed_turn_id': 'retained'},
            builder_work_progress={'retained': True}, builder_recovery_plan={'resume': 'checkpoint'}, repair_directive='Repair instrumentation',
            repair_packet={'retained': 'original'}, native_repair_handoff={'id': 'handoff'}, evidence_read={'result_sha256': 'b' * 64})
        self.item['context'] = context
        with patch.object(experiment_builder, 'enabled', return_value=True), patch.object(experiment_builder, 'catalog', return_value=[{'id': 'retained-package'}]), patch.object(autonomy_read_progress, 'prompt', return_value={'scope': 'current phase'}):
            result = autonomy.build_context(self.item)
        self.assertNotIn('output', result['prior_attempts'][0]); self.assertEqual(result['prior_attempts'][0]['output_reference']['sha256'], digest(canonical({'prior_verdict': 'inconclusive'})))
        for key in ['runtime_retry', 'builder_work_progress', 'builder_recovery_plan', 'repair_directive', 'repair_packet', 'native_repair_handoff', 'evidence_read']: self.assertEqual(result[key], context[key])
        self.assertEqual(result['builder_feedback'], 'Exact package failure'); self.assertEqual(result['available_built_packages'], [{'id': 'retained-package'}])
        self.assertEqual(self.item['context'], context)

    def test_large_prospective_protocol_and_package_remain_hash_bound_reads_and_fresh_reviews_omit_prior_judgments(self):
        self.item['stage'] = 'critic'; protocol = {'execution': {'runner': 'netem-iperf-v1'}, 'retained_details': 'x' * 17000}
        self.item['context'] = {'proposal': {'protocol': protocol, 'experiment_type': 'new-measurement'}, 'last_feedback': 'older verdict', 'last_response_error': 'Typed protocol feedback'}
        package = {'archive': {'artifact_id': str(uuid.uuid4())}, 'code': 'y' * 17000}
        with patch.object(experiment_builder, 'project_document', return_value=package): result = autonomy.build_context(self.item)
        self.assertEqual(result['candidate']['protocol']['sha256'], digest(canonical(protocol)))
        self.assertEqual(result['candidate_package']['sha256'], digest(canonical(package))); self.assertEqual(result['candidate_package']['read_request']['id'], package['archive']['artifact_id'])
        self.assertEqual(result['runner_contracts'], {'netem-iperf-v1': {'version': 'fixture'}})
        for field in ['prior_attempts', 'previous_feedback', 'attempt_number']: self.assertNotIn(field, result)
        self.assertEqual(result['response_contract_feedback'], 'Typed protocol feedback')

    def test_selected_registered_check_references_full_protocol_without_importing_unrelated_evidence(self):
        self.item['stage'] = 'comprehension'; selected_id = str(uuid.uuid4()); self.item['context'] = {'prepared_id': 'retained', 'selected_check_id': selected_id}
        protocol = {'retained': 'x' * 33000}; selected = {'id': selected_id, 'campaign': {'id': 'campaign'}, 'protocol': protocol,
            'protocol_reference': {'id': 'protocol'}, 'retained_input_inventory': {'sha256': 'c' * 64}}
        with patch.object(autonomy, 'choices', return_value=({}, [{'id': selected_id}])), patch.object(autonomy, 'check_context', return_value=selected): result = autonomy.build_context(self.item)
        self.assertEqual(result['registered_checks'], []); self.assertEqual(result['candidate']['protocol']['sha256'], digest(canonical(protocol)))
        self.assertEqual(result['candidate']['retained_input_inventory'], selected['retained_input_inventory'])

    def test_assessment_prompt_preserves_inconclusive_numerical_result_and_separates_environment_repair(self):
        self.item['stage'] = 'assessment'; refs = [{'id': 'retained-result'}]; coverage = {'complete': False}
        def historical(item, context, rows, ids): context['environment_checked_job_ids'] = ids; return {'status': 'retained incident'}
        with patch.object(autonomy, 'coverage_manifest', return_value={'environment_failure_job_ids': ['job']}), patch.object(experiment_environment, 'historical', side_effect=historical), patch.object(autonomy, 'numeric_finding', return_value=('inconclusive', refs, coverage)):
            result = autonomy.build_context(self.item)
        self.assertEqual(result['controller_numeric_check']['label'], 'inconclusive'); self.assertEqual(result['controller_numeric_check']['coverage'], coverage)
        self.assertEqual(result['controller_numeric_check']['evidence_manifest_sha256'], digest(canonical(refs)))
        self.assertEqual(result['operational_environment_check']['checked_job_ids'], ['job'])
        with patch.object(autonomy, 'numeric_finding', return_value=None): self.assertIsNone(autonomy.build_context(self.item)['controller_numeric_check'])



if __name__ == '__main__': unittest.main()
