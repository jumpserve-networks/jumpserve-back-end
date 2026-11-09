"""Runtime qualification, derived pins and recovery without a cloud or Docker.

The retained gate fixture checks actual ASTs and source hashes. Only process and
credentialed-service boundaries are synthetic; this is not a live qualification.
"""
import base64
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from platform_supervisor import research_runtime as runtime, source
from platform_supervisor.common import canonical, digest, Hold, POLICY
import test_research_runtime_scope_refusal as fixture


class RuntimeBroker(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='runtime-broker-unit-'); self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)
        self.enterContext(patch.object(runtime, 'ROOT', self.root))
        self.gate_file = self.root / 'research-runtime-gate.py'; self.gate_file.write_bytes(fixture.FROZEN_GATE_SOURCE)
        self.driver = self.root / 'research-runtime-checks.py'; self.driver.write_bytes(b'# Synthetic frozen software check driver\n')
        self.controller = MagicMock(); self.controller.store.spool = self.root; self.controller.store.task = {'id': str(uuid.uuid4())}; self.controller.state = {'changes': []}; self.controller.flags = {'deadline_at': '2099-01-01T00:00:00Z'}; self.controller.image = 'sha256:' + 'a' * 64
        self.controller.github.main.return_value = 'a' * 40; self.artifacts = {}; self.controller.store.artifact.side_effect = self.artifact
        self.qualification_root = self.root / self.controller.store.task['id'] / 'runtime-qualification'
        self.infra = self.qualification_root / ('a' * 40) / 'infra'; self.previous = self.qualification_root / ('b' * 40) / 'backend'; self.directory = self.root / 'workspace/jumpserve-back-end'
        for path in [self.infra, self.previous, self.directory]: path.mkdir(parents=True)
        original = b'def handler():\n return 1\n'; changed = b'def handler():\n return 2\n'
        binding = digest(canonical({'autonomy.py': digest(original)})); approval = canonical(dict(approved_for_lab_recovery=True, qualified_files_sha256=binding, evidence_sha256='c' * 64))
        for directory in [self.previous, self.directory]:
            (directory / 'research_workflow/policies').mkdir(parents=True); (directory / runtime.APPROVAL).write_bytes(approval)
        (self.previous / 'research_workflow/autonomy.py').write_bytes(original); (self.directory / 'research_workflow/autonomy.py').write_bytes(changed)
        self.manifest = dict(version=4, source_revision='b' * 40, files={'autonomy.py': digest(original), 'policies/autonomous-repair-release-v1.json': digest(approval)})
        (self.infra / 'research-workflow-runtime.json').write_bytes(canonical(self.manifest))
        self.patch = dict(version='platform-patch-v1', files=[dict(path='research_workflow/autonomy.py', mode='100644', content=base64.b64encode(changed).decode())], tree_sha256=digest(canonical(source.snapshot(self.directory))))
        self.checked = dict(passed=True, tree_sha256=self.patch['tree_sha256']); self.check_artifact = {'id': str(uuid.uuid4()), 'sha256': 'd' * 64}
        self.process = self.enterContext(patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'Original qualification output\n', b'')))
    def artifact(self, name, raw):
        item = dict(id=str(uuid.uuid4()), sha256=digest(raw), storage_path='synthetic/' + digest(raw)); self.artifacts[name] = (item, raw); return item
    def qualify(self): return runtime.qualify(self.controller, self.directory, self.patch, self.checked, self.check_artifact)
    def archive(self, directory):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for path in directory.rglob('*'):
                if path.is_file(): archive.add(path, arcname='source/' + path.relative_to(directory).as_posix())
        return buffer.getvalue()
    def pin_workspace(self):
        directory = self.directory.parent / 'runtime-pin' / ('a' * 40); directory.mkdir(parents=True); (directory / 'research-workflow-runtime.json').write_bytes(canonical(self.manifest)); (directory / 'unrelated.txt').write_bytes(b'Unchanged unrelated source'); return directory
    def captured_profile(self, inherited=False):
        # Passive retained source, never used by the production adapter loader.
        # This keeps backend unit checks independent of a sibling infra checkout.
        raw = (Path(__file__).with_name('fixtures') / 'captured-failure-runtime-gate-v1.txt').read_bytes(); self.assertEqual(digest(raw), '82b8f0d47bba4c02593c9db8fff2659797417d2b3809e8b0f9efa530e99c3777')
        (self.root / 'captured-failure-runtime-gate.py').write_bytes(raw); (self.root / 'captured-failure-runtime-checks.py').write_bytes(b'# Synthetic captured-failure check driver\n')
        old = b'def ledger():\n return 1\n'; new = b'def ledger():\n return 2\ndef captured_failure():\n return {"original_outcome": "failed"}\n'
        approval = canonical(dict(approved_for_lab_recovery=True, qualified_files_sha256=digest(canonical({'dispatch_recovery.py': digest(old)})), evidence_sha256='c' * 64))
        for directory, body in [(self.previous, old), (self.directory, new)]:
            (directory / 'research_workflow/dispatch_recovery.py').write_bytes(body); (directory / runtime.APPROVAL).write_bytes(approval); (directory / 'research_workflow/autonomy.py').write_bytes(b'# Unchanged scientific/controller content\n'); tests = directory / 'research_workflow/tests'; tests.mkdir(); (tests / 'test_captured_provider_failures.py').write_bytes(b'# Retained validation source\n')
        self.manifest['files'] = {'dispatch_recovery.py': digest(old), 'policies/autonomous-repair-release-v1.json': digest(approval)}
        self.patch['files'] = [dict(path='research_workflow/dispatch_recovery.py', mode='100644', content=base64.b64encode(new).decode())]
        if inherited:
            base = self.qualification_root / ('f' * 40) / 'backend'; shutil.copytree(self.previous, base); self.manifest['source_overlay'] = dict(version='captured-provider-overlay-v1', base_revision='f' * 40, paths=['dispatch_recovery.py', 'policies/autonomous-repair-release-v1.json'], tests=['tests/test_captured_provider_failures.py'])
            raw_base = self.archive(base); shutil.rmtree(base); self.controller.github.archive.side_effect = lambda repository, revision: raw_base
        (self.infra / 'research-workflow-runtime.json').write_bytes(canonical(self.manifest))

    def test_successful_qualification_binds_actual_source_gate_driver_and_logs_without_editing_candidate(self):
        original = source.snapshot(self.directory); proposal = copy.deepcopy(self.patch); approved = self.qualify(); state = self.controller.state['research_runtime']; certificate = state['certificate']
        self.assertEqual(source.snapshot(self.directory), original); self.assertEqual(self.patch, proposal); self.assertEqual(certificate['driver_sha256'], digest(self.driver.read_bytes())); self.assertEqual(certificate['gate_sha256'], fixture.FROZEN_GATE_SHA256); self.assertEqual(certificate['backend_checks_artifact'], self.check_artifact['sha256']); self.assertEqual(certificate['provider_calls'], 0); self.assertTrue(certificate['credential_free']); self.assertFalse(certificate['scientific_completion'])
        log = json.loads(self.artifacts['research-runtime-frozen-checks.json'][1]); self.assertEqual(log['stdout'], 'Original qualification output\n'); self.assertEqual(log['exit_code'], 0); self.assertEqual(log['network'], 'none')
        artifact, raw = self.artifacts['research-runtime-qualification.json']; self.assertEqual(digest(raw), state['certificate_sha256']); self.assertEqual(certificate, json.loads(raw))
        approval = json.loads(base64.b64decode(approved['files'][-1]['content'])); self.assertTrue(approval['approved_for_lab_recovery']); self.assertEqual(approval['evidence_sha256'], artifact['sha256']); self.assertEqual(approval['qualified_files_sha256'], state['scope']['source_binding']); self.assertEqual(approved['files'][-1]['path'], runtime.APPROVAL); self.assertEqual(approved['files'][:-1], self.patch['files'])
        command = self.process.call_args.args[0]; self.assertEqual(command[:3], ['docker', 'run', '--rm']); self.assertIn('--read-only', command); self.assertIn('--cap-drop', command); self.assertEqual(command[command.index('--network') + 1], 'none'); self.assertTrue(all(value.endswith(',readonly') for value in [command[i + 1] for i, token in enumerate(command) if token == '--mount'])); self.controller.boundary.assert_called_once(); self.controller.store.event.assert_called_once()

    def test_missing_exact_source_archives_are_extracted_and_hash_checked_before_qualification(self):
        archives = {runtime.INFRA: self.archive(self.infra), runtime.BACKEND: self.archive(self.previous)}; shutil.rmtree(self.infra); shutil.rmtree(self.previous)
        self.controller.github.archive.side_effect = lambda repository, revision: archives[repository]
        self.qualify(); self.assertEqual(self.controller.github.archive.call_args_list[0].args, (runtime.INFRA, 'a' * 40)); self.assertEqual(self.controller.github.archive.call_args_list[1].args, (runtime.BACKEND, 'b' * 40)); self.assertEqual(json.loads((self.infra / 'research-workflow-runtime.json').read_bytes()), self.manifest)

    def test_captured_failure_profile_composes_only_its_fixed_paths_and_binds_the_overlay_in_the_pin(self):
        self.captured_profile(); before = source.snapshot(self.directory); self.qualify(); state = self.controller.state['research_runtime']; self.assertEqual(state['scope']['qualification_profile'], 'captured-provider-failures-v1'); overlay = state['scope']['source_overlay']; self.assertEqual(overlay['base_revision'], 'b' * 40); self.assertEqual(overlay['paths'], ['dispatch_recovery.py', 'policies/autonomous-repair-release-v1.json']); self.assertEqual(source.snapshot(self.directory), before)
        command = self.process.call_args.args[0]; mounts = [command[i + 1] for i, value in enumerate(command) if value == '--mount']; self.assertTrue(any('target=/qualification/base-checks.py,readonly' in mount for mount in mounts)); self.assertTrue(any('candidate-overlay-' in mount and 'target=/workspace,readonly' in mount for mount in mounts))
        self.pin_workspace(); runtime.add_pin(self.controller, dict(merge_sha='e' * 40, checks_artifact_id=self.check_artifact['id']), self.directory); patch_data = json.loads(self.artifacts['research-runtime-pin-patch.json'][1]); manifest = next(json.loads(base64.b64decode(item['content'])) for item in patch_data['files'] if item['path'] == 'research-workflow-runtime.json'); self.assertEqual(manifest['source_overlay'], overlay)

    def test_inherited_captured_profile_fetches_exact_base_and_rebuilds_the_prior_overlay_before_comparison(self):
        self.captured_profile(inherited=True); previous = source.snapshot(self.previous); original = source.snapshot(self.directory); self.qualify(); self.controller.github.archive.assert_called_once_with(runtime.BACKEND, 'f' * 40); self.assertEqual(source.snapshot(self.previous), previous); self.assertEqual(source.snapshot(self.directory), original)
        root = self.qualification_root; self.assertEqual(len(list(root.glob('installed-overlay-*'))), 1); self.assertEqual(len(list(root.glob('candidate-overlay-*'))), 1); self.assertEqual(self.controller.state['research_runtime']['scope']['source_overlay'], self.manifest['source_overlay'])

    def test_captured_profile_never_admits_mixed_proposals_and_missing_adapter_is_an_explicit_blocker(self):
        with self.assertRaisesRegex(Hold, 'dedicated installed qualification adapter'): runtime.captured_failure_gate()
        self.captured_profile(); self.patch['files'].append(dict(path='research_workflow/scientific.py', mode='100644', content=base64.b64encode(b'Unreviewed science').decode()))
        with self.assertRaisesRegex(Hold, 'dedicated qualified runtime adapter'): self.qualify()
        self.process.assert_not_called(); self.assertFalse(self.artifacts); self.assertNotIn('research_runtime', self.controller.state)

    def test_unpassed_backend_checks_expired_deadline_and_failed_process_cannot_derive_approval(self):
        self.checked['passed'] = False
        with self.assertRaisesRegex(Hold, 'Frozen backend checks'): self.qualify()
        self.process.assert_not_called(); self.checked['passed'] = True; self.controller.flags['deadline_at'] = '2000-01-01T00:00:00Z'
        with self.assertRaisesRegex(Hold, 'Original deadline') as error: self.qualify()
        self.assertEqual(error.exception.status, 'deadline'); self.process.assert_not_called(); self.controller.flags['deadline_at'] = '2099-01-01T00:00:00Z'; self.process.return_value = subprocess.CompletedProcess([], 1, b'Retained failed check', b'Original stderr')
        with self.assertRaisesRegex(Hold, 'Frozen operational qualification failed'): self.qualify()
        self.assertIn('research-runtime-frozen-checks.json', self.artifacts); self.assertNotIn('research-runtime-qualification.json', self.artifacts); self.assertNotIn('research_runtime', self.controller.state)

    def test_timeout_removes_only_the_named_container_and_unknown_cleanup_remains_unconfirmed(self):
        for cleanup_failed in [False, True]:
            self.process.reset_mock(); self.process.side_effect = [subprocess.TimeoutExpired(['synthetic Docker command'], 120), RuntimeError('Unknown cleanup') if cleanup_failed else subprocess.CompletedProcess([], 0)]
            with self.subTest(cleanup_failed=cleanup_failed), self.assertRaises(Hold) as error: self.qualify()
            self.assertEqual(error.exception.status, 'reconciling' if cleanup_failed else 'testing'); cleanup = self.process.call_args.args[0]; self.assertEqual(cleanup[:3], ['docker', 'rm', '-f']); self.assertIn(self.controller.store.task['id'].replace('-', ''), cleanup[-1]); self.assertNotIn('research_runtime', self.controller.state)

    def test_derived_pin_changes_only_fixed_metadata_preserves_baseline_and_is_idempotent(self):
        self.qualify(); directory = self.pin_workspace(); original = source.snapshot(directory); backend = dict(merge_sha='e' * 40, checks_artifact_id=self.check_artifact['id']); result = runtime.add_pin(self.controller, backend, self.directory)
        self.assertEqual(result['component'], 'research-runtime-pin'); self.assertEqual(result['base_sha'], 'a' * 40); self.assertEqual(source.snapshot(directory), original)
        raw = self.artifacts['research-runtime-pin-patch.json'][1]; patch_data = json.loads(raw); self.assertEqual({f['path'] for f in patch_data['files']}, {'research-workflow-runtime.json', 'research-runtime-qualifications/' + self.controller.state['research_runtime']['scope']['source_binding'] + '.json'})
        manifest = next(json.loads(base64.b64decode(f['content'])) for f in patch_data['files'] if f['path'] == 'research-workflow-runtime.json'); self.assertEqual(manifest['version'], self.manifest['version'] + 1); self.assertEqual(manifest['source_revision'], backend['merge_sha']); self.assertEqual(manifest['files']['policies/autonomous-repair-release-v1.json'], self.controller.state['research_runtime']['approval_sha256'])
        self.assertIs(runtime.add_pin(self.controller, backend, self.directory), result); self.controller.record_change.assert_called_once_with(result); self.controller.save_state.assert_called_once()

    def test_advanced_predecessor_unsafe_path_or_source_drift_prevents_a_derived_pin(self):
        self.qualify(); directory = self.pin_workspace(); (directory / 'research-workflow-runtime.json').write_bytes(canonical(dict(self.manifest, version=99)))
        with self.assertRaisesRegex(Hold, 'Pinned runtime advanced'): runtime.add_pin(self.controller, dict(merge_sha='e' * 40), self.directory)
        baseline = source.snapshot(directory)
        with self.assertRaisesRegex(Hold, 'Unsafe derived runtime metadata'): runtime.derived_patch(directory, baseline, {'.env.private': b'Never publish'})
        with self.assertRaisesRegex(Hold, 'baseline changed'): runtime.derived_patch(directory, {}, {'research-workflow-runtime.json': b'proposed metadata'})
        self.assertEqual(source.snapshot(directory), baseline)

    def test_pin_refresh_closes_only_unmerged_old_pr_and_never_replaces_dispatched_release(self):
        pin = dict(id=str(uuid.uuid4()), component='research-runtime-pin', base_sha='f' * 40, pr_number=17, status='published'); backend = dict(component='research-runtime', merge_sha='e' * 40); self.controller.state['changes'] = [pin, backend]
        self.controller.store.rows.return_value = [{'kind': 'deploy'}]
        with self.assertRaisesRegex(Hold, 'must reconcile'): runtime.refresh_pin(self.controller, self.directory)
        self.assertEqual(pin['status'], 'published')
        self.controller.store.rows.return_value = []; self.controller.github.get.return_value = {'state': 'open', 'merged': False}
        self.controller.operation.side_effect = lambda kind, label, request, dispatch, reconcile: dispatch({})
        with patch.object(runtime, 'add_pin') as add: runtime.refresh_pin(self.controller, self.directory)
        self.assertEqual(pin['status'], 'superseded'); self.controller.github.request.assert_called_once_with('PATCH', '/repos/' + runtime.INFRA + '/pulls/17', {'state': 'closed'}); add.assert_called_once_with(self.controller, backend, self.directory)
        pin['status'] = 'published'; self.controller.github.get.return_value = {'state': 'closed', 'merged': True}
        with self.assertRaisesRegex(Hold, 'Externally merged'): runtime.refresh_pin(self.controller, self.directory)
        self.assertEqual(pin['status'], 'published')

    def test_closed_pins_do_not_repeat_a_close_and_merged_or_unchanged_pins_are_left_alone(self):
        backend = dict(component='research-runtime', merge_sha='e' * 40); pin = dict(id=str(uuid.uuid4()), component='research-runtime-pin', base_sha='f' * 40, pr_number=17, status='published'); self.controller.state['changes'] = [pin, backend]; self.controller.store.rows.return_value = []; self.controller.github.get.return_value = {'state': 'closed', 'merged': False}; self.controller.operation.side_effect = lambda kind, label, request, dispatch, reconcile: reconcile({})
        with patch.object(runtime, 'add_pin') as add: runtime.refresh_pin(self.controller, self.directory)
        self.controller.github.request.assert_not_called(); add.assert_called_once()
        for change in [dict(pin, status='published', merge_sha='1' * 40), dict(pin, status='published', base_sha='a' * 40)]:
            self.controller.state['changes'] = [change, backend]
            with patch.object(runtime, 'add_pin') as add: runtime.refresh_pin(self.controller, self.directory)
            add.assert_not_called()

    def test_release_request_requires_merged_pin_and_freezes_workflow_revision_across_restarts(self):
        pin = dict(component='research-runtime-pin', merge_sha=None); self.controller.state['changes'] = [pin]
        with self.assertRaisesRegex(Hold, 'must merge before deployment'): runtime.release_request(self.controller, {'merge_sha': 'e' * 40})
        pin['merge_sha'] = 'f' * 40; first = runtime.release_request(self.controller, {'merge_sha': 'e' * 40}); self.controller.github.main.return_value = '1' * 40; self.assertEqual(runtime.release_request(self.controller, {'merge_sha': 'e' * 40}), first); self.assertEqual(first, dict(repository=runtime.BACKEND, revision='e' * 40, component='research-runtime', pin_revision='f' * 40, workflow_revision='a' * 40))
        proof = dict(first, operation_id='original-operation'); result = runtime.resume(self.controller, proof); self.assertIs(result, self.controller.store.request.return_value); self.assertEqual(self.controller.store.request.call_args.args[:2], ('/rest/v1/rpc/research_runtime_resume', 'POST')); self.controller.store.event.assert_called_once()

    def test_reviewed_handoff_checks_actual_working_snapshots_and_records_only_a_prospective_change(self):
        self.assertFalse(runtime.reviewed(self.controller, self.directory.parent)); self.controller.state.update(runtime_reviewed_source='a' * 40, sources={runtime.BACKEND: dict(revision='a' * 40, snapshot=source.snapshot(self.directory))})
        self.controller.github.main.return_value = 'changed'
        with self.assertRaisesRegex(Hold, 'exact current backend main'): runtime.reviewed(self.controller, self.directory.parent)
        self.controller.github.main.return_value = 'a' * 40
        (self.directory / 'unreviewed.py').write_bytes(b'# Unreviewed edit')
        with self.assertRaisesRegex(Hold, 'unreviewed working edits'): runtime.reviewed(self.controller, self.directory.parent)
        (self.directory / 'unreviewed.py').unlink()
        # Unit checks and the trusted runtime broker are separate tested units;
        # this handoff must retain their exact artifacts without running them twice.
        with patch.object(runtime, 'qualify', return_value=self.patch) as qualify, patch('platform_supervisor.checks.execute', return_value=(self.checked, self.check_artifact)) as check, self.assertRaisesRegex(Hold, 'next durable stage') as error: runtime.reviewed(self.controller, self.directory.parent)
        self.assertEqual(error.exception.status, 'publishing'); check.assert_called_once(); qualify.assert_called_once(); change = self.controller.state['changes'][0]; self.assertEqual(change['component'], 'research-runtime'); self.assertEqual(change['checks_artifact_id'], self.check_artifact['id']); self.assertEqual(change['status'], 'tested'); self.assertIsNone(change['merge_sha']); self.controller.save_state.assert_called_once()

    def test_reviewed_runtime_handoff_cannot_include_unreviewed_edits_in_another_repository(self):
        other = runtime.INFRA; directory = self.directory.parent / POLICY['repositories'][other]['directory']; directory.mkdir(); (directory / 'retained.txt').write_bytes(b'Original infrastructure')
        self.controller.state.update(runtime_reviewed_source='a' * 40, sources={runtime.BACKEND: dict(revision='a' * 40, snapshot=source.snapshot(self.directory)), other: dict(revision='a' * 40, snapshot=source.snapshot(directory))})
        (directory / 'retained.txt').write_bytes(b'Unreviewed infrastructure edit')
        with self.assertRaisesRegex(Hold, 'other unreviewed repository edits'): runtime.reviewed(self.controller, self.directory.parent)
        self.assertFalse(self.artifacts); self.process.assert_not_called()
        (directory / 'retained.txt').write_bytes(b'Original infrastructure')
        with patch.object(runtime, 'qualify', return_value=self.patch), patch('platform_supervisor.checks.execute', return_value=(self.checked, self.check_artifact)), self.assertRaisesRegex(Hold, 'next durable stage'):
            runtime.reviewed(self.controller, self.directory.parent)
        self.assertEqual(self.controller.state['changes'][0]['status'],'tested')

    def test_unpublished_superseded_pin_does_not_need_a_pull_request_close(self):
        pin=dict(id=str(uuid.uuid4()),component='research-runtime-pin',base_sha='f'*40,status='tested',pr_number=None);backend=dict(component='research-runtime',merge_sha='e'*40)
        self.controller.state['changes']=[pin,backend];self.controller.store.rows.return_value=[]
        with patch.object(runtime,'add_pin') as add:runtime.refresh_pin(self.controller,self.directory)
        self.assertEqual(pin['status'],'superseded');self.controller.operation.assert_not_called();add.assert_called_once_with(self.controller,backend,self.directory)


if __name__ == '__main__': unittest.main()
