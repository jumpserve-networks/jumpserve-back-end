"""Single isolated EC2 host for versioned networking packages, without an IAM profile.

Multi-machine built-in experiments use the original real-world lifecycle. A custom
package can implement Linux namespaces/netem or probe declared remote endpoints;
this runner does not pretend a single AWS host is multiple independent locations.
"""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import uuid
import zlib
import store
from workflow import canonical, digest, identifier
from network_spec import observations

def saved(job, claim=False, runner='custom-package-v1'):
    return store.rpc('research_network_launch',dict(p_job=job,p_runner=runner,p_claim=claim))

def update(job, phase, data, token=None):
    return store.rpc('research_network_update',dict(p_job=job,p_phase=phase,p_data=data,p_ingest_hash=token))

def sign_artifact(study,artifact_id,ttl):
    rows=store.rows('artifacts',{'study_id':'eq.'+study,'id':'eq.'+artifact_id,'limit':1})
    if not rows: raise ValueError('Private package input is unavailable.')
    path=rows[0]['storage_path']
    if path!=study+'/'+rows[0]['sha256']: raise ValueError('Invalid artifact identity.')
    value=store.request('/storage/v1/object/sign/'+store.BUCKET+'/'+path,'POST',{'expiresIn':ttl})
    return 'https://'+store.PROJECT+'.supabase.co/storage/v1'+value['signedURL']

def user_data(n,spec_url,source_url,token,private_inputs=None):
    """No user string is inserted into shell syntax; all values use JSON/base64."""
    builtin=n['spec']['runner']=='netem-iperf-v1'
    files={'package_runtime.py':(Path(__file__).parent/'package_runtime.py').read_text()}
    if builtin: files['netem_iperf.py']=(Path(__file__).parent/'netem_iperf.py').read_text()
    driver=base64.b64encode(zlib.compress(canonical(files))).decode()
    package=n['spec']['config'].get('package',{})
    settings=dict(job_id=n['job_id'],spec_url=spec_url,source_url=source_url,input_sha256=n['spec_sha256'],package_sha256=package.get('sha256'),builtin=builtin,
        deadline=dt.datetime.fromisoformat(n['deadline'].replace('Z','+00:00')).timestamp(),ingest_url=os.environ['NETWORK_PACKAGE_INGEST_URL'],token=token,
        built=n['spec']['runner']=='built-package-v1',container=package.get('kind')=='container',max_output_bytes=n['spec']['limits']['max_output_bytes'],private_inputs=private_inputs or {})
    setup='''import base64,json,pathlib,urllib.request,hashlib,subprocess,sys,time
s=json.loads(base64.b64decode(sys.argv[1]));root=pathlib.Path('/var/lib/jumpserve-experiment');root.mkdir(mode=0o700,exist_ok=True)
def fetch(url,path,sha):
 with urllib.request.urlopen(url,timeout=20) as r:raw=r.read(256001)
 if len(raw)>256000 or hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Input hash differs on the experiment machine')
 path.write_bytes(raw)
fetch(s['spec_url'],root/'spec.json',s['input_sha256'])
if s['source_url']:fetch(s['source_url'],root/'package.py',s['package_sha256'])
if s['private_inputs']:
 (root/'inputs').mkdir(mode=0o700,exist_ok=True)
 for name,item in s['private_inputs'].items():fetch(item['url'],root/'inputs'/name,item['sha256'])
if s['container']:subprocess.run(['apt-get','install','-y','docker.io'],check=True,timeout=max(1,int(s['deadline']-time.time())))
if s['built']:
 subprocess.run(['apt-get','update'],check=True,timeout=max(1,int(s['deadline']-time.time())))
 subprocess.run(['apt-get','install','-y','python3-venv'],check=True,timeout=max(1,int(s['deadline']-time.time())))
if s['builtin']:
 subprocess.run(['apt-get','update'],check=True,timeout=max(1,int(s['deadline']-time.time())),env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','DEBIAN_FRONTEND':'noninteractive'})
 subprocess.run(['apt-get','install','-y','iproute2','iperf3','iputils-ping','ethtool'],check=True,timeout=max(1,int(s['deadline']-time.time())),env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','DEBIAN_FRONTEND':'noninteractive'})
subprocess.run(['python3','/opt/jumpserve-package-runtime.py','--directory',str(root),'--deadline',str(s['deadline'])],check=False)
files={};remaining=s['max_output_bytes']
for name in ('runtime-receipt.json','output.json','stdout.bin','stderr.bin'):
 p=root/name
 if p.exists():
  with p.open('rb') as stream:raw=stream.read(remaining+1)
  if len(raw)>remaining:raw=raw[:remaining];files[name+'.truncated']=True
  remaining-=len(raw)
  files[name]=base64.b64encode(raw).decode()
payload=json.dumps({'job_id':s['job_id'],'files':files},separators=(',',':')).encode()
req=urllib.request.Request(s['ingest_url'],data=payload,headers={'Authorization':'Bearer '+s['token'],'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=30) as response:response.read(4096)
'''
    encoded=base64.b64encode(canonical(settings)).decode()
    script='#!/bin/bash\nset -euo pipefail\ntrap "/sbin/shutdown -h now" EXIT\n'
    seconds=max(1,int(settings['deadline']-time.time()))
    script+=f'systemd-run --unit jumpserve-deadline --on-active={seconds}s /sbin/shutdown -h now\n'
    script+="install -d -m 700 /var/lib/jumpserve-experiment\npython3 - '"+driver+"' <<'JUMPSERVE_DRIVER'\nimport base64,json,pathlib,sys,zlib\nfor name,source in json.loads(zlib.decompress(base64.b64decode(sys.argv[1]))).items():\n pathlib.Path('/opt/jumpserve-'+name.replace('_','-') if name=='package_runtime.py' else '/var/lib/jumpserve-experiment/'+name).write_text(source)\nJUMPSERVE_DRIVER\n"
    script+='python3 - '+encoded+" <<'JUMPSERVE_SETUP'\n"+setup+'\nJUMPSERVE_SETUP\n'
    if len(script.encode())>16384: raise ValueError('Bootstrap exceeds EC2 user-data limit.')
    # Boto3 base64-encodes EC2 UserData itself; return the original script.
    return script

