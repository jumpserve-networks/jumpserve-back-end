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

FUNCTIONS = {'bounded-tcp-v1':'NETWORK_BOUNDED_FUNCTION','emulated-netem-v1':'NETWORK_EMULATED_FUNCTION','real-world-iperf-v1':'NETWORK_REAL_WORLD_FUNCTION','custom-package-v1':'NETWORK_PACKAGE_FUNCTION'}
FUNCTIONS.update({'netem-iperf-v1':'NETWORK_NETEM_IPERF_FUNCTION','source-arithmetic-v1':None,'built-package-v1':'NETWORK_BUILT_PACKAGE_FUNCTION'})
BOUNDED_CAPTURE_VERSION='bounded-tcp-retained-response-v1'

def bounded_data(state,spec,original,artifact,receipt):
    """Build a terminal envelope from captured bytes, never launch an executor."""
    failed=bool(receipt['function_error'])
    result=None if failed else json.loads(original)
    return dict(status='failed' if failed or result.get('provenance',{}).get('failure') else 'completed',started_at=state['started_at'],ended_at=receipt['ended_at'],output=result,
        reason='Bounded executor failed; original response retained.' if failed else result.get('provenance',{}).get('failure'),
        artifacts=[dict(id=artifact['id'],sha256=artifact['sha256'])],
        resources=dict(function_version=receipt['function_version'],memory_mb=128,timeout_seconds=5,experiment_instances=0,remote_network_bytes=0,
            cleanup='Invocation ended; no provisioned experiment machines.',original_response_sha256=digest(original),reserved_cents=receipt['reserved_cents'],measured_charges_usd=None))

def retained_bounded_response(job,state,spec):
    identity=str(uuid.uuid5(uuid.UUID(job['id']),'bounded-original-response'))
    records=store.rows('artifacts',dict(study_id='eq.'+job['study_id'],id='eq.'+identity,limit=2))
    if not records:return None
    if len(records)!=1:raise ValueError('Ambiguous retained bounded response.')
    record=records[0];receipt=record.get('provenance',{})
    if receipt.get('capture_version')!=BOUNDED_CAPTURE_VERSION:return None  # Legacy capture lacks transport metadata.
    if (receipt.get('kind')!='bounded-tcp-original-lambda-response' or receipt.get('complete') is not True or
        receipt.get('spec_sha256')!=digest(canonical(spec)) or receipt.get('started_at')!=state['started_at'] or
        not isinstance(receipt.get('ended_at'),str) or not isinstance(receipt.get('function_version'),str) or
        not receipt['function_version'].startswith('arn:aws:lambda:us-east-1:'+store.ACCOUNT+':function:') or
        receipt.get('response_status_code')!=200 or 'function_error' not in receipt or type(receipt.get('reserved_cents')) is not int):
        raise ValueError('Retained bounded response identity or transport receipt differs.')
    original=store.artifact_bytes(job['study_id'],identity,maximum=32000)
    if len(original)!=record['byte_count'] or digest(original)!=record['sha256']:raise ValueError('Retained bounded response hash differs.')
    return bounded_data(state,spec,original,record,receipt)

def enabled(runner):
    return runner=='source-arithmetic-v1' or bool(os.environ.get(FUNCTIONS[runner]))

def arithmetic_response(job,state,spec):
    if state['phase'] in ('completed','failed','cancelled'): return state['data']
    import datetime as dt
    if state['cancel_requested'] or dt.datetime.now(dt.timezone.utc)>=dt.datetime.fromisoformat(state['deadline'].replace('Z','+00:00')):
        data=dict(status='cancelled' if state['cancel_requested'] else 'failed',started_at=state['started_at'],ended_at=now(),output=None,reason='Source calculation cancelled or retrieval deadline expired; no unavailable value was imputed.')
    else:
        if state['phase']!='pending': return dict(status='launch-unconfirmed')
        if not store.rpc('research_network_launch',dict(p_job=job['id'],p_runner=spec['runner'],p_claim=True)): return dict(status='launch-unconfirmed')
        try:
            output=arithmetic_output(job['study_id'],spec['config'])
            data=dict(status='completed',started_at=state['started_at'],ended_at=now(),output=output,reason=None,resources=dict(experiment_instances=0,external_network_experiment=False))
        except (ValueError,KeyError,store.StoreError):
            data=dict(status='failed',started_at=state['started_at'],ended_at=now(),output=None,reason='Source calculation failed verification or retrieval; original source identities retained.')
    store.rpc('research_network_update',dict(p_job=job['id'],p_phase=data['status'],p_data=data,p_ingest_hash=None))
    return data

