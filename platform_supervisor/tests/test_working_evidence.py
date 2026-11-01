"""Actual local ZIP delivery, task boundaries and closure without inference."""
import base64,copy,io,json,tempfile,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import Mock,patch
from platform_supervisor import working_evidence as evidence,source,repair
from platform_supervisor.common import canonical,digest,Hold
from platform_supervisor.worker import Controller
from platform_supervisor.tests.test_repair_handoff import Store as RunStore,REPO

class Store:
 def __init__(self):
  self.task=dict(id=str(uuid.uuid4()),repositories=[REPO],assessment_repair=True);self.data={};self.records=[];self.requests=[]
 def add(self,name,raw):
  h=digest(raw);row=dict(id=str(uuid.uuid4()),task_id=self.task['id'],name=name,sha256=h,byte_count=len(raw),storage_path=self.task['id']+'/'+h)
  self.data[row['storage_path']]=raw;self.records.append(row);return dict(path=row['storage_path'],sha256=h)
 def rows(self,table,**filters):
  assert table=='artifacts' and filters['limit']==2
  return [r for r in self.records if r['sha256']==filters['sha256'][3:] and r['name']==filters['name'][3:]]
 def request(self,path,method='GET',body=None,raw=False):
  assert method=='GET' and body is None and raw
  prefix='/storage/v1/object/platform-supervisor-private/';assert path.startswith(prefix+self.task['id']+'/')
  self.requests.append(path);return self.data[path.removeprefix(prefix)]

