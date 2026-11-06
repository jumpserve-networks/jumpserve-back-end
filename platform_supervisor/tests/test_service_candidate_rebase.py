"""Rebased service candidates retain immutable earlier patches and publications."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import uuid
from platform_supervisor import service_work as service,source
from platform_supervisor.common import digest,identity,Hold


class ServiceCandidateRebase(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.directory=self.root/'jumpserve-infra';self.directory.mkdir();(self.directory/'base.py').write_text('base = 1\n')
  self.task='22222222-2222-4222-8222-222222222222';self.request=dict(id='11111111-1111-4111-8111-111111111111',profile='assessment-evidence-v1')
  self.records={};self.operations=[];self.artifacts=[]
  store=Mock();store.task=dict(id=self.task,repositories=[service.INFRA])
  def artifact(name,raw):
   self.artifacts.append((name,raw));return dict(id=str(uuid.uuid5(uuid.UUID(self.task),digest(raw))),sha256=digest(raw))
  store.artifact.side_effect=artifact
  store.rows.side_effect=lambda table,**filters:copy.deepcopy([r for r in self.records.values() if 'id' not in filters or r['id']==filters['id'].removeprefix('eq.')] if table=='changes' else self.operations)
  self.controller=SimpleNamespace(store=store,state=dict(sources={service.INFRA:dict(revision='a'*40,snapshot=source.snapshot(self.directory))},generation=0,committed_cents=321,deadline_at='retained'),save_state=Mock())
  def record(value):
   previous=self.records.get(value['id'])
   if previous and any(previous.get(k)!=value.get(k) for k in ('base_sha','tree_sha256','patch_artifact_id','checks_artifact_id')):
    raise ValueError('Change identity is immutable')
   self.records[value['id']]=copy.deepcopy(value)
  self.controller.record_change=Mock(side_effect=record)

 def retained_collision(self,published=True):
  legacy=identity(self.task,'service-manifest:'+self.request['id'])
  with patch.object(service,'candidate_id',return_value=legacy):service.prepare(self.controller,self.root,self.request)
  self.records[legacy].update(status='superseded',pr_number=7 if published else None)
  if published:self.operations=[dict(kind='publish',status='confirmed',request=dict(action='close-superseded',repository=service.INFRA,pr_number=7),result=dict(closed=True))]
  old=copy.deepcopy(self.records[legacy]);manifest=self.directory/'services'/f"{self.request['id']}.json";raw=manifest.read_bytes();manifest.unlink()
  (self.directory/'concurrent.py').write_text('concurrent = True\n')
  self.controller.state['sources'][service.INFRA]=dict(revision='b'*40,snapshot=source.snapshot(self.directory));manifest.write_bytes(raw)
  self.controller.state.update(tested=False,changes=[],generation=1)
  # Reproduce the old controller's checkpoint-before-record ordering exactly.
  with patch.object(service,'candidate_id',return_value=legacy),self.assertRaisesRegex(ValueError,'immutable'):
   service.prepare(self.controller,self.root,self.request)
  self.assertTrue(self.controller.state['tested'])
  return legacy,old

 def test_rebase_uses_a_new_identity_and_retains_the_original_patch(self):
  legacy,old=self.retained_collision();service.prepare(self.controller,self.root,self.request)
  current=self.controller.state['changes'][0]
  self.assertNotEqual(current['id'],legacy);self.assertEqual(current['base_sha'],'b'*40)
  self.assertEqual(self.records[legacy],old);self.assertEqual(len(self.records),2)
  self.assertEqual([x['path'] for x in current['patch']['files']],[f"services/{self.request['id']}.json"])
  self.assertEqual(self.controller.state['generation'],1);self.assertEqual(self.controller.state['committed_cents'],321)
  self.assertEqual(self.controller.state['deadline_at'],'retained')
  self.assertTrue(any(name=='superseded-service-candidate.json' for name,_ in self.artifacts))
  count=self.controller.record_change.call_count;service.prepare(self.controller,self.root,self.request)
  self.assertEqual(self.controller.record_change.call_count,count)

 def test_unpublished_superseded_candidate_can_resume_without_external_actions(self):
  legacy,old=self.retained_collision(published=False);service.prepare(self.controller,self.root,self.request)
  self.assertEqual(self.records[legacy],old);self.assertEqual(len(self.records),2)
  self.controller.store.rpc.assert_not_called()

 def test_unknown_or_unconfirmed_publication_cannot_be_replaced(self):
  for mode in ('unknown','missing-proof','merged'):
   with self.subTest(mode=mode):
    self.setUp();legacy,old=self.retained_collision()
    if mode=='unknown':self.operations[0]['status']='unknown'
    elif mode=='missing-proof':self.operations[0]['result']={}
    else:self.records[legacy]['merge_sha']='c'*40
    before=copy.deepcopy(self.controller.state)
    with self.assertRaises(Hold) as stopped:service.prepare(self.controller,self.root,self.request)
    self.assertEqual(stopped.exception.status,'reconciling');self.assertEqual(self.controller.state,before)
    self.assertEqual(len(self.records),1)

 def test_healthy_published_legacy_candidate_is_not_republished(self):
  legacy,old=self.retained_collision();self.controller.state['changes'][0].update(head_sha='c'*40,pr_number=7,status='published')
  before=copy.deepcopy(self.controller.state);service.prepare(self.controller,self.root,self.request)
  self.assertEqual(self.controller.state,before);self.assertEqual(self.records[legacy],old)

 def test_confirmed_supersession_with_retained_pr_fields_can_recover(self):
  legacy,old=self.retained_collision()
  self.controller.state['changes'][0].update(head_sha='c'*40,pr_number=7,status='superseded')
  service.prepare(self.controller,self.root,self.request)
  self.assertNotEqual(self.controller.state['changes'][0]['id'],legacy)
  self.assertEqual(self.records[legacy],old)

 def test_a_new_format_candidate_stopped_during_rebase_keeps_its_original(self):
  service.prepare(self.controller,self.root,self.request);old_id=self.controller.state['changes'][0]['id']
  self.records[old_id].update(status='superseded',pr_number=7)
  self.controller.state['changes'][0].update(status='superseded',pr_number=7)
  self.operations=[dict(kind='publish',status='confirmed',request=dict(action='close-superseded',repository=service.INFRA,pr_number=7),result=dict(closed=True))]
  old=copy.deepcopy(self.records[old_id]);manifest=self.directory/'services'/f"{self.request['id']}.json";raw=manifest.read_bytes();manifest.unlink()
  (self.directory/'concurrent.py').write_text('concurrent = True\n')
  self.controller.state['sources'][service.INFRA]=dict(revision='b'*40,snapshot=source.snapshot(self.directory));manifest.write_bytes(raw)
  service.prepare(self.controller,self.root,self.request)
  self.assertNotEqual(self.controller.state['changes'][0]['id'],old_id)
  self.assertEqual(self.records[old_id],old)

 def test_candidate_identity_is_stable_per_request_and_base(self):
  a=service.candidate_id(self.controller,self.request,'a'*40)
  self.assertEqual(a,service.candidate_id(self.controller,self.request,'a'*40))
  self.assertNotEqual(a,service.candidate_id(self.controller,self.request,'b'*40))


if __name__=='__main__':unittest.main()
