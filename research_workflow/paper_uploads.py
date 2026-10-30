"""Owner-scoped PDF admission; immutable original bytes precede autonomous work."""
from urllib.parse import parse_qs, urlsplit
import uuid
import autonomy
import store
import paper_selection
from workflow import digest, identifier, paper_details

VERSION = 'research-paper-upload-v2'
REVIEW_VERSION = 'research-paper-review-v1'
BUCKET = 'research-paper-uploads'
MAX_BYTES = 10_000_000


def capability():
    return dict(version=VERSION, review_version=REVIEW_VERSION, selection_version=paper_selection.VERSION, maximum_bytes=MAX_BYTES, media_type='application/pdf', confirmation_required=True)


def owned(actor, request_id):
    found = store.rows('paper_uploads', dict(id='eq.'+request_id, owner_id='eq.'+actor, limit=1))
    if len(found) != 1: raise ValueError('Private paper upload not found.')
    return found[0]


def completed(actor, row):
    reviews=store.rows('paper_reviews',dict(study_id='eq.'+row['id'],limit=1))
    if reviews: return dict(id=row['id'],review_version=REVIEW_VERSION,status=reviews[0]['status'])
    if not store.rows('autonomy_workflows',dict(study_id='eq.'+row['id'],limit=1)):
        raise ValueError('This request ID already identifies a private paper workspace. Use its existing workflow.')
    return autonomy.start(actor, dict(request_id=row['id'], paper=row['paper'], autonomous=True))


def create_private_study(actor, target, paper, source_study):
    """Retain a legitimate owner's original PDF for an explicit follow-up workspace."""
    paper=paper_details(paper)
    if not paper.get('paper_upload'):
        return store.rpc('research_create_study',dict(p_actor=actor,p_id=target,p_document=paper))
    if not store.owns(source_study,actor): raise ValueError('This paper PDF is private. Upload your own copy before creating a follow-up workspace.')
    meta=paper['paper_upload'];artifact_id=str(uuid.uuid5(uuid.UUID(source_study),'paper-original:'+meta['sha256']))
    raw=store.artifact_bytes(source_study,artifact_id,maximum=MAX_BYTES)
    if digest(raw)!=meta['sha256'] or len(raw)!=meta['byte_count']: raise ValueError('The retained source PDF differs from this paper version.')
    artifact=store.artifact(target,actor,raw,str(uuid.uuid5(uuid.UUID(target),'paper-original:'+meta['sha256'])),'application/pdf',
        dict(kind='paper-original-upload',input_origin='owner-upload',transformation='none; byte-identical owner-authorized copy for a private follow-up workspace',source_study_id=source_study,source_artifact_id=artifact_id,sha256=meta['sha256']))
    return store.rpc('research_paper_upload',dict(p_operation='copy',p_actor=actor,p_id=target,p_payload=dict(paper=paper,source_study_id=source_study,artifact=artifact)))


