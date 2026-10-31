"""Bounded, restartable controller; one task, exact source, explicit receipts."""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from . import checks, native, source, documentation
from .common import POLICY, POLICY_SHA256, canonical, digest, identity, save, Hold
from .github import GitHub
from .releases import Releases, ReleaseFailure, plan
from .store import Store, BUCKET

def now():return dt.datetime.now(dt.timezone.utc)
WORK_PACKET_FILES=('progress.json','proposal.json','check-feedback.json','dependency-feedback.json','rebase-conflicts.json','documentation-requests.json')

class Controller:
    def __init__(self, store, github, image, documentation_key=None):
        self.store,self.github,self.image=store,github,image
        self.releases=Releases(github)
        self.state={};self.flags={};self.lease_error=None
        self.documentation_key=documentation_key

    def boundary(self):
        self.flags=self.store.rpc('lease')
        if self.lease_error:raise Hold('Worker lease refresh is unconfirmed; no new external action is permitted.','reconciling')
        if self.flags['cancel_requested']:raise Hold('Cancellation honored at a safe boundary; original in-flight receipts retained.','cancelled')
        if self.flags['paused']:raise Hold('Owner paused new work; checkpoint retained.','implementing')
        if dt.datetime.fromisoformat(self.flags['deadline_at'].replace('Z','+00:00'))<=now():raise Hold('Elapsed-time limit reached; remaining changes and receipts retained.','deadline')
        return self.flags

    def coding_available(self):
        spent=self.flags.get('committed_cents')
        maximum=self.flags.get('max_reserved_cents',self.store.task.get('max_reserved_cents'))
        if type(spent) is not int or spent<0 or type(maximum) is not int or maximum < -1:
            raise Hold('Recorded category budget is missing or invalid.','reconciling')
        calls=self.flags.get('model_calls',0)
        call_maximum=self.flags.get('max_model_calls',-1)
        if ('model_calls' in self.flags)!=('max_model_calls' in self.flags) or type(calls) is not int or calls<0 or type(call_maximum) is not int or call_maximum < -1:
            raise Hold('Recorded category call budget is missing or invalid.','reconciling')
        return (maximum==-1 or spent+100<=maximum) and (call_maximum==-1 or calls+1<=call_maximum)

    def save_state(self):self.store.rpc('progress',dict(checkpoint=self.state))

    def validate_workspace(self,workspace):
        repositories={POLICY['repositories'][repo]['directory'] for repo in self.store.task['repositories']}
        for path in workspace.iterdir():
            if path.is_symlink():raise Hold('Unsupported workspace link retained for diagnosis: '+path.name)
            if path.name not in repositories and path.is_dir() and any(item.is_symlink() for item in path.rglob('*')):raise Hold('Unsupported work-packet link retained for diagnosis: '+path.name)

    def operation(self, kind, label, request, dispatch, reconcile):
        self.boundary()
        sha=digest(canonical(request)); ident=identity(self.store.task['id'],kind+':'+label+':'+sha)
        op=self.store.rpc('begin-operation',dict(id=ident,kind=kind,request_sha256=sha,request=request))
        if op['status']=='confirmed':return op['result']
        if op['status']=='failed':raise Hold('The original '+kind+' operation failed; its receipt remains unchanged.')
        try:
            result=reconcile(op) if op.get('replayed') else dispatch(op)
            if result is None:raise Hold('Waiting to reconcile original '+kind+' operation '+ident+'.','reconciling')
            self.store.rpc('finish-operation',dict(id=ident,request_sha256=sha,status='confirmed',result=result))
            return result
        except ReleaseFailure as error:
            self.record_release_failure(op,error)
            raise
        except Exception:
            self.store.rpc('finish-operation',dict(id=ident,request_sha256=sha,status='unknown',result=dict(reason='Dispatch or acknowledgment requires reconciliation.')))
            raise

    def record_release_failure(self,operation,error):
        request=operation['request']
        self.store.rpc('release',dict(id=identity(self.store.task['id'],'release:'+request['repository']+':'+request['revision']),component=request['component'],repository=request['repository'],revision=request['revision'],status='failed',external_id=operation['id'],details=error.receipt))
        self.store.rpc('finish-operation',dict(id=operation['id'],request_sha256=operation['request_sha256'],status='failed',result=error.receipt))

    def reconcile_pending(self):
        changes={c['repository']:c for c in self.state.get('changes',[])}
        for operation in self.store.rows('operations'):
            if operation['status'] not in ('pending','unknown'):continue
            request=operation['request'];change=changes.get(request.get('repository'));result=None
            if operation['kind']=='deploy':
                try:result=self.releases.receipt(operation)
                except ReleaseFailure as error:self.record_release_failure(operation,error);raise
                if result:
                    self.store.rpc('release',dict(id=identity(self.store.task['id'],'release:'+request['repository']+':'+request['revision']),component=request['component'],repository=request['repository'],revision=request['revision'],status='verified',external_id=operation['id'],verification=result))
            elif operation['kind']=='merge' and change:
                result=self.github.merge_receipt(change)
                if result:change.update(result,status='merged');self.record_change(change)
            elif operation['kind']=='publish' and request.get('action')=='close-superseded':
                pull=self.github.get(request['repository'],'pulls/'+str(request['pr_number']))
                if pull['state']=='closed' and not pull.get('merged'):result=dict(closed=True,pr_number=request['pr_number'])
            elif operation['kind']=='publish' and change:
                raw,_=self.patch_bytes(change)
                result=self.github.publish(self.store.task,dict(change,patch=json.loads(raw)))
                if result:change.update(result,status='published');self.record_change(change)
            if result is None:raise Hold('Original '+operation['kind']+' operation '+operation['id']+' remains unconfirmed; cancellation, pause and limits prohibit replacement actions.','reconciling')
            self.store.rpc('finish-operation',dict(id=operation['id'],request_sha256=operation['request_sha256'],status='confirmed',result=result))
            self.save_state()

    def originals(self, workspace):
        for repo in self.store.task['repositories']:
            name=POLICY['repositories'][repo]['directory']; target=workspace/name
            if repo not in self.state.get('sources',{}):
                revision=self.github.main(repo);raw=self.github.archive(repo,revision)
                baseline=workspace.parent/'baseline'/name
                source.extract(raw,baseline)
                original=source.snapshot(baseline)
                self.state.setdefault('sources',{})[repo]=dict(revision=revision,snapshot=original)
                shutil.copytree(baseline,target,dirs_exist_ok=True)
                self.save_state()
            elif not target.exists():
                original=self.state['sources'][repo]
                source.extract(self.github.archive(repo,original['revision']),target)
            baseline=workspace.parent/'baseline'/name
            if not baseline.exists():source.extract(self.github.archive(repo,self.state['sources'][repo]['revision']),baseline)
        save(workspace/'task.json',canonical(dict(id=self.store.task['id'],objective=self.store.task['objective'],repositories=self.store.task['repositories'],deadline_at=self.store.task['deadline_at'],limits=dict(max_reserved_cents=self.store.task['max_reserved_cents']),protected=POLICY['protected'])))

    def persist_work(self, workspace):
        patches={}
        for repo,original in self.state.get('sources',{}).items():
            directory=workspace/POLICY['repositories'][repo]['directory']
            # Working checkpoints may contain a proposed protected-file edit;
            # retain it for diagnosis while publication still rejects it.
            current=source.snapshot(directory);files=[];total=0
            import base64
            for name in sorted(set(original['snapshot'])|set(current)):
                if original['snapshot'].get(name)==current.get(name):continue
                raw=(directory/name).read_bytes() if name in current else None;total+=len(raw or b'')
                files.append(dict(path=name,mode=current.get(name,original['snapshot'].get(name))['mode'],content=base64.b64encode(raw).decode() if raw is not None else None))
            if total>3000000 or len(files)>150:raise Hold('Working source exceeds bounded checkpoint size; retain local spool for review.')
            patches[repo]=dict(version='platform-working-v1',files=files)
        metadata={};size=0
        for path in sorted(workspace.rglob('*')):
            relative=path.relative_to(workspace).as_posix()
            if relative not in WORK_PACKET_FILES and not relative.startswith('previous-candidates/'):continue
            if path.is_symlink():raise Hold('Work packet contains a symlink.')
            if not path.is_file():continue
            raw=path.read_bytes();size+=len(raw)
            if len(metadata)>=200 or size>3000000:raise Hold('Work packet exceeds its bounded checkpoint size.')
            metadata[relative]=base64.b64encode(raw).decode()
        artifact=self.store.artifact('working-checkpoint.zip',source.checkpoint(patches,metadata))
        self.state['working_artifact']=dict(path=artifact['storage_path'],sha256=artifact['sha256'])
        self.save_state()

    def restore_work(self, workspace):
        pointer=self.state.get('working_artifact')
        if not pointer:return
        import io, zipfile, base64
        raw=self.store.request('/storage/v1/object/'+BUCKET+'/'+pointer['path'],raw=True)
        if digest(raw)!=pointer['sha256']:raise Hold('Working checkpoint hash differs.')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist())>4 or sum(f.file_size for f in archive.infolist())>12000000:raise Hold('Working checkpoint expansion exceeds its limit.')
            for repo in self.state['sources']:
                name=POLICY['repositories'][repo]['directory'];patch=json.loads(archive.read(name+'.json'))
                for item in patch['files']:
                    if not source.safe_name(item['path']):raise Hold('Working checkpoint contains an unsafe path.')
                    path=workspace/name/item['path']
                    if item['content'] is None:path.unlink(missing_ok=True)
                    else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(base64.b64decode(item['content'],validate=True));path.chmod(0o755 if item['mode']=='100755' else 0o644)
            metadata=json.loads(archive.read('work-packet.json'))
            if not isinstance(metadata,dict) or len(metadata)>200:raise Hold('Invalid work packet.')
            for name,encoded in metadata.items():
                if not source.safe_name(name) or (name not in WORK_PACKET_FILES and not name.startswith('previous-candidates/')):raise Hold('Unsafe work packet path.')
                save(workspace/name,base64.b64decode(encoded,validate=True))

    def patch_bytes(self,change):
        artifact=self.store.rows('artifacts',id='eq.'+change['patch_artifact_id'])[0]
        raw=self.store.request('/storage/v1/object/'+BUCKET+'/'+artifact['storage_path'],raw=True)
        if digest(raw)!=artifact['sha256']:raise Hold('Frozen patch hash differs.')
        return raw,artifact

    def rebase(self,workspace,changes):
        if any(c.get('merge_sha') for c in changes):return
        advances={c['repository']:self.github.main(c['repository']) for c in changes}
        if not any(advances[c['repository']]!=c['base_sha'] for c in changes):return
        conflicts=[]
        for change in changes:
            repo=change['repository'];name=POLICY['repositories'][repo]['directory']
            if advances[repo]!=change['base_sha']:
                raw,_=self.patch_bytes(change);patch=json.loads(raw)
                fresh=workspace.parent/('rebase-'+str(uuid.uuid4()));source.extract(self.github.archive(repo,advances[repo]),fresh)
                original=source.snapshot(fresh);previous=self.state['sources'][repo]['snapshot']
                for file in patch['files']:
                    if original.get(file['path'])==previous.get(file['path']):source.apply_patch(dict(files=[file]),fresh)
                    else:
                        import base64
                        retained=workspace/'previous-candidates'/name/file['path'];retained.parent.mkdir(parents=True,exist_ok=True)
                        if file['content'] is not None:save(retained,base64.b64decode(file['content'],validate=True))
                        conflicts.append(dict(repository=repo,path=file['path'],previous_candidate=str(retained.relative_to(workspace)) if file['content'] is not None else None,previous_proposed_deletion=file['content'] is None))
                shutil.rmtree(workspace/name);fresh.rename(workspace/name)
                self.state['sources'][repo]=dict(revision=advances[repo],snapshot=original)
            if change.get('pr_number'):
                def close(op):
                    pull=self.github.get(repo,'pulls/'+str(change['pr_number']))
                    if pull.get('merged'):raise Hold('A superseded candidate was externally merged; reconcile its actual production identity.','reconciling')
                    if pull['state']!='closed':self.github.request('PATCH','/repos/'+repo+'/pulls/'+str(change['pr_number']),dict(state='closed'))
                    return dict(closed=True,pr_number=change['pr_number'])
                self.operation('publish','close:'+change['id'],dict(repository=repo,pr_number=change['pr_number'],action='close-superseded'),close,close)
            change['status']='superseded';self.record_change(change)
        save(workspace/'rebase-conflicts.json',canonical(conflicts))
        self.state.update(tested=False,changes=[],generation=self.state.get('generation',0)+1,feedback='Main advanced. Non-overlapping changes were preserved on the fresh main source. Read rebase-conflicts.json and previous-candidates/ for overlapping edits; preserve concurrent work, repair the proposal and rerun checks. Original patches, tests and PRs remain recorded.')
        self.persist_work(workspace)
        raise Hold('Fresh main revisions acquired; native repair and all fixed checks will run again.','implementing')

    def run(self):
        task=self.store.task;self.state=task['checkpoint'] or {};root=self.store.spool/task['id'];workspace=root/'workspace';workspace.mkdir(parents=True,exist_ok=True)
        stop=threading.Event()
        def renew():
            while not stop.wait(30):
                try:
                    self.flags=self.store.rpc('lease')
                    self.store.rpc('heartbeat',dict(status='working',reason='Processing a leased improvement; original requests, checks and actions are retained.'))
                except Exception:self.lease_error=True;return
        thread=threading.Thread(target=renew,daemon=True);thread.start()
        try:
            removed=native.containers(native.container_prefix(task['id']),workspace)
            if removed:self.store.event('sandbox-cleanup-reconciled','Removed retained containers by their task workspace identity.',dict(container_ids=removed))
            self.validate_workspace(workspace)
            self.state.pop('cleanup_unknown',None);self.save_state()
            native.reconcile(self.store)
            self.reconcile_pending()
            self.boundary()
            self.originals(workspace)
            if not (root/'workspace-ready.json').exists():self.restore_work(workspace);save(root/'workspace-ready.json',canonical(dict(restored=True)))
            self.boundary()
            if not self.state.get('tested'):
                proposal=dict(ready=False,summary='Improve validated platform behavior',feedback='Model-call reservation is exhausted.')
                if self.coding_available():
                    try:documentation.deliver(self.store,workspace,self.state,documentation.Context7(self.documentation_key))
                    except Exception as error:
                        self.state['not_before']=(now()+dt.timedelta(minutes=5)).isoformat()
                        raise Hold('Required Context7 documentation is unavailable ('+type(error).__name__+'); retry is scheduled before further code generation.','investigating') from None
                    prefix='Read references/context7/index.json and relevant library documentation before coding. '
                    if not self.state.get('feedback','').startswith(prefix):self.state['feedback']=prefix+self.state.get('feedback','')
                    dependency_failures=[]
                    for repo in self.state['sources']:
                        receipt=checks.prepare_dependencies(self.store,repo,workspace/POLICY['repositories'][repo]['directory'],self.image,self.github)
                        if receipt:dependency_failures.append(receipt)
                    if dependency_failures:
                        save(workspace/'dependency-feedback.json',canonical(dependency_failures))
                        self.state['feedback']+=' Read dependency-feedback.json. Repair dependency manifests first; the controller retries installation before further application coding. Read the installed Next.js guide before writing Next.js code.'
                    self.save_state()
                    try:proposal=native.execute(self.store,workspace,root/('slice-'+str(uuid.uuid4())),self.image,self.state.get('feedback',''))
                    except Hold as error:
                        if error.status!='budget':raise
                self.validate_workspace(workspace)
                self.persist_work(workspace)
                self.boundary()
                exhausted=not self.coding_available()
                if not proposal['ready'] and not exhausted:
                    raise Hold(proposal['feedback'],'implementing')
                if exhausted:
                    self.store.event('retained-source-validation','Model-call reservation exhausted; fixed checks may validate retained source without another provider call. Original reservation and deadline remain unchanged.')
                changes=[]
                for repo,original in self.state['sources'].items():
                    directory=workspace/POLICY['repositories'][repo]['directory'];patch=source.freeze(original['snapshot'],directory)
                    if not patch:continue
                    component=plan(repo,[f['path'] for f in patch['files']])
                    receipt,artifact=checks.execute(self.store,repo,directory,self.image)
                    if not receipt['passed']:
                        save(workspace/'check-feedback.json',canonical(receipt))
                        self.state['feedback']='Repair the executed fixed checks. Read check-feedback.json for the actual failure output and original artifact references.'
                        self.persist_work(workspace)
                        if exhausted:raise Hold('Reservation exhausted and retained source failed fixed checks; original source and receipts remain retained.','budget')
                        raise Hold('Executed checks failed; native repair will continue from the retained source.','implementing')
                    if source.freeze(original['snapshot'],directory)!=patch:raise Hold('Source changed during testing.')
                    patch_artifact=self.store.artifact('patch-'+repo.split('/')[1]+'.json',canonical(patch))
                    change=dict(id=identity(task['id'],'change:'+repo+':'+str(self.state.get('generation',0))),repository=repo,base_sha=original['revision'],head_sha=None,tree_sha256=patch['tree_sha256'],summary=proposal.get('summary') or 'Improve validated platform behavior',patch_artifact_id=patch_artifact['id'],checks_artifact_id=artifact['id'],pr_number=None,pr_url=None,merge_sha=None,status='tested',component=component,patch=patch)
                    changes.append(change)
                if not changes:raise Hold('No source change was produced. Original coding history is retained.','budget' if exhausted else 'blocked')
                self.state.update(tested=True,changes=changes)
                # The durable SQL checkpoint remains below 1 MB: patches live
                # in immutable artifacts and are reconstructed on the next slot.
                for change in changes:change.pop('patch')
                self.save_state()
                for change in changes:self.record_change(change)
                raise Hold('Frozen source passed fixed checks; publication is the next durable stage.','publishing')
            changes=self.state['changes']
            self.rebase(workspace,changes)
            for change in changes:self.github.protection(change['repository'])
            for change in changes:
                self.record_change(change)
                self.boundary();self.github.protection(change['repository'])
                if not change.get('head_sha'):
                    raw,artifact=self.patch_bytes(change)
                    enriched=dict(change,patch=json.loads(raw))
                    result=self.operation('publish',change['repository'],dict(repository=change['repository'],base_sha=change['base_sha'],patch_sha256=artifact['sha256']),lambda op:self.github.publish(task,enriched),lambda op:self.github.publish(task,enriched))
                    change.update(result,status='published');self.record_change(change)
                if not change.get('merge_sha'):
                    # Waiting on CI creates no merge intent. Once dispatched,
                    # only reconciliation of that original merge is permitted.
                    if not self.store.rows('operations',kind='eq.merge',request_sha256='eq.'+digest(canonical(dict(repository=change['repository'],pr_number=change['pr_number'],head_sha=change['head_sha'],base_sha=change['base_sha'])))):
                        self.github.ready(change)
                    result=self.operation('merge',change['repository'],dict(repository=change['repository'],pr_number=change['pr_number'],head_sha=change['head_sha'],base_sha=change['base_sha']),lambda op:self.github.merge(change),lambda op:self.github.merge_receipt(change))
                    change.update(result,status='merged');self.record_change(change)
                    self.store.rpc('release',dict(id=identity(task['id'],'release:'+change['repository']+':'+change['merge_sha']),component=change['component'],repository=change['repository'],revision=change['merge_sha'],status='pending',external_id=None))
            for change in changes:
                workflow_repository=change['repository'] if change['component']=='backend' else POLICY['release_repository']
                workflow_revision=self.state.setdefault('release_workflow_revisions',{}).setdefault(workflow_repository,self.github.main(workflow_repository))
                request=dict(repository=change['repository'],revision=change['merge_sha'],component=change['component'],workflow_revision=workflow_revision)
                sha=digest(canonical(request));op_id=identity(task['id'],'deploy:'+change['repository']+':'+sha)
                release=dict(id=identity(task['id'],'release:'+change['repository']+':'+change['merge_sha']),component=change['component'],repository=change['repository'],revision=change['merge_sha'],status='deploying',external_id=op_id)
                self.store.rpc('release',release)
                # Dispatch success is not deployment success. Only a verified
                # production artifact confirms the durable deployment operation.
                def dispatch(op):self.releases.dispatch(op);return self.releases.receipt(op)
                proof=self.operation('deploy',change['repository'],request,dispatch,self.releases.receipt)
                release.update(status='verified',verification=proof);self.store.rpc('release',release)
            self.store.checkpoint('completed','All tested changes merged; every scoped production revision was verified.',self.state)
        except Hold as error:
            if isinstance(error,native.CleanupUnknown):self.state['cleanup_unknown']=True
            if error.status=='reconciling':self.state['not_before']=(now()+dt.timedelta(minutes=1)).isoformat()
            elif error.status=='investigating':self.state['not_before']=(now()+dt.timedelta(minutes=5)).isoformat()
            self.store.checkpoint(error.status,str(error),self.state)
        except native.runtime.CredentialFailure as error:
            self.state['not_before']=(now()+dt.timedelta(minutes=2)).isoformat()
            self.store.checkpoint('blocked' if error.status=='auth-required' else 'investigating','Subscription credential recovery: '+error.reason+'. No paid fallback or replacement provider call was used.',self.state)
        except Exception as error:
            self.state['not_before']=(now()+dt.timedelta(minutes=1)).isoformat()
            self.store.checkpoint('reconciling','Unexpected '+type(error).__name__+'; retained action identities must reconcile before new work.',self.state)
        finally:stop.set();thread.join(timeout=2)

    def record_change(self, change):
        payload={k:v for k,v in change.items() if k not in ('patch','component','git_tree_sha')}
        self.store.rpc('change',payload)

def main():
    spool=Path(os.environ.get('PLATFORM_SPOOL','/var/lib/jumpserve-platform'))
    spool.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (spool/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        identity_file=spool/'worker-id'
        if not identity_file.exists():save(identity_file,str(uuid.uuid4()).encode())
        from .credentials import Credentials
        credentials=Credentials()
        store=Store(credentials.supabase,spool,identity_file.read_text().strip())
        github=GitHub(credentials.github)
        controller=Controller(store,github,os.environ.get('PLATFORM_IMAGE',''),credentials.context7)
        while True:
            try:
                store.task=None
                store.rpc('heartbeat',dict(status='ready',reason='Private worker ready; fixed checks and exact production receipts required.'))
                store.rpc('maintenance',dict(policy_sha256=POLICY_SHA256))
                task=store.rpc('claim',dict(policy_sha256=POLICY_SHA256))
                if task:store.task=task;controller.lease_error=None;controller.run()
            except Exception as error:
                # Never print auth headers, response bodies, source or private
                # exception messages into host-wide journal logs.
                print(json.dumps(dict(status='waiting',reason=type(error).__name__)),flush=True)
            time.sleep(15)

if __name__=='__main__':main()
