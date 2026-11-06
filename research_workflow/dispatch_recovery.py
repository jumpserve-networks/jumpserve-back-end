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
CONTROLLER_VERSION='controller-terminal-reconciliation-v1'
FAILURE_VERSION='captured-provider-failure-v1'

def captured_failure(request_raw,response,http_status,reason):
    """Account for an explicit rejected buffered response, never a lost one.

    The bridge persists bytes and raises on the first error before delivering
    this response to the local tool runner. Bytes after that complete error
    frame need not form a complete response. Provider processing/charges remain
    unknown; this is not a successful inference or a zero-call certificate.
    """
    if type(http_status) is not int or http_status not in (200,429,500,502,503,504):return None
    if not isinstance(reason,str) or not reason or len(response)>1048576:return None
    request=json.loads(request_raw)
    tools=request.get('tools')
    if (request.get('model')!='gpt-6.1-sol' or request.get('store') is not False
        or request.get('stream') is not True or request.get('previous_response_id') is not None
        or request.get('background',False) is not False or not isinstance(tools,list)
        or any(not isinstance(t,dict) or t.get('type')!='function' for t in tools)):
        return None
    if http_status!=200:
        try:error=json.loads(response).get('error')
        except (ValueError,UnicodeError,AttributeError):return None
        if not isinstance(error,dict) or error.get('code')!=reason:return None
        kind='explicit-http-error';observed=len(response)
    else:
        # Parse only complete SSE frames up to the first explicit failure, in
        # the same order as the buffered bridge. Missing/malformed frames before
        # it, a prior completion, or an incomplete event never establish this.
        position=0;kind=None;observed=None
        for boundary in re.finditer(rb'\r?\n\r?\n',response):
            end=boundary.end();block=response[position:end];position=end
            lines=[line[5:].lstrip() for line in block.splitlines() if line.startswith(b'data:')]
            if not lines:continue
            payload=b'\n'.join(lines)
            if payload==b'[DONE]':return None
            try:event=json.loads(payload)
            except (ValueError,UnicodeError):return None
            if not isinstance(event,dict):return None
            event_type=event.get('type');value=event.get('response') or {}
            if not isinstance(value,dict):return None
            if value.get('model') not in (None,'gpt-6.1-sol'):return None
            if event_type in ('response.completed','response.incomplete'):return None
            if event_type not in ('response.failed','error'):continue
            error=value.get('error') or event.get('error') or event
            if not isinstance(error,dict) or error.get('code')!=reason:return None
            if event_type=='response.failed' and value.get('status')!='failed':return None
            kind='framed-stream-error';observed=end;break
        if kind is None:return None
    return dict(version=FAILURE_VERSION,evidence_kind=kind,http_status=http_status,error_code=reason,
        request_sha256=digest(request_raw),response_sha256=digest(response),response_bytes=len(response),
        inspected_prefix_bytes=observed,model='gpt-6.1-sol',response_forwarded_to_agent=False,
        tool_calls_executed_from_response=0,provider_processing_unknown=True,measured_charges_usd=None,
        provider_replay=False,scientific_completion=False)

