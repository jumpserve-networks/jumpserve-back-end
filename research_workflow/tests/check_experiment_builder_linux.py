"""Fixed native OpenClaw write/exec/read smoke. Synthetic model, real Docker.

No arbitrary command arguments, credentials, provider calls or AWS resources.
Original requests, outputs and failed reports remain under .test-artifacts.
"""
import copy,datetime as dt,io,json,os,subprocess,sys,traceback,uuid,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder,openclaw_runtime as runtime,autonomy_methods,autonomy_model
from prepare_builder_vendor import prepare
from workflow import canonical,digest
ROOT=Path(__file__).resolve().parents[2]
IMAGE=json.loads((ROOT/'.test-artifacts/builder-image/continuation-v1.json').read_bytes())['image']
class Response(io.BytesIO):
    status=200;headers={}
    def read1(self,n):return self.read(n)
def sse(index,name=None,arguments=None):
    rid='response-'+str(index);events=[dict(type='response.created',response=dict(id=rid,status='in_progress',model=runtime.MODEL,output=[]))]
    if name:
        args=json.dumps(arguments);item=dict(type='function_call',id='fc-'+str(index),call_id='call-'+str(index),name=name,arguments=args,status='completed')
        events.extend([dict(type='response.output_item.added',output_index=0,item=dict(item,arguments='',status='in_progress')),
                       dict(type='response.function_call_arguments.delta',output_index=0,item_id=item['id'],delta=args),dict(type='response.output_item.done',output_index=0,item=item)])
    else:
        text='The command ran and the checkpoint contains the actual result.';item=dict(type='message',id='msg-'+str(index),role='assistant',status='completed',content=[dict(type='output_text',text=text,annotations=[])])
        events.extend([dict(type='response.output_item.added',output_index=0,item=dict(item,content=[])),dict(type='response.content_part.added',output_index=0,content_index=0,part=dict(type='output_text',text='',annotations=[])),dict(type='response.output_text.delta',output_index=0,content_index=0,item_id=item['id'],delta=text),dict(type='response.output_item.done',output_index=0,item=item)])
    events.append(dict(type='response.completed',response=dict(id=rid,status='completed',model=runtime.MODEL,output=[item],usage=dict(input_tokens=100,output_tokens=20,total_tokens=120))))
    return b''.join(b'data: '+canonical(e)+b'\n\n' for e in events)
