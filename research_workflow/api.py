"""Public snapshots and shared queue status; Google-authenticated owner drafts."""
import json
import os
import urllib.error
import urllib.request
import uuid
import runner
import store
import scheduler
import preparation
import ideas
import claim_design
import network_execution
import network_spec
import gap_queue
import autonomy
import paper_uploads
import paper_catalog
import paper_titles
from workflow import LABELS, VERSION, canonical, digest, identifier, paper_details, protocol_record, validate_record

MAX_BODY = 512_000

class HttpError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

def authenticate(event):
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    bearer = headers.get("authorization", "")
    if not bearer.startswith("Bearer ") or len(bearer) > 8192: raise HttpError(401, "Google sign-in is required to manage studies.")
    if os.environ.get("SUPABASE_URL", "").rstrip("/") != f"https://{store.PROJECT}.supabase.co": raise HttpError(503, "Research authentication target is misconfigured.")
    request = urllib.request.Request(os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/user", headers={"Authorization": bearer, "apikey": os.environ["SUPABASE_ANON_KEY"]})
    try:
        with urllib.request.urlopen(request, timeout=8) as response: user = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403): raise HttpError(401, "Your session has expired. Sign in again.") from None
        raise HttpError(503, "Authentication is temporarily unavailable.") from None
    if user.get("is_anonymous") or not user.get("id") or not any(identity.get("provider") == "google" for identity in user.get("identities", [])):
        raise HttpError(403, "A legitimate Google-authenticated account is required.")
    return identifier(user["id"])

def parse_body(event):
    if event.get("isBase64Encoded"): raise HttpError(400, "Send a UTF-8 JSON object.")
    raw = event.get("body") or "{}"
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BODY: raise HttpError(413, "Research request exceeds the interactive byte limit.")
    data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON is invalid.")))
    if not isinstance(data, dict): raise ValueError("Send a JSON object.")
    return data

def campaign_run(study_id, actor, body):
    request_id = identifier(body.get("request_id")); campaign_id = identifier(body.get("campaign_id"))
    raw_text = body.get("raw_input")
    if not isinstance(raw_text, str): raise ValueError("Supply original JSON input as raw_input text, preserving its exact bytes.")
    raw = raw_text.encode()
    prior = store.rows("runs", {"id": "eq." + request_id, "study_id": "eq." + study_id, "limit": 1})
    if prior:
        if prior[0]["input_sha256"] != digest(raw) or prior[0]["campaign_id"] != campaign_id: raise HttpError(409, "Run ID was already used for another input or campaign.")
        return dict(run=prior[0], replayed=True)
    campaigns = store.rows("campaigns", {"id": "eq." + campaign_id, "study_id": "eq." + study_id, "limit": 1})
    if not campaigns: raise HttpError(404, "Campaign not found.")
    campaign = campaigns[0]
    protocols = store.rows("protocols", {"id": "eq." + campaign["protocol_id"], "study_id": "eq." + study_id, "limit": 1})
    if not protocols: raise HttpError(409, "Campaign has no frozen protocol.")
    if protocols[0]["document"]["resource_limits"]["max_observations"] > 1000 or protocols[0]["document"]["resource_limits"]["wall_seconds"] > 10:
        raise HttpError(400, "This protocol exceeds interactive execution limits. Use the operator CLI without changing the original protocol.")
    result = runner.execute(raw, protocols[0], campaign, request_id)
    input_artifact = store.artifact(study_id, actor, raw, str(uuid.uuid5(uuid.UUID(request_id), "original-input")), "application/json", {"run_id": request_id, "transformation": "none; original raw_input UTF-8 bytes"})
    output_artifact = store.artifact(study_id, actor, canonical(result), str(uuid.uuid5(uuid.UUID(request_id), "parsed-output")), "application/json", {"run_id": request_id, "input_sha256": digest(raw), "transformation": VERSION})
    entries = [dict(kind="artifacts", record=r) for r in (input_artifact, output_artifact)]
    entries.append(dict(kind="runs", record=result["run"]))
    for kind in ("published_values", "measurements", "summaries"):
        entries.extend(dict(kind=kind, record=row) for row in result[kind])
    store.append_bundle(study_id, actor, entries)
    return dict(run=result["run"], summaries=result["summaries"], comparisons=result["comparisons"], replayed=False,
      limitation="This is a numerical check, not an automatic claim assessment. Link and review the evidence before changing a claim label.")