def handler(event,context=None):
    if set(event)-{'source','job_id','action','runner'} or not {'source','job_id','action'}<=set(event) or event['source']!='jumpserve.network-execution' or event['action'] not in ('launch','status','cancel') or event.get('runner','custom-package-v1') not in ('custom-package-v1','netem-iperf-v1','built-package-v1'): raise ValueError('IAM-only saved networking invocation required.')
    runner=event.get('runner','custom-package-v1')
    job=identifier(event['job_id']);n=saved(job,event['action']=='launch',runner) if runner!='custom-package-v1' else saved(job,event['action']=='launch');claimed=bool(n and event['action']=='launch')
    if n is None:n=saved(job,runner=runner)
    if not n:raise ValueError('Saved package invocation is unavailable.')
    if n['phase'] in ('completed','failed','cancelled'):return n['data']
    import boto3
    if os.environ.get('NETWORK_AWS_ACCOUNT')!=store.ACCOUNT:raise ValueError('Networking AWS account differs.')
    ec2=boto3.client('ec2',region_name='us-east-1');deadline=dt.datetime.fromisoformat(n['deadline'].replace('Z','+00:00')).timestamp()
    if claimed:
        token=secrets.token_hex(32)
        update(job,'dispatching',n['data'],digest(token.encode()))
        q=store.rows('queue_jobs',{'id':'eq.'+job,'study_id':'eq.'+n['study_id'],'limit':1})[0]
        spec_url=sign_artifact(n['study_id'],q['input_artifact_id'],int(n['spec']['limits']['wall_seconds'])+300)
        package=n['spec']['config'].get('package',{})
        source_url=sign_artifact(n['study_id'],package['artifact_id'],int(n['spec']['limits']['wall_seconds'])+300) if package.get('kind')=='python-script' else None
        private_inputs={name:dict(url=sign_artifact(n['study_id'],item['artifact_id'],int(n['spec']['limits']['wall_seconds'])+300),sha256=item['sha256']) for name,item in n['spec']['config'].get('private_inputs',{}).items()}
        ec2.run_instances(ImageId=os.environ['NETWORK_PACKAGE_AMI'],InstanceType='t3.medium',MinCount=1,MaxCount=1,ClientToken=job,
            CreditSpecification={'CpuCredits':'standard'},BlockDeviceMappings=[{'DeviceName':'/dev/sda1','Ebs':{'VolumeSize':8,'VolumeType':'gp3','DeleteOnTermination':True}}],
            InstanceInitiatedShutdownBehavior='terminate',MetadataOptions={'HttpTokens':'required','HttpEndpoint':'disabled'},
            SubnetId=os.environ['NETWORK_PACKAGE_SUBNET'],SecurityGroupIds=[os.environ['NETWORK_PACKAGE_SECURITY_GROUP']],
            UserData=user_data(n,spec_url,source_url,token,private_inputs),
            TagSpecifications=[{'ResourceType':'instance','Tags':[{'Key':'Project','Value':'JumpServeNetworkPackage'},{'Key':'NetworkExecutionId','Value':job}]}])
    result=ec2.describe_instances(Filters=[{'Name':'tag:Project','Values':['JumpServeNetworkPackage']},{'Name':'tag:NetworkExecutionId','Values':[job]}])
    machines=[machine for group in result.get('Reservations',[]) for machine in group.get('Instances',[])]
    if len(machines)>1:raise RuntimeError('More than one machine found for one immutable invocation.')
    n=saved(job,runner=runner) if runner!='custom-package-v1' else saved(job);data=n['data'];cancel=event['action']=='cancel' or n['cancel_requested'];expired=time.time()>=deadline
    machine=machines[0] if machines else None;state=machine.get('State',{}).get('Name') if machine else None
    if machine and (cancel or expired) and state not in ('shutting-down','terminated'):
        ec2.terminate_instances(InstanceIds=[machine['InstanceId']]);state='shutting-down'
    if state!='terminated' and not (machine is None and (cancel or expired)):
        return dict(status='cleaning' if cancel or expired or 'output' in data else state or 'launch-unconfirmed')
    status='cancelled' if cancel else 'failed' if 'output' not in data or data.get('package_failed') else 'completed'
    response=dict(status=status,external_job_id=machine['InstanceId'] if machine else None,started_at=n['started_at'],ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        output=data.get('output'),reason=data.get('reason') or ('Package produced no valid retained observation envelope.' if status=='failed' else None),
        resources=dict(instance_type=machine.get('InstanceType') if machine else None,image_id=machine.get('ImageId') if machine else None,availability_zone=machine.get('Placement',{}).get('AvailabilityZone') if machine else None,region='us-east-1',instance_id=machine['InstanceId'] if machine else None),artifacts=data.get('artifacts',[]))
    update(job,status,response)
    return response

