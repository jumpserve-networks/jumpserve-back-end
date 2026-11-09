"""Deterministic intake scheduling with synthetic futures and no native/provider calls."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import intake_worker as worker
import store
from workflow import canonical, digest


class StopLoop(BaseException):
    pass


class Future:
    def __init__(self, result=None, error=None, pending=0):
        self.value, self.error, self.pending = result, error, pending
    def done(self):
        if self.pending:
            self.pending -= 1
            return False
        return True
    def result(self):
        if self.error:
            raise self.error
        return self.value


class IntakeLoop(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='jumpserve-intake-loop-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'intake_worker.py'; self.source.write_bytes(b'# synthetic pinned runtime\n')
        files = {'intake_worker.py': digest(self.source.read_bytes())}
        self.manifest = dict(version=worker.VERSION, files=files, source_binding=digest(canonical(files)))
        self.write_manifest()
        self.home = self.root / 'home'
        self.enterContext(patch.dict(os.environ, {'INTAKE_HOME': str(self.home)}))
        self.enterContext(patch.object(worker, '__file__', str(self.source)))
        self.verify = self.enterContext(patch.object(worker.runtime, 'verify_runtime'))
        self.umask = self.enterContext(patch.object(worker.os, 'umask'))
        self.gateway = MagicMock(); self.gateway.process.poll.return_value = None
        self.gateway_factory = self.enterContext(patch.object(worker, 'WarmGateway', return_value=self.gateway))
        self.availability = MagicMock(); self.availability.check.return_value = {'status': 'ready'}
        self.enterContext(patch.object(worker, 'WorkerAvailability', return_value=self.availability))
        self.rpc = self.enterContext(patch.object(store, 'rpc'))

    def write_manifest(self):
        (self.root / 'intake-manifest.json').write_bytes(canonical(self.manifest))

    def loop(self, *, jobs=(), iterations=12, heartbeat_failure=False, claim_failure=False, native_exit=False, clock_step=10):
        clock = [0]; sleeps = [0]; pending_jobs = list(jobs)
        heartbeat_failed = [False]; claim_failed = [False]
        pools = []
        self.operations = []
        def dispatch(name, document):
            self.assertEqual(name, 'research_intake_dispatch')
            operation = document['p_operation']; self.operations.append(document)
            if operation == 'heartbeat' and heartbeat_failure and not heartbeat_failed[0]:
                heartbeat_failed[0] = True; raise OSError('Synthetic heartbeat transport failure')
            if operation == 'claim':
                if claim_failure and not claim_failed[0]:
                    claim_failed[0] = True; raise OSError('Synthetic claim transport failure')
                return pending_jobs.pop(0) if pending_jobs else None
            return {}
        self.rpc.side_effect = dispatch
        def sleep(seconds):
            self.assertEqual(seconds, 1)
            sleeps[0] += 1; clock[0] += clock_step
            if sleeps[0] >= iterations:
                raise StopLoop()
        class Pool:
            def __init__(self, max_workers):
                self.workers = max_workers; self.calls = []; pools.append(self)
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def submit(self, function, *args):
                self.calls.append((function, args))
                try:
                    value = function(*args)
                    return Future(result=value, pending=1 if len(self.calls) == 1 else 0)
                except Exception as error:
                    return Future(error=error)
        controller = MagicMock(side_effect=[{'status': 'idle'}, OSError('Synthetic control error'), {'status': 'idle'}, {'status': 'working'}] + [{'status': 'idle'}] * 100)
        recovery = MagicMock(side_effect=[OSError('Original outbox still unavailable')] + [None] * 20)
        output = io.StringIO()
        with patch.object(worker, 'ThreadPoolExecutor', Pool), patch.object(worker.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(worker.time, 'sleep', side_effect=sleep), patch.object(worker.intake_parallel, 'controller_slot', controller), \
                patch.object(worker.runtime, 'retry_completions', recovery), contextlib.redirect_stdout(output):
            if native_exit:
                self.gateway.process.poll.return_value = 1
                with self.assertRaisesRegex(ValueError, 'retain all running calls'):
                    worker.worker_loop()
            else:
                with self.assertRaises(StopLoop): worker.worker_loop()
        return pools, output.getvalue(), recovery, controller

    def test_loop_preserves_completed_calls_reconciles_outbox_and_never_replays_existing_spool_or_builders(self):
        jobs = [dict(id=str(uuid.uuid4()), request_raw=json.dumps({'fixture': True})),
                dict(id=str(uuid.uuid4()), request_raw=json.dumps({'builder': True})),
                dict(id=str(uuid.uuid4()), request_raw=json.dumps({'fixture': True})),
                dict(id=str(uuid.uuid4()), request_raw=json.dumps({'fixture': True}))]
        existing = self.home / 'spool' / jobs[2]['id']; existing.mkdir(parents=True)
        completed = []
        def execute(logical, directory, **kwargs):
            self.assertEqual(logical, {'fixture': True})
            self.assertIs(kwargs['token_source'], worker.access)
            self.assertEqual(kwargs['native_runner'], self.gateway.run)
            directory.mkdir(); completed.append(directory)
            return {'retained': True}, {'status': 'complete'}
        markers = [MagicMock(), MagicMock()]
        with patch.object(worker.runtime, 'execute', side_effect=execute) as inference, \
                patch.object(worker.runtime, 'save_completion', side_effect=markers) as save, \
                patch.object(worker.runtime, 'persist_completed', side_effect=[True, False]) as persist:
            pools, output, recovery, controller = self.loop(jobs=jobs)
        self.verify.assert_called_once(); self.umask.assert_called_once_with(0o077)
        self.assertEqual([pool.workers for pool in pools], [2, 4, 1])
        self.assertEqual(inference.call_count, 2)
        self.assertEqual(persist.call_count, 2)
        markers[0].unlink.assert_called_once(); markers[1].unlink.assert_not_called()
        self.assertEqual(self.availability.observe.call_count, 2)
        receipt = save.call_args.args[3]
        self.assertEqual(receipt['intake_runtime']['version'], worker.VERSION)
        self.assertTrue(receipt['intake_runtime']['warm_gateway'])
        uuid.UUID(receipt['intake_runtime']['worker_id'])
        self.assertEqual(save.call_args.args[-1], self.home / 'spool/pending-completions')
        self.assertEqual(output.count('intake-operation-unconfirmed'), 2)
        self.assertNotIn('Synthetic control error', output)
        self.assertGreaterEqual(recovery.call_count, 2)
        self.assertGreaterEqual(controller.call_count, 4)
        self.gateway.close.assert_called_once()
        heartbeats = [row['p_payload'] for row in self.operations if row['p_operation'] == 'heartbeat']
        self.assertTrue(all(row['configuration']['refresh_owner'] is False for row in heartbeats))

    def test_auth_pause_heartbeat_failure_and_claim_failure_remain_visible_without_inference(self):
        for mode in ('auth', 'heartbeat', 'claim'):
            self.operations = []; self.gateway.close.reset_mock(); self.rpc.reset_mock()
            self.availability.check.return_value = {'status': 'auth-required' if mode == 'auth' else 'ready'}
            with self.subTest(mode=mode), patch.object(worker.runtime, 'execute') as execute:
                _, _, _, _ = self.loop(iterations=5, heartbeat_failure=mode == 'heartbeat', claim_failure=mode == 'claim')
                execute.assert_not_called(); self.gateway.close.assert_called_once()
            if mode == 'auth':
                heartbeats = [row['p_payload'] for row in self.operations if row['p_operation'] == 'heartbeat']
                self.assertTrue(all(row['status'] == 'paused-auth' for row in heartbeats))
                self.assertFalse(any(row['p_operation'] == 'claim' for row in self.operations))

    def test_native_gateway_exit_closes_resources_without_another_claim(self):
        self.loop(native_exit=True)
        self.rpc.assert_not_called()
        self.gateway.close.assert_called_once()

    def test_idle_controller_polling_waits_until_its_recorded_backoff(self):
        pools,_,_,controller=self.loop(iterations=20,clock_step=1)
        self.assertTrue(controller.called);self.assertLess(controller.call_count,20*4);self.assertEqual(sorted(pool.workers for pool in pools),[1,2,4])
        self.assertFalse(next(pool for pool in pools if pool.workers==2).calls)

    def test_manifest_version_binding_and_source_bytes_are_verified_before_startup(self):
        original = json.loads(canonical(self.manifest))
        for change, message in [({'version': 'unqualified'}, 'source binding differs'),
                                ({'source_binding': '0' * 64}, 'source binding differs'),
                                ({'files': {'intake_worker.py': '0' * 64}}, 'source differs'),
                                ({'files': {'../escape.py': '0' * 64}}, 'source differs'),
                                ({'files': {str(self.root / 'absolute.py'): '0' * 64}}, 'source differs')]:
            self.manifest = dict(original, **change)
            if 'files' in change:
                self.manifest['source_binding'] = digest(canonical(self.manifest['files']))
            self.write_manifest()
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                worker.worker_loop()
        self.gateway_factory.assert_not_called()
        self.manifest = original
        link = self.root / 'alias.py'; link.symlink_to(self.source)
        self.manifest['files'] = {'alias.py': digest(self.source.read_bytes())}
        self.manifest['source_binding'] = digest(canonical(self.manifest['files']))
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'source differs'): worker.worker_loop()

    def test_publisher_loop_reports_only_typed_failure_and_never_private_diagnostics(self):
        sleeps = [0]
        def sleep(seconds):
            self.assertEqual(seconds, 30); sleeps[0] += 1
            if sleeps[0] == 2: raise StopLoop()
        with patch.object(sys, 'argv', ['intake_worker.py', '--publish-access']), \
                patch.object(worker, 'publish_access', side_effect=[{'status': 'ready'}, OSError('private synthetic diagnostic')]), \
                patch.object(worker.time, 'sleep', side_effect=sleep), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(StopLoop): worker.main()
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(rows, [{'status': 'ready'}, {'status': 'access-publication-unconfirmed', 'error_type': 'OSError'}])
        self.assertNotIn('private synthetic diagnostic', output.getvalue())
        with patch.object(sys, 'argv', ['intake_worker.py']), patch.object(worker, 'worker_loop') as loop:
            worker.main(); loop.assert_called_once()

    def test_real_entrypoint_help_does_not_start_an_intake_service(self):
        original = Path(__file__).resolve().parents[1] / 'intake_worker.py'
        with patch.object(sys, 'argv', ['intake_worker.py', '--help']), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            runpy.run_path(str(original), run_name='__main__')
        self.assertEqual(error.exception.code, 0)
        self.gateway_factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
