"""Evidence-backed native HTTP failures and task-local reconciliation backoff.

Provider processing/charges can remain unknown. A non-success HTTP response in
our buffered function-only bridge is never delivered to the local tool runner.
No old request is reissued and no original run or reservation is rewritten.
"""
import datetime as dt
import json
from pathlib import Path
from .common import POLICY, canonical, digest, Hold

VERSION='platform-native-http-reconciliation-v1'
TRANSPORT='buffered-native-function-only-v1'
HTTP_ERRORS=(500,502,503,504)
FUNCTIONS={'read','write','edit','exec','process'}
BACKOFF_VERSION='platform-reconciliation-backoff-v1'


def original(path, maximum):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>maximum:
        raise Hold('Original native evidence is missing, unsafe or oversized.','reconciling')
    raw=path.read_bytes()
    if len(raw)>maximum:raise Hold('Original native evidence exceeds its bound.','reconciling')
    return raw


def http_failure(run, receipt, request, response):
    if not isinstance(receipt,dict):raise Hold('Original native receipt is invalid.','reconciling')
    code=receipt.get('http_status')
    if type(code) is not int or code not in HTTP_ERRORS:return None
    expected_reason='Provider HTTP '+str(code)+'.'
    if (receipt.get('id')!=run['id'] or run['status']!='unknown'
        or receipt.get('status') not in ('unknown','failed')
        or receipt.get('request_sha256')!=run['request_sha256']
        or digest(request)!=run['request_sha256']
        or receipt.get('response_sha256')!=digest(response)
        or receipt.get('reason')!=expected_reason or run.get('reason')!=expected_reason
        or receipt.get('usage') is not None or len(response)>1000000):
        raise Hold('Original native HTTP evidence identity or hash differs.','reconciling')
    try:value=json.loads(request)
    except (ValueError,UnicodeError):raise Hold('Original native request cannot be verified.','reconciling') from None
    tools=value.get('tools') if isinstance(value,dict) else None
    if (not isinstance(value,dict) or value.get('model')!=POLICY['model']
        or value.get('store') is not False or value.get('stream') is not True
        or value.get('previous_response_id') is not None or value.get('background',False) is not False
        or not isinstance(tools,list) or not tools
        or any(not isinstance(t,dict) or t.get('type')!='function' or t.get('name') not in FUNCTIONS for t in tools)):
        raise Hold('Function-only buffered native request required for HTTP reconciliation.','reconciling')
    # In native.inference, the non-200 branch records bytes and raises before
    # returning anything to OpenClaw. This proof does not assert that a provider
    # did no work, charged nothing, or that an experiment/deployment succeeded.
    return dict(version=VERSION,transport=TRANSPORT,run_id=run['id'],task_id=run['task_id'],
        request_sha256=run['request_sha256'],response_sha256=digest(response),http_status=code,
        status='failed',response_forwarded_to_agent=False,tool_calls_executed_from_response=0,
        provider_calls=1,provider_processing_unknown=True,measured_charges_usd=None,
        original_status=run['status'],provider_replay=False,scientific_completion=False)


def reference(store, ident, raw):
    found=store.rows('artifacts',id='eq.'+ident)
    if (len(found)!=1 or found[0]['task_id']!=store.task['id']
        or found[0]['sha256']!=digest(raw) or found[0]['byte_count']!=len(raw)):
        raise Hold('Original native artifact identity or hash differs.','reconciling')
    return dict(artifact_id=ident,sha256=digest(raw),byte_count=len(raw))


def reconcile_http(store, run, directory, receipt, response, receipt_raw):
    request=original(Path(directory)/'request.json',4000000)
    proof=http_failure(run,receipt,request,response)
    if proof is None:return None
    proof['references']=dict(request=reference(store,run['request_artifact_id'],request),
        response=reference(store,run['response_artifact_id'],response))
    retained=store.artifact('model-receipt-'+run['id']+'.json',receipt_raw,
        dict(kind='platform-native-original-receipt-v1',run_id=run['id'],request_sha256=run['request_sha256']))
    proof['references']['receipt']=reference(store,retained['id'],receipt_raw)
    raw=canonical(proof)
    artifact=store.artifact('model-http-reconciliation-'+run['id']+'.json',raw,
        dict(kind=VERSION,run_id=run['id'],request_sha256=run['request_sha256'],proof=proof))
    store.rpc('reconcile-http-error',dict(id=run['id'],artifact_id=artifact['id'],proof=proof))
    return proof


def effective_status(run, reconciliations=()):
    if run['status']!='unknown':return run['status']
    if not run.get('id') or not isinstance(run.get('request_sha256'),str) or len(run['request_sha256'])!=64:return 'unknown'
    for row in reconciliations:
        proof=row.get('proof') or {}
        if (row.get('run_id')==run['id'] and proof.get('request_sha256')==run.get('request_sha256')
            and proof.get('status') in ('complete','failed')
            and (proof['status']=='complete' or proof.get('version')==VERSION)):
            return proof['status']
    return 'unknown'


def defer(store, state, now, cleanup_verified=False):
    """No inference or external mutations; unchanged evidence buys no new turn."""
    if not cleanup_verified or state.get('cleanup_unknown'):return None
    operations=store.rows('operations')
    if any(o['status'] in ('pending','unknown') for o in operations):return None
    settled={p['run_id'] for p in store.rows('run_reconciliations')}
    pending=[r for r in store.rows('runs') if r['status'] in ('running','unknown') and r['id'] not in settled]
    if not pending:return None
    records=[]
    for run in sorted(pending,key=lambda r:r['id']):
        directory=store.spool/store.task['id']/'calls'/run['id'];files={}
        for name in ('request.json','response.bin','receipt.json'):
            path=directory/name
            if path.is_symlink():files[name]='unsafe-link'
            elif not path.is_file():files[name]='missing'
            elif path.stat().st_size>5000000:files[name]='oversized'
            else:files[name]=digest(path.read_bytes())
        records.append(dict(id=run['id'],status=run['status'],request_sha256=run['request_sha256'],files=files))
    fingerprint=digest(canonical(records));before=state.get('reconciliation_wait') or {}
    repeats=min(30,before.get('unchanged_checks',0)+1) if before.get('fingerprint')==fingerprint else 0
    delay=min(1800,60*2**min(repeats,5))
    return dict(version=BACKOFF_VERSION,fingerprint=fingerprint,unchanged_checks=repeats,
        retry_after_seconds=delay,not_before=(now+dt.timedelta(seconds=delay)).isoformat(),
        pending_run_ids=[r['id'] for r in pending],scope='Task-local model reconciliation; own calls remain fenced. Shared cleanup and external-operation fences are unchanged.')
