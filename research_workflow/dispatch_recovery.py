"""Reconcile native ledgers and retained worker bytes; never replay inference.

Parent failures remain immutable. A separate service-only record accounts for
completed native calls and cleanup. Unknown native outcomes always stay fenced.
"""
import fcntl,io,json,os,re,time,uuid
from pathlib import Path
import store,siwc_protocol as wire
from workflow import canonical,digest

VERSION='builder-dispatch-reconciliation-v1'
CALL_VERSION='builder-native-call-reconciliation-v1'
QUEUED_VERSION='queued-dispatch-reconciliation-v1'
QUEUED_CONTROLLER_VERSION='queued-dispatch-reconciliation-v2'

def queued_reconciliation(task,dispatch,rows):
    """Trust only an immutable server proof of atomic pre-transport closure."""
    marker=(dispatch.get('provenance') or {}).get('queued_admission_reconciliation')
    if marker is None:return None
    found=rows(task,'queued_dispatch_reconciliations',dispatch_id='eq.'+dispatch['id'],limit=2)
    if len(found)!=1:raise ValueError('Original queued-admission proof is unavailable or ambiguous.')
    row=found[0];proof=row['proof'];original=row['snapshot_raw'].encode()
    snapshot=json.loads(original)
    request=json.loads(snapshot['request_raw'])
    kind='builder' if isinstance(request,dict) and isinstance(request.get('builder'),dict) else 'controller'
    contract=(isinstance(request,dict) and
        (proof.get('version')==QUEUED_VERSION and kind=='builder' or
         proof.get('version')==QUEUED_CONTROLLER_VERSION and kind=='controller' and proof.get('request_kind')==kind))
    if (dispatch['status']!='failed' or proof!=marker or not contract or
        digest(original)!=row['snapshot_sha256'] or proof.get('snapshot_sha256')!=row['snapshot_sha256'] or
        any(row.get(k)!=v for k,v in dict(dispatch_id=dispatch['id'],study_id=task['study_id'],task_id=task['id'],request_sha256=dispatch['request_sha256']).items()) or
        any(proof.get(k)!=row[k] for k in ('dispatch_id','study_id','task_id','request_sha256','parent_turn_status')) or
        proof.get('parent_turn_request_sha256')!=dispatch['request_sha256'] or proof.get('parent_turn_status') not in ('failed','unknown','invalid-output') or
        proof.get('original_status')!='queued' or proof.get('terminal_status')!='failed' or proof.get('admission_state')!='pristine-unclaimed' or
        type(proof.get('provider_calls')) is not int or proof['provider_calls']!=0 or proof.get('provider_request_sent') is not False or
        any(snapshot.get(k)!=v for k,v in dict(id=dispatch['id'],study_id=task['study_id'],task_id=task['id'],request_sha256=dispatch['request_sha256'],status='queued',runtime='openclaw-production-v1',provenance={}).items()) or
        any(snapshot.get(k) is not None for k in ('lease_token','lease_until','response_artifact_id','response_sha256','ended_at')) or
        digest(snapshot['request_raw'].encode())!=dispatch['request_sha256']):
        raise ValueError('Queued-admission reconciliation does not match its retained original.')
    return dict(id=dispatch['id'],kind='atomic-unclaimed-dispatch',request_sha256=dispatch['request_sha256'],snapshot_sha256=row['snapshot_sha256'],native_calls=0,
        scope='Original queued admission closed before worker claim; no provider replay or scientific finding.')

