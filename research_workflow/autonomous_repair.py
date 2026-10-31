"""Durable operational recovery, using the original claim lease and authorization.

No global application patches, outcome rewrites, provider replays or fabricated
scientific results. Native work still uses ordinary published builder prompts,
artifact persistence, package validation and fresh scientific reviews.
"""
import copy
import json
import os
import uuid
import store
from workflow import canonical, digest

VERSION = 'autonomous-repair-v1'
ACTIVE = {'diagnosing', 'repairing', 'validating', 'resuming', 'waiting'}
REPAIR_INSTRUCTION = '''Operational repair of this exact recorded incident. Preserve the claim, scientific criteria, source bytes, commitments and deadline. Diagnose using repair_packet and the actual checkpoint/tool history. The previous lack of an implementation is work for you, not a request for the user to write code. Check actual installed tools; install missing dependencies within the existing action policy. Make a concrete implementation change and run its development check, recording original failure and corrected command outputs. Do not repeat already delivered context reads or setup without a reason. Keep the implementation and check in project/. Include project/repair_test.py with unittest methods test_original_failure and test_corrected_behavior that exercise actual experiment.py functions. Include repair-test-plan.json exactly {version:"repair-development-validation-v1",incident_sha256:<repair_packet.incident_sha256>,scope:"software-development-only"}. The server runs these software-only tests in a read-only, network-free container for at most 25 seconds, preserving actual stdout/stderr, exit status and unchanged project hashes. A working note or self-reported success is not validation. Publish candidate.json only with actual bounded checks and the full prospective protocol; the server freezes original code and requires fresh reviews. Never run the main campaign here, replace failed scientific observations, invent unavailable data or identifiers, weaken the hypothesis, raise resource limits, or retry an uncertain provider/experiment invocation.'''


def enabled():
    return os.environ.get('RESEARCH_AUTONOMY_REPAIR_ENABLED') == 'true'


def rows(task, kind, **query):
    return store.rows(kind, dict(study_id='eq.' + task['study_id'], **query))


def rpc(task, operation, payload):
    return store.rpc('research_autonomy_repair', dict(p_operation=operation,
        p_study=task['study_id'], p_task=task['id'], p_token=task.get('lease_token'), p_payload=payload))


def category(task, reason, context):
    if not task.get('claim_id') or task.get('_repair_external'):
        return None
    if context.get('builder_stall') or reason.startswith('Builder remained unchanged after targeted replanning.'):
        return None  # A known failed recovery is not a new repair opportunity.
    blockers = context.get('blockers', [])
    if any(b.get('kind') in ('equipment', 'authorization', 'capacity') for b in blockers):
        return None
    if any(word in reason.lower() for word in ('quota', 'auth-required', 'paused-auth', 'cancelled')):
        return None
    if 'identity mismatch' in reason.lower() or 'hash differs' in reason.lower():
        return None  # An integrity violation is not permission to replace evidence.
    if 'shared_campaigns' in reason or 'acquisition' in reason.lower():
        return 'reference'
    if task['stage'] == 'building' or any(b.get('kind') in ('implementation', 'design', 'missing-data') for b in blockers):
        return 'implementation'
    return 'runtime'


def enqueue(task, context, reason, adopt=False):
    kind = category(task, reason, context)
    if not enabled() or kind is None:
        return None
    incident = dict(version=VERSION, stage=task['stage'], reason=reason,
        failed_turn_id=context.get('repair_failure_turn_id') or context.get('last_turn_id'),
        checkpoint=context.get('builder_checkpoint'), blockers=context.get('blockers', []))
    raw = canonical(incident).decode(); sha = digest(raw.encode())
    value = rpc(task, 'adopt' if adopt else 'enqueue', dict(version=VERSION,
        id=str(uuid.uuid5(uuid.UUID(task['id']), VERSION + ':' + sha)), category=kind,
        original_stage=task['stage'], original_context=task['context'], context=context,
        incident_raw=raw, incident_sha256=sha, reason=reason))
    return value.get('task')


