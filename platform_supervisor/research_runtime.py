"""Trusted operational-runtime qualification and derived pin publication.

The coding sandbox cannot write its own approval or runtime pin. This broker
derives both from frozen checks; scientific/request/transport changes fail closed.
"""
import base64
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
from . import source
from .common import canonical,digest,identity,save,Hold

BACKEND='jumpserve-networks/jumpserve-back-end'
INFRA='jumpserve-networks/jumpserve-infra'
ROOT=Path(__file__).resolve().parents[1]
APPROVAL='research_workflow/policies/autonomous-repair-release-v1.json'

def gate():
    path=ROOT/'research-runtime-gate.py'
    if not path.exists():path=ROOT.parent/'jumpserve-infra/assets/platform/research-runtime-gate.py'
    spec=importlib.util.spec_from_file_location('trusted_research_runtime_gate',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def derived_patch(directory,baseline,files):
    """Only the caller's fixed derived filenames, never candidate-supplied paths."""
    changes=[];current=source.snapshot(directory)
    for name,raw in sorted(files.items()):
        if not source.safe_name(name):raise Hold('Unsafe derived runtime metadata.')
        changes.append(dict(path=name,mode='100644',content=base64.b64encode(raw).decode()))
        current[name]=dict(sha256=digest(raw),mode='100644')
    # A generated pin never includes any unrelated source changes.
    if source.snapshot(directory)!=baseline:raise Hold('Runtime pin baseline changed during derivation.')
    return dict(version='platform-patch-v1',tree_sha256=digest(canonical(current)),files=changes)

def qualify(controller,directory,patch,check_receipt,check_artifact):
    module=gate();state=controller.state;store=controller.store
    root=store.spool/store.task['id']/'runtime-qualification'
    infra_revision=controller.github.main(INFRA)
    infra=root/infra_revision/'infra'
    if not infra.exists():source.extract(controller.github.archive(INFRA,infra_revision),infra)
    manifest=json.loads((infra/'research-workflow-runtime.json').read_bytes())
    previous=root/manifest['source_revision']/'backend'
    if not previous.exists():source.extract(controller.github.archive(BACKEND,manifest['source_revision']),previous)
    scope=module.inspect(previous/'research_workflow',directory/'research_workflow',manifest)
    if check_receipt['passed'] is not True:raise Hold('Frozen backend checks are required before runtime qualification.')
    driver=ROOT/'research-runtime-checks.py'
    if not driver.exists():driver=ROOT.parent/'jumpserve-infra/assets/platform/research-runtime-checks.py'
    controller.boundary()
    flags=controller.flags
    remaining=(dt.datetime.fromisoformat(flags['deadline_at'].replace('Z','+00:00'))-dt.datetime.now(dt.timezone.utc)).total_seconds()
    if remaining<30:raise Hold('Original deadline prevents operational qualification.','deadline')
    name='js-platform-'+store.task['id'].replace('-','')+'-runtime-qualification'
    command=['docker','run','--rm','--name',name,'--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','1g','--user',str(__import__('os').getuid())+':'+str(__import__('os').getgid()),'--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--env','HOME=/tmp','--mount','type=bind,source='+str(directory.resolve())+',target=/workspace,readonly','--mount','type=bind,source='+str(driver.resolve())+',target=/qualification/checks.py,readonly','--workdir','/workspace',controller.image,'python3','-B','/qualification/checks.py']
    try:result=subprocess.run(command,capture_output=True,timeout=min(120,remaining-5))
    except subprocess.TimeoutExpired:
        try:subprocess.run(['docker','rm','-f',name],check=True,capture_output=True,timeout=30)
        except Exception:
            from .native import CleanupUnknown
            raise CleanupUnknown('Runtime qualification cleanup remains uncertain.','reconciling') from None
        raise Hold('Runtime qualification timed out with original evidence retained.','testing') from None
    log=store.artifact('research-runtime-frozen-checks.json',canonical(dict(exit_code=result.returncode,stdout=result.stdout[-100000:].decode(errors='replace'),stderr=result.stderr[-100000:].decode(errors='replace'),network='none',source_read_only=True)))
    if result.returncode:raise Hold('Frozen operational qualification failed; original source and output retained.','testing')
    # Recheck every protected byte after the isolated process.
    if module.inspect(previous/'research_workflow',directory/'research_workflow',manifest)!=scope:raise Hold('Runtime source changed during qualification.')
    certificate=dict(version=module.VERSION,scope=scope,passed=True,backend_checks_artifact=check_artifact['sha256'],operational_checks_artifact=log['sha256'],driver_sha256=digest(driver.read_bytes()),gate_sha256=digest(Path(module.__file__).read_bytes()),image=controller.image,provider_calls=0,credential_free=True,scientific_completion=False)
    raw=canonical(certificate);artifact=store.artifact('research-runtime-qualification.json',raw)
    approval=canonical(module.approval(scope,artifact['sha256']))+b'\n'
    current=source.snapshot(directory);current[APPROVAL]=dict(sha256=digest(approval),mode='100644')
    files=list(patch['files'])+[dict(path=APPROVAL,mode='100644',content=base64.b64encode(approval).decode())]
    approved=dict(patch,files=files,tree_sha256=digest(canonical(current)))
    state['research_runtime']=dict(previous_manifest=manifest,scope=scope,certificate=certificate,certificate_sha256=artifact['sha256'],approval_sha256=digest(approval))
    store.event('research-runtime-qualified','Operational controller source passed the frozen qualification. Scientific/request content and existing tests are unchanged; approval metadata was derived by the trusted broker.',dict(source_binding=scope['source_binding'],certificate_sha256=artifact['sha256']))
    return approved

def add_pin(controller,backend_change,workspace):
    state=controller.state;runtime=state['research_runtime']
    existing=next((c for c in state['changes'] if c['component']=='research-runtime-pin' and c.get('status')!='superseded'),None)
    if existing:return existing
    module=gate();revision=controller.github.main(INFRA)
    directory=workspace.parent/'runtime-pin'/revision
    if not directory.exists():source.extract(controller.github.archive(INFRA,revision),directory)
    baseline=source.snapshot(directory);previous=json.loads((directory/'research-workflow-runtime.json').read_bytes())
    if previous!=runtime['previous_manifest']:raise Hold('Pinned runtime advanced. Retain this candidate and qualify against the new predecessor before release.')
    files=dict(runtime['scope']['files']);files[module.APPROVAL]=runtime['approval_sha256']
    manifest=module.pin(previous,backend_change['merge_sha'],files)
    certificate=dict(runtime['certificate'],backend_revision=backend_change['merge_sha'],previous_manifest=previous,manifest=manifest)
    path='research-runtime-qualifications/'+runtime['scope']['source_binding']+'.json'
    patch=derived_patch(directory,baseline,{'research-workflow-runtime.json':canonical(manifest)+b'\n',path:canonical(certificate)+b'\n'})
    artifact=controller.store.artifact('research-runtime-pin-patch.json',canonical(patch))
    change=dict(id=identity(controller.store.task['id'],'runtime-pin:'+backend_change['merge_sha']+':'+revision),repository=INFRA,base_sha=revision,head_sha=None,tree_sha256=patch['tree_sha256'],summary='Pin qualified operational research controller',patch_artifact_id=artifact['id'],checks_artifact_id=backend_change['checks_artifact_id'],pr_number=None,pr_url=None,merge_sha=None,status='tested',component='research-runtime-pin')
    state['changes'].append(change);controller.record_change(change);controller.save_state()
    return change

def refresh_pin(controller,workspace):
    """Rebase derived pin metadata only, never alter already dispatched releases."""
    candidates=[c for c in controller.state['changes'] if c['component']=='research-runtime-pin' and c.get('status')!='superseded']
    for pin in candidates:
        if pin.get('merge_sha') or controller.github.main(INFRA)==pin['base_sha']:continue
        if any(o['kind']=='deploy' for o in controller.store.rows('operations')):raise Hold('Original runtime release intent must reconcile before any pin replacement.','reconciling')
        if pin.get('pr_number'):
            def close(op):
                pull=controller.github.get(INFRA,'pulls/'+str(pin['pr_number']))
                if pull.get('merged'):raise Hold('Externally merged runtime pin requires reconciliation.','reconciling')
                if pull['state']!='closed':controller.github.request('PATCH','/repos/'+INFRA+'/pulls/'+str(pin['pr_number']),dict(state='closed'))
                return dict(closed=True,pr_number=pin['pr_number'])
            controller.operation('publish','close:'+pin['id'],dict(repository=INFRA,pr_number=pin['pr_number'],action='close-superseded'),close,close)
        pin['status']='superseded';controller.record_change(pin)
        backend=next(c for c in controller.state['changes'] if c['component']=='research-runtime')
        add_pin(controller,backend,workspace)

def release_request(controller,change):
    pin=next(c for c in controller.state['changes'] if c['component']=='research-runtime-pin' and c.get('status')!='superseded')
    if not pin.get('merge_sha'):raise Hold('Exact qualified runtime pin must merge before deployment.','merging')
    workflow_revision=controller.state.setdefault('release_workflow_revisions',{}).setdefault(INFRA,controller.github.main(INFRA))
    return dict(repository=BACKEND,revision=change['merge_sha'],component='research-runtime',pin_revision=pin['merge_sha'],workflow_revision=workflow_revision)

def record_pin_release(controller,proof):
    pin=next(c for c in controller.state['changes'] if c['component']=='research-runtime-pin' and c.get('status')!='superseded')
    if proof.get('pin_revision')!=pin['merge_sha']:raise Hold('Production proof does not bind the merged runtime pin.','reconciling')
    derived=dict(proof,repository=INFRA,revision=pin['merge_sha'],component='research-runtime-pin',runtime_revision=proof['revision'])
    controller.store.rpc('release',dict(id=identity(controller.store.task['id'],'release:'+INFRA+':'+pin['merge_sha']),component='research-runtime-pin',repository=INFRA,revision=pin['merge_sha'],status='verified',external_id=proof['operation_id'],verification=derived))
    return derived

def reviewed(controller,workspace):
    """Operator/service handoff of already reviewed main; never a model field."""
    revision=controller.state.get('runtime_reviewed_source')
    if not revision:return False
    if BACKEND not in controller.state['sources'] or revision!=controller.state['sources'][BACKEND]['revision'] or controller.github.main(BACKEND)!=revision:raise Hold('Reviewed runtime handoff differs from exact current backend main.')
    for repo,original in controller.state['sources'].items():
        if repo==BACKEND:continue
        name=controller.state['sources'][repo]
        from .common import POLICY
        if source.snapshot(workspace/POLICY['repositories'][repo]['directory'])!=name['snapshot']:raise Hold('Reviewed runtime handoff cannot include other unreviewed repository edits.')
    directory=workspace/'jumpserve-back-end'
    if source.snapshot(directory)!=controller.state['sources'][BACKEND]['snapshot']:raise Hold('Reviewed main handoff contains unreviewed working edits.')
    from . import checks
    receipt,artifact=checks.execute(controller.store,BACKEND,directory,controller.image)
    patch=qualify(controller,directory,dict(version='platform-patch-v1',files=[],tree_sha256=receipt['tree_sha256']),receipt,artifact)
    pointer=controller.store.artifact('reviewed-runtime-approval-patch.json',canonical(patch))
    change=dict(id=identity(controller.store.task['id'],'reviewed-runtime:'+revision),repository=BACKEND,base_sha=revision,head_sha=None,tree_sha256=patch['tree_sha256'],summary='Qualify retained operational research controller repairs',patch_artifact_id=pointer['id'],checks_artifact_id=artifact['id'],pr_number=None,pr_url=None,merge_sha=None,status='tested',component='research-runtime')
    controller.state.update(tested=True,changes=[change]);controller.record_change(change);controller.save_state()
    raise Hold('Reviewed controller source passed qualification; derived approval publication is the next durable stage.','publishing')

def resume(controller,proof):
    release=identity(controller.store.task['id'],'release:'+BACKEND+':'+proof['revision'])
    # The DB checks verified production proof, original context, cancellation,
    # deadlines and every uncertain identity again under its advisory lock.
    result=controller.store.request('/rest/v1/rpc/research_runtime_resume','POST',dict(p_release=release))
    controller.store.event('research-runtime-recovery','Verified controller release evaluated blocked claims for safe resumption; uncertain calls remain fenced.',result)
    return result