def arithmetic_output(study,config):
    import source_arithmetic
    source=store.rows('sources',dict(study_id='eq.'+study,id='eq.'+config['source_id'],limit=1))
    artifact=store.rows('artifacts',dict(study_id='eq.'+study,id='eq.'+config['text_artifact_id'],limit=1))
    if not source or not artifact or artifact[0]['provenance'].get('kind')!='derived-pdf-text' or artifact[0]['sha256']!=config['text_sha256']: raise ValueError('Retained source/text artifact identity differs.')
    raw=store.artifact_bytes(study,config['text_artifact_id'],maximum=1_000_000)
    return source_arithmetic.envelope(config,raw,source[0]['sha256'])

def bounded_response(job,state,spec):
    """Claim one synchronous attempt durably; unknown responses never relaunch."""
    import datetime as dt
    import autonomy_costs
    if state['phase'] in ('completed','failed','cancelled'):return state['data']
    if state['phase']!='pending':
        retained=retained_bounded_response(job,state,spec)
        if retained:
            store.rpc('research_network_update',dict(p_job=job['id'],p_phase=retained['status'],p_data=retained,p_ingest_hash=None))
            return retained
    deadline=dt.datetime.fromisoformat(state['deadline'].replace('Z','+00:00'))
    expired=dt.datetime.now(dt.timezone.utc)>=deadline
    if state['cancel_requested'] or expired:
        data=dict(status='cancelled' if state['cancel_requested'] else 'failed',started_at=state['started_at'],ended_at=now(),
            output=None,reason='Execution stopped or its one bounded invocation ended without a confirmed response; no replay.',
            resources=dict(experiment_instances=0,remote_network_bytes=0,watchdog='Immutable Lambda five-second timeout'))
        store.rpc('research_network_update',dict(p_job=job['id'],p_phase=data['status'],p_data=data,p_ingest_hash=None))
        return data
    if state['phase']!='pending':return dict(status='launch-unconfirmed')
    quote=autonomy_costs.bounded_quote(canonical(spec))
    # A failed acknowledgement leaves dispatching, never another provider call.
    claimed=store.rpc('research_network_launch',dict(p_job=job['id'],p_runner=spec['runner'],p_claim=True))
    if not claimed:return dict(status='launch-unconfirmed')
    import boto3
    from botocore.config import Config
    client=boto3.client('lambda',region_name='us-east-1',config=Config(connect_timeout=2,read_timeout=10,retries=dict(total_max_attempts=1,mode='standard')))
    target=quote['quote']['function_version']
    configuration=client.get_function_configuration(FunctionName=target)
    if configuration['MemorySize']!=128 or configuration['Timeout']!=5 or configuration.get('EphemeralStorage',{}).get('Size',512)!=512 or configuration.get('VpcConfig',{}).get('VpcId') or configuration.get('Runtime')!='python3.12' or configuration.get('Handler')!='index.handler':
        raise ValueError('Deployed TCP resource configuration differs from the approved cost envelope; no experiment invoked.')
    response=client.invoke(FunctionName=target,InvocationType='RequestResponse',Payload=canonical(dict(version=spec['runner'],config=spec['config'])))
    original=response['Payload'].read(32001)
    # Preserve both successful and failed original response bytes before parsing.
    owner=store.rows('study_owners',{'study_id':'eq.'+job['study_id'],'limit':1})[0]['owner_id']
    receipt=dict(capture_version=BOUNDED_CAPTURE_VERSION,kind='bounded-tcp-original-lambda-response',transformation='none; original synchronous Lambda payload bytes' if len(original)<=32000 else 'Unmodified captured prefix; full response unavailable after transport bound.',complete=len(original)<=32000,spec_sha256=digest(canonical(spec)),
        started_at=state['started_at'],ended_at=now(),function_version=target,function_error=response.get('FunctionError'),response_status_code=response.get('StatusCode'),reserved_cents=quote['cents'])
    artifact=store.artifact(job['study_id'],owner,original,str(uuid.uuid5(uuid.UUID(job['id']),'bounded-original-response')),'application/json',receipt)
    store.append(job['study_id'],owner,'artifacts',artifact)
    if len(original)>32000:raise ValueError('TCP executor output exceeded its approved transport bound; retained prefix is not complete output.')
    data=bounded_data(state,spec,original,artifact,receipt)
    store.rpc('research_network_update',dict(p_job=job['id'],p_phase=data['status'],p_data=data,p_ingest_hash=None))
    return data

