"""Native builder orchestration with in-memory HTTP and synthetic process adapters.

Real stream parsing, byte retention, checkpointing and admission ordering are
tested. No Docker daemon, model credential, provider or cloud service is used.
"""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder
import store
from workflow import canonical, digest

def uid(): return str(uuid.uuid4())
def completed(output=None):
    return dict(status='completed', model=builder.runtime.MODEL, output=output or [dict(type='message', role='assistant', content=[dict(type='output_text', text='Development checkpoint')])], usage=dict(input_tokens=4, output_tokens=2, total_tokens=6))
def stream(value): return b'data: ' + canonical(dict(type='response.completed', response=value)) + b'\n\n'


class NativeBuilderExecution(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='builder-native-unit-'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.job = dict(id=uid(), task_id=uid(), study_id=uid(), actor_id=uid(), lease_token=uid())
        self.logical = dict(system=[{'text': 'Synthetic trusted software-development instructions.'}], builder={'context': {'task': 'building', 'claim': {'id': uid(), 'description': 'Synthetic prospective implementation'}}})
        self.blobs, self.events, self.artifacts, self.provider_calls, self.bridge_replies = {}, [], [], [], []
        self.cli_action = lambda configuration, workspace: None
        self.cli_envelope, self.cli_code = {'status': 'ok'}, 0
        self.provider_status, self.provider_bytes = 200, stream(completed())
        self.begin_result, self.finish_error, self.token_error = None, None, None
        self.server = None; self.thread_joined = False
        test = self
        class Server:
            def __init__(self, address, handler):
                self.server_address = ('127.0.0.1', 49152); self.handler = handler; self.stopped = self.closed = False; test.server = self
            def serve_forever(self): pass
            def shutdown(self): self.stopped = True
            def server_close(self): self.closed = True
        class Thread:
            def __init__(self, target, daemon): test.assertTrue(daemon)
            def start(self): pass
            def join(self, timeout): test.assertEqual(timeout, 2); test.thread_joined = True
        self.enterContext(patch.object(builder, 'ThreadingHTTPServer', Server)); self.enterContext(patch.object(builder.threading, 'Thread', Thread))
        self.enterContext(patch.object(builder.subprocess, 'run', side_effect=self.cli))
        self.containers = self.enterContext(patch.object(builder, 'container_action', return_value=[]))
        self.normalize = self.enterContext(patch.object(builder, 'normalize_workspace', return_value=b'{"bytes_unchanged":true}'))
        self.package = self.enterContext(patch.object(builder, 'builder_package', return_value=self.root / 'pinned-openclaw'))
        self.candidate = self.enterContext(patch.object(builder, 'freeze_candidate', return_value=None))
        self.enterContext(patch.object(store, 'artifact', side_effect=self.artifact)); self.enterContext(patch.object(store, 'append', side_effect=self.append))
        self.enterContext(patch.object(store, 'artifact_bytes', side_effect=lambda study, identity, **kw: self.blobs[identity]))
        self.enterContext(patch.object(store, 'rpc', side_effect=self.rpc)); self.rows = self.enterContext(patch.object(store, 'rows', return_value=[]))
        self.enterContext(patch.dict('os.environ', {'OPENCLAW_BUILDER_IMAGE': 'sha256:' + 'a' * 64}))

    def artifact(self, study, actor, raw, identity, media, provenance):
        self.assertEqual((study, actor), (self.job['study_id'], self.job['actor_id']))
        if identity in self.blobs: self.assertEqual(self.blobs[identity], raw, 'Cannot rewrite a retained original')
        self.blobs[identity] = raw; self.events.append(('artifact', provenance['filename']))
        return dict(id=identity, sha256=digest(raw), byte_count=len(raw), provenance=provenance)
    def append(self, study, actor, kind, record):
        self.assertEqual(kind, 'artifacts'); self.artifacts.append(copy.deepcopy(record)); return record
    def rpc(self, name, payload):
        self.assertEqual(name, 'research_builder_call'); operation = payload['p_operation']; values = payload['p_payload']
        self.assertEqual((values['dispatch_id'], values['token']), (self.job['id'], self.job['lease_token']))
        self.events.append((operation, copy.deepcopy(values)))
        if operation == 'begin': return self.begin_result if self.begin_result is not None else {'id': str(uuid.uuid5(uuid.UUID(self.job['id']), values['request_sha256']))}
        if operation == 'finish':
            if self.finish_error: raise self.finish_error
            return {'recorded': True}
        raise AssertionError(operation)
    def token(self):
        self.events.append(('credential', None))
        if self.token_error: raise self.token_error
        return 'synthetic-native-token'
    def transport(self, method, url, token, body):
        self.assertEqual((method, url, token), ('POST', 'https://api.openai.com/v1/responses', 'synthetic-native-token'))
        self.events.append(('transport', digest(body))); self.provider_calls.append(body)
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        result = Response(self.provider_bytes); result.status = self.provider_status; return result
    def request(self, **changes):
        request = dict(model=builder.runtime.MODEL, input=[{'role': 'user', 'content': 'Continue the synthetic implementation'}], tools=[{'type': 'function', 'name': name} for name in builder.TOOLS])
        request.update(changes); return request
    def post(self, configuration, request=None, path='/v1/responses', authorization=None, length=None):
        raw = canonical(request or self.request()); handler = object.__new__(self.server.handler)
        handler.path = path; handler.headers = {'Authorization': authorization or 'Bearer ' + configuration['models']['providers']['lab']['apiKey'], 'Content-Length': str(len(raw) if length is None else length)}
        handler.rfile = io.BytesIO(raw); handler.wfile = io.BytesIO(); reply = {'headers': {}}
        handler.send_response = lambda status: reply.update(status=status)
        handler.send_header = lambda key, value: reply['headers'].update({key: value})
        handler.end_headers = lambda: None
        handler.send_error = lambda status, reason: reply.update(status=status, reason=reason)
        handler.log_message('Private bridge requests must not be logged')
        handler.do_POST(); reply['body'] = handler.wfile.getvalue(); self.bridge_replies.append(reply); return reply
    def cli(self, command, **options):
        self.assertEqual(command[2:4], ['agent', 'exec']); self.assertTrue(options['capture_output']); self.assertEqual(options['timeout'], builder.SLICE_SECONDS + 20)
        configuration = json.loads(Path(command[command.index('--config') + 1]).read_bytes()); workspace = Path(command[command.index('--cwd') + 1])
        self.assertEqual(configuration['agents']['defaults']['sandbox']['docker']['image'], 'sha256:' + 'a' * 64)
        self.assertEqual(options['env']['JUMPSERVE_LAB_SANDBOX_SUDO'], '1')
        self.cli_action(configuration, workspace)
        return subprocess.CompletedProcess(command, self.cli_code, canonical(self.cli_envelope), b'Synthetic native CLI diagnostics')
    def execute(self, name='slice'):
        return builder.execute(self.logical, self.root / name, self.job, token_source=self.token, transport=self.transport)
    def receipt(self, name='slice'): return json.loads((self.root / name / 'receipt.json').read_bytes())
    def workspace(self): return self.root / 'builder-workspaces' / self.job['task_id'] / 'workspace'

    def test_checkpoint_only_slice_preserves_full_logical_context_and_unknown_costs_without_a_provider_call(self):
        original = canonical(self.logical); answer, result = self.execute()
        self.assertEqual(result['status'], 'complete'); self.assertEqual(result['build_state'], 'checkpoint'); self.assertEqual(result['provider_calls'], 0); self.assertIsNone(result['measured_charges_usd'])
        self.assertEqual(answer['output']['message']['content'][0]['toolUse']['input']['action'], 'blocked')
        self.assertEqual((self.root / 'slice/logical.json').read_bytes(), original); self.assertEqual(result['action_policy_sha256'], builder.action_policy()['sha256'])
        raw = self.blobs[result['checkpoint']['artifact_id']]
        with zipfile.ZipFile(io.BytesIO(raw)) as archive: self.assertEqual(json.loads(archive.read('research-context.json')), self.logical['builder']['context'])
        self.assertEqual(self.provider_calls, []); self.assertTrue(self.server.closed and self.server.stopped and self.thread_joined)
        self.assertEqual([c.args for c in self.containers.call_args_list], [(self.job['task_id'], 'start'), (self.job['task_id'], 'stop'), (self.job['task_id'], 'stop')])

    def test_provider_admission_precedes_credential_transport_and_exact_cached_replay_never_reinvokes(self):
        def native(config, workspace):
            first = self.post(config); second = self.post(config); self.assertEqual(first, second)
        self.cli_action = native; answer, result = self.execute()
        self.assertEqual(result['status'], 'complete'); self.assertEqual(result['provider_calls'], 1); self.assertEqual(len(self.provider_calls), 1)
        names = [e[0] for e in self.events]; self.assertLess(names.index('begin'), names.index('credential')); self.assertLess(names.index('credential'), names.index('transport')); self.assertLess(names.index('transport'), names.index('finish'))
        body = json.loads(self.provider_calls[0]); self.assertFalse(body['store']); self.assertTrue(body['stream']); self.assertEqual(body['input'], self.request()['input'])
        response = next(a for a in self.artifacts if a['provenance']['filename'] == 'call-1-response.bin'); self.assertEqual(self.blobs[response['id']], self.provider_bytes)
        completion = json.loads((self.root / 'slice/call-1-completion.json').read_bytes()); self.assertEqual(completion['response_sha256'], digest(self.provider_bytes)); self.assertTrue(completion['response_received']); self.assertEqual(completion['status'], 'complete')
        self.assertEqual(result['usage']['calls'], [{'inputTokens': 4, 'outputTokens': 2, 'totalTokens': 6}]); self.assertEqual(self.bridge_replies[0]['headers']['Content-Type'], 'text/event-stream'); self.assertIsNotNone(answer)

    def test_private_bridge_and_native_request_identity_reject_before_admission(self):
        def native(config, workspace):
            for kwargs in [dict(path='/other'), dict(authorization='Bearer incorrect'), dict(length=0), dict(length=builder.MAX_REQUEST + 1), dict(request=self.request(model='other-model')), dict(request=self.request(previous_response_id='prior')), dict(request=self.request(background=True)), dict(request=self.request(tools=[{'type': 'function', 'name': 'arbitrary-host-tool'}]))]:
                self.assertEqual(self.post(config, **kwargs)['status'], 400)
        self.cli_action = native; _, result = self.execute(); self.assertEqual(result['provider_calls'], 0); self.assertEqual(len(result['bridge_errors']), 8)
        self.assertFalse(any(name in ['begin', 'credential', 'transport'] for name, _ in self.events))

    def test_context_boundary_retains_denied_request_and_checkpoint_without_a_provider_call(self):
        self.cli_action = lambda config, workspace: self.post(config, self.request(input=[{'role': 'user', 'content': 'x' * builder.CONTEXT_CHECKPOINT_BYTES}]))
        self.cli_code = 1; answer, result = self.execute()
        self.assertTrue(result['context_checkpoint']); self.assertFalse(result['denied_request']['provider_request_sent']); self.assertEqual(result['status'], 'complete'); self.assertEqual(result['provider_calls'], 0)
        original = self.root / 'slice' / result['denied_request']['filename']; self.assertEqual(digest(original.read_bytes()), result['denied_request']['received_sha256']); self.assertIsNotNone(answer)

    def test_budget_denial_is_a_checkpoint_and_never_acquires_credentials(self):
        self.begin_result = {'denied': 'qualification-budget'}; self.cli_code = 1; self.cli_action = lambda config, workspace: self.post(config)
        _, result = self.execute(); self.assertEqual(result['admission_denied'], 'qualification-budget'); self.assertEqual(result['status'], 'complete'); self.assertEqual(result['provider_calls'], 0)
        self.assertFalse(any(name == 'credential' for name, _ in self.events))

    def test_lost_admission_acknowledgement_records_that_no_provider_request_was_sent(self):
        rpc = self.rpc
        def missing(name, payload):
            if payload['p_operation'] == 'begin': raise store.StoreError('Synthetic admission acknowledgement lost')
            return rpc(name, payload)
        self.cli_action = lambda config, workspace: self.post(config)
        with patch.object(store, 'rpc', side_effect=missing): _, result = self.execute()
        self.assertTrue(result['admission_unconfirmed']); self.assertFalse(result['provider_request_sent']); self.assertEqual(result['provider_calls'], 0); self.assertFalse(self.provider_calls)

    def test_authentication_quota_failed_stream_and_malformed_error_payloads_preserve_received_bytes(self):
        for status, raw, expected in [(401, b'{"error":{"code":"subscription_sharing_invalid_user"}}', 'paused-auth'), (429, b'not JSON', 'paused-quota'), (503, b'[]', 'failed'), (200, b'data: not-json\n\n', 'unknown')]:
            self.job['id'] = uid(); self.provider_status, self.provider_bytes = status, raw; self.cli_action = lambda config, workspace: self.post(config)
            name = 'failure-' + str(status) + '-' + expected; answer, result = self.execute(name)
            self.assertIsNone(answer); self.assertEqual(result['status'], expected); self.assertEqual(result['http_status'], status); self.assertTrue(result['response_received']); self.assertEqual(result['provider_calls'], 1)
            self.assertEqual((self.root / name / 'call-1-response.bin').read_bytes(), raw)
            self.logical['builder']['checkpoint'] = result['checkpoint']

    def test_invalid_generated_host_override_is_retained_and_never_delivered_to_native_worker(self):
        self.provider_bytes = stream(completed([dict(type='function_call', name='exec', call_id='synthetic-call', arguments=canonical({'command': 'whoami', 'host': 'gateway', 'elevated': True}).decode())]))
        self.cli_action = lambda config, workspace: self.post(config); answer, result = self.execute()
        self.assertEqual(result['status'], 'invalid-output'); self.assertIsNone(answer); self.assertEqual(self.bridge_replies[0]['status'], 400); self.assertEqual(self.bridge_replies[0]['body'], b'')
        finish = next(v for name, v in self.events if name == 'finish'); self.assertEqual(finish['status'], 'invalid-output'); self.assertTrue(finish['response_artifact_id'])

    def test_lost_finish_acknowledgement_keeps_completion_sidecar_and_removes_unknown_container(self):
        self.finish_error = store.StoreError('Synthetic finish acknowledgement lost'); self.cli_action = lambda config, workspace: self.post(config); answer, result = self.execute()
        self.assertIsNone(answer); self.assertEqual(result['status'], 'unknown'); self.assertEqual(result['error_code'], 'native-persistence-unconfirmed'); self.assertEqual(self.containers.call_args.args[1], 'remove')
        self.assertTrue((self.root / 'slice/call-1-completion.json').exists()); self.assertEqual(result['provider_calls'], 1); self.assertTrue(result['checkpoint'])

    def test_missing_credential_has_no_transport_and_does_not_invent_received_usage(self):
        self.token_error = RuntimeError('Synthetic unavailable credential'); self.cli_action = lambda config, workspace: self.post(config); answer, result = self.execute()
        self.assertIsNone(answer); self.assertEqual(result['provider_calls'], 0); self.assertFalse(result['response_received']); self.assertEqual(result['usage']['calls'], [None]); self.assertFalse(self.provider_calls)

    def test_candidate_feedback_keeps_original_checkpoint_and_never_claims_scientific_completion(self):
        self.candidate.side_effect = ValueError('Frozen package contract differs'); answer, result = self.execute()
        self.assertEqual(result['status'], 'complete'); self.assertEqual(result['build_state'], 'checkpoint'); self.assertEqual(result['feedback'], 'Frozen package contract differs'); self.assertIsNotNone(answer); self.assertTrue(result['checkpoint'])

    def test_failed_native_cleanup_or_failed_container_cleanup_cannot_return_a_success(self):
        self.cli_envelope = {'status': 'error', 'error': {'kind': 'cleanup'}}; answer, result = self.execute('native-cleanup')
        self.assertIsNone(answer); self.assertEqual(result['status'], 'unknown'); self.assertEqual(result['container_cleanup'], 'confirmed')
        self.logical['builder']['checkpoint'] = result['checkpoint']; self.job['id'] = uid(); self.cli_envelope = {'status': 'ok'}
        self.containers.side_effect = lambda task, action: (_ for _ in ()).throw(RuntimeError('Synthetic Docker cleanup error')) if action == 'stop' else []
        answer, result = self.execute('docker-cleanup'); self.assertIsNone(answer); self.assertEqual(result['container_cleanup'], 'unknown'); self.assertEqual(result['status'], 'unknown')

    def test_worker_slot_boundary_denies_before_admission_and_retains_the_request_identity(self):
        self.cli_action = lambda config, workspace: self.post(config); self.cli_code = 1
        with patch.object(builder.time, 'monotonic', side_effect=[0, 120, 120]): answer, result = self.execute()
        self.assertTrue(result['slice_exhausted']); self.assertEqual(result['denied_request']['reason_code'], 'worker-slot-ended'); self.assertEqual(result['provider_calls'], 0); self.assertIsNotNone(answer)
        self.assertFalse(any(name == 'begin' for name, _ in self.events))

    def test_native_context_overflow_after_real_call_checkpoints_but_bootstrap_overflow_does_not_claim_a_call(self):
        for sent in [False, True]:
            self.job['id'] = uid(); self.cli_action = (lambda config, workspace: self.post(config)) if sent else (lambda config, workspace: None)
            self.cli_envelope = {'status': 'error', 'error': {'kind': 'context_overflow'}}; answer, result = self.execute('overflow-' + str(sent))
            self.assertEqual(result['provider_calls'], int(sent)); self.assertEqual(result['native_error_kind'], 'context_overflow')
            if sent: self.assertIsNotNone(answer); self.assertTrue(result['context_checkpoint']); self.assertEqual(result['status'], 'complete')
            else: self.assertIsNone(answer); self.assertEqual(result['error_code'], 'native-bootstrap-context-overflow')
            self.logical['builder']['checkpoint'] = result['checkpoint']

    def test_source_material_is_hash_bound_and_large_text_is_only_an_explicit_reference(self):
        raw = canonical({'pages': [{'page': 3, 'text': 'Synthetic exact extracted original'}]}); identity = uid(); self.blobs[identity] = raw
        material = dict(text_artifact_id=identity, text_artifact_sha256=digest(raw))
        self.logical['builder']['context']['source_material'] = material; self.rows.return_value = [{'sha256': digest(raw), 'byte_count': len(raw)}]
        self.execute('small-source'); self.assertEqual((self.workspace() / 'paper-text.json').read_bytes(), raw)
        # A separate task has no inherited local file; a large source is not
        # fetched or mislabeled as fully delivered/reviewed.
        self.job['task_id'], self.job['id'] = uid(), uid(); self.rows.return_value = [{'sha256': digest(raw), 'byte_count': 1000001}]
        self.execute('large-source'); self.assertFalse((self.workspace() / 'paper-text.json').exists()); self.assertIn('Do not claim full review', json.loads((self.workspace() / 'paper-text-reference.json').read_bytes())['delivery'])
        self.job['task_id'], self.job['id'] = uid(), uid(); self.rows.return_value = [{'sha256': '0' * 64, 'byte_count': len(raw)}]
        with self.assertRaisesRegex(ValueError, 'source binding differs'): self.execute('changed-source')

    def test_restart_restores_original_checkpoint_history_and_versions_previous_candidate_files(self):
        initial = self.root / 'initial'; initial.mkdir()
        originals = {'candidate.json': b'Original rejected candidate', 'blocker.json': b'Original blocker', 'runner-contract.json': b'{"old":"contract"}', 'native-tool-history.json': b'[]'}
        for name, raw in originals.items(): (initial / name).write_bytes(raw)
        raw = builder.checkpoint(initial); identity = uid(); self.blobs[identity] = raw; self.logical['builder']['checkpoint'] = {'artifact_id': identity, 'sha256': digest(raw)}
        _, result = self.execute()
        self.assertEqual(result['status'], 'complete'); self.assertEqual((self.workspace() / 'previous-candidate.json').read_bytes(), originals['candidate.json']); self.assertEqual((self.workspace() / 'previous-blocker.json').read_bytes(), originals['blocker.json'])
        self.assertEqual((self.workspace() / ('previous-runner-contract-' + digest(originals['runner-contract.json']) + '.json')).read_bytes(), originals['runner-contract.json'])
        self.assertEqual((self.workspace().parent / 'native-tool-history.json').read_bytes(), b'[]')
        self.logical['builder']['checkpoint'] = result['checkpoint']; self.job['id'] = uid(); self.execute('resumed')
        self.logical['builder'].pop('checkpoint'); self.job['id'] = uid()
        with self.assertRaisesRegex(ValueError, 'cannot be reset'): self.execute('missing-reference')

    def test_non_list_retained_history_is_rejected_before_native_or_provider_execution(self):
        workspace = self.workspace(); workspace.mkdir(parents=True); (workspace.parent / 'native-tool-history.json').write_bytes(b'{"changed":"history"}')
        with self.assertRaisesRegex(ValueError, 'history is invalid'): self.execute()
        self.assertIsNone(self.server); self.assertFalse(self.provider_calls)

    def repair_context(self):
        identity = uid(); raw = canonical({'diagnosis': 'Synthetic retained incident; not scientific evidence'}); self.blobs[identity] = raw
        self.logical['builder']['context'].update(repair_directive={'scope': 'software-development-only'}, repair_packet={'incident_sha256': 'a' * 64, 'diagnosis': {'artifact_id': identity, 'sha256': digest(raw), 'byte_count': len(raw)}})
        return self.logical['builder']['context']

    def test_native_repair_binds_original_workspace_and_retains_validation_in_a_separate_checkpoint(self):
        import repair_validation
        self.repair_context(); original = canonical(self.logical)
        def native(config, workspace):
            self.post(config); (workspace / 'candidate.json').write_bytes(b'{"prospective":"sentinel validated by the mocked candidate boundary"}')
        self.cli_action = native; self.candidate.return_value = {'protocol': {'fixture': 'separately validated prospective protocol'}, 'experiment_type': 'new-measurement'}
        def validate(job, workspace, context, retain):
            (workspace / 'executed-validation.json').write_bytes(b'{"software-check":"synthetic-boundary"}')
            return {'passed': True, 'scope': 'software-development-only'}
        with patch.object(repair_validation, 'validate', side_effect=validate): answer, result = self.execute()
        self.assertEqual((self.root / 'slice/logical.json').read_bytes(), original); self.assertEqual(result['build_state'], 'candidate'); self.assertTrue(result['repair_validation']['passed'])
        self.assertEqual(result['native_repair_handoff']['incident_sha256'], 'a' * 64); self.assertTrue(result['native_repair_handoff']['baseline_checkpoint'])
        before = next(a for a in self.artifacts if a['provenance']['filename'] == 'checkpoint.zip')
        with zipfile.ZipFile(io.BytesIO(self.blobs[before['id']])) as archive: self.assertNotIn('executed-validation.json', archive.namelist())
        with zipfile.ZipFile(io.BytesIO(self.blobs[result['checkpoint']['artifact_id']])) as archive: self.assertIn('executed-validation.json', archive.namelist())
        decision = answer['output']['message']['content'][0]['toolUse']['input']; self.assertEqual(decision['action'], 'propose'); self.assertFalse(decision['coverage_complete']); self.assertIsNone(decision['assessment_label'])

    def test_failed_repair_validation_keeps_its_diagnostics_without_admitting_a_candidate(self):
        import repair_validation
        self.repair_context(); self.cli_action = lambda config, workspace: (workspace / 'candidate.json').write_bytes(b'Original invalid candidate')
        failure = repair_validation.RepairCheckFailure({'artifact_id': uid(), 'sha256': 'b' * 64})
        with patch.object(repair_validation, 'validate', side_effect=failure): answer, result = self.execute()
        self.assertEqual(result['build_state'], 'checkpoint'); self.assertEqual(result['repair_validation_failure'], failure.repair_evidence); self.candidate.assert_not_called()
        self.assertEqual(answer['output']['message']['content'][0]['toolUse']['input']['action'], 'blocked'); self.assertIn('repair-check-stdout', result['feedback'])

    def test_blocker_declaration_must_use_the_actual_typed_contract_and_is_not_a_scientific_label(self):
        variants = [('capacity', {'blockers': [{'kind': 'capacity', 'detail': 'resource-exhausted', 'required_input': builder.runtime.model.LIMIT_INPUT}]}), ('invalid', {'unexpected': True}), ('both', {'blockers': []})]
        # External prerequisites use the actual fixed typed decision contract.
        for name, declaration in variants:
            self.job['task_id'], self.job['id'] = uid(), uid()
            def native(config, workspace):
                (workspace / 'blocker.json').write_bytes(canonical(declaration))
                if name == 'both': (workspace / 'candidate.json').write_bytes(b'Original ambiguous candidate')
            self.cli_action = native
            if name == 'capacity':
                answer, result = self.execute(name)
                self.assertEqual(result['build_state'], 'external-blocked'); self.assertEqual(answer['output']['message']['content'][0]['toolUse']['input']['reason'], 'resource-limit')
            else:
                answer, result = self.execute(name); self.assertEqual(result['build_state'], 'checkpoint'); self.assertTrue(result['feedback']); self.assertFalse(answer['output']['message']['content'][0]['toolUse']['input']['coverage_complete'])

    def test_failed_workspace_normalization_does_not_invent_a_verified_checkpoint(self):
        self.normalize.side_effect = RuntimeError('Synthetic normalization rejection'); answer, result = self.execute()
        self.assertIsNone(answer); self.assertEqual(result['checkpoint_failure'], 'RuntimeError'); self.assertNotIn('checkpoint', result)

    def test_changed_checkpoint_identity_is_rejected_before_native_execution(self):
        identity = uid(); self.blobs[identity] = b'Changed checkpoint bytes'
        self.logical['builder']['checkpoint'] = dict(artifact_id=identity, sha256='a' * 64, version=builder.VERSION)
        with self.assertRaisesRegex(ValueError, 'checkpoint original hash differs'): self.execute()
        self.assertIsNone(self.server); self.package.assert_not_called(); self.assertFalse(self.provider_calls)

    def test_reference_implementations_cannot_be_redirected_or_replaced_before_admission(self):
        original = self.root / 'original'; original.write_bytes(b'Original outside workspace')
        for kind in ['directory-link', 'file-link', 'changed-file']:
            self.job['task_id'], self.job['id'] = uid(), uid(); workspace = self.workspace(); workspace.mkdir(parents=True)
            directory = workspace / 'reference-implementations'
            if kind == 'directory-link': directory.symlink_to(self.root, target_is_directory=True)
            else:
                directory.mkdir(); raw = Path(builder.__file__).with_name('netem_iperf.py').read_bytes(); target = directory / ('netem_iperf-' + digest(raw) + '.py')
                if kind == 'file-link': target.symlink_to(original)
                else: target.write_bytes(b'Changed reference implementation')
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'reference (directory|file|implementation)'): self.execute(kind)
            self.assertEqual(original.read_bytes(), b'Original outside workspace'); self.assertIsNone(self.server)
        self.assertFalse(self.provider_calls); self.package.assert_not_called()

    def test_context_budget_cannot_follow_a_generated_symlink(self):
        original = self.root / 'original'; original.write_bytes(b'Original unrelated record')
        def native(config, workspace):
            (workspace / 'native-context-budget.json').symlink_to(original)
            self.assertEqual(self.post(config)['status'], 400)
        self.cli_action = native; answer, result = self.execute()
        self.assertIsNone(answer); self.assertEqual(result['provider_calls'], 0); self.assertFalse(any(e[0] == 'begin' for e in self.events)); self.assertIn('cannot replace a symlink', result['bridge_errors'][0]); self.assertEqual(original.read_bytes(), b'Original unrelated record')

    def test_in_flight_duplicate_never_repeats_admission_or_transport(self):
        original_transport = self.transport
        def native(config, workspace):
            # The request handler closes over the transport passed to execute.
            self.reentrant_configuration = config
            self.assertEqual(self.post(config)['status'], 200)
        def transport(*args):
            duplicate = self.post(self.reentrant_configuration); self.assertEqual(duplicate['status'], 400)
            return original_transport(*args)
        self.cli_action = native
        with patch.object(self, 'transport', side_effect=transport): answer, result = self.execute()
        self.assertEqual(len(self.provider_calls), 1); self.assertEqual(sum(e[0] == 'begin' for e in self.events), 1); self.assertEqual(sum(e[0] == 'finish' for e in self.events), 1); self.assertIsNotNone(answer); self.assertEqual(result['provider_calls'], 1)
        self.assertIn('In-flight native call cannot be replayed.', result['bridge_errors'])

    def test_oversized_blocker_remains_in_checkpoint_without_becoming_an_external_prerequisite(self):
        raw = canonical({'blockers': [{'kind': 'capacity', 'detail': 'x' * 12001}]})
        self.cli_action = lambda config, workspace: (workspace / 'blocker.json').write_bytes(raw)
        answer, result = self.execute(); self.assertEqual(result['build_state'], 'checkpoint'); self.assertIn('safe file bound', result['feedback'])
        self.assertEqual(answer['output']['message']['content'][0]['toolUse']['input']['blockers'][0]['kind'], 'implementation')
        with zipfile.ZipFile(io.BytesIO(self.blobs[result['checkpoint']['artifact_id']])) as archive: self.assertEqual(archive.read('blocker.json'), raw)

    def test_failed_checkpoint_retention_reuses_completed_normalization_and_keeps_status_failed(self):
        original = builder.retain
        def retain(job, name, raw, kind='builder-original'):
            if name == 'checkpoint.zip': raise OSError('Synthetic storage acknowledgment lost')
            return original(job, name, raw, kind)
        with patch.object(builder, 'retain', side_effect=retain): answer, result = self.execute()
        self.assertIsNone(answer); self.assertEqual(result['status'], 'failed'); self.assertIn('storage acknowledgment lost', result['feedback']); self.normalize.assert_called_once()
        self.assertTrue(result['checkpoint']); self.assertTrue(any(a['provenance']['filename'] == 'failed-checkpoint.zip' for a in self.artifacts))


if __name__ == '__main__': unittest.main()
