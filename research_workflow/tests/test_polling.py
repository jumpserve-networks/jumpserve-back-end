"""Virtual-clock integration checks: less polling, same original admission."""
import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_dispatch as dispatch
import openclaw_runtime as runtime
from polling import Backoff, wait
from workflow import canonical, digest


class StopLoop(BaseException):
    pass


class Clock:
    def __init__(self, duration=60):
        self.at = 1000
        self.end = self.at + duration

    def monotonic(self):
        return self.at

    def sleep(self, delay):
        if delay <= 0:
            raise AssertionError('A loop must yield.')
        self.at += delay
        if self.at >= self.end:
            raise StopLoop()


class Pool:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def submit(self, fn, *args):
        future = concurrent.futures.Future()
        if fn.__name__ == 'recover':
            future.set_result(None)
        # Claimed inference is deliberately never executed by these fixtures.
        return future


class PollingTests(unittest.TestCase):
    def worker(self, replies=None, outage=False, duration=60):
        clock = Clock(duration)
        calls = []
        replies = iter(replies or [])

        def rpc(operation, payload):
            calls.append((operation, clock.at))
            if outage:
                raise TimeoutError('Synthetic database outage')
            if operation == 'claim':
                return next(replies, None)
            return {}

        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {
            'OPENCLAW_SPOOL': temp, 'RESEARCH_BUILDER_ENABLED': 'false',
        }), patch.object(runtime, 'verify_runtime'), patch.object(runtime, 'ThreadPoolExecutor', Pool), \
                patch('openclaw_health.WorkerAvailability') as availability, \
                patch.object(dispatch, 'rpc', side_effect=rpc), \
                patch.object(runtime.time, 'monotonic', side_effect=clock.monotonic), \
                patch.object(runtime.time, 'sleep', side_effect=clock.sleep), patch('builtins.print'):
            availability.return_value.check.return_value = {'status': 'ready', 'reason': 'ready', 'retry_after_seconds': 0}
            with self.assertRaises(StopLoop):
                runtime.worker_loop()
        return calls

    def test_idle_claims_back_off_but_heartbeats_stay_within_freshness_window(self):
        calls = self.worker()
        claims = [at for operation, at in calls if operation == 'claim']
        health = [at for operation, at in calls if operation == 'heartbeat']
        self.assertLessEqual(len(claims), 10)
        self.assertEqual(len(health), 6)
        self.assertTrue(all(b - a == 10 for a, b in zip(health, health[1:])))
        self.assertLessEqual(max(b - a for a, b in zip(claims, claims[1:])), 10)

    def test_claimed_work_fills_two_slots_immediately_without_waiting_for_idle_backoff(self):
        calls = self.worker([{'id': 'original-first'}, {'id': 'original-second'}])
        self.assertEqual([at for operation, at in calls if operation == 'claim'], [1000, 1001])

    def test_database_outage_slows_new_polls_without_invoking_any_inference(self):
        calls = self.worker(outage=True)
        self.assertEqual([operation for operation, at in calls], ['heartbeat'] * 5)
        self.assertEqual([at for operation, at in calls], [1000, 1002, 1006, 1014, 1030])

    def test_slow_result_polls_keep_one_original_enqueue(self):
        clock = Clock(100)
        task = dict(id=str(uuid.uuid4()), study_id=str(uuid.uuid4()))
        turn = str(uuid.uuid4())
        raw = canonical(dict(output={}, usage={}))
        complete = dict(status='complete', response_artifact_id=str(uuid.uuid4()), response_sha256=digest(raw), provenance={})
        calls = []

        def rpc(operation, payload):
            calls.append((operation, clock.at, payload))
            if operation == 'enqueue':
                return dict(replayed=False)
            return complete if clock.at >= 1020 else dict(status='running')

        with patch.object(dispatch, 'rpc', side_effect=rpc), patch.object(dispatch.store, 'artifact_bytes', return_value=raw), \
                patch.object(dispatch.time, 'monotonic', side_effect=clock.monotonic), \
                patch.object(dispatch.time, 'sleep', side_effect=clock.sleep):
            self.assertEqual(dispatch.invoke(task, turn, dict(modelId=dispatch.MODEL))['output'], {})
        self.assertEqual(sum(op == 'enqueue' for op, at, payload in calls), 1)
        reads = [(at, payload) for op, at, payload in calls if op == 'result']
        self.assertLessEqual(len(reads), 7)
        self.assertTrue(all(payload == dict(id=turn, study_id=task['study_id']) for at, payload in reads))

    def test_poll_sleep_cannot_extend_the_original_unconfirmed_deadline(self):
        clock = Clock(100)
        with patch.object(dispatch, 'WAIT_SECONDS', 7), \
                patch.object(dispatch, 'rpc', side_effect=lambda op, p: dict(replayed=False) if op == 'enqueue' else dict(status='running')) as rpc, \
                patch.object(dispatch.time, 'monotonic', side_effect=clock.monotonic), \
                patch.object(dispatch.time, 'sleep', side_effect=clock.sleep):
            with self.assertRaisesRegex(ValueError, 'do not retry'):
                dispatch.invoke(dict(id=str(uuid.uuid4()), study_id=str(uuid.uuid4())), str(uuid.uuid4()), dict(modelId=dispatch.MODEL))
        self.assertEqual(clock.at, 1007)
        self.assertEqual(sum(call.args[0] == 'enqueue' for call in rpc.call_args_list), 1)

    def test_backoff_is_bounded_and_resettable(self):
        backoff = Backoff(2, 5)
        self.assertEqual([backoff.next_delay() for _ in range(5)], [2, 4, 5, 5, 5])
        backoff.reset()
        self.assertEqual(backoff.next_delay(), 2)
        with patch('polling.time.monotonic', return_value=100), patch('polling.time.sleep') as sleep:
            wait(10, 102)
            sleep.assert_called_once_with(2)


if __name__ == '__main__':
    unittest.main()