def controller_failure(task,dispatch,receipt,original,rows):
    if dispatch['status']!='failed':return None
    if any(dispatch.get(k)!=task[v] for k,v in (('task_id','id'),('study_id','study_id'))):return None
    expected=dict(runtime='openclaw-production-v1',model_id='gpt-6.1-sol',status='failed',provider_calls=1,
        provider_request_sent=True,response_received=True,provider_retries=0,paid_fallback=False)
    if any(type(receipt.get(k)) is not type(v) or receipt[k]!=v or (dispatch.get('provenance') or {}).get(k)!=v for k,v in expected.items()):return None
    if rows(task,'builder_calls',dispatch_id='eq.'+dispatch['id'],limit=1):return None
    refs={}
    for ref in (dispatch.get('provenance') or {}).get('artifacts',[]):
        if ref.get('name') not in ('logical.json','provider-request.json','provider-response.bin','receipt.json'):continue
        if ref['name'] in refs:raise ValueError('Ambiguous original failure artifact.')
        refs[ref['name']]=ref
    if len(refs)!=4:return None
    logical_raw=original(task,refs['logical.json'],maximum=96000)
    if digest(logical_raw)!=dispatch['request_sha256']:raise ValueError('Original failed logical hash differs.')
    logical=json.loads(logical_raw)
    if not isinstance(logical,dict) or 'builder' in logical:return None
    request=original(task,refs['provider-request.json'],maximum=96000)
    if json.loads(request)!=wire.response_request(logical):raise ValueError('Original failed provider request differs.')
    if json.loads(original(task,refs['receipt.json']))!=receipt:raise ValueError('Original failure receipt differs.')
    response=original(task,refs['provider-response.bin'],maximum=1048576)
    proof=captured_failure(request,response,receipt.get('http_status'),receipt.get('error_code'))
    if proof is None:return None
    return dict(version=FAILURE_VERSION,dispatch_id=dispatch['id'],study_id=task['study_id'],task_id=task['id'],
        request_sha256=dispatch['request_sha256'],original_status=dispatch['status'],request_kind='controller',
        provider_calls=1,provider_request_sent=True,response_received=True,provider_retries=0,
        status='failed',failure=proof,references={name:dict(artifact_id=ref.get('id') or ref['artifact_id'],sha256=ref['sha256']) for name,ref in refs.items()},
        scientific_completion=False,provider_replay=False)