def discover():
    if not enabled():
        return []
    result = []
    for task in store.rpc('research_autonomy_repair_candidates', {}):
        outcome = enqueue(task, task['context'], task['reason'], adopt=True)
        result.append(dict(task_id=task['id'], status='diagnosing' if outcome else 'retained-blocker'))
    return result


def progress(record):
    return {k: record[k] for k in ('id', 'version', 'state', 'category', 'revision', 'updated_at')}


def transition(task, record, state, reason, evidence):
    value = rpc(task, 'transition', dict(id=record['id'], expected_revision=record['revision'],
        state=state, reason=reason, evidence=evidence))
    if value.get('denied'):
        raise ValueError('Repair admission stopped: ' + value['denied'])
    return value


def original(task, pointer, maximum=150000):
    identity = pointer.get('artifact_id') or pointer.get('id')
    found = rows(task, 'artifacts', id='eq.' + identity, limit=2)
    if len(found) != 1 or found[0]['sha256'] != pointer['sha256']:
        raise ValueError('Repair original artifact identity mismatch.')
    raw = store.artifact_bytes(task['study_id'], identity, maximum=maximum)
    if len(raw) != found[0]['byte_count'] or digest(raw) != pointer['sha256']:
        raise ValueError('Repair original artifact hash differs.')
    return raw


