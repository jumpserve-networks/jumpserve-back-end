"""Owning-repository service handoffs preserve original stops and source scope."""
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research_workflow'))
from platform_supervisor import service_work as service,source
from platform_supervisor.common import canonical,digest,Hold


class ServiceWork(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.spec=dict(version=service.VERSION,profile='assessment-evidence-v1',objective='Deliver retained intake failure evidence.',acceptance=['Verify the original artifact hash.'])
  self.request=dict(id='11111111-1111-4111-8111-111111111111',profile=self.spec['profile'],spec=self.spec)

 def test_progress_captures_only_a_typed_unfinished_service_request(self):
  progress=dict(version='platform-progress-v1',ready=False,status='blocked',service_work=self.spec)
  path=self.root/'progress.json';path.write_bytes(canonical(progress));state={}
  with self.assertRaises(Hold) as stopped:service.capture(self.root,state)
  self.assertEqual(stopped.exception.status,'implementing');self.assertEqual(state['pending_service_work']['spec'],self.spec)
  self.assertEqual(state['pending_service_work']['progress_sha256'],digest(path.read_bytes()));self.assertTrue(path.exists())
  for change in (dict(self.spec,profile='arbitrary-sql'),dict(self.spec,sql='drop table research_claims'),dict(self.spec,acceptance=[])):
   path.write_bytes(canonical(dict(progress,service_work=change)))
   with self.assertRaises(Hold):service.capture(self.root,{})

 def test_plain_progress_and_untrusted_ready_flags_do_not_authorize_a_service(self):
  path=self.root/'progress.json';path.write_text('{"version":"platform-progress-v1","ready":false,"status":"blocked"}')
  self.assertFalse(service.capture(self.root,{}))
  path.write_bytes(canonical(dict(version='platform-progress-v1',ready=True,service_work=self.spec)))
  with self.assertRaises(Hold):service.capture(self.root,{})

 def controller(self):
  store=Mock();store.task=dict(id='22222222-2222-4222-8222-222222222222',repositories=[service.INFRA],lease_token='44444444-4444-4444-8444-444444444444');store.worker='33333333-3333-4333-8333-333333333333'
  store.artifact.side_effect=lambda name,raw:dict(id=name,sha256=digest(raw))
  directory=self.root/'jumpserve-infra';directory.mkdir();(directory/'baseline.py').write_text('immutable = True\n')
  state=dict(sources={service.INFRA:dict(revision='a'*40,snapshot=source.snapshot(directory))})
  return SimpleNamespace(store=store,state=state,record_change=Mock(),save_state=Mock(),flags=dict(service_work_contract=service.VERSION))

 def test_trusted_service_task_derives_only_its_manifest_without_model_or_credentials(self):
  controller=self.controller();service.prepare(controller,self.root,self.request)
  change=controller.state['changes'][0];self.assertEqual(change['component'],'infrastructure')
  self.assertEqual([f['path'] for f in change['patch']['files']],['services/'+self.request['id']+'.json'])
  self.assertTrue(controller.state['tested']);self.assertTrue(controller.state['service_work_manifest']['passed'])
  self.assertFalse(controller.state['service_work_manifest']['executable_changes'])
  controller.store.rpc.assert_not_called();controller.record_change.assert_called_once()
  service.prepare(controller,self.root,self.request);controller.record_change.assert_called_once()

 def test_other_repository_edits_and_a_modified_manifest_are_not_qualified(self):
  controller=self.controller();(self.root/'jumpserve-infra/baseline.py').write_text('changed = True')
  with self.assertRaises(Hold):service.prepare(controller,self.root,self.request)
  self.assertNotIn('tested',controller.state)

 def test_manifest_cannot_request_commands_other_profiles_or_another_request(self):
  valid=service.manifest(self.request);self.assertTrue(service.inspect_manifest(valid,self.request)['passed'])
  for value in (dict(valid,command='aws iam attach-role-policy'),dict(valid,request_id='44444444-4444-4444-8444-444444444444'),dict(valid,profile='different')):
   with self.assertRaises(ValueError):service.inspect_manifest(value,self.request)

 def test_activation_preserves_old_stop_once_and_never_resets_deadline_or_budget(self):
  controller=self.controller();controller.state.update(deadline_at='retained',committed_cents=123,repair_guard=dict(waiting=True))
  original=canonical(dict(version='platform-progress-v1',ready=False,status='blocked',remaining_blocker='Service missing.'))
  path=self.root/'progress.json';path.write_bytes(original);activation=dict(request_id=self.request['id'])
  service.restore(controller,self.root,activation)
  self.assertFalse(path.exists());self.assertEqual(controller.state['deadline_at'],'retained');self.assertEqual(controller.state['committed_cents'],123)
  controller.store.artifact.assert_called_once_with('progress-before-service-activation.json',original)
  path.write_bytes(original);service.restore(controller,self.root,activation);self.assertTrue(path.exists())

 def test_uninstalled_database_contract_never_enters_the_service_path(self):
  controller=self.controller();controller.flags={};service.deliver(controller,self.root)
  controller.store.request.assert_not_called()

 def test_verified_missing_parser_failure_automatically_hands_off_without_another_model_request(self):
  controller=self.controller();failure=canonical({'reason':"ModuleNotFoundError: No module named 'pypdf'"});sha=digest(failure)
  evidence=dict(assessment_id='55555555-5555-4555-8555-555555555555',task=dict(stage='intake',status='blocked',attempts=0,context={'intake_pool_version':'warm-intake-v1'}),
   review=dict(status='failed',confirmed=False,candidate_count=0),intake=dict(enabled=True,status='ready'),
   artifacts=[dict(id=self.request['id'],kind='failed-text-extraction',sha256=sha,byte_count=len(failure))])
  calls=[]
  def request(path,*args,**kwargs):
   if path.startswith('/storage/'):return failure
   payload=args[1];calls.append(payload)
   if payload['p_operation']=='context':return dict(version=service.VERSION,evidence=evidence,implementation=None,activation=None)
   return dict(id=self.request['id'],implementation_task_id='66666666-6666-4666-8666-666666666666')
  controller.store.request.side_effect=request
  with self.assertRaises(Hold) as stopped:service.deliver(controller,self.root)
  self.assertEqual(stopped.exception.status,'blocked');self.assertEqual(calls[-1]['p_payload']['spec']['profile'],'intake-parser-recovery-v1')
  self.assertIn('service_work_wait',controller.state)


if __name__=='__main__':unittest.main()
