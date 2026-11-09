"""Release-verifier unit tests; cloud launches, resource reads and evidence are synthetic."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smoke_test as smoke


class SmokeVerification(unittest.TestCase):
    def test_placement_filters_zone_type_availability_and_selected_instance_offering(self):
        rows = [dict(available=True, type='local-zone', instance_types=['t3.large'], zone_id='local'),
                dict(available=False, type='availability-zone', instance_types=['t3.large'], zone_id='closed'),
                dict(available=True, type='availability-zone', instance_types=['t3.medium'], zone_id='wrong-offering'),
                dict(available=True, type='availability-zone', instance_types=['t3.large'], zone_id='eligible')]
        with patch.object(smoke.cloud, 'locations', return_value=rows):
            self.assertEqual(smoke.placement('us-east-1', 't3.large'), {'region': 'us-east-1', 'zone_id': 'eligible', 'instance_type': 't3.large'})
        with patch.object(smoke.cloud, 'locations', return_value=[]), self.assertRaisesRegex(RuntimeError, 'No eligible zones'): smoke.placement('us-east-1')

    @contextlib.contextmanager
    def environment(self, flags=(), *, status='completed', failure=None):
        import boto3
        stack = contextlib.ExitStack()
        with stack:
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            stack.enter_context(patch.object(sys, 'argv', ['smoke', *flags]))
            stack.enter_context(patch.object(smoke.subprocess, 'check_output', return_value=json.dumps(dict(AccessKeyId='fixture-access', SecretAccessKey='fixture-secret', SessionToken='fixture-session'))))
            stack.enter_context(patch.object(boto3, 'setup_default_session'))
            resources = [dict(ResourceType=kind, LogicalResourceId=logical, PhysicalResourceId=physical) for kind, logical, physical in [
                ('AWS::StepFunctions::StateMachine', 'RealWorldTestsWorkflow', 'fixture-workflow'), ('AWS::Lambda::Function', 'RealWorldTestsApi', 'fixture-api')]]
            services = {name: MagicMock() for name in ['cloudformation', 'lambda', 'ec2']}
            services['cloudformation'].describe_stack_resources.return_value = {'StackResources': resources}
            services['lambda'].get_function_configuration.return_value = {'Environment': {'Variables': {'RUNTIME_REVISION': 'fixture-revision', 'SUPABASE_URL': 'https://fixture.supabase.co', 'SUPABASE_SECRET_ARN': 'fixture-arn'}}}
            services['ec2'].describe_instances.return_value = {'Reservations': [{'Instances': [{'State': {'Name': 'running' if failure == 'instances' else 'terminated'}}]}]}
            services['ec2'].describe_vpcs.return_value = {'Vpcs': [{'VpcId': 'fixture'}] if failure == 'vpc' else []}
            calls = stack.enter_context(patch.object(smoke.cloud, 'client', side_effect=lambda name, *args: services[name]))
            stack.enter_context(patch.object(smoke.cloud, 'locations', return_value=[dict(available=True, type='availability-zone', instance_types=['t3.medium', 't3.large'], zone_id='use1-az1')]))
            launch = stack.enter_context(patch.object(smoke.api, 'launch', return_value={'job_id': 'fixture-job'}))
            nodes = [dict(name=name, role=role, region='us-east-1', instance_id='i-fixture') for name, role in [('server', 'server'), ('bottleneck', 'bottleneck'), ('receiver-1', 'receiver')]]
            job = dict(job_id='fixture-job', status=status, nodes=nodes, deadline=5000)
            load = stack.enter_context(patch.object(smoke.store, 'load', return_value=job))
            cancel = stack.enter_context(patch.object(smoke.store, 'cancel'))
            def original(job_id, name):
                self.assertEqual(job_id, 'fixture-job')
                return json.dumps(dict(success=failure != 'success', effective_cca='cubic' if failure == 'cca' else 'bbr',
                    samples=[dict(qdisc=[dict(kind='fq_codel' if failure == 'fifo' else 'bfifo', bytes=100)])])).encode(), {}
            read = stack.enter_context(patch.object(smoke.artifacts, 'read', side_effect=original))
            clock = stack.enter_context(patch.object(smoke.time, 'time', side_effect=[0, 2401] if failure == 'timeout' else [0, 1, 2, 3]))
            sleep = stack.enter_context(patch.object(smoke.time, 'sleep'))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            yield dict(job=job, load=load, cancel=cancel, launch=launch, read=read, output=output, services=services, calls=calls, clock=clock, sleep=sleep)

    def test_default_command_prints_a_bounded_plan_without_launching_or_exposing_credentials(self):
        for flags in [(), ('--instance-type', 't3.large', '--receiver-region', 'us-east-1', '--receiver-region', 'us-west-2')]:
            with self.subTest(flags=flags), self.environment(flags) as env:
                smoke.main(); value = json.loads(env['output'].getvalue())
                self.assertFalse(value['apply']); self.assertEqual(value['config']['duration_seconds'], 10)
                self.assertEqual(value['machines'], 3 if not flags else 4)
                self.assertEqual(value['config']['server']['instance_type'], 't3.medium' if not flags else 't3.large')
                self.assertIn('exclude from research comparisons', value['config']['notes'])
                env['launch'].assert_not_called(); env['load'].assert_not_called(); env['read'].assert_not_called()
                self.assertNotIn('fixture-secret', env['output'].getvalue())

    def test_success_requires_terminal_status_zero_residual_resources_and_original_cca_fifo_evidence(self):
        with self.environment(['--apply']) as env:
            smoke.main(); rows = [json.loads(line) for line in env['output'].getvalue().splitlines()]
            self.assertTrue(rows[-1]['verified']); self.assertTrue(rows[-1]['resources_removed'])
            request, actor = env['launch'].call_args.args; self.assertEqual(actor, 'deployment-smoke-test')
            self.assertEqual(request['config']['cca'], 'bbr'); self.assertEqual(request['config']['duration_seconds'], 10)
            self.assertEqual(env['read'].call_count, 3); env['cancel'].assert_not_called()
            env['services']['ec2'].describe_instances.assert_called_once(); env['services']['ec2'].describe_vpcs.assert_called_once()

    def test_terminal_failures_residual_resources_or_missing_measurements_never_emit_verified_success(self):
        cases = [('failed', None, 'failed'), ('completed', 'instances', 'Residual test resources'), ('completed', 'vpc', 'Residual test resources'),
                 ('completed', 'success', 'Missing success/CCA evidence'), ('completed', 'cca', 'Missing success/CCA evidence'), ('completed', 'fifo', 'FIFO has no measured traffic')]
        for status, failure, message in cases:
            with self.subTest(status=status, failure=failure), self.environment(['--apply'], status=status, failure=failure) as env:
                with self.assertRaisesRegex(RuntimeError, message): smoke.main()
                self.assertNotIn('"verified": true', env['output'].getvalue()); env['cancel'].assert_not_called()

    def test_cancel_occurs_once_after_instance_creation_and_cancelled_runs_do_not_require_measurement_reports(self):
        with self.environment(['--apply', '--cancel'], status='cancelled') as env:
            active = dict(env['job'], status='running'); env['load'].side_effect = [active, active, env['job'], env['job']]
            smoke.main(); env['cancel'].assert_called_once_with('fixture-job'); self.assertEqual(env['sleep'].call_count, 2)
            self.assertTrue(all(call.args == (10,) for call in env['sleep'].call_args_list))
            env['read'].assert_not_called(); self.assertIn('"verified": true', env['output'].getvalue())

    def test_deadline_or_unexpected_failure_requests_cancellation_for_only_the_created_active_job(self):
        for failure in ['timeout', 'transport']:
            with self.subTest(failure=failure), self.environment(['--apply'], status='running', failure=failure) as env:
                if failure == 'transport': env['load'].side_effect = [RuntimeError('fixture retrieval failure'), env['job']]
                with self.assertRaises(RuntimeError): smoke.main()
                env['cancel'].assert_called_once_with('fixture-job'); self.assertIn('"cancel_requested": true', env['output'].getvalue())
        with self.environment(['--apply'], status='running', failure='timeout') as env:
            env['load'].return_value = None
            with self.assertRaisesRegex(RuntimeError, 'timed out'): smoke.main()
            env['cancel'].assert_not_called()

    def test_help_does_not_retrieve_credentials_or_touch_cloud_services(self):
        with patch.object(sys, 'argv', ['smoke', '--help']), patch.object(smoke.subprocess, 'check_output') as cli, patch.object(smoke.cloud, 'client') as cloud, contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
            runpy.run_path(smoke.__file__, run_name='__main__')
        self.assertEqual(result.exception.code, 0); cli.assert_not_called(); cloud.assert_not_called()


if __name__ == '__main__': unittest.main()