def write_once(path,raw):
    """Publish durable bytes atomically; an existing original cannot change."""
    path=Path(path);temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with temporary.open('xb') as sink:
            os.chmod(temporary,0o600);sink.write(raw);sink.flush();os.fsync(sink.fileno())
        try:os.link(temporary,path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes()!=raw:raise ValueError('Durable original bytes differ.')
        descriptor=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:temporary.unlink(missing_ok=True)

def reconciled_call(task,call,original,rows):
    found=rows(task,'builder_call_reconciliations',call_id='eq.'+call['id'],limit=2)
    if not found:return None
    if len(found)!=1:raise ValueError('Ambiguous native call reconciliation.')
    row=found[0];proof=json.loads(original(task,dict(artifact_id=row['artifact_id'],sha256=row['sha256'])))
    if (proof!=row['proof'] or proof.get('version')!=CALL_VERSION or
        any(proof.get(k)!=call[k] for k in ('dispatch_id','task_id','study_id','request_sha256')) or
        proof.get('call_id')!=call['id'] or row['call_id']!=call['id'] or row['request_sha256']!=call['request_sha256'] or
        proof.get('original_status')!=call['status'] or proof.get('status')!='complete' or
        proof.get('response_received') is not True or proof.get('http_status')!=200):
        raise ValueError('Native call reconciliation identity mismatch.')
    return dict(call,status='complete',response_received=True,http_status=200,
        response_artifact_id=proof['response']['artifact_id'],response_sha256=proof['response']['sha256'])

def ledger(task,dispatch,original,rows):
    calls=rows(task,'builder_calls',dispatch_id='eq.'+dispatch['id'],order='number.asc',limit=101)
    if len(calls)>100:raise ValueError('Native reconciliation coverage exceeds 100 calls per dispatch.')
    proofs=[]
    for call in calls:
        if call['study_id']!=task['study_id'] or call['task_id']!=task['id'] or call['dispatch_id']!=dispatch['id']:raise ValueError('Native reconciliation identity mismatch.')
        if call['status']!='complete':
            call=reconciled_call(task,call,original,rows)
            if call is None:return None
        if call.get('response_received') is not True or call.get('http_status')!=200:return None
        request=original(task,dict(id=call['request_artifact_id'],sha256=call['request_sha256']),maximum=256000)
        value=json.loads(request)
        if value.get('model')!='gpt-6.1-sol' or value.get('store') is not False or value.get('stream') is not True or value.get('previous_response_id'):raise ValueError('Original native request contract differs.')
        raw=original(task,dict(id=call['response_artifact_id'],sha256=call['response_sha256']),maximum=1048576)
        sink=io.BytesIO();terminal=wire.read_terminal_stream(io.BytesIO(raw),sink,time.monotonic()+10,1048576)
        if terminal.get('model')!='gpt-6.1-sol':raise ValueError('Original native response model differs.')
        proofs.append(dict(id=call['id'],request_sha256=call['request_sha256'],response_sha256=call['response_sha256']))
    return proofs

def reconcile_native_calls(task,dispatch,directory,retain):
    """Use captured terminal bytes only. Never obtain credentials or call a model."""
    import autonomous_repair as repair,experiment_builder as builder
    calls=repair.rows(task,'builder_calls',dispatch_id='eq.'+dispatch['id'],order='number.asc',limit=101)
    if len(calls)>100:raise ValueError('Native reconciliation coverage exceeds 100 calls per dispatch.')
    receipts=list(directory.glob('call-*-completion.json'))
    if len(receipts)>100:raise ValueError('Native completion receipt coverage exceeds its bound.')
    for call in calls:
        if call['status']=='complete' or reconciled_call(task,call,repair.original,repair.rows):continue
        if call['status']!='unknown':continue
        matches=[]
        for path in receipts:
            if path.is_symlink() or path.stat().st_size>16000:raise ValueError('Invalid native completion receipt.')
            receipt=json.loads(path.read_bytes())
            if receipt.get('call_id')==call['id']:matches.append((path,receipt))
        if len(matches)>1:raise ValueError('Ambiguous original native completion receipt.')
        if matches:
            path,receipt=matches[0];captured_raw=path.read_bytes()
            filename=receipt.get('response_filename','')
            if not re.fullmatch(r'call-[1-9][0-9]*-response\.bin',filename) or path.name!=filename.replace('-response.bin','-completion.json'):
                raise ValueError('Original native response path differs.')
            source=directory/filename
            if not source.is_file() or source.is_symlink() or source.stat().st_size>1048576:continue
            raw=source.read_bytes()
        else:
            # Older workers predate sidecars. Their retained transport fields
            # and original response can still establish a terminal completion;
            # an absent field or merely missing local output cannot do so.
            if not (call.get('response_received') is True and call.get('http_status')==200 and call.get('response_artifact_id') and call.get('response_sha256')):continue
            raw=repair.original(task,dict(artifact_id=call['response_artifact_id'],sha256=call['response_sha256']),maximum=1048576)
            captured_raw=canonical(dict(version='retained-native-call-record-v1',record=call,scope='Canonical projection of the unchanged original database call, not original HTTP receipt bytes.'))
            receipt=dict(version=CALL_VERSION,call_id=call['id'],dispatch_id=call['dispatch_id'],task_id=call['task_id'],study_id=call['study_id'],request_sha256=call['request_sha256'],
                response_sha256=digest(raw),status='complete',response_received=True,http_status=200,capture_origin='retained-database-transport-metadata-and-terminal-stream')
        if (receipt.get('version')!=CALL_VERSION or any(receipt.get(k)!=call[k] for k in ('dispatch_id','task_id','study_id','request_sha256')) or
            call['dispatch_id']!=dispatch['id'] or receipt.get('status')!='complete' or receipt.get('response_received') is not True or receipt.get('http_status')!=200):
            raise ValueError('Original native completion identity mismatch.')
        if digest(raw)!=receipt.get('response_sha256') or call.get('response_sha256') not in (None,digest(raw)):
            raise ValueError('Original native response hash differs.')
        request=repair.original(task,dict(artifact_id=call['request_artifact_id'],sha256=call['request_sha256']),maximum=256000)
        value=json.loads(request)
        if value.get('model')!='gpt-6.1-sol' or value.get('store') is not False or value.get('stream') is not True or value.get('previous_response_id'):
            raise ValueError('Original native request contract differs.')
        terminal=wire.read_terminal_stream(io.BytesIO(raw),io.BytesIO(),time.monotonic()+10,1048576)
        if terminal.get('model')!='gpt-6.1-sol':raise ValueError('Original native response model differs.')
        builder.validate_calls(terminal)
        response=retain('native-'+call['id']+'-response.bin',raw,'builder-reconciled-native-response')
        captured=retain('native-'+call['id']+'-completion.json',captured_raw,'builder-reconciled-native-completion')
        proof=dict(receipt,original_status=call['status'],response=response,completion=captured,container_cleanup='stopped',usage=wire.usage(terminal))
        evidence=retain('native-'+call['id']+'-proof.json',canonical(proof),'builder-native-call-reconciliation')
        store.rpc('research_builder_call_reconcile',dict(p_call=call['id'],p_artifact=evidence['artifact_id'],p_sha256=evidence['sha256'],p_proof=proof))

def confirmed(task,dispatch,receipt,original,rows):
    """Pure recorded-data check. No cleanup, writes, model or experiment calls."""
    if dispatch['status'] not in ('unknown','failed','invalid-output'):return None
    refs={a['name']:a for a in (dispatch.get('provenance') or {}).get('artifacts',[])}
    records=rows(task,'model_reconciliations',dispatch_id='eq.'+dispatch['id'],limit=2)
    proof=None
    if records:
        if len(records)!=1:raise ValueError('Ambiguous native reconciliation record.')
        row=records[0]
        if row['task_id']!=task['id'] or row['request_sha256']!=dispatch['request_sha256']:raise ValueError('Reconciliation original identity mismatch.')
        proof=json.loads(original(task,dict(id=row['artifact_id'],sha256=row['sha256']),maximum=150000))
        if proof!=row['proof']:raise ValueError('Reconciliation record differs from its original artifact.')
        if proof.get('version')!=VERSION or proof.get('dispatch_id')!=dispatch['id'] or proof.get('request_sha256')!=dispatch['request_sha256'] or proof.get('container_cleanup')!='stopped':raise ValueError('Reconciliation proof differs.')
    elif receipt.get('native_tool_cleanup') is True and receipt.get('container_cleanup') in ('stopped','confirmed') and 'logical.json' in refs:
        logical=original(task,refs['logical.json'])
        if digest(logical)!=dispatch['request_sha256'] or not json.loads(logical).get('builder'):return None
    else:return None
    calls=ledger(task,dispatch,original,rows)
    if calls is None:return None
    if proof:
        if proof.get('calls')!=calls:raise ValueError('Native reconciliation call coverage differs.')
        checkpoint=proof.get('checkpoint')
    else:
        if type(receipt.get('provider_calls')) is not int or receipt['provider_calls']!=len(calls) or not calls:return None
        checkpoint=receipt.get('checkpoint')
    if not checkpoint:return None
    original(task,checkpoint,maximum=4000000)
    return dict(id=dispatch['id'],kind='hash-verified-native-ledger-and-cleanup',request_sha256=dispatch['request_sha256'],native_calls=len(calls),checkpoint=checkpoint,
        checkpoint_recorded_at=records[0]['created_at'] if records else dispatch.get('ended_at') or dispatch['created_at'],
        reconciliation_artifact_id=records[0]['artifact_id'] if records else refs['receipt.json'].get('id') or refs['receipt.json']['artifact_id'],scope='Operational recovery, no inference replay or scientific finding.')

def reconcile_spool(spool):
    """Fair bounded scan; one damaged or unavailable spool cannot block others."""
    cursor=Path(spool)/'reconciliation-scan.json'
    after=json.loads(cursor.read_bytes())['after'] if cursor.exists() else None
    if after is not None:after=str(uuid.UUID(after))
    candidates=store.rpc('research_model_reconciliation_candidates',dict(p_after=after))
    if not candidates and after:candidates=store.rpc('research_model_reconciliation_candidates',dict(p_after=None))
    recovered=[]
    for d in candidates:
        try:recovered.extend(_reconcile_dispatches(spool,[d]))
        except Exception as error:
            print(json.dumps(dict(version=VERSION,dispatch_id=d['id'],status='reconciliation-fenced',error_type=type(error).__name__)),flush=True)
    temporary=cursor.with_suffix('.tmp');temporary.write_bytes(canonical(dict(after=candidates[-1]['id'] if candidates else None)));os.replace(temporary,cursor)
    return recovered

def _reconcile_dispatches(spool,dispatches):
    """Worker-only persisted-result recovery, under the ordinary task file lock.

    Historical interrupted builder requests can be accounted for by their
    durable admission ledger, including zero admitted calls. A native unknown
    remains unknown; lack of a local output is never a zero-transport proof.
    """
    import autonomous_repair as repair,experiment_builder as builder
    recovered=[]
    for d in dispatches:
        if d['status']=='queued':
            result=store.rpc('research_model_reconcile_queued',dict(p_dispatch=d['id'],p_request_sha256=d['request_sha256']))
            if result.get('reconciled') is True:recovered.append(d['id'])
            continue
        if not json.loads(d['request_raw']).get('builder'):continue
        if (d.get('provenance') or {}).get('queued_admission_reconciliation'):continue
        if store.rows('model_reconciliations',dict(dispatch_id='eq.'+d['id'],limit=1)):continue
        directory=Path(spool)/d['id'];task_root=Path(spool)/'builder-workspaces'/d['task_id']
        if not directory.is_dir() or directory.is_symlink() or not task_root.is_dir() or task_root.is_symlink():continue
        with (task_root/'workspace.lock').open('a+b') as lock:
            try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:continue
            task=store.rows('autonomy_tasks',dict(id='eq.'+d['task_id'],study_id='eq.'+d['study_id'],limit=1))[0]
            if task['status']=='running' or store.rows('model_dispatches',dict(task_id='eq.'+task['id'],status='in.(queued,running)',limit=1)):continue
            owner=store.rows('study_owners',dict(study_id='eq.'+d['study_id'],limit=1))[0]['owner_id'];task=dict(task,actor_id=owner)
            logical=(directory/'logical.json').read_bytes()
            if logical!=d['request_raw'].encode() or digest(logical)!=d['request_sha256']:raise ValueError('Interrupted worker logical hash differs.')
            builder.container_action(task['id'],'stop')
            workspace=task_root/'workspace'
            # Preserve exact history, including the last pre-transport input.
            history_path=task_root/'native-tool-history.json'
            if history_path.exists():builder.save_history(workspace,json.loads(history_path.read_bytes()))
            metadata=builder.normalize_workspace(workspace,__import__('os').environ.get('OPENCLAW_BUILDER_IMAGE',''))
            def retain(name,raw,kind):
                identity=str(uuid.uuid5(uuid.UUID(d['id']),'reconciliation:'+name+':'+digest(raw)))
                record=store.artifact(d['study_id'],owner,raw,identity,'application/octet-stream',dict(kind=kind,version=VERSION,dispatch_id=d['id']))
                store.append(d['study_id'],owner,'artifacts',record)
                if store.artifact_bytes(d['study_id'],identity,maximum=len(raw)+1)!=raw:raise ValueError('Reconciliation artifact hash differs after retrieval.')
                return dict(artifact_id=identity,sha256=record['sha256'])
            reconcile_native_calls(task,d,directory,retain)
            calls=ledger(task,d,repair.original,repair.rows)
            if calls is None:continue
            normalization=retain('normalization.json',metadata,'builder-reconciliation-metadata')
            checkpoint=retain('checkpoint.zip',builder.checkpoint(workspace),'builder-reconciled-workspace')
            proof=dict(version=VERSION,dispatch_id=d['id'],request_sha256=d['request_sha256'],original_status=d['status'],calls=calls,container_cleanup='stopped',checkpoint=checkpoint,normalization=normalization,
                scope='Native admission ledger and original terminal streams verified; task containers stopped under lock. No provider replay, result rewrite, refund or scientific validation.')
            evidence=retain('proof.json',canonical(proof),'builder-dispatch-reconciliation')
            store.rpc('research_model_reconcile',dict(p_dispatch=d['id'],p_artifact=evidence['artifact_id'],p_sha256=evidence['sha256'],p_proof=proof))
            (task_root/'checkpoint-reference.json').write_bytes(canonical(checkpoint))
            recovered.append(d['id'])
    return recovered
