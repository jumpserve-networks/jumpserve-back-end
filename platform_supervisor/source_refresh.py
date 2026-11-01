"""Refresh an untested repair against exact main, retaining every old candidate.

The pending plan is persisted before either directory is replaced. On restart
the same plan completes; a changed or missing original fails closed.
"""
import json
import re
import shutil
from . import source
from .common import POLICY,canonical,digest,save,Hold

VERSION='platform-repair-source-refresh-v1'


def prepare(controller,workspace,repo,revision,root):
    name=POLICY['repositories'][repo]['directory'];old=controller.state['sources'][repo]
    candidate=workspace/name;current=source.snapshot(candidate)
    fresh=root/'baseline';merged=root/'candidate'
    if not fresh.exists():
        raw=controller.github.archive(repo,revision);incoming=root/'incoming-baseline'
        if incoming.exists():shutil.rmtree(incoming)
        incoming.mkdir(parents=True);source.extract(raw,incoming);incoming.rename(fresh)
    baseline=source.snapshot(fresh)
    if not baseline:raise Hold('Refreshed main source is empty; original candidate retained.','reconciling')
    if merged.exists():shutil.rmtree(merged)
    shutil.copytree(fresh,merged)
    conflicts=[]
    for path in sorted(set(old['snapshot'])|set(current)):
        previous=old['snapshot'].get(path);local=current.get(path);latest=baseline.get(path)
        if local==previous or local==latest:continue
        # Preserve overlapping and protected proposals in the original working
        # checkpoint; never overwrite concurrent main or its fixed checks.
        if latest!=previous or source.protected(path,path in old['snapshot']):
            conflicts.append(dict(repository=repo,path=path,proposed_deletion=local is None))
            continue
        target=merged/path
        if local is None:target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((candidate/path).read_bytes());target.chmod(0o755 if local['mode']=='100755' else 0o644)
    return dict(version=VERSION,repository=repo,previous=old,next=dict(revision=revision,snapshot=baseline),
        previous_candidate=current,candidate=source.snapshot(merged),conflicts=conflicts,
        original_working_artifact=controller.state['working_artifact'])


def replace(target,staged,backup,before,after):
    """Each rename is atomic; hashes distinguish its two restart positions."""
    if backup.exists():
        if source.snapshot(backup)!=before:raise Hold('Retained refresh backup differs.','reconciling')
        if target.exists():
            if source.snapshot(target)!=after:raise Hold('Refreshed source changed during recovery.','reconciling')
            return
    elif target.exists():
        if source.snapshot(target)!=before:raise Hold('Original source changed before refresh.','reconciling')
        if not staged.exists() or source.snapshot(staged)!=after:raise Hold('Staged refresh source differs; original remains in place.','reconciling')
        target.rename(backup)
    else:raise Hold('Original refresh source and backup are unavailable.','reconciling')
    if not staged.exists() or source.snapshot(staged)!=after:raise Hold('Staged refresh source differs; retained backup remains available.','reconciling')
    staged.rename(target)