def runtime_diagnostic(task,dispatch,spool,retain):
    """Persist bounded observations at the worker, with no provider/cleanup call.

    Only the fixed installed runtime filenames and exact task/dispatch spool
    names are inspected. Absence, denied access and unsafe links are distinct.
    Source originals stay private; callers receive artifact/hash references.
    """
    import datetime as dt,stat
    source_root=Path(__file__).resolve().parent
    names=('autonomy_read_progress.py','autonomy.py','autonomy_model.py','dispatch_recovery.py','siwc_protocol.py')
    sources=[]
    for name in names:
        path=source_root/name
        try:
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size>262144:raise ValueError('Installed diagnostic source is unsafe or oversized.')
            raw=path.read_bytes()
            if len(raw)!=info.st_size:raise ValueError('Installed diagnostic source changed during read.')
            # These fixed executable source files must never carry literal keys.
            if re.search(rb'(?:sb_secret_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{16,}|-----BEGIN (?:RSA |EC )?PRIVATE KEY',raw):
                raise ValueError('Installed source cannot be safely delivered.')
            sources.append(dict(filename=name,sha256=digest(raw),byte_count=len(raw),raw=raw))
        except FileNotFoundError:sources.append(dict(filename=name,status='absent'))
        except PermissionError:sources.append(dict(filename=name,status='inaccessible'))
    binding=digest(canonical([{k:v for k,v in s.items() if k!='raw'} for s in sources]))
    root=Path(spool);directory=root/str(uuid.UUID(dispatch['id']))
    calls=__import__('autonomous_repair').rows(task,'builder_calls',dispatch_id='eq.'+dispatch['id'],order='number.asc',limit=101)
    if len(calls)>100:raise ValueError('Diagnostic native call coverage exceeds its bound.')
    def observe(path,maximum):
        try:
            # Do not follow a link at any original spool component.
            for parent in [path,*path.parents]:
                if parent==root.parent:break
                if parent.is_symlink():return dict(status='unsafe-link')
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode):return dict(status='unsafe-file')
            if info.st_size>maximum:return dict(status='oversized',byte_count=info.st_size)
            raw=path.read_bytes()
            if len(raw)!=info.st_size:return dict(status='changed-during-read')
            return dict(status='captured',sha256=digest(raw),byte_count=len(raw))
        except FileNotFoundError:return dict(status='absent')
        except PermissionError:return dict(status='inaccessible')
    observations=[]
    for call in calls:
        if call['status']=='complete':continue
        number=call.get('number')
        if type(number) is not int or not 1<=number<=100:raise ValueError('Original native call number differs.')
        response=observe(directory/('call-'+str(number)+'-response.bin'),1048576)
        completion=observe(directory/('call-'+str(number)+'-completion.json'),16000)
        observations.append(dict(call_id=call['id'],number=number,original_status=call['status'],request_sha256=call['request_sha256'],
            indexed_response=call.get('response_artifact_id') is not None,response=response,completion=completion,
            provider_outcome='uncertain' if call['status']=='unknown' else call['status']))
    value=dict(version='worker-runtime-diagnostics-v1',study_id=task['study_id'],task_id=task['id'],dispatch_id=dispatch['id'],
        request_sha256=dispatch['request_sha256'],runtime_source_binding=binding,worker_pid=os.getpid(),
        observation_request_id=dispatch.get('runtime_observation_request_id'),
        calls=observations,provider_calls=0,reconciliation_performed=False,cleanup_performed=False,scientific_completion=False,
        sources=[{k:v for k,v in s.items() if k!='raw'} for s in sources])
    fingerprint=digest(canonical(value));cache=root/'runtime-diagnostics'/dispatch['id']/(fingerprint+'.json')
    if cache.exists():return
    intent=cache.with_suffix('.intent')
    if intent.exists():value=json.loads(intent.read_bytes())
    else:
        for s in sources:
            if 'raw' in s:
                ref=retain('installed-'+s['filename'],s.pop('raw'),'worker-installed-source-v1')
                s['artifact']=ref
        value['sources']=sources;value['observed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        intent.parent.mkdir(parents=True,exist_ok=True);write_once(intent,canonical(value))
    ref=retain('worker-runtime-diagnostics.json',canonical(value),'worker-runtime-diagnostics-v1')
    # A private service-only observation row authenticates this report. Owner-
    # authored artifact provenance alone never certifies installed worker bytes.
    store.rpc('research_model_reconcile',dict(p_dispatch=dispatch['id'],p_artifact=ref['artifact_id'],p_sha256=ref['sha256'],p_proof=value))
    cache.parent.mkdir(parents=True,exist_ok=True);write_once(cache,canonical(ref))

def recovery_artifact(task,dispatch,owner,name,raw,kind):
    identity=str(uuid.uuid5(uuid.UUID(dispatch['id']),'reconciliation:'+name+':'+digest(raw)))
    provenance=dict(kind=kind,version=VERSION,dispatch_id=dispatch['id'],task_id=task['id'],filename=name.removeprefix('installed-'))
    record=store.artifact(dispatch['study_id'],owner,raw,identity,'application/octet-stream',provenance)
    store.append(dispatch['study_id'],owner,'artifacts',record)
    if store.artifact_bytes(dispatch['study_id'],identity,maximum=len(raw)+1)!=raw:raise ValueError('Reconciliation artifact hash differs after retrieval.')
    return dict(artifact_id=identity,sha256=record['sha256'])

def controller_terminal(task,dispatch,receipt,original,rows):
    """Prove one rejected, completed data-only response; never reuse its decision.

    A receipt alone is insufficient. The retained logical/provider requests and
    the complete consistent terminal stream must all match. Incomplete streams,
    native tool admissions and accepted decisions are outside this adapter.
    """
    if dispatch['status']!='invalid-output':return None
    if any(dispatch.get(k)!=task[v] for k,v in (('task_id','id'),('study_id','study_id'))):
        raise ValueError('Controller reconciliation task identity differs.')
    expected=dict(runtime='openclaw-production-v1',model_id='gpt-6.1-sol',status='invalid-output',
        error_code='invalid-structured-decision',provider_calls=1,provider_request_sent=True,
        response_received=True,http_status=200,provider_retries=0,paid_fallback=False)
    if (any(type(receipt.get(k)) is not type(v) or receipt[k]!=v for k,v in expected.items()) or
        any((dispatch.get('provenance') or {}).get(k)!=v for k,v in expected.items())):return None
    if rows(task,'builder_calls',dispatch_id='eq.'+dispatch['id'],limit=1):return None
    refs={}
    for ref in (dispatch.get('provenance') or {}).get('artifacts',[]):
        name=ref.get('name')
        if name not in ('logical.json','provider-request.json','provider-response.bin','receipt.json'):continue
        if name in refs:raise ValueError('Ambiguous original controller artifact identity.')
        refs[name]=ref
    if len(refs)!=4:return None
    logical_raw=original(task,refs['logical.json'],maximum=96000)
    if digest(logical_raw)!=dispatch['request_sha256']:raise ValueError('Original controller logical hash differs.')
    logical=json.loads(logical_raw)
    if not isinstance(logical,dict) or 'builder' in logical:return None
    request_raw=original(task,refs['provider-request.json'],maximum=96000)
    request=json.loads(request_raw)
    if request!=wire.response_request(logical):raise ValueError('Original controller provider request differs from its logical request.')
    retained_receipt=json.loads(original(task,refs['receipt.json']))
    if retained_receipt!=receipt:raise ValueError('Original controller receipt differs.')
    response=original(task,refs['provider-response.bin'],maximum=1048576)
    terminal=wire.read_stream(io.BytesIO(response),io.BytesIO(),time.monotonic()+10,1048576)
    if terminal.get('model')!='gpt-6.1-sol' or terminal.get('status')!='completed':
        raise ValueError('Original controller terminal model/status differs.')
    response_id=terminal.get('id')
    if not isinstance(response_id,str) or not 1<=len(response_id)<=200:return None
    try:wire.structured_response(terminal)
    except (ValueError,KeyError,TypeError):pass
    else:return None  # Accepted decisions need their separate normal outcome.
    references={name:dict(artifact_id=ref.get('id') or ref.get('artifact_id'),sha256=ref['sha256']) for name,ref in refs.items()}
    return dict(version=CONTROLLER_VERSION,dispatch_id=dispatch['id'],study_id=task['study_id'],task_id=task['id'],
        request_sha256=dispatch['request_sha256'],original_status='invalid-output',request_kind='controller',
        provider_calls=1,provider_request_sent=True,response_received=True,http_status=200,provider_retries=0,
        terminal_status='completed',response_id=response_id,terminal_sha256=digest(canonical(terminal)),
        decision_accepted=False,error_code='invalid-structured-decision',references=references,
        scope='One original data-only provider operation has a complete rejected decision. Original outcomes, usage and limits remain unchanged; no decision reuse, inference replay, container cleanup claim or scientific validation.')

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
    if proof.get('version')==FAILURE_VERSION:
        if (proof!=row['proof'] or proof.get('status')!='failed'
            or any(proof.get(k)!=call[k] for k in ('dispatch_id','task_id','study_id','request_sha256'))
            or proof.get('call_id')!=call['id'] or row['call_id']!=call['id']
            or row['request_sha256']!=call['request_sha256'] or proof.get('original_status')!=call['status']
            or proof.get('response_received') is not True or proof.get('container_cleanup')!='stopped'):
            raise ValueError('Captured native failure identity differs.')
        request=original(task,dict(artifact_id=call['request_artifact_id'],sha256=call['request_sha256']),maximum=256000)
        response=original(task,proof['response'],maximum=1048576)
        expected=captured_failure(request,response,proof.get('http_status'),proof.get('error_code'))
        if expected is None or proof.get('failure')!=expected:raise ValueError('Captured native failure differs from original bytes.')
        captured=json.loads(original(task,proof['completion'],maximum=16000))
        if captured!=proof.get('completion_record'):raise ValueError('Original failure capture differs.')
        return dict(call,status='failed',response_received=True,http_status=proof['http_status'],reason=proof['error_code'],
            response_artifact_id=proof['response']['artifact_id'],response_sha256=proof['response']['sha256'],failure_accounted=True)
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
        if call.get('failure_accounted') is True:
            proofs.append(dict(id=call['id'],request_sha256=call['request_sha256'],response_sha256=call['response_sha256'],status='failed'))
            continue
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
        if call['status'] not in ('unknown','failed'):continue
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
            if not (call.get('response_received') is True and call.get('http_status') in (200,429,500,502,503,504) and call.get('response_artifact_id') and call.get('response_sha256')):continue
            raw=repair.original(task,dict(artifact_id=call['response_artifact_id'],sha256=call['response_sha256']),maximum=1048576)
            captured_raw=canonical(dict(version='retained-native-call-record-v1',record=call,scope='Canonical projection of the unchanged original database call, not original HTTP receipt bytes.'))
            receipt=dict(version=CALL_VERSION,call_id=call['id'],dispatch_id=call['dispatch_id'],task_id=call['task_id'],study_id=call['study_id'],request_sha256=call['request_sha256'],
                response_sha256=digest(raw),status=call['status'] if call['status']=='failed' else 'complete',response_received=True,http_status=call['http_status'],capture_origin='retained-database-transport-metadata-and-terminal-stream')
        if receipt.get('status')=='failed':
            if (receipt.get('version')!=CALL_VERSION or receipt.get('call_id')!=call['id']
                or any(receipt.get(k)!=call[k] for k in ('dispatch_id','task_id','study_id','request_sha256'))
                or receipt.get('response_received') is not True or receipt.get('http_status')!=call.get('http_status')
                or call['status']!='failed' or call['response_received'] is not True
                or digest(raw)!=receipt.get('response_sha256') or digest(raw)!=call.get('response_sha256')):
                raise ValueError('Original native failure capture identity differs.')
            request=repair.original(task,dict(artifact_id=call['request_artifact_id'],sha256=call['request_sha256']),maximum=256000)
            failure=captured_failure(request,raw,call['http_status'],call.get('reason'))
            if failure is None:continue
            response=retain('native-'+call['id']+'-response.bin',raw,'builder-reconciled-native-response')
            captured=retain('native-'+call['id']+'-completion.json',captured_raw,'builder-reconciled-native-completion')
            proof=dict(version=FAILURE_VERSION,call_id=call['id'],dispatch_id=call['dispatch_id'],task_id=call['task_id'],study_id=call['study_id'],
                request_sha256=call['request_sha256'],response_sha256=digest(raw),original_status=call['status'],status='failed',
                http_status=call['http_status'],response_received=True,error_code=call['reason'],failure=failure,
                response=response,completion=captured,completion_record=json.loads(captured_raw),container_cleanup='stopped',
                provider_replay=False,scientific_completion=False)
            evidence=retain('native-'+call['id']+'-proof.json',canonical(proof),'builder-native-call-reconciliation')
            store.rpc('research_builder_call_reconcile',dict(p_call=call['id'],p_artifact=evidence['artifact_id'],p_sha256=evidence['sha256'],p_proof=proof))
            continue
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
        if proof.get('version')==CONTROLLER_VERSION:
            expected=controller_terminal(task,dispatch,receipt,original,rows)
            if expected is None or expected!=proof or row.get('study_id')!=task['study_id'] or row.get('dispatch_id')!=dispatch['id']:
                raise ValueError('Controller terminal reconciliation differs from its originals.')
            return dict(id=dispatch['id'],kind='hash-verified-terminal-controller-failure',request_sha256=dispatch['request_sha256'],
                provider_calls=1,native_calls=0,reconciliation_artifact_id=row['artifact_id'],error_code=proof['error_code'],
                scope='Original completed invalid decision accounted for; no replay, outcome rewrite or scientific validation.')
        if proof.get('version')==FAILURE_VERSION:
            expected=controller_failure(task,dispatch,receipt,original,rows)
            if expected is None or expected!=proof or row.get('study_id')!=task['study_id'] or row.get('dispatch_id')!=dispatch['id']:
                raise ValueError('Captured controller failure differs from its originals.')
            return dict(id=dispatch['id'],kind='hash-verified-captured-controller-failure',request_sha256=dispatch['request_sha256'],
                provider_calls=1,native_calls=0,reconciliation_artifact_id=row['artifact_id'],error_code=proof['failure']['error_code'],
                provider_processing_unknown=True,scientific_completion=False)
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
        if d.get('runtime_observation_only') is True:
            task=store.rows('autonomy_tasks',dict(id='eq.'+d['task_id'],study_id='eq.'+d['study_id'],limit=1))[0]
            if task['status']=='running':continue
            owner=store.rows('study_owners',dict(study_id='eq.'+d['study_id'],limit=1))[0]['owner_id']
            runtime_diagnostic(task,d,spool,lambda name,raw,kind:recovery_artifact(task,d,owner,name,raw,kind))
            continue
        if d['status']=='queued':
            result=store.rpc('research_model_reconcile_queued',dict(p_dispatch=d['id'],p_request_sha256=d['request_sha256']))
            if result.get('reconciled') is True:recovered.append(d['id'])
            continue
        if not json.loads(d['request_raw']).get('builder'):
            if d['status'] not in ('invalid-output','failed'):continue
            if store.rows('model_reconciliations',dict(dispatch_id='eq.'+d['id'],limit=1)):continue
            task=store.rows('autonomy_tasks',dict(id='eq.'+d['task_id'],study_id='eq.'+d['study_id'],limit=1))[0]
            if task['status']=='running':continue
            owner=store.rows('study_owners',dict(study_id='eq.'+d['study_id'],limit=1))[0]['owner_id']
            if d['status']=='failed':runtime_diagnostic(task,d,spool,lambda name,raw,kind:recovery_artifact(task,d,owner,name,raw,kind))
            refs=[r for r in (d.get('provenance') or {}).get('artifacts',[]) if r.get('name')=='receipt.json']
            if len(refs)!=1:continue
            receipt=json.loads(repair.original(task,refs[0]))
            proof=(controller_failure if d['status']=='failed' else controller_terminal)(task,d,receipt,repair.original,repair.rows)
            if proof is None:continue
            raw=canonical(proof);identity=str(uuid.uuid5(uuid.UUID(d['id']),proof['version']+':'+digest(raw)))
            record=store.artifact(d['study_id'],owner,raw,identity,'application/json',dict(kind='controller-dispatch-reconciliation',version=proof['version'],dispatch_id=d['id']))
            store.append(d['study_id'],owner,'artifacts',record)
            if store.artifact_bytes(d['study_id'],identity,maximum=len(raw)+1)!=raw:raise ValueError('Controller reconciliation readback differs.')
            store.rpc('research_controller_dispatch_reconcile',dict(p_dispatch=d['id'],p_artifact=identity,p_sha256=record['sha256'],p_proof=proof))
            recovered.append(d['id'])
            continue
        if (d.get('provenance') or {}).get('queued_admission_reconciliation'):continue
        if store.rows('model_reconciliations',dict(dispatch_id='eq.'+d['id'],limit=1)):continue
        directory=Path(spool)/d['id'];task_root=Path(spool)/'builder-workspaces'/d['task_id']
        task=store.rows('autonomy_tasks',dict(id='eq.'+d['task_id'],study_id='eq.'+d['study_id'],limit=1))[0]
        if task['status']=='running':continue
        owner=store.rows('study_owners',dict(study_id='eq.'+d['study_id'],limit=1))[0]['owner_id']
        calls=repair.rows(task,'builder_calls',dispatch_id='eq.'+d['id'],order='number.asc',limit=101)
        if any(c['status']!='complete' for c in calls) or not directory.is_dir() or not task_root.is_dir():
            runtime_diagnostic(task,d,spool,lambda name,raw,kind:recovery_artifact(task,d,owner,name,raw,kind))
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
                return recovery_artifact(task,d,owner,name,raw,kind)
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
