"""Private persistence, rotating credentials and frozen self-repair boundaries.

All service adapters are synthetic. These tests never contact a control plane.
"""
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import Mock, patch

from platform_supervisor import call_recovery, credentials, self_runtime, store
from platform_supervisor.common import Hold, canonical, digest, identity


TASK = '00000000-0000-4000-8000-000000000001'
WORKER = '00000000-0000-4000-8000-000000000002'
SECRET = 'arn:aws:secretsmanager:us-east-1:395567831870:secret:jumpserve/synthetic'


class PersistenceBoundaries(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='platform-persistence-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.opener = Mock()
        with patch.object(store, 'network_opener', return_value=self.opener):
            self.store = store.Store('synthetic-private-key', self.root, WORKER)
        self.store.task = dict(id=TASK, lease_token='synthetic-lease')

    def test_invalid_keys_relations_and_unleased_reads_fail_before_network(self):
        for key in ['', 'x' * 10001, None]:
            with self.subTest(key_length=len(key or '')), self.assertRaises(Hold):
                store.Store(key, self.root, WORKER)
        with self.assertRaisesRegex(ValueError, 'relation'):
            self.store.rows('private-users')
        self.store.task = None
        with self.assertRaisesRegex(ValueError, 'lease'):
            self.store.rows('runs')
        with self.assertRaisesRegex(Hold, 'redirects'):
            store.NoRedirect().redirect_request(None)
        self.opener.open.assert_not_called()

    def test_http_rejection_retains_typed_uncertain_outcome_without_private_response(self):
        self.opener.open.side_effect = urllib.error.HTTPError(
            store.ORIGIN + '/rest/v1/rpc/platform_worker', 503,
            'private provider detail', {}, io.BytesIO(b'private body'))
        with self.assertRaisesRegex(Hold, 'HTTP 503') as caught:
            self.store.rpc('lease')
        self.assertEqual(caught.exception.status, 'reconciling')
        self.assertNotIn('private', str(caught.exception))
        request = self.opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload, dict(p_operation='lease', p_worker=WORKER,
            p_task=TASK, p_token='synthetic-lease', p_payload={}))

    def test_an_oversized_response_stops_at_the_read_bound(self):
        response = Mock()
        response.read.return_value = b'x' * 5000001
        self.opener.open.return_value.__enter__ = Mock(return_value=response)
        self.opener.open.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaisesRegex(Hold, 'response exceeds'):
            self.store.request('/rest/v1/platform_runs')
        response.read.assert_called_once_with(5000001)

    def test_http_failure_while_reading_a_response_keeps_the_action_unconfirmed(self):
        response = Mock()
        response.read.side_effect = urllib.error.HTTPError('synthetic-response', 502, 'private detail', {}, None)
        self.opener.open.return_value.__enter__ = Mock(return_value=response)
        self.opener.open.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaisesRegex(Hold, 'HTTP 502') as caught:
            self.store.request('/rest/v1/platform_runs')
        self.assertEqual(caught.exception.status, 'reconciling')

    def test_retained_artifact_requires_exact_size_and_hash_and_never_reuploads(self):
        raw = b'unchanged original evidence\n'
        row = dict(id=identity(TASK, 'artifact:' + digest(raw)),
                   sha256=digest(raw), byte_count=len(raw))
        with patch.object(self.store, 'rows', return_value=[row]), \
                patch.object(self.store, 'request') as request:
            self.assertEqual(self.store.artifact('original', raw), row)
            for field, value in [('sha256', '0' * 64), ('byte_count', 0)]:
                with self.subTest(field=field), patch.dict(row, {field: value}), \
                        self.assertRaisesRegex(Hold, 'identity differs'):
                    self.store.artifact('original', raw)
            request.assert_not_called()
        self.assertFalse((self.root / TASK).exists())

    def test_new_artifact_retains_bytes_and_binds_verified_readback_before_recording(self):
        raw = b'\x00synthetic original\xff'
        result = dict(id='retained-artifact')
        with patch.object(self.store, 'rows', return_value=[]), \
                patch.object(self.store, 'request', side_effect=[None, raw]) as request, \
                patch.object(self.store, 'rpc', return_value=result) as rpc:
            self.assertEqual(self.store.artifact('original.bin', raw), result)
        self.assertEqual((self.root / TASK / 'artifacts' / digest(raw)).read_bytes(), raw)
        path = '/storage/v1/object/' + store.BUCKET + '/' + TASK + '/' + digest(raw)
        self.assertEqual(request.call_args_list[0].args, (path, 'POST', raw))
        self.assertEqual(request.call_args_list[1].kwargs, {'raw': True})
        operation, record = rpc.call_args.args
        self.assertEqual(operation, 'artifact')
        self.assertEqual(record['storage_path'], TASK + '/' + digest(raw))
        self.assertEqual(record['sha256'], digest(raw))
        self.assertEqual(record['byte_count'], len(raw))
        self.assertEqual(record['provenance'], {'version': 'platform-original-v1'})

    def test_uncertain_upload_is_reconciled_by_exact_bytes_without_another_post(self):
        raw = b'original retained bytes'
        failure = Hold('Upload acknowledgement unavailable.', 'reconciling')
        with patch.object(self.store, 'rows', return_value=[]), \
                patch.object(self.store, 'request', side_effect=[failure, raw, raw]) as request, \
                patch.object(self.store, 'rpc', return_value={'id': 'original'}) as rpc:
            self.assertEqual(self.store.artifact('original', raw, {'kind': 'synthetic'}), {'id': 'original'})
        self.assertEqual(len(request.call_args_list), 3)
        self.assertEqual(rpc.call_args.args[1]['provenance'], {'kind': 'synthetic'})
        self.assertEqual(sum(call.args[1:] == ('POST', raw) for call in request.call_args_list), 1)

    def test_unconfirmed_or_changed_readback_never_records_a_successful_artifact(self):
        raw = b'original'
        failure = Hold('Original upload remains unconfirmed.', 'reconciling')
        for outcomes, expected in [([failure, b'different'], 'unconfirmed'),
                                   ([None, b'different'], 'readback differs')]:
            with self.subTest(outcomes=outcomes), \
                    patch.object(self.store, 'rows', return_value=[]), \
                    patch.object(self.store, 'request', side_effect=outcomes), \
                    patch.object(self.store, 'rpc') as rpc, \
                    self.assertRaisesRegex(Hold, expected):
                self.store.artifact('original', raw)
            rpc.assert_not_called()
        with patch.object(self.store, 'rows') as rows, self.assertRaisesRegex(Hold, '5 MB'):
            self.store.artifact('too-large', b'x' * 5000001)
        rows.assert_not_called()

    def test_original_file_growth_after_stat_cannot_escape_the_delivery_bound(self):
        path = self.root / 'original.bin'
        path.write_bytes(b'within-bound')
        with patch.object(Path, 'read_bytes', return_value=b'x' * 101), \
                self.assertRaisesRegex(Hold, 'exceeds its bound'):
            call_recovery.original(path, 100)

    def test_request_metadata_remains_task_scoped_and_messages_are_bounded(self):
        with patch.object(self.store, 'request', return_value=[]) as request:
            self.assertEqual(self.store.rows('runs', limit='2', status='eq.unknown'), [])
            self.assertIn('task_id=eq.' + TASK, request.call_args.args[0])
            self.assertIn('limit=2', request.call_args.args[0])
        with patch.object(self.store, 'rpc', return_value={'recorded': True}) as rpc:
            self.assertEqual(self.store.event('retained', 'x' * 5000), {'recorded': True})
            self.assertEqual(len(rpc.call_args.args[1]['message']), 4000)
            self.assertEqual(rpc.call_args.args[1]['details'], {})
            self.store.checkpoint('reconciling', 'x' * 5000, {'zero': 0})
            self.assertEqual(rpc.call_args.args, ('checkpoint', dict(status='reconciling', reason='x' * 4000, checkpoint={'zero': 0})))


