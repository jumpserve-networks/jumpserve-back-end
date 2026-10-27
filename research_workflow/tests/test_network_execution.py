import copy
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch,Mock
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import api,ideas,network_execution,network_package,network_spec,package_runtime,store,workflow

SOURCE=Path(__file__).parent/'fixtures/network_http_probe_v1.py'
STUDY,ACTOR,JOB,PACKAGE=(str(uuid.uuid4()) for _ in range(4))
def document(runner='emulated-netem-v1',replications=1):
    cfg=dict(num_clients=1,client_delays_ms=[10],client_ccas=['cubic'],client_file_sizes_mbytes=[1],bottleneck_all_client_rate_mbit=100,bottleneck_buffer_kbytes=125,script='netem_cubic_benchmark_nines.py')
    p=workflow.numerical_protocol('a'*64,'flow_completion_time_ms','ms',0,[dict(identity='cubic-10ms',details={'test_config':cfg})])
    p.update(planned_units=replications,prior_exposure='Exposed software development cases; no held-out scientific validation',data_origin='simulation')
    p['resource_limits']['wall_seconds']=1800
    p['execution']=dict(version=network_spec.VERSION,runner=runner,replications=replications,limits=dict(wall_seconds=1800,max_instances=1,max_output_bytes=1000000,max_network_bytes=100000000),exclusive_resources=['fixture:target'])
    if runner=='custom-package-v1':
        p['metrics']=[dict(name='response_headers_ms',units='ms',planned_observations=3,lower_bound=0)]
        p['configurations'][0]['details']['test_config']=dict(mode='real-world',package=dict(kind='python-script',version='http-probe-v1',artifact_id=PACKAGE,sha256=workflow.digest(SOURCE.read_bytes())),arguments=[],parameters=dict(url='http://127.0.0.1:1',requests=3))
        p['data_origin']='new-empirical'
    return network_spec.bind_inputs(p)

