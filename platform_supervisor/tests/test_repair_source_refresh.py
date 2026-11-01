"""Exact-main refresh preserves work and resumes a partially completed swap."""
import copy,io,json,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from platform_supervisor import source,source_refresh
from platform_supervisor.common import Hold
from platform_supervisor.worker import Controller
from platform_supervisor.tests.test_repair_handoff import Store,REPO
from platform_supervisor.tests.test_recovery_contract import contract


def archive(files):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as tar:
        for name,value in files.items():
            info=tarfile.TarInfo('root/'+name);raw=value.encode();info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
    return stream.getvalue()


class Persistence(Store):
    def __init__(self,root):super().__init__(root);self.originals={}
    def artifact(self,name,raw):
        item=super().artifact(name,raw);self.originals[item['storage_path']]=raw;return item
    def request(self,path,*args,**kwargs):
        prefix='/storage/v1/object/platform-supervisor-private/'
        if path.startswith(prefix):return self.originals[path.removeprefix(prefix)]
        return super().request(path,*args,**kwargs)


class RepairSourceRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=Persistence(self.root);self.github=Mock();self.github.main.return_value='b'*40
        self.old={'code.py':'old\n','independent.py':'old independent\n','removed.py':'old removed\n','tests/test_existing.py':'original check\n'}
        self.new=dict(self.old,**{'code.py':'landed recovery adapter\n','new-contract.json':'{}\n'})
        self.github.archive.side_effect=lambda repo,revision:archive(self.old if revision=='a'*40 else self.new)
        self.workspace=self.root/self.store.task['id']/'workspace';self.workspace.mkdir(parents=True)
        baseline=self.workspace.parent/'baseline'/'jumpserve-back-end';baseline.mkdir(parents=True)
        for directory in (self.workspace/'jumpserve-back-end',baseline):source.extract(archive(self.old),directory)
        self.controller=Controller(self.store,self.github,'unchanged-image');self.controller.state=dict(sources={REPO:dict(revision='a'*40,snapshot=source.snapshot(baseline))},repair_contract=dict(version='platform-repair-recovery-contract-v1'),repair_guard=dict(waiting=False,unchanged_slices=1))
        self.before_task=copy.deepcopy(self.store.task)

    def test_current_main_is_visible_and_nonoverlapping_edits_modes_and_deletions_survive(self):
        local=self.workspace/'jumpserve-back-end';(local/'independent.py').write_bytes(b'');(local/'independent.py').chmod(0o755);(local/'removed.py').unlink()
        (self.workspace/'proposal.json').write_text('{"ready":true}')
        source_refresh.refresh(self.controller,self.workspace)
        self.assertEqual((local/'code.py').read_text(),'landed recovery adapter\n');self.assertTrue((local/'new-contract.json').exists())
        self.assertEqual((local/'independent.py').read_bytes(),b'');self.assertTrue((local/'independent.py').stat().st_mode&0o111)
        self.assertFalse((local/'removed.py').exists());self.assertFalse((self.workspace/'proposal.json').exists())
        self.assertEqual(self.controller.state['sources'][REPO]['revision'],'b'*40)
        self.assertEqual(self.controller.state['repair_guard'],dict(waiting=False,unchanged_slices=1))
        self.assertEqual(self.store.task['deadline_at'],self.before_task['deadline_at']);self.assertEqual(self.store.runs,[])
        # The exact fresh baseline supports the existing protected-file and
        # manifest checks; independent candidate changes remain a fresh patch.
        patch=source.freeze(self.controller.state['sources'][REPO]['snapshot'],local)
        self.assertEqual({f['path'] for f in patch['files']},{'independent.py','removed.py'})

    def test_overlaps_and_protected_edits_are_retained_without_overwriting_main(self):
        local=self.workspace/'jumpserve-back-end';(local/'code.py').write_text('overlapping proposal\n');(local/'tests/test_existing.py').write_text('weakened proposal\n')
        source_refresh.refresh(self.controller,self.workspace)
        report=json.loads((self.workspace/'rebase-conflicts.json').read_bytes())
        self.assertEqual({v['path'] for v in report['conflicts']},{'code.py','tests/test_existing.py'})
        self.assertEqual((local/'code.py').read_text(),self.new['code.py']);self.assertEqual((local/'tests/test_existing.py').read_text(),self.old['tests/test_existing.py'])
        for item in report['conflicts']:self.assertIn('proposal',(self.workspace/item['previous_candidate']).read_text())
        self.assertIn(report['original_working_artifact']['path'],self.store.originals)

    def test_overlapping_deletion_does_not_delete_concurrent_main_change(self):
        local=self.workspace/'jumpserve-back-end';(local/'code.py').unlink()
        source_refresh.refresh(self.controller,self.workspace)
        report=json.loads((self.workspace/'rebase-conflicts.json').read_bytes())
        self.assertEqual(report['conflicts'],[dict(repository=REPO,path='code.py',proposed_deletion=True)])
        self.assertEqual((local/'code.py').read_text(),self.new['code.py'])

    def test_real_controller_refreshes_before_the_first_coding_call(self):
        self.store.packet.update(recovery_contract=contract(),recovery_contract_sha256='c'*64)
        self.store.task['checkpoint']=copy.deepcopy(self.controller.state)
        def coding(store,workspace,*args):
            self.assertEqual((workspace/'jumpserve-back-end/code.py').read_text(),self.new['code.py'])
            self.assertEqual(store.task['checkpoint']['sources'][REPO]['revision'],'b'*40)
            self.assertTrue((workspace/'references/assessment-repair/recovery-contract.json').is_file())
            return dict(ready=False,feedback='Synthetic first slice; no provider transport.')
        with patch('platform_supervisor.worker.native.containers',return_value=[]),patch('platform_supervisor.worker.native.reconcile'),patch('platform_supervisor.worker.documentation.deliver'),patch('platform_supervisor.worker.checks.prepare_dependencies',return_value=None),patch('platform_supervisor.worker.native.execute',side_effect=coding) as model:
            self.controller.run()
        self.assertEqual(model.call_count,1,self.store.stages)
        self.assertEqual(self.store.stages[-1][0],'implementing')
        self.assertEqual(self.store.task['deadline_at'],self.before_task['deadline_at'])
        self.github.publish.assert_not_called();self.github.merge.assert_not_called()

    def test_restart_completes_the_same_plan_after_candidate_swap(self):
        replace=source_refresh.replace;calls=0
        def crash(*args):
            nonlocal calls
            replace(*args);calls+=1
            if calls==1:raise RuntimeError('simulated process exit after atomic candidate rename')
        with patch.object(source_refresh,'replace',side_effect=crash),self.assertRaisesRegex(RuntimeError,'simulated'):
            source_refresh.refresh(self.controller,self.workspace)
        pointer=copy.deepcopy(self.store.task['checkpoint']['source_refresh_pending'])
        resumed=Controller(self.store,self.github,'unchanged-image');resumed.state=copy.deepcopy(self.store.task['checkpoint'])
        self.github.main.reset_mock();source_refresh.refresh(resumed,self.workspace,resume_only=True)
        self.assertNotIn('source_refresh_pending',resumed.state);self.github.main.assert_not_called()
        self.assertEqual(resumed.state['sources'][REPO]['revision'],'b'*40)
        self.assertEqual((self.workspace/'jumpserve-back-end/code.py').read_text(),self.new['code.py'])
        self.assertTrue((self.workspace.parent/'source-refresh'/pointer['sha256']/'old-candidate').is_dir())

    def test_replacement_host_reconstructs_pending_plan_from_durable_original_checkpoint(self):
        with patch.object(source_refresh,'finish',side_effect=RuntimeError('before first swap')),self.assertRaises(RuntimeError):
            source_refresh.refresh(self.controller,self.workspace)
        resumed=Controller(self.store,self.github,'unchanged-image');resumed.state=copy.deepcopy(self.store.task['checkpoint'])
        fresh_workspace=self.root/'replacement-host'/'workspace';fresh_workspace.mkdir(parents=True)
        resumed.originals(fresh_workspace);resumed.restore_work(fresh_workspace)
        source_refresh.refresh(resumed,fresh_workspace,resume_only=True)
        self.assertEqual((fresh_workspace/'jumpserve-back-end/new-contract.json').read_text(),'{}\n')
        self.assertNotIn('source_refresh_pending',resumed.state)

    def test_changed_pending_source_stops_before_another_swap(self):
        with patch.object(source_refresh,'finish',side_effect=RuntimeError('before swap')),self.assertRaises(RuntimeError):
            source_refresh.refresh(self.controller,self.workspace)
        (self.workspace/'jumpserve-back-end/code.py').write_text('unexpected concurrent edit')
        with self.assertRaisesRegex(Hold,'changed before refresh'):
            source_refresh.refresh(self.controller,self.workspace,resume_only=True)
        self.assertEqual(self.controller.state['sources'][REPO]['revision'],'a'*40)

    def test_legacy_and_already_qualified_candidates_do_not_enter_pre_coding_refresh(self):
        for state in ({},{'tested':True}):
            self.controller.state.update(state)
            if not state:self.controller.state.pop('repair_contract')
            source_refresh.refresh(self.controller,self.workspace)
        self.github.main.assert_not_called()


if __name__=='__main__':unittest.main()