def finish(controller,workspace,pointer):
    raw=controller.store.request('/storage/v1/object/platform-supervisor-private/'+pointer['path'],raw=True)
    if digest(raw)!=pointer['sha256']:raise Hold('Retained source refresh plan hash differs.','reconciling')
    plan=json.loads(raw);repo=plan['repository'];name=POLICY['repositories'][repo]['directory']
    if plan['version']!=VERSION or controller.state['sources'].get(repo)!=plan['previous']:raise Hold('Source refresh predecessor differs.','reconciling')
    root=workspace.parent/'source-refresh'/pointer['sha256'];root.mkdir(parents=True,exist_ok=True)
    # A replacement host reconstructs the old source/checkpoint before arriving
    # here. Rebuild only the same exact GitHub revision and verify the same plan.
    if not (root/'baseline').exists() and not (root/'old-baseline').exists():
        rebuilt=prepare(controller,workspace,repo,plan['next']['revision'],root)
        if rebuilt!=plan:raise Hold('Rebuilt refresh plan differs from its original.','reconciling')
    replace(workspace/name,root/'candidate',root/'old-candidate',plan['previous_candidate'],plan['candidate'])
    replace(workspace.parent/'baseline'/name,root/'baseline',root/'old-baseline',plan['previous']['snapshot'],plan['next']['snapshot'])
    # The full original delta, metadata and readiness reports remain retrievable
    # by this immutable checkpoint reference, including overlapping deletions.
    report=dict(version=VERSION,repository=repo,previous_revision=plan['previous']['revision'],revision=plan['next']['revision'],
        original_working_artifact=plan['original_working_artifact'],conflicts=plan['conflicts'],plan_sha256=pointer['sha256'])
    for conflict in report['conflicts']:
        if conflict['proposed_deletion']:continue
        relative='previous-candidates/source-refresh-'+pointer['sha256']+'/'+name+'/'+conflict['path']
        value=(root/'old-candidate'/conflict['path']).read_bytes();retained=workspace/relative
        if retained.exists() and (retained.is_symlink() or retained.read_bytes()!=value):raise Hold('Retained overlapping proposal differs.','reconciling')
        save(retained,value);conflict['previous_candidate']=relative
    save(workspace/'previous-candidates'/('source-refresh-'+pointer['sha256']+'.json'),canonical(report))
    proposal=workspace/'proposal.json'
    if proposal.exists():
        if proposal.is_symlink() or not proposal.is_file():raise Hold('Unsafe previous readiness report.','reconciling')
        value=proposal.read_bytes();save(workspace/'previous-candidates'/('proposal-'+digest(value)+'.json'),value);proposal.unlink()
    save(workspace/'rebase-conflicts.json',canonical(report))
    controller.state['sources'][repo]=plan['next'];controller.state.pop('source_refresh_pending',None)
    # A prior qualified candidate is never passed to this path. A special
    # reviewed-runtime handoff must be requested again for its new exact source.
    controller.state.pop('runtime_reviewed_source',None)
    controller.state['feedback']='Main advanced before coding. Read rebase-conflicts.json and its immutable original working checkpoint. Non-overlapping edits are retained; overlapping proposals are retained outside the candidate. Use the current adapter/schema and rerun all fixed checks.'
    controller.persist_work(workspace)
    controller.store.event('repair-source-refreshed','Retained repair acquired current exact main before coding; original candidate and conflicts remain immutable.',report)


def refresh(controller,workspace,resume_only=False):
    state=controller.state
    if state.get('source_refresh_pending'):
        finish(controller,workspace,state['source_refresh_pending'])
    if resume_only or not state.get('repair_contract') or state.get('tested'):return
    if any(c.get('status')!='superseded' for c in state.get('changes',[])):raise Hold('Existing qualified changes must reconcile before source refresh.','reconciling')
    for repo in sorted(state['sources']):
        revision=controller.github.main(repo)
        if not isinstance(revision,str) or not re.fullmatch('[a-f0-9]{40}',revision):raise Hold('Current main identity is unavailable.','reconciling')
        if revision==state['sources'][repo]['revision']:continue
        controller.boundary();controller.persist_work(workspace)
        staging=workspace.parent/'source-refresh'/'preparing'
        if staging.exists():shutil.rmtree(staging)
        staging.mkdir(parents=True)
        plan=prepare(controller,workspace,repo,revision,staging);raw=canonical(plan)
        artifact=controller.store.artifact('repair-source-refresh-plan.json',raw)
        pointer=dict(path=artifact['storage_path'],sha256=artifact['sha256'])
        destination=staging.parent/pointer['sha256']
        if destination.exists():raise Hold('Original source refresh plan already exists; reconcile its retained identity.','reconciling')
        staging.rename(destination)
        state['source_refresh_pending']=pointer;controller.save_state()
        finish(controller,workspace,pointer)
