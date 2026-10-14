"""Durable dispatch of frozen networking jobs to registered cloud executors.

Workers never forward or persist browser credentials. Direct Lambda envelopes
carry only the saved job identity; launchers resolve the owner/configuration.
An uncertain launch is polled by its original identity, never blindly repeated.
"""
import base64
import json
import os
import uuid
import urllib.parse
from pathlib import Path
import sys
import store
import network_spec
from workflow import canonical, digest, identifier, now, validate_record

FUNCTIONS = {'emulated-netem-v1':'NETWORK_EMULATED_FUNCTION','real-world-iperf-v1':'NETWORK_REAL_WORLD_FUNCTION','custom-package-v1':'NETWORK_PACKAGE_FUNCTION'}

def capabilities():
    return dict(version=network_spec.VERSION, runners=[dict(id=k, **v, enabled=bool(os.environ.get(FUNCTIONS[k]))) for k,v in network_spec.RUNNERS.items()], max_jobs=network_spec.MAX_JOBS,
        package_contract='network-observations-v1', limitations='Executable configurations and versioned packages are required. Actual observations do not automatically validate a hypothesis. Missing charges remain unknown.')

def upload(study, actor, body):
    if set(body)!={'request_id','source','version'} or not isinstance(body['source'],str) or not 1<=len(body['source'].encode())<=12000 or not isinstance(body['version'],str) or not 1<=len(body['version'])<=200: raise ValueError('Upload 1–12000 original UTF-8 bytes and declare an immutable package version.')
    row=store.artifact(study,actor,body['source'].encode(),identifier(body['request_id']),'text/x-python',dict(kind='network-python-package',version=body['version'],transformation='none; original uploaded UTF-8 bytes; not executed on the API'))
    saved=store.append(study,actor,'artifacts',row)
    return dict(kind='python-script',version=body['version'],artifact_id=saved['id'],sha256=saved['sha256'])

def upload_input(study,actor,body):
    if set(body)!={'request_id','original_base64','version'} or not isinstance(body['version'],str) or not 1<=len(body['version'])<=200: raise ValueError('Preserve an original private input with its version.')
    raw=base64.b64decode(body['original_base64'],validate=True)
    if not 1<=len(raw)<=256000: raise ValueError('Private inputs must contain 1–256000 original bytes.')
    row=store.artifact(study,actor,raw,identifier(body['request_id']),'application/octet-stream',dict(kind='network-private-input',version=body['version'],transformation='none; original private input bytes'))
    saved=store.append(study,actor,'artifacts',row)
    return dict(artifact_id=saved['id'],sha256=saved['sha256'],version=body['version'])

def validate_input(study, protocol, raw):
    spec=json.loads(raw)
    if not any(canonical(candidate)==raw for candidate in network_spec.specs(protocol['document'])): raise ValueError('Networking input differs from the exact frozen executable specification.')
    if not os.environ.get(FUNCTIONS[spec['runner']]): raise ValueError('This registered networking executor is not enabled in the connected environment. Preserve the plan; do not substitute manual execution silently.')
    package=spec['config'].get('package',{})
    if package.get('kind')=='python-script':
        records=store.rows('artifacts',{'study_id':'eq.'+study,'id':'eq.'+package['artifact_id'],'limit':1})
        if not records or records[0]['provenance'].get('kind')!='network-python-package' or records[0]['provenance'].get('version')!=package['version']: raise ValueError('Package type/version differs from the preserved private original.')
        source=store.artifact_bytes(study,package['artifact_id'],maximum=12000)
        if digest(source)!=package['sha256']: raise ValueError('Private package bytes differ from the reviewed version.')
    total=0
    for item in spec['config'].get('private_inputs',{}).values():
        records=store.rows('artifacts',{'study_id':'eq.'+study,'id':'eq.'+item['artifact_id'],'limit':1})
        if not records or records[0]['provenance'].get('kind')!='network-private-input' or records[0]['provenance'].get('version')!=item['version']: raise ValueError('Private input version differs from its preserved original.')
        data=store.artifact_bytes(study,item['artifact_id']);total+=len(data)
        if digest(data)!=item['sha256'] or total>protocol['document']['resource_limits']['max_input_bytes']: raise ValueError('Private inputs exceed the frozen byte budget or differ from their hashes.')
    return spec

