"""Warm intake transport and access publication, with all native/HTTP I/O synthetic."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import intake_worker as worker
import openclaw_runtime as runtime
from workflow import canonical


class Response:
    def __init__(self, body=b'', status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.body[:limit]


class WarmIntakeGateway(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='jumpserve-intake-gateway-unit-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.server = MagicMock(server_address=('127.0.0.1', 12345))
        self.process = MagicMock()
        self.process.poll.return_value = None
        self.opener = MagicMock()
        self.opener.open.return_value = Response()
        self.native_status = 0
        self.readback_change = None
        self.handler = None
        self.enterContext(patch.object(worker, 'ThreadingHTTPServer', side_effect=self.make_server))
        self.thread = self.enterContext(patch.object(worker.threading, 'Thread'))
        self.enterContext(patch.object(runtime, 'environment', return_value={'SYNTHETIC': 'unit-fixture'}))
        self.enterContext(patch.object(worker.urllib.request, 'build_opener', return_value=self.opener))
        self.native = self.enterContext(patch.object(worker.subprocess, 'run', side_effect=self.configure))
        self.launch = self.enterContext(patch.object(worker.subprocess, 'Popen', return_value=self.process))
        self.enterContext(patch.object(worker.time, 'sleep'))
        self.clock = self.enterContext(patch.object(worker.time, 'monotonic', return_value=0))

    def make_server(self, address, handler):
        self.assertEqual(address, ('127.0.0.1', 0))
        self.handler = handler
        return self.server

    def configure(self, args, **kwargs):
        config = json.loads(kwargs['input'])
        if self.readback_change:
            config.update(self.readback_change)
        (self.home / 'openclaw.json').write_bytes(canonical(config))
        return SimpleNamespace(returncode=self.native_status, stdout=b'synthetic native diagnostic', stderr=b'')

    def gateway(self):
        value = worker.WarmGateway(self.home)
        self.addCleanup(lambda: value.close() if not value.log.closed else None)
        return value

    def turn(self, base='http://127.0.0.1:4100/v1'):
        folder = self.home / str(uuid.uuid4()); folder.mkdir()
        config = folder / 'original.json'
        config.write_bytes(canonical({'models': {'providers': {'lab': {'baseUrl': base, 'apiKey': 'synthetic-original-bridge-key'}}}}))
        message = folder / 'message.txt'; message.write_text('Original source-grounded request')
        return folder, ['native', '--config', str(config), '--message-file', str(message)]

    def proxy(self, gateway, raw, *, path='/v1/chat/completions', authorization=None, declared=None):
        # Instantiate only the handler method; no socket or HTTP server is opened.
        handler = object.__new__(self.handler)
        handler.path = path
        handler.headers = {'Authorization': authorization if authorization is not None else 'Bearer ' + gateway.bridge_token,
                           'Content-Length': str(len(raw) if declared is None else declared)}
        handler.rfile = io.BytesIO(raw); handler.wfile = io.BytesIO()
        handler.send_response = MagicMock(); handler.send_header = MagicMock()
        handler.end_headers = MagicMock(); handler.send_error = MagicMock()
        handler.do_POST()
        return handler

    def test_gateway_configures_a_private_data_only_native_process_and_verifies_readiness(self):
        gateway = self.gateway()
        self.assertTrue(gateway.verified)
        self.assertEqual(json.loads((self.home / 'openclaw.json').read_bytes())['tools']['deny'], ['*'])
        self.assertEqual((self.home / 'configuration-diagnostics.bin').read_bytes(), b'synthetic native diagnostic')
        self.assertEqual(self.native.call_args.args[0][-3:], ['--stdin', '--replace-path', 'plugins'])
        self.assertEqual(self.launch.call_args.args[0][-4:], ['--port', str(worker.PORT), '--bind', 'loopback'])
        self.assertEqual(self.launch.call_args.kwargs['env']['SYNTHETIC'], 'unit-fixture')
        self.assertEqual(self.thread.call_args.kwargs['daemon'], True)
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Authorization'), 'Bearer ' + gateway.token)
        self.assertEqual(self.opener.open.call_args.kwargs['timeout'], 2)

    def test_native_configuration_failure_or_changed_readback_prevents_gateway_launch(self):
        self.native_status = 2
        with self.assertRaisesRegex(ValueError, 'configuration failed'):
            worker.WarmGateway(self.home)
        self.launch.assert_not_called()
        self.native_status = 0
        for key in ('gateway', 'tools', 'models', 'plugins'):
            self.readback_change = {key: {'unqualified': True}}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'readback differs'):
                worker.WarmGateway(self.home)
        self.launch.assert_not_called()

    def test_gateway_retries_readiness_errors_without_an_inference_call(self):
        self.opener.open.side_effect = [OSError('starting'), Response(status=503), Response(status=200)]
        gateway = self.gateway()
        self.assertTrue(gateway.verified)
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertTrue(all(call.args[0].full_url.endswith('/v1/models') for call in self.opener.open.call_args_list))

    def test_startup_exit_and_readiness_timeout_are_unconfirmed_failures(self):
        allocated = []
        class Captured(worker.WarmGateway):
            def __new__(cls, *args):
                value = super().__new__(cls); allocated.append(value); return value
        self.process.poll.return_value = 1
        try:
            with self.assertRaisesRegex(ValueError, 'exited during startup'):
                Captured(self.home)
            self.process.poll.return_value = None
            self.clock.side_effect = [0, 151]
            with self.assertRaisesRegex(ValueError, 'did not become ready'):
                Captured(self.home)
        finally:
            for gateway in allocated:
                if hasattr(gateway, 'log'): gateway.log.close()

    def test_turn_preserves_request_response_and_uses_one_temporary_original_route(self):
        gateway = self.gateway()
        folder, args = self.turn()
        body = canonical({'choices': [{'message': {'content': 'retained exact answer'}}]})
        def open(request, **kwargs):
            self.assertEqual(len(gateway.routes), 1)
            ticket, (target, token) = next(iter(gateway.routes.items()))
            self.assertEqual((target, token), ('http://127.0.0.1:4100/v1', 'synthetic-original-bridge-key'))
            payload = json.loads(request.data)
            self.assertEqual(payload['user'], 'intake-' + ticket)
            self.assertEqual(payload['messages'][0]['content'], 'Original source-grounded request\nwarm-intake-ticket:' + ticket)
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + gateway.token)
            self.assertEqual(kwargs['timeout'], runtime.DATA_PROCESS_SECONDS)
            return Response(body)
        self.opener.open.side_effect = open
        result = gateway.run(args)
        self.assertEqual(json.loads(result.stdout), {'payloads': [{'text': 'retained exact answer'}]})
        self.assertEqual(result.returncode, 0); self.assertEqual(result.stderr, b'')
        self.assertEqual((folder / 'warm-gateway-response.bin').read_bytes(), body)
        self.assertFalse(json.loads((folder / 'warm-gateway-request.json').read_bytes())['stream'])
        self.assertEqual(gateway.routes, {})

    def test_turn_rejects_nonlocal_originals_and_cleans_routes_after_unconfirmed_results(self):
        gateway = self.gateway()
        _, args = self.turn('https://remote.example.test/v1')
        before = self.opener.open.call_count
        with self.assertRaisesRegex(ValueError, 'Local original bridge required'):
            gateway.run(args)
        self.assertEqual(self.opener.open.call_count, before)
        for body, error in [(b'x' * 150001, ValueError), (b'{}', KeyError)]:
            folder, args = self.turn()
            self.opener.open.side_effect = None
            self.opener.open.return_value = Response(body)
            with self.subTest(length=len(body)), self.assertRaises(error):
                gateway.run(args)
            self.assertEqual((folder / 'warm-gateway-response.bin').read_bytes(), body)
            self.assertEqual(gateway.routes, {})
        _, args = self.turn()
        self.opener.open.side_effect = OSError('unconfirmed transport')
        with self.assertRaisesRegex(OSError, 'unconfirmed transport'):
            gateway.run(args)
        self.assertEqual(gateway.routes, {})

    def test_proxy_relays_exact_bytes_only_for_one_registered_ticket(self):
        gateway = self.gateway()
        ticket = str(uuid.uuid4())
        gateway.routes[ticket] = ('http://127.0.0.1:4100/v1', 'synthetic-turn-token')
        original = canonical({'message': 'warm-intake-ticket:' + ticket})
        answer = b'{"retained":"exact native bytes"}'
        self.opener.open.return_value = Response(answer)
        handler = self.proxy(gateway, original)
        handler.send_response.assert_called_once_with(200)
        handler.send_error.assert_not_called()
        self.assertEqual(handler.wfile.getvalue(), answer)
        forwarded = self.opener.open.call_args.args[0]
        self.assertEqual(forwarded.full_url, 'http://127.0.0.1:4100/v1/chat/completions')
        self.assertEqual(forwarded.data, original)
        self.assertEqual(forwarded.get_header('Authorization'), 'Bearer synthetic-turn-token')
        handler.log_message('synthetic diagnostic')

    def test_proxy_rejects_untrusted_incomplete_and_ambiguous_requests_without_forwarding(self):
        gateway = self.gateway()
        ticket = str(uuid.uuid4()); raw = ('warm-intake-ticket:' + ticket).encode()
        cases = [(raw, {'path': '/other'}), (raw, {'authorization': 'Bearer wrong'}), (b'', {}),
                 (raw, {'declared': 1048577}), (raw, {'declared': len(raw) + 1}), (b'no ticket', {}),
                 (raw + (' warm-intake-ticket:' + str(uuid.uuid4())).encode(), {}), (raw, {})]
        for body, change in cases:
            before = self.opener.open.call_count
            handler = self.proxy(gateway, body, **change)
            handler.send_error.assert_called_once_with(409, 'Original intake operation unconfirmed; no provider replay.')
            handler.send_response.assert_not_called()
            self.assertEqual(self.opener.open.call_count, before)

    def test_proxy_bounds_original_response_and_rejects_transport_errors(self):
        gateway = self.gateway(); ticket = str(uuid.uuid4())
        gateway.routes[ticket] = ('http://127.0.0.1:4100/v1', 'synthetic-turn-token')
        raw = ('warm-intake-ticket:' + ticket).encode()
        self.opener.open.return_value = Response(b'x' * 150001)
        handler = self.proxy(gateway, raw)
        handler.send_error.assert_called_once()
        self.assertEqual(handler.wfile.getvalue(), b'')
        self.opener.open.side_effect = OSError('private transport diagnostic')
        handler = self.proxy(gateway, raw)
        handler.send_error.assert_called_once_with(409, 'Original intake operation unconfirmed; no provider replay.')

    def test_close_terminates_then_kills_only_if_the_native_process_does_not_stop(self):
        gateway = self.gateway()
        self.process.wait.side_effect = [subprocess.TimeoutExpired('synthetic native', 90), None]
        gateway.close()
        self.process.terminate.assert_called_once(); self.process.kill.assert_called_once()
        self.assertEqual([call.kwargs['timeout'] for call in self.process.wait.call_args_list], [90, 10])
        self.server.shutdown.assert_called_once(); self.server.server_close.assert_called_once()
        self.thread.return_value.join.assert_called_once_with(timeout=2)
        self.assertTrue(gateway.log.closed)


class IntakeAccess(unittest.TestCase):
    def tearDown(self): worker.ssm.cache_clear()

    def test_ssm_uses_only_an_instance_role_and_caches_the_scoped_client(self):
        import boto3
        session = MagicMock()
        session.get_credentials.return_value.method = 'env'
        worker.ssm.cache_clear()
        with patch.object(boto3, 'Session', return_value=session) as factory:
            with self.assertRaisesRegex(ValueError, 'instance role'):
                worker.ssm()
            session.get_credentials.return_value.method = 'iam-role'
            client = worker.ssm()
            self.assertIs(worker.ssm(), client)
            session.client.assert_called_once_with('ssm')
            factory.assert_called_with(region_name='us-east-1')

    def test_access_validates_version_type_bounds_and_expiry_without_returning_a_refresh_grant(self):
        good = dict(version=worker.VERSION, status='ready', issuer='https://auth.openai.com', access='synthetic-access', checked_at=1000, expires_at=2000)
        client = MagicMock()
        client.get_parameter.return_value = {'Parameter': {'Value': json.dumps(good)}}
        with patch.object(worker, 'ssm', return_value=client), patch.object(worker.time, 'time', return_value=1030):
            self.assertEqual(worker.access(), 'synthetic-access')
        for change in ({'version': 'other'}, {'access': None}, {'access': ''}, {'access': 'x' * 3501},
                       {'checked_at': True}, {'checked_at': '1000'}, {'expires_at': True}, {'expires_at': '2000'}):
            client.get_parameter.return_value = {'Parameter': {'Value': json.dumps(dict(good, **change))}}
            with self.subTest(change=list(change)), self.assertRaises(runtime.CredentialFailure):
                worker.access(client, now=1030)
        self.assertEqual(client.get_parameter.call_args.kwargs, {'Name': worker.PARAMETER, 'WithDecryption': True})

    def test_publisher_invalidates_on_owner_pause_rotation_or_oversized_access(self):
        with tempfile.TemporaryDirectory(prefix='jumpserve-intake-access-unit-') as directory:
            root = Path(directory); (root / 'state').mkdir()
            with contextlib.closing(sqlite3.connect(root / 'state/openclaw.sqlite')) as db, db:
                db.execute('create table config_machine_state(state_key text,value_json text)')
                db.execute('insert into config_machine_state values(?,?)', ('authProfiles.store', json.dumps({'profiles': {'owner': {'access': 'different', 'refresh': 'never publish', 'expires': 2000000}}})))
            client = MagicMock()
            health = MagicMock(); health.check.return_value = {'status': 'paused-quota'}
            with patch.object(runtime, 'AUTH', root), patch.object(worker, 'ssm', return_value=client), patch.object(worker, 'WorkerAvailability', return_value=health):
                self.assertEqual(worker.publish_access(), {'status': 'unavailable'})
            class Ready:
                def __init__(self, auth, spool, token_source): self.token_source = token_source
                def check(self): self.token_source(); return {'status': 'ready'}
            for token in ('synthetic-access', 'x' * 4100):
                with contextlib.closing(sqlite3.connect(root / 'state/openclaw.sqlite')) as db, db:
                    db.execute('update config_machine_state set value_json=?', (json.dumps({'profiles': {'owner': {'access': token if len(token) > 4000 else 'different', 'refresh': 'never publish', 'expires': 2000000}}}),))
                with patch.object(runtime, 'AUTH', root), patch.object(runtime, 'credential', return_value=token), patch.object(worker, 'WorkerAvailability', Ready):
                    self.assertEqual(worker.publish_access(client), {'status': 'unavailable'})
            for call in client.put_parameter.call_args_list:
                value = json.loads(call.kwargs['Value'])
                self.assertNotIn('access', value); self.assertNotIn('refresh', value)
                self.assertNotIn('never publish', call.kwargs['Value'])


if __name__ == '__main__':
    unittest.main()
