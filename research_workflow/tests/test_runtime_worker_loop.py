"""Virtual-clock worker/outbox regressions; no threads, providers or cloud calls."""
import concurrent.futures
import contextlib
import copy
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
import experiment_builder
import model_dispatch
import openclaw_runtime as runtime
from test_openclaw import decision
from test_polling import Clock, StopLoop
from workflow import canonical


class InlinePool:
    pools = []
    def __init__(self, *args, **options): self.options = options; self.submissions = []; self.pools.append(self)
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def submit(self, function, *args):
        self.submissions.append((function.__name__, args)); future = concurrent.futures.Future()
        try: future.set_result(function(*args))
        except Exception as error: future.set_exception(error)
        return future


class RuntimeWorkerLoop(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='runtime-loop-unit-'); self.addCleanup(temporary.cleanup)
        self.spool = Path(temporary.name); self.clock = Clock(12); self.health = dict(status='ready', reason='ready', retry_after_seconds=0)
        self.id = str(uuid.uuid4()); self.logical = {'synthetic_original_request': True, 'zero': 0, 'missing': None}
        self.job = dict(id=self.id, study_id=str(uuid.uuid4()), actor_id=str(uuid.uuid4()), request_raw=canonical(self.logical).decode(), lease_token='original-fence')
        self.receipt = dict(status='complete', provider_calls=1, paid_fallback=False, measured_charges_usd=None)
        self.calls = []; self.claims = [self.job]; self.outcome = True; self.execute_error = None
        self.enterContext(patch.dict(os.environ, OPENCLAW_SPOOL=str(self.spool), RESEARCH_BUILDER_ENABLED='false'))
        self.enterContext(patch.object(runtime, 'verify_runtime')); self.enterContext(patch.object(runtime, 'ThreadPoolExecutor', InlinePool)); InlinePool.pools = []
        self.enterContext(patch.object(runtime.time, 'monotonic', side_effect=self.clock.monotonic)); self.enterContext(patch.object(runtime.time, 'sleep', side_effect=self.clock.sleep)); self.enterContext(patch.object(runtime.os, 'umask'))
        self.availability = self.enterContext(patch('openclaw_health.WorkerAvailability')); self.availability.return_value.check.side_effect = lambda: copy.deepcopy(self.health)
        self.dispatch = self.enterContext(patch.object(model_dispatch, 'rpc', side_effect=self.rpc))
        self.execute = self.enterContext(patch.object(runtime, 'execute', side_effect=self.execute_turn))
        self.persist = self.enterContext(patch.object(runtime, 'persist_completed', side_effect=lambda *args: self.outcome))
        self.retry = self.enterContext(patch.object(runtime, 'retry_completions'))
    def rpc(self, operation, payload):
        self.calls.append((operation, copy.deepcopy(payload), self.clock.at))
        if operation == 'claim': return self.claims.pop(0) if self.claims else None
        return {}
    def execute_turn(self, logical, directory, *args):
        self.assertEqual(logical, self.logical); directory.mkdir(); (directory / 'logical.json').write_bytes(canonical(logical))
        if self.execute_error: raise self.execute_error
        return decision(), copy.deepcopy(self.receipt)
    def run_loop(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(StopLoop): runtime.worker_loop()
        return [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]

    def test_completed_controller_turn_keeps_durable_original_request_and_payload_then_removes_only_its_outbox_marker(self):
        original = copy.deepcopy(self.job); self.run_loop()
        self.execute.assert_called_once(); self.persist.assert_called_once_with(self.job, self.spool / self.id)
        self.assertEqual(self.job, original); completion = json.loads((self.spool / self.id / 'completion.json').read_bytes()); self.assertEqual(completion['answer'], decision()); self.assertEqual(completion['receipt'], self.receipt)
        self.assertFalse((self.spool / 'pending-completions' / (self.id + '.json')).exists()); self.assertEqual((self.spool / self.id / 'logical.json').read_bytes(), canonical(self.logical))
        self.availability.return_value.observe.assert_called_once_with(self.receipt)
        self.assertTrue(any(pool.options.get('thread_name_prefix') == 'retained-result-recovery' and pool.options['max_workers'] == 1 for pool in InlinePool.pools)); self.assertEqual(InlinePool.pools[0].options['max_workers'], 2)
        heartbeat = next(payload for operation, payload, _ in self.calls if operation == 'heartbeat'); self.assertFalse(heartbeat['configuration']['paid_fallback']); self.assertIsNone(heartbeat['configuration']['actual_cost_usd'])

    def test_unacknowledged_completion_keeps_the_exact_private_job_and_answer_without_replaying_the_provider(self):
        self.outcome = False; self.run_loop(); marker = self.spool / 'pending-completions' / (self.id + '.json')
        self.assertEqual(json.loads(marker.read_bytes()), self.job); self.assertEqual(marker.stat().st_mode & 0o777, 0o600); self.assertEqual(self.execute.call_count, 1)
        self.assertEqual(json.loads((self.spool / self.id / 'completion.json').read_bytes())['receipt']['provider_calls'], 1)

    def test_builder_turn_uses_its_own_executor_and_reaps_and_reconciles_retained_work_without_changing_the_request(self):
        self.logical['builder'] = {'original_checkpoint': 'retained-sha'}; self.job['request_raw'] = canonical(self.logical).decode()
        with patch.dict(os.environ, RESEARCH_BUILDER_ENABLED='true'), patch.object(experiment_builder, 'execute', side_effect=self.execute_turn) as builder, patch.object(experiment_builder, 'reap') as reap, patch('dispatch_recovery.reconcile_spool') as reconcile:
            self.run_loop()
        self.execute.assert_not_called(); builder.assert_called_once_with(self.logical, self.spool / self.id, self.job); reap.assert_called_once_with(self.spool); reconcile.assert_called_once_with(self.spool)

    def test_existing_spool_and_execution_errors_are_retained_and_logged_as_unconfirmed_without_replacement_calls(self):
        (self.spool / self.id).mkdir(); (self.spool / self.id / 'original.bin').write_bytes(b'Original unfinished outcome')
        output = self.run_loop(); self.execute.assert_not_called(); self.persist.assert_not_called(); self.assertTrue(any(record['status'] == 'unconfirmed' for record in output))
        self.assertEqual((self.spool / self.id / 'original.bin').read_bytes(), b'Original unfinished outcome')
        self.clock = Clock(12); self.claims = [dict(self.job, id=str(uuid.uuid4()))]; self.execute_error = RuntimeError('Synthetic private failure')
        with patch.object(runtime.time, 'monotonic', side_effect=self.clock.monotonic), patch.object(runtime.time, 'sleep', side_effect=self.clock.sleep): output = self.run_loop()
        self.assertTrue(any(record['error_type'] == 'RuntimeError' and record['status'] == 'unconfirmed' for record in output)); self.assertNotIn('Synthetic private failure', json.dumps(output)); self.persist.assert_not_called()

    def test_recovery_failure_backs_off_separately_and_newly_paused_admission_does_not_claim_more_work(self):
        self.claims = []; self.retry.side_effect = TimeoutError('Synthetic private recovery failure'); output = self.run_loop()
        self.assertTrue(any(record['status'] == 'reconciliation-pending' and record['retry_after_seconds'] == 30 for record in output)); self.assertEqual(self.retry.call_count, 1)
        self.clock = Clock(12); self.calls.clear(); self.retry.side_effect = None
        self.availability.return_value.check.side_effect = [self.health, dict(self.health, status='unavailable'), dict(self.health, status='unavailable')]
        with patch.object(runtime.time, 'monotonic', side_effect=self.clock.monotonic), patch.object(runtime.time, 'sleep', side_effect=self.clock.sleep): self.run_loop()
        self.assertFalse(any(operation == 'claim' for operation, _, _ in self.calls)); self.execute.assert_not_called()

    def test_cli_quota_resume_and_invalid_arguments_never_start_a_provider_or_worker(self):
        source = Path(runtime.__file__); self.availability.return_value.resume_quota.return_value = dict(provider_allowance_verified=False, status='ready')
        with patch.object(sys, 'argv', [str(source), '--resume-quota']), contextlib.redirect_stdout(io.StringIO()) as output: runpy.run_path(str(source), run_name='__main__')
        self.assertFalse(json.loads(output.getvalue())['provider_allowance_verified']); self.execute.assert_not_called()
        with patch.object(sys, 'argv', [str(source), '--unsupported']), self.assertRaisesRegex(SystemExit, 'Only --resume-quota'): runpy.run_path(str(source), run_name='__main__')

    def test_cold_worker_cli_keeps_the_same_idle_heartbeat_and_claim_contract(self):
        source = Path(runtime.__file__); package = self.spool / 'synthetic-package'; package.mkdir(); (package / 'package.json').write_text('{"version":"2026.10.5"}')
        self.claims = []
        with patch.dict(os.environ, OPENCLAW_PACKAGE=str(package)), patch.object(sys, 'argv', [str(source)]), patch('concurrent.futures.ThreadPoolExecutor', InlinePool), patch.object(runtime.subprocess, 'check_output', return_value='v24.16.0\n'), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(StopLoop): runpy.run_path(str(source), run_name='__main__')
        self.assertTrue(any(operation == 'heartbeat' for operation, _, _ in self.calls)); self.execute.assert_not_called()


if __name__ == '__main__': unittest.main()