def assessment_statuses(studies):
    """Small owner-only status projection; never send prompts or raw inputs."""
    ids = {row['id'] for row in studies if row.get('study_kind') != 'idea'}
    if not ids: return {}
    query = {'study_id': 'in.(' + ','.join(sorted(ids)) + ')', 'limit': 101,
             'select': 'study_id,status,updated_at'}
    workflows = store.rows('autonomy_workflows', query)
    if len(workflows) > 100 or len({w['study_id'] for w in workflows}) != len(workflows):
        raise store.StoreError('Assessment notification coverage exceeds the interactive bound.')
    states = ('running', 'completed', 'completed-with-blockers', 'budget', 'deadline', 'cancelled')
    if any(w['study_id'] not in ids or w['status'] not in states for w in workflows):
        raise store.StoreError('Assessment notification identities or status are invalid.')
    if not workflows: return {}
    workflow_ids = {w['study_id'] for w in workflows}
    tasks, cursor = [], None
    for _ in range(41):
        params = {'study_id': 'in.(' + ','.join(sorted(workflow_ids)) + ')',
            'select': 'id,study_id,claim_id,status,label', 'order': 'id.asc', 'limit': min(500, 20001 - len(tasks))}
        if cursor: params['id'] = 'gt.' + cursor
        page = store.rows('autonomy_tasks', params)
        if not page: break
        page_ids = [t['id'] for t in page]
        if len(page) > params['limit'] or page_ids != sorted(page_ids) or (cursor is not None and any(value <= cursor for value in page_ids)):
            raise store.StoreError('Assessment notification pagination is invalid.')
        tasks.extend(page)
        if len(tasks) > 20000: raise store.StoreError('Assessment notification coverage exceeds the interactive bound.')
        cursor = page[-1]['id']
    else:
        raise store.StoreError('Assessment notification pagination exceeds the interactive bound.')
    task_states = ('queued', 'running', 'waiting', 'resolved', 'finished', 'blocked', 'attempt-limit', 'budget', 'deadline', 'cancelled')
    if len(tasks) > 20000 or len({t['id'] for t in tasks}) != len(tasks) or any(
        t['study_id'] not in workflow_ids or t['status'] not in task_states or
        (t['claim_id'] is not None and t['label'] not in LABELS) for t in tasks):
        raise store.StoreError('Assessment notification claim coverage is invalid or exceeds the interactive bound.')
    claim_ids = [(t['study_id'], t['claim_id']) for t in tasks if t['claim_id'] is not None]
    if len(set(claim_ids)) != len(claim_ids): raise store.StoreError('Assessment notification claims are duplicated.')
    result = {w['study_id']: dict(status=w['status'], updated_at=w['updated_at'],
        counts=dict(total=0, finished=0, reproduced=0, discrepant=0, blocked=0, running=0, queued=0, waiting=0)) for w in workflows}
    for task in tasks:
        if task['claim_id'] is None: continue  # Intake is not a scientific claim.
        counts = result[task['study_id']]['counts']
        counts['total'] += 1
        if task['status'] in ('resolved', 'finished'):
            counts['finished'] += 1
            if task['label'] in ('reproduced', 'discrepant'): counts[task['label']] += 1
        if task['status'] in ('blocked', 'running', 'queued', 'waiting'): counts[task['status']] += 1
    return result