class CredentialBoundaries(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'SUPABASE_SECRET_ARN': SECRET, 'PLATFORM_SECRET_ARN': SECRET}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = Mock()
        self.session = Mock()
        self.session.get_credentials.return_value.method = 'iam-role'
        self.session.client.return_value = self.client

    def test_default_session_uses_only_the_instance_role(self):
        boto = types.ModuleType('boto3')
        boto.Session = Mock(return_value=self.session)
        with patch.dict('sys.modules', {'boto3': boto}):
            credential = credentials.Credentials()
        boto.Session.assert_called_once_with(region_name='us-east-1')
        self.session.client.assert_called_once_with('secretsmanager')
        self.assertEqual(credential.cache, {})

    def test_cached_secret_is_reused_before_expiry_and_refetched_at_expiry(self):
        credential = credentials.Credentials(self.session)
        self.client.get_secret_value.side_effect = [dict(SecretString='first'), dict(SecretString='rotated')]
        with patch.object(credentials.time, 'monotonic', side_effect=[100, 399, 400, 400]):
            self.assertEqual(credential.secret(SECRET), 'first')
            self.assertEqual(credential.secret(SECRET), 'first')
            self.assertEqual(credential.secret(SECRET), 'rotated')
        self.assertEqual(self.client.get_secret_value.call_count, 2)

    def test_named_accessors_return_only_the_requested_secret_fields(self):
        credential = credentials.Credentials(self.session)
        with patch.object(credential, 'secret', return_value='synthetic-db-key') as secret:
            self.assertEqual(credential.supabase(), 'synthetic-db-key')
            secret.assert_called_once_with(SECRET)
        with patch.object(credential, 'secret', return_value=canonical(dict(github_token='synthetic-github', context7_api_key='synthetic-docs')).decode()):
            self.assertEqual(credential.github(), 'synthetic-github')
            self.assertEqual(credential.context7(), 'synthetic-docs')
        for token in [None, '', 0, False]:
            with self.subTest(token=token), patch.object(credential, 'secret', return_value=json.dumps({'github_token': token})), self.assertRaisesRegex(Hold, 'credential is missing'):
                credential.github()
        with patch.object(credential, 'secret', return_value='{}'):
            self.assertIsNone(credential.context7())

    def test_cached_read_clock_failure_releases_the_lock_and_retains_the_cached_value(self):
        credential = credentials.Credentials(self.session)
        credential.cache[SECRET] = (100, 'original-secret')
        with patch.object(credentials.time, 'monotonic', side_effect=OSError('Synthetic clock unavailable')), self.assertRaises(OSError):
            credential.secret(SECRET)
        self.assertTrue(credential.lock.acquire(blocking=False))
        credential.lock.release()
        self.assertEqual(credential.cache[SECRET], (100, 'original-secret'))
        self.client.get_secret_value.assert_not_called()


