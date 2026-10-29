"""Dependency-aware scheduling. Parallel execution does not imply independence."""
import uuid
import os
import re
import store
import network_execution
import paper_titles
from workflow import canonical, digest, identifier, text

VERSION = "research-queue-v1"
PUBLIC_VERSION = "research-public-queue-v1"
LIMITS = dict(global_jobs=4, study_jobs=2, invocation_jobs=2, lease_seconds=600,
              input_bytes=256000, observations=1000, algorithm_wall_seconds=10,
              study_queue_jobs=1000, automatic_retries=0)
TERMINAL = ("awaiting-review", "reviewed", "failed", "cancelled", "expired")

def public_reason(value, fallback=None, requirements=None, allow_prompts=False, maximum=4000):
    """Bounded plain-text diagnostics; original private records stay unchanged."""
    def scrub(raw):
        if not isinstance(raw, str) or not raw.strip(): return None
        if len(raw) > 100_000: return '[Recorded detail exceeds the public size limit.]'
        hidden = 'cookie|set-cookie|prompt|initial_prompt|raw_response|messages'
        if re.search(r'-----BEGIN [A-Z ]*PRIVATE KEY-----', raw, re.I) or not allow_prompts and re.search(r'''\b(?:''' + hidden + r''')\b["']?\s*[:=]''', raw, re.I):
            return '[Private prompt, header or key content omitted from diagnostic.]'
        patterns = (
            (r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.S),
            (r'\b(?:Cookie|Set-Cookie)\s*:[^\r\n]*', re.I),
            (r'\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', re.I),
            (r'''\b(?:authorization|cookie|set-cookie|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?token|client[_-]?secret|credential)\b["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', re.I),
            (r'\b(?:sk|rk)-[A-Za-z0-9_-]{8,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', 0),
            (r'''\b(?:https?|s3|file|postgres(?:ql)?):[^\s<>"']+''', re.I),
            (r"(?<![\w.!#$%&'*+/=?^_`{|}~-])[\w.!#$%&'*+/=?^_`{|}~-]+@[\w.-]+", 0),
            (r'''\barn:[a-z0-9-]+:[^\s<>"']+|\b(?:i|eni|sg|subnet|vpc|vol)-[a-f0-9]{8,}\b|(?<![\w-])\d{12}(?![\w-])''', re.I),
            (r'''(?<![\w])(?:/(?:Users|home|var|private|tmp|mnt|opt|srv|root|app|workspace)(?:/[^\s<>"']*)?|[A-Za-z]:\\[^\s<>"']+|(?:research-paper-uploads|research-artifacts|raw-inputs)/[^\s<>"']+)''', re.I),
            (r'\b(?:\d{1,3}\.){3}\d{1,3}\b|(?<![\w:])(?:[a-f0-9]{0,4}:){2,7}[a-f0-9]{0,4}(?![\w:])', re.I),
            (r'[A-Za-z0-9_+/=-]{40,}', 0),
        )
        clean = raw
        for pattern, flags in patterns: clean = re.sub(pattern, '[redacted]', clean, flags=flags)
        clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', clean).strip()
        return clean or None

    recorded = scrub(value)
    result = recorded or fallback
    if isinstance(requirements, list) and requirements:
        inputs = [scrub(item) or '[Invalid recorded requirement omitted.]' for item in requirements[:32]]
        if len(requirements) > 32: inputs.append('[Additional recorded requirements omitted from this view.]')
        if recorded is None: result = 'This task is waiting for recorded requirements.'
        result = (result or 'Recorded requirements:') + '\n\nNeeded to resume:\n' + '\n'.join('- ' + item for item in inputs)
    elif requirements is not None and not isinstance(requirements, list):
        result = (recorded or 'This task is paused.') + '\n[Recorded requirements have an invalid format.]'
    if result is not None and len(result) > maximum: result = result[:maximum-100] + '\n[Public detail truncated; the original record is retained.]'
    return result

def shared_document(document):
    """Share research content, retaining types and labeling protected values."""
    from urllib.parse import urlsplit
    import ipaddress
    counts = dict(redacted=0, truncated=0)
    nodes = 0
    protected = re.compile(r'^(?:authorization|cookies?|set.cookie|password|passwd|secret|api.key|access.token|refresh.token|session.token|session.id|client.secret|credentials?|lease.token|owner.id|actor.id|requester(?:.id|.email)?|user(?:.id|.metadata)?|profile|identities|sub|avatar.url|picture|account(?:.id)?|email|storage.path|bucket|filename|signed.url|upload.token)$', re.I)
    credential_keys = {'apikey','xapikey','accesstoken','refreshtoken','sessiontoken','clientsecret','authtoken','servicerolekey','supabaseservicerolekey','awsaccesskeyid','awssecretaccesskey','awssessiontoken','xamzsecuritytoken'}
    identity_keys = {'ownerid','actorid','requesterid','userid','profileid','sessionid','cognitoidentityid','googleuserid','authid'}
    def visit(value, key='', depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 100_000 or depth > 40: raise store.StoreError('Shared detail exceeds its structural budget; no partial document was returned.')
        compact = re.sub(r'[_\-\s]', '', key).lower()
        if protected.fullmatch(key) or compact in credential_keys or compact in identity_keys or compact.startswith(('secret','password','credential')): counts['redacted'] += 1; return '[redacted]'
        if value is None or isinstance(value, (bool, int, float)): return value
        if isinstance(value, dict): return {k: visit(v, k, depth+1) for k, v in value.items()}
        if isinstance(value, list): return [visit(item, '', depth+1) for item in value]
        if not isinstance(value, str): raise store.StoreError('Shared detail has an unsupported value type.')
        if (key == 'id' or key.endswith('_id')) and re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', value): return value
        if re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value): return value
        if key.endswith('sha256') and re.fullmatch(r'[a-f0-9]{64}', value): return value
        if key in ('paper_url', 'source_url', 'retrieved_url'):
            try: url = urlsplit(value)
            except ValueError: url = urlsplit('')
            try: ipaddress.ip_address(url.hostname or ''); literal = True
            except ValueError: literal = False
            host = url.hostname or ''
            if url.scheme in ('http', 'https') and '.' in host and not literal and not url.username and not url.password and not url.query and not host.endswith(('.internal', '.local', '.amazonaws.com', '.supabase.co')):
                return value
        cleaned = public_reason(value, allow_prompts=True, maximum=100_000) if value else value
        if cleaned != value: counts['redacted'] += 1
        if len(value) > 100_000: counts['truncated'] += 1
        return cleaned
    result = visit(document)
    if len(canonical(result)) > 4_000_000: raise store.StoreError('Shared details exceed the byte budget; no partial document was returned.')
    return result, dict(policy='research-shared-details-v1', **counts,
        note='Shared research content with protected values redacted. Hashes identify retained originals, not this transformed view. Original records are unchanged.')

def shared_paper(study_id):
    study_id = identifier(study_id)
    found = store.rows('studies', dict(id='eq.'+study_id, select='id,study_kind', limit=2))
    if not found or found[0].get('study_kind', 'paper') != 'paper': return None
    if len(found) != 1 or found[0]['id'] != study_id: raise store.StoreError('Shared assessment identity is invalid.')
    return study_id

def public_records(study_id, kind, after=None, limit='25'):
    from workflow import PUBLIC_KINDS, FIELDS
    study_id = shared_paper(study_id)
    if study_id is None: return None
    extra = {
        'autonomy_workflows': 'study_id,initial_prompt,initial_sha256,max_spend_cents,max_attempts,committed_cents,status,reason,created_at,deadline_at,updated_at',
        'autonomy_tasks': 'id,study_id,claim_id,stage,status,label,attempts,context,reason,required_inputs,next_at,created_at,updated_at',
        'autonomy_repairs': 'id,study_id,task_id,version,category,original_stage,incident_raw,incident_sha256,state,revision,evidence,reason,created_at,updated_at',
        'autonomy_repair_events': 'id,study_id,task_id,repair_id,revision,state,details,created_at',
        'queue_jobs': 'id,study_id,campaign_id,input_artifact_id,followup_of,execution_mode,priority,dependencies,exclusive_resources,request,definition_sha256,status,reason,run_id,lease_until,created_at,updated_at',
        'queue_events': 'id,study_id,job_id,event,status,reason,details,created_at',
        'artifacts': 'id,study_id,sha256,byte_count,media_type,provenance,verified_at,created_at',
    }
    if kind not in (*PUBLIC_KINDS, *extra): raise ValueError('Choose a supported assessment record kind.')
    if not str(limit).isascii() or not str(limit).isdigit() or not 1 <= int(limit) <= 100: raise ValueError('Choose 1 to 100 shared records per page.')
    maximum = int(limit)
    identity = 'study_id' if kind == 'autonomy_workflows' else 'id'
    if after is not None:
        after = identifier(after)
        if identity == 'study_id': raise ValueError('The workflow is a single record without a page cursor.')
    query = dict(study_id='eq.'+study_id, select=extra[kind] if kind in extra else ','.join(('id','study_id','created_at',*FIELDS[kind])), order=identity+'.asc')
    if after is not None: query[identity] = 'gt.'+identifier(after)
    rows = []
    while len(rows) <= maximum:
        page_query = dict(query, limit=maximum+1-len(rows))
        if rows:
            if identity == 'study_id': page_query['offset'] = len(rows)
            else: page_query[identity] = 'gt.'+rows[-1][identity]
        page = store.rows(kind, page_query)
        if not page: break
        ids = [row.get(identity) for row in page]
        if len(page) > maximum+1-len(rows) or ids != sorted(set(ids)) or any(row.get('study_id') != study_id for row in page) or any(identifier(value) != value for value in ids) or rows and ids[0] <= rows[-1][identity] or after and any(value <= after for value in ids):
            raise store.StoreError('Shared record pagination or identities are invalid.')
        rows.extend(page)
    values, projection = shared_document(rows[:maximum])
    return dict(version='research-shared-records-v1', access='public-read-only', study_id=study_id, kind=kind, records=values,
        next_after=rows[maximum-1][identity] if len(rows)>maximum else None, coverage=dict(returned=len(values)), projection=projection)

def public_history(study_id, task_id, before=None):
    study_id = shared_paper(study_id)
    if study_id is None: return None
    task_id = identifier(task_id)
    if os.environ.get('RESEARCH_BUILDER_ENABLED')=='true':
        import autonomy
        history=autonomy.history(study_id,task_id,before)
        values,projection=shared_document({k:v for k,v in history.items() if k not in ('access','version')})
        return dict(values,version='research-shared-turns-v1',access='public-read-only',projection=projection)
    found = store.rows('autonomy_tasks', dict(study_id='eq.'+study_id, id='eq.'+task_id, select='id,study_id,attempts,stage,context', limit=2))
    if not found: return None
    if len(found) != 1 or found[0]['id'] != task_id or found[0]['study_id'] != study_id: raise store.StoreError('Shared turn task identity is invalid.')
    if found[0].get('stage')=='building' or found[0].get('context',{}).get('builder_checkpoint'):
        import autonomy
        history=autonomy.history(study_id,task_id,before)
        values,projection=shared_document({k:v for k,v in history.items() if k not in ('access','version')})
        return dict(values,version='research-shared-turns-v1',access='public-read-only',projection=projection)
    attempts = found[0]['attempts']
    if type(attempts) is not int or attempts < 0: raise store.StoreError('Shared attempt coverage is invalid.')
    query = dict(study_id='eq.'+study_id, task_id='eq.'+task_id, order='number.desc', select='id,study_id,task_id,number,stage,prompt_version_id,model_id,request,request_sha256,status,reserved_cents,estimated_cents,raw_response,response_sha256,output,usage,reason,created_at,ended_at')
    query['number'] = 'lt.'+str(attempts+1)
    if before is not None:
        if not str(before).isascii() or not str(before).isdigit() or not 1 <= int(before) <= attempts+1: raise ValueError('Turn cursor must be a retained attempt number.')
        query['number'] = 'lt.'+str(int(before))
    rows = []
    while len(rows) < 11:
        page = store.rows('autonomy_turns', dict(query, offset=len(rows), limit=11-len(rows)))
        if not page: break
        numbers = [row.get('number') for row in page]
        if len(page) > 11-len(rows) or any(row.get('study_id') != study_id or row.get('task_id') != task_id for row in page) or any(type(n) is not int or not 1 <= n <= attempts or before is not None and n >= int(before) for n in numbers) or numbers != sorted(set(numbers), reverse=True) or rows and numbers[0] >= rows[-1]['number']:
            raise store.StoreError('Shared turn coverage or identities are invalid.')
        rows.extend(page)
    values, projection = shared_document(rows[:10])
    return dict(version='research-shared-turns-v1', access='public-read-only', study_id=study_id, task_id=task_id, turns=values,
        next_before=rows[9]['number'] if len(rows)>10 else None, coverage=dict(returned=len(values), total=attempts), projection=projection)

def invoke_worker(target):
    import boto3
    from botocore.config import Config
    return boto3.client('lambda', region_name='us-east-1', config=Config(connect_timeout=1, read_timeout=2,
        retries={'total_max_attempts': 1})).invoke(FunctionName=target, InvocationType='Event',
            Payload=canonical(dict(invocation='durable queue enqueue; claim through existing capacity gates')))

def wake_worker(job):
    """A notification is not a lease or a claim that execution has started."""
    if job.get('execution_mode') != 'automatic' or job.get('status') != 'queued':
        return dict(status='not-needed', reason='Manual or already-started/terminal work is not launched by this notification.')
    target = os.environ.get('RESEARCH_QUEUE_WORKER_FUNCTION', '')
    if os.environ.get('RESEARCH_QUEUE_WORKERS_ENABLED') != 'true' or not target:
        return dict(status='deferred', reason='Immediate worker notification is unavailable; the job remains durably queued.')
    import re
    if not re.fullmatch(r'arn:aws:lambda:us-east-1:395567831870:function:[A-Za-z0-9_-]+', target):
        return dict(status='deferred', reason='Worker target is misconfigured; the job remains queued for scheduled polling.')
    try:
        response = invoke_worker(target)
        if response.get('StatusCode') == 202:
            return dict(status='requested', reason='Worker notified. Atomic capacity, priority, dependencies and resource locks determine execution.')
    except Exception:
        pass
    # Queue persistence succeeded. Notification failure must not turn it into an
    # apparent failed enqueue or cause another experiment definition to be made.
    return dict(status='deferred', reason='Worker notification is unconfirmed; scheduled polling can still pick up this queued job.')

def definition(body):
    allowed = {"request_id", "campaign_id", "execution_mode", "priority", "dependencies", "exclusive_resources", "raw_input", "rationale", "followup_of"}
    if not isinstance(body, dict) or set(body) - allowed: raise ValueError("Unsupported queue fields.")
    job_id = identifier(body.get("request_id"))
    mode = body.get("execution_mode")
    if mode not in ("automatic", "manual"): raise ValueError("Choose automatic or manual execution.")
    priority = body.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5: raise ValueError("Priority must be 1 through 5.")
    dependencies = body.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) > 32: raise ValueError("At most 32 explicitly typed dependencies are allowed.")
    seen = set(); validated = []
    for item in dependencies:
        if not isinstance(item, dict) or set(item) != {"job_id", "requirement"} or item["requirement"] not in ("complete-run", "reviewed-evidence"):
            raise ValueError("Each dependency needs job_id and complete-run or reviewed-evidence.")
        predecessor = identifier(item["job_id"])
        if predecessor == job_id or predecessor in seen: raise ValueError("Self or duplicate dependency is invalid.")
        seen.add(predecessor); validated.append(dict(job_id=predecessor, requirement=item["requirement"]))
    resources = body.get("exclusive_resources")
    import re
    if not isinstance(resources, list) or len(resources) > 16 or any(not isinstance(r, str) or not re.fullmatch(r"[a-z0-9][a-z0-9:._/-]{0,99}", r) for r in resources):
        raise ValueError("Exclusive resources need at most 16 stable lowercase identities.")
    if len(set(resources)) != len(resources): raise ValueError("Duplicate exclusive resource identity.")
    raw = body.get("raw_input")
    if mode == "automatic":
        if not isinstance(raw, str) or not raw: raise ValueError("Supply the exact original UTF-8 input text.")
        raw = raw.encode()
        if len(raw) > LIMITS["input_bytes"]: raise ValueError("Queue input exceeds 256 KB.")
    elif raw not in (None, ""):
        raise ValueError("Manual tasks retain originals through the domain importer; do not submit an automatic input.")
    else: raw = None
    request = dict(campaign_id=identifier(body.get("campaign_id")), execution_mode=mode, priority=priority,
      dependencies=validated, exclusive_resources=sorted(resources), rationale=text(body.get("rationale"), "Job rationale", 5000),
      followup_of=identifier(body["followup_of"]) if body.get("followup_of") else None,
      input_artifact_id=str(uuid.uuid5(uuid.UUID(job_id), "queue-input")) if raw is not None else None,
      input_sha256=digest(raw) if raw is not None else None)
    return job_id, request, raw

def enqueue(study_id, actor, body):
    job_id, request, raw = definition(body)
    prior = store.rows("queue_jobs", {"study_id": "eq." + study_id, "id": "eq." + job_id, "limit": 1})
    if prior:
        if prior[0]["request"] != request: raise ValueError("Queue ID already used; create a follow-up rather than changing the original.")
        return dict(job=prior[0], replayed=True, dispatch=wake_worker(prior[0]))
    if raw is not None:
        campaigns = store.rows("campaigns", {"study_id": "eq." + study_id, "id": "eq." + request["campaign_id"], "limit": 1})
        if not campaigns: raise ValueError("Campaign not found.")
        campaign = campaigns[0]
        protocols = store.rows("protocols", {"study_id": "eq." + study_id, "id": "eq." + campaign["protocol_id"], "limit": 1})
        if not protocols: raise ValueError("A frozen protocol is required.")
        p = protocols[0]["document"]; limits = p["resource_limits"]
        if campaign['adapter']=='network-execution-v1':
            if campaign['experiment_type'] not in ('new-measurement','independent-check','broader-validation'): raise ValueError('Networking execution requires an applicable experiment method.')
            network_execution.validate_input(study_id,protocols[0],raw)
            if request['exclusive_resources']!=sorted(p['execution'].get('exclusive_resources',[])): raise ValueError('Queue resource locks differ from the reviewed execution specification.')
        elif campaign["adapter"] != "matched-numeric-v1" or campaign["experiment_type"] not in ("reanalysis", "independent-check"):
            raise ValueError("This campaign needs a domain implementation or manual task.")
        if (campaign['adapter']!='network-execution-v1' and (limits["max_observations"] > 1000 or limits["wall_seconds"] > 10)) or len(raw) > limits["max_input_bytes"]:
            raise ValueError("Frozen protocol exceeds queue execution limits; preserve it and use a domain operator.")
        if request["input_sha256"] not in [i["sha256"] for i in p["input_versions"]]: raise ValueError("Input hash differs from the frozen protocol.")
        artifact = store.artifact(study_id, actor, raw, request["input_artifact_id"], "application/json", {"job_id": job_id, "transformation": "none; original UTF-8 queue input"})
        store.append(study_id, actor, "artifacts", artifact)
    result = store.rpc("research_queue_enqueue", dict(p_study=study_id, p_actor=actor, p_id=job_id, p_request=request, p_sha256=digest(canonical(request))))
    return dict(job=result, replayed=False, dispatch=wake_worker(result))

def blockers(job, indexed):
    reasons = []
    if job["execution_mode"] == "manual": reasons.append("Requires a domain operator or substantive review; automatic execution is inapplicable.")
    for dep in job["dependencies"]:
        prior = indexed.get(dep["job_id"])
        if not prior:
            reasons.append("Required predecessor is unavailable: " + dep["job_id"])
        elif dep["requirement"] == "reviewed-evidence" and prior["status"] != "reviewed":
            reasons.append("Awaiting reviewed evidence: " + dep["job_id"])
        elif dep["requirement"] == "complete-run" and (prior["status"] not in ("awaiting-review", "reviewed") or prior.get("run_status") != "complete"):
            reasons.append("Awaiting a complete recorded run: " + dep["job_id"])
    return reasons

def snapshot(study_id, actor):
    data = store.rpc("research_queue_snapshot", dict(p_study=study_id, p_actor=actor))
    jobs, events = data["jobs"], data["events"]
    if len(jobs) > 1000 or len(events) > 5000: raise store.StoreError("Queue export exceeds the interactive budget; no truncated history is returned.")
    indexed = {j["id"]: j for j in jobs}
    for job in jobs:
        job["blockers"] = blockers(job, indexed)
        job["readiness"] = "blocked" if job["status"] in ("queued", "manual") and job["blockers"] else "ready" if job["status"] == "queued" else job["status"]
        job.pop("lease_token", None)
    for event in events:
        event["details"] = {k: v for k, v in event["details"].items() if k != "actor_id"}
    result = dict(version=VERSION, study_id=study_id, access="owner-private", limits=LIMITS, workers_enabled=os.environ.get("RESEARCH_QUEUE_WORKERS_ENABLED") == "true", jobs=jobs, events=events,
      coverage=dict(jobs=len(jobs), events=len(events)),
      limitations="Readiness means prerequisites are satisfied; global/study slots and exclusive resources are checked atomically at claim time. Execution and review status do not establish scientific validity or independent evidence.")
    if len(canonical(result)) > 4_000_000: raise store.StoreError("Queue export exceeds the byte budget; history was not truncated.")
    return result

def overview(actor, title_study_id=None):
    """Owner-only paper queue, bounded across studies; no silent truncation."""
    owners = store.rows('study_owners', {'owner_id': 'eq.' + actor, 'limit': 101})
    if len(owners) > 100: raise store.StoreError('Queue study coverage exceeds 100; use an operator export.')
    ids = [r['study_id'] for r in owners]
    studies = store.rows('studies', {'id': 'in.(' + ','.join(ids) + ')', 'limit': 101}) if ids else []
    if {s['id'] for s in studies} != set(ids): raise store.StoreError('Queue ownership coverage is incomplete.')
    studies = [s for s in studies if s.get('study_kind', 'paper') == 'paper']
    if title_study_id is not None:
        title_study_id = identifier(title_study_id)
        studies = [dict(s, title=paper_titles.display_title(s)) if s['id'] == title_study_id else s for s in studies]
    ids = [s['id'] for s in studies]
    jobs = store.rows('queue_jobs', {'study_id': 'in.(' + ','.join(ids) + ')', 'limit': 1001}) if ids else []
    if len(jobs) > 1000: raise store.StoreError('Queue job coverage exceeds 1000; no truncated status is returned.')
    campaign_ids = sorted({j['campaign_id'] for j in jobs})
    campaigns = store.rows('campaigns', {'id': 'in.(' + ','.join(campaign_ids) + ')', 'study_id': 'in.(' + ','.join(ids) + ')', 'limit': 1000}) if campaign_ids else []
    titles = {c['id']: c['title'] for c in campaigns}
    if set(titles) != set(campaign_ids): raise store.StoreError('Queue campaign coverage is incomplete.')
    run_ids = sorted({j['run_id'] for j in jobs if j.get('run_id')})
    runs = store.rows('runs', {'id': 'in.(' + ','.join(run_ids) + ')', 'study_id': 'in.(' + ','.join(ids) + ')', 'limit': 1000}) if run_ids else []
    statuses = {r['id']: r['status'] for r in runs}
    indexed = {j['id']: dict(j, run_status=statuses.get(j.get('run_id'))) for j in jobs}
    public_jobs = []
    for job in indexed.values():
        row = {key: job.get(key) for key in ('id', 'study_id', 'campaign_id', 'execution_mode', 'priority', 'status', 'reason', 'dependencies', 'exclusive_resources', 'followup_of', 'run_id', 'run_status', 'created_at', 'updated_at', 'lease_until', 'definition_sha256')}
        row['campaign_title'] = titles[job['campaign_id']]
        row['blockers'] = blockers(job, indexed)
        row['readiness'] = 'blocked' if job['status'] in ('queued', 'manual') and row['blockers'] else 'ready' if job['status'] == 'queued' else job['status']
        public_jobs.append(row)
    import autonomy
    result = dict(version='research-queue-overview-v1', access='owner-private', studies=studies, jobs=public_jobs, autonomy=autonomy.overview(studies),
                  workers_enabled=os.environ.get('RESEARCH_QUEUE_WORKERS_ENABLED') == 'true', limits=LIMITS,
                  coverage=dict(studies=len(studies), jobs=len(jobs)),
                  limitations='Your paper studies only. Status is recorded execution progress, not scientific validation. Detailed event history remains in each private study queue. Missing run status is unknown.')
    if len(canonical(result)) > 4_000_000: raise store.StoreError('Queue overview exceeds its byte budget; no truncated status is returned.')
    return result

def public_snapshot(study_id, include_diagnostics=False):
    """Explicit status-only projection for a shared paper link, never a draft export.

    Backend service reads retain RLS/browser write denial. No public study listing,
    owners, prompts, uploaded files, raw inputs or resource identities are exposed
    by this endpoint. Recorded diagnostic text is redacted before publication.
    """
    study_id = identifier(study_id)
    studies = store.rows('studies', {'id': 'eq.' + study_id, 'select': 'id,title,study_kind,paper_upload', 'limit': 1})
    if not studies or studies[0].get('study_kind', 'paper') != 'paper': return None
    if len(studies) != 1 or studies[0].get('id') != study_id: raise store.StoreError('Shared paper identity is invalid.')

    def records(kind, select, maximum, **filters):
        # Continue to an empty page, including when PostgREST caps a page below
        # the requested limit. Never turn a server row cap into complete coverage.
        result = []
        while True:
            page = store.rows(kind, dict(study_id='eq.' + study_id, select=select,
                limit=min(200, maximum + 1 - len(result)), offset=len(result), **filters))
            if not page: break
            if any(r.get('study_id') != study_id for r in page): raise store.StoreError('Shared queue study coverage is invalid.')
            result.extend(page)
            if len(result) > maximum: raise store.StoreError('Shared queue exceeds its coverage budget; no truncated status was returned.')
        keys = [r.get('id', r.get('study_id')) for r in result]
        if len(set(keys)) != len(keys): raise store.StoreError('Shared queue identities changed during retrieval; retry the status read.')
        return result

    def linked(kind, select, ids):
        result = []
        for offset in range(0, len(ids), 100):
            chunk = ids[offset:offset + 100]
            page = records(kind, select, len(chunk), id='in.(' + ','.join(chunk) + ')')
            if any(r['id'] not in chunk for r in page): raise store.StoreError('Shared queue linked identities are invalid.')
            result.extend(page)
        return result

    jobs = records('queue_jobs', 'id,study_id,status,execution_mode,run_id,created_at,updated_at' + (',reason' if include_diagnostics else ''), 1000)
    runs = linked('runs', 'id,study_id,status' + (',reason' if include_diagnostics else ''), sorted({j['run_id'] for j in jobs if j.get('run_id')}))
    run_status = {r['id']: r['status'] for r in runs}
    run_reasons = {r['id']: r.get('reason') for r in runs}
    public_jobs = [dict({k: j[k] for k in ('id', 'study_id', 'status', 'execution_mode', 'created_at', 'updated_at')},
                        run_status=run_status.get(j.get('run_id')),
                        **({'reason': public_reason(j.get('reason')), 'run_reason': public_reason(run_reasons.get(j.get('run_id')))} if include_diagnostics else {})) for j in jobs]
    workflows = records('autonomy_workflows', 'study_id,max_spend_cents,max_attempts,committed_cents,status,reason,created_at,deadline_at,updated_at', 1)
    tasks = records('autonomy_tasks', 'id,study_id,claim_id,stage,status,label,attempts,reason,required_inputs,evidence_progress:context->evidence_progress,repair_progress:context->repair_progress,created_at,updated_at', 1000) if workflows else []
    claim_ids = sorted({t['claim_id'] for t in tasks if t.get('claim_id')})
    claims = linked('claims', 'id,study_id,description', claim_ids)
    titles = {c['id']: c['description'] for c in claims}
    if set(titles) != set(claim_ids): raise store.StoreError('Shared claim inventory coverage is incomplete.')
    task_reasons = {
        'queued': 'Waiting for a worker and available capacity.', 'running': 'A worker has started this stage.',
        'waiting': 'Waiting for recorded checks or a later controller turn.',
        'resolved': 'A finding has been recorded under the tested conditions.',
        'finished': 'Preparation finished within its recorded scope; this does not establish complete source review.',
        'blocked': 'Paused. No failure details or requirements were recorded.',
        'attempt-limit': 'Stopped at a retained attempt limit.', 'budget': 'No further work admitted within the paper commitment budget.',
        'deadline': 'The elapsed-time limit was reached.', 'cancelled': 'Cancelled; previous records are retained.',
    }
    workflow_reasons = {
        'running': 'Background preparation and claim checks are in progress.',
        'completed': 'The recorded claim tasks have finished under their tested conditions.',
        'completed-with-blockers': 'The workflow stopped with unresolved claims or unavailable requirements.',
        'budget': task_reasons['budget'], 'deadline': task_reasons['deadline'], 'cancelled': task_reasons['cancelled'],
    }
    public_tasks = []
    for task in tasks:
        row = {k: task[k] for k in ('id', 'study_id', 'claim_id', 'stage', 'status', 'label', 'attempts', 'created_at', 'updated_at')}
        fallback = task_reasons['finished'] if task['claim_id'] is None and task['status'] == 'resolved' else task_reasons.get(task['status'], 'Recorded status is unavailable.')
        row.update(claim_title=titles.get(task['claim_id']), reason=public_reason(task.get('reason'),
            fallback, task.get('required_inputs') if task['status'] == 'blocked' else None))
        progress = task.get('evidence_progress')
        repair = task.get('repair_progress')
        if repair is not None:
            from autonomous_repair import VERSION as repair_version, ACTIVE
            if not isinstance(repair,dict) or set(repair)!={'id','version','state','category','revision','updated_at'} or repair['version']!=repair_version or repair['state'] not in ACTIVE|{'resolved','blocked','budget','deadline','cancelled'} or repair['category'] not in ('runtime','reference','implementation') or type(repair['revision']) is not int or repair['revision']<0:
                raise store.StoreError('Shared operational repair status is invalid.')
            identifier(repair['id'])
            import datetime as dt
            dt.datetime.fromisoformat(repair['updated_at'].replace('Z','+00:00'))
            row['repair_progress']=repair
        if progress is not None:
            required, completed = progress.get('required_checks'), progress.get('completed_checks')
            if type(required) is not int or type(completed) is not int or not 0 <= completed <= required <= 1000:
                raise store.StoreError('Shared recorded-check coverage is invalid.')
            row['evidence_progress'] = dict(required_checks=required, completed_checks=completed,
                scope='Recorded check coverage only; scientific coverage depends on the frozen claim criterion.')
        public_tasks.append(row)
    public_workflows = [dict({k: w[k] for k in ('study_id', 'max_spend_cents', 'max_attempts', 'committed_cents', 'status', 'created_at', 'deadline_at', 'updated_at')},
                             reason=public_reason(w.get('reason'), workflow_reasons.get(w['status'], 'Recorded status is unavailable.'))) for w in workflows]
    import autonomy
    health = autonomy.runtime_health()
    runtime = {k: health[k] for k in ('runtime', 'model_id', 'status', 'updated_at') if k in health}
    result = dict(version=PUBLIC_VERSION, access='public-read-only', study=dict(id=study_id, title=paper_titles.display_title(studies[0])),
        jobs=public_jobs, workers_enabled=os.environ.get('RESEARCH_QUEUE_WORKERS_ENABLED') == 'true',
        autonomy=dict(version=autonomy.VERSION, access='public-read-only', defaults={k: autonomy.DEFAULTS[k] for k in ('maximum_spend_usd', 'elapsed_days', 'attempts_per_claim', 'parallel_claims_per_paper', 'global_parallel_claims')},
            enabled=os.environ.get('RESEARCH_AUTONOMY_ENABLED') == 'true', runtime=runtime,
            workflows=public_workflows, tasks=public_tasks, coverage=dict(workflows=len(workflows), tasks=len(public_tasks))),
        coverage=dict(jobs=len(public_jobs)),
        limitations='Shared recorded progress for this assessment. Counts come from bounded separate reads and can change during retrieval. Candidate inventory and scientific coverage may be incomplete. Execution is not scientific validation. Model turns, evidence and event history can be opened separately. Protected credentials, account information, private storage locations and original file downloads are omitted or redacted; original records remain unchanged.')
    if len(canonical(result)) > 4_000_000: raise store.StoreError('Shared queue exceeds its byte budget; no truncated status was returned.')
    return result

def action(study_id, actor, body):
    if set(body) - {"request_id", "job_id", "action", "reason", "assessment_ids", "run_id"}: raise ValueError("Unsupported queue action fields.")
    ids = body.get("assessment_ids", [])
    if not isinstance(ids, list) or len(ids) > 1000: raise ValueError("Assessment IDs must be a bounded array.")
    action = body.get("action")
    if action not in ("cancel", "review", "attach-run"): raise ValueError("Choose cancel, review or attach-run.")
    return store.rpc("research_queue_action", dict(p_study=study_id, p_actor=actor,
      p_job=identifier(body.get("job_id")), p_id=identifier(body.get("request_id")), p_action=action,
      p_reason=text(body.get("reason"), "Action reason", 5000), p_assessments=[identifier(i) for i in ids], p_run=identifier(body["run_id"]) if body.get("run_id") else None))
