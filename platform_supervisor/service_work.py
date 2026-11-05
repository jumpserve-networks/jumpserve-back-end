"""Infrastructure service handoffs and trusted, derived activation manifests."""
import json
from pathlib import Path
import uuid
from .common import canonical,digest,identity,save,Hold
from . import source

VERSION='platform-service-work-v1'
MANIFEST='platform-service-manifest-v1'
INFRA='jumpserve-networks/jumpserve-infra'
PROFILES=('assessment-evidence-v1','intake-parser-recovery-v1')


def validate(value):
    if (not isinstance(value,dict) or set(value)!={'version','profile','objective','acceptance'}
        or value['version']!=VERSION or value['profile'] not in PROFILES
        or not isinstance(value['objective'],str) or not 1<=len(value['objective'])<=8000
        or not isinstance(value['acceptance'],list) or not 1<=len(value['acceptance'])<=12
        or any(not isinstance(x,str) or not 1<=len(x)<=500 for x in value['acceptance'])):
        raise ValueError('Invalid infrastructure service request.')
    return value


def manifest(request):
    return dict(version=MANIFEST,request_id=str(uuid.UUID(request['id'])),profile=request['profile'])


def inspect_manifest(value,request):
    if value!=manifest(request):raise ValueError('Service activation manifest differs from its immutable request.')
    return dict(passed=True,version='platform-service-contract-checks-v1',request_id=request['id'],
        profile=request['profile'],manifest_sha256=digest(canonical(value)),executable_changes=False,scientific_completion=False)


def rpc(controller,operation,payload=None):
    store=controller.store
    return store.request('/rest/v1/rpc/platform_service_work','POST',dict(p_operation=operation,
        p_worker=store.worker,p_task=store.task['id'],p_token=store.task['lease_token'],p_payload=payload or {}))


def capture(workspace,state):
    """Retain a typed request inside the existing versioned progress packet."""
    path=Path(workspace)/'progress.json'
    if not path.is_file() or path.is_symlink() or path.stat().st_size>32000:return False
    try:progress=json.loads(path.read_bytes())
    except (ValueError,UnicodeError):return False
    if not isinstance(progress,dict) or 'service_work' not in progress:return False
    if progress.get('version')!='platform-progress-v1' or progress.get('ready') is not False:
        raise Hold('Infrastructure work must be requested in a retained, unfinished progress packet.','implementing')
    try:spec=validate(progress['service_work'])
    except (ValueError,TypeError):
        raise Hold('Correct the infrastructure service request contract in progress.json.','implementing') from None
    state['pending_service_work']=dict(spec=spec,progress_sha256=digest(path.read_bytes()))
    raise Hold('Infrastructure service request retained; the trusted controller will assign its owning repository.','implementing')


def restore(controller,workspace,activation):
    if not activation or controller.state.get('service_work_restored')==activation['request_id']:return
    path=Path(workspace)/'progress.json'
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.stat().st_size>32000:
            raise Hold('Retained service stop requires reconciliation.','reconciling')
        raw=path.read_bytes();controller.store.artifact('progress-before-service-activation.json',raw)
        save(Path(workspace)/'previous-candidates'/('service-stop-'+digest(raw)+'.json'),raw);path.unlink()
    controller.state.pop('pending_service_work',None)
    controller.state['service_work_restored']=activation['request_id']
    if controller.state.get('repair_guard'):controller.state['repair_guard'].update(waiting=False,unchanged_slices=0)
    controller.state['feedback']='The requested infrastructure service has a verified release. Read references/service-work/index.json and continue from retained evidence. Original limits and uncertain calls are unchanged.'
    controller.save_state()


def prepare(controller,workspace,request):
    """Derive configuration only; executable code is the installed release broker."""
    if controller.store.task['repositories']!=[INFRA]:raise Hold('Infrastructure service work requires its owning repository only.')
    if controller.state.get('tested'):return
    directory=Path(workspace)/'jumpserve-infra';original=controller.state['sources'][INFRA]
    name='services/'+request['id']+'.json';value=manifest(request);raw=canonical(value)+b'\n'
    path=directory/name
    if path.is_symlink() or path.parent.is_symlink():raise Hold('Service manifest path contains a link.')
    if path.exists() and path.read_bytes()!=raw:raise Hold('Retain the original service manifest; its request identity differs.')
    save(path,raw)
    current=source.snapshot(directory)
    changed={n for n in set(original['snapshot'])|set(current) if original['snapshot'].get(n)!=current.get(n)}
    if changed!={name}:raise Hold('Derived service activation must be isolated from other infrastructure edits.')
    receipt=inspect_manifest(json.loads(path.read_bytes()),request)
    patch=source.freeze(original['snapshot'],directory)
    checks=controller.store.artifact('service-contract-checks.json',canonical(receipt))
    artifact=controller.store.artifact('service-manifest-patch.json',canonical(patch))
    change=dict(id=identity(controller.store.task['id'],'service-manifest:'+request['id']),repository=INFRA,
        base_sha=original['revision'],head_sha=None,tree_sha256=patch['tree_sha256'],summary='Activate qualified '+request['profile'],
        patch_artifact_id=artifact['id'],checks_artifact_id=checks['id'],pr_number=None,pr_url=None,merge_sha=None,
        status='tested',component='infrastructure',patch=patch)
    controller.state.update(changes=[change],tested=True,service_work_manifest=receipt)
    controller.record_change(change);controller.save_state()
    controller.store.event('service-manifest-qualified','Trusted service configuration derived from its immutable request. Protected branch CI and actual service execution remain required.',receipt)