def diagnose(task, record):
    """Read stored identities; never invoke a provider or submit an experiment."""
    pending = rows(task, 'builder_calls', task_id='eq.' + task['id'], status='in.(running,unknown)', limit=1)
    live = rows(task, 'autonomy_turns', task_id='eq.' + task['id'], status='eq.running', limit=1)
    if pending or live:
        return dict(waiting=True, reason='An original native call or controller turn is unresolved; no replacement call admitted.')
    unknown_turns = rows(task, 'autonomy_turns', task_id='eq.' + task['id'], status='eq.unknown', limit=101)
    if len(unknown_turns) > 100:
        raise ValueError('Unknown controller turn coverage exceeds the bounded diagnosis.')
    dispatches = rows(task, 'model_dispatches', task_id='eq.' + task['id'], order='created_at.desc', limit=21)
    if len(dispatches) == 21:
        # Include every uncertain dispatch even when the recent window is full.
        uncertain = rows(task, 'model_dispatches', task_id='eq.' + task['id'], status='in.(unknown,queued,running)', limit=101)
        if len(uncertain) > 100:
            raise ValueError('Uncertain dispatch coverage exceeds the bounded diagnosis.')
        dispatches += [d for d in uncertain if d['id'] not in {x['id'] for x in dispatches}]
    missing = [t['id'] for t in unknown_turns if t['id'] not in {d['id'] for d in dispatches}]
    if missing:
        dispatches += rows(task, 'model_dispatches', task_id='eq.' + task['id'],
            id='in.(' + ','.join(missing) + ')', limit=101)
    by_id = {d['id']: d for d in dispatches}
    for turn in unknown_turns:
        if turn['study_id'] != task['study_id'] or turn['task_id'] != task['id']:
            raise ValueError('Repair controller turn identity mismatch.')
        dispatch = by_id.get(turn['id'])
        if dispatch is None:
            return dict(waiting=True, reason='Provider outcome remains unconfirmed for controller turn ' + turn['id'] + '; original dispatch evidence is unavailable. No replay or replacement call admitted.')
        if dispatch['request_sha256'] != turn['request_sha256']:
            raise ValueError('Repair controller turn/dispatch request identity mismatch.')
    events = rows(task, 'autonomy_events', task_id='eq.' + task['id'], event='eq.assessment-recovery-resumed', limit=20)
    reconciled = []; checkpoint = None; checkpoint_time=None;failure_details=[]
    import datetime as dt
    def when(value):return dt.datetime.fromisoformat(value.replace('Z','+00:00')) if value else dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    for dispatch in dispatches:
        if dispatch['task_id'] != task['id'] or dispatch['study_id'] != task['study_id']:
            raise ValueError('Repair dispatch identity mismatch.')
        if dispatch['status'] in ('queued', 'running'):
            return dict(waiting=True, reason='Original dispatch is still admitted; read-only reconciliation will continue.')
        provenance = dispatch.get('provenance') or {}
        if dispatch['status']!='complete' and len(failure_details)<3:
            snippets=[]
            for pointer in provenance.get('artifacts',[]):
                if pointer.get('name') not in ('stderr.bin','stdout.bin','receipt.json'):continue
                if pointer.get('byte_count',64001)>64000:
                    snippets.append(dict(name=pointer['name'],sha256=pointer['sha256'],representation='unreviewed-retained-original-reference'));continue
                raw=original(task,pointer,maximum=64000)
                import scheduler
                safe,projection=scheduler.shared_document(dict(text=raw.decode(errors='replace')[:4000]))
                snippets.append(dict(name=pointer['name'],sha256=pointer['sha256'],excerpt=safe['text'],original_bytes=len(raw),excerpt_complete=len(raw.decode(errors='replace'))<=4000,redaction=projection))
            failure_details.append(dict(dispatch_id=dispatch['id'],status=dispatch['status'],request_sha256=dispatch['request_sha256'],original_log_excerpts=snippets))
        if dispatch['status'] == 'complete':
            raw = original(task, dict(id=dispatch['response_artifact_id'], sha256=dispatch['response_sha256']))
            json.loads(raw)
            reconciled.append(dict(id=dispatch['id'], request_sha256=dispatch['request_sha256'], response_sha256=digest(raw), kind='completed-original-response'))
            if provenance.get('checkpoint') and (checkpoint_time is None or when(dispatch.get('ended_at'))>=checkpoint_time):
                checkpoint = provenance['checkpoint']
                checkpoint_time=when(dispatch.get('ended_at'))
            continue
        # Historical operator reconciliations are immutable service-only audit
        # records, not statements inside a paper, model answer or working note.
        audit = next((e for e in events if any(p.get('id') == dispatch['id'] and
            p.get('request_sha256') == dispatch['request_sha256'] and p.get('status') == dispatch['status'] and
            p.get('kind') in ('confirmed-pre-provider-byte-precheck', 'confirmed-pre-provider-admission-failure')
            for p in e['details'].get('reconciled_dispatches', []))), None)
        if audit:
            reconciled.append(dict(id=dispatch['id'], kind='retained-operator-reconciliation', event_id=audit['id'], request_sha256=dispatch['request_sha256']))
            continue
        refs = {a['name']: a for a in provenance.get('artifacts', [])}
        receipt = json.loads(original(task, refs['receipt.json'])) if 'receipt.json' in refs else {}
        import dispatch_recovery
        verified=dispatch_recovery.confirmed(task,dispatch,receipt,original,rows)
        if verified:
            reconciled.append(verified)
            if verified.get('checkpoint') and (checkpoint_time is None or when(verified.get('checkpoint_recorded_at'))>=checkpoint_time):
                checkpoint=verified['checkpoint'];checkpoint_time=when(verified.get('checkpoint_recorded_at'))
            continue
        zero = type(receipt.get('provider_calls')) is int and receipt['provider_calls'] == 0 and receipt.get('provider_request_sent') is False
        if zero and 'logical.json' in refs:
            logical = original(task, refs['logical.json'])
            if digest(logical) != dispatch['request_sha256']:
                raise ValueError('Original logical request hash differs from its dispatch.')
            reconciled.append(dict(id=dispatch['id'], kind='hash-verified-zero-provider-transport', receipt_sha256=refs['receipt.json']['sha256'], request_sha256=dispatch['request_sha256']))
            continue
        return dict(waiting=True, reason='Provider outcome remains unconfirmed for dispatch ' + dispatch['id'] + '; no replay or replacement call admitted.')
    checkpoint = checkpoint or task['context'].get('builder_checkpoint')
    if checkpoint:
        original(task, checkpoint, maximum=4000000)
    jobs = rows(task, 'queue_jobs', id='in.(' + ','.join(task['context'].get('job_ids', [])) + ')', limit=1000) if task['context'].get('job_ids') else []
    if len(jobs) != len(set(task['context'].get('job_ids', []))):
        raise ValueError('Original experiment identity coverage is incomplete.')
    last_turns=rows(task,'autonomy_turns',task_id='eq.'+task['id'],order='number.desc',limit=3)
    import scheduler
    summaries,_=scheduler.shared_document([{k:t.get(k) for k in ('id','number','stage','status','reason','request_sha256','response_sha256')} for t in last_turns])
    return dict(version=VERSION, waiting=False, reconciled_dispatches=reconciled,failure_details=failure_details,last_turns=summaries,
        checkpoint=checkpoint, original_jobs=[dict(id=j['id'], status=j['status']) for j in jobs],
        native_repair=record['category'] == 'implementation', validated=False,
        scope='Operational identity reconciliation only; no scientific readiness, verdict or provider replay.')


