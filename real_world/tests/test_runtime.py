"""Offline EC2 runner tests: local files, synthetic command output, no root actions."""
import contextlib
import copy
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as specification
import runtime


def settings():
    placement = dict(region='us-east-1', zone_id='use1-az1', instance_type='t3.medium')
    value = dict(server=placement.copy(), bottleneck=placement.copy(), receivers=[placement.copy(), placement.copy()],
                 cca='bbr', duration_seconds=1, rate_mbit=10, buffer_kbytes=125, notes='Unit fixture')
    nodes = specification.nodes_for(value)
    for index, node in enumerate(nodes):
        node.update(public_key=f'synthetic-public-{index}', public_ip=f'192.0.2.{index + 1}',
                    instance_id=f'i-fixture-{index}', image_id='ami-fixture')
    return dict(value, nodes=nodes, node=nodes[0], start_epoch=100, job_id='synthetic-job',
                runtime_revision='synthetic-revision', upload_url='https://example.test/synthetic-upload')


class RunnerRuntime(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='jumpserve-real-world-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(runtime, 'ROOT', self.root))
        self.config = settings()
        (self.root / 'private-key').write_text('synthetic-private-key\n')
        (self.root / 'preflight.json').write_text('{"route":"overlay"}')

    @staticmethod
    def commands(*args, **kwargs):
        if args[:2] == ('iperf3', '--version'):
            return 'iperf3 synthetic fixture'
        if args[:3] == ('sysctl', '-n', 'net.ipv4.tcp_available_congestion_control'):
            return 'bbr cubic'
        if args[:3] == ('sysctl', '-n', 'net.ipv4.tcp_congestion_control'):
            return 'bbr'
        if args[0] == 'timedatectl':
            return 'yes'
        if args[:3] == ('ip', '-j', 'route'):
            return '[{"dev":"wg0"}]'
        if args[:2] == ('tc', '-j'):
            return '[{"kind":"bfifo","bytes":0}]'
        return 'synthetic command output'

    def test_command_wrapper_bounds_diagnostics_and_preserves_check_flag_and_input(self):
        with patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, ' output \n', '')) as run:
            self.assertEqual(runtime.run('wg', 'pubkey', input='private'), 'output')
            run.assert_called_once_with(('wg', 'pubkey'), check=False, input='private', text=True, capture_output=True)
        with patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, 'output', 'x' * 2000)):
            with self.assertRaises(RuntimeError) as error:
                runtime.run('synthetic')
            self.assertEqual(str(error.exception), 'synthetic: ' + 'x' * 1000)
            self.assertEqual(runtime.run('synthetic', check=False), 'output')

    def test_prepare_generates_private_key_once_and_reuses_it(self):
        (self.root / 'private-key').unlink()
        with patch.object(runtime, 'run', side_effect=['synthetic-generated-key', 'synthetic-public-key', 'same-public-key']) as run:
            self.assertEqual(runtime.prepare()['public_key'], 'synthetic-public-key')
            self.assertEqual((self.root / 'private-key').read_text(), 'synthetic-generated-key\n')
            self.assertEqual((self.root / 'private-key').stat().st_mode & 0o777, 0o600)
            self.assertEqual(runtime.prepare()['public_key'], 'same-public-key')
            self.assertEqual(sum(call.args == ('wg', 'genkey') for call in run.call_args_list), 1)

    def test_each_role_configures_overlay_only_services_and_only_the_hub_forwards(self):
        for role in ('server', 'bottleneck', 'receiver'):
            with self.subTest(role=role):
                value = copy.deepcopy(self.config)
                value['node'] = next(node for node in value['nodes'] if node['role'] == role)
                with patch.object(runtime, 'prepare') as prepare, patch.object(runtime, 'run', side_effect=self.commands) as run:
                    result = runtime.configure(value)
                self.assertTrue(result['configured']); prepare.assert_called_once()
                calls = [call.args for call in run.call_args_list]
                self.assertIn(('iptables', '-P', 'FORWARD', 'DROP'), calls)
                self.assertIn(('ip', 'link', 'set', 'wg0', 'mtu', '1380', 'up'), calls)
                self.assertEqual((self.root / 'wg.conf').stat().st_mode & 0o777, 0o600)
                listeners = [call for call in calls if call[:2] == ('iperf3', '-s')]
                self.assertEqual(len(listeners), 2 if role == 'server' else 0)
                for listener in listeners:
                    self.assertEqual(listener[listener.index('-B') + 1], '10.254.0.2')
                forwards = [call for call in calls if call[:3] == ('iptables', '-A', 'FORWARD')]
                self.assertEqual(len(forwards), 4 if role == 'bottleneck' else 0)
                self.assertEqual(any('bfifo' in call for call in calls), role == 'bottleneck')

    def test_missing_cca_and_unsynchronized_clock_fail_configuration(self):
        for problem, message in [('cca', 'CCA is unavailable'), ('clock', 'Clock synchronization')]:
            def run(*args, **kwargs):
                if problem == 'cca' and args[:3] == ('sysctl', '-n', 'net.ipv4.tcp_available_congestion_control'):
                    return 'cubic'
                if problem == 'clock' and args[0] == 'timedatectl':
                    return 'no'
                return self.commands(*args, **kwargs)
            with self.subTest(problem=problem), patch.object(runtime, 'prepare'), patch.object(runtime, 'run', side_effect=run), self.assertRaisesRegex(RuntimeError, message):
                runtime.configure(self.config)

    def test_preflight_rejects_underlay_routes_and_records_overlay_routes(self):
        for role, destination in [('server', '10.254.0.1'), ('receiver', '10.254.0.2')]:
            value = copy.deepcopy(self.config)
            value['node'] = next(node for node in value['nodes'] if node['role'] == role)
            with patch.object(runtime, 'run', side_effect=['synthetic ping', '[{"dev":"wg0"}]']) as run:
                self.assertEqual(runtime.preflight(value), {'ready': True})
                self.assertEqual(run.call_args_list[0].args[-1], destination)
                self.assertEqual(json.loads((self.root / 'preflight.json').read_text())['route'][0]['dev'], 'wg0')
        with patch.object(runtime, 'run', side_effect=['synthetic ping', '[{"dev":"eth0"}]']), self.assertRaisesRegex(RuntimeError, 'does not use the bottleneck'):
            runtime.preflight(self.config)

    def test_upload_retries_transport_failures_with_bounded_backoff(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(runtime.urllib.request, 'urlopen', side_effect=[OSError('unavailable'), response]) as send, patch.object(runtime.time, 'sleep') as sleep:
            runtime.upload('https://example.test/synthetic-upload', {'value': 0})
            self.assertEqual(send.call_count, 2)
            sleep.assert_called_once_with(1)
            self.assertEqual(json.loads(send.call_args.args[0].data), {'value': 0})
        with patch.object(runtime.urllib.request, 'urlopen', side_effect=OSError('unavailable')) as send, patch.object(runtime.time, 'sleep') as sleep:
            with self.assertRaisesRegex(OSError, 'unavailable'):
                runtime.upload('https://example.test/synthetic-upload', {})
            self.assertEqual(send.call_count, 5)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8])

    def measure(self, *, role='server', iperf=None, returncode=0, stderr='', effective='bbr', missed=False):
        value = copy.deepcopy(self.config)
        value['node'] = next(node for node in value['nodes'] if node['role'] == role)
        report = iperf if iperf is not None else {'end': {'sum_received': {'bytes': 1000}, 'sender_tcp_congestion': 'bbr'}}
        def commands(*args, **kwargs):
            if args[:3] == ('sysctl', '-n', 'net.ipv4.tcp_congestion_control'):
                return effective
            return self.commands(*args, **kwargs)
        ticks = iter([103, 115] if missed else [100, 100, 100, 101, 115, 115])
        with patch.object(runtime, 'run', side_effect=commands), \
                patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], returncode, json.dumps(report), stderr)) as transfer, \
                patch.object(runtime.time, 'time', side_effect=lambda: next(ticks)), \
                patch.object(runtime.time, 'sleep'), patch.object(runtime.os, 'getloadavg', return_value=(0, 0, 0)), patch.object(runtime, 'upload') as upload:
            runtime.measure(value)
            recorded = json.loads((self.root / 'result.json').read_text())
            upload.assert_called_once()
            self.assertEqual(upload.call_args.args[0], value['upload_url'])
            self.assertEqual(json.loads(json.dumps(upload.call_args.args[1])), recorded)
            return value, recorded, transfer

    def test_measurements_preserve_overlay_provenance_and_original_iperf_results(self):
        for role in ('server', 'bottleneck', 'receiver'):
            with self.subTest(role=role):
                config, result, transfer = self.measure(role=role)
                self.assertTrue(result['success'])
                self.assertEqual(result['configuration']['buffer_kbytes'], 125)
                self.assertEqual(result['placement']['instance_id'], config['node']['instance_id'])
                self.assertEqual(result['runtime_revision'], 'synthetic-revision')
                self.assertEqual(result['samples'][0]['qdisc'][0]['bytes'], 0)
                self.assertEqual(result['preflight'], {'route': 'overlay'})
                self.assertEqual(transfer.call_count, 1 if role == 'receiver' else 0)
                if role == 'receiver':
                    command = transfer.call_args.args[0]
                    self.assertEqual(command[command.index('-B') + 1], config['node']['overlay_ip'])
                    self.assertEqual(transfer.call_args.kwargs['timeout'], 46)
                    self.assertEqual(result['iperf']['end']['sum_received']['bytes'], 1000)

    def test_failed_experiments_still_write_and_upload_an_honest_failure_report(self):
        for change, expected in [({'missed': True}, 'Missed the common start barrier'),
                                 ({'effective': 'cubic'}, 'Effective server CCA differs'),
                                 ({'role': 'receiver', 'returncode': 1, 'stderr': 'synthetic iperf failure'}, 'synthetic iperf failure'),
                                 ({'role': 'receiver', 'iperf': {'error': 'remote failure'}}, 'remote failure'),
                                 ({'role': 'receiver', 'iperf': {}}, 'No test bytes received'),
                                 ({'role': 'receiver', 'iperf': {'end': {'sum_received': {'bytes': 1}, 'sender_tcp_congestion': 'cubic'}}}, 'different congestion control')]:
            with self.subTest(change=change):
                _, result, transfer = self.measure(**change)
                self.assertFalse(result['success'])
                self.assertIn(expected, result['error'])
                self.assertIn('finished_at', result)
                if change.get('missed'):
                    self.assertEqual(result['samples'], []); transfer.assert_not_called()

    def test_absent_reported_cca_is_retained_without_inventing_it(self):
        _, result, _ = self.measure(role='receiver', iperf={'end': {'sum_received': {'bytes': 1}}})
        self.assertTrue(result['success'])
        self.assertNotIn('sender_tcp_congestion', result['iperf']['end'])

    def test_cli_dispatch_and_start_redelivery_never_launch_a_second_measurement(self):
        path = self.root / 'config.json'; path.write_text(json.dumps(self.config))
        for action in ('prepare', 'configure', 'preflight', 'measure'):
            method = 'measure' if action == 'measure' else action
            with self.subTest(action=action), patch.object(sys, 'argv', ['runtime.py', action, str(path)]), \
                    patch.object(runtime, method, return_value={'synthetic': True}) as run, contextlib.redirect_stdout(io.StringIO()) as output:
                runtime.main()
                if action == 'prepare': run.assert_called_once_with()
                else: run.assert_called_once_with(self.config)
                self.assertEqual(json.loads(output.getvalue()), {'finished': True} if action == 'measure' else {'synthetic': True})
        with patch.object(runtime, 'run') as run:
            for _ in range(2):
                with patch.object(sys, 'argv', ['runtime.py', 'start', str(path)]), contextlib.redirect_stdout(io.StringIO()):
                    runtime.main()
            run.assert_called_once()
            self.assertIn('--property=RuntimeMaxSec=900', run.call_args.args)
            self.assertEqual(run.call_args.args[-1], str(path.resolve()))
        with patch.object(sys, 'argv', ['runtime.py', 'prepare']), patch.object(runtime, 'prepare', return_value={}) as prepare, contextlib.redirect_stdout(io.StringIO()):
            runtime.main(); prepare.assert_called_once_with()

    def test_cli_entrypoint_help_does_not_run_system_commands(self):
        with patch.object(sys, 'argv', ['runtime.py', '--help']), patch.object(runtime.subprocess, 'run') as run, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            runpy.run_path(runtime.__file__, run_name='__main__')
        self.assertEqual(error.exception.code, 0); run.assert_not_called()

    def test_unconfirmed_upload_responses_do_not_claim_storage_success(self):
        response = MagicMock()
        response.__enter__.return_value.status = 202
        with patch.object(runtime.urllib.request, 'urlopen', return_value=response) as send, patch.object(runtime.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'did not confirm storage'):
                runtime.upload('https://example.test/synthetic-upload', {'success': True})
            self.assertEqual(send.call_count, 5)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8])


if __name__ == '__main__':
    unittest.main()