class NetworkingContract(unittest.TestCase):
    def test_independent_reaper_includes_cancellation_before_the_deadline(self):
        import urllib.parse
        with patch.object(store,'request',return_value=[dict(job_id=JOB)]) as request, patch.object(network_package,'handler',return_value=dict(status='cleaning')) as handle:
            result=network_package.reap({})
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[0]).query)
        self.assertEqual(query['phase'],['not.in.(completed,failed,cancelled)'])
        self.assertIn('cancel_requested.eq.true',query['or'][0])
        self.assertIn('deadline.lte.',query['or'][0])
        handle.assert_called_once_with(dict(source='jumpserve.network-execution',job_id=JOB,action='status'),None)
        self.assertEqual(result['outcomes'],[dict(job_id=JOB,status='cleaning')])
    def test_declared_observations_cannot_exceed_frozen_budget(self):
        p=document('custom-package-v1');p['resource_limits']['max_observations']=2
        with self.assertRaises(ValueError):network_spec.bind_inputs(p)
    def test_invalid_executor_output_is_a_failed_run_with_original_hash(self):
        p=document('custom-package-v1');spec=network_spec.specs(p)[0];raw=b'{"invalid":"envelope"}'
        run,rows,summaries=network_execution.evidence(dict(id=JOB),dict(id=str(uuid.uuid4())),dict(document=p),spec,raw,dict(status='completed',started_at='2026-10-13T00:00:00Z',ended_at='2026-10-13T00:00:01Z'))
        self.assertEqual(run['status'],'failed');self.assertEqual(run['output_sha256'],workflow.digest(raw));self.assertEqual(rows,[]);self.assertEqual(summaries[0]['missing'],3);self.assertEqual(run['provenance']['data_origin'],'new-empirical')
    def test_compilation_preserves_scientific_methods_and_independent_unit_identities(self):
        p=document(replications=2);workflow.validate_protocol(p)
        specs=network_spec.specs(p);self.assertEqual(len(specs),2);self.assertEqual([s['replication'] for s in specs],[0,1])
        self.assertNotEqual(workflow.digest(workflow.canonical(specs[0])),workflow.digest(workflow.canonical(specs[1])))
        self.assertEqual(network_spec.bind_inputs(p),p)
        for field in ('hypotheses','statistics','dependence','sample_size_rationale','prior_exposure'):self.assertEqual(network_spec.bind_inputs(p)[field],p[field])
    def test_frozen_configuration_units_repetitions_and_budgets_cannot_drift(self):
        p=document()
        for change in ('config','metric','replications','limit'):
            q=copy.deepcopy(p)
            if change=='config':q['configurations'][0]['details']['test_config']['client_delays_ms']=[11]
            if change=='metric':q['metrics'][0]['units']='seconds'
            if change=='replications':q['execution']['replications']=2
            if change=='limit':q['execution']['limits']['wall_seconds']=3601
            with self.assertRaises(ValueError):network_spec.specs(q)
    def test_custom_packages_require_immutable_private_source_or_image_digest(self):
        p=document('custom-package-v1');self.assertEqual(len(network_spec.specs(p)),1)
        cfg=p['configurations'][0]['details']['test_config']
        for package in [dict(kind='container',version='v1',image='docker.io/library/python:latest'),dict(kind='python-script',version='v1',source='execute me',sha256='a'*64)]:
            with self.assertRaises(ValueError):network_spec.config('custom-package-v1',dict(cfg,package=package))
    def test_missing_zero_invalid_and_duplicate_observations_remain_distinct(self):
        metric=[dict(name='x',units='ms',lower_bound=0)]
        make=lambda value,status,reason:dict(observation_key='one',metric='x',units='ms',value=value,status=status,reason=reason,metadata={})
        envelope=lambda rows:workflow.canonical(dict(version='network-observations-v1',observations=rows,provenance={},usage=None))
        self.assertEqual(network_spec.observations(envelope([make(0,'recorded',None)]),metric,3)['observations'][0]['value'],0)
        bad=network_spec.observations(envelope([make(-1,'recorded',None)]),metric,3)['observations'][0]
        self.assertEqual(bad['status'],'invalid');self.assertIsNone(bad['value']);self.assertEqual(bad['metadata']['original_reported_value'],-1)
        missing=network_spec.observations(envelope([make(None,'missing','Unavailable')]),metric,3)['observations'][0];self.assertIsNone(missing['value'])
        with self.assertRaises(ValueError):network_spec.observations(envelope([make(0,'recorded',None)]*2),metric,3)
        with self.assertRaises(ValueError):network_spec.observations(envelope([dict(make(0,'recorded',None),units='s')]),metric,3)
    def test_unavailable_executor_does_not_silently_become_a_manual_job(self):
        p=document();raw=workflow.canonical(network_spec.specs(p)[0])
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):network_execution.validate_input(STUDY,dict(document=p),raw)
    def test_other_users_and_anonymous_callers_cannot_upload_compile_or_cancel(self):
        event=dict(rawPath=f'/research/studies/{STUDY}/network',requestContext={'http':{'method':'POST'}},body=json.dumps(dict(action='upload-package',request_id=PACKAGE,source='print(1)',version='v1')))
        with patch.object(store,'owns',return_value=False),patch.object(network_execution,'upload') as upload:
            with self.assertRaises(api.HttpError):api.dispatch(event,ACTOR)
            self.assertEqual(api.handler(event)['statusCode'],401);upload.assert_not_called()
    def test_terminal_unknown_coverage_does_not_invent_planned_observation_counts(self):
        p=document();p['metrics']=[dict(name='flow_completion_time_ms',units='ms')];spec=network_spec.specs(p)[0]
        raw=workflow.canonical(dict(version='network-observations-v1',observations=[dict(observation_key='client-1',metric='flow_completion_time_ms',units='ms',value=0,status='recorded',reason=None,metadata={})],provenance={},usage=None))
        run,rows,summaries=network_execution.evidence(dict(id=JOB),dict(id=str(uuid.uuid4())),dict(document=p),spec,raw,dict(status='completed',started_at='2026-10-13T00:00:00Z',ended_at='2026-10-13T00:00:01Z'))
        self.assertEqual(rows[0]['value'],0);self.assertEqual(summaries,[]);self.assertIsNone(run['usage']['package_usage']);self.assertIsNone(run['usage']['measured_charges_usd'])
    def test_partial_and_cancelled_runs_preserve_explicit_reasons(self):
        p=document('custom-package-v1');spec=network_spec.specs(p)[0]
        raw=workflow.canonical(dict(version='network-observations-v1',observations=[dict(observation_key='request-0',metric='response_headers_ms',units='ms',value=None,status='missing',reason='Timeout',metadata={})],provenance={},usage=None))
        for state,status in [('completed','partial'),('cancelled','excluded')]:
            run,rows,summaries=network_execution.evidence(dict(id=JOB),dict(id=str(uuid.uuid4())),dict(document=p),spec,raw,dict(status=state,started_at='2026-10-13T00:00:00Z',ended_at='2026-10-13T00:00:01Z'))
            self.assertEqual(run['status'],status);self.assertTrue(run['reason']);self.assertEqual(summaries[0]['missing'],3)

