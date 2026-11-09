"""Fixed runner orchestration with synthetic Linux command and process edges.

No namespace, package installation, privileged command or measured traffic runs.
Synthetic tool receipts exercise failure retention, metric validity and cleanup.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import netem_iperf as runner
from test_experiment_environment import config


class NetemRunnerBoundaries(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='netem-runner-unit-'); self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)
        self.tool = self.root / 'synthetic-tool'; self.tool.write_bytes(b'Synthetic tool bytes, never executed\n'); self.config = config(); self.config['loss_seed'] = None
        self.argv = []; self.processes = []; self.readiness = 0; self.outcome = 'complete'; self.ping_output = '64 bytes: time=4.0 ms\n64 bytes: time<1.0 ms\n'; self.transfer = dict(end=dict(sum_received=dict(bytes=750000, seconds=3, bits_per_second=2000000), sum_sent={'retransmits': 0})); self.timeout_output = (b'original stdout\xff', 'original stderr'); self.seed_retained = True
        self.enterContext(patch.object(runner.platform, 'system', return_value='Linux')); self.enterContext(patch.object(runner.os, 'geteuid', return_value=0)); self.enterContext(patch.object(runner.os, 'getloadavg', return_value=(0.0, 1.0, 2.0))); self.enterContext(patch.object(runner.shutil, 'which', return_value=str(self.tool))); self.sleep = self.enterContext(patch.object(runner.time, 'sleep')); self.enterContext(patch.object(runner.subprocess, 'run', side_effect=self.command)); self.start = self.enterContext(patch.object(runner.subprocess, 'Popen', side_effect=self.process))
    def spec(self): return dict(config=copy.deepcopy(self.config), replication=2)
    def command(self, argv, **options):
        self.argv.append(argv); args = argv[4:] if argv[:3] == ['ip', 'netns', 'exec'] else argv; out = ''; err = ''; code = 0
        self.assertTrue(options['capture_output']); self.assertTrue(options['text']); self.assertEqual(options['env']['LC_ALL'], 'C'); self.assertEqual(options['env']['DEBIAN_FRONTEND'], 'noninteractive')
        if args[:2] == ['sysctl', '-n']: out = '16777216\n'
        elif args == ['iperf3', '--version']: out = 'Synthetic iperf3 version\n'
        elif args == ['tc', '-V']: out = 'Synthetic tc version\n'
        elif args == ['tc', 'qdisc', 'add', 'dev', 'lo', 'root', 'netem', 'help']: err = 'netem seed SEED'
        elif args[:4] == ['tc', '-j', 'qdisc', 'show']: out = json.dumps([dict(kind='netem', options={'seed': self.config['loss_seed'] if self.seed_retained else -1})])
        elif args[0] == 'ping': out = self.ping_output; code = 1 if not out else 0
        elif args[:2] == ['ss', '-ltn']:
            self.readiness += 1; out = '' if self.outcome in ['server-exit', 'readiness-timeout'] or self.readiness == 1 else 'LISTEN 0 128 *:5201\n'
        elif args[:2] == ['iperf3', '-c']:
            if self.outcome in ['client-timeout', 'cleanup-timeout']: raise subprocess.TimeoutExpired(argv, options['timeout'], output=self.timeout_output[0], stderr=self.timeout_output[1])
            out = json.dumps(self.transfer)
        elif args[:3] == ['ip', 'addr', 'add'] and self.outcome == 'setup-failure': code = 1; err = 'Original synthetic namespace address failure'
        return subprocess.CompletedProcess(argv, code, out, err)
    def process(self, argv, **options):
        self.assertEqual(argv[:3], ['ip', 'netns', 'exec']); self.assertEqual(argv[4:], ['iperf3', '-s', '-1', '-J']); self.assertTrue(options['text']); self.readiness = 0
        state = types.SimpleNamespace(returncode=0, killed=False)
        def communicate(timeout):
            if self.outcome == 'cleanup-timeout' and not state.killed: raise subprocess.TimeoutExpired(argv, timeout)
            return 'Original synthetic server stdout', 'Original synthetic server stderr'
        state.poll = Mock(return_value=1 if self.outcome == 'server-exit' else None); state.communicate = Mock(side_effect=communicate); state.terminate = Mock(); state.kill = Mock(side_effect=lambda: setattr(state, 'killed', True)); self.processes.append(state); return state
    def values(self, result, loss): return {row['metric']: row for row in result['observations'] if row['metadata']['loss_pct'] == loss}

    def test_completed_conditions_preserve_independent_metrics_source_hashes_commands_and_reverse_namespace_cleanup(self):
        spec = self.spec(); before = copy.deepcopy(spec); result = runner.run(spec); self.assertEqual(spec, before); self.assertEqual(len(result['observations']), 10); self.assertNotIn('failure', result['provenance']); self.assertEqual(result['provenance']['tool_binaries']['tc']['sha256'], hashlib.sha256(self.tool.read_bytes()).hexdigest()); self.assertIsNone(result['usage']['measured_charges_usd'])
        for loss in self.config['loss_levels_pct']:
            rows = self.values(result, loss); self.assertTrue(all(row['status'] == 'recorded' for row in rows.values())); self.assertEqual(rows['throughput_mbit_s']['value'], 2.0); self.assertEqual(rows['idle_rtt_ms']['value'], 2.5); self.assertEqual(rows['retransmits']['value'], 0); self.assertEqual(rows['idle_rtt_ms']['metadata']['ping_received'], 2); self.assertEqual(rows['idle_rtt_ms']['metadata']['requested_rtt_ms'], 40)
        created = [args[3] for args in self.argv if args[:3] == ['ip', 'netns', 'add']]; removed = [args[3] for args in self.argv if args[:3] == ['ip', 'netns', 'del']]; self.assertEqual(removed, list(reversed(created))); self.assertEqual(len(created), 3); self.assertEqual(self.start.call_count, 2); self.assertEqual(len(self.sleep.call_args_list), 2)
        self.assertTrue(all(not process.terminate.called and not process.kill.called for process in self.processes)); self.assertTrue(result['provenance']['environment_preparation']['qualified'])

    def test_no_icmp_replies_remain_missing_while_successful_transfer_zero_and_other_metrics_survive(self):
        self.ping_output = ''; self.transfer['end']['sum_received'].update(bytes=0, bits_per_second=0); result = runner.run(self.spec())
        for loss in self.config['loss_levels_pct']:
            rows = self.values(result, loss); self.assertIsNone(rows['idle_rtt_ms']['value']); self.assertEqual(rows['idle_rtt_ms']['status'], 'missing'); self.assertEqual(rows['idle_rtt_ms']['metadata']['ping_received'], 0); self.assertEqual(rows['throughput_mbit_s']['value'], 0); self.assertEqual(rows['throughput_mbit_s']['status'], 'recorded'); self.assertIsNone(rows['throughput_mbit_s']['reason'])

    def test_short_transfers_and_invalid_receiver_counters_keep_the_separately_recorded_rtt(self):
        for defect in ['short', 'inconsistent-rate', 'provider-error', 'missing-counter']:
            with self.subTest(defect=defect):
                original = copy.deepcopy(self.transfer)
                if defect == 'short': self.transfer['end']['sum_received'].update(seconds=1, bits_per_second=6000000)
                elif defect == 'inconsistent-rate': self.transfer['end']['sum_received']['bits_per_second'] = 1
                elif defect == 'provider-error': self.transfer['error'] = 'Original iperf error'
                else: self.transfer['end']['sum_received'].pop('bytes')
                result = runner.run(self.spec())
                for loss in self.config['loss_levels_pct']:
                    rows = self.values(result, loss); self.assertEqual(rows['idle_rtt_ms']['value'], 2.5); self.assertTrue(all(row['status'] == 'missing' and row['value'] is None and row['reason'].startswith('ValueError:') for metric, row in rows.items() if metric != 'idle_rtt_ms'))
                self.transfer = original

    def test_exited_and_unready_servers_never_start_the_measured_client_and_are_terminated(self):
        for outcome, reason in [('server-exit', 'exited before readiness'), ('readiness-timeout', 'readiness deadline exceeded')]:
            with self.subTest(outcome=outcome):
                self.outcome = outcome; self.argv = []; self.processes = []; result = runner.run(self.spec()); self.assertFalse(any('iperf3' in args and '-c' in args for args in self.argv)); self.assertTrue(all(process.terminate.called for process in self.processes)); self.assertTrue(any(reason in row['reason'] for row in result['observations'] if row['status'] == 'missing'))

    def test_timeout_receipts_preserve_partial_stdout_stderr_and_kill_only_when_cleanup_times_out(self):
        for outcome, output in [('client-timeout', (b'original stdout\xff', 'original stderr')), ('cleanup-timeout', (None, None))]:
            with self.subTest(outcome=outcome):
                self.outcome = outcome; self.timeout_output = output; self.processes = []; result = runner.run(self.spec()); failures = [row for row in result['provenance']['commands'] if row.get('error_kind') == 'timeout']; self.assertEqual(len(failures), 2); self.assertTrue(all(row['returncode'] is None for row in failures)); self.assertEqual(failures[0]['stdout'], 'original stdout\ufffd' if output[0] else ''); self.assertEqual(failures[0]['stderr'], output[1] or '')
                for process in self.processes: process.terminate.assert_called_once(); self.assertEqual(process.kill.call_count, 1 if outcome == 'cleanup-timeout' else 0)
                self.assertEqual(self.values(result, 0)['idle_rtt_ms']['value'], 2.5); self.assertIsNone(self.values(result, 0)['throughput_mbit_s']['value'])

    def test_the_kernel_must_retain_a_requested_seed_and_every_trial_records_its_condition_seed(self):
        self.config['loss_seed'] = 104729; result = runner.run(self.spec()); self.assertNotIn('failure', result['provenance']); expected = {str((104729 + 2 * 101 + index) % 2147483647) for index in range(2)}; shaped = [args[args.index('seed') + 1] for args in self.argv if 'seed' in args]; self.assertTrue(expected <= set(shaped)); self.assertIn('104729', shaped)
        self.seed_retained = False; self.start.reset_mock(); result = runner.run(self.spec()); self.assertIn('did not retain', result['provenance']['failure']); self.start.assert_not_called(); self.assertTrue(all(row['status'] == 'missing' for row in result['observations']))

    def test_malformed_configuration_values_are_rejected_before_commands_and_do_not_coerce_booleans_or_nonfinite_losses(self):
        for field, values in {'loss_levels_pct': [None, [], [0] * 2, [False], [float('nan')], [float('inf')], [-1], [31], ['0'], list(range(9))], 'loss_seed': [True, -1, 2147483648], 'rate_mbit': [True, 0, 1001], 'cca': ['bbr', None]}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    invalid = self.spec(); invalid['config'][field] = value
                    with self.assertRaises(ValueError): runner.run(invalid)
        self.assertEqual(self.argv, []); self.start.assert_not_called()

    def test_missing_configuration_fields_and_changed_frozen_implementations_stop_before_host_preparation(self):
        for invalid in [None, {}, dict(self.config, unexpected=True)]:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'Declare all'): runner.validate(invalid)
        spec = dict(self.spec(), implementation_sha256='f' * 64)
        with self.assertRaisesRegex(ValueError, 'implementation hash differs'): runner.run(spec)
        self.assertEqual(self.argv, []); self.start.assert_not_called()

    def test_unsupported_hosts_record_missing_observations_without_executing_any_linux_command(self):
        for system, uid in [('Darwin', 0), ('Linux', 501)]:
            with self.subTest(system=system), patch.object(runner.platform, 'system', return_value=system), patch.object(runner.os, 'geteuid', return_value=uid):
                result = runner.run(self.spec()); self.assertIn('network namespace privileges', result['provenance']['failure']); self.assertEqual(len(result['observations']), 10); self.assertTrue(all(row['status'] == 'missing' and row['value'] is None for row in result['observations']))
        self.assertEqual(self.argv, []); self.start.assert_not_called()

    def test_failed_namespace_configuration_retains_its_exit_and_cleans_up_every_created_namespace(self):
        self.outcome = 'setup-failure'; result = runner.run(self.spec()); self.assertIn('Linux experiment command failed: ip', result['provenance']['failure']); self.assertTrue(all(row['status'] == 'missing' for row in result['observations'])); self.start.assert_not_called()
        failures = [row for row in result['provenance']['commands'] if row['returncode'] == 1]; self.assertEqual(len(failures), 1); self.assertEqual(failures[0]['stderr'], 'Original synthetic namespace address failure'); self.assertEqual(sum(args[:3] == ['ip', 'netns', 'del'] for args in self.argv), 3)

    def test_a_later_host_failure_retains_completed_condition_measurements_and_only_fills_unrecorded_conditions(self):
        with patch.object(runner.os, 'getloadavg', side_effect=[(0, 1, 2), (0, 1, 2), OSError('Original host load observation unavailable')]): result = runner.run(self.spec())
        order = result['provenance']['condition_order']; self.assertEqual(len(result['observations']), 10); self.assertTrue(all(row['status'] == 'recorded' for row in self.values(result, order[0]).values())); self.assertTrue(all(row['status'] == 'missing' and row['value'] is None for row in self.values(result, order[1]).values())); self.assertIn('Original host load observation unavailable', result['provenance']['failure'])

    def test_upstream_archive_links_traversal_and_absolute_paths_are_rejected_after_hash_verification_before_any_build(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE), ('iproute2-6.8.0/link', tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode='w:xz') as capsule:
                    item = tarfile.TarInfo(name); item.type = kind; item.linkname = 'outside' if kind == tarfile.SYMTYPE else ''; capsule.addfile(item)
                raw = stream.getvalue(); execute = Mock(); response = io.BytesIO(raw)
                with patch.object(runner, 'IPROUTE_SHA256', hashlib.sha256(raw).hexdigest()), patch.object(runner.urllib.request, 'urlopen', return_value=response), self.assertRaisesRegex(ValueError, 'unsupported upstream archive member'): runner.install_seeded_tc(execute, {})
                self.assertEqual([call.args[0][0] for call in execute.call_args_list], ['apt-get', 'apt-get'])

    def test_the_file_entrypoint_emits_the_same_bounded_observation_envelope_to_its_configured_output(self):
        spec = self.root / 'spec.json'; output = self.root / 'output.json'; spec.write_text(json.dumps(self.spec()))
        with patch.dict(os.environ, JUMPSERVE_SPEC=str(spec), JUMPSERVE_OUTPUT=str(output)): runpy.run_path(runner.__file__, run_name='__main__')
        result = json.loads(output.read_bytes()); self.assertEqual(result['version'], 'network-observations-v1'); self.assertEqual(len(result['observations']), 10); self.assertTrue(all(row['status'] == 'recorded' for row in result['observations'])); self.assertTrue(output.read_bytes().endswith(b'\n'))


if __name__ == '__main__': unittest.main()
