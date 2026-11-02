"""Operational regressions from retained tc/iperf failures; no cloud mutations."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy, autonomous_repair, experiment_builder, experiment_environment as repair
import netem_iperf as netem
import network_package, package_runtime, workflow
from test_autonomy import task


def config():
    return dict(cca='cubic',loss_levels_pct=[0,5],rate_mbit=100,one_way_delay_ms=20,
        duration_seconds=3,queue_packets=1000,mtu=1500,socket_buffer_bytes=4194304,
        seed=42,loss_seed=104729,ping_count=3)


class Host:
    def __init__(self,maximum=212992,seed=False):
        self.maxima={'net.core.rmem_max':maximum,'net.core.wmem_max':maximum}
        self.seed=seed;self.commands=[]
    def __call__(self,args,**kwargs):
        self.commands.append(args);out='';err=''
        if args[:2]==['sysctl','-n']:out=str(self.maxima[args[2]])+'\n'
        elif args[:2]==['sysctl','-w']:
            key,value=args[2].split('=');self.maxima[key]=int(value)
        elif args[0]=='tc':err='Usage: netem [ seed SEED ]' if self.seed else 'Usage: netem [ delay TIME ]'
        return subprocess.CompletedProcess(args,0,out,err)


class Environment(unittest.TestCase):
    def test_preparation_raises_both_maxima_to_frozen_buffer_without_changing_config(self):
        c=config();before=copy.deepcopy(c);host=Host();p={}
        with patch.object(netem,'install_seeded_tc',side_effect=lambda *a:setattr(host,'seed',True)) as install:
            netem.prepare_environment(c,host,p,True)
        self.assertEqual(c,before);self.assertEqual(set(host.maxima.values()),{4194304});install.assert_called_once()
        self.assertEqual(len(p['environment_preparation']['changes']),3)
        self.assertTrue(p['environment_preparation']['prepared'])
        self.assertNotIn('qualified',p['environment_preparation'])

    def test_preparation_never_lowers_existing_maxima_or_installs_unneeded_tools(self):
        host=Host(maximum=16777216,seed=True);p={}
        with patch.object(netem,'install_seeded_tc') as install:netem.prepare_environment(config(),host,p,True)
        install.assert_not_called();self.assertEqual(p['environment_preparation']['changes'],[])
        self.assertFalse(any(c[:2]==['sysctl','-w'] for c in host.commands))

    def test_unmarked_shared_host_cannot_be_modified_or_provisioned(self):
        for host in (Host(),Host(maximum=16777216)):
            p={}
            with patch.object(netem,'install_seeded_tc') as install:
                with self.assertRaisesRegex(ValueError,'dedicated-host'):netem.prepare_environment(config(),host,p,False)
            install.assert_not_called();self.assertFalse(any(c[:2]==['sysctl','-w'] for c in host.commands))

    def test_missing_programs_are_installed_by_the_fixed_dedicated_host_path(self):
        execute=Mock();p={}
        with patch.object(netem.shutil,'which',side_effect=[None]*7+['/sbin/tool']*7):
            netem.prepare_tools(execute,p,True)
        self.assertEqual(execute.call_args_list[0].args[0],['apt-get','update'])
        self.assertEqual(execute.call_args_list[1].args[0],['apt-get','install','-y','procps','iproute2','iperf3','iputils-ping','ethtool'])
        self.assertEqual(p['initial_missing_tools'],netem.environment_contract()['tools'])
        with patch.object(netem.shutil,'which',return_value=None):
            with self.assertRaisesRegex(ValueError,'dedicated-host provisioning'):netem.prepare_tools(Mock(),{},False)

    def test_failed_installation_is_an_environment_incident_with_original_diagnostics(self):
        p={};execute=Mock(side_effect=ValueError('Linux experiment command failed: apt-get'))
        with patch.object(netem.shutil,'which',return_value=None):
            with self.assertRaises(ValueError):netem.prepare_tools(execute,p,True)
        p['failure']='ValueError: Linux experiment command failed: apt-get'
        self.assertEqual(repair.failures(p)[0]['code'],'environment-preflight-failed')

    def test_null_loss_seed_remains_null_and_does_not_require_a_new_tc(self):
        c=config();c['loss_seed']=None;host=Host();p={}
        with patch.object(netem,'install_seeded_tc') as install:netem.prepare_environment(c,host,p,True)
        install.assert_not_called();self.assertIsNone(c['loss_seed']);self.assertFalse(any(a[0]=='tc' for a in host.commands))

    def test_failed_sysctl_readback_never_qualifies_the_host(self):
        def execute(args,**kw):return subprocess.CompletedProcess(args,0,'212992\n','')
        p={}
        with self.assertRaisesRegex(ValueError,'readback'):netem.prepare_environment(config(),execute,p,True)
        self.assertNotIn('prepared',p['environment_preparation'])

    def test_source_mismatch_is_rejected_before_build_or_install(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock();response.read.return_value=b'corrupt'
        execute=Mock()
        with patch.object(netem.urllib.request,'urlopen',return_value=response):
            with self.assertRaisesRegex(ValueError,'source hash differs'):netem.install_seeded_tc(execute,{})
        self.assertEqual([c.args[0][0] for c in execute.call_args_list],['apt-get','apt-get'])

    def test_fixed_build_installs_only_tc_from_hash_verified_regular_files(self):
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:xz') as archive:
            info=tarfile.TarInfo('iproute2-6.8.0/Makefile');payload=b'all:\n';info.size=len(payload);archive.addfile(info,io.BytesIO(payload))
        raw=stream.getvalue();response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock();response.read.return_value=raw
        execute=Mock();p={}
        with patch.object(netem,'IPROUTE_SHA256',hashlib.sha256(raw).hexdigest()),patch.object(netem.urllib.request,'urlopen',return_value=response):
            netem.install_seeded_tc(execute,p)
        commands=[c.args[0] for c in execute.call_args_list]
        self.assertEqual(commands[-2][0],'make');self.assertIn('SUBDIRS=lib tc',commands[-2])
        self.assertEqual(commands[-1][-1],'/usr/local/sbin/tc');self.assertEqual(p['tc_source']['sha256'],hashlib.sha256(raw).hexdigest())

    def test_socket_probe_rejects_clamping_even_when_setsockopt_returns_success(self):
        fake=Mock();fake.getsockopt.return_value=425984
        with patch('socket.socket',return_value=fake),patch.object(sys,'argv',['probe','4194304']),patch('builtins.print'):
            with self.assertRaisesRegex(ValueError,'clamped'):exec(netem.SOCKET_PROBE,{})
        fake.getsockopt.return_value=8388608
        with patch('socket.socket',return_value=fake),patch.object(sys,'argv',['probe','4194304']),patch('builtins.print'):exec(netem.SOCKET_PROBE,{})

    def test_failed_host_preflight_produces_missing_observations_without_starting_traffic(self):
        c=config();spec=dict(config=c,replication=0)
        with patch.object(netem.platform,'system',return_value='Linux'),patch.object(netem.os,'geteuid',return_value=0),patch.object(netem.shutil,'which',return_value='/sbin/tool'),patch.object(netem,'prepare_environment',side_effect=ValueError('Experiment environment: socket maxima need dedicated-host preparation.')),patch.object(netem.subprocess,'Popen') as start:
            result=netem.run(spec)
        start.assert_not_called();self.assertEqual(len(result['observations']),10)
        self.assertTrue(all(r['status']=='missing' and r['value'] is None for r in result['observations']))
        self.assertEqual(repair.failures(result['provenance'])[0]['code'],'environment-preflight-failed')

    def test_actual_kernel_seed_must_be_visible_before_any_transfer(self):
        c=config();spec=dict(config=c,replication=0);seen=[]
        def run(argv,**kw):
            seen.append(argv);stdout='[]' if '-j' in argv else ''
            return subprocess.CompletedProcess(argv,0,stdout,'')
        with patch.object(netem.platform,'system',return_value='Linux'),patch.object(netem.os,'geteuid',return_value=0),patch.object(netem.shutil,'which',return_value=netem.__file__),patch.object(netem,'prepare_environment',side_effect=lambda c,e,p,d:p.update(environment_preparation={'prepared':True})),patch.object(netem.subprocess,'run',side_effect=run),patch.object(netem.subprocess,'Popen') as start:
            result=netem.run(spec)
        start.assert_not_called();self.assertIn('did not retain the explicit netem seed',result['provenance']['failure'])
        self.assertEqual(sum(netem.SOCKET_PROBE in argv for argv in seen),2)
        self.assertTrue(any('seed' in argv and str(c['loss_seed']) in argv for argv in seen))


class Incidents(unittest.TestCase):
    def envelope(self):
        return dict(provenance=dict(implementation_sha256='a'*64,commands=[
            dict(argv=['iperf3','-w','4194304'],returncode=1,stdout='',stderr='iperf3: error - socket buffer size not set correctly'),
            dict(argv=['tc','qdisc','replace','seed','104729'],returncode=1,stdout='',stderr='What is "seed"?'),
            dict(argv=['tc','qdisc','del'],returncode=1,stdout='',stderr='No such file'),
            dict(argv=['tc','qdisc','add','netem','help'],returncode=1,stdout='',stderr='Usage: seed SEED')]))

    def run_fixture(self):
        data=self.envelope();raw=workflow.canonical(data);t=task('execution');job=dict(id='job',run_id='run',status='awaiting-review')
        run=dict(id='run',status='partial',output_sha256=workflow.digest(raw),provenance=dict(runner='netem-iperf-v1',producer=dict(implementation_sha256='a'*64,raw_artifact_id='artifact',raw_envelope_sha256=workflow.digest(raw))))
        return t,job,run,raw

    def test_original_failures_are_classified_without_misreading_cleanup_or_help(self):
        failures=repair.failures(self.envelope()['provenance'])
        self.assertEqual([f['code'] for f in failures],['socket-buffer-unavailable','seeded-netem-tool-unavailable'])
        self.assertEqual([f['command_index'] for f in failures],[0,1]);self.assertTrue(all(len(f['command_sha256'])==64 for f in failures))

    def test_legacy_failure_fetch_is_hash_bound_and_preserves_original_run(self):
        t,j,r,raw=self.run_fixture();before=copy.deepcopy(r)
        with patch.object(repair.store,'artifact_bytes',return_value=raw):packet=repair.collect(t,[j],lambda *a,**k:[r])
        self.assertEqual(r,before);self.assertEqual(packet['incidents'][0]['run_id'],'run')
        with patch.object(repair.store,'artifact_bytes',return_value=raw+b' '):
            with self.assertRaisesRegex(ValueError,'hash differs'):repair.collect(t,[j],lambda *a,**k:[r])

    def test_structured_failures_need_no_repeated_raw_download(self):
        t,j,r,raw=self.run_fixture();r['provenance']['producer']['environment_failures']=repair.failures(json.loads(raw)['provenance'])
        with patch.object(repair.store,'artifact_bytes') as fetch:packet=repair.collect(t,[j],lambda *a,**k:[r])
        fetch.assert_not_called();self.assertEqual(len(packet['incidents']),1)

    def test_complete_runs_and_unsettled_jobs_are_not_repair_targets(self):
        t,j,r,raw=self.run_fixture()
        with patch.object(repair.store,'artifact_bytes') as fetch:
            self.assertIsNone(repair.collect(t,[dict(j,status='running')],Mock()))
            self.assertIsNone(repair.collect(t,[j],lambda *a,**k:[dict(r,status='complete')]))
        fetch.assert_not_called()

    def test_unchanged_runtime_waits_without_repeated_model_calls(self):
        t,j,r,raw=self.run_fixture()
        with patch.object(repair.store,'artifact_bytes',return_value=raw):packet=repair.collect(t,[j],lambda *a,**k:[r])
        packet['incidents'][0]['implementation_sha256']=workflow.digest(Path(netem.__file__).read_bytes())
        self.assertTrue(repair.diagnosis(packet)['waiting'])
        packet['incidents'][0]['implementation_sha256']='a'*64
        self.assertFalse(repair.diagnosis(packet)['waiting']);self.assertEqual(repair.diagnosis(packet)['resume_stage'],'planning')

    def test_resume_keeps_failed_jobs_and_scientific_criterion_and_requires_new_reviews(self):
        context=dict(job_ids=['one','two'],completed_job_ids=['old'],experiment_environment={'version':repair.VERSION},proposal={'frozen':'original'},reviews=['prior'],criterion={'original':True},deadline_at='fixed',prior_design_hashes=['original'])
        before=copy.deepcopy(context);restored=repair.resume_context(context)
        self.assertEqual(context,before);self.assertEqual(restored['completed_job_ids'],['old','one','two'])
        self.assertEqual(restored['criterion'],context['criterion']);self.assertEqual(restored['deadline_at'],'fixed')
        self.assertNotIn('job_ids',restored);self.assertNotIn('proposal',restored);self.assertNotIn('reviews',restored)

    def test_execution_routes_actual_environment_failure_to_immediate_repair(self):
        t,j,r,raw=self.run_fixture();t['context']['job_ids']=['job'];packet={'incidents':[{'failures':[{'code':'socket-buffer-unavailable'}]}]}
        with patch.object(autonomy,'rows',return_value=[j]),patch.object(repair,'collect',return_value=packet),patch.object(autonomy,'advance',return_value='repair') as advance,patch.object(autonomy.model,'call') as model:
            self.assertEqual(autonomy.step(t),'repair')
        model.assert_not_called();self.assertEqual(advance.call_args.args[1],'blocked');self.assertEqual(advance.call_args.kwargs['context']['experiment_environment'],packet)

    def test_pending_original_job_prevents_new_environment_action(self):
        t=task('execution');t['context']['job_ids']=['job']
        with patch.object(autonomy,'rows',return_value=[dict(id='job',status='running')]),patch.object(repair,'collect') as collect,patch.object(autonomy,'advance',return_value='waiting'):
            self.assertEqual(autonomy.step(t),'waiting')
        collect.assert_not_called()

    def test_environment_incident_does_not_override_authorization_or_integrity_stops(self):
        t=task();context={'experiment_environment':{'version':repair.VERSION}}
        self.assertEqual(autonomous_repair.category(t,'Experiment environment repair required',context),'runtime')
        for reason in ('auth-required','Original hash differs','Original identity mismatch','cancelled'):
            self.assertIsNone(autonomous_repair.category(t,reason,context))

    def test_historical_failure_is_adopted_once_and_only_from_this_claims_jobs(self):
        t,j,r,raw=self.run_fixture();context=dict(completed_job_ids=[j['id'],'unrelated'])
        def load(kind,study,**kwargs):return [j] if kind=='queue_jobs' else [r]
        with patch.object(repair.store,'artifact_bytes',return_value=raw) as fetch:
            packet=repair.historical(t,context,load,[j['id']])
            self.assertEqual(packet['incidents'][0]['job_id'],j['id'])
            self.assertIsNone(repair.historical(t,context,load,[j['id']]))
        self.assertEqual(fetch.call_count,1);self.assertEqual(context['environment_checked_job_ids'],[j['id']])

    def test_historical_context_intercepts_before_another_planning_model_call(self):
        t=task('planning');packet=dict(incidents=[dict(failures=[dict(code='seeded-netem-tool-unavailable')])])
        view=dict(operational_environment_check=dict(incident=packet,checked_job_ids=['original']))
        with patch.object(autonomy,'build_context',return_value=view),patch.object(autonomy,'advance',return_value='repair') as advance,patch.object(autonomy.model,'call') as model:
            self.assertEqual(autonomy.step(t),'repair')
        model.assert_not_called();self.assertEqual(advance.call_args.kwargs['context']['environment_checked_job_ids'],['original'])

    def test_diagnosis_waits_for_actual_runtime_change_after_originals_settle(self):
        t,j,r,raw=self.run_fixture();t['context']['job_ids']=[j['id']]
        with patch.object(repair.store,'artifact_bytes',return_value=raw):packet=repair.collect(t,[j],lambda *a,**k:[r])
        packet['incidents'][0]['implementation_sha256']=workflow.digest(Path(netem.__file__).read_bytes())
        t['context']['experiment_environment']=packet
        def rows(task,kind,**kwargs):return [j] if kind=='queue_jobs' else []
        with patch.object(autonomous_repair,'rows',side_effect=rows),patch.object(autonomy.model,'call') as model:
            result=autonomous_repair.diagnose(t,dict(category='runtime'))
        model.assert_not_called();self.assertTrue(result['waiting']);self.assertFalse(result['validated'])
        self.assertEqual(result['original_jobs'],[dict(id=j['id'],status=j['status'])])

    def test_bootstrap_fits_signed_urls_and_delivers_exact_source_without_shell_interpolation(self):
        import base64,lzma,re
        spec=dict(runner=netem.VERSION,config=config(),replication=0,implementation_sha256=workflow.digest(Path(netem.__file__).read_bytes()),limits=dict(max_output_bytes=3000000))
        n=dict(job_id='00000000-0000-4000-8000-000000000001',spec=spec,spec_sha256=workflow.digest(workflow.canonical(spec)),deadline='2099-01-01T00:00:00Z')
        with patch.dict(os.environ,NETWORK_PACKAGE_INGEST_URL='https://example.invalid/research/network-ingest'):
            script=network_package.user_data(n,'https://example.invalid/'+'x'*1000,None,'a'*64)
        self.assertLessEqual(len(script.encode()),16384)
        encoded=re.search(r"python3 - '([^']+)' <<'JUMPSERVE_DRIVER'",script).group(1)
        files=json.loads(lzma.decompress(base64.b64decode(encoded)))
        self.assertEqual(files['netem_iperf.py'],Path(netem.__file__).read_text())
        self.assertEqual(files['package_runtime.py'],Path(package_runtime.__file__).read_text())
        self.assertIn("runtime_env['JUMPSERVE_DEDICATED_EXPERIMENT']=s['input_sha256']",files['bootstrap_setup.py'])

    def test_builder_receives_versioned_skill_bytes_and_hash_without_counting_it_as_implementation(self):
        logical={'system':[{'text':'Preserve the scientific method.'}],'builder':{'context':{'claim':{'description':'Original claim'}}}}
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Path(tmp)
            experiment_builder.bootstrap(logical,workspace,workspace,[])
            raw=(workspace/'network-environment-skill.md').read_bytes()
            packet=json.loads((workspace/'work-packet.json').read_bytes())
            self.assertEqual(packet['environment_repair_skill']['sha256'],workflow.digest(raw))
            self.assertEqual(packet['work']['implementation_files'],0)
            self.assertIn(b'Tooling and\ninstrumentation are system work',raw)


if __name__=='__main__':unittest.main()