def invoke(spec, job_id, action):
    import boto3
    name=os.environ[FUNCTIONS[spec['runner']]]
    if not name.startswith('arn:aws:lambda:') or name.split(':')[4]!=store.ACCOUNT: raise store.StoreError('Networking executor target account differs.')
    response=boto3.client('lambda',region_name=name.split(':')[3]).invoke(FunctionName=name,InvocationType='RequestResponse',
        Payload=canonical(dict(source='jumpserve.network-execution',job_id=job_id,action=action)))
    raw=response['Payload'].read(12000001)
    if len(raw)>12000000 or response.get('FunctionError'): raise store.StoreError('Networking executor response is unconfirmed; preserve the original invocation identity.')
    answer=json.loads(raw)
    if answer.get('statusCode',200)>=400: raise store.StoreError('Networking executor rejected the saved request.')
    return json.loads(answer['body']) if 'body' in answer else answer

def evidence(job, campaign, protocol, spec, raw, external):
    """One stable terminal run per invocation; package-level timings are descriptive."""
    invalid_output=None
    try:result=network_spec.observations(raw,protocol['document']['metrics'],protocol['document']['resource_limits']['max_observations'])
    except (ValueError,KeyError,TypeError):
        invalid_output='Executor output violates the frozen observation schema, units or budget; available original bytes retained.'
        result=dict(observations=[],provenance=dict(invalid_output=invalid_output),usage=None)
    uid=lambda name:str(uuid.uuid5(uuid.UUID(job['id']),name))
    rows=[validate_record('measurements',dict(id=uid('measurement:'+r['observation_key']+':'+r['metric']),run_id=job['id'],published_id=None,
        observation_id=r['observation_key'],configuration_identity=spec['configuration_identity'],metric=r['metric'],units=r['units'],value=r['value'],status=r['status'],reason=r['reason'],
        details=dict(r['metadata'],replication=spec['replication'],data_origin=protocol['document'].get('data_origin','unclassified'),execution_environment=spec['config'].get('mode',network_spec.RUNNERS[spec['runner']]['mode']),producer=result['provenance']))) for r in result['observations']]
    counts={m['name']:sum(r['metric']==m['name'] for r in rows) for m in protocol['document']['metrics']}
    coverage_gap=any(counts[m['name']]==0 or m.get('planned_observations') is not None and counts[m['name']]!=m['planned_observations'] for m in protocol['document']['metrics'])
    status='excluded' if external['status']=='cancelled' else 'failed' if external['status']!='completed' or invalid_output else 'partial' if any(r['status']!='recorded' for r in rows) or not rows or coverage_gap else 'complete'
    run=validate_record('runs',dict(id=job['id'],campaign_id=campaign['id'],configuration_id=None,
        status=status,
        reason=invalid_output or external.get('reason') or (None if status=='complete' else 'Invocation cancelled; available observations retained.' if status=='excluded' else 'Some declared observations are unavailable.' if rows else 'No observations were returned.'),started_at=external['started_at'],ended_at=external['ended_at'],
        analysis_version=network_spec.VERSION,input_sha256=digest(canonical(spec)),output_sha256=digest(raw),requested_resources=spec['limits'],
        actual_resources=external.get('resources',{}),usage=dict(package_usage=result['usage'],measured_charges_usd=None,estimated_charges_usd=None,model_usage=None),
        provenance=dict(runner=spec['runner'],configuration_identity=spec['configuration_identity'],replication=spec['replication'],external_job_id=external.get('external_job_id'),
            protocol_id=protocol.get('id'),protocol_sha256=protocol.get('sha256'),
            execution_implementation_sha256=digest(Path(__file__).read_bytes()),spec_implementation_sha256=digest(Path(network_spec.__file__).read_bytes()),python_version=sys.version.split()[0],
            executor_response_sha256=digest(raw),executor_response_scope='Canonical normalized executor envelope; original package files retained separately',
            original_artifacts=external.get('artifacts',[]),observation_contract='network-observations-v1',producer=result['provenance'],automatic_retries=0,
            limits='Returned package observations require scientific review; independence, causal validity and correctness are not established by successful execution.')))
    summaries=[]
    for metric in protocol['document']['metrics']:
        if metric.get('planned_observations') is None: continue
        planned=network_spec.integer(metric['planned_observations'],'planned_observations',1,protocol['document']['resource_limits']['max_observations'])
        selected=[r for r in rows if r['metric']==metric['name']];counts={state:sum(r['status']==state for r in selected) for state in ('recorded','missing','invalid','ambiguous','excluded')}
        counts['missing']+=max(0,planned-len(selected))
        summaries.append(validate_record('summaries',dict(id=uid('summary:'+metric['name']),run_id=job['id'],metric=metric['name'],units=metric['units'],
            statistics=dict(counts=counts,observations_returned=len(selected),planned_observations=planned),interval_kind='none',uncertainty_method='No inferential interval is computed by the execution layer; apply a separately declared analysis to the recorded replication structure.',
            planned=planned,unexpected=max(0,len(selected)-planned),**counts)))
    return run,rows,summaries

