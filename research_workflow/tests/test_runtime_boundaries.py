"""Pinned runtime, read-only auth state and transport boundaries without providers."""
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import openclaw_runtime as runtime
from workflow import canonical, digest


class Response(io.BytesIO):
    def __init__(self, raw, status=200): super().__init__(raw); self.status = status


class CredentialBoundary(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='credential-boundary-unit-'); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.auth = self.root / 'auth'; (self.auth / 'state').mkdir(parents=True); self.package = self.root / 'package'; self.package.mkdir(); self.node = self.root / 'node/bin/node'
        for name, value in [('AUTH', self.auth), ('PACKAGE', self.package), ('NODE', self.node)]: self.enterContext(patch.object(runtime, name, value))
        self.enterContext(patch.object(runtime.time, 'time', return_value=1000))
        self.profile = dict(type='oauth', provider='openai', authFlow='chatgpt-token-sharing', issuer='https://auth.openai.com', access='synthetic-access-token', expires=2_000_000, grantedScope='resource.invoke chatgpt.tokens.use.direct')
        self.database = self.auth / 'state/openclaw.sqlite'
        with closing(sqlite3.connect(self.database)) as db, db: db.execute('create table config_machine_state(state_key text primary key,value_json text)')
        self.seed({'profiles': {'main': self.profile}})
        self.run = self.enterContext(patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess(['synthetic helper'], 0, stdout=b'{"status":"ready"}', stderr=b'')))
    def seed(self, value):
        with closing(sqlite3.connect(self.database)) as db, db: db.execute('insert or replace into config_machine_state values(?,?)', ('authProfiles.store', json.dumps(value)))
    def failure(self, reason, **options):
        with self.assertRaises(runtime.CredentialFailure) as caught: runtime.credential(**options)
        self.assertEqual(caught.exception.reason, reason); self.assertNotIn('synthetic-access-token', str(caught.exception)); return caught.exception

    def test_helper_uses_one_private_host_lock_and_reads_the_single_verified_grant_without_writing_it(self):
        before = self.database.read_bytes(); connections = []; original_connect = sqlite3.connect
        def connect(*args, **options):
            self.assertTrue(args[0].endswith('?mode=ro')); self.assertTrue(options['uri']); connection = original_connect(*args, **options); connections.append(connection); return connection
        with patch.object(runtime.sqlite3, 'connect', side_effect=connect): self.assertEqual(runtime.credential(force_refresh=True), 'synthetic-access-token')
        self.assertEqual(len(connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError): connections[0].execute('select 1')
        command = self.run.call_args.args[0]; self.assertEqual(command[0], str(self.node)); self.assertTrue(command[1].endswith('/openclaw_auth.mjs')); self.assertEqual(command[2], str(self.database)); self.assertEqual(command[-1], '--force')
        self.assertEqual(self.run.call_args.kwargs['timeout'], 75); self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.auth.stat().st_mode & 0o777, 0o700); self.assertEqual((self.auth / 'refresh.lock').stat().st_mode & 0o777, 0o600)
        environment = self.run.call_args.kwargs['env']; self.assertEqual(environment['HOME'], str(self.auth)); self.assertEqual(environment['OPENCLAW_STATE_DIR'], str(self.auth / 'state'))
        for name in ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'OPENAI_API_KEY', 'HTTPS_PROXY']: self.assertNotIn(name, environment)

    def test_helper_status_and_backoff_fields_are_validated_without_trusting_arbitrary_stdout(self):
        for state in [[], None, False, {'status': 'unavailable', 'reason': 'refresh-backoff', 'retry_after_seconds': True}, {'status': 'unavailable', 'reason': 'refresh-backoff', 'retry_after_seconds': 901}, {'status': 'unknown', 'reason': 'grant-invalid', 'retry_after_seconds': 0}, {'status': 'unavailable', 'reason': 'synthetic-secret', 'retry_after_seconds': 0}]:
            self.run.return_value.stdout = canonical(state)
            with self.subTest(state=state): self.failure('refresh-unconfirmed')
        self.run.return_value.stdout = b'{"status":"auth-required","reason":"grant-invalid","retry_after_seconds":0}'
        failure = self.failure('grant-invalid'); self.assertEqual(failure.status, 'auth-required'); self.assertEqual(failure.retry_after_seconds, 0)
        self.run.return_value.stdout = b'{"status":"unavailable","reason":"refresh-backoff","retry_after_seconds":900}'
        failure = self.failure('refresh-backoff'); self.assertEqual(failure.retry_after_seconds, 900)

    def test_malformed_profile_containers_are_typed_configuration_failures_not_uncaught_attribute_errors(self):
        for document in [[], {'profiles': []}, {'profiles': {'main': None}}, {'profiles': {'main': dict(self.profile, grantedScope=['resource.invoke'])}}]:
            self.seed(document)
            with self.subTest(document=document): self.failure('client-configuration')
        for change in [dict(type='api-key'), dict(provider='other'), dict(authFlow='other'), dict(issuer='https://untrusted.example.test'), dict(grantedScope='resource.invoke'), dict(access=None), dict(access='sk-synthetic-forbidden-key')]:
            self.seed({'profiles': {'main': dict(self.profile, **change)}})
            with self.subTest(change=change): self.failure('client-configuration')
        self.seed({'profiles': {'first': self.profile, 'second': self.profile}}); self.failure('client-configuration')
        with closing(sqlite3.connect(self.database)) as db, db: db.execute('delete from config_machine_state')
        self.failure('client-configuration')

    def test_expired_sqlite_and_filesystem_state_never_start_an_inference_or_return_a_partial_credential(self):
        self.seed({'profiles': {'main': dict(self.profile, expires=1_030_000)}})
        failure = self.failure('refresh-backoff'); self.assertEqual(failure.retry_after_seconds, 1)
        self.database.write_bytes(b'Original malformed SQLite bytes, never delete automatically')
        self.failure('storage-unavailable'); self.assertEqual(self.database.read_bytes(), b'Original malformed SQLite bytes, never delete automatically')
        with patch.object(Path, 'mkdir', side_effect=PermissionError('Synthetic private path denial')): self.failure('storage-unavailable')
        self.run.side_effect = subprocess.TimeoutExpired(['synthetic helper'], 75); self.failure('refresh-unconfirmed')

    def test_runtime_versions_are_exact_and_no_other_binary_or_package_is_accepted(self):
        (self.package / 'package.json').write_bytes(b'{"version":"2026.10.5"}')
        with patch.object(runtime.subprocess, 'check_output', return_value='v24.16.0\n') as version: runtime.verify_runtime()
        version.assert_called_once_with([str(self.node), '--version'], text=True)
        for node, package in [('v22.0.0', '2026.10.5'), ('v24.16.0', 'other-version')]:
            (self.package / 'package.json').write_text(json.dumps({'version': package}))
            with self.subTest(node=node, package=package), patch.object(runtime.subprocess, 'check_output', return_value=node), self.assertRaisesRegex(ValueError, 'Pinned OpenClaw/Node'): runtime.verify_runtime()


