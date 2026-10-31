import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from platform_supervisor.common import Hold, canonical, digest, save, identity
from platform_supervisor.credentials import Credentials
from platform_supervisor.github import GitHub, ApiError
from platform_supervisor import source
from platform_supervisor.native import failure_status,readiness,containers,container_prefix,confirmed_outcomes,CleanupUnknown
from platform_supervisor.releases import plan,ReleaseFailure
from platform_supervisor.worker import Controller

REPO='jumpserve-networks/jumpserve-front-end'
TASK='11111111-1111-4111-8111-111111111111'

class DurableStore:
    task=dict(id=TASK)
    def __init__(self):self.operations={};self.events=[]
    def rpc(self, operation, payload=None):
        if operation=='lease':return dict(paused=False,cancel_requested=False,automatic_releases=True,deadline_at='2099-01-01T00:00:00Z')
        if operation=='begin-operation':
            if payload['id'] in self.operations:return dict(self.operations[payload['id']],replayed=True)
            result=dict(payload,status='pending',replayed=False);self.operations[result['id']]=result;return dict(result)
        if operation=='finish-operation':
            self.operations[payload['id']].update(payload);self.events.append(payload);return payload
        raise AssertionError(operation)

class SupervisorTests(unittest.TestCase):
    def test_truncated_sandbox_names_are_cleaned_by_exact_workspace_without_touching_research(self):
        root=Path('/var/lib/jumpserve-platform')/TASK/'workspace';own='a'*64;research='b'*64
        records='\n'.join(json.dumps(value) for value in [dict(Id=own,Name='/js-plat-workspace-retained',Mounts=[dict(Type='bind',Source=str(root),Destination='/agent')]),dict(Id=research,Name='/research-workspace',Mounts=[dict(Type='bind',Source='/var/lib/jumpserve-openclaw/another-task',Destination='/workspace')])])
        with patch('platform_supervisor.native.subprocess.check_output',side_effect=[own+'\n'+research,records]),patch('platform_supervisor.native.subprocess.run') as remove:
            self.assertEqual(containers(container_prefix(TASK),root),[own]);self.assertEqual(remove.call_args.args[0],['docker','rm','-f',own])
        self.assertLessEqual(len(container_prefix(TASK))+len('workspace-'+'a'*32),63)
        with patch('platform_supervisor.native.subprocess.check_output',side_effect=TimeoutError),self.assertRaises(CleanupUnknown):containers(container_prefix(TASK),root)

    def test_known_failed_tool_proposals_do_not_become_unknown_or_erase_valid_progress(self):
        self.assertTrue(confirmed_outcomes([dict(status='complete'),dict(status='failed')]))
        self.assertFalse(confirmed_outcomes([dict(status='failed')]))
        for state in ('running','unknown'):self.assertFalse(confirmed_outcomes([dict(status='complete'),dict(status=state)]))

    def test_completed_proposal_survives_local_budget_stop_without_accepting_unknown_or_cancelled_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);error=dict(status='error',error=dict(kind='incomplete_turn'))
            self.assertFalse(readiness(root,error,['budget'],True)['ready'])
            (root/'proposal.json').write_text(json.dumps(dict(version='platform-proposal-v1',ready=True,summary='Retained tested source')))
            recovered=readiness(root,error,['budget'],True)
            self.assertTrue(recovered['ready']);self.assertEqual(recovered['readiness_origin'],'retained-proposal-after-local-admission-stop')
            for reasons,complete in [([],True),(['budget'],False),(['paused-cancelled-or-deadline'],True)]:
                self.assertFalse(readiness(root,error,reasons,complete)['ready'])

    def test_build_outputs_do_not_become_source_edits_and_runtime_inputs_stay_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'app.ts').write_text('source')
            before=source.snapshot(root)
            for name in ('next-env.d.ts','tsconfig.tsbuildinfo'):(root/name).write_text('generated')
            (root/'.runtime-autonomy-python').mkdir();(root/'.runtime-autonomy-python/manifest.json').write_text('{}')
            self.assertEqual(source.snapshot(root),before)
            self.assertIsNone(source.freeze(before,root))
        for name in ('bin/prepare-research-runtime.py','real-world-runtime.json','autonomy-pdf-runtime.json'):
            self.assertTrue(source.protected(name,True))

    def test_failed_release_records_failure_without_inventing_rollback_or_success(self):
        class Store(DurableStore):
            def rpc(self,operation,payload=None):
                if operation=='release':self.release=payload;return payload
                return super().rpc(operation,payload)
        store=Store();controller=Controller(store,Mock(),'unused')
        dispatch=Mock(side_effect=ReleaseFailure(dict(id=37,conclusion='failure')))
        request=dict(repository=REPO,revision='a'*40,component='frontend',workflow_revision='b'*40)
        with self.assertRaises(ReleaseFailure):controller.operation('deploy','case',request,dispatch,Mock())
        self.assertEqual(store.release['status'],'failed');self.assertFalse(store.release['details']['production_revision_verified'])
        self.assertEqual(next(iter(store.operations.values()))['status'],'failed')
        with self.assertRaises(Hold):controller.operation('deploy','case',request,dispatch,Mock())
        self.assertEqual(dispatch.call_count,1)

    def test_known_rejections_are_distinct_from_uncertain_streams(self):
        self.assertEqual(failure_status(False,None,None),'failed')
        self.assertEqual(failure_status(True,dict(status='completed'),200),'failed')
        for code in (400,401,403,404,413,422,429):self.assertEqual(failure_status(True,None,code),'failed')
        for code in (None,200,500,503):self.assertEqual(failure_status(True,None,code),'unknown')
    def test_controller_restores_durable_stages_and_waits_for_production_proof(self):
        class Store(DurableStore):
            def __init__(self,directory):
                super().__init__();self.spool=directory;self.task=dict(id=TASK,checkpoint={},repositories=[REPO],objective='Private objective',title='Private title',created_at='2026-10-30T00:00:00Z',deadline_at='2099-01-01T00:00:00Z',max_reserved_cents=10000);self.artifacts={};self.releases={};self.changes={};self.stages=[]
            def artifact(self,name,raw):
                ident=identity(TASK,digest(raw));item=dict(id=ident,name=name,sha256=digest(raw),storage_path=TASK+'/'+digest(raw),byte_count=len(raw));self.artifacts[ident]=(item,raw);return item
            def rows(self,table,**filters):
                values=list(self.operations.values()) if table=='operations' else [item for item,raw in self.artifacts.values()] if table=='artifacts' else []
                for key,value in filters.items():values=[row for row in values if row.get(key)==value.removeprefix('eq.')]
                return values
            def request(self,path,raw=False):return next(raw for item,raw in self.artifacts.values() if path.endswith(item['storage_path']))
            def rpc(self,operation,payload=None):
                if operation=='progress':self.task['checkpoint']=json.loads(json.dumps(payload['checkpoint']));return {}
                if operation=='change':self.changes[payload['id']]=payload;return payload
                if operation=='release':self.releases[payload['id']]=payload;return payload
                return super().rpc(operation,payload)
            def checkpoint(self,status,reason,checkpoint):self.stages.append(status);self.task['checkpoint']=json.loads(json.dumps(checkpoint))
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:gz') as archive:
            raw=b'old source';info=tarfile.TarInfo('root/app.ts');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
        github=Mock();github.main.return_value='a'*40;github.archive.return_value=stream.getvalue();github.publish.return_value=dict(head_sha='b'*40,pr_number=7,pr_url='https://github.com/'+REPO+'/pull/7');github.merge.return_value=dict(merge_sha='c'*40)
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));controller=Controller(store,github,'image')
            controller.releases=Mock();controller.releases.receipt.return_value=None
            def coding(store,workspace,*args):
                (workspace/'jumpserve-front-end/app.ts').write_text('corrected source')
                return dict(ready=True,summary='Changed public source')
            def checked(store,repo,directory,image):return dict(passed=True),store.artifact('checks.json',b'{"passed":true}')
            with patch('platform_supervisor.worker.native.containers',return_value=[]),patch('platform_supervisor.worker.native.reconcile'),patch('platform_supervisor.worker.documentation.deliver'),patch('platform_supervisor.worker.checks.prepare_dependencies',return_value=None),patch('platform_supervisor.worker.native.execute',side_effect=coding),patch('platform_supervisor.worker.checks.execute',side_effect=checked):
                controller.run();self.assertEqual(store.stages[-1],'publishing')
                controller.run();self.assertEqual(store.stages[-1],'reconciling')
                self.assertEqual(controller.releases.dispatch.call_count,1)
                self.assertNotEqual(store.stages[-1],'completed')
                controller.releases.receipt.return_value=dict(passed=True,revision='c'*40)
                controller.run();self.assertEqual(store.stages[-1],'completed')
                self.assertEqual(controller.releases.dispatch.call_count,1)
                self.assertEqual(github.publish.call_count,1);self.assertEqual(github.merge.call_count,1)
                self.assertTrue(all(r['status']=='verified' for r in store.releases.values()))

    def test_unknown_mutation_is_reconciled_without_redispatch(self):
        store=DurableStore();controller=Controller(store,Mock(),'unused')
        dispatch=Mock(side_effect=TimeoutError('lost response'));reconcile=Mock(return_value=dict(merge_sha='a'*40))
        with self.assertRaises(TimeoutError):controller.operation('merge','case',dict(head='b'*40),dispatch,reconcile)
        self.assertEqual(next(iter(store.operations.values()))['status'],'unknown')
        # Simulate a different process with only retained database identities.
        recovered=Controller(store,Mock(),'unused')
        self.assertEqual(recovered.operation('merge','case',dict(head='b'*40),dispatch,reconcile),dict(merge_sha='a'*40))
        self.assertEqual(dispatch.call_count,1);self.assertEqual(reconcile.call_count,1)
        self.assertEqual(recovered.operation('merge','case',dict(head='b'*40),dispatch,reconcile),dict(merge_sha='a'*40))
        self.assertEqual(reconcile.call_count,1)

    def test_pending_receipt_does_not_redispatch(self):
        store=DurableStore();controller=Controller(store,Mock(),'unused');dispatch=Mock(return_value=None);reconcile=Mock(return_value=None)
        for _ in range(2):
            with self.assertRaises(Hold):controller.operation('deploy','case',dict(revision='a'*40),dispatch,reconcile)
        self.assertEqual(dispatch.call_count,1);self.assertEqual(reconcile.call_count,1)

    def test_pause_cancel_deadline_and_lost_lease_fence_new_actions(self):
        for changes in (dict(paused=True),dict(cancel_requested=True),dict(deadline_at='2000-01-01T00:00:00Z')):
            store=DurableStore();original=store.rpc('lease');store.rpc=Mock(return_value={**original,**changes})
            with self.assertRaises(Hold):Controller(store,Mock(),'unused').boundary()
        controller=Controller(DurableStore(),Mock(),'unused');controller.lease_error=True
        with self.assertRaises(Hold):controller.boundary()

    def test_frozen_source_rejects_links_authority_and_existing_test_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'app').mkdir();(root/'app/main.ts').write_text('original')
            baseline=source.snapshot(root);(root/'app/main.ts').write_text('changed')
            frozen=source.freeze(baseline,root)
            self.assertEqual(frozen['tree_sha256'],digest(canonical(source.snapshot(root))))
            (root/'AGENTS.md').write_text('escalate authority')
            with self.assertRaises(Hold):source.freeze(baseline,root)
            (root/'AGENTS.md').unlink();(root/'app/link').symlink_to('/etc/passwd')
            with self.assertRaises(Hold):source.snapshot(root)
        self.assertTrue(source.protected('tests/existing.test.ts',True))
        for name in ('test_benchmark_ingest.py','test_sender_metrics.py','component.test.ts','component.spec.ts','module_test.py'):
            self.assertTrue(source.protected(name,True))
        self.assertFalse(source.protected('tests/new.test.ts',False))

    def test_excluded_secret_and_dependency_paths_never_enter_patch(self):
        for name in ('.env','.env.local','.aws/credentials','.codex/config.toml','.agents/private.json','app/node_modules/pkg/a.js','.git/config','experiments/private.txt'):
            self.assertFalse(source.safe_name(name))
        for name in ('../escape','/absolute','a/../escape','a\\escape'):
            with self.assertRaises(Hold):source.safe_name(name)

    def test_source_archive_cannot_escape_through_parent_or_links(self):
        for name,kind in (('root/../escape',tarfile.REGTYPE),('root/link',tarfile.SYMTYPE)):
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w:gz') as archive:
                info=tarfile.TarInfo(name);info.type=kind;info.linkname='/etc/passwd';archive.addfile(info,io.BytesIO())
            with tempfile.TemporaryDirectory() as temp, self.assertRaises(Hold):source.extract(stream.getvalue(),temp)

    def test_candidate_credentials_are_not_published(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'token.txt').write_text('github_pat_synthetic-not-real')
            with self.assertRaises(Hold):source.freeze({},root)

    def test_atomic_spool_retains_exact_zero_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'nested/receipt';save(path,b'');self.assertEqual(path.read_bytes(),b'')
            save(path,b'new');self.assertEqual(path.read_bytes(),b'new')

    def test_git_client_rejects_other_repositories_and_redirect_credentials(self):
        github=GitHub('synthetic-not-a-token')
        with self.assertRaises(ValueError):github.request('POST','/repos/other/repo/git/refs',{})
        with self.assertRaises(ValueError):github.request('GET','/repos/'+REPO+'/../other')
        with self.assertRaises(Hold):GitHub('')

    def test_exact_head_base_checks_and_strict_protection_precede_merge(self):
        github=GitHub('synthetic');github.protection=Mock(return_value={});github.main=Mock(return_value='a'*40)
        change=dict(repository=REPO,base_sha='a'*40,head_sha='b'*40,pr_number=7)
        pull=dict(head=dict(sha='b'*40),base=dict(sha='a'*40),mergeable_state='clean')
        checks=dict(check_runs=[dict(name='Platform validation',app=dict(slug='github-actions'),status='completed',conclusion='success')])
        github.get=Mock(side_effect=[pull,checks]);self.assertTrue(github.ready(change))
        github.main.return_value='c'*40
        with self.assertRaisesRegex(Hold,'Main advanced'):github.ready(change)
        github.main.return_value='a'*40;github.get=Mock(return_value={**pull,'head':dict(sha='c'*40)})
        with self.assertRaises(Hold):github.ready(change)

    def test_missing_or_non_strict_main_protection_refuses_automation(self):
        github=GitHub('synthetic');github.get=Mock(side_effect=ApiError(404))
        with self.assertRaises(Hold):github.protection(REPO)
        github.get=Mock(return_value=dict(required_status_checks=dict(strict=False,contexts=['Platform validation']),enforce_admins=dict(enabled=True)))
        with self.assertRaises(Hold):github.protection(REPO)

    def test_aws_credentials_use_refreshable_instance_role_and_cached_secrets(self):
        session=Mock();session.get_credentials.return_value.method='iam-role';session.client.return_value.get_secret_value.return_value={'SecretString':'private-fixture'}
        with patch.dict(os.environ,{},clear=True):credentials=Credentials(session)
        arn='arn:aws:secretsmanager:us-east-1:395567831870:secret:jumpserve/test-fixture'
        self.assertEqual(credentials.secret(arn),'private-fixture');self.assertEqual(credentials.secret(arn),'private-fixture')
        session.client.return_value.get_secret_value.assert_called_once()
        with self.assertRaises(Hold):credentials.secret(arn.replace('395567831870','000000000000'))
        session.get_credentials.return_value.method='sso'
        with patch.dict(os.environ,{},clear=True), self.assertRaises(Hold):Credentials(session)
        for variable in ('AWS_ACCESS_KEY_ID','AWS_SESSION_TOKEN','AWS_PROFILE'):
            with patch.dict(os.environ,{variable:'synthetic'},clear=True), self.assertRaises(Hold):Credentials(session)

    def test_dedicated_runtime_qualifications_are_preserved(self):
        self.assertEqual(plan(REPO,['app/page.tsx']),'frontend')
        for repo,paths in [('jumpserve-networks/jumpserve-back-end',['research_workflow/autonomy.py']),('jumpserve-networks/jumpserve-infra',['assets/openclaw/install.sh'])]:
            with self.assertRaises(Hold):plan(repo,paths)

if __name__=='__main__':unittest.main()