class WorkingEvidence(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=Store()
  self.raw=source.checkpoint({REPO:dict(version='platform-working-v1',files=[dict(path='code.py',mode='100644',content=base64.b64encode(b'original source\n').decode())])},{'progress.json':base64.b64encode(b'{"ready":false}').decode()})
  self.pointer=self.store.add('working-checkpoint.zip',self.raw)
  self.plan=dict(version='platform-repair-source-refresh-v1',repository=REPO,next=dict(revision='b'*40),original_working_artifact=self.pointer)
  self.plan_pointer=self.store.add('repair-source-refresh-plan.json',canonical(self.plan))
  self.report=dict(version=self.plan['version'],repository=REPO,plan_sha256=self.plan_pointer['sha256'],original_working_artifact=self.pointer,conflicts=[])
  (self.root/'rebase-conflicts.json').write_bytes(canonical(self.report))
  self.controller=Mock(store=self.store,state=dict(sources={REPO:dict(revision='b'*40,snapshot={'frozen':True})},working_evidence_contract=evidence.VERSION))
 def test_report_has_a_real_hash_verified_local_original_without_changing_source_or_authority(self):
  before=copy.deepcopy(self.controller.state);evidence.deliver(self.store,self.root,self.controller.state)
  value=json.loads((self.root/'rebase-conflicts.json').read_bytes());local=value.pop('original_working_checkpoint')
  self.assertEqual(value,self.report);self.assertEqual((self.root/local['path']).read_bytes(),self.raw)
  self.assertEqual(local['sha256'],digest(self.raw));self.assertEqual(local['bytes'],len(self.raw));self.assertEqual({k:v for k,v in self.controller.state.items() if k!='working_evidence_seen'},before)
  index=json.loads((self.root/'references/working-checkpoints/index.json').read_bytes());self.assertTrue(index['complete'])
  evidence.deliver(self.store,self.root,self.controller.state);self.assertEqual((self.root/local['path']).read_bytes(),self.raw)
 def test_untrusted_report_cannot_select_another_tasks_checkpoint_or_override_the_server_plan(self):
  self.report['original_working_artifact']=dict(path=str(uuid.uuid4())+'/'+digest(self.raw),sha256=digest(self.raw))
  (self.root/'rebase-conflicts.json').write_bytes(canonical(self.report))
  with self.assertRaises(Hold):evidence.deliver(self.store,self.root,self.controller.state)
  self.assertTrue(all(self.store.task['id'] in p for p in self.store.requests))
 def test_missing_changed_oversized_or_ambiguous_server_originals_fail_before_coding(self):
  for defect in ('missing','hash','size','duplicate','wrong-task'):
   with self.subTest(defect=defect):
    records=copy.deepcopy(self.store.records);data=copy.deepcopy(self.store.data)
    if defect=='missing':self.store.records=self.store.records[1:]
    elif defect=='hash':self.store.data[self.pointer['path']]+=b'changed'
    elif defect=='size':self.store.records[0]['byte_count']=5000001
    elif defect=='duplicate':self.store.records.append(copy.deepcopy(self.store.records[0]))
    else:self.store.records[0]['task_id']=str(uuid.uuid4())
    with self.assertRaises(Hold):evidence.deliver(self.store,self.root,self.controller.state)
    self.store.records,self.store.data=records,data
 def test_delivered_original_and_symlink_targets_cannot_be_silently_replaced(self):
  evidence.deliver(self.store,self.root,self.controller.state);local=self.root/'references/working-checkpoints'/ (digest(self.raw)+'.zip');local.write_bytes(b'changed')
  with self.assertRaises(Hold):evidence.deliver(self.store,self.root,self.controller.state)
  local.unlink();target=self.root/'target';target.write_bytes(b'unchanged');local.symlink_to(target)
  with self.assertRaises(Hold):evidence.deliver(self.store,self.root,self.controller.state)
  self.assertEqual(target.read_bytes(),b'unchanged')
 def test_unsafe_archive_members_are_never_delivered_or_executed(self):
  for name in ('../outside','/absolute','unknown.json'):
   with self.subTest(name=name):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('work-packet.json','{}');z.writestr(name,'untrusted')
    with self.assertRaises(Hold):evidence.members(stream.getvalue(),[REPO])
 def test_server_capability_is_required_and_wrong_versions_do_not_enter_delivery(self):
  self.controller.state.pop('working_evidence_contract');evidence.deliver(self.store,self.root,self.controller.state);self.assertEqual(self.store.requests,[])
 def test_verified_original_opens_one_retry_but_deleted_local_files_cannot_rearm_it(self):
  handoff=RunStore(self.root);handoff.task['id']=self.store.task['id'];handoff.packet['platform_task_id']=handoff.task['id'];handoff.packet['working_checkpoint_delivery']=evidence.VERSION
  original_request=handoff.request
  handoff.request=lambda path,*a,**kw:original_request(path,*a,**kw) if path=='/rest/v1/rpc/platform_repair_context' else self.store.request(path,*a,**kw)
  handoff.rows=self.store.rows
  state=dict(self.controller.state,repair_guard=dict(version=repair.VERSION,evidence_revision='a'*64,waiting=True,unchanged_slices=2))
  progress=canonical(dict(version='platform-progress-v1',ready=False,status='blocked',remaining_blocker='Need the original checkpoint'))
  (self.root/'progress.json').write_bytes(progress)
  repair.deliver(handoff,self.root,state);self.assertFalse(state['repair_guard']['waiting']);self.assertFalse((self.root/'progress.json').exists())
  (self.root/'progress.json').write_bytes(progress)
  with self.assertRaises(Hold):repair.observe(self.root,state,dict(ready=False),None)
  seen=copy.deepcopy(state['working_evidence_seen'])
  (self.root/'references/working-checkpoints'/ (digest(self.raw)+'.zip')).unlink()
  (self.root/'rebase-conflicts.json').write_bytes(canonical(self.report))
  with self.assertRaisesRegex(Hold,'unchanged'):repair.deliver(handoff,self.root,state)
  self.assertTrue(state['repair_guard']['waiting']);self.assertEqual(state['working_evidence_seen'],seen);self.assertEqual(handoff.runs,[])

class IncidentClosureBoundary(unittest.TestCase):
 def test_server_closure_completes_without_another_model_source_or_release_action(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=RunStore(Path(tmp));original=store.rpc
   closure=dict(version='platform-incident-closure-v1',platform_task_id=store.task['id'],qualified_platform_change=False,scientific_completion=False)
   def rpc(op,payload=None):
    value=original(op,payload)
    return dict(value,incident_closure=closure) if op=='lease' else value
   store.rpc=rpc;github=Mock();controller=Controller(store,github,'unchanged-image')
   with patch('platform_supervisor.worker.native.containers',return_value=[]),patch('platform_supervisor.worker.native.reconcile'),patch('platform_supervisor.worker.native.execute') as model:
    controller.run()
   self.assertEqual(store.stages[-1][0],'completed');self.assertEqual(controller.state['incident_closure'],closure)
   model.assert_not_called();github.archive.assert_not_called();github.publish.assert_not_called();github.merge.assert_not_called();self.assertEqual(store.runs,[])
 def test_pause_cancellation_and_closure_binding_keep_their_original_fences(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=RunStore(Path(tmp));controller=Controller(store,Mock(),'unchanged-image');flags=store.rpc('lease')
   for extra,status in [(dict(paused=True),'implementing'),(dict(cancel_requested=True),'cancelled'),(dict(incident_closure=dict(version='platform-incident-closure-v1',platform_task_id=str(uuid.uuid4()))),'reconciling')]:
    store.rpc=Mock(return_value=dict(flags,**extra))
    with self.assertRaises(Hold) as error:controller.boundary()
    self.assertEqual(error.exception.status,status)

if __name__=='__main__':unittest.main()
