"""Fixed local OpenClaw repair integration: synthetic inference, real Docker tools.

No credentials, cloud mutations or paper experiments. Every run keeps originals.
"""
import argparse
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import uuid
import zipfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_model as model
import experiment_builder as builder
import autonomous_repair as repair
import generated_package
import openclaw_runtime as runtime
import store
from workflow import canonical,digest,numerical_protocol
from prepare_builder_vendor import prepare
from check_experiment_builder_linux import Response,sse

ROOT=Path(__file__).resolve().parents[2]

def verify_live_result(name):
    """Review retained actual outputs without replaying model/tool operations."""
    import re
    if not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z',name):raise ValueError('A retained local trial identifier is required.')
    prior=ROOT/'.test-artifacts/native-repair-handoff'/name;original=(prior/'report.json').read_bytes();report=json.loads(original)
    assert report['live'] and report['receipt']['status']=='complete' and report['receipt']['build_state']=='candidate'
    originals={}
    for item in report['artifacts']:
        raw=(prior/('original-'+item['path'])).read_bytes();assert digest(raw)==item['sha256'] and len(raw)==item['bytes'];originals[item['path']]=raw
    validation=json.loads(originals['repair-validation.json']);candidate=json.loads(originals['candidate-compiled.json']);project=originals['project.zip']
    assert validation['passed'] and validation['exit_code']==0 and validation['source_change']['changed']
    assert validation['source_change']['baseline_checkpoint']==report['receipt']['native_repair_handoff']['baseline_checkpoint']
    assert validation['test_result']['passed'] and validation['test_result']['tests_run']>=2 and validation['test_result']['experiment_functions_executed']==['convert']
    assert digest(originals['repair-check-stdout.bin'])==validation['stdout']['sha256'] and digest(originals['repair-check-stderr.bin'])==validation['stderr']['sha256']
    assert digest(project)==validation['project_sha256']
    baseline=validation['source_change']['baseline_checkpoint'];matches=[p for p in prior.parent.glob('*/original-live-failed-workspace.zip') if digest(p.read_bytes())==baseline['sha256']]
    assert matches,'Original pre-repair workspace is required, not a reconstructed baseline.'
    with zipfile.ZipFile(io.BytesIO(matches[0].read_bytes())) as archive:
        original_source=archive.read('project/experiment.py');original_tests=archive.read('project/repair_test.py')
    members=generated_package.members(project)
    assert members['repair_test.py']==original_tests and digest(original_source)==validation['source_change']['original_experiment_sha256']
    assert members['experiment.py']!=original_source and digest(members['experiment.py'])==validation['source_change']['current_experiment_sha256']
    assert {c['details']['test_config']['private_inputs']['project.zip']['sha256'] for c in candidate['protocol']['configurations']}=={digest(project)}
    plan=json.loads(originals['repair-test-plan-0.json']);assert plan==dict(version='repair-development-validation-v1',incident_sha256=validation['incident_sha256'],scope='software-development-only')
    for index in range(1,report['actual_provider_calls']+1):
        receipt=json.loads((prior/'slice'/('call-'+str(index)+'-completion.json')).read_bytes());raw=(prior/'slice'/receipt['response_filename']).read_bytes()
        assert receipt['status']=='complete' and receipt['http_status']==200 and digest(raw)==receipt['response_sha256']
        assert digest((prior/('actual-request-'+str(index)+'.json')).read_bytes())==receipt['request_sha256']
        runtime.wire.read_terminal_stream(io.BytesIO(raw),io.BytesIO(),__import__('time').monotonic()+10,1048576)
    result=dict(version='native-repair-retained-review-v1',status='passed',original_report_sha256=digest(original),prior_directory=name,original_harness_status=report['status'],
        correction='The continuation reuses retained native checks and separately executes both server tests. A new native exec in every continuation is unnecessary.',
        new_provider_calls=0,actual_provider_calls_in_trial=report['actual_provider_calls'],aggregate_actual_provider_calls=report.get('continuation',{}).get('earlier_actual_provider_calls',0)+report['actual_provider_calls'],
        validation=validation,project_sha256=digest(project),platform=report['platform'],emulated=report['emulated'],scientific_completion=False,production_writes=0,
        usage=report['receipt']['usage'],source_files=report['source_files'],reviewer=dict(identity='Codex implementing AI',independent=False,human=False,held_out=False))
    destination=prior.parent/('retained-review-'+uuid.uuid4().hex+'.json');destination.write_bytes(canonical(result));print(json.dumps(dict(report=str(destination),status='passed',new_provider_calls=0)))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--amd64',action='store_true');parser.add_argument('--live',action='store_true');parser.add_argument('--resume-live',action='store_true');parser.add_argument('--resume-live-v2',action='store_true');parser.add_argument('--verify-live-result');args=parser.parse_args()
    if args.verify_live_result:return verify_live_result(args.verify_live_result)
    if args.resume_live_v2:args.resume_live=True
    if args.resume_live and not args.live:parser.error('--resume-live requires --live')
    directory=ROOT/'.test-artifacts/native-repair-handoff'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True)
    protocol=ROOT/'research_workflow/policies/native-repair-handoff-validation-v1.json'
    image_report=json.loads((ROOT/'.test-artifacts/builder-image'/('selected-linux-amd64.json' if args.amd64 else 'continuation-v1.json')).read_bytes());image=image_report['image']
    live_protocol=ROOT.parent/'jumpserve-infra/docs/autonomy-recovery-execution-protocol-v1.json'
    limits=json.loads(live_protocol.read_bytes())['qualification']
    report=dict(version='native-repair-handoff-linux-v1',status='failed',started_at=dt.datetime.now(dt.timezone.utc).isoformat(),protocol_sha256=digest(protocol.read_bytes()),qualification_protocol_sha256=digest(live_protocol.read_bytes()),
        actual_provider_calls=0,cloud_instances=0,production_writes=0,image=image,platform=image_report['platform'],host_platform=__import__('platform').machine(),emulated=args.amd64 and __import__('platform').machine() not in ('x86_64','AMD64'),live=args.live,
        measured_charges_usd=None,unallocated_local_compute_cost_usd=None,reviewer=dict(identity='Codex implementing AI',type='AI',independent=False,human=False,held_out=False))
    names=['autonomy.py','autonomy_model.py','autonomous_repair.py','experiment_builder.py','repair_validation.py','source_project.py','generated_package.py','network_spec.py','netem_iperf.py','openclaw-builder-patch.json','policies/native-repair-handoff-validation-v1.json','tests/check_native_repair_handoff_linux.py','tests/test_native_repair_handoff.py']
    report['source_files']={}
    for name in names:
        raw=(ROOT/'research_workflow'/name).read_bytes();dest=directory/'original-source'/name
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        report['source_files'][name]=dict(sha256=digest(raw),bytes=len(raw))
    job=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),task_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()))
    artifacts={};by_id={};events=[];requests=[];controller=[]
    def retain(j,name,raw,*args):
        ident=str(uuid.uuid5(uuid.UUID(j['id']),name));artifacts[name]=raw;by_id[ident]=raw
        (directory/('original-'+name)).write_bytes(raw)
        return dict(artifact_id=ident,sha256=digest(raw),version=builder.VERSION)
    def rpc(operation,j,**payload):
        if operation=='begin' and args.live and sum(e['operation']=='begin' for e in events)>=limits['maximum_actual_provider_calls']:
            return dict(denied='qualification-budget')
        events.append(dict(operation=operation,**payload))
        return dict(id=str(uuid.uuid4())) if operation=='begin' else {}
    def control(t,operation,payload):
        controller.append(dict(operation=operation,**payload));return dict(replayed=False)
    source=directory/'failed-workspace';project=source/'project';project.mkdir(parents=True)
    original=b'def convert(value):\n    return value\n'
    checks=b'import unittest,experiment\nclass RepairChecks(unittest.TestCase):\n def test_original_failure(self):\n  with self.assertRaises(TypeError): experiment.convert(None)\n def test_corrected_behavior(self):\n  self.assertEqual(experiment.convert(7),14)\n'
    files={'experiment.py':original,'repair_test.py':checks,'requirements.lock':b'','system-packages.lock.json':b'[]','build-evidence.json':b'{"commands":[],"checks":[],"limitations":["Software-only control; no scientific experiment."]}'}
    for name,raw in files.items():(project/name).write_bytes(raw)
    baseline=retain(job,'failed-workspace.zip',builder.checkpoint(source))
    incident=dict(stage='building',reason='The retained conversion returns an unchanged input.',checkpoint=baseline)
    packet=dict(incident=incident,incident_sha256=digest(canonical(incident)))
    diagnosis=canonical(dict(version='synthetic-software-diagnosis-v1',native_repair=True,waiting=False,scope='Development fixture, not a production incident or scientific evidence.'))
    packet['diagnosis']=dict(retain(job,'diagnosis.json',diagnosis),byte_count=len(diagnosis))
    ctx=dict(task='building',repair_directive=repair.REPAIR_INSTRUCTION,repair_packet=packet,builder_checkpoint=baseline,
        claim=dict(id=str(uuid.uuid4()),description='Synthetic software check: convert(7) returns 14; no research claim.'),
        builder_feedback='Edit the actual convert function, execute the existing tests and freeze the exact software candidate.')
    ctx['native_repair_handoff']=builder.repair_handoff(ctx)
    candidate=numerical_protocol('a'*64,'converted','count',0,[dict(identity='software-control',details=dict(test_config=dict(mode='emulated',arguments=[],parameters={})) )])
    candidate.update(stage='pilot',data_origin='simulation',prior_exposure='Exposed software development control; no scientific result.',planned_units=1,
        metrics=[dict(name='converted',units='count',planned_observations=1)],execution=dict(version='network-execution-v1',runner='built-package-v1',replications=1,limits=dict(wall_seconds=60,max_instances=1,max_output_bytes=32000,max_network_bytes=1024)))
    candidate['resource_limits'].update(wall_seconds=60,max_observations=1)
    manifest=dict(protocol=candidate,experiment_type='independent-check')
    (source/'software-candidate-template.json').write_bytes(canonical(manifest))
    # The template is prospective fixture metadata, not a supplied repair.
    if args.live:
        baseline=retain(job,'live-failed-workspace.zip',builder.checkpoint(source));ctx['builder_checkpoint']=baseline
        ctx['repair_packet']['incident']['checkpoint']=baseline;ctx['repair_packet']['incident_sha256']=digest(canonical(ctx['repair_packet']['incident']))
        ctx.pop('native_repair_handoff',None);ctx['native_repair_handoff']=builder.repair_handoff(ctx)
        ctx['builder_feedback']='Software qualification only: convert(7) must return 14, and convert(None) must raise TypeError. Keep repair_test.py unchanged. Edit the actual function and run the incident checks. Write the exact incident-bound repair-test-plan.json required by repair_directive. After both checks pass, copy software-candidate-template.json verbatim to candidate.json. No research experiment or user input is needed.'
    if args.resume_live:
        amendment_path=ROOT.parent/'jumpserve-infra/docs'/('autonomy-recovery-execution-followup-v2.json' if args.resume_live_v2 else 'autonomy-recovery-execution-followup-v1.json');amendment=json.loads(amendment_path.read_bytes())
        prior=ROOT/'.test-artifacts/native-repair-handoff'/amendment['prior_directory'];prior_raw=(prior/'report.json').read_bytes();assert digest(prior_raw)==amendment['prior_report_sha256']
        old=json.loads(prior_raw);assert old['actual_provider_calls']==amendment.get('prior_actual_provider_calls',12)
        for index in range(1,old['actual_provider_calls']+1):
            captured=json.loads((prior/'slice'/('call-'+str(index)+'-completion.json')).read_bytes());raw_response=(prior/'slice'/captured['response_filename']).read_bytes()
            assert captured['status']=='complete' and captured['http_status']==200 and digest(raw_response)==captured['response_sha256']
            assert digest((prior/('actual-request-'+str(index)+'.json')).read_bytes())==captured['request_sha256']
            runtime.wire.read_terminal_stream(io.BytesIO(raw_response),io.BytesIO(),__import__('time').monotonic()+10,1048576)
        checkpoint=(prior/('original-'+amendment.get('prior_checkpoint_filename','failed-checkpoint.zip'))).read_bytes();assert digest(checkpoint)==amendment['prior_checkpoint_sha256']
        with zipfile.ZipFile(io.BytesIO(checkpoint)) as archive:ctx=json.loads(archive.read('research-context.json'))
        origins=[*amendment.get('ancestors',[]),dict(directory=prior.name,report_sha256=digest(prior_raw))]
        for origin in origins:
            ancestor=prior.parent/origin['directory'];ancestor_raw=(ancestor/'report.json').read_bytes();assert digest(ancestor_raw)==origin['report_sha256'];ancestor_report=json.loads(ancestor_raw)
            for item in ancestor_report['artifacts']:
                raw_original=(ancestor/('original-'+item['path'])).read_bytes();assert digest(raw_original)==item['sha256']
                ident=str(uuid.uuid5(uuid.UUID(json.loads((ancestor/'slice/call-1-completion.json').read_bytes())['dispatch_id']),item['path']));by_id[ident]=raw_original
        ctx['builder_checkpoint']=old['receipt']['checkpoint'];by_id[ctx['builder_checkpoint']['artifact_id']]=checkpoint
        baseline=ctx['native_repair_handoff']['baseline_checkpoint']
        job['task_id']=json.loads((prior/'slice/call-1-completion.json').read_bytes())['task_id'];job['study_id']=json.loads((prior/'slice/call-1-completion.json').read_bytes())['study_id']
        ctx['builder_feedback']='Source and unchanged tests already passed. Correct repair-test-plan.json using the exact work-packet repair_handoff.test_plan object. Then copy software-candidate-template.json verbatim to candidate.json. Do not reread contracts, edit the fixed function again or run empty test scripts. Server validation follows.'
        limits=dict(limits,maximum_actual_provider_calls=amendment['prospective_limits']['maximum_new_actual_provider_calls'])
        report['continuation']=dict(protocol_sha256=digest(amendment_path.read_bytes()),original_report_sha256=digest(prior_raw),checkpoint_sha256=digest(checkpoint),earlier_actual_provider_calls=amendment.get('earlier_aggregate_actual_provider_calls',12))
    steps=[('read',dict(path='/workspace/project/experiment.py')),
        ('exec',dict(command='python3 -m unittest discover -s /workspace/project -p repair_test.py -v',timeoutSeconds=10)),
        ('edit',dict(path='/workspace/project/experiment.py',oldText='return value',newText='return value * 2')),
        ('write',dict(path='/workspace/project/repair-test-plan.json',content=canonical(dict(version='repair-development-validation-v1',incident_sha256=packet['incident_sha256'],scope='software-development-only')).decode())),
        ('exec',dict(command='python3 -m unittest discover -s /workspace/project -p repair_test.py -v',timeoutSeconds=10)),
        ('write',dict(path='/workspace/candidate.json',content=canonical(manifest).decode()))]
    def transport(method,url,token,body):
        assert method=='POST' and url=='https://api.openai.com/v1/responses'
        if not args.live:assert token=='synthetic-not-a-credential'
        request=json.loads(body);builder.validate_tool_surface(request,ctx);requests.append(request)
        index=len(requests);(directory/(('actual' if args.live else 'synthetic')+'-request-'+str(index)+'.json')).write_bytes(body)
        if args.live:
            if index>limits['maximum_actual_provider_calls']:raise ValueError('Qualification provider allowance exhausted before transport.')
            report['actual_provider_calls']+=1
            (directory/'live-admissions.json').write_bytes(canonical(dict(provider_calls=report['actual_provider_calls'],reserved_cents=100*report['actual_provider_calls'])))
            return runtime.http_request(method,url,token,body)
        if index>len(steps)+1:raise ValueError('Fixed synthetic integration exceeded its declared response count.')
        return Response(sse(index,*steps[index-1])) if index<=len(steps) else Response(sse(index))
    def provider(logical):
        answer,receipt=builder.execute(logical,directory/'slice',job,token_source=runtime.credential if args.live else lambda:'synthetic-not-a-credential',transport=transport)
        report['receipt']=receipt
        # The production dispatcher attaches this receipt to the SDK envelope.
        answer['usage']['research_runtime']=receipt
        return answer
    task=dict(job,stage='building',attempts=270,context=copy.deepcopy(ctx))
    package=ROOT/'experiments/openclaw_pilot/runtime/node_modules/openclaw';node=package.parent/'node/bin/node'
    try:
        subprocess.run(['docker','image','inspect',image],capture_output=True,check=True,timeout=15)
        corrected=prepare(package,ROOT/'.test-artifacts/builder-vendor/node_modules/openclaw',builder.PATCH_PATH)
        template=json.loads((ROOT/'.test-artifacts/builder-image/published-prompt-v2.json').read_bytes())
        system=template['system_prompt']+'\n\n'+template['research_context']
        assert digest(system.encode())==template['content_sha256']
        before=canonical(ctx)
        with patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package),patch.object(runtime,'AUTH',ROOT/'experiments/openclaw_pilot/.subscription-auth'),patch.object(builder,'retain',side_effect=retain),patch.object(builder,'rpc',side_effect=rpc),patch.object(store,'artifact_bytes',side_effect=lambda study,ident,**kw:by_id[ident]),patch.object(store,'rpc',return_value={}),patch.object(model,'OPENCLAW',True),patch.object(model,'MODEL',runtime.MODEL),patch.object(builder,'enabled',return_value=True),patch.object(model,'load_prompt',return_value=(dict(id=template['id']),system)),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE=image,OPENCLAW_BUILDER_PACKAGE=str(corrected)):
            if args.live:runtime.verify_runtime();report['authorized_model_catalog']=runtime.catalog(runtime.credential())
            result=model.call(task,ctx,control,invoke_provider=provider)
        assert canonical(ctx)==before
        assert result['status']=='complete',result
        assert result['runtime']['build_state']=='candidate',result['runtime']
        assert result['output']['action']=='propose'
        if not args.live:assert len(requests)==len(steps)+1
        validation=json.loads(artifacts['repair-validation.json'])
        assert validation['passed'] and validation['source_change']['changed']
        assert validation['source_change']['original_experiment_sha256']==digest(original)
        assert validation['source_change']['baseline_checkpoint']==baseline
        assert validation['test_result']['experiment_functions_executed']==['convert']
        compiled=json.loads(artifacts['candidate-compiled.json']);cfg=compiled['protocol']['configurations'][0]['details']['test_config']
        assert cfg['private_inputs']['project.zip']['sha256']==digest(artifacts['project.zip'])==validation['project_sha256']
        actual_source=generated_package.members(artifacts['project.zip'])['experiment.py']
        if args.live:assert actual_source!=original and generated_package.members(artifacts['project.zip'])['repair_test.py']==checks
        else:assert actual_source==original.replace(b'return value',b'return value * 2')
        history=builder.merge_tool_history([],requests[-1]['input'])
        outputs=[h for h in history if h.get('type')=='function_call_output']
        execs=[h for h in history if h.get('type')=='function_call' and h.get('name')=='exec']
        if args.live:
            assert validation['test_result']['passed'] and validation['test_result']['tests_run']>=2
        else:
            assert len(execs)==2 and json.loads(execs[0]['arguments'])==json.loads(execs[1]['arguments'])
        # The native runtime normalizes call IDs. Match actual paired identities,
        # not provider IDs or assumed output positions.
        if not args.live:
            original_output=next(h['output'] for h in outputs if h['call_id']==execs[0]['call_id'])
            corrected_output=next(h['output'] for h in outputs if h['call_id']==execs[1]['call_id'])
            assert 'FAILED' in original_output and 'OK' in corrected_output
        assert [e['operation'] for e in controller]==['begin','finish']
        assert 'builder' in controller[0]['request'] and 'toolConfig' not in controller[0]['request']
        assert task['attempts']==271
        report.update(status='passed',synthetic_native_responses=0 if args.live else len(requests),controller_admissions=len(controller)//2,
            checks=['Actual model.call selects native builder logical request and persists begin/finish through fixture control.',
                'Actual OpenClaw read, failing exec, edit, write and passing exec ran in the pinned local Docker image.',
                'Server validator executed two incident-bound tests in a read-only network-free container against the frozen changed source.',
                'Frozen project, wrapper and candidate identities match the server validation; no experiment was launched.'],
            validation=validation,prompt_content_sha256=template['content_sha256'])
    except BaseException as error:
        report.update(error_type=type(error).__name__,error=str(error),traceback=traceback.format_exc())
        if isinstance(error,subprocess.CalledProcessError):report['process_failure']=dict(returncode=error.returncode,stdout=(error.stdout or b'').decode(errors='replace')[:10000],stderr=(error.stderr or b'').decode(errors='replace')[:10000])
        raise
    finally:
        report.update(ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),synthetic_native_responses=0 if args.live else len(requests),events=events,
            artifacts=[dict(path=n,sha256=digest(raw),bytes=len(raw)) for n,raw in artifacts.items()])
        (directory/'controller-events.json').write_bytes(canonical(controller))
        # Only this fixture task's containers are removed; original evidence stays.
        had_error=sys.exc_info()[0] is not None
        try:builder.container_action(job['task_id'],'remove')
        except Exception as error:
            report.update(status='failed',cleanup_error=str(error))
            if not had_error:raise
        finally:
            (directory/'report.json').write_bytes(canonical(report));print(json.dumps(dict(directory=str(directory),status=report['status'],actual_provider_calls=report['actual_provider_calls'],synthetic_native_responses=report['synthetic_native_responses'])))


if __name__=='__main__':main()