class SelfRuntimeBoundaries(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='self-runtime-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace'
        self.directory = self.workspace / 'backend'
        self.directory.mkdir(parents=True)
        self.controller = types.SimpleNamespace(
            state={'sources': {self_runtime.BACKEND: {'snapshot': {'platform_supervisor/helper.py': {'sha256': 'old'}}}}},
            store=Mock(), github=Mock(), save_state=Mock())
        self.manifest = dict(source_revision='a' * 40)
        self.controller.store.request.return_value = self.manifest
        self.scope = dict(source_binding='synthetic-binding')

    def prepare(self):
        return self_runtime.prepare(self.controller, self.workspace, self.directory)

    def test_missing_frozen_gate_is_an_explicit_hold(self):
        with patch.object(Path, 'is_file', return_value=False), self.assertRaisesRegex(Hold, 'Frozen supervisor scope gate'):
            self_runtime.gate()

    def test_unchanged_supervisor_needs_no_qualification_and_mixed_changes_are_refused(self):
        original = self.controller.state['sources'][self_runtime.BACKEND]['snapshot']
        with patch('platform_supervisor.capabilities.validate_candidate') as validate, patch.object(self_runtime.source, 'snapshot', return_value=original):
            self.assertIsNone(self.prepare())
            validate.assert_called_once_with(self.controller, self.directory)
        self.controller.store.request.assert_not_called()
        with patch('platform_supervisor.capabilities.validate_candidate'), patch.object(self_runtime.source, 'snapshot', return_value={'unrelated.py': {}}), self.assertRaisesRegex(Hold, 'isolated operational'):
            self.prepare()

    def test_absent_activation_does_not_fetch_or_qualify_candidate(self):
        self.controller.store.request.return_value = None
        with patch('platform_supervisor.capabilities.validate_candidate'), patch.object(self_runtime.source, 'snapshot', return_value={}), self.assertRaisesRegex(Hold, 'activation is unavailable'):
            self.prepare()
        self.controller.github.archive.assert_not_called()

    def test_exact_predecessor_is_fetched_once_and_frozen_gate_binds_candidate(self):
        frozen = Mock()
        frozen.inspect.return_value = self.scope
        previous = self.root / 'supervisor-qualification' / ('a' * 40) / 'backend'
        with patch('platform_supervisor.capabilities.validate_candidate'), patch.object(self_runtime.source, 'snapshot', return_value={}), patch.object(self_runtime.source, 'extract') as extract, patch.object(self_runtime, 'gate', return_value=frozen):
            self.assertEqual(self.prepare(), self.scope)
            extract.assert_called_once_with(self.controller.github.archive.return_value, previous)
            self.controller.github.archive.assert_called_once_with(self_runtime.BACKEND, 'a' * 40)
            frozen.inspect.assert_called_once_with(previous / 'platform_supervisor', self.directory / 'platform_supervisor', self.manifest)
            previous.mkdir(parents=True)
            extract.reset_mock()
            self.assertEqual(self.prepare(), self.scope)
            extract.assert_not_called()
        self.assertEqual(self.controller.state['supervisor_runtime'], dict(scope=self.scope, previous_manifest=self.manifest))

    def test_frozen_scope_failure_does_not_derive_a_qualification(self):
        frozen = Mock()
        frozen.inspect.side_effect = ValueError('Protected bytes differ')
        with patch('platform_supervisor.capabilities.validate_candidate'), patch.object(self_runtime.source, 'snapshot', return_value={}), patch.object(self_runtime.source, 'extract'), patch.object(self_runtime, 'gate', return_value=frozen), self.assertRaisesRegex(Hold, 'Protected bytes differ'):
            self.prepare()
        self.assertNotIn('supervisor_runtime', self.controller.state)

    def test_qualification_requires_passed_checks_and_retains_a_non_scientific_certificate(self):
        artifact = dict(id='checks', sha256='b' * 64)
        for passed in [None, False, 1, 'true']:
            with self.subTest(passed=passed), self.assertRaisesRegex(Hold, 'executed fixed checks'):
                self_runtime.qualify(self.controller, self.directory, self.scope, {'passed': passed}, artifact)
        self.controller.store.artifact.assert_not_called()
        self.controller.state['supervisor_runtime'] = dict(scope=self.scope)
        self.controller.store.artifact.return_value = dict(id='certificate')
        self_runtime.qualify(self.controller, self.directory, self.scope, {'passed': True}, artifact)
        name, raw = self.controller.store.artifact.call_args.args
        proof = json.loads(raw)
        self.assertEqual(name, 'supervisor-runtime-qualification.json')
        self.assertEqual(proof['checks_artifact_id'], 'checks')
        self.assertEqual(proof['checks_sha256'], artifact['sha256'])
        self.assertFalse(proof['scientific_completion'])
        self.assertEqual(self.controller.state['supervisor_runtime']['certificate_artifact'], 'certificate')
        self.controller.save_state.assert_called_once()


if __name__ == '__main__':
    unittest.main()
