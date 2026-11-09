"""Host-local transports and disposable runtime commands with synthetic adapters."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bounded_network as tcp
import package_runtime as runtime


class BoundedTransportBoundaries(unittest.TestCase):
    def exchange(self, *, chunks=None, server_error=False, alive=False, expired=False):
        listener, server, client = (MagicMock() for _ in range(3))
        listener.getsockname.return_value = ('127.0.0.1', 54321)
        listener.accept.return_value = (server, ('127.0.0.1', 54322))
        for stream in [server, client]: stream.__enter__.return_value = stream
        server.recv.return_value = b'JJJJ'
        client.recv.side_effect = chunks or [b'JJJJ', b'JJJJ']
        if server_error: server.__exit__.side_effect = OSError('Synthetic server cleanup failed')
        threads = []
        def thread(*, target, daemon):
            self.assertTrue(daemon)
            value = types.SimpleNamespace(start=target, join=Mock(), is_alive=lambda: alive)
            threads.append(value)
            return value
        clock = Mock(side_effect=[0, *([4] * 20)]) if expired else Mock(return_value=0)
        with patch.object(tcp.socket, 'socket', side_effect=[listener, client]), \
                patch.object(tcp.threading, 'Thread', side_effect=thread), \
                patch.object(tcp.time, 'monotonic', clock), patch.object(tcp.time, 'monotonic_ns', side_effect=[0, 1000000, 2000000, 3000000]), patch.object(tcp.time, 'sleep'):
            result = tcp.run({'payload_bytes': 4, 'exchanges': 2, 'application_delay_ms': 0})
        listener.bind.assert_called_once_with(('127.0.0.1', 0))
        for stream in [listener, client]: stream.close.assert_called_once()
        if expired: server.close.assert_not_called()
        else: server.close.assert_called_once()
        self.assertEqual(threads[0].join.call_args.kwargs, {'timeout': .6})
        return result

    def test_eof_and_corrupt_echoes_never_become_zero_or_successful_measurements(self):
        for chunks in [[b''], [b'XXXX'], [b'JJJJ', b'']]:
            result = self.exchange(chunks=chunks)
            with self.subTest(chunks=chunks):
                self.assertIn('ValueError', result['provenance']['failure'])
                self.assertEqual(len(result['observations']), 2 if len(chunks) == 2 else 0)
                if len(chunks) == 2:
                    self.assertEqual(result['observations'][1]['metric'], 'received_bytes')
                    self.assertEqual(result['observations'][1]['value'], 4)
                    self.assertEqual(result['observations'][1]['observation_key'], '0')
                self.assertIsNone(result['usage']['measured_charges_usd'])

    def test_deadlines_and_unfinished_or_failed_server_cleanup_keep_the_original_failure(self):
        result = self.exchange(expired=True)
        self.assertIn('TimeoutError', result['provenance']['failure'])
        self.assertEqual(result['observations'], [])
        for options in [{'alive': True}, {'server_error': True}]:
            result = self.exchange(**options)
            with self.subTest(options=options):
                self.assertIn('ValueError', result['provenance']['failure'])
                self.assertEqual(len(result['observations']), 4)
                self.assertTrue(all(row['status'] == 'recorded' for row in result['observations']))

    def test_handler_requires_exact_invocation_and_enforces_transport_byte_bound(self):
        for value in [None, [], {}, {'version': 'future', 'config': {}}, {'version': tcp.VERSION, 'config': {}, 'extra': True}]:
            with self.subTest(value=value), patch.object(tcp, 'run') as run, self.assertRaisesRegex(ValueError, 'Exact bounded TCP'):
                tcp.handler(value)
            run.assert_not_called()
        with patch.object(tcp, 'run', return_value={'original': 'x' * 32001}), self.assertRaisesRegex(ValueError, 'transport limit'):
            tcp.handler({'version': tcp.VERSION, 'config': {}})


class ExperimentRuntimeBoundaries(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='experiment-runtime-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.process = Mock(returncode=0, pid=4242)
        self.process.poll.return_value = 0

    def write(self, name, config, runner='custom-package-v1'):
        directory = self.root / name
        directory.mkdir()
        spec = {'runner': runner, 'config': config, 'limits': {'max_output_bytes': 1000000, 'max_network_bytes': 10000}}
        (directory / 'spec.json').write_text(json.dumps(spec))
        return directory

    def test_builtin_host_preparation_capability_is_forwarded_only_for_the_exact_spec_hash(self):
        for index, granted in enumerate([False, True]):
            directory = self.write(str(index), {}, runner='netem-iperf-v1')
            expected = hashlib.sha256((directory / 'spec.json').read_bytes()).hexdigest()
            with patch.object(runtime.subprocess, 'Popen', return_value=self.process) as launch, \
                    patch.object(runtime, 'network_bytes', side_effect=[100, 150]), \
                    patch.dict(os.environ, {'JUMPSERVE_DEDICATED_EXPERIMENT': expected if granted else 'different-spec', 'AWS_SECRET_ACCESS_KEY': 'synthetic-private', 'SUPABASE_SERVICE_ROLE_KEY': 'synthetic-private'}):
                receipt = runtime.run(directory, 0)
            self.assertEqual(launch.call_args.args[0], ['python3', str(directory / 'netem_iperf.py')])
            environment = launch.call_args.kwargs['env']
            self.assertEqual(environment.get('JUMPSERVE_DEDICATED_EXPERIMENT'), expected if granted else None)
            self.assertNotIn('AWS_SECRET_ACCESS_KEY', environment)
            self.assertNotIn('SUPABASE_SERVICE_ROLE_KEY', environment)
            self.assertEqual(receipt['execution_kind'], 'builtin-netem-iperf')
            self.assertEqual(receipt['network_bytes'], 50)
            self.assertIsNone(receipt['measured_charges_usd'])

    def test_container_launch_has_declared_resource_bounds_and_removes_only_its_own_named_container(self):
        for mode in ['emulated', 'real-world']:
            directory = self.write(mode, {'mode': mode, 'package': {'kind': 'container', 'version': 'original-v1', 'image': 'public.ecr.aws/synthetic/test@sha256:' + 'a' * 64}, 'arguments': ['--synthetic', 'a; b']})
            with patch.object(runtime.subprocess, 'Popen', return_value=self.process) as launch, \
                    patch.object(runtime.subprocess, 'run') as cleanup, patch.object(runtime, 'network_bytes', side_effect=[100, 90]):
                receipt = runtime.run(directory, 0)
            command = launch.call_args.args[0]
            self.assertEqual(command[:3], ['docker', 'run', '--rm'])
            self.assertEqual(command[command.index('--memory') + 1], '1g')
            self.assertEqual(command[command.index('--cpus') + 1], '1')
            self.assertEqual(command[command.index('--pids-limit') + 1], '256')
            self.assertEqual('SYS_ADMIN' in command, mode == 'emulated')
            self.assertEqual(command[-2:], ['--synthetic', 'a; b'])
            cleanup.assert_called_once_with(['docker', 'rm', '-f', 'jumpserve-' + directory.name[:50]], stdout=runtime.subprocess.DEVNULL, stderr=runtime.subprocess.DEVNULL, timeout=15, check=False)
            self.assertTrue(receipt['interface_counter_reset'])
            self.assertIsNone(receipt['network_bytes'])
            self.assertEqual(json.loads((directory / 'runtime-receipt.json').read_bytes()), receipt)

    def test_unavailable_or_unknown_container_runtime_cannot_start_a_process(self):
        for kind, enabled in [('container', False), ('unregistered', True)]:
            directory = self.write(kind, {'package': {'kind': kind, 'version': 'v1'}})
            with patch.object(runtime.subprocess, 'Popen') as launch, patch.object(runtime.subprocess, 'run') as cleanup, self.assertRaisesRegex(ValueError, 'Container execution is unavailable'):
                runtime.run(directory, 0, allow_container=enabled)
            launch.assert_not_called(); cleanup.assert_not_called()

    def test_a_process_that_ignores_termination_is_killed_within_the_original_deadline(self):
        source=b'# synthetic frozen source\n'
        directory=self.write('ignores-termination',{'package':{'kind':'python-script','version':'v1','sha256':hashlib.sha256(source).hexdigest()}})
        (directory/'package.py').write_bytes(source)
        self.process.poll.return_value=None;self.process.returncode=-9
        self.process.wait.side_effect=[runtime.subprocess.TimeoutExpired('synthetic-process',3),None]
        with patch.object(runtime.subprocess,'Popen',return_value=self.process),patch.object(runtime.time,'time',return_value=10),patch.object(runtime,'network_bytes',return_value=None),patch.object(runtime.os,'killpg') as kill:
            receipt=runtime.run(directory,1)
        self.assertEqual([call.args for call in kill.call_args_list],[(4242,runtime.signal.SIGTERM),(4242,runtime.signal.SIGKILL)])
        self.assertEqual(receipt['termination_reason'],'Wall deadline exceeded.');self.assertEqual(receipt['exit_code'],-9)

    def test_network_counters_sum_nonloopback_interfaces_and_preserve_unavailable(self):
        with patch.object(runtime.Path, 'exists', return_value=False):
            self.assertIsNone(runtime.network_bytes())
        raw = ('Inter-| Receive | Transmit\nface | bytes packets | bytes packets\n'
               ' lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n'
               ' eth0: 10 0 0 0 0 0 0 0 20 0 0 0 0 0 0 0\n'
               ' eth1: 5 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n')
        with patch.object(runtime.Path, 'exists', return_value=True), patch.object(runtime.Path, 'read_text', return_value=raw):
            self.assertEqual(runtime.network_bytes(), 35)

    def test_cli_exit_status_comes_from_the_retained_process_outcome(self):
        source = b'# original synthetic source\n'
        for exit_code in [0, 1]:
            directory = self.write(str(exit_code), {'package': {'kind': 'python-script', 'version': 'v1', 'sha256': hashlib.sha256(source).hexdigest()}})
            (directory / 'package.py').write_bytes(source)
            self.process.returncode = exit_code
            with patch.object(sys, 'argv', ['package_runtime.py', '--directory', str(directory), '--deadline', '0']), \
                    patch.object(runtime.subprocess, 'Popen', return_value=self.process), self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(Path(runtime.__file__)), run_name='__main__')
            self.assertEqual(caught.exception.code, exit_code)
            receipt = json.loads((directory / 'runtime-receipt.json').read_bytes())
            self.assertEqual(receipt['exit_code'], exit_code)
            self.assertIsNone(receipt['measured_charges_usd'])


if __name__ == '__main__':
    unittest.main()
