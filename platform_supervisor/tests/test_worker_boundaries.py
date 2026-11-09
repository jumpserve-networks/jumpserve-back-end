"""Controller recovery and publication stages with durable synthetic boundaries.

Source extraction, protected-file checks and retained artifacts use real bytes.
GitHub, qualification, provider and release services are adapters; nothing merges,
deploys, calls a model or changes an assessment outside this temporary fixture.
"""
import copy
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from platform_supervisor import worker, source, service_work
from platform_supervisor.common import POLICY, Hold, canonical, digest, identity
from platform_supervisor.releases import ReleaseFailure
from test_supervisor import DurableStore, REPO, TASK

BACK = 'jumpserve-networks/jumpserve-back-end'
INFRA = 'jumpserve-networks/jumpserve-infra'


class FixtureStore(DurableStore):
    def __init__(self, root):
        super().__init__(); self.spool = root; self.task = dict(id=TASK, checkpoint={}, repositories=[REPO], objective='Synthetic repair objective', title='Synthetic repair', created_at='2026-11-07T00:00:00Z', deadline_at='2099-01-01T00:00:00Z', max_reserved_cents=10000); self.flags = dict(paused=False, cancel_requested=False, automatic_releases=True, deadline_at=self.task['deadline_at'], committed_cents=0, max_reserved_cents=10000); self.calls = []; self.artifacts = {}; self.names = {}; self.changes = {}; self.releases = {}; self.stages = []; self.fail_once = None
    def rpc(self, operation, payload=None):
        self.calls.append((operation, copy.deepcopy(payload)))
        if operation == self.fail_once: self.fail_once = None; raise TimeoutError('Synthetic original persistence acknowledgement missing')
        if operation == 'lease': return copy.deepcopy(self.flags)
        if operation == 'progress': self.task['checkpoint'] = copy.deepcopy(payload['checkpoint']); return {}
        if operation == 'change': self.changes[payload['id']] = copy.deepcopy(payload); return payload
        if operation == 'release': self.releases[payload['id']] = copy.deepcopy(payload); return payload
        if operation == 'heartbeat': return {}
        return super().rpc(operation, payload)
    def artifact(self, name, raw):
        ident = identity(TASK, name + ':' + digest(raw)); record = dict(id=ident, name=name, sha256=digest(raw), byte_count=len(raw), storage_path=TASK + '/' + digest(raw)); self.artifacts[ident] = (record, raw); self.names[name] = ident; return record
    def blob(self, name): return self.artifacts[self.names[name]][1]
    def rows(self, table, **filters):
        values = list(self.operations.values()) if table == 'operations' else [record for record, raw in self.artifacts.values()] if table == 'artifacts' else []
        for key, value in filters.items(): values = [row for row in values if row.get(key) == value.removeprefix('eq.')]
        return copy.deepcopy(values)
    def request(self, path, **options): return next(raw for record, raw in self.artifacts.values() if path.endswith(record['storage_path']))
    def event(self, *args): self.events.append(args)
    def checkpoint(self, status, reason, state): self.stages.append((status, reason, copy.deepcopy(state))); self.task['checkpoint'] = copy.deepcopy(state)


