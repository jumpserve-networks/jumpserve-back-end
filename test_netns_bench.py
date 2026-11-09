"""Network-namespace runner contracts without Linux commands or real transfers."""
import contextlib
import datetime
import importlib
import io
import json
import runpy
import struct
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch
from tests.benchmark_support import simulate

MODULES = tuple(importlib.import_module(name) for name in ('netem_cubic_benchmark_nines', 'netem_cubic_benchmark_hotnets'))


def benchmark(module, **change):
    args = module.build_parser().parse_args([])
    args.num_clients = 2; args.rate_mbit = 10; args.bottleneck_buffer_kbytes = 125; args.snapshot_interval_ms = 100
    for name, value in change.items(): setattr(args, name, value)
    return module.NetnsBench(args, module.resolve_client_run_configs(args))


class NamespaceBenchmarks(unittest.TestCase):
    def test_shell_adapters_quote_arguments_preserve_output_and_apply_the_requested_error_policy(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), patch.object(module.os, 'system', return_value=0) as system, contextlib.redirect_stdout(io.StringIO()) as output:
                command = module.ns_shell('ns synthetic', module.shell_words('printf', 'a; b'))
                self.assertEqual(module.run_shell(command), 0); system.assert_called_once_with(command)
                self.assertIn("'ns synthetic'", command); self.assertIn("'a; b'", command); self.assertIn(command, output.getvalue())
                system.return_value = 256
                self.assertEqual(module.run_shell(command, verbose=False, check=False), 256)
                with self.assertRaisesRegex(RuntimeError, 'error 256'): module.run_shell(command, verbose=False)
            stream = io.StringIO('Original command diagnostics\n')
            with patch.object(module.os, 'popen', return_value=stream) as popen, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(module.run_and_grab_shell('synthetic'), 'Original command diagnostics\n'); self.assertTrue(stream.closed); popen.assert_called_once_with('synthetic'); self.assertIn('synthetic', output.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as output: module.print_banner('Explicit test configuration')
            self.assertIn('Explicit test configuration', output.getvalue())
            with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                module.run_cmd(['synthetic', 'argument'], check=False, capture=True); run.assert_called_once_with(['synthetic', 'argument'], check=False, text=True, capture_output=True)

    def test_counter_files_are_atomic_and_missing_or_malformed_counters_are_not_parsed_as_measurements(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'counter'; self.assertEqual(module.read_counter_file(str(path)), 0); self.assertEqual(module.read_float_file(str(path)), 0)
                module.write_counter_file(str(path), 1234); self.assertEqual(module.read_counter_file(str(path)), 1234)
                module.write_float_file(str(path), 12.3456789); self.assertEqual(module.read_float_file(str(path)), 12.345679); self.assertFalse(Path(str(path) + '.tmp').exists())
                path.write_text('not-a-counter'); self.assertEqual(module.read_counter_file(str(path)), 0); self.assertEqual(module.read_float_file(str(path)), 0)

    def test_csv_fallbacks_and_random_port_bounds_preserve_order_without_reusing_ports(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.parse_float_csv_arg('1.5,invalid,2', 'delays'), [1.5, 2]); self.assertEqual(module.parse_csv_arg(' , ', 'names'), [])
                self.assertEqual(module.choose_random_unique_ports(0), [])
                for count, low, high in [(1, 2, 1), (1, 2, 2), (4, 2, 4)]:
                    with self.assertRaises(ValueError): module.choose_random_unique_ports(count, low, high)
                self.assertEqual(sorted(module.choose_random_unique_ports(3, 2, 4)), [2, 3, 4])
                bench = benchmark(module, client_names='')
                self.assertEqual([client.name for client in bench.clients], ['client1', 'client2'])
                named = benchmark(module, client_names='alpha,beta')
                self.assertEqual([client.name for client in named.clients], ['alpha', 'beta'])

    def test_qdisc_text_and_json_keep_root_backlog_and_sent_bytes_separate(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.parse_tc_backlog_bytes('backlog 1.5Kb 2p\nbacklog 1KiB 1p'), 1536)
                self.assertEqual(module.parse_tc_backlog_bytes('backlog 2MiB 2p'), 2 * 1024 ** 2)
                self.assertEqual(module.parse_tc_backlog_bytes('missing backlog'), 0)
                self.assertEqual(module.parse_tc_sent_bytes('Sent 40 bytes 2 pkt\nSent 50 bytes 3 pkt'), 50); self.assertEqual(module.parse_tc_sent_bytes('missing sent'), 0)
                for raw in ['invalid JSON', '{}', '[]', '[null,3]', '[{"root":true,"bytes":true,"backlog":"not-a-number"}]']:
                    self.assertIsNone(module.parse_tc_root_qdisc_stats_json(raw))
                raw = json.dumps([None, {'bytes': 999, 'backlog': 1000}, {'root': True, 'bytes': ' 42 ', 'backlog': 1.9}])
                self.assertEqual(module.parse_tc_root_qdisc_stats_json(raw), {'sent_bytes': 42, 'backlog_bytes': 1})
                self.assertEqual(module.parse_tc_root_qdisc_stats_json('[{"bytes":-10,"backlog":-3}]'), {'sent_bytes': 0, 'backlog_bytes': 0})
                for value in [None, True, [], {}, '', 'invalid']: self.assertIsNone(module._coerce_int(value))
                for value, expected in [(0, 0), (2.9, 2), (' 42 ', 42)]: self.assertEqual(module._coerce_int(value), expected)

    def test_socket_sampler_parses_original_ipv4_and_ipv6_peer_identities_and_tcp_units(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                for raw in ['address-without-port', ':123', 'host:', 'host:invalid']: self.assertIsNone(module.parse_ss_socket_address(raw))
                self.assertEqual(module.parse_ss_socket_address('[2001:db8::1]:456'), ('2001:db8::1', 456))
                self.assertEqual(module.parse_ss_skmem('none'), {})
                self.assertEqual(module.parse_ss_skmem('skmem:(r12,rb64,,unknown8,invalid, t9, d0)'), {'r': 12, 'rb': 64, 't': 9, 'd': 0})
                raw = '\nshort record\nESTAB 0 0 local invalid-peer info\nESTAB 0 0 local 192.0.2.1:9001 delivery_rate 8000000bps rtt:12.5/1 mss:1500 cwnd:10 unacked:3 skmem:(r12,rb64)\nESTAB 0 0 local [2001:db8::1]:9002 missing-info\n'
                parsed = module.parse_ss_metrics_by_peer(raw)
                self.assertEqual(module.ss_metrics_to_log_dict(parsed[('192.0.2.1', 9001)]), dict(delivery_rate_bps=8000000, rtt_ms=12.5, in_flight_packets=3, congestion_window_bytes=15000, skmem={'r': 12, 'rb': 64}))
                self.assertEqual(module.ss_metrics_to_log_dict(parsed[('2001:db8::1', 9002)]), dict(delivery_rate_bps=0, rtt_ms=0, in_flight_packets=0, congestion_window_bytes=0, skmem={}))

    def test_oversized_counters_and_nonfinite_rates_cannot_contaminate_sampler_measurements(self):
        huge = '9' * 5000
        for module in MODULES:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.parse_tc_backlog_bytes('backlog ' + huge + 'Pb 1p'), 0)
                self.assertEqual(module.parse_tc_backlog_bytes('backlog ' + huge + 'Pb 1p\nbacklog 12b 1p'), 12)
                self.assertEqual(module.parse_tc_sent_bytes('Sent ' + huge + ' bytes 1 pkt'), 0)
                self.assertEqual(module.parse_tc_sent_bytes('Sent ' + huge + ' bytes 1 pkt\nSent 7 bytes 1 pkt'), 7)
                self.assertEqual(module.parse_ss_skmem('skmem:(r' + huge + ',rb12)'), {'rb': 12})
                raw = ('ESTAB 0 0 local 192.0.2.1:9001 delivery_rate ' + huge + 'bps rtt:' + huge + '/1 mss:' + huge + ' cwnd:' + huge + ' unacked:' + huge)
                metrics = module.ss_metrics_to_log_dict(module.parse_ss_metrics_by_peer(raw)[('192.0.2.1', 9001)])
                self.assertEqual(metrics, dict(delivery_rate_bps=0, rtt_ms=0, in_flight_packets=0, congestion_window_bytes=0, skmem={}))

    def test_snapshot_file_map_skips_incomplete_entries_and_nonverbose_shell_reads_keep_stdout_quiet(self):
        for module in MODULES:
            self.assertEqual(module.parse_snapshot_file_map('missing,:empty,name:, first : /synthetic/counter '), {'first': '/synthetic/counter'})
            with patch.object(module.os, 'popen', return_value=io.StringIO('original output')) as popen, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(module.run_and_grab_shell('synthetic command', verbose=False), 'original output')
                self.assertEqual(output.getvalue(), ''); popen.assert_called_once_with('synthetic command')

    def test_binary_tcp_info_uses_microseconds_packets_and_mss_without_changing_the_socket(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), patch.object(module.socket, 'TCP_INFO', 11, create=True):
                sock = MagicMock(); info = bytearray(104)
                for offset, value in [(16, 1448), (24, 7), (68, 12500), (80, 10)]: struct.pack_into('I', info, offset, value)
                sock.getsockopt.return_value = bytes(info)
                self.assertEqual(module.tcp_rtt_ms(sock), 12.5); self.assertEqual(module.tcp_sender_metrics(sock), dict(rtt_ms=12.5, in_flight_packets=7, congestion_window_bytes=14480)); sock.setsockopt.assert_not_called()
                sock.getsockopt.return_value = b'truncated'; self.assertEqual(module.tcp_rtt_ms(sock), 0); self.assertEqual(module.tcp_sender_metrics(sock), dict(rtt_ms=0, in_flight_packets=0, congestion_window_bytes=0))
                sock.getsockopt.side_effect = OSError('Synthetic unsupported TCP_INFO'); self.assertEqual(module.tcp_rtt_ms(sock), 0); self.assertEqual(module.tcp_sender_metrics(sock), dict(rtt_ms=0, in_flight_packets=0, congestion_window_bytes=0))

    def test_buffer_conversions_and_sampler_log_paths_keep_declared_units(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as folder, patch.object(module.Path, 'cwd', return_value=Path(folder)):
                self.assertEqual(module.buffer_kbytes_to_packet_limit(0), 0); self.assertEqual(module.buffer_kbytes_to_packet_limit(.00001), 0); self.assertEqual(module.buffer_kbytes_to_packet_limit(1.5), 2)
                self.assertEqual(module.buffer_kbytes_to_byte_limit(0), 0); self.assertEqual(module.buffer_kbytes_to_byte_limit(1.5), 1536)
                self.assertEqual(module.tbf_burst_bytes(0), 3000); self.assertEqual(module.tbf_burst_bytes(100), 125000)
                self.assertEqual(module.tbf_limit_bytes(100, 1), 125000); self.assertEqual(module.tbf_limit_bytes(0, 0), 65536)
                self.assertEqual(module.resolve_ss_log_path('', 'synthetic'), str(Path(folder) / 'ss_sampler_synthetic.jsonl'))
                target = Path(folder) / 'nested/sampler.jsonl'; self.assertEqual(module.resolve_ss_log_path(str(target), 'synthetic'), str(target)); self.assertTrue(target.parent.is_dir())
                expected = str(Path(folder).resolve() / 'relative/sampler.jsonl')
                with patch.object(module.os, 'getcwd', return_value=folder): self.assertEqual(module.resolve_ss_log_path('relative/sampler.jsonl', 'synthetic'), expected)

    def test_many_flow_configuration_has_unique_ports_and_independent_receiver_namespaces(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                bench = benchmark(module, num_clients=3, client_ccas='cubic,bbr,reno', client_delays_ms='10,20,30', client_start_delays_ms='0,100,200')
                self.assertEqual(len(bench.clients), 3)
                self.assertEqual(len({client.port for client in bench.clients}), 3)
                self.assertEqual(len({client.namespace for client in bench.clients}), 3)
                self.assertEqual([client.delay_ms for client in bench.clients], [10, 20, 30])
                self.assertEqual([client.start_delay_ms for client in bench.clients], [0, 100, 200])
                self.assertEqual(len(bench._all_namespaces()), 12)
                for count in (0, 254):
                    with self.assertRaises(ValueError): benchmark(module, num_clients=count)

    def test_setup_shapes_one_communal_link_and_preserves_routes_for_each_flow(self):
        for module in MODULES:
            for rate, buffer, loss in [(0, 0, 0), (10, 0, 0), (0, 125, 0), (0, 0, 1), (10, 125, 1)]:
                bench = benchmark(module, num_clients=3, rate_mbit=rate, bottleneck_buffer_kbytes=buffer, loss_pct=loss)
                with self.subTest(module=module.__name__, rate=rate, buffer=buffer, loss=loss), patch.object(module, 'run_shell') as shell:
                    bench.setup(); script = shell.call_args.args[0]
                    self.assertEqual(script.count(' netns add '), 12)
                    communal = [line for line in script.splitlines() if f'qdisc add dev {bench.switch_bottleneck_veth} root netem' in line]
                    self.assertEqual(len(communal), int(bool(rate or buffer or loss)))
                    if communal:
                        self.assertEqual(' rate ' in communal[0], rate > 0)
                        self.assertEqual(' limit ' in communal[0], buffer > 0)
                        self.assertEqual(' loss ' in communal[0], loss > 0)
                    for client in bench.clients:
                        self.assertIn(f'route add {client.sender_ip}/32 via {client.sender_router_switch_ip}', script)
                        self.assertIn(f'route add {client.client_ip}/32 via {bench.transit_switch_ip}', script)
                        self.assertIn(f'qdisc add dev {client.sender_router_veth_sender} root netem delay {client.delay_ms}ms', script)
                    shell.reset_mock(); bench.cleanup()
                    self.assertEqual(shell.call_count, 12)
                    self.assertTrue(all(call.kwargs == {'verbose': False, 'check': False} for call in shell.call_args_list))

    def test_snapshots_keep_receiver_interface_counters_separate_from_shared_queue_counters(self):
        for module in MODULES:
            bench = benchmark(module)
            with self.subTest(module=module.__name__), patch.object(module, 'run_and_grab_shell', side_effect=['100', '200', 'qdisc netem 1: root\n Sent 400 bytes 4 pkt\n backlog 1250b 1p']):
                self.assertEqual(bench._read_snapshot_byte_counters(), {'client1': 100, 'client2': 200, '__total__': 400, '__bottleneck_backlog_bytes__': 1250})
            with patch.object(module, 'run_and_grab_shell', return_value='invalid counter'):
                self.assertEqual(bench._read_interface_counter_bytes('ns', 'veth', 'rx_bytes'), 0)
            with patch.object(module, 'run_and_grab_shell', return_value='Sent 400 bytes 4 pkt\n backlog 1250b 1p'):
                self.assertEqual(bench._read_bottleneck_backlog_bytes(), 1250)
                self.assertEqual(bench._read_bottleneck_sent_bytes(), 400)
            metrics = module.SSSocketMetrics(rtt_ms=10)
            with patch.object(module, 'run_and_grab_shell', side_effect=['first', 'second']), patch.object(module, 'parse_ss_metrics_by_peer', side_effect=[{}, {(bench.clients[1].client_ip, bench.clients[1].port): metrics}]):
                raw, values = bench._read_ss_metrics_sample()
                self.assertEqual(values, {'client2': metrics})
                self.assertIn('# client1\nfirst', raw); self.assertIn('# client2\nsecond', raw)

    def test_kernel_snapshots_and_metadata_preserve_measured_units_and_each_flow_identity(self):
        for module in MODULES:
            for rate in (0, 10):
                with self.subTest(module=module.__name__, rate=rate):
                    bench = benchmark(module, rate_mbit=rate)
                    result, processes, _ = simulate(bench, module)
                    self.assertIsInstance(result, dict)
                    self.assertEqual(result['config']['num_clients'], 2)
                    self.assertEqual(result['config']['topology']['communal_bottleneck_device'], bench.switch_bottleneck_veth)
                    snapshot = result['snapshots'][0]
                    self.assertEqual(snapshot['receivers']['client1']['megabits_per_second'], 3.2)
                    self.assertEqual(snapshot['receivers']['client2']['megabits_per_second'], 4.8)
                    self.assertEqual(snapshot['receivers']['total_megabits_per_second'], 8)
                    self.assertEqual(snapshot['bottleneck_queuing_delay_ms'], 1 if rate else 0)
                    self.assertEqual(snapshot['overall_in_flight_packets'], 24)
                    self.assertEqual(snapshot['overall_congestion_window_bytes'], 36000)
                    self.assertEqual(len(processes), 4)
                    for client in bench.clients:
                        self.assertEqual(result['config']['clients'][client.name]['output_namespace'], client.namespace)
                        self.assertEqual(result['sender']['targets'][client.name]['bytes_sent'], 1000)

    def test_ss_mode_uses_original_sampler_snapshots_without_kernel_counter_files(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                result, processes, sampler = simulate(benchmark(module, snapshot_metrics_source='ss'), module)
                self.assertEqual(result['snapshots'], sampler.stop.return_value)
                self.assertEqual(result['config']['snapshot_metrics_source'], 'ss')
                sampler.start.assert_called_once(); sampler.stop.assert_called_once()
                self.assertTrue(all('--snapshot-rtt-ms-files' not in process.command for process in processes))

    def test_process_and_startup_failures_never_return_a_successful_benchmark_result(self):
        for module in MODULES:
            for failure in ('startup', 'sender', 'receiver'):
                with self.subTest(module=module.__name__, failure=failure):
                    result, processes, _ = simulate(benchmark(module), module, startup=failure != 'startup', failure=failure)
                    self.assertIsInstance(result, RuntimeError)
                    if failure == 'startup':
                        self.assertEqual(len(processes), 2); self.assertTrue(all(process.killed for process in processes))
                    else: self.assertIn(f'{failure} failed', str(result))

    def test_orchestrator_verifies_prerequisites_and_cleans_up_on_both_success_and_failure(self):
        for module in MODULES:
            for keep, fail in [(False, False), (True, False), (False, True)]:
                args = module.build_parser().parse_args([]); args.keep_namespaces = keep
                bench = MagicMock(); bench.run_benchmark.return_value = {'synthetic': True}
                if fail: bench.run_benchmark.side_effect = RuntimeError('synthetic run failure')
                with self.subTest(module=module.__name__, keep=keep, fail=fail), contextlib.ExitStack() as stack:
                    checks = {name: stack.enter_context(patch.object(module, name)) for name in ('require_persistence', 'require_linux', 'require_root', 'require_tools', 'print_banner', 'persist_to_supabase')}
                    stack.enter_context(patch.object(module, 'NetnsBench', return_value=bench))
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'synthetic run failure'): module.orchestrator_mode(args)
                        checks['persist_to_supabase'].assert_not_called()
                    else:
                        self.assertEqual(module.orchestrator_mode(args), 0)
                        checks['persist_to_supabase'].assert_called_once()
                        self.assertEqual(checks['persist_to_supabase'].call_args.args[1], {'synthetic': True})
                    self.assertEqual(bench.cleanup.call_count, 1 if keep else 2)
                    checks['require_tools'].assert_called_once_with(args.snapshot_metrics_source)

    def test_cli_mode_dispatch_and_legacy_interval_keep_sender_and_receiver_roles_separate(self):
        for module in MODULES:
            for mode in ('sender', 'receiver', 'orchestrator'):
                args = module.build_parser().parse_args([]); args.mode = mode
                args.snapshot_interval_seconds_legacy = 0.1 if mode == 'orchestrator' else None
                parser = MagicMock(); parser.parse_args.return_value = args
                with self.subTest(module=module.__name__, mode=mode), patch.object(module, 'build_parser', return_value=parser), patch.object(module, mode + '_mode', return_value=17) as run:
                    self.assertEqual(module.main(), 17); run.assert_called_once_with(args)
                    if mode == 'orchestrator': self.assertEqual(args.snapshot_interval_ms, 100)
            with patch.object(sys, 'argv', [module.__file__, '--help']), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                runpy.run_path(module.__file__, run_name='__main__')
            self.assertEqual(error.exception.code, 0)


class InlineThread:
    """Run one deterministic writer tick and the transfer, without scheduling races."""
    def __init__(self, target, args=(), daemon=False):
        self.target = target; self.args = args; self.started = False; self.joined = False

    def start(self):
        self.started = True; self.target(*self.args)

    def join(self):
        self.joined = True


class WriterEvent:
    def __init__(self): self.waits = 0; self.stopped = False
    def wait(self, timeout):
        self.waits += 1
        return self.stopped or self.waits > 1
    def set(self): self.stopped = True


class TransferModes(unittest.TestCase):
    def test_receiver_snapshot_outputs_can_be_requested_independently_without_a_ready_file(self):
        for module in MODULES:
            for kind in ['bytes', 'rtt']:
                with self.subTest(module=module.__name__, kind=kind), tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
                    args = module.build_parser().parse_args([])
                    args.ready_file = ''; args.snapshot_bytes_file = ''; args.snapshot_rtt_ms_file = ''
                    setattr(args, 'snapshot_' + ('bytes' if kind == 'bytes' else 'rtt_ms') + '_file', str(Path(folder) / kind))
                    server = MagicMock(); server.__enter__.return_value = server
                    conn = MagicMock(); conn.__enter__.return_value = conn; conn.recv.side_effect = [b'abc', b'']
                    server.accept.return_value = (conn, ('192.0.2.1', 1))
                    stack.enter_context(patch.object(module.socket, 'socket', return_value=server))
                    stack.enter_context(patch.object(module.threading, 'Thread', InlineThread))
                    stack.enter_context(patch.object(module.threading, 'Event', WriterEvent))
                    stack.enter_context(patch.object(module.time, 'monotonic', side_effect=[10, 11, 12, 13]))
                    sample = stack.enter_context(patch.object(module, 'tcp_rtt_ms', return_value=4.5))
                    output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    self.assertEqual(module.receiver_mode(args), 0)
                    self.assertEqual(json.loads(output.getvalue())['bytes'], 3)
                    self.assertEqual(float((Path(folder) / kind).read_text()), 3 if kind == 'bytes' else 4.5)
                    self.assertEqual(sample.call_count, 0 if kind == 'bytes' else 1)

    def test_snapshot_sampling_without_an_open_log_still_preserves_measurements(self):
        for module in MODULES:
            bench = benchmark(module)
            with self.subTest(module=module.__name__), patch.object(bench, '_read_ss_metrics_sample', return_value=('', {})), patch.object(bench, '_read_bottleneck_qdisc_stats', return_value={'backlog_bytes': 0, 'sent_bytes': 0}), patch.object(module.time, 'monotonic', return_value=1):
                snapshot = module.SSSampler(bench, 100, '/synthetic/not-opened')._sample(1)
                self.assertEqual(snapshot['snapshot_index'], 1)
                self.assertEqual(snapshot['receivers']['total_megabits_per_second'], 0)

    def test_sender_handles_partial_writes_exact_byte_targets_start_delay_and_snapshot_units(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
                root = Path(folder)
                args = module.build_parser().parse_args(['--targets', 'first:192.0.2.1:9001:bbr:0.00001:2,second:192.0.2.2:9002:cubic:0.00001:0', '--chunk-size', '4'])
                for option, suffix in [('snapshot_bytes_files', 'bytes'), ('snapshot_rtt_ms_files', 'rtt'), ('snapshot_in_flight_files', 'flight'), ('snapshot_cwnd_bytes_files', 'cwnd')]:
                    setattr(args, option, f'first:{root / suffix}')
                args.snapshot_writer_interval_seconds = 0
                sockets = [MagicMock(), MagicMock()]
                for sock in sockets:
                    sock.__enter__.return_value = sock
                    sock.send.side_effect = lambda payload: min(3, len(payload))
                stack.enter_context(patch.object(module.socket, 'socket', side_effect=sockets))
                stack.enter_context(patch.object(module.socket, 'TCP_CONGESTION', 13, create=True))
                stack.enter_context(patch.object(module.threading, 'Thread', InlineThread))
                stack.enter_context(patch.object(module.threading, 'Event', WriterEvent))
                stack.enter_context(patch.object(module.time, 'monotonic', side_effect=[10, 11]))
                sleep = stack.enter_context(patch.object(module.time, 'sleep'))
                sample = stack.enter_context(patch.object(module, 'tcp_sender_metrics', return_value={'rtt_ms': 12.5, 'in_flight_packets': 7, 'congestion_window_bytes': 15000}))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(module.sender_mode(args), 0)
                self.assertEqual([len(call.args[0]) for call in sockets[0].send.call_args_list], [4, 4, 4, 1])
                for sock, host, port, cca in zip(sockets, ['192.0.2.1', '192.0.2.2'], [9001, 9002], [b'bbr', b'cubic']):
                    sock.connect.assert_called_once_with((host, port))
                    sock.setsockopt.assert_called_once_with(module.socket.IPPROTO_TCP, 13, cca)
                    sock.shutdown.assert_called_once_with(module.socket.SHUT_WR)
                sleep.assert_called_once_with(.002); self.assertEqual(sample.call_count, 2)
                self.assertEqual(int((root / 'bytes').read_text()), 10)
                self.assertEqual(float((root / 'rtt').read_text()), 12.5)
                self.assertEqual(int((root / 'flight').read_text()), 7)
                self.assertEqual(int((root / 'cwnd').read_text()), 15000)
                self.assertEqual({p.name for p in root.iterdir()}, {'bytes', 'rtt', 'flight', 'cwnd'})
                result = json.loads(output.getvalue()); self.assertEqual(result['total_seconds'], 1)
                self.assertEqual(result['targets']['first']['start_delay_ms'], 2)

    def test_sender_optional_target_fields_retain_the_declared_defaults_without_snapshot_writes(self):
        for module in MODULES:
            for target in ['bad,first:192.0.2.1:9001', 'first:192.0.2.1:9001:bbr:invalid:invalid']:
                args = module.build_parser().parse_args(['--targets', target, '--chunk-size', '16'])
                sock = MagicMock(); sock.__enter__.return_value = sock; sock.send.side_effect = len
                with self.subTest(module=module.__name__, target=target), patch.object(module.socket, 'socket', return_value=sock), patch.object(module.socket, 'TCP_CONGESTION', 13, create=True), patch.object(module, 'DEFAULT_FILE_SIZE_TO_BE_TRANSFERRED_IN_MBYTES', .00001), patch.object(module.threading, 'Thread', InlineThread), patch.object(module.time, 'sleep') as sleep, patch.object(module, 'write_counter_file') as write, contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(module.sender_mode(args), 0)
                    self.assertEqual(len(sock.send.call_args.args[0]), 10)
                    sleep.assert_not_called(); write.assert_not_called()
                    self.assertEqual(json.loads(output.getvalue())['targets']['first']['start_delay_ms'], 0)

    def test_sender_errors_close_the_socket_and_never_report_success(self):
        for module in MODULES:
            for failure in ['closed', 'cca', 'connect', 'size']:
                args = module.build_parser().parse_args(['--targets', f'first:192.0.2.1:9001:bbr:{0 if failure == "size" else .00001}'])
                sock = MagicMock(); sock.__enter__.return_value = sock; sock.send.return_value = 0
                if failure == 'cca': sock.setsockopt.side_effect = OSError('CCA unavailable')
                if failure == 'connect': sock.connect.side_effect = OSError('Connection refused')
                with self.subTest(module=module.__name__, failure=failure), patch.object(module.socket, 'socket', return_value=sock), patch.object(module.socket, 'TCP_CONGESTION', 13, create=True), patch.object(module.threading, 'Thread', InlineThread), contextlib.redirect_stdout(io.StringIO()) as output:
                    with self.assertRaisesRegex(RuntimeError, 'first:'): module.sender_mode(args)
                    self.assertEqual(output.getvalue(), '')
                    if failure != 'size': sock.__exit__.assert_called_once()
            for target in ['', 'first:192.0.2.1:not-a-port']:
                args = module.build_parser().parse_args(['--targets', target])
                with self.assertRaises(ValueError): module.sender_mode(args)

    def test_receiver_preserves_ready_signal_exact_bytes_rtt_and_measured_completion_duration(self):
        for module in MODULES:
            for snapshots, chunks in [(True, [b'abc', b'defgh', b'']), (False, [b''])]:
                with self.subTest(module=module.__name__, snapshots=snapshots), tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
                    root = Path(folder); args = module.build_parser().parse_args([])
                    args.listen_ip = '192.0.2.1'; args.port = 9001; args.name = 'first'; args.ready_file = str(root / 'ready')
                    args.snapshot_bytes_file = str(root / 'bytes') if snapshots else ''
                    args.snapshot_rtt_ms_file = str(root / 'rtt') if snapshots else ''
                    args.snapshot_writer_interval_seconds = 0
                    server = MagicMock(); server.__enter__.return_value = server
                    conn = MagicMock(); conn.__enter__.return_value = conn; conn.recv.side_effect = chunks
                    server.accept.return_value = (conn, ('192.0.2.2', 1234))
                    stack.enter_context(patch.object(module.socket, 'socket', return_value=server))
                    stack.enter_context(patch.object(module.threading, 'Thread', InlineThread))
                    stack.enter_context(patch.object(module.threading, 'Event', WriterEvent))
                    stack.enter_context(patch.object(module.time, 'monotonic', side_effect=[10, 11, 13, 14] if snapshots else [10, 11]))
                    sample = stack.enter_context(patch.object(module, 'tcp_rtt_ms', return_value=12.5))
                    output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    self.assertEqual(module.receiver_mode(args), 0)
                    server.bind.assert_called_once_with(('192.0.2.1', 9001)); server.listen.assert_called_once_with(1)
                    self.assertEqual((root / 'ready').read_text(), 'ready\n')
                    result = json.loads(output.getvalue())
                    if snapshots:
                        self.assertEqual(result['bytes'], 8); self.assertEqual(result['seconds'], 2)
                        self.assertEqual(result['flow_completion_time_ms'], 4000)
                        self.assertEqual(result['megabits_per_second'], .000032)
                        self.assertEqual(int((root / 'bytes').read_text()), 8)
                        self.assertEqual(float((root / 'rtt').read_text()), 12.5)
                        self.assertEqual(sample.call_count, 2)
                    else:
                        self.assertEqual(result['bytes'], 0); self.assertEqual(result['seconds'], 0)
                        self.assertEqual(result['flow_completion_time_ms'], 1000)
                        sample.assert_not_called()

    def test_sampler_records_original_ss_evidence_and_converts_delivery_rate_without_merging_clients(self):
        for module in MODULES:
            for rate in [0, 10]:
                bench = benchmark(module, rate_mbit=rate)
                metrics = module.SSSocketMetrics(delivery_rate_bps=8_000_000, rtt_ms=12.5, in_flight_packets=7, congestion_window_bytes=15000, skmem={'r': 4})
                with self.subTest(module=module.__name__, rate=rate), tempfile.TemporaryDirectory() as folder, patch.object(bench, '_read_ss_metrics_sample', return_value=('original ss evidence', {'client1': metrics})), patch.object(bench, '_read_bottleneck_qdisc_stats', return_value={'backlog_bytes': 1250, 'sent_bytes': 5000}), patch.object(module.threading, 'Thread', InlineThread), patch.object(module.threading, 'Event', WriterEvent), patch.object(module.time, 'monotonic', side_effect=[10.5, 10.75]):
                    path = str(Path(folder) / 'sample.jsonl'); sampler = module.SSSampler(bench, 250, path)
                    sampler.start(10); snapshots = sampler.stop()
                    self.assertEqual(len(snapshots), 2)
                    self.assertEqual(snapshots[0]['elapsed_microseconds'], 500_000)
                    self.assertEqual(snapshots[1]['snapshot_index'], 2)
                    self.assertEqual(snapshots[0]['receivers']['client1']['megabits_per_second'], 8)
                    self.assertEqual(snapshots[0]['receivers']['client2']['megabits_per_second'], 0)
                    self.assertEqual(snapshots[0]['overall_congestion_window_bytes'], 15000)
                    self.assertEqual(snapshots[0]['bottleneck_queuing_delay_ms'], 1 if rate else 0)
                    original = [json.loads(line) for line in Path(path).read_text().splitlines()]
                    self.assertEqual(original[0]['raw_ss_output'], 'original ss evidence')
                    self.assertEqual(original[0]['sampled_metrics']['client1']['skmem'], {'r': 4})
                    self.assertIsNone(sampler._log_handle); self.assertTrue(sampler._thread.joined)
                    self.assertEqual(module.SSSampler(bench, 250, path).stop(), [])

    def test_prerequisites_refuse_unsupported_platform_privileges_or_missing_tools(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), contextlib.redirect_stderr(io.StringIO()), patch.object(module.platform, 'system', return_value='Darwin'), self.assertRaises(SystemExit) as error:
                module.require_linux()
            self.assertEqual(error.exception.code, 1)
            with patch.object(module.platform, 'system', return_value='Linux'): module.require_linux()
            with patch.object(module.os, 'geteuid', return_value=1), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): module.require_root()
            with patch.object(module.os, 'geteuid', return_value=0): module.require_root()
            for source in ['ss', 'kernel']:
                with patch.object(module.shutil, 'which', side_effect=lambda name: None if name == 'ss' else '/fixture/tool'):
                    if source == 'ss':
                        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): module.require_tools(source)
                    else: module.require_tools(source)

    def test_rest_adapter_uses_exact_public_table_contracts_and_returns_no_partial_success_on_error(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), patch.object(module, 'ingest_settings', return_value=None):
                client = module.SupabaseRestClient('fixture-project', 'fixture-only-key', 4)
                response = io.BytesIO(b'[{"id": 17}]')
                with patch.object(module.urllib.request, 'urlopen', return_value=response) as request:
                    self.assertEqual(client._request('POST', 'emulated_runs', query='select=id', payload=[{'client_number': 1}], prefer='return=representation'), [{'id': 17}])
                    sent = request.call_args.args[0]
                    self.assertEqual(sent.full_url, 'https://fixture-project.supabase.co/rest/v1/emulated_runs?select=id')
                    self.assertEqual(sent.headers['Apikey'], 'fixture-only-key'); self.assertNotIn('Authorization', sent.headers)
                    self.assertEqual(json.loads(sent.data), [{'client_number': 1}]); self.assertEqual(request.call_args.kwargs, {'timeout': 4})
                with patch.object(module.urllib.request, 'urlopen', return_value=io.BytesIO(b' ')):
                    self.assertIsNone(client._request('GET', 'emulated_runs'))
                error = urllib.error.HTTPError('https://fixture.invalid', 503, 'Unavailable', {}, io.BytesIO(b'fixture failure'))
                with patch.object(module.urllib.request, 'urlopen', side_effect=error), self.assertRaisesRegex(RuntimeError, 'Supabase GET emulated_runs failed \\(503\\): fixture failure'):
                    client._request('GET', 'emulated_runs')
                buffer = MagicMock(); client.ingest = buffer
                self.assertEqual(client._request('POST', 'runs', payload=[{'id': 'fixture'}]), buffer.request.return_value)
                buffer.request.assert_called_once_with('POST', 'runs', '', [{'id': 'fixture'}])

    def test_rest_inserts_preserve_units_requested_values_and_algorithm_identity(self):
        for module in MODULES:
            with self.subTest(module=module.__name__), patch.object(module, 'ingest_settings', return_value=None):
                client = module.SupabaseRestClient('fixture', 'fixture-only-key')
                with patch.object(client, '_request', side_effect=[[{'id': '5'}], [], [{'id': '6'}]]) as request:
                    self.assertEqual(client.get_or_create_algorithm_id('bbr v2'), 5)
                    self.assertIn('bbr%20v2', request.call_args.kwargs['query'])
                    self.assertEqual(client.get_or_create_algorithm_id('cubic'), 6)
                    self.assertEqual(request.call_args.kwargs['payload'], [{'name': 'cubic'}])
                with patch.object(client, '_request', return_value=[]) as request:
                    with self.assertRaisesRegex(RuntimeError, 'algorithm'): client.get_or_create_algorithm_id('missing')
                    with self.assertRaisesRegex(RuntimeError, 'parent_runs'): client.insert_emulated_parent_run(number_of_clients=2, bottleneck_rate_megabit=10, queue_buffer_size_kilobyte=125, snapshot_length_ms=100)
                    with self.assertRaisesRegex(ValueError, 'must be > 0'): client.insert_emulated_parent_run(number_of_clients=2, bottleneck_rate_megabit=0, queue_buffer_size_kilobyte=125, snapshot_length_ms=100)
                    with self.assertRaisesRegex(RuntimeError, 'emulated_runs'): client.insert_emulated_run(1, 10, 5, 7, 100, 0, 2000)
                    client.insert_emulated_snapshot_stats([])
                with patch.object(client, '_request', return_value=[{'id': 7}]) as request:
                    self.assertEqual(client.insert_emulated_parent_run(number_of_clients=2, bottleneck_rate_megabit=10.5, queue_buffer_size_kilobyte=125, snapshot_length_ms=100), 7)
                    self.assertEqual(request.call_args.kwargs['payload'][0]['bottleneck_rate_megabit'], '10.5')
                    self.assertEqual(client.insert_emulated_run(1, 10, 5, 7, 100, 0, 2000), 7)
                    self.assertEqual(request.call_args.kwargs['payload'][0]['client_start_delay_ms'], 0)
                    client.insert_emulated_snapshot_stats([{'round_trip_time_ms': '12.5'}]); self.assertEqual(request.call_args.kwargs['payload'], [{'round_trip_time_ms': '12.5'}])
                    client.insert_run({'raw_log': {'original': True}}); self.assertEqual(request.call_args.kwargs['payload'], [{'raw_log': {'original': True}}])


class NamespacePersistence(unittest.TestCase):
    def payload(self):
        return {'config': {}, 'receivers': {'client1': {'flow_completion_time_ms': 1500}, 'client2': {'flow_completion_time_ms': 2300}},
                'snapshots': [None, {}, {'snapshot_index': 1, 'elapsed_microseconds': 125000,
                    'bottleneck_queuing_delay_ms': 1.5, 'bottleneck_backlog_bytes': 1875,
                    'receivers': {name: {'megabits_per_second': 0 if name == 'client1' else 8.75, 'rtt_ms': 12.5,
                        'in_flight_packets': 7, 'congestion_window_bytes': 15000} for name in ['client1', 'client2']}}]}

    def client(self, buffered=False):
        client = MagicMock(); client.ingest = MagicMock() if buffered else None
        client.insert_emulated_parent_run.return_value = 7
        client.get_or_create_algorithm_id.return_value = 5
        client.insert_emulated_run.side_effect = [11, 12]
        if buffered: client.ingest.commit.return_value = {'parent_run_id': 70, 'run_ids': {'11': 110, '12': 120}, 'algorithm_ids': {'cubic': 50}, 'raw_run_id': 'recorded-raw-run'}
        return client

    def test_measurements_persist_with_configs_original_log_units_and_zero_without_duplicate_algorithm_inserts(self):
        for module in MODULES:
            for buffered in [False, True]:
                bench = benchmark(module, client_ccas='cubic,cubic', client_delays_ms='10,60', client_file_sizes_mbytes='50,100')
                value = self.payload(); client = self.client(buffered)
                started = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc); ended = started + datetime.timedelta(seconds=3)
                with self.subTest(module=module.__name__, buffered=buffered), patch.object(module, 'require_persistence') as require, patch.object(module, 'SupabaseRestClient', return_value=client), contextlib.redirect_stdout(io.StringIO()):
                    result = module.persist_to_supabase(bench.args, value, started, ended, bench.clients)
                require.assert_called_once_with(bench.args)
                client.get_or_create_algorithm_id.assert_called_once_with('cubic')
                self.assertEqual(client.insert_emulated_parent_run.call_args.kwargs, dict(number_of_clients=2, bottleneck_rate_megabit=10, queue_buffer_size_kilobyte=125, snapshot_length_ms=100))
                rows = client.insert_emulated_snapshot_stats.call_args.args[0]
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0], {'snapshot_index': 1, 'emulated_run_id': 11, 'elapsed_microseconds': 125000,
                    'megabits_per_second': '0', 'round_trip_time_ms': '12.5', 'bottleneck_queuing_delay_ms': '1.5',
                    'bottleneck_backlog_bytes': 1875, 'in_flight_packets': 7, 'congestion_window_bytes': 15000})
                self.assertEqual(rows[1]['megabits_per_second'], '8.75')
                raw = client.insert_run.call_args.args[0]
                self.assertIs(raw['raw_log'], value); self.assertEqual(raw['started_at'], started.isoformat())
                self.assertEqual(raw['ended_at'], ended.isoformat()); self.assertEqual(raw['delay_added_ms'], 60)
                self.assertIsNone(raw['log_path']); self.assertEqual(result['emulated_snapshot_stats_rows'], 2)
                self.assertEqual(result['emulated_parent_run_id'], 70 if buffered else 7)
                self.assertEqual([run['id'] for run in result['emulated_runs']], [110, 120] if buffered else [11, 12])
                if buffered:
                    client.ingest.commit.assert_called_once(); self.assertEqual(result['algorithm_ids'], {'cubic': 50})
                    self.assertEqual(result['run_id'], 'recorded-raw-run')

    def test_missing_or_invalid_measurements_never_commit_an_ingest_report_or_mark_raw_run_success(self):
        for module in MODULES:
            for field, value, message in [('receivers', [], 'receivers'), ('receivers', {}, 'flow completion'),
                                          ('snapshots', {}, 'snapshots'), ('config', [], 'config')]:
                payload = self.payload(); payload[field] = value; bench = benchmark(module); client = self.client(True)
                with self.subTest(module=module.__name__, field=field, value=value), patch.object(module, 'require_persistence'), patch.object(module, 'SupabaseRestClient', return_value=client), self.assertRaisesRegex(RuntimeError, message):
                    module.persist_to_supabase(bench.args, payload, datetime.datetime.now(), datetime.datetime.now(), bench.clients)
                client.ingest.commit.assert_not_called(); client.insert_run.assert_not_called()
            for metric in ['elapsed_microseconds', 'bottleneck_backlog_bytes', 'bottleneck_queuing_delay_ms', 'receivers']:
                payload = self.payload(); del payload['snapshots'][-1][metric]; client = self.client(True); bench = benchmark(module)
                with self.subTest(module=module.__name__, metric=metric), patch.object(module, 'require_persistence'), patch.object(module, 'SupabaseRestClient', return_value=client), self.assertRaisesRegex(RuntimeError, 'missing expected metrics'):
                    module.persist_to_supabase(bench.args, payload, datetime.datetime.now(), datetime.datetime.now(), bench.clients)
                client.ingest.commit.assert_not_called(); client.insert_emulated_snapshot_stats.assert_not_called()

    def test_ss_source_requires_original_log_path_and_retains_sampling_interval(self):
        for module in MODULES:
            bench = benchmark(module, snapshot_metrics_source='ss', ss_sample_interval_ms=250); client = self.client()
            with self.subTest(module=module.__name__), patch.object(module, 'require_persistence'), patch.object(module, 'SupabaseRestClient', return_value=client), self.assertRaisesRegex(RuntimeError, 'missing ss_log_file'):
                module.persist_to_supabase(bench.args, self.payload(), datetime.datetime.now(), datetime.datetime.now(), bench.clients)
            payload = self.payload(); payload['config']['ss_log_file'] = '/fixture/retained-sampler.jsonl'; client = self.client()
            with patch.object(module, 'require_persistence'), patch.object(module, 'SupabaseRestClient', return_value=client), contextlib.redirect_stdout(io.StringIO()):
                module.persist_to_supabase(bench.args, payload, datetime.datetime.now(), datetime.datetime.now(), bench.clients)
            self.assertEqual(client.insert_emulated_parent_run.call_args.kwargs['snapshot_length_ms'], 250)
            self.assertEqual(client.insert_run.call_args.args[0]['log_path'], '/fixture/retained-sampler.jsonl')
            bench.args.ss_sample_interval_ms = 0; client = self.client()
            with patch.object(module, 'require_persistence'), patch.object(module, 'SupabaseRestClient', return_value=client), self.assertRaisesRegex(ValueError, 'snapshot_length_ms must be > 0'):
                module.persist_to_supabase(bench.args, payload, datetime.datetime.now(), datetime.datetime.now(), bench.clients)
            client.insert_emulated_parent_run.assert_not_called()

    def test_postgres_range_and_decimal_conversions_reject_overflow_and_nonfinite_values(self):
        for module in MODULES:
            for function, low, high in [(module.to_smallint, -32768, 32767), (module.to_int32, -(2**31), 2**31 - 1), (module.to_int64, -(2**63), 2**63 - 1)]:
                self.assertEqual(function(low, 'fixture'), low); self.assertEqual(function(high, 'fixture'), high)
                for value in [low - 1, high + 1]:
                    with self.subTest(module=module.__name__, value=value), self.assertRaisesRegex(ValueError, 'fixture must fit'): function(value, 'fixture')
            self.assertEqual(module.to_decimal_text('0.000125', 'fixture'), '0.000125')
            self.assertEqual(module.to_positive_smallint_milliseconds('0.001', 'fixture'), 1)
            for value in ['NaN', 'Infinity', None, 'invalid']:
                with self.assertRaisesRegex(ValueError, 'finite numeric'): module.to_decimal_text(value, 'fixture')
                with self.assertRaisesRegex(ValueError, 'finite numeric'): module.to_positive_smallint_milliseconds(value, 'fixture')
            for value, expected in [(0, 'must be > 0'), (-1, 'must be > 0'), (.0001, 'at least 1 ms')]:
                with self.assertRaisesRegex(ValueError, expected): module.to_positive_smallint_milliseconds(value, 'fixture')

    def test_final_json_line_is_required_to_be_an_object_and_diagnostics_cannot_masquerade_as_results(self):
        for module in MODULES:
            self.assertEqual(module.parse_single_json_line('diagnostic\n\n{"mode":"receiver"}\n', 'fixture'), {'mode': 'receiver'})
            for raw, message in [('', 'no JSON output'), ('{}\nbad', 'invalid JSON'), ('[]', 'must be an object')]:
                with self.assertRaisesRegex(RuntimeError, message): module.parse_single_json_line(raw, 'fixture')


if __name__ == '__main__':
    unittest.main()
