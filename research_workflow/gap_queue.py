"""Select retained checks for a gap without changing its published assessment."""
import time
import uuid
import os
import preparation
import scheduler
import store
from workflow import canonical, digest, identifier, paper_details

VERSION = 'research-gap-queue-v1'


def selection(study_id, actor, gap_id):
    snapshot = store.snapshot(study_id, owner=store.owns(study_id, actor))
    if not snapshot or snapshot['study'].get('study_kind', 'paper') != 'paper':
        raise ValueError('This paper assessment is unavailable to your account.')
    gaps = snapshot['records']['gaps']
    gap = next((g for g in gaps if g['id'] == identifier(gap_id)), None)
    if not gap or gap['status'] in ('resolved', 'stopped', 'inapplicable'):
        raise ValueError('Select a current open, blocked or planned evidence gap.')
    latest = max((g for g in gaps if g['claim_id'] == gap['claim_id']), key=lambda g: (g['created_at'], g['id']))
    if latest['id'] != gap['id']:
        raise ValueError('This evidence gap has a newer version. Refresh the assessment before queueing.')
    claim = next(c for c in snapshot['records']['claims'] if c['id'] == gap['claim_id'])
    source = next(s for s in snapshot['records']['sources'] if s['id'] == claim['source_id'])
    plan, raw = preparation.registered(snapshot['study'])
    matches = []
    if plan and source.get('sha256'):
        sources = {e['record']['id']: e['record'] for e in plan['records'] if e['kind'] == 'sources'}
        matches = [e['record']['id'] for e in plan['records'] if e['kind'] == 'claims'
                   and e['record']['description'] == claim['description'] and e['record']['location'] == claim['location']
                   and sources[e['record']['source_id']].get('sha256') == source['sha256']]
    jobs = []
    if len(matches) == 1:
        for item in plan['campaigns']:
            if matches[0] not in item['claim_ids']: continue
            jobs.append(dict(key=item['key'], title=item['campaign']['title'], execution_mode=item['execution_mode'],
                             experiment_type=item['campaign']['experiment_type'], coverage_limits=item['campaign']['coverage_limits'],
                             dependencies=item['dependencies']))
    gap_sha = digest(canonical(dict(study_id=study_id, gap=gap, claim=claim, source=source)))
    preview = dict(version=VERSION, study_id=study_id, gap_id=gap['id'], gap_sha256=gap_sha,
                   plan_sha256=digest(raw) if raw else None, choices=jobs,
                   limitation='These are retained checks, not a guarantee that the proposed gap can be resolved. Archived rechecks do not collect new network measurements. Manual tasks require a domain operator. Original assessments remain unchanged.',
                   unavailable_reason=None if jobs else 'No retained executable plan matches this exact claim and source version. Prepare a reviewed domain campaign in a private paper workspace before queueing this experiment.')
    return preview, snapshot, plan, raw


def preview(study_id, actor, gap_id):
    return selection(study_id, actor, gap_id)[0]


def enqueue(study_id, actor, body):
    if set(body) != {'request_id', 'gap_id', 'gap_sha256', 'plan_sha256', 'key', 'action'} or body['action'] not in ('create-followup', 'enqueue'):
        raise ValueError('Select a previewed gap and retained check without overriding its inputs.')
    identifier(body['request_id'])
    value, snapshot, plan, raw = selection(study_id, actor, body['gap_id'])
    if not value['choices'] or not any(c['key'] == body['key'] for c in value['choices']):
        raise ValueError('This gap has no matching retained check. No job was queued.')
    if body['gap_sha256'] != value['gap_sha256'] or body['plan_sha256'] != value['plan_sha256']:
        raise ValueError('The gap or retained plan changed. Refresh its preview before queueing.')
    # A stable owner-qualified identity makes double clicks, lost responses and
    # later visits resume this follow-up. It never opens another user's study.
    target = str(uuid.uuid5(uuid.UUID(actor), 'gap-followup:' + study_id + ':' + value['gap_id'] + ':' + value['gap_sha256'] + ':' + value['plan_sha256']))
    original = snapshot['study']
    paper = paper_details(dict(title=('Follow-up: ' + original['title'])[:300], paper_url=original['paper_url'], domain=original['domain'],
        origin_module=original['origin_module'], study_kind='paper',
        scope=f"Private follow-up to published or owner study {study_id}, gap {value['gap_id']} (selection SHA256 {value['gap_sha256']}). Only selected retained checks and their prerequisites are queued. Imported findings keep their original scope; no new measurement or scientific review is inferred."))
    if body['action'] == 'create-followup':
        store.rpc('research_create_study', dict(p_actor=actor, p_id=target, p_document=paper))
        # Retain the exact selection as well as the preparation manifest/input bytes.
        provenance = canonical(dict(version=VERSION, source_study_id=study_id, gap_id=value['gap_id'], gap_sha256=value['gap_sha256'], plan_sha256=value['plan_sha256']))
        artifact = store.artifact(target, actor, provenance, str(uuid.uuid5(uuid.UUID(target), 'gap-selection')), 'application/json',
                                  dict(transformation='Canonical gap selection provenance; no source content is executed.'))
        store.append(target, actor, 'artifacts', artifact)
        return dict(version=VERSION, study_id=target)
    if not store.owns(target, actor): raise ValueError('Create your private follow-up before queueing its checks.')
    study = dict(paper, id=target)
    started = time.monotonic()
    compiled = preparation.compile_plan(plan, raw, study)
    if not store.rows('prepared_plans', {'study_id': 'eq.' + target, 'id': 'eq.' + compiled['id'], 'limit': 1}):
        raise ValueError('Prepare and preserve this retained plan in your follow-up before queueing.')
    indexed = {j['key']: j for j in compiled['jobs']}
    required = set()

    def visit(key):
        if key in required: return
        for dependency in indexed[key]['request']['dependencies']:
            visit(next(j['key'] for j in compiled['jobs'] if j['id'] == dependency['job_id']))
        required.add(key)

    visit(body['key'])
    queued = []
    # The parser already sorts prerequisites first. No other campaign is queued.
    for job in compiled['jobs']:
        if job['key'] not in required: continue
        if time.monotonic() - started > 24: break
        result = preparation.enqueue(study, actor, dict(action='enqueue', prepared_id=compiled['id'], job_id=job['id']))
        if result['job']['id'] != job['id']: raise store.StoreError('Queue did not confirm the selected job identity.')
        queued.append(job['id'])
    selected = indexed[body['key']]['id']
    return dict(version=VERSION, study_id=target, job_id=selected, queued_job_ids=queued,
                complete=selected in queued, workers_enabled=os.environ.get('RESEARCH_QUEUE_WORKERS_ENABLED') == 'true',
                limitation=value['limitation'])
