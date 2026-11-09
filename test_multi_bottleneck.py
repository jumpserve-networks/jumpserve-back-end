"""Multi-bottleneck control-plane tests; no root, kernel changes or cloud traffic.

Command capture checks routing and persistence contracts, not physical accuracy.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import runpy
import unittest
from unittest.mock import MagicMock, patch

import netem_multi_bottleneck as bench


def clients():
    return [bench.ClientConfig(i, 'client' + str(i), ['cubic', 'bbr'][i], 10 + i, 1 + i, 5 * i, group=i, port=23000 + i, namespace='ns_client' + str(i), veth_client='veth_c' + str(i), ip='10.11.' + str(i) + '.1') for i in range(2)]


def args(**values):
    result = vars(bench.build_parser().parse_args(['--topology', 'dumbbell']))
    result.update(bottleneck_rates_mbit=[100, 50], bottleneck_buffers_kbytes=[125, 0], supabase_project_id='synthetic-project', supabase_service_role_key='synthetic-key')
    result.update(values); return argparse.Namespace(**result)


class TopologyContracts(unittest.TestCase):
    def test_kernel_commands_are_scoped_and_preserve_error_policy(self):
        with patch.object(bench.subprocess, 'run') as run:
            bench.run_cmd(['ip', 'netns', 'list'], check=False, capture=True)
            run.assert_called_once_with(['ip', 'netns', 'list'], check=False, text=True, capture_output=True)
        with patch.object(bench, 'run_cmd') as run:
            bench.ns_exec('ns_client', ['ip', 'addr'], capture=True)
            run.assert_called_once_with(['ip', 'netns', 'exec', 'ns_client', 'ip', 'addr'], capture=True)
        with patch.object(bench, 'ns_exec') as execute:
            bench.disable_offloads('ns_client', 'veth_c')
            self.assertEqual([c.args for c in execute.call_args_list], [('ns_client', ['ethtool', '-K', 'veth_c', flag, 'off']) for flag in ['gro', 'gso', 'tso']])

    def test_buffer_and_burst_units_include_explicit_zero_default_and_small_rate(self):
        self.assertEqual(bench.tbf_burst_bytes(100), 1500000); self.assertEqual(bench.tbf_burst_bytes(0.001), 1600)
        self.assertEqual(bench.tbf_limit_bytes(100, 125), 128000)
        self.assertEqual(bench.tbf_limit_bytes(0.001, 0), 6400)

    def test_base_topology_primitives_never_modify_host_routing_and_cleanup_is_best_effort(self):
        topology = bench.TopologyBuilder(clients(), [100, 50], [125, 0], loss_pct=2)
        with self.assertRaises(NotImplementedError): topology.setup()
        with patch.object(bench, 'run_cmd') as run, patch.object(bench, 'ns_exec') as execute:
            topology._create_ns('ns_private'); topology._create_veth_pair('left', 'right'); topology._assign_veth('left', 'ns_private', '10.0.0.1/24')
            topology._add_route('ns_private', '10.1.0.0/24', '10.0.0.254'); topology._add_default_route('ns_private', '10.0.0.254'); topology._enable_forwarding('ns_private')
            topology._add_netem('ns_private', 'left', 12.5); topology._add_tbf('ns_private', 'left', 50, 0); topology.cleanup()
            self.assertEqual(run.call_args.args[0], ['ip', 'netns', 'del', 'ns_private']); self.assertFalse(run.call_args.kwargs['check'])
            commands = [c.args[1] for c in execute.call_args_list]
            self.assertIn(['ip', 'route', 'add', '10.1.0.0/24', 'via', '10.0.0.254'], commands)
            self.assertIn(['tc', 'qdisc', 'add', 'dev', 'left', 'root', 'netem', 'delay', '12.5ms', 'loss', '2%'], commands)
            self.assertIn(['tc', 'qdisc', 'add', 'dev', 'left', 'root', 'tbf', 'rate', '50mbit', 'burst', '750000', 'limit', '3000000'], commands)

    def test_dumbbell_has_two_distinct_bottlenecks_and_group_sender_routes(self):
        cs = clients(); topology = bench.DumbbellTopology(cs, [100, 50], [125, 0])
        with patch.object(bench, 'run_cmd') as run, patch.object(bench, 'ns_exec') as execute: topology.setup(); topology.cleanup()
        self.assertEqual(len(topology.bottlenecks), 2); self.assertEqual([b.rate_mbit for b in topology.bottlenecks], [100, 50])
        self.assertEqual([c.ip for c in cs], ['10.11.0.1', '10.22.0.1'])
        deletes = [c.args[0][-1] for c in run.call_args_list if c.args[0][:3] == ['ip', 'netns', 'del']]
        self.assertCountEqual(deletes, ['ns_sender0', 'ns_sender1', 'ns_router', 'ns_client0', 'ns_client1'])
        commands = [c.args for c in execute.call_args_list]
        self.assertIn(('ns_sender0', ['ip', 'route', 'add', 'default', 'via', '10.10.0.254']), commands)
        self.assertIn(('ns_sender1', ['ip', 'route', 'add', 'default', 'via', '10.20.0.254']), commands)
        netem = [c for c in commands if c[1][:2] == ['tc', 'qdisc'] and 'netem' in c[1]]
        self.assertEqual(len(netem), 2); self.assertTrue(all('loss' not in c[1] for c in netem))

    def test_topology_link_counts_are_validated_before_creating_namespaces(self):
        for klass in [bench.DumbbellTopology, bench.ParkingLotTopology]:
            for rates, buffers in [([100], [125, 125]), ([100, 50], [125])]:
                with self.subTest(topology=klass.__name__, rates=rates, buffers=buffers), patch.object(bench, 'run_cmd') as run:
                    with self.assertRaises(AssertionError): klass(clients(), rates, buffers).setup()
                    run.assert_not_called()

    def test_parking_lot_records_client_addresses_and_routes(self):
        cs = clients(); topology = bench.ParkingLotTopology(cs, [100, 50], [125, 0])
        with patch.object(bench, 'run_cmd'), patch.object(bench, 'ns_exec') as execute: topology.setup()
        self.assertEqual([c.ip for c in cs], ['10.10.1.1', '10.10.2.1'])
        self.assertEqual(topology.namespaces, ['ns_sender', 'ns_relay', 'ns_router', 'ns_client0', 'ns_client1'])
        self.assertIn(('ns_sender', ['ip', 'route', 'add', 'default', 'via', '10.10.0.254']), [c.args for c in execute.call_args_list])
        self.assertEqual([(b.src_ns, b.dst_ns, b.rate_mbit) for b in topology.bottlenecks], [('ns_sender', 'ns_relay', 100), ('ns_relay', 'ns_router', 50)])
        shaping = [c.args for c in execute.call_args_list if c.args[1][:2] == ['tc', 'qdisc'] and 'tbf' in c.args[1]]
        self.assertEqual(len(shaping), 2); self.assertEqual(shaping[1][0], 'ns_relay'); self.assertIn('50mbit', shaping[1][1])
        self.assertIn(('ns_router', ['ip', 'route', 'add', '10.10.0.0/24', 'via', '10.11.0.1']), [c.args for c in execute.call_args_list])


class PersistenceContracts(unittest.TestCase):
    def client(self, ingest=False):
        with patch.object(bench, 'ingest_settings', return_value=ingest), patch.object(bench, 'IngestBuffer') as buffer:
            result = bench.SupabaseClient('synthetic-project', 'synthetic-key', timeout=4)
        return result, buffer.return_value

    def test_direct_transport_keeps_key_in_header_and_preserves_response(self):
        client, _ = self.client(); response = MagicMock(); response.__enter__.return_value.read.return_value = b'[{"id":7}]'
        with patch.object(bench.urllib.request, 'urlopen', return_value=response) as transport:
            self.assertEqual(client._request('POST', 'emulated_runs', {'client_number': 1}), [{'id': 7}])
            request = transport.call_args.args[0]; self.assertEqual(request.full_url, 'https://synthetic-project.supabase.co/rest/v1/emulated_runs')
            self.assertEqual(json.loads(request.data), {'client_number': 1}); self.assertEqual(request.get_header('Apikey'), 'synthetic-key'); self.assertEqual(transport.call_args.kwargs, {'timeout': 4})
            client._request('GET', 'emulated_runs?select=id'); self.assertIsNone(transport.call_args.args[0].data)

    def test_ingest_transport_preserves_table_query_payload_and_does_not_open_network(self):
        client, buffer = self.client(True); buffer.request.return_value = [{'id': 7}]
        with patch.object(bench.urllib.request, 'urlopen', side_effect=AssertionError('Network forbidden')):
            self.assertEqual(client._request('GET', 'emulated_runs?select=id'), [{'id': 7}])
        buffer.request.assert_called_once_with('GET', 'emulated_runs', 'select=id', None)

    def test_algorithm_lookup_insert_and_cache_share_recorded_identity(self):
        client, _ = self.client()
        with patch.object(client, '_request', side_effect=[[{'id': 7}], [], [{'id': 8}]]) as request:
            self.assertEqual(client.get_or_create_algorithm('cubic'), 7); self.assertEqual(client.get_or_create_algorithm('cubic'), 7)
            self.assertEqual(client.get_or_create_algorithm('bbr'), 8); self.assertEqual(len(request.call_args_list), 3)
            self.assertEqual(request.call_args.args, ('POST', 'congestion_control_algorithms', {'name': 'bbr'}))

    def test_parent_and_client_rows_preserve_units_metadata_and_zero(self):
        client, _ = self.client()
        with patch.object(client, '_request', return_value=[{'id': 10}]) as request:
            self.assertEqual(client.insert_parent_run(2, 50, 0, 100), 10); row = request.call_args.args[2]
            self.assertEqual(row, {'number_of_clients': 2, 'bottleneck_rate_megabit': '50', 'queue_buffer_size_kilobyte': '0', 'snapshot_length_ms': 100})
            client.insert_parent_run(2, 50, 0, 100, topology='dumbbell', topology_config={'client_groups': [1, 1]}, tags=['fixture'], notes='Synthetic', experiment_name='Unit')
            row = request.call_args.args[2]; self.assertEqual(row['tags'], ['fixture']); self.assertEqual(row['experiment_name'], 'Unit'); self.assertEqual(row['topology_config'], {'client_groups': [1, 1]})
            cs = clients()[0]; client.insert_run(10, cs, 7, 0)
            self.assertEqual(request.call_args.args[2], {'emulated_parent_run_id': 10, 'client_number': 1, 'delay_added': 10, 'congestion_control_algorithm_id': 7, 'client_file_size_megabytes': 1, 'client_start_delay_ms': 0, 'flow_completion_time_ms': 0})
            client.insert_snapshot_stats([{'snapshot_index': 0}]); self.assertEqual(request.call_args.args, ('POST', 'emulated_snapshot_stats', [{'snapshot_index': 0}]))

    def test_persist_uses_matching_parent_client_ids_and_counter_deltas(self):
        cs = clients(); configuration = args(client_groups='1,1', experiment_tags='unit,synthetic', experiment_name='fixture', experiment_notes='Unit evidence only')
        runner = bench.MultiBottleneckBench(configuration, cs, MagicMock()); runner.client_results = {'client0': {'flow_completion_time_ms': 17}}
        runner.snapshots = [dict(snapshot_index=0, elapsed_microseconds=100000, receivers={'client0': {'rx_bytes': 1000}, 'client1': {'rx_bytes': 2000}}), dict(snapshot_index=1, elapsed_microseconds=200000, receivers={'client0': {'rx_bytes': 3000}, 'client1': {'rx_bytes': 2000}}), dict(snapshot_index=2, elapsed_microseconds=200000, receivers={'client0': {'rx_bytes': 2000}})]
        sb = MagicMock(); sb.ingest = None; sb.insert_parent_run.return_value = 41; sb.get_or_create_algorithm.side_effect = [7, 8]; sb.insert_run.side_effect = [61, 62]
        with patch.object(bench, 'SupabaseClient', return_value=sb), contextlib.redirect_stdout(io.StringIO()) as output: runner.persist_to_supabase({})
        self.assertEqual(json.loads(output.getvalue()), {'parent_run_id': 41, 'topology': 'dumbbell'})
        self.assertEqual(sb.insert_parent_run.call_args.kwargs['topology_config']['client_groups'], '1,1')
        self.assertEqual([c.args[3] for c in sb.insert_run.call_args_list], [17, 0])
        batches = [c.args[0] for c in sb.insert_snapshot_stats.call_args_list]
        self.assertEqual([[v['emulated_run_id'] for v in rows] for rows in batches], [[61, 62]] * 3)
        self.assertEqual([[v['megabits_per_second'] for v in rows] for rows in batches], [['0.08', '0.16'], ['0.16', '0'], ['0', '0']])

    def test_persist_ingest_commit_supplies_authoritative_parent_identity_even_without_snapshots(self):
        runner = bench.MultiBottleneckBench(args(bottleneck_rates_mbit=[], bottleneck_buffers_kbytes=[]), [], MagicMock())
        runner.snapshots = [dict(snapshot_index=0, elapsed_microseconds=100000)]
        sb = MagicMock(); sb.ingest.commit.return_value = {'parent_run_id': 92}
        with patch.object(bench, 'SupabaseClient', return_value=sb), contextlib.redirect_stdout(io.StringIO()) as output: runner.persist_to_supabase({})
        self.assertEqual(json.loads(output.getvalue())['parent_run_id'], 92); sb.insert_snapshot_stats.assert_not_called()
        self.assertEqual((sb.insert_parent_run.call_args.kwargs['rate_mbit'], sb.insert_parent_run.call_args.kwargs['buffer_kb']), (100, 0))


class TransferContracts(unittest.TestCase):
    def test_subprocess_sender_uses_each_topology_group_and_preserves_result_json(self):
        cs = clients(); topologies = [bench.TopologyBuilder(cs, [100, 50], [125, 0]), bench.DumbbellTopology(cs, [100, 50], [125, 0])]
        custom = MagicMock(); custom.sender_ns_for_group.return_value = 'custom-sender'; topologies.append(custom)
        for topology, namespace in zip(topologies, ['ns_sender', 'ns_sender1', 'custom-sender']):
            runner = bench.MultiBottleneckBench(args(), cs, topology)
            with self.subTest(namespace=namespace), patch.object(bench.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"bytes_sent":2000000,"duration_ms":20,"rtt_ms":1.5}', '')) as run:
                self.assertEqual(runner._spawn_sender(cs[1])['bytes_sent'], 2000000); self.assertEqual(run.call_args.args[0][:4], ['ip', 'netns', 'exec', namespace]); self.assertIn('target = 2000000', run.call_args.args[0][-1])
                self.assertEqual(run.call_args.kwargs['timeout'], 300)

    def test_subprocess_receiver_preserves_namespace_port_and_ready_file_contract(self):
        cs = clients(); runner = bench.MultiBottleneckBench(args(), cs, MagicMock())
        with patch.object(bench.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"bytes_received":100,"duration_ms":8}', '')) as run:
            result = runner._spawn_receiver(cs[0], '/synthetic/ready')
            self.assertEqual(result, {'bytes_received': 100, 'duration_ms': 8}); command = run.call_args.args[0]
            self.assertEqual(command[:4], ['ip', 'netns', 'exec', 'ns_client0']); self.assertIn('23000', command[-1]); self.assertIn('/synthetic/ready', command[-1])

    def test_failed_or_empty_processes_cannot_become_zero_valued_measurements(self):
        cs = clients(); runner = bench.MultiBottleneckBench(args(), cs, bench.TopologyBuilder(cs, [100, 50], [125, 0]))
        for process in [subprocess.CompletedProcess([], 1, '', 'actual failure'), subprocess.CompletedProcess([], 0, ' ', '')]:
            with self.subTest(process=process), patch.object(bench.subprocess, 'run', return_value=process):
                with self.assertRaisesRegex(RuntimeError, 'Sender process failed'): runner._spawn_sender(cs[0])
                with self.assertRaisesRegex(RuntimeError, 'Receiver process failed'): runner._spawn_receiver(cs[0], '/synthetic/ready')

    def test_snapshot_retains_each_client_identity_and_rx_counter(self):
        cs = clients(); runner = bench.MultiBottleneckBench(args(), cs, MagicMock())
        for values, expected in [([subprocess.CompletedProcess([], 0, '123\n', ''), subprocess.CompletedProcess([], 0, '\n', '')], [123, 0]), ([OSError('missing interface'), ValueError('counter failure')], [0, 0])]:
            with patch.object(bench, 'ns_exec', side_effect=values): result = runner._collect_snapshot(4, 500000)
            self.assertEqual((result['snapshot_index'], result['elapsed_microseconds']), (4, 500000)); self.assertEqual([result['receivers'][c.name]['rx_bytes'] for c in cs], expected)


class Orchestration(unittest.TestCase):
    def test_cleanup_runs_on_success_and_failure_unless_explicitly_retained(self):
        for keep in [False, True]:
            for fail in [False, True]:
                topology = MagicMock(); runner = bench.MultiBottleneckBench(args(keep_namespaces=keep), clients(), topology)
                with patch.object(runner, '_run_benchmark', side_effect=RuntimeError('synthetic failure') if fail else None, return_value={'clients': {}}):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'synthetic failure'): runner.run()
                    else: self.assertEqual(runner.run(), {'clients': {}})
                topology.setup.assert_called_once(); self.assertEqual(topology.cleanup.call_count, int(not keep))

    def test_partially_created_topology_is_cleaned_when_setup_fails(self):
        topology = MagicMock(); topology.setup.side_effect = OSError('synthetic qdisc rejection')
        runner = bench.MultiBottleneckBench(args(), clients(), topology)
        with patch.object(runner, '_run_benchmark') as transfer, self.assertRaisesRegex(OSError, 'qdisc rejection'): runner.run()
        topology.cleanup.assert_called_once(); transfer.assert_not_called()
        topology = bench.TopologyBuilder(clients(), [100, 50], [125, 0])
        with patch.object(bench, 'run_cmd') as run, patch.object(bench, 'ns_exec', side_effect=OSError('loopback rejected')):
            with self.assertRaises(OSError): topology._create_ns('ns_partial')
            topology.cleanup(); self.assertEqual(run.call_args.args[0], ['ip', 'netns', 'del', 'ns_partial'])

    def test_threaded_orchestration_waits_for_readiness_collects_snapshots_and_joins_every_client(self):
        cs = clients(); runner = bench.MultiBottleneckBench(args(), cs, MagicMock()); threads = []
        class Thread:
            def __init__(self, target): self.target = target; self.polls = 0; self.joined = False; threads.append(self)
            def start(self): self.target()
            def is_alive(self): self.polls += 1; return not self.joined and self.polls == 1
            def join(self, timeout): self.joined = True
        with tempfile.TemporaryDirectory() as tmp, patch.object(bench.tempfile, 'mkdtemp', return_value=tmp), patch.object(bench.threading, 'Thread', Thread), patch.object(bench.time, 'monotonic', side_effect=[0, 1, 1.1, 1.2]), patch.object(bench.time, 'sleep'):
            def receive(client, ready): Path(ready).touch(); return {'bytes_received': 1000 + client.index}
            with patch.object(runner, '_spawn_receiver', side_effect=receive), patch.object(runner, '_spawn_sender', side_effect=lambda client: {'bytes_sent': 1000 + client.index, 'duration_ms': 12, 'rtt_ms': 3.5}), patch.object(runner, '_collect_snapshot', side_effect=lambda i, t: {'snapshot_index': i, 'elapsed_microseconds': t}) as snapshots:
                result = runner._run_benchmark()
        self.assertEqual(result['num_clients'], 2); self.assertTrue(all(t.joined for t in threads)); self.assertEqual(len(result['snapshots']), 2)
        self.assertEqual(result['clients']['client1']['bytes_received'], 1001); self.assertEqual(result['clients']['client0']['final_rtt_ms'], 3.5); self.assertIs(runner.client_results, result['clients'])

    def test_failed_receiver_prevents_any_sender_or_successful_result(self):
        cs = clients(); runner = bench.MultiBottleneckBench(args(), cs, MagicMock())
        class Thread:
            def __init__(self, target): self.target = target
            def start(self): self.target()
        with tempfile.TemporaryDirectory() as tmp, patch.object(bench.tempfile, 'mkdtemp', return_value=tmp), patch.object(bench.threading, 'Thread', Thread), patch.object(runner, '_spawn_receiver', side_effect=RuntimeError('actual child failure')), patch.object(runner, '_spawn_sender') as send:
            with self.assertRaisesRegex(RuntimeError, 'Transfer failed') as error: runner._run_benchmark()
            self.assertIn('actual child failure', str(error.exception.__cause__)); send.assert_not_called(); self.assertEqual(runner.client_results, {})

    def test_receiver_readiness_deadline_prevents_senders_without_replaying_child(self):
        cs = clients(); runner = bench.MultiBottleneckBench(args(), cs, MagicMock())
        class Thread:
            def __init__(self, target): self.target = target
            def start(self): self.target()
        with tempfile.TemporaryDirectory() as tmp, patch.object(bench.tempfile, 'mkdtemp', return_value=tmp), patch.object(bench.threading, 'Thread', Thread), patch.object(runner, '_spawn_receiver', return_value={}) as receive, patch.object(runner, '_spawn_sender') as send, patch.object(bench.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(RuntimeError, 'readiness timed out'): runner._run_benchmark()
            self.assertEqual(receive.call_count, 2); send.assert_not_called()

    def test_sender_thread_exception_is_not_swallowed_and_receiver_must_finish(self):
        for fail, unfinished in [(True, False), (False, True)]:
            cs = clients(); runner = bench.MultiBottleneckBench(args(), cs, MagicMock())
            class Thread:
                def __init__(self, target): self.target = target; self.receiver = 'receiver' in target.__name__
                def start(self): self.target()
                def is_alive(self): return self.receiver and unfinished
                def join(self, timeout): pass
            def receive(client, ready): Path(ready).touch(); return {'bytes_received': 100}
            def send(client):
                if fail: raise OSError('sender child failed')
                return {'bytes_sent': 100}
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp, patch.object(bench.tempfile, 'mkdtemp', return_value=tmp), patch.object(bench.threading, 'Thread', Thread), patch.object(runner, '_spawn_receiver', side_effect=receive), patch.object(runner, '_spawn_sender', side_effect=send):
                with self.assertRaisesRegex(RuntimeError, 'Transfer failed' if fail else 'Receiver did not finish'): runner._run_benchmark()

    def test_readiness_waits_without_starting_senders_until_original_marker_exists(self):
        cs = clients()[:1]; runner = bench.MultiBottleneckBench(args(), cs, MagicMock()); marker = []
        class Thread:
            def __init__(self, target): self.target = target
            def start(self): self.target()
            def is_alive(self): return False
            def join(self, timeout): pass
        def receive(client, ready): marker.append(Path(ready)); return {'bytes_received': 100}
        def sleep(seconds): self.assertEqual(seconds, 0.05); marker[0].touch()
        def send(client): self.assertTrue(marker[0].exists()); return {'bytes_sent': 100}
        with tempfile.TemporaryDirectory() as tmp, patch.object(bench.tempfile, 'mkdtemp', return_value=tmp), patch.object(bench.threading, 'Thread', Thread), patch.object(runner, '_spawn_receiver', side_effect=receive), patch.object(runner, '_spawn_sender', side_effect=send), patch.object(bench.time, 'sleep', side_effect=sleep):
            self.assertEqual(runner._run_benchmark()['clients']['client0']['bytes_sent'], 100)


class CommandLine(unittest.TestCase):
    def test_csv_inputs_preserve_single_broadcast_trim_order_padding_and_defaults(self):
        self.assertEqual(bench.parse_csv_float('1.5', 3), [1.5] * 3); self.assertEqual(bench.parse_csv_float(' 1, 2, 3 ', 2), [1, 2])
        self.assertEqual(bench.parse_csv_str(' bbr ', 2), ['bbr'] * 2); self.assertEqual(bench.parse_csv_str('cubic,bbr', 4), ['cubic', 'bbr', 'bbr', 'bbr'])
        self.assertEqual(bench.parse_csv_str('', 2, 'cubic'), ['cubic', 'cubic'])

    def test_cli_checks_privilege_and_persistence_before_starting_kernel_work(self):
        with patch.object(bench.os, 'geteuid', return_value=1000), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error: bench.main()
        self.assertEqual(error.exception.code, 1)
        with patch.object(bench.os, 'geteuid', return_value=0), patch.object(sys, 'argv', ['multi', '--topology', 'parking-lot']), patch.object(bench, 'require_persistence', side_effect=RuntimeError('missing persistence')), patch.object(bench, 'ParkingLotTopology') as topology:
            with self.assertRaisesRegex(RuntimeError, 'missing persistence'): bench.main()
            topology.assert_not_called()

    def test_script_entrypoint_rejects_unprivileged_execution_before_network_changes(self):
        with patch.object(bench.os, 'geteuid', return_value=1000), patch.object(bench.subprocess, 'run') as command, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            runpy.run_path(bench.__file__, run_name='__main__')
        self.assertEqual(error.exception.code, 1); command.assert_not_called()

    def test_cli_builds_selected_topology_and_persists_the_actual_result(self):
        for topology in ['parking-lot', 'dumbbell']:
            argv = ['multi', '--topology', topology, '--num-clients', '2', '--client-groups', '1,1,4', '--client-names', 'Alice,Bob', '--client-ccas', 'cubic,bbr', '--client-delays-ms', '10,20']
            with self.subTest(topology=topology), patch.object(sys, 'argv', argv), patch.object(bench.os, 'geteuid', return_value=0), patch.object(bench, 'require_persistence'), patch.object(bench, 'MultiBottleneckBench') as runner, contextlib.redirect_stderr(io.StringIO()):
                runner.return_value.run.return_value = {'clients': {'synthetic': {}}}; bench.main()
                configuration, cs, built = runner.call_args.args
                self.assertIsInstance(built, bench.ParkingLotTopology if topology == 'parking-lot' else bench.DumbbellTopology)
                self.assertEqual([c.name for c in cs], ['Alice', 'Bob']); self.assertEqual([c.group for c in cs], [0, 0] if topology == 'parking-lot' else [0, 1])
                self.assertEqual(configuration.bottleneck_rates_mbit, [100, 50]); runner.return_value.persist_to_supabase.assert_called_once_with(runner.return_value.run.return_value)
        with patch.object(sys, 'argv', ['multi', '--topology', 'parking-lot', '--num-clients', '1']), patch.object(bench.os, 'geteuid', return_value=0), patch.object(bench, 'require_persistence'), patch.object(bench, 'MultiBottleneckBench') as runner, contextlib.redirect_stderr(io.StringIO()):
            bench.main(); self.assertEqual(runner.call_args.args[1][0].name, 'client0')


if __name__ == '__main__': unittest.main()
