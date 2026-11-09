"""Exercise the EC2 lifecycle with recorded, synthetic AWS/storage responses.

The controller, command serialization and transitions are real. No machines,
SSM commands, database writes, signed uploads or measurement runs are started.
"""
import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shlex
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import controller
from test_real_world import AwsError, job


class ControllerLifecycle(unittest.TestCase):
    def setUp(self):
        self.value = job()
        self.value['deadline'] = 10000
        self.value['runtime_revision'] = 'retained-synthetic-revision'
        self.saved = []
        self.save = self.enterContext(patch.object(controller.store, 'save', side_effect=lambda value: self.saved.append(copy.deepcopy(value))))
        self.enterContext(patch.object(controller.time, 'time', return_value=1000))
        self.enterContext(patch.dict(os.environ, INSTANCE_PROFILE_ARN='synthetic-scoped-instance-profile'))
        self.ssm = MagicMock()
        self.client = self.enterContext(patch.object(controller.cloud, 'client', return_value=self.ssm))

    def instances(self):
        for i, node in enumerate(self.value['nodes']):
            node['instance_id'] = 'i-synthetic-' + str(i)
        return self.value['nodes']

    def test_prepare_and_start_commands_retain_exact_settings_without_shell_interpolation(self):
        node = self.instances()[0]
        self.value['config']['notes'] = "Unicode Ω and literal $(touch /unwanted); 'quoted'"
        self.value['start_epoch'] = 1120
        original = copy.deepcopy(self.value)
        self.ssm.send_command.return_value = {'Command': {'CommandId': 'synthetic-command'}}
        with patch.object(controller.artifacts, 'signed_upload', return_value='https://upload.example.test/synthetic?secret=private') as signed:
            for action in ['prepare', 'configure', 'preflight', 'start']:
                with self.subTest(action=action):
                    self.assertEqual(controller.command(self.value, node, action), 'synthetic-command')
                    args = self.ssm.send_command.call_args.kwargs
                    self.assertEqual(args['InstanceIds'], [node['instance_id']])
                    self.assertEqual(args['DocumentName'], 'AWS-RunShellScript')
                    self.assertEqual(args['TimeoutSeconds'], 1200)
                    self.assertEqual(args['Parameters']['executionTimeout'], ['1000'])
                    lines = args['Parameters']['commands']
                    payload_line = next(line for line in lines if line.endswith('/' + action + '.json'))
                    payload = json.loads(base64.b64decode(shlex.split(payload_line)[1]))
                    self.assertEqual(payload['notes'], original['config']['notes'])
                    self.assertEqual(payload['nodes'], original['nodes'])
                    self.assertEqual(payload['runtime_revision'], original['runtime_revision'])
                    self.assertIn('chmod 600 /var/lib/jumpserve/' + action + '.json', lines)
                    self.assertNotIn(original['config']['notes'], '\n'.join(lines))
                    if action == 'prepare':
                        script = next(line for line in lines if line.endswith('/runtime.py'))
                        self.assertEqual(base64.b64decode(shlex.split(script)[1]), Path(controller.__file__).with_name('runtime.py').read_bytes())
                        self.assertTrue(any('bootstrap-ready' in line and 'timeout 900' in line for line in lines))
                    if action == 'start':
                        self.assertEqual(payload['start_epoch'], 1120)
                        self.assertEqual(payload['upload_url'], signed.return_value)
                    else:
                        self.assertNotIn('upload_url', payload)
            signed.assert_called_once_with(self.value['job_id'], node['name'])
        self.assertEqual(self.value, original, 'Private command settings must not mutate the persisted configuration')

    def test_prepare_waits_for_online_ssm_and_persists_each_command_before_polling(self):
        nodes = self.instances()
        nodes[0]['prepare_done'] = True
        self.ssm.describe_instance_information.side_effect = [
            {'InstanceInformationList': []}, {'InstanceInformationList': [{'PingStatus': 'ConnectionLost'}]},
            {'InstanceInformationList': [{'PingStatus': 'Online'}]},
        ]
        self.ssm.send_command.return_value = {'Command': {'CommandId': 'original-prepare-command'}}
        self.assertFalse(controller.commands_complete(self.value, 'prepare'))
        self.assertEqual(self.ssm.describe_instance_information.call_count, 3)
        self.assertNotIn('prepare_command', nodes[1]); self.assertNotIn('prepare_command', nodes[2])
        self.assertEqual(nodes[3]['prepare_command'], 'original-prepare-command')
        self.assertEqual(self.saved[-1]['nodes'][3]['prepare_command'], 'original-prepare-command')
        self.ssm.get_command_invocation.assert_not_called()
        self.ssm.describe_instance_information.assert_called_with(Filters=[{'Key': 'InstanceIds', 'Values': [nodes[3]['instance_id']]}])

    def test_missing_and_nonterminal_invocations_never_redispatch_an_existing_command(self):
        self.instances()
        for node in self.value['nodes']: node['configure_command'] = node['name'] + '-original'
        self.ssm.get_command_invocation.side_effect = [AwsError('InvocationDoesNotExist'),
            {'Status': 'Pending'}, {'Status': 'InProgress'}, {'Status': 'Delayed'}]
        original = copy.deepcopy(self.value)
        self.assertFalse(controller.commands_complete(self.value, 'configure'))
        self.assertEqual(self.value, original)
        self.ssm.send_command.assert_not_called(); self.save.assert_not_called()
        self.ssm.describe_instance_information.assert_not_called()
        self.ssm.get_command_invocation.assert_called_with(CommandId='receiver-2-original', InstanceId='i-synthetic-3')

    def test_first_configuration_dispatch_does_not_require_a_second_ssm_registration_query(self):
        self.instances()
        with patch.object(controller, 'command', side_effect=lambda value, node, action: 'original-' + node['name']) as command:
            self.assertFalse(controller.commands_complete(self.value, 'configure'))
        self.assertEqual(command.call_count, 4); self.assertEqual(len(self.saved), 4)
        self.ssm.describe_instance_information.assert_not_called(); self.ssm.get_command_invocation.assert_not_called()
        self.assertTrue(all(node['configure_command'] == 'original-' + node['name'] for node in self.value['nodes']))

    def test_success_preserves_prepare_public_keys_and_kernel_then_skips_completed_commands(self):
        self.instances()
        for node in self.value['nodes']: node['prepare_command'] = node['name'] + '-original'
        self.ssm.get_command_invocation.return_value = {'Status': 'Success', 'StandardOutputContent': 'Retained diagnostic line\n' + json.dumps({'public_key': 'synthetic-wireguard-key', 'kernel': 'synthetic-kernel'}) + '\n'}
        self.assertTrue(controller.commands_complete(self.value, 'prepare'))
        for node in self.value['nodes']:
            self.assertTrue(node['prepare_done']); self.assertEqual(node['public_key'], 'synthetic-wireguard-key'); self.assertEqual(node['kernel'], 'synthetic-kernel')
        self.assertEqual(len(self.saved), 4)
        self.ssm.reset_mock(); self.save.reset_mock()
        self.assertTrue(controller.commands_complete(self.value, 'prepare'))
        self.ssm.get_command_invocation.assert_not_called(); self.save.assert_not_called()
        for node in self.value['nodes']: node['configure_command'] = 'original-configure'
        self.ssm.get_command_invocation.return_value = {'Status': 'Success', 'StandardOutputContent': '{}'}
        self.assertTrue(controller.commands_complete(self.value, 'configure'))
        self.assertTrue(all(node['configure_done'] for node in self.value['nodes']))

    def test_provider_and_command_failures_remain_errors_with_bounded_diagnostics(self):
        nodes = self.instances()
        nodes[0]['start_command'] = 'original-start'
        self.ssm.get_command_invocation.side_effect = AwsError('AccessDeniedException')
        with self.assertRaises(AwsError): controller.commands_complete(self.value, 'start')
        self.ssm.get_command_invocation.side_effect = None
        for status in ['Failed', 'TimedOut', 'Cancelled']:
            self.ssm.get_command_invocation.return_value = {'Status': status, 'StandardErrorContent': 'private-prefix-' + 'x' * 501}
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, 'server: start ' + status) as caught:
                controller.commands_complete(self.value, 'start')
            self.assertNotIn('private-prefix', str(caught.exception)); self.assertTrue(str(caught.exception).endswith('x' * 500))
        self.assertNotIn('start_done', nodes[0]); self.ssm.send_command.assert_not_called()

    def test_lifecycle_only_advances_after_every_prerequisite_and_sets_one_shared_future_start(self):
        def provision(value, node, profile):
            self.assertEqual(profile, 'synthetic-scoped-instance-profile'); node['instance_id'] = 'i-' + node['name']
        with patch.object(controller.cloud, 'provision', side_effect=provision) as allocate, \
                patch.object(controller.cloud, 'refresh', return_value=True) as refresh, \
                patch.object(controller.cloud, 'allow_peers') as peers, \
                patch.object(controller, 'commands_complete', return_value=False) as commands:
            for _ in range(4): controller.step(self.value)
            self.assertEqual(allocate.call_count, 4); self.assertEqual(self.value['status'], 'provisioning')
            controller.step(self.value); self.assertEqual(self.value['status'], 'bootstrapping')
            controller.step(self.value); self.assertEqual(self.value['status'], 'bootstrapping'); peers.assert_not_called()
            commands.return_value = True
            controller.step(self.value); self.assertEqual(self.value['status'], 'configuring'); self.assertEqual(peers.call_count, 4)
            self.assertEqual(refresh.call_count, 8)
            for state, action, following in [('configuring', 'configure', 'checking'), ('checking', 'preflight', 'starting'), ('starting', 'start', 'running')]:
                commands.return_value = False; controller.step(self.value); self.assertEqual(self.value['status'], state)
                commands.assert_called_with(self.value, action)
                commands.return_value = True; controller.step(self.value); self.assertEqual(self.value['status'], following)
            self.assertEqual(self.value['start_epoch'], 1120)
            self.assertEqual(allocate.call_count, 4)

    def test_one_unready_machine_prevents_ssm_preparation_and_peer_configuration(self):
        self.instances(); self.value['status'] = 'bootstrapping'
        with patch.object(controller.cloud, 'refresh', side_effect=[True, False, True, True]) as refresh, \
                patch.object(controller, 'commands_complete') as commands, patch.object(controller.cloud, 'allow_peers') as peers:
            controller.step(self.value)
        self.assertEqual(refresh.call_count, 4); commands.assert_not_called(); peers.assert_not_called()
        self.assertEqual(self.value['status'], 'bootstrapping')

    def test_artifact_timeout_uses_recorded_start_duration_and_grace_without_fabricating_results(self):
        self.value.update(status='running', start_epoch=730)
        with patch.object(controller, 'collect', return_value=False):
            controller.step(self.value)  # Exactly 730 + 30 + 240 is still inside the grace boundary.
            with patch.object(controller.time, 'time', return_value=1001), self.assertRaisesRegex(RuntimeError, 'measurement artifacts'):
                controller.step(self.value)
        with patch.object(controller, 'collect', return_value=True): controller.step(self.value)
        self.assertNotIn('results', self.value)
        self.value['status'] = 'unrecognized'
        with self.assertRaisesRegex(RuntimeError, 'Unknown lifecycle state'): controller.step(self.value)

    def test_collection_rejects_failed_reports_without_inventing_partial_or_zero_results(self):
        for report in [{'success': False}, {'success': False, 'error': 'Synthetic receiver process failure'}]:
            with self.subTest(report=report), patch.object(controller.artifacts, 'records', return_value=['retained-inventory']) as records, \
                    patch.object(controller.artifacts, 'read', return_value=(json.dumps(report).encode(), {})) as read, \
                    self.assertRaisesRegex(RuntimeError, report.get('error', 'Measurement failed')):
                controller.collect(self.value)
            records.assert_called_once_with(self.value['job_id'])
            read.assert_called_once_with(self.value['job_id'], 'server', ['retained-inventory'])
            self.assertNotIn('results', self.value); self.assertNotIn('outcome', self.value)

    def test_cleanup_retries_all_regions_and_only_marks_terminated_after_every_deletion(self):
        self.value.update(status='cleaning', outcome='completed'); self.value['nodes'][-1]['region'] = 'us-west-2'
        with patch.object(controller.cloud, 'cleanup_region', side_effect=[RuntimeError('x' * 2000), False]) as cleanup:
            controller.cleanup(self.value)
        self.assertEqual(cleanup.call_count, 2); self.assertEqual(len(self.value['cleanup_error']), 1500)
        self.assertEqual(self.value['active'], 'yes'); self.assertEqual(self.value['status'], 'cleaning')
        with patch.object(controller.cloud, 'cleanup_region', return_value=True): controller.cleanup(self.value)
        self.assertIsNone(self.value['cleanup_error']); self.assertEqual(self.value['status'], 'completed')
        self.assertTrue(all(node['state'] == 'terminated' for node in self.value['nodes']))
        self.value.pop('outcome'); self.value['status'] = 'cleaning'
        with patch.object(controller.cloud, 'cleanup_region', return_value=True): controller.cleanup(self.value)
        self.assertEqual(self.value['status'], 'failed')

    def test_forced_cleanup_preserves_the_original_outcome_error_and_entry_before_deletions(self):
        self.value.update(outcome='cancelled', error='Original recorded reason')
        def remove(value, region):
            self.assertEqual(self.saved[0]['status'], 'cleaning'); self.assertEqual(self.saved[0]['outcome'], 'cancelled')
            return True
        with patch.object(controller.store, 'claim', return_value='original-lease'), patch.object(controller.store, 'load', return_value=self.value), \
                patch.object(controller.store, 'release') as release, patch.object(controller.cloud, 'cleanup_region', side_effect=remove), patch.object(controller, 'finalize') as finalize:
            self.assertEqual(controller.handler({'job_id': 'job-1', 'force_cleanup': True}, None), {'job_id': 'job-1', 'finished': True})
        self.assertEqual(self.value['error'], 'Original recorded reason'); self.assertEqual(self.value['_lease_token'], 'original-lease')
        release.assert_called_once_with('job-1', 'original-lease'); finalize.assert_called_once_with(self.value)
        self.assertEqual(len(self.saved), 2)
        self.value.update(status='cleaning', active='yes')
        self.saved.clear()
        with patch.object(controller.store, 'claim', return_value='original-lease'), patch.object(controller.store, 'load', return_value=self.value), patch.object(controller.store, 'release'), patch.object(controller.cloud, 'cleanup_region', return_value=True), patch.object(controller, 'finalize'):
            self.assertTrue(controller.tick('job-1', force_cleanup=True)['finished'])
        self.assertEqual(len(self.saved), 1, 'An already-recorded cleanup entry needs no second entry write')

    def test_load_save_and_finalization_failures_always_release_the_claimed_lease(self):
        with patch.object(controller.store, 'claim', return_value='original-lease'), patch.object(controller.store, 'load', side_effect=RuntimeError('load failed')), patch.object(controller.store, 'release') as release:
            with self.assertRaisesRegex(RuntimeError, 'load failed'): controller.tick('job-1')
            release.assert_called_once_with('job-1', 'original-lease')
        with patch.object(controller.store, 'claim', return_value='original-lease'), patch.object(controller.store, 'load', return_value=self.value), patch.object(controller.store, 'release') as release, patch.object(controller, 'step'), patch.object(controller.store, 'save', side_effect=RuntimeError('save failed')):
            with self.assertRaisesRegex(RuntimeError, 'save failed'): controller.tick('job-1')
            release.assert_called_once_with('job-1', 'original-lease')
        for status in ['completed', 'failed', 'cancelled']:
            self.value['status'] = status
            with self.subTest(status=status), patch.object(controller.store, 'claim', return_value='original-lease'), patch.object(controller.store, 'load', return_value=self.value), patch.object(controller.store, 'release') as release, patch.object(controller, 'finalize') as finalize, patch.object(controller, 'step') as step:
                self.assertTrue(controller.tick('job-1')['finished']); finalize.assert_called_once_with(self.value)
                step.assert_not_called(); release.assert_called_once_with('job-1', 'original-lease')

    def test_reaper_continues_after_a_failed_cleanup_and_persistence_follows_artifact_freezing(self):
        with patch.object(controller.store, 'expired', return_value=['first', 'second']), patch.object(controller, 'tick', side_effect=[RuntimeError('cleanup failed'), {'finished': True}]) as tick, contextlib.redirect_stdout(io.StringIO()) as output:
            controller.reap({}, None)
        self.assertEqual(tick.call_args_list[0].kwargs, {'force_cleanup': True}); self.assertEqual(tick.call_args_list[1].args, ('second',))
        self.assertEqual(json.loads(output.getvalue()), {'job_id': 'first', 'cleanup_error': 'cleanup failed'})
        calls = []
        with patch.object(controller.artifacts, 'freeze', side_effect=lambda value: calls.append(('freeze', value))), patch('reports.persist_report', side_effect=lambda value: calls.append(('persist', value))):
            controller.finalize(self.value)
        self.assertEqual(calls, [('freeze', self.value), ('persist', self.value)])
        with patch.object(controller.artifacts, 'freeze', side_effect=RuntimeError('original archive unavailable')), patch('reports.persist_report') as persist:
            with self.assertRaisesRegex(RuntimeError, 'original archive unavailable'): controller.finalize(self.value)
            persist.assert_not_called()


if __name__ == '__main__': unittest.main()
