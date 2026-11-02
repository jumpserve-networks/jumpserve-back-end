"""Passive predecessor delivery, never replay or inherited execution authority."""
import copy,io,json,tempfile,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import Mock
from platform_supervisor import continuations,source
from platform_supervisor.common import canonical,digest,Hold

class ContinuationDelivery(unittest.TestCase):
 def fixture(self):
  store=Mock();store.task=dict(id=str(uuid.uuid4()),repositories=['jumpserve-networks/jumpserve-back-end']);previous=str(uuid.uuid4())
  raw=source.checkpoint({'jumpserve-networks/jumpserve-back-end':dict(files=[dict(path='worker.py',content=None)])},{'progress.json':'untrusted passive predecessor data'})
  store.request.return_value=raw
  proof=dict(version=continuations.VERSION,platform_task_id=store.task['id'],assessment_id='assessment',previous_task_id=previous,original_task_modified=False,uncertain_call_replay=False,
   predecessor_checkpoint=dict(task_id=previous,sha256=digest(raw),byte_count=len(raw),storage_path=previous+'/'+digest(raw)))
  return store,dict(continuation_contract=continuations.VERSION,assessment_id='assessment',incident_continuation=proof),raw
 def test_followup_receives_exact_passive_source_with_bound_predecessor_and_no_execution(self):
  store,packet,raw=self.fixture();original=copy.deepcopy(packet)
  with tempfile.TemporaryDirectory() as tmp:
   value=continuations.deliver(store,Path(tmp),packet)
   self.assertEqual((Path(tmp)/value['checkpoint']['path']).read_bytes(),raw)
   self.assertEqual(json.loads((Path(tmp)/'references/incident-continuation/index.json').read_bytes())['history'],original['incident_continuation'])
  self.assertEqual(packet,original);store.rpc.assert_not_called();self.assertEqual(store.request.call_count,1)
 def test_absent_checkpoint_is_honest_and_not_fabricated(self):
  store,packet,_=self.fixture();packet['incident_continuation']['predecessor_checkpoint']=None
  with tempfile.TemporaryDirectory() as tmp:self.assertIsNone(continuations.deliver(store,Path(tmp),packet)['checkpoint'])
  store.request.assert_not_called()
 def test_scope_hash_size_and_changed_originals_remain_fenced(self):
  for field,value in [('task_id',str(uuid.uuid4())),('sha256','0'*64),('byte_count',5000001),('storage_path','another-task/path')]:
   store,packet,_=self.fixture();packet['incident_continuation']['predecessor_checkpoint'][field]=value
   with tempfile.TemporaryDirectory() as tmp,self.assertRaises(Hold):continuations.deliver(store,Path(tmp),packet)
 def test_missing_contract_or_other_attempt_cannot_supply_a_checkpoint(self):
  for change in ('contract','task','replay'):
   store,packet,_=self.fixture()
   if change=='contract':packet['continuation_contract']='model-supplied-approval'
   elif change=='task':packet['incident_continuation']['platform_task_id']=str(uuid.uuid4())
   else:packet['incident_continuation']['uncertain_call_replay']=True
   with tempfile.TemporaryDirectory() as tmp,self.assertRaises(Hold):continuations.deliver(store,Path(tmp),packet)
   store.request.assert_not_called()
 def test_unsafe_archive_member_is_not_written_or_extracted(self):
  store,packet,_=self.fixture();stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:z.writestr('../outside.py','untrusted');z.writestr('work-packet.json','{}')
  raw=stream.getvalue();store.request.return_value=raw;ref=packet['incident_continuation']['predecessor_checkpoint'];ref.update(sha256=digest(raw),byte_count=len(raw),storage_path=ref['task_id']+'/'+digest(raw))
  with tempfile.TemporaryDirectory() as tmp,self.assertRaises(Hold):continuations.deliver(store,Path(tmp),packet)

if __name__=='__main__':unittest.main()
