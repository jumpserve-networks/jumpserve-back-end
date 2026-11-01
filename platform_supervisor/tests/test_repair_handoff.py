"""Credential-free incident handoff, blocked-call and restart regressions."""
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
import urllib.error
import urllib.request
import uuid
import zipfile

from platform_supervisor import native,repair,source
from platform_supervisor.worker import Controller
from platform_supervisor.store import Store as Persistence
from platform_supervisor.common import canonical,digest,save,Hold

TASK='11111111-1111-4111-8111-111111111111'
STUDY='22222222-2222-4222-8222-222222222222'
REPO='jumpserve-networks/jumpserve-back-end'


def packet():
    return dict(version=repair.VERSION,platform_task_id=TASK,assessment_id=STUDY,assessment_task_id=str(uuid.uuid4()),evidence_revision='a'*64,
        task=dict(id=str(uuid.uuid4()),status='blocked',attempts=172,reason='Original failure',references={}),
        workflow=dict(status='running',deadline_at='2099-01-01T00:00:00Z',max_spend_cents=None,committed_cents=0),
        blocker=dict(source_kind='repair',source_id=str(uuid.uuid4())),coverage=dict(events_total=0),artifacts=[],
        private=dict(lease_token='never-deliver-token',email='person@example.invalid',url='https://private.example.invalid/token?secret=value'))


class Store:
    def __init__(self,root):
        self.spool=root;self.worker=str(uuid.uuid4());self.packet=packet();self.artifacts={};self.runs=[];self.stages=[];self.events=[];self.saved_checkpoint={}
        self.task=dict(id=TASK,lease_token=str(uuid.uuid4()),assessment_repair=True,checkpoint={},repositories=[REPO],objective='Synthetic incident',deadline_at='2099-01-01T00:00:00Z',max_reserved_cents=-1)
    def artifact(self,name,raw):
        ident=digest(raw);item=dict(id=str(uuid.uuid5(uuid.UUID(TASK),name+ident)),name=name,sha256=ident,storage_path=TASK+'/'+ident)
        self.artifacts[name]=(item,raw);return item
    def event(self,*args):self.events.append(args)
    def rpc(self,operation,payload=None):
        if operation=='lease':return dict(paused=False,cancel_requested=False,automatic_releases=True,deadline_at=self.task['deadline_at'],committed_cents=17200,max_reserved_cents=-1,model_calls=172,max_model_calls=-1)
        if operation=='progress':self.saved_checkpoint=copy.deepcopy(payload['checkpoint']);self.task['checkpoint']=self.saved_checkpoint;return {}
        if operation=='begin-run':self.runs.append(dict(payload,status='running'));return dict(id=payload['id'])
        if operation=='finish-run':self.runs[-1].update(payload);return {}
        raise AssertionError(operation)
    def rows(self,kind,**kwargs):return self.runs if kind=='runs' else []
    def checkpoint(self,status,reason,state):
        self.stages.append((status,reason));self.saved_checkpoint=copy.deepcopy(state);self.task['checkpoint']=self.saved_checkpoint
    def request(self,path,method='GET',body=None,raw=False):
        if path=='/rest/v1/rpc/platform_repair_context':return copy.deepcopy(self.packet)
        return self.research[path]


class RepairHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=Store(self.root)

    def test_original_context_is_retained_and_model_delivery_redacts_private_values(self):
        state={};original=canonical(self.store.packet)
        self.assertEqual(repair.deliver(self.store,self.root,state),'a'*64)
        self.assertEqual(self.store.artifacts['assessment-repair-context.json'][1],original)
        view=(self.root/'references/assessment-repair/context.json').read_bytes()
        for private in (b'never-deliver-token',b'person@example.invalid',b'https://private.example.invalid'):self.assertNotIn(private,view)
        self.assertEqual(json.loads(view)['workflow']['committed_cents'],0)
        self.assertIsNone(json.loads(view)['workflow']['max_spend_cents'])
        self.assertFalse(state['repair_guard']['waiting'])
        self.assertIn('assessment-repair-delivery.json',self.store.artifacts)

    def test_scoped_original_artifact_hashes_and_safe_checkpoint_source_projection(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('project/experiment.py','def original(value): return value\n')
            z.writestr('private-file.txt','private content is not delivered')
        raw=stream.getvalue();sha=digest(raw);path=STUDY+'/'+sha
        self.store.packet['artifacts']=[dict(id=str(uuid.uuid4()),sha256=sha,byte_count=len(raw),storage_path=path,provenance=dict(filename='checkpoint.zip'))]
        self.store.research={'/storage/v1/object/research-workflow-raw/'+path:raw}
        repair.deliver(self.store,self.root,{})
        view=(self.root/'references/assessment-repair/artifacts'/ (sha+'.json')).read_bytes()
        self.assertIn(b'def original(value)',view);self.assertNotIn(b'private content',view)
        self.store.research['/storage/v1/object/research-workflow-raw/'+path]=raw+b'changed'
        with self.assertRaisesRegex(Hold,'hash differs'):repair.deliver(self.store,self.root,{})
        self.store.packet['artifacts'][0]['storage_path']='different-study/'+sha
        with self.assertRaisesRegex(Hold,'outside this assessment'):repair.deliver(self.store,self.root,{})

    def test_logs_and_notes_do_not_count_as_implementation_or_reset_waiting(self):
        repository=self.root/'jumpserve-back-end';repository.mkdir();(repository/'worker.py').write_text('print(1)\n')
        sources={REPO:dict(snapshot=source.snapshot(repository))};state=dict(sources=sources)
        repair.deliver(self.store,self.root,state);before=repair.implementation(self.root,sources)
        for iteration in range(2):
            (repository/('check-'+str(iteration)+'.log')).write_text('passed again')
            (repository/'investigation.md').write_text(str(iteration))
            if iteration==0:repair.observe(self.root,state,dict(ready=False),before)
            else:
                with self.assertRaisesRegex(Hold,'no implementation progress'):repair.observe(self.root,state,dict(ready=False),before)
        saved=copy.deepcopy(state)
        with self.assertRaisesRegex(Hold,'unchanged'):repair.deliver(self.store,self.root,state)
        self.assertEqual(state,saved)
        self.store.packet['evidence_revision']='b'*64
        repair.deliver(self.store,self.root,state)
        self.assertEqual(state['repair_guard']['unchanged_slices'],0);self.assertFalse(state['repair_guard']['waiting'])
        (repository/'worker.py').write_text('print(2)\n');self.assertNotEqual(repair.implementation(self.root,sources),before)
        repair.observe(self.root,state,dict(ready=False),before);self.assertEqual(state['repair_guard']['unchanged_slices'],0)

    def test_semantic_protocol_upgrade_does_not_reset_blocked_progress_or_admit_a_retry(self):
        state=dict(repair_guard=dict(version=repair.VERSION,evidence_revision='old-original-token',waiting=True,unchanged_slices=2,
            semantic_baseline=dict(version=repair.SEMANTIC_VERSION,evidence_revision='a'*64)))
        self.store.packet['evidence_revision_protocol']=repair.SEMANTIC_VERSION
        original=canonical(dict(version='platform-progress-v1',ready=False,status='blocked',remaining_blocker='Original queue still unresolved.'))
        save(self.root/'progress.json',original);before=copy.deepcopy(state)
        with self.assertRaisesRegex(Hold,'unchanged'):repair.deliver(self.store,self.root,state)
        self.assertEqual(state,before);self.assertEqual((self.root/'progress.json').read_bytes(),original)
        self.store.packet['evidence_revision']='b'*64;repair.deliver(self.store,self.root,state)
        self.assertFalse(state['repair_guard']['waiting']);self.assertNotIn('semantic_baseline',state['repair_guard'])
        self.assertEqual(next((self.root/'previous-candidates').iterdir()).read_bytes(),original)

    def test_blocked_progress_is_conservative_and_preserved_when_new_evidence_arrives(self):
        state={};repair.deliver(self.store,self.root,state)
        raw=canonical(dict(version='platform-progress-v1',ready=False,status='blocked',remaining_blocker='Need the retained diagnostic.'))
        save(self.root/'progress.json',raw)
        with self.assertRaisesRegex(Hold,'Repair blocked'):repair.observe(self.root,state,dict(ready=False),None)
        self.assertTrue(state['repair_guard']['waiting'])
        self.store.packet['evidence_revision']='b'*64;repair.deliver(self.store,self.root,state)
        self.assertFalse((self.root/'progress.json').exists())
        self.assertEqual(next((self.root/'previous-candidates').iterdir()).read_bytes(),raw)
        unknown=dict(status='error',error=dict(kind='incomplete_turn'))
        save(self.root/'progress.json',raw)
        self.assertTrue(native.readiness(self.root,unknown,['blocked-progress'],True)['blocked'])
        self.assertNotIn('blocked',native.readiness(self.root,unknown,['blocked-progress'],False))

    def test_progress_input_cannot_follow_links_or_force_a_resume(self):
        progress=self.root/'progress.json'
        for value in (b'{broken',b'[]',canonical(dict(version='platform-progress-v1',ready=True,status='blocked'))):
            save(progress,value);self.assertIsNone(repair.blocked_progress(self.root))
        save(progress,b' '*32001)
        with self.assertRaisesRegex(Hold,'bounded regular file'):repair.blocked_progress(self.root)
        progress.unlink();target=self.root/'original.json';target.write_text('{}');progress.symlink_to(target)
        with self.assertRaisesRegex(Hold,'bounded regular file'):repair.blocked_progress(self.root)
        self.assertEqual(target.read_text(),'{}')

    def test_persistence_research_extension_is_fixed_origin_and_read_only(self):
        store=Persistence('synthetic-key',self.root,str(uuid.uuid4()))
        path='/storage/v1/object/research-workflow-raw/'+STUDY+'/'+'a'*64
        store.opener=Mock();store.opener.open.return_value=io.BytesIO(b'original diagnostic')
        self.assertEqual(store.request(path,raw=True),b'original diagnostic')
        self.assertEqual(store.opener.open.call_args.args[0].full_url,'https://regphejnlvfpyokpniny.supabase.co'+path)
        for candidate,method,raw in ((path,'POST',True),(path,'GET',False),(path+'?download=1','GET',True),
            (path+'/../other','GET',True),('https://other.invalid'+path,'GET',True),(path.replace('research-workflow-raw','research-paper-uploads'),'GET',True)):
            with self.assertRaisesRegex(ValueError,'Unexpected platform persistence path'):store.request(candidate,method,raw=raw)
        self.assertEqual(store.opener.open.call_count,1)

    def test_task_binding_and_unsafe_checkpoint_stop_before_model_delivery(self):
        self.store.packet['platform_task_id']=str(uuid.uuid4())
        with self.assertRaisesRegex(Hold,'binding differs'):repair.deliver(self.store,self.root,{})
        self.assertEqual(self.store.artifacts,{})
        self.store.packet=None
        with self.assertRaisesRegex(Hold,'evidence is unavailable'):repair.deliver(self.store,self.root,{})
        self.store.task['assessment_repair']=False
        self.assertIsNone(repair.deliver(self.store,self.root,{}))
        for name in ('../outside.py','/absolute.py','project/../experiment.py','project\\experiment.py'):
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:z.writestr(name,'original untrusted bytes')
            with self.assertRaisesRegex(Hold,'Unsafe original research checkpoint'):repair._artifact_view(stream.getvalue(),'checkpoint.zip')

    def test_real_bridge_does_not_reserve_or_transport_another_call_after_blocked_progress(self):
        workspace=self.root/'workspace';workspace.mkdir();store=self.store
        response=io.BytesIO(b'original synthetic terminal bytes');response.status=200
        transport=Mock(return_value=response)
        def stream(source,sink,*args):
            sink.write(source.read());return dict(output=[],usage={})
        def cli(command,**kwargs):
            configuration=json.loads(Path(command[command.index('--config')+1]).read_bytes());provider=configuration['models']['providers']['lab']
            def post(number):
                request=dict(model=native.POLICY['model'],tools=[dict(type='function',name=n) for n in native.builder.TOOLS],input=[dict(type='message',role='user',content=str(number))])
                return urllib.request.urlopen(urllib.request.Request(provider['baseUrl']+'/responses',data=canonical(request),headers={'Authorization':'Bearer '+provider['apiKey']}),timeout=5)
            with post(1) as result:self.assertEqual(result.status,200)
            save(workspace/'progress.json',canonical(dict(version='platform-progress-v1',ready=False,status='blocked',remaining_blocker='Need original source evidence.')))
            with self.assertRaises(urllib.error.HTTPError):post(2)
            return subprocess.CompletedProcess(command,2,canonical(dict(status='error',error=dict(kind='incomplete_turn'))),b'')
        with patch.object(native.runtime,'verify_runtime'),patch.object(native.builder,'builder_package',return_value=Path('/fixed-package')),patch.object(native.subprocess,'run',side_effect=cli),patch.object(native,'containers',return_value=[]),patch.object(native,'normalize'),patch.object(native.wire,'read_stream',side_effect=stream):
            result=native.execute(store,workspace,self.root/'slice','sha256:'+'a'*64,token_source=lambda:'synthetic-token',transport=transport)
        self.assertTrue(result['blocked']);self.assertEqual(len(store.runs),1);self.assertEqual(store.runs[0]['status'],'complete');self.assertEqual(transport.call_count,1)

    def test_controller_restart_keeps_blocked_attempt_quiet_until_new_evidence(self):
        store=self.store;github=Mock()
        def originals(controller,workspace):
            root=workspace/'jumpserve-back-end';root.mkdir(exist_ok=True)
            if not (root/'worker.py').exists():(root/'worker.py').write_text('print(1)\n')
            controller.state.setdefault('sources',{REPO:dict(snapshot=source.snapshot(root),revision='a'*40)})
        def coding(store,workspace,*args):
            save(workspace/'progress.json',canonical(dict(version='platform-progress-v1',ready=False,status='blocked',remaining_blocker='Original diagnostic is missing.')))
            return dict(ready=False,feedback='blocked')
        before=copy.deepcopy(store.task)
        with patch.object(Controller,'originals',originals),patch('platform_supervisor.worker.native.containers',return_value=[]),patch('platform_supervisor.worker.native.reconcile'),patch('platform_supervisor.worker.research_runtime.reviewed'),patch('platform_supervisor.worker.documentation.deliver'),patch('platform_supervisor.worker.checks.prepare_dependencies',return_value=None),patch('platform_supervisor.worker.native.execute',side_effect=coding) as model:
            Controller(store,github,'image').run();self.assertEqual(store.stages[-1][0],'blocked')
            # New controller/process sees only the persisted checkpoint.
            Controller(store,github,'image').run();self.assertEqual(model.call_count,1)
            store.packet['evidence_revision']='b'*64
            Controller(store,github,'image').run();self.assertEqual(model.call_count,2)
        self.assertEqual(store.task['id'],before['id']);self.assertEqual(store.task['deadline_at'],before['deadline_at']);self.assertEqual(store.task['max_reserved_cents'],-1)
        github.publish.assert_not_called();github.merge.assert_not_called()


if __name__=='__main__':unittest.main()
