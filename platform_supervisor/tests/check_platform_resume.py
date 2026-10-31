"""Real Docker/OpenClaw two-slice fixture with synthetic transport; no provider call."""
import io,json,sys,uuid,subprocess
from pathlib import Path

def main():
    source,out,image=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
    sys.path.insert(0,str(source))
    from platform_supervisor import native
    from platform_supervisor.common import canonical,digest,identity,save
    class Response(io.BytesIO):
        status=200;headers={}
        def read1(self,n):return self.read(n)
    def sse(index,command=None):
        rid='synthetic-resume-'+str(index)
        events=[dict(type='response.created',response=dict(id=rid,status='in_progress',model=native.runtime.MODEL,output=[]))]
        if command:
            args=json.dumps(dict(command=command,timeoutSeconds=15));item=dict(type='function_call',id='fc-'+str(index),call_id='call-'+str(index),name='exec',arguments=args,status='completed')
            events.extend([dict(type='response.output_item.added',output_index=0,item=dict(item,arguments='',status='in_progress')),dict(type='response.function_call_arguments.delta',output_index=0,item_id=item['id'],delta=args),dict(type='response.output_item.done',output_index=0,item=item)])
        else:
            text='Synthetic software fixture completed.';item=dict(type='message',id='msg-'+str(index),role='assistant',status='completed',content=[dict(type='output_text',text=text,annotations=[])])
            events.extend([dict(type='response.output_item.added',output_index=0,item=dict(item,content=[])),dict(type='response.content_part.added',output_index=0,content_index=0,part=dict(type='output_text',text='',annotations=[])),dict(type='response.output_text.delta',output_index=0,content_index=0,item_id=item['id'],delta=text),dict(type='response.output_item.done',output_index=0,item=item)])
        events.append(dict(type='response.completed',response=dict(id=rid,status='completed',model=native.runtime.MODEL,output=[item],usage=dict(input_tokens=0,output_tokens=0,total_tokens=0))))
        return b''.join(b'data: '+canonical(event)+b'\n\n' for event in events)
    class Store:
        def __init__(self):self.spool=out/'spool';self.task=dict(id=str(uuid.uuid4()));self.runs=[];self.artifacts={}
        def rows(self,table):return self.runs if table=='runs' else []
        def artifact(self,name,raw):
            item=dict(id=identity(self.task['id'],digest(raw)),sha256=digest(raw),name=name);save(self.spool/self.task['id']/'artifacts'/item['sha256'],raw);self.artifacts[name]=(item,raw);return item
        def rpc(self,operation,payload=None):
            if operation=='begin-run':
                if len(self.runs)>=6:return dict(denied='budget')
                row=dict(payload,status='running');self.runs.append(row);return row
            if operation=='finish-run':next(row for row in self.runs if row['id']==payload['id']).update(payload);return payload
            raise AssertionError(operation)
    out.mkdir(parents=True,exist_ok=False);workspace=out/'workspace';workspace.mkdir(mode=0o700);store=Store()
    save(out/'prospective.json',canonical(dict(version='platform-resume-fixture-v1',provider_transport='synthetic',actual_provider_calls=0,scientific_claim=False,maximum_synthetic_responses=6)))
    original_run,original_output=subprocess.run,subprocess.check_output
    def observe(method):
        def invoke(*args,**kwargs):
            try:return method(*args,**kwargs)
            except Exception as error:
                stderr=getattr(error,'stderr',b'') or b''
                save(out/'subprocess-failure.json',canonical(dict(type=type(error).__name__,command=args[0],returncode=getattr(error,'returncode',None),stderr=stderr.decode('utf-8','replace')[-3000:] if isinstance(stderr,bytes) else str(stderr)[-3000:])))
                raise
        return invoke
    subprocess.run,subprocess.check_output=observe(original_run),observe(original_output)
    save(workspace/'task.json',canonical(dict(objective='Explicit sandbox resume software fixture. Synthetic transport chooses a fixed command. No research or production operation is admitted.')))
    save(workspace/'proposal.json',canonical(dict(version='platform-proposal-v1',summary='Synthetic sandbox lifecycle fixture; controller checks results.',ready=True)))
    commands=["python3 -c \"from pathlib import Path;Path('marker.txt').write_text('retained')\"","python3 -c \"from pathlib import Path;assert Path('marker.txt').read_text()=='retained'\""]
    report=dict(version='platform-resume-fixture-v1',passed=False,actual_provider_calls=0,provider_transport='synthetic',scientific_claim=False,image=image,slices=[])
    try:
        for number,command in enumerate(commands):
            calls=0
            def transport(method,url,token,body):
                nonlocal calls
                calls+=1;return Response(sse(10*number+calls,command if calls==1 else None))
            result=native.execute(store,workspace,out/('slice-'+str(number)),image,token_source=lambda:'synthetic-not-a-credential',transport=transport)
            envelope=json.loads(store.artifacts['native-stdout-slice-'+str(number)+'.json'][1])
            assert result['ready'] and envelope['status']=='ok' and envelope['toolSummary']['calls']==1 and envelope['toolSummary']['failures']==0
            assert native.containers(native.container_prefix(store.task['id']),workspace)==[]
            assert (workspace/'marker.txt').read_text()=='retained'
            report['slices'].append(dict(number=number,exec_calls=1,synthetic_responses=calls,retained_workspace_verified=True,containers_after_cleanup=0))
        assert len(store.runs)==4 and all(row['status']=='complete' for row in store.runs)
        report.update(passed=True,synthetic_responses=len(store.runs))
    finally:
        subprocess.run,subprocess.check_output=original_run,original_output
        save(out/'report.json',canonical(report));print(json.dumps(report))

if __name__=='__main__':main()