class WorkerBoundaries(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='supervisor-worker-unit-'); self.addCleanup(temporary.cleanup); self.root = Path(temporary.name); self.store = FixtureStore(self.root); self.github = Mock(); self.github.main.return_value = 'a' * 40; self.github.archive.side_effect = self.archive; self.github.publish.return_value = dict(head_sha='b' * 40, pr_number=7, pr_url='https://github.com/' + REPO + '/pull/7'); self.github.merge.return_value = dict(merge_sha='c' * 40)
        self.controller = worker.Controller(self.store, self.github, 'sha256:synthetic-image'); self.controller.releases = Mock(); self.controller.releases.receipt.return_value = {'passed': True, 'revision': 'c' * 40}; self.proposal = dict(ready=True, summary='Synthetic tested correction', feedback='Continue the exact retained work'); self.code = self.default_code; self.checked = None; self.renew_now = False
        self.stop = Mock(); self.stop.wait.side_effect = [False, True]; self.thread = types.SimpleNamespace(target=None, start=Mock(side_effect=self.start_thread), join=Mock()); self.enterContext(patch.object(worker.threading, 'Event', return_value=self.stop)); self.enterContext(patch.object(worker.threading, 'Thread', side_effect=self.thread_factory))
        self.containers = self.enterContext(patch.object(worker.native, 'containers', return_value=[])); self.reconcile = self.enterContext(patch.object(worker.native, 'reconcile')); self.model = self.enterContext(patch.object(worker.native, 'execute', side_effect=lambda *args: self.code(*args))); self.documentation = self.enterContext(patch.object(worker.documentation, 'deliver')); self.dependencies = self.enterContext(patch.object(worker.checks, 'prepare_dependencies', return_value=None)); self.checks = self.enterContext(patch.object(worker.checks, 'execute', side_effect=self.check)); self.enterContext(patch('platform_supervisor.runtime_sources.deliver')); self.enterContext(patch.object(worker.self_runtime, 'prepare', return_value=None))
    def archive(self, repository, revision):
        self.assertEqual(revision, 'a' * 40); buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as bundle:
            raw = b'Original retained source\n'; info = tarfile.TarInfo('root/app.ts'); info.size = len(raw); bundle.addfile(info, io.BytesIO(raw))
        return buffer.getvalue()
    def thread_factory(self, target, daemon): self.assertTrue(daemon); self.thread.target = target; return self.thread
    def start_thread(self):
        if self.renew_now: self.thread.target()
    def default_code(self, store, workspace, *args):
        repo = store.task['repositories'][0]; (workspace / POLICY['repositories'][repo]['directory'] / 'app.ts').write_bytes(b'Prospective synthetic correction\n'); return copy.deepcopy(self.proposal)
    def check(self, store, repo, directory, image):
        if self.checked: return self.checked(store, repo, directory, image)
        return dict(passed=True, synthetic=True), store.artifact('checks.json', b'{"passed":true,"synthetic":true}')
    def exercise(self): self.controller.run(); return self.store.stages[-1]
    def operation(self, kind, request, status='unknown'):
        value = dict(id=identity(TASK, kind + ':' + digest(canonical(request))), kind=kind, status=status, request_sha256=digest(canonical(request)), request=copy.deepcopy(request)); self.store.operations[value['id']] = value; return value
    def retained_change(self, repo=REPO):
        raw = canonical(dict(version='platform-patch-v1', files=[])); artifact = self.store.artifact('retained-patch.json', raw)
        return dict(id=identity(TASK, 'synthetic-change:' + repo), repository=repo, patch_artifact_id=artifact['id'], base_sha='a' * 40, head_sha=None, pr_number=None, component='frontend', status='tested')

    def test_pending_deploy_reconciliation_records_only_the_original_verified_receipt_and_runtime_pin(self):
        request = dict(repository=BACK, revision='a' * 40, component='research-runtime'); op = self.operation('deploy', request)
        with patch.object(worker.research_runtime, 'record_pin_release') as pin: self.controller.reconcile_pending()
        self.assertEqual(self.store.operations[op['id']]['status'], 'confirmed'); release = next(iter(self.store.releases.values())); self.assertEqual(release['status'], 'verified'); self.assertEqual(release['external_id'], op['id']); pin.assert_called_once_with(self.controller, self.controller.releases.receipt.return_value); self.controller.releases.dispatch.assert_not_called(); self.model.assert_not_called()

    def test_pending_merge_publish_and_superseded_close_reconcile_without_dispatching_new_operations(self):
        for kind in ['merge', 'publish', 'close-superseded']:
            with self.subTest(kind=kind):
                self.store.operations = {}; change = self.retained_change(); self.controller.state = {'changes': [change]}; request = dict(repository=REPO, pr_number=17)
                if kind == 'close-superseded': request['action'] = kind; self.github.get.return_value = dict(state='closed', merged=False)
                self.github.merge_receipt.return_value = {'merge_sha': 'd' * 40}; op = self.operation('publish' if kind == 'close-superseded' else kind, request); self.controller.reconcile_pending(); self.assertEqual(self.store.operations[op['id']]['status'], 'confirmed')
                if kind == 'merge': self.assertEqual(change['merge_sha'], 'd' * 40); self.assertEqual(change['status'], 'merged')
                if kind == 'publish': self.assertEqual(change['head_sha'], 'b' * 40); self.assertEqual(change['status'], 'published')
        self.github.merge.assert_not_called(); self.github.request.assert_not_called(); self.assertFalse(any(name == 'begin-operation' for name, _ in self.store.calls))

    def test_unconfirmed_or_failed_pending_releases_never_create_replacement_dispatches(self):
        op = self.operation('deploy', dict(repository=REPO, revision='a' * 40, component='frontend')); self.controller.releases.receipt.return_value = None
        with self.assertRaisesRegex(Hold, 'remains unconfirmed'): self.controller.reconcile_pending()
        self.assertEqual(self.store.operations[op['id']]['status'], 'unknown'); self.controller.releases.dispatch.assert_not_called()
        self.controller.releases.receipt.side_effect = ReleaseFailure(dict(id=7, conclusion='failure'))
        with self.assertRaises(ReleaseFailure): self.controller.reconcile_pending()
        self.assertEqual(self.store.operations[op['id']]['status'], 'failed'); self.assertEqual(next(iter(self.store.releases.values()))['status'], 'failed'); self.assertFalse(next(iter(self.store.releases.values()))['details']['production_revision_verified'])

    def test_open_or_externally_merged_superseded_prs_are_not_reported_as_closed(self):
        for pull in [dict(state='open', merged=False), dict(state='closed', merged=True)]:
            self.store.operations = {}; op = self.operation('publish', dict(repository=REPO, pr_number=7, action='close-superseded')); self.github.get.return_value = pull
            with self.subTest(pull=pull), self.assertRaises(Hold): self.controller.reconcile_pending()
            self.assertEqual(self.store.operations[op['id']]['status'], 'unknown')

    def test_pending_publish_without_its_change_remains_unconfirmed(self):
        self.controller.state={'changes':[]};op=self.operation('publish',dict(repository=REPO,pr_number=7))
        with self.assertRaisesRegex(Hold,'remains unconfirmed'):self.controller.reconcile_pending()
        self.assertEqual(self.store.operations[op['id']]['status'],'unknown');self.github.publish.assert_not_called()

    def test_original_source_and_missing_baselines_are_restored_at_the_retained_revision(self):
        workspace = self.root / 'workspace'; workspace.mkdir(); self.controller.originals(workspace); target = workspace / POLICY['repositories'][REPO]['directory']; baseline = workspace.parent / 'baseline' / target.name; expected = source.snapshot(target); shutil.rmtree(target); self.controller.originals(workspace); self.assertEqual(source.snapshot(target), expected); shutil.rmtree(baseline); self.controller.originals(workspace); self.assertEqual(source.snapshot(baseline), expected); self.assertTrue(all(call.args[1] == 'a' * 40 for call in self.github.archive.call_args_list))
        self.controller.flags = dict(capability_contract=worker.capabilities.VERSION, service_work_contract=service_work.VERSION); self.controller.state['supervisor_failure'] = {'error_type': 'SyntheticOriginalFailure'}
        with patch('platform_supervisor.service_work.deliver') as services: self.controller.originals(workspace)
        packet = json.loads((workspace / 'task.json').read_bytes()); self.assertTrue(packet['capability_acquisition']['enabled']); self.assertEqual(packet['runtime_source_reference'], 'references/runtime-source/index.json'); self.assertEqual(packet['infrastructure_service_reference'], 'references/service-work/index.json'); self.assertEqual(json.loads((workspace / 'supervisor-failure.json').read_bytes()), self.controller.state['supervisor_failure']); services.assert_called_once()

    def test_lease_renewal_preserves_heartbeat_and_cleanup_receipts_and_joins_its_thread(self):
        self.renew_now = True; self.containers.return_value = ['synthetic-retired-container']; self.store.flags['paused'] = True; self.store.task['checkpoint'] = {'cleanup_unknown': True}; status, _, state = self.exercise(); self.assertEqual(status, 'implementing'); self.assertNotIn('cleanup_unknown', state); self.assertTrue(any(event[0] == 'sandbox-cleanup-reconciled' for event in self.store.events)); self.assertTrue(any(name == 'heartbeat' for name, _ in self.store.calls)); self.stop.set.assert_called_once(); self.thread.join.assert_called_once_with(timeout=2); self.model.assert_not_called()

    def test_unconfirmed_lease_or_heartbeat_fences_coding_after_the_background_owner_reports_failure(self):
        for operation in ['lease', 'heartbeat']:
            with self.subTest(operation=operation):
                self.renew_now = True; self.store.fail_once = operation; self.stop.wait.side_effect = [False, True]; self.controller.lease_error = None; status, reason, _ = self.exercise(); self.assertEqual(status, 'reconciling'); self.assertIn('lease refresh is unconfirmed', reason); self.assertTrue(self.controller.lease_error)
        self.model.assert_not_called()

    def test_reconciliation_holds_preserve_cleanup_uncertainty_and_defer_only_from_recorded_evidence(self):
        self.containers.side_effect = worker.native.CleanupUnknown('Original cleanup outcome unknown', 'reconciling')
        with patch.object(worker.call_recovery, 'defer', side_effect=ValueError('Original evidence unavailable')) as defer: status, _, state = self.exercise()
        self.assertEqual(status, 'reconciling'); self.assertTrue(state['cleanup_unknown']); self.assertNotIn('reconciliation_wait', state); self.assertFalse(defer.call_args.kwargs['cleanup_verified'])
        self.containers.side_effect = None; self.reconcile.side_effect = Hold('Original paid request unconfirmed', 'reconciling'); waiting = dict(not_before='2099-01-01T00:00:00Z', original_operation_id='retained')
        with patch.object(worker.call_recovery, 'defer', return_value=waiting) as defer: status, _, state = self.exercise()
        self.assertEqual(state['reconciliation_wait'], waiting); self.assertEqual(state['not_before'], waiting['not_before']); self.assertTrue(defer.call_args.kwargs['cleanup_verified']); self.model.assert_not_called()

    def test_unavailable_incident_or_documentation_context_stops_before_model_admission(self):
        with patch.object(worker.repair, 'deliver', side_effect=OSError('Private original incident unavailable')): status, reason, _ = self.exercise()
        self.assertEqual(status, 'investigating'); self.assertIn('incident evidence is unavailable', reason); self.assertNotIn('Private original', reason); self.model.assert_not_called()
        self.documentation.side_effect = TimeoutError('Private documentation credential must not enter feedback'); status, reason, state = self.exercise(); self.assertEqual(status, 'investigating'); self.assertIn('(TimeoutError)', reason); self.assertNotIn('credential', reason); self.assertIn('not_before', state); self.model.assert_not_called()

    def test_missing_installed_capability_requests_implementation_after_retaining_work(self):
        self.store.flags['capability_contract'] = worker.capabilities.VERSION
        with patch.object(worker.capabilities, 'deliver', side_effect=worker.capabilities.CapabilityFault('Synthetic installed collector failed')), patch.object(worker.capabilities, 'request', side_effect=Hold('Capability implementation retained', 'implementing')) as request:
            status, reason, state = self.exercise()
        self.assertEqual(status, 'implementing'); self.assertIn('Capability implementation retained', reason); self.assertIn('working_artifact', state); self.assertIn('working-checkpoint.zip', self.store.names); request.assert_called_once(); self.model.assert_not_called()

    def test_dependency_failures_are_retained_before_the_bounded_native_repair_instruction(self):
        receipt = dict(passed=False, reason='Synthetic dependency lock mismatch'); self.dependencies.return_value = receipt; self.proposal['ready'] = False; status, reason, state = self.exercise(); self.assertEqual(status, 'implementing'); workspace = self.root / TASK / 'workspace'; self.assertEqual(json.loads((workspace / 'dependency-feedback.json').read_bytes()), [receipt]); self.assertIn('dependency-feedback.json', self.model.call_args.args[-1]); self.assertTrue(state['feedback'].startswith('Read references/context7/index.json')); self.checks.assert_not_called()

    def test_native_budget_stop_can_validate_retained_source_but_other_holds_do_not_reenter_checks(self):
        original = self.code
        def coding(*args): original(*args); self.store.flags['committed_cents'] = 10000; raise Hold('Original admission limit reached', 'budget')
        self.code = coding; status, _, state = self.exercise(); self.assertEqual(status, 'publishing'); self.assertTrue(state['tested']); self.checks.assert_called_once(); self.assertTrue(any(event[0] == 'retained-source-validation' for event in self.store.events))
        self.store.task['checkpoint'] = {}; self.store.flags['committed_cents'] = 0; self.checks.reset_mock(); self.code = Mock(side_effect=Hold('Original native repair checkpoint', 'implementing')); status, reason, _ = self.exercise(); self.assertEqual(status, 'implementing'); self.assertIn('native repair checkpoint', reason); self.checks.assert_not_called()

    def test_protected_source_edits_are_retained_for_repair_without_running_publication_or_fixed_checks(self):
        def coding(store, workspace, *args): (workspace / 'jumpserve-front-end/AGENTS.md').write_bytes(b'Proposed protected edit, retained only\n'); return self.proposal
        self.code = coding; status, reason, state = self.exercise(); self.assertEqual(status, 'implementing'); self.assertIn('scope correction', reason); self.assertIn('Restore the protected file exactly', state['feedback']); self.assertIn('working-checkpoint.zip', self.store.names); self.github.publish.assert_not_called(); self.checks.assert_not_called()

    def test_failed_or_changed_source_checks_cannot_publish_the_candidate(self):
        self.checked = lambda store, *args: (dict(passed=False), store.artifact('failed-checks.json', b'{"passed":false}')); status, _, state = self.exercise(); self.assertEqual(status, 'implementing'); self.assertIn('check-feedback.json', state['feedback']); self.github.publish.assert_not_called()
        self.store.task['checkpoint'] = {}
        def changed(store, repo, directory, image): (directory / 'app.ts').write_bytes(b'Source changed after its checks\n'); return dict(passed=True), store.artifact('changed-checks.json', b'{"passed":true}')
        self.checked = changed; status, reason, _ = self.exercise(); self.assertEqual(status, 'blocked'); self.assertIn('Source changed during testing', reason); self.github.publish.assert_not_called()

    def test_no_changes_and_budget_exhaustion_preserve_explicit_incomplete_outcomes(self):
        self.code = lambda *args: self.proposal; status, reason, _ = self.exercise(); self.assertEqual(status, 'blocked'); self.assertIn('No source change', reason)
        self.store.task['checkpoint'] = {}; self.store.flags['committed_cents'] = 10000; self.model.reset_mock(); status, _, _ = self.exercise(); self.assertEqual(status, 'budget'); self.model.assert_not_called(); self.github.publish.assert_not_called()

    def test_runtime_changes_require_their_specialized_qualification_before_recording_a_tested_candidate(self):
        self.store.task['repositories'] = [BACK]
        def coding(store, workspace, *args): path = workspace / 'jumpserve-back-end/research_workflow/new_unit_module.py'; path.parent.mkdir(); path.write_bytes(b'# Synthetic prospective runtime change\n'); return self.proposal
        self.code = coding; scope = dict(version='research-controller-recovery-v1', scientific_and_request_content_unchanged=True, existing_tests_unchanged=True)
        def qualify(controller, directory, patch, receipt, artifact): controller.state['research_runtime'] = {'scope': scope}; return patch
        with patch.object(worker.research_runtime, 'qualify', side_effect=qualify) as qualification: status, _, state = self.exercise()
        self.assertEqual(status, 'publishing'); self.assertEqual(state['changes'][0]['component'], 'research-runtime'); self.assertNotIn('patch', state['changes'][0]); qualification.assert_called_once(); self.github.publish.assert_not_called()

    def test_self_runtime_candidate_is_bound_to_its_exact_allowed_source_path_and_qualified_checks(self):
        self.store.task['repositories'] = [BACK]; raw = b'# Synthetic future collector, never imported\n'; scope = dict(version='platform-supervisor-self-repair-v1', candidate_paths={'platform_supervisor/new_collector.py': digest(raw)})
        def coding(store, workspace, *args): path = workspace / 'jumpserve-back-end/platform_supervisor/new_collector.py'; path.parent.mkdir(); path.write_bytes(raw); return self.proposal
        self.code = coding
        with patch.object(worker.self_runtime, 'prepare', return_value=scope), patch.object(worker.self_runtime, 'qualify') as qualify: status, _, state = self.exercise()
        self.assertEqual(status, 'publishing'); self.assertEqual(state['changes'][0]['component'], 'supervisor-runtime'); qualify.assert_called_once(); self.assertEqual(qualify.call_args.args[2], scope)

    def test_completed_releases_activate_only_verified_capabilities_and_skip_superseded_candidates(self):
        self.assertEqual(self.exercise()[0], 'publishing'); checkpoint = self.store.task['checkpoint']; checkpoint['changes'].append(dict(checkpoint['changes'][0], id=identity(TASK, 'superseded'), status='superseded', head_sha='d' * 40, merge_sha='e' * 40)); self.store.flags['capability_contract'] = worker.capabilities.VERSION
        with patch.object(worker.capabilities, 'deliver'), patch.object(worker.capabilities, 'activate', return_value={'installed': 'synthetic-verified-pack'}) as activate: status, _, state = self.exercise()
        self.assertEqual(status, 'completed'); self.assertEqual(state['capability_activation'], {'installed': 'synthetic-verified-pack'}); activate.assert_called_once(); self.github.publish.assert_called_once(); self.github.merge.assert_called_once(); self.controller.releases.dispatch.assert_called_once(); self.assertTrue(all(record['status'] == 'verified' for record in self.store.releases.values()))

    def test_a_confirmed_merge_survives_a_lost_checkpoint_without_another_ci_check_or_merge(self):
        self.assertEqual(self.exercise()[0], 'publishing'); change = self.store.task['checkpoint']['changes'][0]; change.update(head_sha='b' * 40, pr_number=7, pr_url='https://github.com/' + REPO + '/pull/7', status='published'); request = dict(repository=REPO, pr_number=7, head_sha='b' * 40, base_sha='a' * 40); sha = digest(canonical(request)); ident = identity(TASK, 'merge:' + REPO + ':' + sha); self.store.operations[ident] = dict(id=ident, kind='merge', request_sha256=sha, request=request, status='confirmed', result=dict(merge_sha='c' * 40))
        self.assertEqual(self.exercise()[0], 'completed'); self.github.publish.assert_not_called(); self.github.ready.assert_not_called(); self.github.merge.assert_not_called(); self.controller.releases.dispatch.assert_called_once(); self.assertEqual(self.store.task['checkpoint']['changes'][0]['merge_sha'], 'c' * 40)

    def test_runtime_release_uses_the_original_pin_proof_before_resuming_and_never_deploys_the_pin_as_an_application(self):
        self.store.task['repositories'] = [BACK]; scope = dict(version='research-controller-recovery-v1', scientific_and_request_content_unchanged=True, existing_tests_unchanged=True)
        def coding(store, workspace, *args): path = workspace / 'jumpserve-back-end/research_workflow/diagnostic.py'; path.parent.mkdir(); path.write_bytes(b'# Synthetic diagnostic correction\n'); return self.proposal
        def qualify(controller, directory, patch, receipt, artifact): controller.state['research_runtime'] = {'scope': scope}; return patch
        self.code = coding
        with patch.object(worker.research_runtime, 'qualify', side_effect=qualify): self.assertEqual(self.exercise()[0], 'publishing')
        def pin(controller, change, workspace):
            controller.state['changes'].append(dict(id=identity(TASK, 'derived-pin'), repository=INFRA, base_sha='d' * 40, head_sha='e' * 40, merge_sha='f' * 40, component='research-runtime-pin', status='merged'))
        request = dict(repository=BACK, revision='c' * 40, component='research-runtime', workflow_revision='a' * 40, pin_revision='f' * 40); recovery = dict(continued_from_original=True, provider_replay=False)
        with patch.object(worker.research_runtime, 'refresh_pin') as refresh, patch.object(worker.research_runtime, 'add_pin', side_effect=pin), patch.object(worker.research_runtime, 'release_request', return_value=request) as release, patch.object(worker.research_runtime, 'record_pin_release') as retained, patch.object(worker.research_runtime, 'resume', return_value=recovery) as resume:
            status, _, state = self.exercise()
        self.assertEqual(status, 'completed'); self.assertEqual(state['runtime_recovery'], recovery); refresh.assert_called_once(); release.assert_called_once(); retained.assert_called_once_with(self.controller, self.controller.releases.receipt.return_value); resume.assert_called_once_with(self.controller, self.controller.releases.receipt.return_value); self.assertEqual(self.controller.releases.dispatch.call_count, 1); self.assertEqual(self.controller.releases.dispatch.call_args.args[0]['request'], request); self.assertTrue(all(record['component'] != 'research-runtime-pin' for record in self.store.releases.values()))

    def test_credential_failures_and_unexpected_private_exceptions_become_bounded_recovery_checkpoints(self):
        for auth_status, expected in [('auth-required', 'blocked'), ('unavailable', 'investigating')]:
            self.reconcile.side_effect = worker.native.runtime.CredentialFailure('synthetic-refresh-failure', auth_status); status, reason, state = self.exercise(); self.assertEqual(status, expected); self.assertIn('Subscription credential recovery', reason); self.assertIn('not_before', state)
        self.reconcile.side_effect = ValueError('Private original credential marker must remain in original evidence only'); status, reason, state = self.exercise(); self.assertEqual(status, 'reconciling'); self.assertIn('Unexpected ValueError', reason); self.assertIn(b'Private original credential marker', self.store.blob('supervisor-failure-original.json')); self.assertNotIn('Private original credential marker', json.dumps(state)); self.assertIn('original_artifact_id', json.loads(self.store.blob('supervisor-failure.json'))); self.model.assert_not_called()


if __name__ == '__main__': unittest.main()
