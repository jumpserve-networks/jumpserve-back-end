"""Owner-scoped PDF admission; immutable original bytes precede autonomous work."""
from urllib.parse import parse_qs, urlsplit
import uuid
import autonomy
import store
from workflow import digest, identifier, paper_details

VERSION = 'research-paper-upload-v1'
BUCKET = 'research-paper-uploads'
MAX_BYTES = 10_000_000


def capability():
    return dict(version=VERSION, maximum_bytes=MAX_BYTES, media_type='application/pdf')


def owned(actor, request_id):
    found = store.rows('paper_uploads', dict(id='eq.'+request_id, owner_id='eq.'+actor, limit=1))
    if len(found) != 1: raise ValueError('Private paper upload not found.')
    return found[0]


def completed(actor, row):
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
    if action == 'reserve':
        if set(body) != {'action','request_id','filename','sha256','byte_count','domain','scope'}: raise ValueError('Supply one PDF identity, research domain and assessment scope.')
        filename = body['filename']
        title = filename[:-4].strip() if isinstance(filename,str) else ''
        paper = paper_details(dict(title=title or 'Uploaded paper', paper_url=None,
            paper_upload={key:body[key] for key in ('filename','sha256','byte_count')}, domain=body['domain'], scope=body['scope'], study_kind='paper', origin_module=None))
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
    if action != 'start' or set(body) != {'action','request_id'}: raise ValueError('Choose reserve or start for the retained PDF upload.')
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
    result = store.rpc('research_paper_upload',dict(p_operation='start',p_actor=actor,p_id=request_id,p_payload=payload))
    result['dispatch'] = autonomy.wake()
    return result