def capabilities():
    return dict(version=network_spec.VERSION, runners=[dict(id=k, **v, enabled=enabled(k)) for k,v in network_spec.RUNNERS.items()], max_jobs=network_spec.MAX_JOBS,
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
    if not enabled(spec['runner']): raise ValueError('This registered networking executor is not enabled in the connected environment. Preserve the plan; do not substitute manual execution silently.')
    if spec['runner']=='source-arithmetic-v1': arithmetic_output(study,spec['config'])
    package=spec['config'].get('package',{})
    if package.get('kind')=='python-script':
        records=store.rows('artifacts',{'study_id':'eq.'+study,'id':'eq.'+package['artifact_id'],'limit':1})
        expected='builder-server-wrapper' if spec['runner']=='built-package-v1' else 'network-python-package'
        if not records or records[0]['provenance'].get('kind')!=expected or records[0]['provenance'].get('version')!=package['version']: raise ValueError('Package type/version differs from the preserved private original.')
        source=store.artifact_bytes(study,package['artifact_id'],maximum=12000)
        if digest(source)!=package['sha256']: raise ValueError('Private package bytes differ from the reviewed version.')
        if spec['runner']=='built-package-v1':
            import generated_package
            if source!=Path(generated_package.__file__).read_bytes(): raise ValueError('Built package wrapper differs from this server revision.')
    total=0
    for item in spec['config'].get('private_inputs',{}).values():
        records=store.rows('artifacts',{'study_id':'eq.'+study,'id':'eq.'+item['artifact_id'],'limit':1})
        expected='builder-frozen-project' if spec['runner']=='built-package-v1' else 'network-private-input'
        if not records or records[0]['provenance'].get('kind')!=expected or records[0]['provenance'].get('version')!=item['version']: raise ValueError('Private input version differs from its preserved original.')
        data=store.artifact_bytes(study,item['artifact_id']);total+=len(data)
        if digest(data)!=item['sha256'] or total>protocol['document']['resource_limits']['max_input_bytes']: raise ValueError('Private inputs exceed the frozen byte budget or differ from their hashes.')
        if spec['runner']=='built-package-v1': generated_package.members(data)
    return spec

def invoke(spec, job_id, action):
    import boto3
    name=os.environ[FUNCTIONS[spec['runner']]]
    if not name.startswith('arn:aws:lambda:') or name.split(':')[4]!=store.ACCOUNT: raise store.StoreError('Networking executor target account differs.')
    payload=dict(source='jumpserve.network-execution',job_id=job_id,action=action)
    if spec['runner'] in ('netem-iperf-v1','built-package-v1'): payload['runner']=spec['runner']
    response=boto3.client('lambda',region_name=name.split(':')[3]).invoke(FunctionName=name,InvocationType='RequestResponse',Payload=canonical(payload))
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
    producer=result['provenance']
    if spec['runner']=='netem-iperf-v1':
        import experiment_environment
        # Original command results stay in the hash-bound raw artifact rather
        # than being copied into every observation and inflating SQL bundles.
        producer={k:v for k,v in producer.items() if k!='commands'}
        producer['environment_failures']=experiment_environment.failures(result['provenance'])
        producer.update(command_count=len(result['provenance'].get('commands',[])),commands_sha256=digest(canonical(result['provenance'].get('commands',[]))),
            raw_envelope_sha256=digest(raw),raw_artifact_id=uid('network-output'),scope='Full original command outputs retained in the referenced raw envelope artifact.')
    rows=[validate_record('measurements',dict(id=uid('measurement:'+r['observation_key']+':'+r['metric']),run_id=job['id'],published_id=None,
        observation_id=r['observation_key'],configuration_identity=spec['configuration_identity'],metric=r['metric'],units=r['units'],value=r['value'],status=r['status'],reason=r['reason'],
        details=dict(r['metadata'],replication=spec['replication'],data_origin=protocol['document'].get('data_origin','unclassified'),execution_environment=spec['config'].get('mode',network_spec.RUNNERS[spec['runner']]['mode']),producer=producer))) for r in result['observations']]
    counts={m['name']:sum(r['metric']==m['name'] for r in rows) for m in protocol['document']['metrics']}
    coverage_gap=any(counts[m['name']]==0 or m.get('planned_observations') is not None and counts[m['name']]!=m['planned_observations'] for m in protocol['document']['metrics'])
    status='excluded' if external['status']=='cancelled' else 'failed' if external['status']!='completed' or invalid_output else 'partial' if any(r['status']!='recorded' for r in rows) or not rows or coverage_gap else 'complete'
    run=validate_record('runs',dict(id=job['id'],campaign_id=campaign['id'],configuration_id=None,
        status=status,
        reason=invalid_output or external.get('reason') or (None if status=='complete' else 'Invocation cancelled; available observations retained.' if status=='excluded' else 'Some declared observations are unavailable.' if rows else 'No observations were returned.'),started_at=external['started_at'],ended_at=external['ended_at'],
        analysis_version=network_spec.VERSION,input_sha256=digest(canonical(spec)),output_sha256=digest(raw),requested_resources=spec['limits'],
        actual_resources=external.get('resources',{}),usage=dict(package_usage=result['usage'],measured_charges_usd=None,estimated_charges_usd=None,model_usage=None),
        provenance=dict(runner=spec['runner'],data_origin=protocol['document'].get('data_origin','unclassified'),execution_environment=spec['config'].get('mode',network_spec.RUNNERS[spec['runner']]['mode']),configuration_identity=spec['configuration_identity'],replication=spec['replication'],external_job_id=external.get('external_job_id'),
            protocol_id=protocol.get('id'),protocol_sha256=protocol.get('sha256'),
            execution_implementation_sha256=digest(Path(__file__).read_bytes()),spec_implementation_sha256=digest(Path(network_spec.__file__).read_bytes()),python_version=sys.version.split()[0],
            executor_response_sha256=digest(raw),executor_response_scope='Canonical normalized executor envelope; original package files retained separately',
            original_artifacts=external.get('artifacts',[]),observation_contract='network-observations-v1',producer=producer,automatic_retries=0,
            limits='Returned package observations require scientific review; independence, causal validity and correctness are not established by successful execution.')))
    summaries=[]
    for metric in protocol['document']['metrics']:
        if metric.get('planned_observations') is None: continue
        planned=network_spec.integer(metric['planned_observations'],'planned_observations',1,protocol['document']['resource_limits']['max_observations'])
        selected=[r for r in rows if r['metric']==metric['name']];counts={state:sum(r['status']==state for r in selected) for state in ('recorded','missing','invalid','ambiguous','excluded')}
        counts['missing']+=max(0,planned-len(selected))
        statistics=dict(counts=counts,observations_returned=len(selected),planned_observations=planned)
        if spec['runner']=='source-arithmetic-v1':
            statistics['numerical_consistency']=dict(consistent=sum(r['status']=='recorded' and r['details'].get('matched') is True for r in selected),inconsistent=sum(r['status']=='recorded' and r['details'].get('matched') is False for r in selected),
                criterion='Frozen operation and ROUND_HALF_EVEN decimal places against source-reported result or declared strict-increase criterion.',scope='Source numerical consistency only; not an empirical paper verdict.')
        summaries.append(validate_record('summaries',dict(id=uid('summary:'+metric['name']),run_id=job['id'],metric=metric['name'],units=metric['units'],
            statistics=statistics,interval_kind='none',uncertainty_method='No inferential interval is computed by the execution layer; apply a separately declared analysis to the recorded replication structure.',
            planned=planned,unexpected=max(0,len(selected)-planned),**counts)))
    return run,rows,summaries

def advance(job, campaign, protocol, raw):
    spec=validate_input(job['study_id'],protocol,raw)
    # Insert once under the queue lease; this records an attempt before cloud I/O.
    state=store.rpc('research_network_begin',dict(p_job=job['id'],p_token=job['lease_token'],p_spec=spec,p_sha=digest(raw)))
    if spec['runner']=='bounded-tcp-v1':
        response=bounded_response(job,state,spec)
    elif spec['runner']=='source-arithmetic-v1':
        response=arithmetic_response(job,state,spec)
    elif state['phase']=='pending':
        try: invoke(spec,job['id'],'launch')
        except Exception:
            # Launch acknowledgement may be lost; query the stable external ID.
            pass
    if spec['runner'] not in ('bounded-tcp-v1','source-arithmetic-v1'):response=state['data'] if state['phase'] in ('completed','failed','cancelled') else invoke(spec,job['id'],'cancel' if state['cancel_requested'] else 'status')
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
    if spec['runner']=='source-arithmetic-v1':
        # A declared boolean criterion is not a published numeric measurement.
        # Retain actual reported numeric cells separately from computed results.
        from decimal import Decimal
        from source_arithmetic import decimal_token
        for row in measurements:
            info=row['details']
            if info.get('operation')=='strict-increase' or info.get('reported') is None: continue
            cell=info['cells'][-1];lexeme=cell['original_numeric_lexeme'];value=float(decimal_token(lexeme))
            if Decimal(str(value))!=decimal_token(lexeme): continue
            published_id=str(uuid.uuid5(uuid.UUID(job['id']),'published:'+row['observation_id']))
            published=validate_record('published_values',dict(id=published_id,source_id=spec['config']['source_id'],location='PDF page '+str(cell['page']),observation_id=row['observation_id'],configuration_identity=row['configuration_identity'],metric=row['metric'],units=row['units'],value=value,status='recorded',reason=None,
                extraction=canonical(dict(quote=cell['quote'],token_index=cell['token_index'],original_numeric_lexeme=lexeme,text_sha256=spec['config']['text_sha256'],transformation='Exact source token; separate from computed measurement.')).decode()))
            entries.append(dict(kind='published_values',record=published));row['published_id']=published_id
    entries.extend(dict(kind=kind,record=row) for kind,rows in [('measurements',measurements),('summaries',summaries)] for row in rows)
    return store.rpc('research_queue_finish',dict(p_job=job['id'],p_token=job['lease_token'],p_records=entries,p_run=job['id'],p_error=run['reason'],p_usage=run['usage']))

def pending():
    return store.rpc('research_network_pending',{})

def cancel(study,actor,body):
    if set(body)!={'request_id','job_id','reason'}: raise ValueError('Cancellation needs the original job identity and a reason.')
    return store.rpc('research_network_cancel',dict(p_study=study,p_actor=actor,p_job=identifier(body['job_id']),p_id=identifier(body['request_id']),p_reason=body['reason']))