def save_diagnosis(task, document):
    raw = canonical(document); identity = str(uuid.uuid5(uuid.UUID(task['id']), 'repair-diagnosis:' + digest(raw)))
    artifact = store.artifact(task['study_id'], task['actor_id'], raw, identity, 'application/json',
        dict(kind='autonomous-repair-diagnosis', version=VERSION, scientific_completion=False))
    store.append(task['study_id'], task['actor_id'], 'artifacts', artifact)
    original(task, artifact)
    return dict(artifact_id=identity, sha256=artifact['sha256'], byte_count=len(raw))


def drive(task, normal_step, advance):
    context = copy.deepcopy(task['context']); ref = context['repair_progress']
    found = rows(task, 'autonomy_repairs', id='eq.' + ref['id'], task_id='eq.' + task['id'], limit=2)
    if len(found) != 1 or found[0]['version'] != VERSION:
        raise ValueError('Retained repair incident identity is unavailable.')
    record = found[0]
    if record['state'] == 'repairing':
        return normal_step(task)
    if record['state'] == 'waiting':
        record = transition(task, record, 'diagnosing', 'Rechecking original identities without a model call.', record['evidence'])
    if record['state'] == 'diagnosing':
        document = diagnose(task, record); artifact = save_diagnosis(task, document)
        evidence = dict(document, diagnosis=artifact)
        if document.get('waiting'):
            record = transition(task, record, 'waiting', document['reason'], evidence)
            context['repair_progress'] = progress(record)
            return advance(task, 'waiting', context=context, reason=document['reason'], delay=min(300,30*2**min(record['revision'],4)))
        if document.get('checkpoint'):
            context['builder_checkpoint'] = document['checkpoint']
        # Retain historical feedback in the incident; replace misleading stale
        # references with current catalogs and authoritative executable contracts.
        context.pop('evidence_read', None)
        context['last_feedback'] = 'Operational identities reconciled. Use the CURRENT shared_acquisitions catalog and exact runner_contracts. An empty catalog is not an existing acquisition: design the first applicable acquisition rather than request an invented ID. Preserve this claim and its original decision criterion. Inspect runtime contracts by their supplied references.'
        if document['native_repair']:
            record = transition(task, record, 'repairing', 'Native implementation repair with the retained checkpoint and fresh incident-specific instructions.', evidence)
            context.update(repair_directive=REPAIR_INSTRUCTION, repair_packet=dict(incident=json.loads(record['incident_raw']), incident_sha256=record['incident_sha256'], diagnosis=artifact, reconciled_dispatches=document['reconciled_dispatches'], command='Inspect original native history and project files; implement and run a concrete development check next.'))
            context['builder_feedback'] = ('Repair this incident: '+str(json.loads(record['incident_raw']).get('reason','Recorded implementation failure'))+
                '. Continue from existing files and verified reads. Write or amend project/experiment.py, execute a bounded development check, then supply the exact incident-bound repair tests/plan required by repair_directive. A repeated read-only slice is not progress.')
            context['repair_progress'] = progress(record)
            return advance(task, 'queued', stage='building', context=context, reason=record['reason'])
        # Context/reference correction must pass the actual full request preflight
        # before another numbered model invocation may be admitted.
        record = transition(task, record, 'validating', 'Validate the corrected source/runner context before resuming.', evidence)
    if record['state'] == 'validating':
        import autonomy
        import autonomy_model as model
        context.pop('evidence_read',None)
        context['last_feedback']='Use the current source and runtime contracts, with the original claim and criterion. Only shared acquisitions actually listed in the current catalog exist for reuse; an empty catalog requires planning the first acquisition. Repair reconciliation is operational, not scientific evidence.'
        restored = dict(task, stage='execution' if record['evidence'].get('original_jobs') else 'planning', context=context)
        if restored['stage'] == 'planning':
            _, system = model.load_prompt()
            value = model.request(system, autonomy.build_context(restored))
            validation = dict(record['evidence'], validated=True, request_sha256=digest(canonical(value)), request_bytes=len(canonical(value)), resume_stage='planning', validator='exact-model-request-preflight-v1')
        else:
            validation = dict(record['evidence'], validated=True, resume_stage='execution', validator='retained-experiment-identities-v1')
        record = transition(task, record, 'resuming', 'Operational correction passed preflight; original scientific reviews and limits remain required.', validation)
    if record['state'] == 'resuming':
        record = transition(task, record, 'resolved', 'Repair completed; resume original claim without rewriting its finding.', record['evidence'])
        context['repair_progress'] = progress(record)
        context.pop('repair_directive', None); context.pop('repair_packet', None)
        return advance(task, 'queued', stage=record['evidence']['resume_stage'], context=context, reason=record['reason'])
    raise ValueError('Unsupported active repair state.')


