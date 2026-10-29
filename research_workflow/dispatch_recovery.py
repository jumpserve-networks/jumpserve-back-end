"""Reconcile native ledgers and retained worker bytes; never replay inference.

Parent failures remain immutable. A separate service-only record accounts for
completed native calls and cleanup. Unknown native outcomes always stay fenced.
"""
import fcntl,io,json,time,uuid
from pathlib import Path
import store,siwc_protocol as wire
from workflow import canonical,digest

VERSION='builder-dispatch-reconciliation-v1'

def ledger(task,dispatch,original,rows):
    calls=rows(task,'builder_calls',dispatch_id='eq.'+dispatch['id'],order='number.asc',limit=101)
    if len(calls)>100:raise ValueError('Native reconciliation coverage exceeds 100 calls per dispatch.')
    proofs=[]
    for call in calls:
        if call['study_id']!=task['study_id'] or call['task_id']!=task['id'] or call['dispatch_id']!=dispatch['id']:raise ValueError('Native reconciliation identity mismatch.')
        if call['status']!='complete' or call.get('response_received') is not True or call.get('http_status')!=200:return None
        request=original(task,dict(id=call['request_artifact_id'],sha256=call['request_sha256']),maximum=256000)
        value=json.loads(request)
        if value.get('model')!='gpt-6.1-sol' or value.get('store') is not False or value.get('stream') is not True or value.get('previous_response_id'):raise ValueError('Original native request contract differs.')
        raw=original(task,dict(id=call['response_artifact_id'],sha256=call['response_sha256']),maximum=1048576)
        sink=io.BytesIO();terminal=wire.read_terminal_stream(io.BytesIO(raw),sink,time.monotonic()+10,1048576)
        if terminal.get('model')!='gpt-6.1-sol':raise ValueError('Original native response model differs.')
        proofs.append(dict(id=call['id'],request_sha256=call['request_sha256'],response_sha256=call['response_sha256']))
    return proofs

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
    cursor.write_bytes(canonical(dict(after=candidates[-1]['id'] if candidates else None)))
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
        if not json.loads(d['request_raw']).get('builder'):continue
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
            calls=ledger(task,d,repair.original,repair.rows)
            if calls is None:continue
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
            normalization=retain('normalization.json',metadata,'builder-reconciliation-metadata')
            checkpoint=retain('checkpoint.zip',builder.checkpoint(workspace),'builder-reconciled-workspace')
            proof=dict(version=VERSION,dispatch_id=d['id'],request_sha256=d['request_sha256'],original_status=d['status'],calls=calls,container_cleanup='stopped',checkpoint=checkpoint,normalization=normalization,
                scope='Native admission ledger and original terminal streams verified; task containers stopped under lock. No provider replay, result rewrite, refund or scientific validation.')
            evidence=retain('proof.json',canonical(proof),'builder-dispatch-reconciliation')
            store.rpc('research_model_reconcile',dict(p_dispatch=d['id'],p_artifact=evidence['artifact_id'],p_sha256=evidence['sha256'],p_proof=proof))
            (task_root/'checkpoint-reference.json').write_bytes(canonical(checkpoint))
            recovered.append(d['id'])
    return recovered