def deliver(controller,workspace):
    if controller.flags.get('service_work_contract')!=VERSION:return
    packet=rpc(controller,'context')
    if not isinstance(packet,dict) or packet.get('version')!=VERSION:raise Hold('Installed infrastructure service contract differs.')
    evidence=packet.get('evidence')
    if evidence:
        failures=[]
        for artifact in evidence.get('artifacts',[]):
            if artifact.get('kind')!='failed-text-extraction':continue
            if type(artifact.get('byte_count')) is not int or not 0<=artifact['byte_count']<=1048576:continue
            study=str(uuid.UUID(evidence['assessment_id']));sha=artifact.get('sha256','')
            if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise Hold('Retained service artifact hash is invalid.','reconciling')
            raw=controller.store.request('/storage/v1/object/research-workflow-raw/'+study+'/'+sha,raw=True)
            if len(raw)!=artifact['byte_count'] or digest(raw)!=sha:raise Hold('Retained service evidence bytes differ.','reconciling')
            failures.append(dict(artifact_id=artifact['id'],sha256=sha,failure=json.loads(raw)))
        evidence['failed_text_extractions']=failures
    original=controller.store.artifact('infrastructure-service-context.json',canonical(packet))
    from scheduler import shared_document
    view,projection=shared_document(packet)
    root=Path(workspace)/'references/service-work'
    save(root/'index.json',canonical(dict(view,original_artifact_id=original['id'],projection=projection)))
    restore(controller,workspace,packet.get('activation'))
    pending=controller.state.get('pending_service_work')
    if not pending and not packet.get('implementation') and evidence:
        task=evidence.get('task') or {};review=evidence.get('review') or {};runtime=evidence.get('intake') or {}
        missing=any("No module named 'pypdf'" in item['failure'].get('reason','') for item in evidence.get('failed_text_extractions',[]))
        if (missing and task.get('stage')=='intake' and task.get('status')=='blocked' and task.get('attempts')==0
            and task.get('context')=={'intake_pool_version':'warm-intake-v1'} and review.get('status')=='failed'
            and review.get('candidate_count')==0 and review.get('confirmed') is False
            and runtime.get('enabled') is True and runtime.get('status')=='ready'):
            spec=dict(version=VERSION,profile='intake-parser-recovery-v1',
                objective='Recover this retained zero-call PDF intake failure using the installed pinned parser; reuse original source evidence and preserve the assessment scope, limits and call identities.',
                acceptance=['Verify the installed dedicated parser and original failure/PDF hashes.',
                    'Persist and read back the derived text artifact before guarded resumption.',
                    'Resume only the unchanged unconfirmed zero-call intake task; preserve uncertain-call fences and original limits.'])
            pending=dict(spec=spec,automatic_diagnosis='Hash-verified retained ModuleNotFoundError for pypdf.')
            controller.state['pending_service_work']=pending;controller.save_state()
    if pending:
        spec=validate(pending['spec'])
        artifact=controller.store.artifact('infrastructure-service-request.json',canonical(spec))
        result=rpc(controller,'request',dict(id=identity(controller.store.task['id'],'service:'+digest(canonical(spec))),
            spec=spec,artifact_id=artifact['id']))
        controller.state['service_work_wait']=result;controller.save_state()
        raise Hold('Infrastructure service work assigned to '+result['implementation_task_id']+'; retained parent waits for a verified release.','blocked')
    implementation=packet.get('implementation')
    if implementation:prepare(controller,workspace,implementation)
    instructions=('For infrastructure-owned evidence or a fresh zero-call PDF parser failure, request service work in progress.json: '
        'version platform-progress-v1, ready false, status blocked, service_work {version '+VERSION+
        ', profile assessment-evidence-v1 or intake-parser-recovery-v1, objective, acceptance [concrete checks]}. '
        'This creates a linked jumpserve-infra task and a protected, source-bound release; it does not grant permissions, execute model SQL or replay uncertain calls. '
        'Do not request another backend tool pack to obtain these existing service adapters. Unsupported profiles remain explicit implementation work.')
    if instructions not in controller.state.get('feedback',''):controller.state['feedback']=controller.state.get('feedback','')+' '+instructions
    controller.save_state()