class TransportBoundary(unittest.TestCase):
    def test_only_the_fixed_catalog_and_response_endpoints_receive_a_bearer_and_redirects_are_disabled(self):
        context = MagicMock(); opener = MagicMock(); response = Response(b'original response'); opener.open.return_value = response
        with patch.object(runtime.ssl, 'create_default_context', return_value=context) as tls, patch.object(runtime.urllib.request, 'build_opener', return_value=opener):
            for method, url, accept in [('GET', 'https://api.openai.com/v1/models', 'application/json'), ('POST', 'https://api.openai.com/v1/responses', 'text/event-stream')]:
                self.assertIs(runtime.http_request(method, url, 'synthetic-header-token', b'{"original":true}', 60), response)
                request = opener.open.call_args.args[0]; self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-header-token'); self.assertEqual(request.get_header('Accept'), accept); self.assertEqual(request.data, b'{"original":true}'); self.assertNotIn('synthetic-header-token', request.full_url)
                self.assertEqual(opener.open.call_args.kwargs, {'timeout': 60})
            self.assertIsNone(context.keylog_filename); self.assertIn('cafile', tls.call_args.kwargs)
            for method, url in [('GET', 'https://unrelated.example.test'), ('DELETE', 'https://api.openai.com/v1/models')]:
                with self.subTest(method=method, url=url), self.assertRaisesRegex(ValueError, 'Unexpected subscription endpoint'): runtime.http_request(method, url, 'synthetic')
            for timeout in [0, 66]:
                with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, 'outside its bound'): runtime.http_request('GET', 'https://api.openai.com/v1/models', 'synthetic', timeout_seconds=timeout)
            error = urllib.error.HTTPError('https://api.openai.com/v1/models', 401, 'Unauthorized', {}, None); opener.open.side_effect = error
            self.assertIs(runtime.http_request('GET', 'https://api.openai.com/v1/models', 'synthetic'), error)
        with self.assertRaisesRegex(ValueError, 'redirects are disabled'): runtime.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_catalog_requires_the_exact_visible_model_and_a_bounded_original_response(self):
        raw = canonical({'models': [{'slug': runtime.MODEL, 'visibility': 'list'}]})
        with patch.object(runtime, 'http_request', return_value=Response(raw)) as request: result = runtime.catalog('synthetic-header-token')
        self.assertEqual(result, {'model_id': runtime.MODEL, 'catalog_sha256': digest(raw)}); request.assert_called_once_with('GET', 'https://api.openai.com/v1/models', 'synthetic-header-token')
        for response in [Response(b'{}', 403), Response(b'x' * 1_048_577), Response(canonical({'models': []})), Response(canonical({'models': [{'slug': runtime.MODEL, 'visibility': 'hidden'}]})), Response(canonical({'models': [{'slug': 'other-model', 'visibility': 'list'}]}))]:
            with self.subTest(status=response.status), patch.object(runtime, 'http_request', return_value=response), self.assertRaisesRegex(ValueError, 'exact model is unavailable'): runtime.catalog('synthetic')


if __name__ == '__main__': unittest.main()
