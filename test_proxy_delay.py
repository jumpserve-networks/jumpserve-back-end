"""ProxyDelay runner contracts across the three maintained entrypoints.

All namespace commands and child processes are synthetic. These are software
regressions, not measured networking experiments or paper-reproduction evidence.
"""
import contextlib
import importlib
import io
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import MagicMock, patch
from tests.benchmark_support import simulate

MODULES = tuple(importlib.import_module(name) for name in ('netem_nines', 'netem_nines_many_clients', 'netem_nines_many_senders'))


def benchmark(module, **change):
    args = module.build_parser().parse_args([])
    args.snapshot_interval_ms = 100
    args.rate_mbit = 10
    args.bottleneck_buffer_kbytes = 125
    for key, value in change.items(): setattr(args, key, value)
    clients = module.resolve_client_run_configs(args)
    return module.ProxyDelayTwoFlowBench(args, clients)


class ProxyDelayRunners(unittest.TestCase):
    def test_two_flow_configuration_preserves_defaults_and_explicit_per_flow_values(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                bench = benchmark(module)
                self.assertEqual([client.name for client in bench.clients], ['client1', 'client2'])
                self.assertNotEqual(bench.clients[0].port, bench.clients[1].port)
                self.assertEqual(bench.clients[0].namespace, bench.clients[1].namespace)
                self.assertNotEqual(bench.clients[0].sender_namespace, bench.clients[1].sender_namespace)
                args = module.build_parser().parse_args(['--client-names', 'alpha,beta', '--client-ccas', 'cubic,bbr',
                    '--client-delays-ms', '10,20', '--client-start-delays-ms', '0,200', '--client-file-sizes-mbytes', '1,2'])
                clients = module.resolve_client_run_configs(args)
                self.assertEqual([(c.name, c.cca, c.delay_ms, c.start_delay_ms, c.file_size_to_be_transferred_in_mbytes) for c in clients],
                                 [('alpha', 'cubic', 10, 0, 1), ('beta', 'bbr', 20, 200, 2)])

    def test_invalid_flow_counts_names_and_array_shapes_fail_before_namespace_creation(self):
        invalid = [dict(num_clients=1), dict(client_names='one'), dict(client_names='same,same'),
                   dict(client_names='has:colon,valid'), dict(client_names='has space,valid'),
                   dict(client_names='total_megabits_per_second,valid'), dict(client_delays_ms='1'),
                   dict(client_delays_ms='-1,1'), dict(client_ccas='cubic'), dict(client_start_delays_ms='1'),
                   dict(client_start_delays_ms='-1,1'), dict(client_file_sizes_mbytes='1'), dict(client_file_sizes_mbytes='0,1')]
        for module in MODULES:
            with patch.object(module.base, 'run_shell') as shell:
                for change in invalid:
                    args = module.build_parser().parse_args([])
                    for name, value in change.items(): setattr(args, name, value)
                    with self.subTest(module=module.__name__, change=change), self.assertRaises(ValueError):
                        module.resolve_client_run_configs(args)
                with self.assertRaisesRegex(ValueError, 'exactly two flow configs'):
                    module.ProxyDelayTwoFlowBench(args, [])
                shell.assert_not_called()

    def test_topology_has_one_shared_bottleneck_and_per_flow_delays_with_return_routes(self):
        for module in MODULES:
            for rate, buffer, loss in [(0, 0, 0), (10, 0, 0), (0, 125, 0), (0, 0, 0.5), (10, 125, 0.5)]:
                with self.subTest(module=module.__name__, rate=rate, buffer=buffer, loss=loss), patch.object(module.base, 'run_shell') as shell:
                    bench = benchmark(module, rate_mbit=rate, bottleneck_buffer_kbytes=buffer, loss_pct=loss)
                    bench.setup()
                    script = shell.call_args.args[0]
                    self.assertEqual(len(bench._all_namespaces()), 8)
                    self.assertEqual(script.count(' netns add '), 8)
                    shared = [line for line in script.splitlines() if f'qdisc add dev {bench.client_router_client_veth} root netem' in line]
                    self.assertEqual(len(shared), int(rate > 0 or buffer > 0 or loss > 0))
                    if shared:
                        self.assertEqual(' rate ' in shared[0], rate > 0)
                        self.assertEqual(' limit ' in shared[0], buffer > 0)
                        self.assertEqual(' loss ' in shared[0], loss > 0)
                    for client in bench.clients:
                        self.assertIn(f'qdisc add dev {client.sender_router_veth_sender} root netem delay {client.delay_ms}ms', script)
                        self.assertIn(f'route add default via {client.sender_router_sender_ip}', script)
                    shell.reset_mock(); bench.cleanup()
                    self.assertEqual(shell.call_count, 8)
                    self.assertTrue(all(call.kwargs == {'verbose': False, 'check': False} for call in shell.call_args_list))

    def test_qdisc_and_receiver_counters_use_the_same_snapshot_identity(self):
        for module in MODULES:
            bench = benchmark(module)
            bench._receiver_byte_files = {client.name: client.name + '.bytes' for client in bench.clients}
            with self.subTest(module=module.__name__), patch.object(module.base, 'read_counter_file', side_effect=[100, 200]), \
                    patch.object(module.base, 'run_and_grab_shell', return_value='qdisc netem 1: root\n Sent 400 bytes 4 pkt\n backlog 1250b 1p'):
                self.assertEqual(bench._read_snapshot_byte_counters(), {'client1': 100, 'client2': 200, '__total__': 400, '__bottleneck_backlog_bytes__': 1250})
                self.assertEqual(bench._read_bottleneck_backlog_bytes(), 1250)
                self.assertEqual(bench._read_bottleneck_sent_bytes(), 400)
            sample = module.base.SSSocketMetrics(rtt_ms=21, in_flight_packets=2, congestion_window_bytes=3000)
            with patch.object(module.base, 'run_and_grab_shell', side_effect=['first ss fixture', 'second ss fixture']), \
                    patch.object(module.base, 'parse_ss_metrics_by_peer', side_effect=[{(bench.client_ip, bench.clients[0].port): sample}, {}]):
                raw, values = bench._read_ss_metrics_sample()
                self.assertEqual(values, {'client1': sample})
                self.assertIn('# client1\nfirst ss fixture', raw)
                self.assertIn('# client2\nsecond ss fixture', raw)
            rates = bench._snapshot_rates({'client1': 1000000, 'client2': 2000000, '__total__': 3000000},
                {'client1': 0, 'client2': 0, '__total__': 0}, 2)['receivers']
            self.assertEqual(rates['client1']['megabits_per_second'], 4)
            self.assertEqual(rates['client2']['megabits_per_second'], 8)
            self.assertEqual(rates['total_megabits_per_second'], 12)

    def run_benchmark(self, module, *, sampler='kernel', startup=True, failure=None, rate=10):
        bench = benchmark(module, snapshot_metrics_source=sampler, rate_mbit=rate)
        return simulate(bench, module.base, startup=startup, failure=failure)

    def test_kernel_snapshots_preserve_units_congestion_metrics_and_per_flow_configuration(self):
        for module in MODULES:
            for rate in (10, 0):
                with self.subTest(module=module.__name__, rate=rate):
                    result, processes, _ = self.run_benchmark(module, rate=rate)
                    self.assertIsInstance(result, dict)
                    self.assertEqual(result['config']['num_clients'], 2)
                    self.assertEqual(result['config']['topology']['shared_receiver_ip'], '192.168.3.100')
                    self.assertEqual(result['sender']['total_seconds'], 0.15)
                    self.assertEqual(len(processes), 4)
                    self.assertGreaterEqual(len(result['snapshots']), 1)
                    snap = result['snapshots'][0]
                    self.assertEqual(snap['snapshot_index'], 1)
                    self.assertEqual(snap['overall_in_flight_packets'], 24)
                    self.assertEqual(snap['overall_congestion_window_bytes'], 36000)
                    self.assertEqual(snap['bottleneck_queuing_delay_ms'], 1 if rate else 0)
                    self.assertEqual(snap['receivers']['client1']['megabits_per_second'], 3.2)
                    self.assertEqual(snap['receivers']['client2']['megabits_per_second'], 4.8)
                    self.assertEqual(snap['receivers']['client1']['rtt_ms'], 21.5)
                    self.assertEqual(snap['receivers']['total_megabits_per_second'], 8)
                    for process in processes:
                        if process.mode == 'sender': self.assertIn('--snapshot-rtt-ms-files', process.command)

    def test_ss_sampler_retains_its_snapshots_and_disables_kernel_counter_writers(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                result, processes, sampler = self.run_benchmark(module, sampler='ss')
                self.assertEqual(result['snapshots'], sampler.stop.return_value)
                self.assertEqual(result['config']['ss_log_file'], '/synthetic/ss.jsonl')
                sampler.start.assert_called_once(); sampler.stop.assert_called_once()
                for process in processes:
                    self.assertNotIn('--snapshot-rtt-ms-files', process.command)
                    self.assertNotIn('--snapshot-bytes-file', process.command)

    def test_startup_timeout_kills_receivers_and_process_failures_are_not_successful_results(self):
        for module in MODULES:
            with self.subTest(module=module.__name__, failure='startup'):
                result, processes, _ = self.run_benchmark(module, startup=False)
                self.assertIsInstance(result, RuntimeError)
                self.assertIn('Receiver startup timeout', str(result))
                self.assertEqual(len(processes), 2)
                self.assertTrue(all(process.killed for process in processes))
            for role in ('sender', 'receiver'):
                with self.subTest(module=module.__name__, failure=role):
                    result, _, _ = self.run_benchmark(module, failure=role)
                    self.assertIsInstance(result, RuntimeError)
                    self.assertIn(f'{role} failed: synthetic process failure', str(result))

    def test_orchestrator_checks_prerequisites_persists_once_and_cleans_up_after_failure(self):
        for module in MODULES:
            for keep, fail in [(False, False), (True, False), (False, True)]:
                args = module.build_parser().parse_args([]); args.keep_namespaces = keep
                bench = MagicMock(); bench.run_benchmark.return_value = {'fixture': 'result'}
                if fail: bench.run_benchmark.side_effect = RuntimeError('synthetic run failure')
                with self.subTest(module=module.__name__, keep=keep, fail=fail), contextlib.ExitStack() as stack:
                    checks = {name: stack.enter_context(patch.object(module.base, name)) for name in ('require_persistence', 'require_linux', 'require_root', 'require_tools', 'print_banner', 'persist_to_supabase')}
                    stack.enter_context(patch.object(module, 'ProxyDelayTwoFlowBench', return_value=bench))
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'synthetic run failure'): module.orchestrator_mode(args)
                        checks['persist_to_supabase'].assert_not_called()
                    else:
                        self.assertEqual(module.orchestrator_mode(args), 0)
                        checks['persist_to_supabase'].assert_called_once()
                        self.assertEqual(checks['persist_to_supabase'].call_args.args[1], {'fixture': 'result'})
                    self.assertEqual(bench.cleanup.call_count, 1 if keep else 2)
                    checks['require_tools'].assert_called_once_with(args.snapshot_metrics_source)

    def test_cli_routes_sender_receiver_orchestrator_and_legacy_snapshot_interval(self):
        for module in MODULES:
            for mode in ('sender', 'receiver', 'orchestrator'):
                args = module.build_parser().parse_args([]); args.mode = mode
                args.snapshot_interval_seconds_legacy = 0.1 if mode == 'orchestrator' else None
                parser = MagicMock(); parser.parse_args.return_value = args
                owner = module if mode == 'orchestrator' else module.base
                method = 'orchestrator_mode' if mode == 'orchestrator' else mode + '_mode'
                with self.subTest(module=module.__name__, mode=mode), patch.object(module, 'build_parser', return_value=parser), patch.object(owner, method, return_value=17) as run:
                    self.assertEqual(module.main(), 17); run.assert_called_once_with(args)
                    if mode == 'orchestrator': self.assertEqual(args.snapshot_interval_ms, 100)
            with patch.object(sys, 'argv', [module.__file__, '--help']), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                runpy.run_path(module.__file__, run_name='__main__')
            self.assertEqual(error.exception.code, 0)


if __name__ == '__main__':
    unittest.main()
