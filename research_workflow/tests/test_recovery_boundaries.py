"""Worker diagnostic and recovery evidence limits, with local-only originals."""
import copy
import fcntl
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomous_repair as repair
import dispatch_recovery as recovery
import experiment_builder as builder
import openclaw_runtime as runtime
import store
from workflow import canonical, digest
from test_dispatch_recovery import Fixture, uid
from test_captured_provider_failures import failed_controller, failed_native
from test_controller_terminal_recovery import Fixture as ControllerFixture


class RecoveryBoundaries(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='recovery-boundaries-unit-'); self.addCleanup(temporary.cleanup); self.root = Path(temporary.name).resolve(); self.spool = self.root / 'spool'; self.spool.mkdir(); self.source = self.root / 'installed'; self.source.mkdir(); self.fixture = Fixture(1)
        original_resolve = Path.resolve; source_path = Path(recovery.__file__)
        def installed(path, *args, **kwargs): return self.source / source_path.name if path == source_path else original_resolve(path, *args, **kwargs)
        self.enterContext(patch.object(Path, 'resolve', installed))
        for name in ['autonomy_read_progress.py', 'autonomy.py', 'autonomy_model.py', 'dispatch_recovery.py', 'siwc_protocol.py']: (self.source / name).write_bytes(('# Synthetic installed original ' + name + '\n').encode())
        self.calls = []; self.retained = []; self.refs = {}; self.rpc = self.enterContext(patch.object(store, 'rpc', return_value={}))
        self.enterContext(patch.object(repair, 'rows', side_effect=lambda *args, **options: copy.deepcopy(self.calls)))
    def retain(self, name, raw, kind):
        self.retained.append((name, raw, kind)); ref = dict(artifact_id=uid(), sha256=digest(raw)); self.refs[ref['artifact_id']] = raw; return ref
    def diagnostic(self): return recovery.runtime_diagnostic(self.fixture.task, self.fixture.d, self.spool, self.retain)
    def report(self): return json.loads(next(raw for name, raw, _ in reversed(self.retained) if name == 'worker-runtime-diagnostics.json'))
    def native(self, number, status='unknown'):
        return dict(id=uid(), number=number, status=status, request_sha256='a' * 64, response_artifact_id=None)

    def test_runtime_diagnostics_capture_only_fixed_installed_sources_and_exact_task_spool_observations(self):
        directory = self.spool / self.fixture.d['id']; directory.mkdir(); self.calls = [self.native(1), self.native(2), self.native(3, 'complete')]
        original = b'Original retained transport bytes\n'; (directory / 'call-1-response.bin').write_bytes(original); (directory / 'call-1-completion.json').write_bytes(canonical({'response_received': False, 'zero': 0, 'missing': None})); self.diagnostic(); value = self.report()
        self.assertEqual(len(value['sources']), 5); self.assertEqual(len(value['calls']), 2); self.assertEqual(value['calls'][0]['response']['sha256'], digest(original)); self.assertEqual(value['calls'][1]['response']['status'], 'absent'); self.assertEqual(value['calls'][0]['provider_outcome'], 'uncertain'); self.assertFalse(value['reconciliation_performed']); self.assertFalse(value['cleanup_performed']); self.assertFalse(value['scientific_completion']); self.assertEqual(value['provider_calls'], 0)
        before = list(self.retained); self.diagnostic(); self.assertEqual(self.retained, before); self.rpc.assert_called_once(); payload = self.rpc.call_args.args[1]; self.assertEqual(payload['p_dispatch'], self.fixture.d['id']); self.assertEqual(self.refs[payload['p_artifact']], canonical(payload['p_proof']))

    def test_diagnostic_intent_reuses_original_report_after_uncertain_database_publication_without_readmitting_any_provider(self):
        self.calls = [self.native(1)]; self.rpc.side_effect = store.StoreError('Original observation publication is unconfirmed')
        with self.assertRaises(store.StoreError): self.diagnostic()
        first = self.report(); self.assertEqual(len(list((self.spool / 'runtime-diagnostics').rglob('*.intent'))), 1); self.rpc.side_effect = None; self.diagnostic(); self.assertEqual(self.report(), first)
        sources = [name for name, _, _ in self.retained if name.startswith('installed-')]; self.assertEqual(len(sources), 5); self.assertEqual(self.rpc.call_count, 2)

    def test_source_absence_and_permission_denial_are_distinct_and_never_filled_with_empty_source_bytes(self):
        absent = self.source / 'autonomy.py'; absent.unlink(); inaccessible = self.source / 'autonomy_model.py'; original = Path.lstat
        def inspect(path, *args, **kwargs):
            if path == inaccessible: raise PermissionError('Synthetic source permission denial')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', inspect): self.diagnostic()
        status = {row['filename']: row.get('status') for row in self.report()['sources']}; self.assertEqual(status['autonomy.py'], 'absent'); self.assertEqual(status['autonomy_model.py'], 'inaccessible'); self.assertFalse(any(name in ['installed-autonomy.py', 'installed-autonomy_model.py'] for name, _, _ in self.retained))

    def test_source_links_credentials_oversize_and_changed_read_lengths_stop_before_delivering_source(self):
        path = self.source / 'autonomy.py'; original = path.read_bytes()
        for case, reason in [('credential', 'safely delivered'), ('oversize', 'unsafe or oversized'), ('changed', 'changed during read'), ('link', 'unsafe or oversized')]:
            with self.subTest(case=case):
                if path.is_symlink(): path.unlink()
                path.write_bytes(original)
                if case == 'credential': path.write_bytes(b'# synthetic marker sb_secret_' + b'x' * 20)
                if case == 'oversize': path.write_bytes(b'x' * 262145)
                if case == 'link': path.unlink(); path.symlink_to(self.source / 'autonomy_model.py')
                reader = Path.read_bytes
                def read(item): return reader(item) + b'changed' if case == 'changed' and item == path else reader(item)
                with patch.object(Path, 'read_bytes', read), self.assertRaisesRegex(ValueError, reason): self.diagnostic()
        self.assertEqual(self.retained, []); self.rpc.assert_not_called()

    def test_spool_file_link_type_size_changed_read_and_permission_failures_are_bounded_structural_observations(self):
        directory = self.spool / self.fixture.d['id']; directory.mkdir(); self.calls = [self.native(number) for number in range(1, 7)]
        (directory / 'call-1-response.bin').symlink_to(self.source / 'autonomy.py'); (directory / 'call-2-response.bin').mkdir()
        with (directory / 'call-3-response.bin').open('wb') as sparse: sparse.truncate(1048577)
        (directory / 'call-4-response.bin').write_bytes(b'original'); (directory / 'call-5-response.bin').write_bytes(b'original'); stat = Path.lstat; read = Path.read_bytes
        def inspect(path, *args, **kwargs):
            if path == directory / 'call-5-response.bin': raise PermissionError('Synthetic retained read denial')
            return stat(path, *args, **kwargs)
        def contents(path): return read(path) + b'changed' if path == directory / 'call-4-response.bin' else read(path)
        with patch.object(Path, 'lstat', inspect), patch.object(Path, 'read_bytes', contents): self.diagnostic()
        self.assertEqual([row['response']['status'] for row in self.report()['calls']], ['unsafe-link', 'unsafe-file', 'oversized', 'changed-during-read', 'inaccessible', 'absent'])
        self.assertTrue(all(not row['indexed_response'] for row in self.report()['calls'])); self.assertFalse(self.report()['reconciliation_performed'])

    def test_call_inventory_and_original_numbers_are_bounded_before_diagnostics_can_be_published(self):
        self.calls = [self.native(1)] * 101
        with self.assertRaisesRegex(ValueError, 'coverage exceeds'): self.diagnostic()
        for number in [True, 0, 101]:
            with self.subTest(number=number):
                self.calls = [self.native(number)]
                with self.assertRaisesRegex(ValueError, 'call number differs'): self.diagnostic()
        self.rpc.assert_not_called(); self.assertEqual(self.retained, [])

    def test_private_recovery_artifact_uses_content_identity_and_confirms_exact_downloaded_original_bytes(self):
        f = self.fixture; raw = b'Original private diagnostic, zero=0, missing=null\n'
        def artifact(study, owner, data, ident, media, provenance): return dict(id=ident, sha256=digest(data), provenance=provenance)
        with patch.object(store, 'artifact', side_effect=artifact) as write, patch.object(store, 'append') as append, patch.object(store, 'artifact_bytes', return_value=raw): result = recovery.recovery_artifact(f.task, f.d, f.task['actor_id'], 'installed-autonomy.py', raw, 'worker-installed-source-v1')
        self.assertEqual(result['sha256'], digest(raw)); self.assertEqual(write.call_args.args[-1]['filename'], 'autonomy.py'); append.assert_called_once()
        with patch.object(store, 'artifact', side_effect=artifact), patch.object(store, 'append'), patch.object(store, 'artifact_bytes', return_value=raw + b'changed'), self.assertRaisesRegex(ValueError, 'hash differs after retrieval'): recovery.recovery_artifact(f.task, f.d, f.task['actor_id'], 'installed-autonomy.py', raw, 'worker-installed-source-v1')

    def test_captured_failures_require_a_complete_explicit_error_frame_and_preserve_unknown_processing_and_charges(self):
        request = canonical(dict(model='gpt-6.1-sol', store=False, stream=True, tools=[])); frame = b'data: ' + canonical(dict(type='response.failed', response=dict(status='failed', error={'code': 'original-overload'}))) + b'\n\n'; raw = b': heartbeat\n\n' + frame + b'Original trailing bytes do not authorize inference.'
        proof = recovery.captured_failure(request, raw, 200, 'original-overload'); self.assertEqual(proof['inspected_prefix_bytes'], len(b': heartbeat\n\n' + frame)); self.assertTrue(proof['provider_processing_unknown']); self.assertIsNone(proof['measured_charges_usd']); self.assertEqual(proof['tool_calls_executed_from_response'], 0); self.assertFalse(proof['provider_replay']); self.assertFalse(proof['scientific_completion']); self.assertEqual(proof['response_sha256'], digest(raw))
        variants = [b'data: [DONE]\n\n', b'data: []\n\n', b'data: {broken}\n\n', b'data: ' + canonical(dict(type='error', response=['malformed'])) + b'\n\n', b'data: ' + canonical(dict(type='response.completed', response={})) + b'\n\n', b'data: ' + canonical(dict(type='response.failed', response=dict(status='in_progress', error={'code': 'original-overload'}))) + b'\n\n']
        for value in variants:
            with self.subTest(raw=value), patch.object(runtime, 'credential') as credential, patch.object(runtime, 'http_request') as transport: self.assertIsNone(recovery.captured_failure(request, value, 200, 'original-overload')); credential.assert_not_called(); transport.assert_not_called()
        for response in [b'not JSON', b'[]', b'{"error":"unstructured"}']:
            with self.subTest(response=response): self.assertIsNone(recovery.captured_failure(request, response, 503, 'original-overload'))

    def test_worker_observation_dispatch_routes_only_fixed_diagnostics_and_leaves_running_tasks_and_unknown_calls_fenced(self):
        f = self.fixture; dispatch = dict(f.d, runtime_observation_only=True); before = copy.deepcopy(dispatch)
        def rows(kind, params):
            if kind == 'autonomy_tasks': return [dict(f.task, status='waiting')]
            if kind == 'study_owners': return [{'owner_id': f.task['actor_id']}]
            raise AssertionError(kind)
        with patch.object(store, 'rows', side_effect=rows), patch.object(recovery, 'runtime_diagnostic') as diagnostic, patch.object(builder, 'container_action') as cleanup: self.assertEqual(recovery._reconcile_dispatches(self.spool, [dispatch]), [])
        diagnostic.assert_called_once(); cleanup.assert_not_called(); self.assertEqual(dispatch, before)
        callback = diagnostic.call_args.args[-1]
        with patch.object(recovery, 'recovery_artifact', return_value={'retained': True}) as retain: self.assertEqual(callback('original.bin', b'bytes', 'kind'), {'retained': True})
        retain.assert_called_once_with(f.task, dispatch, f.task['actor_id'], 'original.bin', b'bytes', 'kind')
        with patch.object(store, 'rows', return_value=[dict(f.task, status='running')]), patch.object(recovery, 'runtime_diagnostic') as diagnostic: self.assertEqual(recovery._reconcile_dispatches(self.spool, [dispatch]), [])
        diagnostic.assert_not_called()

    def test_native_reconciliation_requires_unique_ordinary_receipts_and_a_real_bounded_response_before_publishing(self):
        f = self.fixture; f.calls[0]['status'] = 'unknown'; directory = self.spool / f.d['id']; directory.mkdir(); original = copy.deepcopy(f.calls[0])
        receipt = dict(version=recovery.CALL_VERSION, call_id=f.calls[0]['id'], dispatch_id=f.d['id'], task_id=f.task['id'], study_id=f.task['study_id'], request_sha256=f.calls[0]['request_sha256'], response_filename='call-1-response.bin', response_sha256=f.calls[0]['response_sha256'], status='complete', response_received=True, http_status=200)
        path = directory / 'call-1-completion.json'; path.write_bytes(canonical(receipt)); (directory / 'call-2-completion.json').write_bytes(canonical(receipt))
        with f.patches(), self.assertRaisesRegex(ValueError, 'Ambiguous original native'): recovery.reconcile_native_calls(f.task, f.d, directory, Mock())
        (directory / 'call-2-completion.json').unlink(); self.rpc.reset_mock()
        with f.patches(): recovery.reconcile_native_calls(f.task, f.d, directory, Mock())
        self.rpc.assert_not_called(); self.assertEqual(f.calls[0], original)
        path.unlink(); path.symlink_to(self.source / 'autonomy.py')
        with f.patches(), self.assertRaisesRegex(ValueError, 'Invalid native completion'): recovery.reconcile_native_calls(f.task, f.d, directory, Mock())

    def test_retained_call_supplements_revalidate_failure_contents_and_original_completion_identity(self):
        for defect in ['failure-description', 'failure-identity', 'completion-record', 'completion-identity']:
            with self.subTest(defect=defect):
                f = Fixture(1) if defect == 'completion-identity' else failed_native(); call = f.calls[0]
                if defect == 'completion-identity': call['status'] = 'unknown'
                def retain(name, raw, kind): return f.add(raw, provenance=dict(kind=kind, dispatch_id=f.d['id']))
                def record(name, payload):
                    self.assertEqual(name, 'research_builder_call_reconcile')
                    f.call_proofs.append(dict(call_id=payload['p_call'], artifact_id=payload['p_artifact'], sha256=payload['p_sha256'], proof=payload['p_proof'], request_sha256=call['request_sha256']))
                with f.patches(), patch.object(store, 'rpc', side_effect=record): recovery.reconcile_native_calls(f.task, f.d, self.spool, retain)
                row = f.call_proofs[0]
                with f.patches(): self.assertIsNotNone(recovery.reconciled_call(f.task, call, f.original, f.rows))
                proof = copy.deepcopy(row['proof'])
                if defect == 'failure-description': proof['failure']['error_code'] = 'forged-error-code'
                elif defect == 'failure-identity': proof['call_id'] = uid()
                elif defect == 'completion-record': proof['completion_record']['forged'] = True
                else: proof['http_status'] = 503
                changed = f.add(canonical(proof), identity=row['artifact_id']); row.update(sha256=changed['sha256'], proof=proof)
                expected = {'failure-description': 'failure differs from original bytes', 'failure-identity': 'failure identity differs', 'completion-record': 'failure capture differs', 'completion-identity': 'reconciliation identity mismatch'}[defect]
                with f.patches(), patch.object(runtime, 'credential') as credential, patch.object(runtime, 'http_request') as transport, self.assertRaisesRegex(ValueError, expected): recovery.reconciled_call(f.task, call, f.original, f.rows)
                credential.assert_not_called(); transport.assert_not_called()

    def test_native_sidecars_cannot_substitute_a_path_another_calls_filename_or_a_failure_request(self):
        for defect in ['traversal', 'different-call', 'failure-request']:
            with self.subTest(defect=defect), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                f = failed_native(); call = f.calls[0]; directory = Path(temporary); raw = f.blobs[call['response_artifact_id']]
                receipt = dict(version=recovery.CALL_VERSION, call_id=call['id'], dispatch_id=f.d['id'], task_id=f.task['id'], study_id=f.task['study_id'], request_sha256=call['request_sha256'], response_filename='call-1-response.bin', response_sha256=digest(raw), status='failed', response_received=True, http_status=200)
                if defect == 'traversal': receipt['response_filename'] = '../call-1-response.bin'
                elif defect == 'different-call': receipt['response_filename'] = 'call-2-response.bin'
                else: receipt['request_sha256'] = 'a' * 64
                (directory / 'call-1-completion.json').write_bytes(canonical(receipt)); (directory / 'call-1-response.bin').write_bytes(raw)
                with f.patches(), patch.object(store, 'rpc') as record, self.assertRaisesRegex(ValueError, 'failure capture identity differs' if defect == 'failure-request' else 'response path differs'): recovery.reconcile_native_calls(f.task, f.d, directory, Mock())
                record.assert_not_called()

    def test_a_hash_valid_original_native_request_still_must_satisfy_the_admitted_model_contract(self):
        for field, value in [('model', 'different-model'), ('store', True), ('stream', False), ('previous_response_id', 'another-response')]:
            with self.subTest(field=field):
                f = Fixture(1); call = f.calls[0]; call['status'] = 'unknown'; original = json.loads(f.blobs[call['request_artifact_id']]); original[field] = value; ref = f.add(canonical(original)); call.update(request_artifact_id=ref['artifact_id'], request_sha256=ref['sha256'])
                with f.patches(), patch.object(store, 'rpc') as record, patch.object(runtime, 'http_request') as transport, self.assertRaisesRegex(ValueError, 'request contract differs'): recovery.reconcile_native_calls(f.task, f.d, self.spool, Mock())
                record.assert_not_called(); transport.assert_not_called()

    def test_controller_supplements_cannot_be_attached_to_a_different_study_or_dispatch(self):
        for kind in ['terminal', 'captured-failure']:
            for field in ['study_id', 'dispatch_id']:
                with self.subTest(kind=kind, field=field):
                    f = ControllerFixture() if kind == 'terminal' else failed_controller()
                    if kind == 'terminal': f.retain()
                    else:
                        proof = recovery.controller_failure(f.task, f.d, f.receipt, f.original, f.rows); ref = f.add('proof.json', canonical(proof)); f.proofs = [dict(dispatch_id=f.d['id'], study_id=f.task['study_id'], task_id=f.task['id'], request_sha256=f.d['request_sha256'], artifact_id=ref['id'], sha256=ref['sha256'], proof=proof)]
                    before = copy.deepcopy(f.d); f.proofs[0][field] = uid()
                    with patch.object(runtime, 'http_request') as transport, patch.object(builder, 'container_action') as cleanup, self.assertRaisesRegex(ValueError, 'differs from its originals'): recovery.confirmed(f.task, f.d, f.receipt, f.original, f.rows)
                    self.assertEqual(f.d, before); transport.assert_not_called(); cleanup.assert_not_called()

    def test_worker_preserves_original_native_history_before_checkpoint_and_rejects_a_changed_logical_request(self):
        for changed in [False, True]:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                f = Fixture(1); spool = Path(temporary); directory = spool / f.d['id']; directory.mkdir(); (directory / 'logical.json').write_bytes(f.logical + (b'changed' if changed else b''))
                task_root = spool / 'builder-workspaces' / f.task['id']; workspace = task_root / 'workspace'; workspace.mkdir(parents=True); (workspace / 'experiment.py').write_text('print(42)\n'); history = [{'role': 'user', 'content': 'Exact original pre-transport history'}]; (task_root / 'native-tool-history.json').write_bytes(canonical(history))
                def rows(kind, query):
                    if kind in ['model_dispatches', 'model_reconciliations']: return []
                    if kind == 'autonomy_tasks': return [f.task]
                    if kind == 'study_owners': return [dict(owner_id=f.task['actor_id'])]
                    raise AssertionError(kind)
                def artifact(study, owner, raw, identity, media, provenance): f.add(raw, identity, provenance); return f.records[identity]
                with f.patches(), patch.object(store, 'rows', side_effect=rows), patch.object(store, 'artifact', side_effect=artifact), patch.object(store, 'append'), patch.object(builder, 'save_history') as preserve, patch.object(builder, 'normalize_workspace', return_value=b'{"bytes_unchanged":true}'), patch.object(builder, 'container_action') as cleanup, patch.object(runtime, 'credential') as credential, patch.object(runtime, 'http_request') as transport, patch.object(store, 'rpc') as record:
                    if changed:
                        with self.assertRaisesRegex(ValueError, 'logical hash differs'): recovery._reconcile_dispatches(spool, [f.d])
                        preserve.assert_not_called(); cleanup.assert_not_called(); record.assert_not_called()
                    else:
                        self.assertEqual(recovery._reconcile_dispatches(spool, [f.d]), [f.d['id']]); preserve.assert_called_once_with(workspace, history); cleanup.assert_called_once_with(f.task['id'], 'stop'); self.assertEqual(record.call_args.args[0], 'research_model_reconcile'); self.assertTrue((task_root / 'checkpoint-reference.json').is_file())
                    credential.assert_not_called(); transport.assert_not_called()

    def test_missing_worker_directories_publish_only_diagnostics_and_an_active_workspace_lock_defers_cleanup(self):
        for missing in [True, False]:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                f = Fixture(1); spool = Path(temporary); task_root = spool / 'builder-workspaces' / f.task['id']; task_root.mkdir(parents=True)
                if not missing: (spool / f.d['id']).mkdir()
                def rows(kind, query):
                    if kind in ['model_dispatches', 'model_reconciliations']: return []
                    if kind == 'autonomy_tasks': return [f.task]
                    if kind == 'study_owners': return [dict(owner_id=f.task['actor_id'])]
                    raise AssertionError(kind)
                def artifact(study, owner, raw, ident, media, provenance): f.add(raw, ident, provenance); return f.records[ident]
                with (task_root / 'workspace.lock').open('a+b') as lock, f.patches(), patch.object(store, 'rows', side_effect=rows), patch.object(store, 'artifact', side_effect=artifact), patch.object(store, 'append'), patch.object(store, 'rpc') as record, patch.object(builder, 'container_action') as cleanup, patch.object(runtime, 'http_request') as transport:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); self.assertEqual(recovery._reconcile_dispatches(spool, [f.d]), [])
                    cleanup.assert_not_called(); transport.assert_not_called()
                    if missing:
                        self.assertEqual(record.call_count, 1); self.assertEqual(record.call_args.args[0], 'research_model_reconcile'); self.assertEqual(record.call_args.args[1]['p_proof']['version'], 'worker-runtime-diagnostics-v1'); self.assertFalse(record.call_args.args[1]['p_proof']['reconciliation_performed'])
                    else: record.assert_not_called()


if __name__ == '__main__': unittest.main()
