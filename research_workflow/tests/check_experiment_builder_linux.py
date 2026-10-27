"""Fixed native OpenClaw write/exec/read smoke. Synthetic model, real Docker.

No arbitrary command arguments, credentials, provider calls or AWS resources.
Original requests, outputs and failed reports remain under .test-artifacts.
"""
import datetime as dt,io,json,os,subprocess,sys,uuid,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder,openclaw_runtime as runtime
from prepare_builder_vendor import prepare
from workflow import canonical,digest
ROOT=Path(__file__).resolve().parents[2]
IMAGE=json.loads((ROOT/'.test-artifacts/builder-image/selected.json').read_bytes())['image']
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
    logical=dict(modelId=runtime.MODEL,system=[dict(text='Synthetic development smoke; use the provided tools in the isolated workspace.')],builder=dict(version=builder.VERSION,context={'task':'building','question':'Write and run a tiny Python calculation, then read its result.'},checkpoint=None,execution_authority={k:authority[k] for k in ('version','sha256')}))
    package=ROOT/'experiments/openclaw_pilot/runtime/node_modules/openclaw';node=package.parent/'node/bin/node'
    corrected=prepare(package,ROOT/'.test-artifacts/builder-vendor/node_modules/openclaw',builder.PATCH_PATH)
    report=dict(protocol='experiment-builder-validation-v1',kind='development-regression',provider_calls=0,measured_charges_usd=None,image=IMAGE,reviewer={'type':'AI','identity':'Codex','independent':False},status='failed')
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
        steps=[('exec',{'command':'test -f /opt/jumpserve-builder-marker && test -f /workspace/hello.py && /opt/jumpserve-builder-helper > /workspace/resumed-helper-version.txt && printf retained > /workspace/resumed.txt','timeoutSeconds':10}),('read',{'path':'/workspace/resumed.txt'})]
        resumed_job=dict(job,id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()))
        logical['builder']['checkpoint']=receipt['checkpoint'];logical['builder']['context']['question']='Continue the checkpoint and verify the retained installed-state marker.'
        with patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package),patch.object(builder,'retain',side_effect=retain),patch.object(builder,'rpc',side_effect=rpc),patch.object(builder.store,'artifact_bytes',side_effect=lambda study,identity,**kw:by_id[identity]),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE=IMAGE,OPENCLAW_BUILDER_PACKAGE=str(corrected)):
            resumed_answer,resumed_receipt=builder.execute(logical,directory/'resume',resumed_job,token_source=lambda:'synthetic-not-a-credential',transport=transport)
        report['resume_receipt']=resumed_receipt;report['native_model_calls']=len(requests)
        assert resumed_receipt['status']=='complete',resumed_receipt
        with zipfile.ZipFile(io.BytesIO(artifacts['checkpoint.zip'])) as archive:
            assert archive.read('resumed.txt')==b'retained';assert archive.read('resumed-helper-version.txt')==b'helper-v1'
        assert len(requests)==8 and len(calls)==8
        report['status']='passed'
    finally:
        builder.container_action(job['task_id'],'remove')
        (directory/'report.json').write_bytes(canonical(report));print(json.dumps({'status':report['status'],'report':str(directory/'report.json')}))
    if report['status']!='passed':raise SystemExit(1)
if __name__=='__main__':main()