def main():
    # Use a Linux Docker volume, not macOS bind-mount permission emulation.
    # Native root file tools create 0600 files that a nonroot worker must retain.
    volume='js-builder-permissions-'+uuid.uuid4().hex
    subprocess.run(['docker','volume','create',volume],capture_output=True,check=True)
    mount=['--mount','type=volume,src='+volume+',dst=/workspace']
    try:
        subprocess.run(['docker','run','--rm','--network','none',*mount,IMAGE,'python3','-I','-c',"from pathlib import Path;p=Path('/workspace/private.py');p.write_bytes(b'original bytes');p.chmod(0o600)"],capture_output=True,check=True)
        denied=subprocess.run(['docker','run','--rm','--network','none','--user','65534:65534',*mount,IMAGE,'python3','-I','-c',"open('/workspace/private.py','rb').read()"],capture_output=True)
        assert denied.returncode!=0 and b'PermissionError' in denied.stderr
        fixed=subprocess.run(['docker','run','--rm','--network','none',*mount,IMAGE,'python3','-I','-c',builder.NORMALIZE_WORKSPACE,'65534','65534'],capture_output=True,check=True)
        assert json.loads(fixed.stdout)['bytes_unchanged'] is True
        restored=subprocess.run(['docker','run','--rm','--network','none','--user','65534:65534',*mount,IMAGE,'python3','-I','-c',"assert open('/workspace/private.py','rb').read()==b'original bytes'"],capture_output=True,check=True)
        aligned=subprocess.run(['docker','run','--rm','--network','none','--user','65534:65534',*mount,IMAGE,'python3','-I','-c',"import os;from pathlib import Path;p=Path('/workspace/private.py');p.write_bytes(b'aligned bytes');p.chmod(0o600);assert p.stat().st_uid==os.getuid()==65534;assert p.read_bytes()==b'aligned bytes'"],capture_output=True,check=True)
    finally:subprocess.run(['docker','volume','rm',volume],capture_output=True,check=True)
    directory=ROOT/'.test-artifacts/experiment-builder'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');directory.mkdir(parents=True)
    job=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),task_id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()))
    artifacts={};by_id={};calls=[];events=[];requests=[];offset=0
    def retain(j,name,raw,kind='builder-original'):
        identity=str(uuid.uuid5(uuid.UUID(j['id']),name));artifacts[name]=raw;by_id[identity]=raw;(directory/(j['id']+'-retained-'+name)).write_bytes(raw)
        return dict(artifact_id=identity,sha256=digest(raw),version=builder.VERSION)
    def rpc(operation,j,**payload):
        events.append(dict(operation=operation,**payload))
        if operation=='begin':calls.append(payload);return {'id':str(uuid.uuid4())}
        return {}
    steps=[('write',{'path':'/workspace/hello.py','content':"from pathlib import Path\nPath('/workspace/result.txt').write_text(str(sum([2, 3, 4])))\n"}),('edit',{'path':'/workspace/hello.py','oldText':'[2, 3, 4]','newText':'[2, 3, 5]'}),('exec',{'command':"python3 /workspace/hello.py && sudo -n sh -c \"printf '#!/bin/sh\\\\nprintf helper-v1\\\\n' > /opt/jumpserve-builder-helper; chmod 755 /opt/jumpserve-builder-helper; printf installed > /opt/jumpserve-builder-marker\" && /opt/jumpserve-builder-helper > /workspace/helper-version.txt",'timeoutSeconds':10}),('read',{'path':'/workspace/result.txt'})]
    def transport(method,url,token,body):
        request=json.loads(body);requests.append(request);index=len(requests)
        assert method=='POST' and url=='https://api.openai.com/v1/responses' and token=='synthetic-not-a-credential'
        assert request['store'] is False and request['stream'] is True
        local_index=index-offset
        if local_index<=len(steps):return Response(sse(index,*steps[local_index-1]))
        return Response(sse(index))
    authority=builder.action_policy()
    template=json.loads((ROOT/'.test-artifacts/builder-image/published-prompt-v2.json').read_bytes())
    system=template['system_prompt']+'\n\n'+template['research_context']
    assert template['id']=='dfab5ecb-b8b9-5ad3-8771-f28a06b53b27' and digest(system.encode())==template['content_sha256']
    logical=dict(modelId=runtime.MODEL,system=[dict(text=system),dict(text=autonomy_model.TURN_POLICY['instruction']),dict(text=authority['document']['instruction']),dict(text=autonomy_methods.INSTRUCTION)],builder=dict(version=builder.VERSION,context={'task':'building','question':'Write and run a tiny Python calculation, then read its result.','large_source_fixture':'exact-retained-source-α;'*3200},checkpoint=None,execution_authority={k:authority[k] for k in ('version','sha256')}))
    package=ROOT/'experiments/openclaw_pilot/runtime/node_modules/openclaw';node=package.parent/'node/bin/node'
    corrected=prepare(package,ROOT/'.test-artifacts/builder-vendor/node_modules/openclaw',builder.PATCH_PATH)
    protocol=ROOT/'research_workflow/policies/builder-continuation-validation-v2.json'
    report=dict(protocol='builder-continuation-validation-v2',protocol_sha256=digest(protocol.read_bytes()),kind='development-regression',provider_calls=0,measured_charges_usd=None,image=IMAGE,prompt_content_sha256=template['content_sha256'],full_published_system_composition=True,reviewer={'type':'AI','identity':'Codex','independent':False},status='failed',live_provider_calls=0)
    names=['experiment_builder.py','autonomy_methods.py','autonomy_model.py','openclaw_runtime.py','store.py','network_spec.py','generated_package.py','source_project.py','workflow.py','openclaw-builder-patch.json','policies/experiment-action-policy-v1.json','policies/autonomy-turn-policy-v1.json','policies/builder-continuation-validation-v1.json','policies/builder-continuation-validation-v2.json','experiment-builder.Dockerfile','tests/check_experiment_builder_linux.py']
    report['source_files']={}
    for name in names:
        raw=(ROOT/'research_workflow'/name).read_bytes();dest=directory/'original-source'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        report['source_files'][name]=dict(sha256=digest(raw),bytes=len(raw))
    try:
        with patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package),patch.object(builder,'retain',side_effect=retain),patch.object(builder,'rpc',side_effect=rpc),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE=IMAGE,OPENCLAW_BUILDER_PACKAGE=str(corrected)):
            answer,receipt=builder.execute(logical,directory/'slice',job,token_source=lambda:'synthetic-not-a-credential',transport=transport)
        report['receipt']=receipt;report['native_model_calls']=len(requests);report['events']=events
        assert receipt['status']=='complete',receipt
        with zipfile.ZipFile(io.BytesIO(artifacts['checkpoint.zip'])) as archive:
            assert archive.read('result.txt')==b'10';assert archive.read('helper-version.txt')==b'helper-v1'
        assert receipt['action_policy_sha256']==authority['sha256']
        assert len(requests)==5 and len(calls)==5
        outputs=[item for request in requests for item in request['input'] if item.get('type')=='function_call_output']
        assert outputs and not any('Write verification failed' in str(item) or 'Edit verification failed' in str(item) or 'PermissionError' in str(item) for item in outputs)
        assert all(e['status']=='complete' for e in events if e['operation']=='finish')
        assert all(any(item.get('type')=='function_call_output' for item in r['input']) for r in requests[1:])
        offset=len(requests)
        steps=[('exec',{'command':'test -f /opt/jumpserve-builder-marker && test -f /workspace/hello.py && /opt/jumpserve-builder-helper > /workspace/resumed-helper-version.txt && printf retained > /workspace/resumed.txt','timeoutSeconds':10})]
        steps += [('exec',{'command':'python3 /workspace/read-retained.py context large_source_fixture '+str(page),'timeoutSeconds':10}) for page in range(12)]
        resumed_job=dict(job,id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()))
        logical['builder']['checkpoint']=receipt['checkpoint'];logical['builder']['context']['question']='Continue the checkpoint and verify the retained installed-state marker.'
        with patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package),patch.object(builder,'retain',side_effect=retain),patch.object(builder,'rpc',side_effect=rpc),patch.object(builder.store,'artifact_bytes',side_effect=lambda study,identity,**kw:by_id[identity]),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE=IMAGE,OPENCLAW_BUILDER_PACKAGE=str(corrected)):
            resumed_answer,resumed_receipt=builder.execute(logical,directory/'resume',resumed_job,token_source=lambda:'synthetic-not-a-credential',transport=transport)
        report['resume_receipt']=resumed_receipt;report['native_model_calls']=len(requests)
        assert resumed_receipt['status']=='complete',resumed_receipt
        with zipfile.ZipFile(io.BytesIO(artifacts['checkpoint.zip'])) as archive:
            assert archive.read('resumed.txt')==b'retained';assert archive.read('resumed-helper-version.txt')==b'helper-v1'
        assert resumed_receipt.get('context_checkpoint') is True,resumed_receipt
        assert all(len(canonical(r))<=builder.CONTEXT_CHECKPOINT_BYTES for r in requests)
        boundary_count=len(requests);offset=boundary_count
        steps=[('exec',{'command':'test -f /opt/jumpserve-builder-marker && /opt/jumpserve-builder-helper > /workspace/third-helper-version.txt','timeoutSeconds':10}),('exec',{'command':'python3 /workspace/read-retained.py context question 0','timeoutSeconds':10}),('read',{'path':'/workspace/resumed.txt'})]
        third_job=dict(job,id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()))
        logical['builder']['checkpoint']=resumed_receipt['checkpoint']
        logical['builder']['context']['question']='Resume after the local context checkpoint; inspect retained evidence in pages.'
        with patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package),patch.object(builder,'retain',side_effect=retain),patch.object(builder,'rpc',side_effect=rpc),patch.object(builder.store,'artifact_bytes',side_effect=lambda study,identity,**kw:by_id[identity]),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE=IMAGE,OPENCLAW_BUILDER_PACKAGE=str(corrected)):
            third_answer,third_receipt=builder.execute(logical,directory/'third',third_job,token_source=lambda:'synthetic-not-a-credential',transport=transport)
        report['third_receipt']=third_receipt;report['native_model_calls']=len(requests)
        assert third_receipt['status']=='complete',third_receipt
        assert len(requests)==boundary_count+4 and len(calls)==len(requests)
        with zipfile.ZipFile(io.BytesIO(artifacts['checkpoint.zip'])) as archive:
            assert archive.read('third-helper-version.txt')==b'helper-v1'
            assert json.loads(archive.read('research-context.json'))==logical['builder']['context']
            index=json.loads(archive.read('context-index.json'))
            page_records=next(x for x in index['fields'] if x['key']=='large_source_fixture')['delivered_pages']
            assert page_records and page_records[0]['page']==0
            history=json.loads(archive.read('native-tool-history.json'))
            assert len(canonical(history))>32000
            assert any('call_id' in item for item in history)
        for name in ('slice','resume','third'):
            delivery=json.loads((directory/name/'context-delivery.json').read_bytes())
            assert delivery['bootstrap_bytes']<=builder.BOOTSTRAP_BYTES
        report.update(slots=3,retained_history_bytes=len(canonical(history)),maximum_provider_request_bytes=max(len(canonical(r)) for r in requests),context_boundary_before_provider_admission=True,verified_previous_page_deliveries=len(page_records),delivery_is_not_completed_review=True)
        if sys.argv[1:]==['--retained-paper']:
            # Static synthetic tool plan with the REAL retained paper context.
            # This tests the native interface, not whether a model chooses code.
            infra=ROOT.parent/'jumpserve-infra';base=infra/'.test-artifacts/assessment-4055396e'
            matched_path=sorted(base.glob('*/matched-checkpoints.json'))[-1];matched=json.loads(matched_path.read_bytes())
            capture=json.loads((infra/matched['source_capture']).read_bytes());entry=matched['checkpoints'][0]
            original_zip=(matched_path.parent/entry['file']).read_bytes();assert digest(original_zip)==entry['sha256']
            parent=next(d for d in capture['dispatches'] if d['id']==entry['dispatch_id'])
            logical=copy.deepcopy(json.loads(parent['request_raw']))
            with zipfile.ZipFile(io.BytesIO(original_zip)) as archive:context=json.loads(archive.read('research-context.json'))
            logical['builder']['context']=context;logical['builder']['checkpoint']=parent['provenance']['checkpoint']
            by_id[logical['builder']['checkpoint']['artifact_id']]=original_zip
            paper_job={k:str(uuid.uuid4()) for k in ('id','study_id','actor_id','task_id','lease_token')}
            program='import json\nfrom pathlib import Path\ndef read_conditions(path):\n return json.loads(Path(path).read_text())["loss_levels_pct"]\n'
            check='import sys,json\nfrom pathlib import Path\nsys.path.insert(0,"/workspace/project")\nimport experiment\np=Path("/workspace/development-input.json")\nfor values in ([0,1,2,5,10],[0,None]):\n p.write_text(json.dumps({"loss_levels_pct":values}))\n assert experiment.read_conditions(p)==values\nprint("software-only checks passed; no measurements collected")\n'
            steps=[('read',{'path':'/workspace/work-packet.json'}),('exec',{'command':'python3 /workspace/read-retained.py paper-page 2 0','timeoutSeconds':10}),('write',{'path':'/workspace/project/experiment.py','content':program}),('write',{'path':'/workspace/development_check.py','content':check}),('exec',{'command':'python3 /workspace/development_check.py','timeoutSeconds':10})]
            offset=len(requests)
            with patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package),patch.object(builder,'retain',side_effect=retain),patch.object(builder,'rpc',side_effect=rpc),patch.object(builder,'deliver_repair_diagnosis',side_effect=lambda value,job:value),patch.object(builder.store,'artifact_bytes',side_effect=lambda study,identity,**kw:by_id[identity]),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE=IMAGE,OPENCLAW_BUILDER_PACKAGE=str(corrected)):
                paper_answer,paper_receipt=builder.execute(logical,directory/'retained-paper',paper_job,token_source=lambda:'synthetic-not-a-credential',transport=transport)
            try:
                assert paper_receipt['status']=='complete' and len(requests)==offset+6,paper_receipt
                with zipfile.ZipFile(io.BytesIO(artifacts['checkpoint.zip'])) as archive:
                    assert archive.read('project/experiment.py')==program.encode()
                    assert json.loads(archive.read('research-context.json'))==context
                outputs=[x['output'] for request in requests[offset:] for x in request['input'] if x.get('type')=='function_call_output']
                assert any('software-only checks passed; no measurements collected' in text for text in outputs)
                report['retained_paper']=dict(status='passed',original_dispatch_id=parent['id'],original_checkpoint_sha256=digest(original_zip),original_context_sha256=digest(canonical(context)),context_bytes=len(canonical(context)),receipt=paper_receipt,maximum_request_bytes=max(len(canonical(q)) for q in requests[offset:]),synthetic_provider_requests=len(requests)-offset,live_provider_calls=0,scope='Real native CLI read packet/source, wrote and executed a fixed SOFTWARE-ONLY conditions parser with full retained paper context. Diagnosis was already delivered in that checkpoint. No model behavior, paper experiment, candidate publication or measurement ingestion is validated.')
            finally:builder.container_action(paper_job['task_id'],'remove')
        report['status']='passed'
    except Exception:
        report['failure']=traceback.format_exc();raise
    finally:
        builder.container_action(job['task_id'],'remove')
        if any(digest((ROOT/'research_workflow'/name).read_bytes())!=record['sha256'] for name,record in report['source_files'].items()):
            report.update(status='failed',source_changed_during_check=True)
        (directory/'report.json').write_bytes(canonical(report));print(json.dumps({'status':report['status'],'report':str(directory/'report.json')}))
    if report['status']!='passed':raise SystemExit(1)
if __name__=='__main__':
    if sys.argv[1:] not in ([],['--retained-paper']):raise SystemExit('Only the fixed --retained-paper development fixture is supported.')
    main()
