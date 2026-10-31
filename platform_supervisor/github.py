"""Credentialed broker. Native coding tools never receive this client or token."""
import base64
import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from .common import POLICY, canonical, Hold, network_opener

class ApiError(Hold):
    def __init__(self,status):
        super().__init__('GitHub request failed (HTTP '+str(status)+'); reconcile its original identity.','reconciling')
        self.code=status

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class GitHub:
    def __init__(self,token):
        if not callable(token) and (not token or token.startswith('Bearer ')): raise Hold('A scoped GitHub publisher credential is required.')
        self.token=token;self.opener=network_opener(NoRedirect())

    def request(self,method,path,body=None):
        if not path.startswith('/repos/') or '..' in path or not any(path=='/repos/'+repo or path.startswith('/repos/'+repo+'/') for repo in POLICY['repositories']): raise ValueError('GitHub path exceeds repository scope.')
        token=self.token() if callable(self.token) else self.token
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'JumpServe-Platform-Supervisor','Content-Type':'application/json'}
        try:
            with self.opener.open(urllib.request.Request('https://api.github.com'+path,method=method,data=canonical(body) if body is not None else None,headers=headers),timeout=30) as response:
                raw=response.read(5000001)
                if len(raw)>5000000:raise Hold('GitHub response exceeds its bound.')
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error: raise ApiError(error.code) from None

    def get(self,repository,path): return self.request('GET','/repos/'+repository+'/'+path)
    def post(self,repository,path,body): return self.request('POST','/repos/'+repository+'/'+path,body)

    def main(self,repository):
        sha=self.get(repository,'git/ref/heads/main')['object']['sha']
        if not re.fullmatch('[a-f0-9]{40}',sha):raise Hold('GitHub main revision is malformed.')
        return sha

    def archive(self,repository,revision):
        if repository not in POLICY['repositories'] or not re.fullmatch('[a-f0-9]{40}',revision): raise ValueError('Exact authorized revision required.')
        # These three repositories are public. No authorization header is sent to
        # the archive host, and redirects are refused.
        url='https://codeload.github.com/'+repository+'/tar.gz/'+revision
        with self.opener.open(urllib.request.Request(url,headers={'User-Agent':'JumpServe-Platform-Supervisor'}),timeout=60) as response:
            raw=response.read(100000001)
            if len(raw)>100000000: raise Hold('Repository archive exceeds 100 MB.')
            return raw

    def protection(self,repository):
        try: result=self.get(repository,'branches/main/protection')
        except ApiError as error:
            if error.code==404:raise Hold('Configure strict main-branch protection and the Platform validation check for '+repository+'.') from None
            raise
        required=result.get('required_status_checks') or {}
        contexts=set(required.get('contexts') or [])|{c['context'] for c in required.get('checks',[])}
        if required.get('strict') is not True or POLICY['required_check'] not in contexts or (result.get('enforce_admins') or {}).get('enabled') is not True:
            raise Hold('Strict, administrator-enforced Platform validation protection is required for '+repository+'.')
        return result

    def publish(self,task,change):
        repository=change['repository']; branch='platform/'+task['id']+'/'+change['id']
        base=change['base_sha']; tree=[]
        for file in change['patch']['files']:
            sha=None
            if file['content'] is not None:
                sha=self.post(repository,'git/blobs',dict(content=file['content'],encoding='base64'))['sha']
            tree.append(dict(path=file['path'],mode=file['mode'],type='blob',sha=sha))
        base_commit=self.get(repository,'git/commits/'+base)
        tree_sha=self.post(repository,'git/trees',dict(base_tree=base_commit['tree']['sha'],tree=tree))['sha']
        # Fixed author, date and message make a retried object creation content
        # addressed. A lost response cannot produce a second distinct commit.
        author=dict(name='JumpServe Platform Supervisor',email='platform-supervisor@users.noreply.github.com',date=task['created_at'])
        public_title='Improve JumpServe platform ('+repository.split('/')[1]+')'
        commit=self.post(repository,'git/commits',dict(message=public_title+'\n\nPlatform task: '+task['id'],tree=tree_sha,parents=[base],author=author,committer=author))['sha']
        try: current=self.get(repository,'git/ref/heads/'+branch)['object']['sha']
        except ApiError as error:
            if error.code!=404:raise
            try:self.post(repository,'git/refs',dict(ref='refs/heads/'+branch,sha=commit))
            except ApiError as error:
                if error.code!=422:raise
            current=self.get(repository,'git/ref/heads/'+branch)['object']['sha']
        if current!=commit:raise Hold('Retained publication branch differs from the frozen tested commit.')
        query=urllib.parse.urlencode(dict(head=repository.split('/')[0]+':'+branch,state='all',base='main',per_page=100))
        pulls=self.get(repository,'pulls?'+query)
        if len(pulls)>1:raise Hold('Publication branch has ambiguous pull-request identities.')
        if not pulls:
            body='Updates '+', '.join('`'+f['path']+'`' for f in change['patch']['files'])+'. The configured platform owners can review the objective and detailed evidence in private Platform Updates.\n\nValidation: fixed checks passed for the frozen source tree '+change['patch']['tree_sha256']+'.\n\nPlatform task: '+task['id']+'\nChange ID: '+change['id']+'\n\nGenerated and checked by the platform supervisor; no independent or human review is claimed.'
            try: pull=self.post(repository,'pulls',dict(title=public_title,body=body,head=branch,base='main'))
            except ApiError as error:
                if error.code!=422:raise
                pulls=self.get(repository,'pulls?'+query)
                if len(pulls)!=1:raise Hold('Publication acknowledgment remains uncertain.','reconciling')
                pull=pulls[0]
        else:pull=pulls[0]
        if pull['head']['sha']!=commit:raise Hold('Published pull-request head differs from the tested commit.')
        return dict(head_sha=commit,pr_number=pull['number'],pr_url=pull['html_url'],git_tree_sha=tree_sha)

    def merge_receipt(self,change):
        pull=self.get(change['repository'],'pulls/'+str(change['pr_number']))
        if pull['head']['sha']!=change['head_sha']:raise Hold('Pull-request head changed after validation.')
        if pull.get('merged'):
            return dict(merge_sha=pull['merge_commit_sha'],head_sha=change['head_sha'],pr_number=change['pr_number'])
        return None

    def ready(self,change):
        repository=change['repository'];self.protection(repository)
        if self.main(repository)!=change['base_sha']:raise Hold('Main advanced after this source was tested; rebuild and retest against the new base.','publishing')
        pull=self.get(repository,'pulls/'+str(change['pr_number']))
        if pull['head']['sha']!=change['head_sha'] or pull['base']['sha']!=change['base_sha'] or pull.get('mergeable_state')!='clean':raise Hold('Pull request is not clean at the exact tested head and base.','merging')
        checks=self.get(repository,'commits/'+change['head_sha']+'/check-runs?per_page=100')['check_runs']
        checks=[c for c in checks if c['name']==POLICY['required_check'] and (c.get('app') or {}).get('slug')=='github-actions']
        if not checks or any(c['status']!='completed' or c['conclusion']!='success' for c in checks):raise Hold('Waiting for successful Platform validation at the exact head.','merging')
        return True

    def merge(self,change):
        repository=change['repository'];self.ready(change)
        value=self.request('PUT','/repos/'+repository+'/pulls/'+str(change['pr_number'])+'/merge',dict(sha=change['head_sha'],merge_method='merge'))
        if value.get('merged') is not True:raise Hold('GitHub has not confirmed the merge.','reconciling')
        return self.merge_receipt(change)
