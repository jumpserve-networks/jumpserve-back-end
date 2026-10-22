"""Dependency-aware scheduling. Parallel execution does not imply independence."""
import uuid
import os
import store
import network_execution
from workflow import canonical, digest, identifier, text

VERSION = "research-queue-v1"
PUBLIC_VERSION = "research-public-queue-v1"
LIMITS = dict(global_jobs=4, study_jobs=2, invocation_jobs=2, lease_seconds=600,
              input_bytes=256000, observations=1000, algorithm_wall_seconds=10,
              study_queue_jobs=1000, automatic_retries=0)
TERMINAL = ("awaiting-review", "reviewed", "failed", "cancelled", "expired")

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

def overview(actor):
    """Owner-only paper queue, bounded across studies; no silent truncation."""
    owners = store.rows('study_owners', {'owner_id': 'eq.' + actor, 'limit': 101})
    if len(owners) > 100: raise store.StoreError('Queue study coverage exceeds 100; use an operator export.')
    ids = [r['study_id'] for r in owners]
    studies = store.rows('studies', {'id': 'in.(' + ','.join(ids) + ')', 'limit': 101}) if ids else []
    if {s['id'] for s in studies} != set(ids): raise store.StoreError('Queue ownership coverage is incomplete.')
    studies = [s for s in studies if s.get('study_kind', 'paper') == 'paper']
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

def public_snapshot(study_id):
    """Explicit status-only projection for a shared paper link, never a draft export.

    Backend service reads retain RLS/browser write denial. No public study listing,
    owners, prompts, uploaded files, raw inputs, resource identities or free-form
    error/requirement text are exposed by this endpoint.
    """
    study_id = identifier(study_id)
    studies = store.rows('studies', {'id': 'eq.' + study_id, 'select': 'id,title,study_kind', 'limit': 1})
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

    jobs = records('queue_jobs', 'id,study_id,status,execution_mode,run_id,created_at,updated_at', 1000)
    runs = linked('runs', 'id,study_id,status', sorted({j['run_id'] for j in jobs if j.get('run_id')}))
    run_status = {r['id']: r['status'] for r in runs}
    public_jobs = [dict({k: j[k] for k in ('id', 'study_id', 'status', 'execution_mode', 'created_at', 'updated_at')},
                        run_status=run_status.get(j.get('run_id'))) for j in jobs]
    workflows = records('autonomy_workflows', 'study_id,max_spend_cents,max_attempts,committed_cents,status,created_at,deadline_at,updated_at', 1)
    tasks = records('autonomy_tasks', 'id,study_id,claim_id,stage,status,label,attempts,evidence_progress:context->evidence_progress,created_at,updated_at', 1000) if workflows else []
    claim_ids = sorted({t['claim_id'] for t in tasks if t.get('claim_id')})
    claims = linked('claims', 'id,study_id,description', claim_ids)
    titles = {c['id']: c['description'] for c in claims}
    if set(titles) != set(claim_ids): raise store.StoreError('Shared claim inventory coverage is incomplete.')
    task_reasons = {
        'queued': 'Waiting for a worker and available capacity.', 'running': 'A worker has started this stage.',
        'waiting': 'Waiting for recorded checks or a later controller turn.',
        'resolved': 'A finding has been recorded under the tested conditions.',
        'finished': 'Preparation finished within its recorded scope; this does not establish complete source review.',
        'blocked': 'Paused. The study owner can inspect the recorded requirements or failure details.',
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
        row.update(claim_title=titles.get(task['claim_id']), reason=task_reasons.get(task['status'], 'Recorded status is unavailable.'))
        progress = task.get('evidence_progress')
        if progress is not None:
            required, completed = progress.get('required_checks'), progress.get('completed_checks')
            if type(required) is not int or type(completed) is not int or not 0 <= completed <= required <= 1000:
                raise store.StoreError('Shared recorded-check coverage is invalid.')
            row['evidence_progress'] = dict(required_checks=required, completed_checks=completed,
                scope='Recorded check coverage only; scientific coverage depends on the frozen claim criterion.')
        public_tasks.append(row)
    public_workflows = [dict({k: w[k] for k in ('study_id', 'max_spend_cents', 'max_attempts', 'committed_cents', 'status', 'created_at', 'deadline_at', 'updated_at')},
                             reason=workflow_reasons.get(w['status'], 'Recorded status is unavailable.')) for w in workflows]
    import autonomy
    health = autonomy.runtime_health()
    runtime = {k: health[k] for k in ('runtime', 'model_id', 'status', 'updated_at') if k in health}
    result = dict(version=PUBLIC_VERSION, access='public-read-only', study={k: studies[0][k] for k in ('id', 'title')},
        jobs=public_jobs, workers_enabled=os.environ.get('RESEARCH_QUEUE_WORKERS_ENABLED') == 'true',
        autonomy=dict(version=autonomy.VERSION, access='public-read-only', defaults={k: autonomy.DEFAULTS[k] for k in ('maximum_spend_usd', 'elapsed_days', 'attempts_per_claim', 'parallel_claims_per_paper', 'global_parallel_claims')},
            enabled=os.environ.get('RESEARCH_AUTONOMY_ENABLED') == 'true', runtime=runtime,
            workflows=public_workflows, tasks=public_tasks, coverage=dict(workflows=len(workflows), tasks=len(public_tasks))),
        coverage=dict(jobs=len(public_jobs)),
        limitations='Public recorded status for this paper only. Counts come from bounded separate reads and can change during retrieval. Candidate inventory and scientific coverage may be incomplete. Execution is not scientific validation. Prompts, requirements, raw inputs, uploaded PDFs, owner information and private event history are omitted; status explanations are general summaries.')
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