class PackageRuntime(unittest.TestCase):
    def test_cloud_dispatch_is_single_host_without_credentials_or_metadata(self):
        spec=network_spec.specs(document('custom-package-v1'))[0]
        n=dict(job_id=JOB,study_id=STUDY,spec=spec,spec_sha256=workflow.digest(workflow.canonical(spec)),deadline='2099-01-01T00:00:00+00:00',started_at='2026-01-01T00:00:00Z',phase='dispatching',data={},cancel_requested=False)
        ec2=Mock();ec2.describe_instances.return_value={'Reservations':[{'Instances':[{'InstanceId':'i-fixed','State':{'Name':'running'}}]}]}
        env=dict(NETWORK_AWS_ACCOUNT=store.ACCOUNT,NETWORK_PACKAGE_AMI='ami-fixed',NETWORK_PACKAGE_SUBNET='subnet-fixed',NETWORK_PACKAGE_SECURITY_GROUP='sg-fixed',NETWORK_PACKAGE_INGEST_URL='https://example.invalid/ingest')
        with patch.dict(os.environ,env),patch.object(network_package,'saved',return_value=n),patch.object(network_package,'update'),patch.object(store,'rows',return_value=[{'input_artifact_id':PACKAGE}]),patch.object(network_package,'sign_artifact',return_value='https://example.invalid/original'),patch('boto3.client',return_value=ec2):
            result=network_package.handler(dict(source='jumpserve.network-execution',job_id=JOB,action='launch'))
        args=ec2.run_instances.call_args.kwargs
        self.assertEqual(args['ClientToken'],JOB);self.assertEqual(args['MaxCount'],1);self.assertNotIn('IamInstanceProfile',args)
        self.assertEqual(args['MetadataOptions']['HttpEndpoint'],'disabled');self.assertTrue(args['UserData'].startswith('#!/bin/bash'))
        self.assertEqual(args['CreditSpecification']['CpuCredits'],'standard');self.assertEqual(result['status'],'running')
    def test_lost_acknowledgement_only_polls_original_machine(self):
        spec=network_spec.specs(document('custom-package-v1'))[0]
        n=dict(job_id=JOB,study_id=STUDY,spec=spec,deadline='2099-01-01T00:00:00+00:00',phase='dispatching',data={},cancel_requested=False)
        ec2=Mock();ec2.describe_instances.return_value={'Reservations':[]}
        with patch.dict(os.environ,{'NETWORK_AWS_ACCOUNT':store.ACCOUNT}),patch.object(network_package,'saved',side_effect=[None,n,n]),patch('boto3.client',return_value=ec2):
            result=network_package.handler(dict(source='jumpserve.network-execution',job_id=JOB,action='launch'))
        ec2.run_instances.assert_not_called();self.assertEqual(result['status'],'launch-unconfirmed')
    def test_cancellation_waits_for_observed_machine_termination(self):
        spec=network_spec.specs(document('custom-package-v1'))[0]
        n=dict(job_id=JOB,study_id=STUDY,spec=spec,deadline='2099-01-01T00:00:00+00:00',phase='running',data={},cancel_requested=True,started_at='2026-01-01T00:00:00Z')
        ec2=Mock();ec2.describe_instances.return_value={'Reservations':[{'Instances':[{'InstanceId':'i-fixed','State':{'Name':'running'}}]}]}
        with patch.dict(os.environ,{'NETWORK_AWS_ACCOUNT':store.ACCOUNT}),patch.object(network_package,'saved',return_value=n),patch.object(network_package,'update') as update,patch('boto3.client',return_value=ec2):
            result=network_package.handler(dict(source='jumpserve.network-execution',job_id=JOB,action='cancel'))
            self.assertEqual(result['status'],'cleaning');update.assert_not_called()
            ec2.describe_instances.return_value={'Reservations':[{'Instances':[{'InstanceId':'i-fixed','State':{'Name':'terminated'}}]}]}
            result=network_package.handler(dict(source='jumpserve.network-execution',job_id=JOB,action='cancel'))
        self.assertEqual(result['status'],'cancelled');self.assertEqual(ec2.terminate_instances.call_count,1)
    def test_closed_ingest_capability_cannot_read_or_change_private_records(self):
        payload=dict(job_id=JOB,files={})
        with patch.object(store,'rpc',return_value=None),patch.object(network_package,'saved') as saved:
            response=network_package.ingest(dict(headers={'Authorization':'Bearer '+'a'*64},body=json.dumps(payload)))
        self.assertEqual(response['statusCode'],403);saved.assert_not_called()
    def test_actual_loopback_http_requests_preserve_original_package_and_output(self):
        attempts=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                attempts.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'fixture')
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);p=document('custom-package-v1');p['configurations'][0]['details']['test_config']['parameters']['url']=f'http://127.0.0.1:{server.server_port}/probe'
                spec=network_spec.specs(network_spec.bind_inputs(p))[0];(root/'spec.json').write_bytes(workflow.canonical(spec));(root/'package.py').write_bytes(SOURCE.read_bytes())
                result=package_runtime.run(root,time.time()+10,allow_container=False)
                self.assertEqual(result['exit_code'],0);self.assertIsNone(result['measured_charges_usd']);self.assertEqual(len(attempts),3)
                output=network_spec.observations((root/'output.json').read_bytes(),p['metrics'],10)
                self.assertTrue(all(r['status']=='recorded' and r['value']>=0 for r in output['observations']))
                self.assertEqual(workflow.digest((root/'package.py').read_bytes()),spec['config']['package']['sha256'])
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
    def test_wall_timeout_keeps_original_stderr_stdout_and_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=b'import time\nprint("started",flush=True)\ntime.sleep(20)\n'
            spec=network_spec.specs(document('custom-package-v1'))[0];spec['config']['package']['sha256']=workflow.digest(source)
            (root/'spec.json').write_bytes(workflow.canonical(spec));(root/'package.py').write_bytes(source)
            # Leave room for interpreter startup when native/Docker checks run
            # concurrently; the sleeping process still hits a real deadline.
            result=package_runtime.run(root,time.time()+1,allow_container=False)
            self.assertEqual(result['termination_reason'],'Wall deadline exceeded.');self.assertIsNotNone(result['exit_code']);self.assertTrue((root/'runtime-receipt.json').is_file());self.assertIn(b'started',(root/'stdout.bin').read_bytes())
    def test_corrupt_source_is_rejected_before_running(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);spec=network_spec.specs(document('custom-package-v1'))[0]
            (root/'spec.json').write_bytes(workflow.canonical(spec));(root/'package.py').write_text('raise RuntimeError("must not run")')
            with self.assertRaises(ValueError):package_runtime.run(root,time.time()+1,allow_container=False)
            self.assertFalse((root/'stdout.bin').exists())
    def test_unsigned_package_ingest_never_reads_private_state(self):
        with patch.object(store,'rpc') as rpc:
            self.assertEqual(network_package.ingest(dict(headers={},body='{}'))['statusCode'],401);rpc.assert_not_called()
    def test_boto3_bootstrap_is_original_script_and_has_independent_shutdown(self):
        spec=network_spec.specs(document('custom-package-v1'))[0]
        n=dict(job_id=JOB,spec=spec,spec_sha256=workflow.digest(workflow.canonical(spec)),deadline='2099-01-01T00:00:00+00:00')
        with patch.dict(os.environ,{'NETWORK_PACKAGE_INGEST_URL':'https://example.invalid/ingest'}):
            script=network_package.user_data(n,'https://example.invalid/spec','https://example.invalid/source','a'*64)
        self.assertTrue(script.startswith('#!/bin/bash\n'));self.assertLessEqual(len(script.encode()),16384)
        self.assertIn('systemd-run --unit jumpserve-deadline',script);self.assertIn('trap "/sbin/shutdown -h now" EXIT',script)
        self.assertNotIn('SUPABASE_SERVICE_ROLE_KEY',script)

if __name__=='__main__':unittest.main()
