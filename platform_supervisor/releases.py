"""Dispatch one scoped release and reconcile its exact workflow/artifact receipt."""
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from .common import POLICY, Hold

class ReleaseFailure(Hold):
    def __init__(self,run):
        super().__init__('Scoped release failed. Workflow '+str(run['id'])+' retains its original logs and rollback outcome.')
        self.receipt=dict(workflow_run_id=str(run['id']),conclusion=run['conclusion'],status='failed',production_revision_verified=False)

def plan(repository, paths, qualification=None):
    component=POLICY['repositories'][repository]['component']
    if component=='backend' and any(p.startswith('research_workflow/') for p in paths):
        if not all(p.startswith(('research_workflow/','docs/')) for p in paths):raise Hold('Research controller releases require an isolated source change.')
        if not qualification or qualification.get('version')!='research-controller-recovery-v1' or qualification.get('scientific_and_request_content_unchanged') is not True or qualification.get('existing_tests_unchanged') is not True:raise Hold('This research source requires the trusted operational qualification before release.')
        return 'research-runtime'
    if component=='backend' and any(p.startswith(('real_world/','openclaw_worker/')) for p in paths):
        raise Hold('This backend change needs its versioned runtime pin and existing qualification before release. Retained source is available; automatic AMI builds are reserved for benchmark code.')
    if component=='infrastructure' and any(p.startswith(('ami/','assets/openclaw/','agent/')) for p in paths):
        raise Hold('This change needs the dedicated image, worker or evaluated-agent release path; a general infrastructure release cannot replace those components.')
    return component

class Releases:
    def __init__(self, github):self.github=github
    def target(self,request):
        if request['component']=='research-runtime':return (POLICY['release_repository'],'research-runtime-release.yml')
        return (request['repository'],'deploy.yml') if request['component']=='backend' else (POLICY['release_repository'],POLICY['release_workflow'])
    def find(self, operation):
        repository,workflow=self.target(operation['request'])
        matches=[]
        for page in range(1,11):
            runs=self.github.get(repository,'actions/workflows/'+workflow+'/runs?event=workflow_dispatch&per_page=100&page='+str(page))['workflow_runs']
            matches.extend(r for r in runs if r.get('display_title')=='Platform release '+operation['id'])
            if len(runs)<100:break
        if len(matches)>1:raise Hold('Multiple release runs claim one operation identity.','reconciling')
        if not matches:return None
        run=matches[0]
        if run['head_sha']!=operation['request']['workflow_revision']:raise Hold('Release executor revision changed before dispatch; original run must reconcile.','reconciling')
        return run

    def dispatch(self, operation):
        if self.find(operation):return dict(dispatched=True)
        request=operation['request']
        repository,workflow=self.target(request)
        if self.github.main(repository)!=request['workflow_revision']:raise Hold('Release workflow main advanced; freeze a newly reviewed release intent.')
        inputs=dict(operation_id=operation['id'],expected_revision=request['revision']) if request['component']=='backend' else dict(operation_id=operation['id'],repository=request['repository'],revision=request['revision'],component=request['component'])
        if request['component']=='research-runtime':inputs=dict(operation_id=operation['id'],revision=request['revision'],pin_revision=request['pin_revision'])
        self.github.post(repository,'actions/workflows/'+workflow+'/dispatches',dict(ref='main',inputs=inputs))
        return dict(dispatched=True)

    def receipt(self, operation):
        run=self.find(operation)
        if not run: return None
        if run['status']!='completed':return None
        if run['conclusion']!='success':raise ReleaseFailure(run)
        repository,_=self.target(operation['request'])
        artifacts=self.github.get(repository,'actions/runs/'+str(run['id'])+'/artifacts?per_page=100')['artifacts']
        matches=[a for a in artifacts if a['name']=='platform-release-'+operation['id'] and not a['expired']]
        if len(matches)!=1:raise Hold('Successful workflow has no unique production verification artifact.','reconciling')
        artifact=matches[0]
        if artifact['size_in_bytes']>5000000:raise Hold('Release receipt exceeds its size limit.')
        # GitHub returns a presigned artifact URL. Authenticate only the first
        # API request, then explicitly strip credentials for the object host.
        url='https://api.github.com/repos/'+repository+'/actions/artifacts/'+str(artifact['id'])+'/zip'
        try:
            token=self.github.token() if callable(self.github.token) else self.github.token
            response=self.github.opener.open(urllib.request.Request(url,headers={'Authorization':'Bearer '+token,'User-Agent':'JumpServe-Platform-Supervisor'}),timeout=30)
        except urllib.error.HTTPError as error:
            if error.code!=302:raise Hold('Release artifact download is unconfirmed.','reconciling') from None
            target=urllib.parse.urlsplit(error.headers.get('Location',''))
            if target.scheme!='https' or not target.hostname or not (target.hostname.endswith('.blob.core.windows.net') or target.hostname.endswith('.githubusercontent.com')) or target.username or target.password:raise Hold('Unexpected release artifact host.')
            response=self.github.opener.open(urllib.request.Request(target.geturl()),timeout=30)
        with response:raw=response.read(5000001)
        if len(raw)>5000000:raise Hold('Release artifact exceeds its download limit.')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files=archive.infolist()
            if len(files)!=1 or files[0].filename!='production-verification.json' or files[0].file_size>100000:raise Hold('Release artifact has an unexpected layout.')
            proof=json.loads(archive.read(files[0]))
        expected=operation['request']
        if proof.get('passed') is not True or proof.get('revision')!=expected['revision'] or proof.get('repository')!=expected['repository'] or proof.get('operation_id')!=operation['id'] or proof.get('component')!=expected['component']:raise Hold('Production verification does not bind the requested release.')
        return dict(**proof,workflow_run_id=str(run['id']),workflow_revision=run['head_sha'])