def submit(actor, body):
    request_id = identifier(body.get('request_id')); actor = identifier(actor)
    action = body.get('action')
    if action in ('review','confirm','organize'):
        if not store.owns(request_id,actor): raise ValueError('Private paper review not found.')
        if action in ('review','organize'):
            if set(body)!={'action','request_id'}: raise ValueError('Supply the retained review identity.')
            if action=='organize':
                store.rpc('research_paper_review',dict(p_operation='organize',p_actor=actor,p_study=request_id,p_payload={}))
                autonomy.wake()
        else:
            if set(body) not in ({'action','request_id','title','domain','claim_ids','revision'},{'action','request_id','title','domain','claim_ids','revision','selection'}): raise ValueError('Confirm title, research domain, selected claims and review revision.')
            if not isinstance(body['title'],str) or not 1<=len(body['title'].strip())<=300 or not isinstance(body['domain'],str) or not 1<=len(body['domain'].strip())<=5000 or type(body['revision']) is not int or body['revision']<1: raise ValueError('Invalid paper review fields.')
            if not isinstance(body['claim_ids'],list) or not 1<=len(body['claim_ids'])<=200: raise ValueError('Select at least one extracted claim.')
            payload=dict(title=body['title'],domain=body['domain'],revision=body['revision'],claim_ids=[identifier(c) for c in body['claim_ids']])
            if 'selection' in body:
                selected=paper_selection.selection_ids(body['selection'],payload['claim_ids'])
                if set(selected)!=set(payload['claim_ids']) or len(selected)!=len(payload['claim_ids']):raise ValueError('Selected groups must match the selected assessment statements.')
                payload['selection']=body['selection']
            store.rpc('research_paper_review',dict(p_operation='confirm',p_actor=actor,p_study=request_id,p_payload=payload))
            autonomy.wake()
        return review(actor,request_id)
    if action == 'reserve':
        if set(body) != {'action','request_id','filename','sha256','byte_count'}: raise ValueError('Supply one PDF identity. Paper details are reviewed after extraction.')
        filename = body['filename']
        title = filename[:-4].strip() if isinstance(filename,str) else ''
        paper = paper_details(dict(title=title or 'Uploaded paper', paper_url=None,
            paper_upload={key:body[key] for key in ('filename','sha256','byte_count')}, domain='Pending paper review', scope='Assess owner-confirmed claims under their documented conditions; retain uncovered claims and evidence gaps.', study_kind='paper', origin_module=None))
        row = store.rpc('research_paper_upload',dict(p_operation='reserve',p_actor=actor,p_id=request_id,p_payload=paper))
        if row['status'] == 'started': return dict(version=VERSION,id=request_id,status='started',result=completed(actor,row))
        if row['status'] != 'pending': raise ValueError(row.get('reason') or 'This original upload was rejected; preserve it and submit a new file.')
        path = request_id+'/'+paper['paper_upload']['sha256']
        signed = store.request('/storage/v1/object/upload/sign/'+BUCKET+'/'+path,'POST',{})
        url = urlsplit(signed['url']); tokens = parse_qs(url.query).get('token',[])
        if url.netloc or url.path != '/object/upload/sign/'+BUCKET+'/'+path or len(tokens)!=1 or not 16<=len(tokens[0])<=8192:
            raise store.StoreError('Storage returned an invalid scoped upload permission.')
        # Returned only to its authenticated owner, never to prompts or logs.
        return dict(version=VERSION,id=request_id,status='pending',bucket=BUCKET,path=path,token=tokens[0],sha256=paper['paper_upload']['sha256'],byte_count=paper['paper_upload']['byte_count'],maximum_bytes=MAX_BYTES)
    if action != 'prepare' or set(body) != {'action','request_id'}: raise ValueError('Upload the paper, prepare its details, then confirm before starting the assessment.')
    row = owned(actor,request_id)
    if row['status'] == 'started': return completed(actor,row)
    if row['status'] != 'pending': raise ValueError(row.get('reason') or 'This upload cannot start an assessment.')
    meta = row['paper']['paper_upload']; path = request_id+'/'+meta['sha256']
    raw = store.request('/storage/v1/object/'+BUCKET+'/'+path,raw=True)
    reason = None
    if len(raw)!=meta['byte_count'] or len(raw)>MAX_BYTES or digest(raw)!=meta['sha256']: reason='Uploaded PDF bytes differ from their reserved size or SHA256. No assessment was started.'
    elif not raw[:1024].lstrip().startswith(b'%PDF-'): reason='Uploaded file has no PDF signature. No assessment was started.'
    if reason:
        store.rpc('research_paper_upload',dict(p_operation='reject',p_actor=actor,p_id=request_id,p_payload=dict(reason=reason)))
        raise ValueError(reason)
    original = store.artifact(request_id,actor,raw,str(uuid.uuid5(uuid.UUID(request_id),'paper-original:'+meta['sha256'])),'application/pdf',
        dict(kind='paper-original-upload',input_origin='owner-upload',transformation='none; exact original uploaded PDF bytes',upload_id=request_id,sha256=meta['sha256']))
    payload = dict(autonomy.start_payload(request_id,row['paper']),artifact=original)
    store.rpc('research_paper_upload',dict(p_operation='prepare',p_actor=actor,p_id=request_id,p_payload=payload))
    autonomy.wake()
    return dict(id=request_id,review_version=REVIEW_VERSION,status='preparing')


def review(actor, study):
    if not store.owns(study,actor): raise ValueError('Private paper review not found.')
    records=store.rows('paper_reviews',dict(study_id='eq.'+study,limit=1))
    if len(records)!=1: raise ValueError('Private paper review not found.')
    r=records[0]; workflow=store.rows('autonomy_workflows',dict(study_id='eq.'+study,limit=1))[0]
    claims=store.rows('claims',dict(study_id='eq.'+study,order='created_at.asc,id.asc',limit=201))
    if len(claims)>200: raise ValueError('Paper review exceeds the bounded claim inventory.')
    reason=r['reason'];status=r['status']
    if status=='preparing' and workflow['status']!='running': status='failed';reason=workflow['reason']
    allowed={s['claim_id'] for s in r['seeds']}
    can_start=status=='ready' and workflow['status'] not in ('cancelled','budget','deadline') and (workflow['max_spend_cents'] is None or workflow['committed_cents']+5<=workflow['max_spend_cents'])
    from datetime import datetime, timezone
    can_start=can_start and datetime.fromisoformat(workflow['deadline_at'].replace('Z','+00:00')).timestamp()>datetime.now(timezone.utc).timestamp()+180
    tasks=store.rows('autonomy_tasks',dict(study_id='eq.'+study,claim_id='is.null',limit=1))
    progress=tasks[0]['reason'] if tasks else 'Waiting for paper preparation.'
    runtime=autonomy.runtime_health()
    if status=='preparing' and runtime.get('status') not in ('ready','configured'): progress='Paper preparation is waiting for its AI worker: '+str(runtime.get('status','unavailable'))+'. No claim experiments have started.'
    selection=r['metadata'].get('selection')
    organization=dict(version=paper_selection.VERSION,status='unorganized',central_claims=[],testable_findings=[],candidate_count=len(allowed),cursor=0)
    if selection:organization=dict(organization,**selection)
    confirmation=dict(r['confirmation'],selection=r['metadata']['owner_selection']) if r['confirmation'] and r['metadata'].get('owner_selection') else r['confirmation']
    return dict(version=REVIEW_VERSION,id=study,status=status,revision=r['revision'],title=r['title'],domain=r['domain'],metadata=r['metadata'],progress=progress,organization=organization,
        claims=[{k:c[k] for k in ('id','description','claim_type','location','source_id')} for c in claims if c['id'] in allowed],
        coverage=r['coverage'],reason=reason,can_start=can_start,confirmation=confirmation,
        limits=dict(max_spend_cents=workflow['max_spend_cents'],committed_cents=workflow['committed_cents'],deadline_at=workflow['deadline_at']),access='owner-private')
