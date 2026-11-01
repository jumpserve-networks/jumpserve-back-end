"""Claims remain prompt while optional maintenance and outages poll less."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from platform_supervisor import worker


class StopLoop(BaseException):
    pass


class WorkerPolling(unittest.TestCase):
    def exercise(self, outage=False):
        now = [1000]
        calls = []

        def sleep(delay):
            now[0] += delay
            if now[0] >= 1075:
                raise StopLoop()

        def rpc(operation, payload):
            calls.append((operation, now[0]))
            if outage:
                raise TimeoutError('Synthetic persistence outage')
            return None

        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', PLATFORM_SPOOL=temp), \
                patch('platform_supervisor.credentials.Credentials'), patch.object(worker, 'Store') as store, \
                patch.object(worker, 'GitHub'), patch.object(worker, 'Controller'), \
                patch.object(worker.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(worker.time, 'sleep', side_effect=sleep), patch('builtins.print'):
            store.return_value.rpc.side_effect = rpc
            with self.assertRaises(StopLoop):
                worker.main()
        return calls

    def test_maintenance_is_throttled_without_delaying_task_claims(self):
        calls = self.exercise()
        self.assertEqual([at for op, at in calls if op == 'claim'], [1000, 1015, 1030, 1045, 1060])
        self.assertEqual([at for op, at in calls if op == 'maintenance'], [1000, 1060])

    def test_outage_backoff_does_not_admit_or_replay_an_operation(self):
        self.assertEqual(self.exercise(True), [('heartbeat', 1000), ('heartbeat', 1015), ('heartbeat', 1045)])