def dispatch(event, actor=None):
    path = event.get("rawPath", "").rstrip("/")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    query = event.get("queryStringParameters") or {}
    if path == '/research/papers' and method == 'GET':
        return paper_catalog.papers(query.get('before'), include_authors=query.get('bibliography') == '1', include_initiator=query.get('initiator') == '1')
    if path.startswith('/research/papers/') and method == 'GET':
        parts = path.split('/')
        if len(parts) != 5 or parts[4] != 'title': raise HttpError(404, 'Paper endpoint not found.')
        result = paper_catalog.title(parts[3], include_authors=query.get('bibliography') == '1')
        if result is None: raise HttpError(404, 'Paper not found.')
        return result
    if path == '/research/paper-uploads' and method == 'POST':
        if not actor: raise HttpError(401, 'Sign in to upload a private paper.')
        body=parse_body(event)
        if body.get('action')!='review' and os.environ.get('RESEARCH_AUTONOMY_ENABLED') != 'true': raise HttpError(503, 'Autonomous paper assessment is temporarily unavailable. No upload was admitted.')
        return paper_uploads.submit(actor, body)
    if path == "/research/capabilities" and method == "GET":
        return dict(autonomy=autonomy.capability(), paper_uploads=paper_uploads.capability(), version=VERSION, ideas=dict(version=ideas.VERSION, paper_required=False, packet_bytes=ideas.MAX_PACKET_BYTES), networking=network_execution.capabilities(), adapters=list(runner.ADAPTERS), preparation=dict(version=preparation.VERSION, gap_queue_version=gap_queue.VERSION, claim_design_version=claim_design.VERSION, source_grounded_plans=True, automatic_claim_extraction=False, uploaded_plan_bytes=preparation.MAX_UPLOADED_BYTES), queue=dict(version=scheduler.VERSION, overview_version="research-queue-overview-v1", limits=scheduler.LIMITS, workers_enabled=os.environ.get("RESEARCH_QUEUE_WORKERS_ENABLED") == "true", claim_labels_automatic=False), body_bytes=MAX_BODY, interactive_observations=1000, interactive_wall_seconds=10, labels=LABELS, arbitrary_code_execution=False, generic_ai_prompt="not enabled; requires independent module evaluation and publication" )
    if path == "/research/studies":
        if method == "GET":
            if query.get("mine") == "1":
                if not actor: raise HttpError(401, "Sign in to view your draft studies.")
                owners = store.rows("study_owners", {"owner_id": "eq." + actor, "limit": 101})
                if len(owners) > 100: raise HttpError(413, "Owner study listing exceeds 100 records; use operator pagination.")
                ids = [r["study_id"] for r in owners]
                status_only = query.get('status') == '1'
                study_query = {"id": "in.(" + ",".join(ids) + ")", "limit": 100}
                if status_only: study_query['select'] = 'id,title,domain,study_kind'
                studies = store.rows("studies", study_query) if ids else []
                designs = {row['study_id'] for row in store.rows('claim_designs', {'study_id': 'in.(' + ','.join(ids) + ')', 'select': 'study_id', 'limit': 101})} if ids and not status_only else set()
                review_select = 'study_id,status,title,domain' + ('' if status_only else ',authors:metadata->authors')
                reviews = {row['study_id']:row for row in store.rows('paper_reviews', {'study_id':'in.('+','.join(ids)+')','select':review_select,'limit':101})} if ids else {}
                studies = [dict(row,paper_review_status=reviews[row['id']]['status'],title=reviews[row['id']]['title'] or row['title'],domain=reviews[row['id']]['domain'] or row['domain'],
                    **({'authors':paper_titles.saved_authors(reviews[row['id']].get('authors'))} if not status_only and row.get('study_kind') != 'idea' else {})) if row['id'] in reviews else row for row in studies]
                studies = [dict(row, claim_design=True) if row['id'] in designs else row for row in studies]
                if status_only:
                    statuses = assessment_statuses(studies)
                    projected = [dict(id=row['id'], title=row['title'], study_kind=row.get('study_kind', 'paper'),
                        **({'paper_review_status': row['paper_review_status']} if 'paper_review_status' in row else {}),
                        assessment_status=statuses.get(row['id'])) for row in studies if row.get('study_kind') != 'idea']
                    return dict(studies=projected,
                                access='owner-drafts', assessment_status_version='research-assessment-status-v1')
                return {"studies": studies, "access": "owner-drafts"}
            records = store.rows("studies", {"limit": 101}, public=True)
            return {"studies": records[:100], "has_more": len(records) > 100, "access": "published"}
        if method == "POST":
            if not actor: raise HttpError(401, "Sign in to create a study.")
            body = parse_body(event)
            if body.get('autonomous') is True: return autonomy.start(actor,body)
            if 'autonomous' in body: raise ValueError('Choose the autonomous paper intake with its application defaults.')
            return store.rpc("research_create_study", dict(p_actor=actor, p_id=identifier(body.get("request_id")), p_document=paper_details(body.get("paper"))))
    if path == '/research/queue' and method == 'GET':
        if not actor: raise HttpError(401, 'Sign in to view your experiment queue.')
        return scheduler.overview(actor, query.get('title_study')) if query.get('title_study') else scheduler.overview(actor)
    parts = path.split("/")
    if len(parts) not in (4, 5) or parts[1:3] != ["research", "studies"]: raise HttpError(404, "Research endpoint not found.")
    study_id = identifier(parts[3])
    if len(parts) == 5 and parts[4] == 'queue' and method == 'GET' and query.get('public') == '1':
        data = scheduler.public_records(study_id, query['records'], query.get('after'), query.get('limit','25')) if query.get('records') else scheduler.public_snapshot(study_id, include_diagnostics=query.get('diagnostics') == '1')
        if data is None: raise HttpError(404, 'Paper queue not found.')
        return data
    if len(parts) == 5 and parts[4] == 'autonomy' and method == 'GET' and query.get('public') == '1' and query.get('task'):
        if query.get('builder_call'):
            if scheduler.shared_paper(study_id) is None:raise HttpError(404,'Paper task evidence not found.')
            data=autonomy.builder_evidence(study_id,query['task'],query['builder_call'],query.get('kind','request'),query.get('cursor','0'))
            value,projection=scheduler.shared_document(data)
            return dict(value,access='public-read-only',projection=projection)
        data = scheduler.public_history(study_id, query['task'], query.get('before'))
        if data is None: raise HttpError(404, 'Paper task history not found.')
        return data
    owner = bool(actor and store.owns(study_id, actor))
    if len(parts)==5 and parts[4]=='autonomy':
        if not owner: raise HttpError(404 if actor else 401,'Sign in as this paper owner to view autonomous progress.')
        if method=='GET':
            if query.get('task') and query.get('builder_call'):return autonomy.builder_evidence(study_id,query['task'],query['builder_call'],query.get('kind','request'),query.get('cursor','0'))
            if query.get('task'): return autonomy.history(study_id,query['task'],query.get('before'))
            return autonomy.overview([{'id':study_id}])
        if method=='POST' and parse_body(event)=={'action':'cancel'}:
            return store.rpc('research_autonomy_control',dict(p_operation='cancel',p_actor=actor,p_study=study_id,p_payload={}))
        raise HttpError(400,'Choose owner-scoped history or cancellation; limits and prompts are server owned.')
    if len(parts) == 5 and parts[4] == 'gap-experiments':
        if not actor: raise HttpError(401, 'Sign in to queue follow-up experiments.')
        if method == 'GET': return claim_design.selection(study_id, actor, query['claim_id'])[0] if query.get('claim_id') else gap_queue.preview(study_id, actor, query.get('gap_id'))
        if method == 'POST':
            body = parse_body(event)
            return claim_design.start(study_id, actor, body) if body.get('action') == 'design' else gap_queue.enqueue(study_id, actor, body)
        raise HttpError(405, 'GET or POST required.')
    if len(parts)==5 and parts[4]=='network':
        if not owner: raise HttpError(404 if actor else 401,'Sign in as this study owner to manage networking execution.')
        body=parse_body(event)
        if method!='POST': raise HttpError(405,'POST required.')
        action=body.pop('action',None)
        if action=='upload-package': return network_execution.upload(study_id,actor,body)
        if action=='upload-input': return network_execution.upload_input(study_id,actor,body)
        if action=='compile':
            if set(body)-{'document','original_configuration','configuration_identity'} or 'document' not in body: raise ValueError('Compile the supplied candidate protocol only.')
            candidate=body['document']
            if 'original_configuration' in body:
                from runner import parsed_decimal
                raw=body['original_configuration']
                if not isinstance(raw,str) or len(raw.encode())>24000: raise ValueError('Original configuration text exceeds the planning budget.')
                parsed=json.loads(raw,parse_float=parsed_decimal,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite configuration JSON.')))
                selected=next((c for c in candidate.get('configurations',[]) if c['identity']==body.get('configuration_identity')),None)
                if not selected or parsed!=selected['details']['test_config']: raise ValueError('Original configuration bytes lose numerical precision or differ from the selected executable configuration.')
                artifact=store.artifact(study_id,actor,raw.encode(),str(uuid.uuid5(uuid.UUID(study_id),'original-network-config:'+digest(raw.encode()))),'application/json',dict(kind='original-network-configuration',transformation='none; original owner configuration text'))
                store.append(study_id,actor,'artifacts',artifact)
                candidate['input_versions']=[v for v in candidate.get('input_versions',[]) if v.get('identity')!='original-network-config:'+selected['identity']]
                candidate['input_versions'].append(dict(identity='original-network-config:'+selected['identity'],sha256=artifact['sha256'],artifact_id=artifact['id']))
            return dict(document=network_spec.bind_inputs(candidate),limitations='Only executable specifications and hashes are compiled; scientific methods and readiness remain separately reviewed.')
        if action=='cancel': return network_execution.cancel(study_id,actor,body)
        raise ValueError('Choose upload-package, upload-input, compile or cancel.')
    if len(parts) == 5 and parts[4] in ("idea", "claim-design"):
        if not actor: raise HttpError(401, "Sign in to manage your private research idea.")
        if not owner: raise HttpError(404, "Study not found.")
        studies = store.rows("studies", {"id": "eq." + study_id, "limit": 1})
        designing = parts[4] == 'claim-design'
        retained = claim_design.context(study_id) if designing else None
        if not studies or studies[0].get('study_kind') != ('paper' if designing else 'idea'): raise HttpError(409, 'Choose the planning flow for this study origin.')
        if method == "GET": return claim_design.status(study_id) if designing else ideas.status(study_id)
        if method == "POST":
            body = parse_body(event)
            if body.get('action') == 'adopt' and designing: return claim_design.adopt(study_id, actor, body)
            if body.get("action") == "save": return claim_design.save(study_id, actor, body) if designing else ideas.save(study_id, actor, body)
            if body.get("action") == "accept": return claim_design.accept(study_id, actor, body) if designing else ideas.accept(study_id, actor, body)
            if body.get("action") == "prepare-campaign": return ideas.prepare_campaign(studies[0], actor, body, retained=retained)
            if body.get("action") == "resume-campaign": return ideas.resume_campaign(studies[0], actor, body, retained=retained)
            raise ValueError("Choose save, accept, prepare-campaign or resume-campaign.")
        raise HttpError(404, "Idea endpoint not found.")
    if len(parts) == 5 and parts[4] == "prepare":
        if not actor: raise HttpError(401, "Sign in to prepare the private study.")
        if not owner: raise HttpError(404, "Study not found.")
        studies = store.rows('studies', {'id': 'eq.' + study_id, 'limit': 1})
        if not studies: raise HttpError(404, "Study not found.")
        if studies[0].get("study_kind") == "idea": raise HttpError(409, "Use the idea planning flow; unpublished proposals do not have a registered paper manifest.")
        if method == 'GET': return preparation.status(studies[0])
        if method == 'POST':
            body = parse_body(event)
            if body.get('action') == 'prepare': return preparation.prepare(studies[0], actor, body)
            if body.get('action') == 'enqueue': return preparation.enqueue(studies[0], actor, body)
            raise ValueError('Choose prepare or enqueue; queued jobs keep their original definitions.')
        raise HttpError(404, 'Preparation endpoint not found.')
    if len(parts) == 5 and parts[4] == "queue" and method == "GET":
        if not actor: raise HttpError(401, "Sign in to view the private claim queue.")
        if not owner: raise HttpError(404, "Study not found.")
        return scheduler.snapshot(study_id, actor)
    if method == "GET" and len(parts) == 4:
        data = store.snapshot(study_id, owner=owner)
        if not data: raise HttpError(404, "Study not found or no published assessment is available.")
        if owner and store.rows('claim_designs', {'study_id': 'eq.' + study_id, 'limit': 1}): data['study']['claim_design'] = True
        return data
    if method != "POST" or len(parts) != 5: raise HttpError(404, "Research endpoint not found.")
    if not actor: raise HttpError(401, "Sign in to manage this study.")
    if not owner: raise HttpError(404, "Study not found.")
    body = parse_body(event)
    if parts[4] == "queue": return scheduler.enqueue(study_id, actor, body)
    if parts[4] == "queue-actions": return scheduler.action(study_id, actor, body)
    if parts[4] == "records":
        kind = body.get("kind")
        if kind in ("protocols", "runs", "measurements", "summaries", "published_values", "artifacts", "publications"):
            raise HttpError(400, "Execution records use the verified campaign runner or operator import.")
        return store.append(study_id, actor, kind, body.get("record"))
    if parts[4] == "protocols":
        existing = store.rows("protocols", {"id": "eq." + identifier(body.get("request_id")), "study_id": "eq." + study_id, "limit": 1})
        if existing:
            if existing[0]["document"] != body.get("document") or existing[0]["supersedes_id"] != body.get("supersedes_id") or existing[0]["version"] != body.get("version", 1) or existing[0]["amendment_reason"] != body.get("amendment_reason"):
                raise HttpError(409, "Protocol ID already used; preserve its original version and create an amendment.")
            return existing[0]
        row = protocol_record(body.get("document"), identifier(body.get("request_id")), body.get("supersedes_id"), body.get("version", 1), body.get("amendment_reason"))
        return store.append(study_id, actor, "protocols", row)
    if parts[4] == "runs": return campaign_run(study_id, actor, body)
    if parts[4] == "publish":
        if not isinstance(body.get("withdraw", False), bool): raise ValueError("withdraw must be boolean.")
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 5000: raise ValueError("Publication or withdrawal needs a reason.")
        return store.rpc("research_publish", dict(p_study=study_id, p_actor=actor, p_id=identifier(body.get("request_id")), p_scientific=identifier(body.get("scientific_review_id")), p_software=identifier(body.get("software_review_id")), p_reason=reason, p_withdraw=body.get("withdraw", False)))
    raise HttpError(404, "Research endpoint not found.")

def handler(event, context=None):
    try:
        method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        actor = authenticate(event) if method != "GET" or headers.get("authorization") else None
        result, status = dispatch(event, actor), 200
    except HttpError as error: result, status = {"error": error.message}, error.status
    except (ValueError, TypeError, KeyError) as error: result, status = {"error": str(error)}, 400
    except (store.StoreError, urllib.error.URLError): result, status = {"error": "Research storage is unavailable or rejected the operation. Preserve inputs and retry with the same ID."}, 503
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__, "version": VERSION}))
        result, status = {"error": "Research request failed. Preserve the original request and retry with the same ID; verify storage before treating it as recorded."}, 503
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"}, "body": json.dumps(result, allow_nan=False)}