def advance(job, campaign, protocol, raw):
    spec=validate_input(job['study_id'],protocol,raw)
    # Insert once under the queue lease; this records an attempt before cloud I/O.
    state=store.rpc('research_network_begin',dict(p_job=job['id'],p_token=job['lease_token'],p_spec=spec,p_sha=digest(raw)))
    if state['phase']=='pending':
        try: invoke(spec,job['id'],'launch')
        except Exception:
            # Launch acknowledgement may be lost; query the stable external ID.
            pass
    response=state['data'] if state['phase'] in ('completed','failed','cancelled') else invoke(spec,job['id'],'cancel' if state['cancel_requested'] else 'status')
    if response['status'] not in ('completed','failed','cancelled'):
        return dict(status='running',job_id=job['id'],external_status=response['status'])
    if state['phase'] not in ('completed','failed','cancelled'):
        response.setdefault('ended_at',now())
        if response['ended_at'] is None: response['ended_at']=now()
        store.rpc('research_network_update',dict(p_job=job['id'],p_phase=response['status'],p_data=response,p_ingest_hash=None))
    output=response.get('output')
    if output is None:
        output=dict(version='network-observations-v1',observations=[],provenance=dict(runner=spec['runner'],failure=response.get('reason','Executor returned no observations.')),usage=None)
    output_raw=canonical(output)
    owners=store.rows('study_owners',{'study_id':'eq.'+job['study_id'],'limit':1});actor=owners[0]['owner_id']
    artifact=store.artifact(job['study_id'],actor,output_raw,str(uuid.uuid5(uuid.UUID(job['id']),'network-output')),'application/json',dict(job_id=job['id'],transformation='Canonical normalized executor observation envelope; original package files referenced separately; no claim classification'))
    run,measurements,summaries=evidence(job,campaign,protocol,spec,output_raw,response)
    entries=[dict(kind='artifacts',record=artifact),dict(kind='runs',record=run)]
    entries.extend(dict(kind=kind,record=row) for kind,rows in [('measurements',measurements),('summaries',summaries)] for row in rows)
    return store.rpc('research_queue_finish',dict(p_job=job['id'],p_token=job['lease_token'],p_records=entries,p_run=job['id'],p_error=run['reason'],p_usage=run['usage']))

def pending():
    return store.rpc('research_network_pending',{})

def cancel(study,actor,body):
    if set(body)!={'request_id','job_id','reason'}: raise ValueError('Cancellation needs the original job identity and a reason.')
    return store.rpc('research_network_cancel',dict(p_study=study,p_actor=actor,p_job=identifier(body['job_id']),p_id=identifier(body['request_id']),p_reason=body['reason']))