def ingest(event,context=None):
    """Job-specific capability. Source stdout/stderr bytes remain private originals."""
    try:
        headers={k.lower():v for k,v in (event.get('headers') or {}).items()};auth=headers.get('authorization','')
        import re
        if not re.fullmatch(r'Bearer [a-f0-9]{64}',auth):return {'statusCode':401,'body':'{"error":"Job capability required"}'}
        if event.get('isBase64Encoded') or not isinstance(event.get('body'),str) or len(event['body'].encode())>15000000:raise ValueError('Ingestion byte limit exceeded.')
        payload=json.loads(event['body']);job=identifier(payload['job_id'])
        if set(payload)!={'job_id','files'} or not isinstance(payload['files'],dict):raise ValueError('Invalid package output envelope.')
        state=store.rpc('research_network_token',dict(p_job=job,p_hash=digest(auth[7:].encode())))
        if not state:return {'statusCode':403,'body':'{"error":"Expired or closed job capability"}'}
        n=saved(job,runner=state.get('runner','custom-package-v1'));owners=store.rows('study_owners',{'study_id':'eq.'+n['study_id'],'limit':1});actor=owners[0]['owner_id']
        files={};artifacts=[];total=0
        for name,encoded in payload['files'].items():
            if name.endswith('.truncated') and name[:-10] in ('stdout.bin','stderr.bin','output.json','runtime-receipt.json'):continue
            if name not in ('stdout.bin','stderr.bin','output.json','runtime-receipt.json') or not isinstance(encoded,str):raise ValueError('Unexpected output file.')
            raw=base64.b64decode(encoded,validate=True);total+=len(raw)
            if total>n['spec']['limits']['max_output_bytes']:raise ValueError('Combined output budget exceeded.')
            row=store.artifact(n['study_id'],actor,raw,str(uuid.uuid5(uuid.UUID(job),'package:'+name)),'application/octet-stream',dict(job_id=job,original_filename=name,transformation='none; original package bytes',complete=not payload['files'].get(name+'.truncated',False)))
            store.append(n['study_id'],actor,'artifacts',row);artifacts.append(dict(id=row['id'],sha256=row['sha256'],filename=name));files[name]=raw
        output=None;reason=None;failed=any(k.endswith('.truncated') for k in payload['files'])
        try:
            q=store.rows('queue_jobs',{'id':'eq.'+job,'study_id':'eq.'+n['study_id'],'limit':1})[0]
            campaign=store.rows('campaigns',{'id':'eq.'+q['campaign_id'],'study_id':'eq.'+n['study_id'],'limit':1})[0]
            protocol=store.rows('protocols',{'id':'eq.'+campaign['protocol_id'],'study_id':'eq.'+n['study_id'],'limit':1})[0]
            output=observations(files['output.json'],protocol['document']['metrics'],protocol['document']['resource_limits']['max_observations'])
            receipt=json.loads(files['runtime-receipt.json']);failed=failed or receipt['exit_code']!=0 or receipt.get('termination_reason') is not None
            failed=failed or bool(output['provenance'].get('failure'))
            reason=receipt.get('termination_reason') or output['provenance'].get('failure');output['usage']=dict(output['usage'] or {},runtime_receipt=receipt,measured_charges_usd=None)
        except (ValueError,KeyError,TypeError):failed=True;reason='Package output is missing or invalid; original available bytes retained.'
        data=dict(n['data'],output=output,artifacts=artifacts,package_failed=failed,reason=reason)
        update(job,'running',data)
        return {'statusCode':200,'headers':{'Content-Type':'application/json'},'body':'{"retained":true}'}
    except Exception:
        return {'statusCode':400,'headers':{'Content-Type':'application/json'},'body':'{"error":"Package output rejected; preserve original files and invocation identity"}'}

def reap(event,context=None):
    """Independent deadline cleanup continues even if research workers are paused."""
    import urllib.parse
    query=urllib.parse.urlencode(dict(select='job_id,runner',runner='in.(custom-package-v1,netem-iperf-v1,built-package-v1)',phase='not.in.(completed,failed,cancelled)',
        **{'or':'(deadline.lte.'+dt.datetime.now(dt.timezone.utc).isoformat()+',cancel_requested.eq.true)'},limit=100))
    jobs=store.request('/rest/v1/research_network_executions?'+query)
    outcomes=[]
    for row in jobs:
        try:
            event=dict(source='jumpserve.network-execution',job_id=row['job_id'],action='status')
            if row.get('runner') in ('netem-iperf-v1','built-package-v1'): event['runner']=row['runner']
            result=handler(event,context)
            outcomes.append(dict(job_id=row['job_id'],status=result['status']))
        except Exception as error:outcomes.append(dict(job_id=row['job_id'],status='cleanup-unconfirmed',error_type=type(error).__name__))
    return dict(outcomes=outcomes,measured_charges_usd=None)