def route(task, status, context, stage, reason):
    """Intercept terminal operational stops; keep normal stage/claim admission."""
    if not enabled():
        return None
    ref = context.get('repair_progress')
    if ref and ref['state'] in ACTIVE:
        found = rows(task, 'autonomy_repairs', id='eq.' + ref['id'], task_id='eq.' + task['id'], limit=2)
        if len(found) != 1:
            raise ValueError('Repair transition identity is unavailable.')
        record = found[0]
        if status in ('budget','deadline','cancelled'):
            record=transition(task,record,status,reason,record['evidence'])
            context['repair_progress']=progress(record)
            return dict(status=status,context=context,reason=reason)
        if status == 'blocked':
            if category(task, reason, context) is None:
                record = transition(task, record, 'blocked', reason, record['evidence'])
            else:
                record = transition(task, record, 'diagnosing', reason, dict(record['evidence'], latest_failure=reason))
                status = 'queued'
            context['repair_progress'] = progress(record)
            return dict(status=status, context=context, reason=reason)
        if record['state'] == 'repairing' and stage == 'comprehension' and context.get('proposal'):
            # The normal path already froze exact package bytes and performed
            # semantic protocol validation. Fresh scientific reviews follow.
            validation=context.get('repair_validation')
            if not validation:raise ValueError('Original server repair validation receipt required before resumption.')
            report=json.loads(original(task,validation))
            configs=context['proposal']['protocol']['configurations']
            hashes={c['details']['test_config']['private_inputs']['project.zip']['sha256'] for c in configs}
            if report.get('passed') is not True or report.get('incident_sha256')!=record['incident_sha256'] or hashes!={report.get('project_sha256')}:
                raise ValueError('Repair validation does not match this exact incident and frozen project.')
            evidence = dict(record['evidence'], validated=True, candidate_sha256=digest(canonical(context['proposal'])),development_validation=validation, validator='executed-software-checks-frozen-package-and-semantic-protocol-v1', resume_stage='comprehension')
            for state in ('validating', 'resuming', 'resolved'):
                record = transition(task, record, state, 'Versioned implementation candidate validated; fresh comprehension and critic reviews remain pending.', evidence)
            context['repair_progress'] = progress(record)
            context.pop('repair_directive', None); context.pop('repair_packet', None)
            return dict(status=status, context=context, reason=reason)
    if status == 'blocked':
        result = enqueue(task, context, reason)
        if result:
            return dict(result=result)
    return None
