"""Unproven and active original operations never admit replacement work."""
import contextlib
import copy
import fcntl
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomous_repair as repair
import dispatch_recovery as recovery
import experiment_builder as builder
import store
from workflow import canonical,digest
from test_dispatch_recovery import Fixture
from test_controller_terminal_recovery import Fixture as ControllerFixture
from test_captured_provider_failures import failed_native


class RecoveryAdmissionContracts(unittest.TestCase):
 def test_ambiguous_call_reconciliations_and_nonterminal_parent_are_not_proof(self):
  f=Fixture(1);f.call_proofs=[dict(call_id=f.calls[0]['id'])]*2
  with self.assertRaisesRegex(ValueError,'Ambiguous native call reconciliation'):
   recovery.reconciled_call(f.task,f.calls[0],f.original,f.rows)
  f.d['status']='complete'
  with f.patches():self.assertIsNone(recovery.confirmed(f.task,f.d,{},f.original,f.rows))

 def test_unproven_native_failure_and_foreign_terminal_model_cannot_reconcile(self):
  f=failed_native();f.calls[0]['reason']='different-error'
  with tempfile.TemporaryDirectory() as tmp,f.patches(),patch.object(store,'rpc') as rpc:
   retain=Mock();recovery.reconcile_native_calls(f.task,f.d,Path(tmp),retain);retain.assert_not_called();rpc.assert_not_called()
  f=Fixture(1);call=f.calls[0];call['status']='unknown';raw=b'data: '+canonical(dict(type='response.completed',response=dict(status='completed',model='foreign-model',output=[])))+b'\n\n';response=f.add(raw);call.update(response_artifact_id=response['artifact_id'],response_sha256=response['sha256'])
  with tempfile.TemporaryDirectory() as tmp,f.patches(),patch.object(store,'rpc') as rpc,self.assertRaisesRegex(ValueError,'response model differs'):
   recovery.reconcile_native_calls(f.task,f.d,Path(tmp),Mock())
  rpc.assert_not_called()

 def test_worker_skips_ineligible_originals_before_cleanup_or_reconciliation(self):
  cases=['controller-unknown','controller-active','controller-no-receipt','controller-unproven','builder-queued-proof','builder-proof','builder-active','builder-no-directory','workspace-locked','became-active','live-dispatch','unresolved-ledger']
  for defect in cases:
   with self.subTest(defect=defect),tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
    f=ControllerFixture() if defect.startswith('controller') else Fixture(0);root=Path(tmp);task_reads=[]
    if defect=='controller-unknown':f.d['status']='unknown'
    if defect=='controller-no-receipt':f.d['provenance']['artifacts']=[]
    if defect=='builder-queued-proof':f.d['provenance']['queued_admission_reconciliation']=True
    if defect=='builder-proof':f.proofs=[{'original_proof':True}]
    if defect=='unresolved-ledger':
     f=Fixture(1);f.calls[0].update(status='unknown',response_received=False,response_artifact_id=None,response_sha256=None)
    directory=root/f.d['id'];workspace=root/'builder-workspaces'/f.task['id']/'workspace'
    if not defect.startswith('controller') and defect!='builder-no-directory':
     directory.mkdir();(directory/'logical.json').write_bytes(f.logical);workspace.mkdir(parents=True)
    if defect=='workspace-locked':
     lock=stack.enter_context((workspace.parent/'workspace.lock').open('a+b'));fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    def rows(kind,query):
     if kind=='model_reconciliations':return f.proofs
     if kind=='autonomy_tasks':
      task_reads.append(True);return [dict(f.task,status='running' if defect in ('controller-active','builder-active') or defect=='became-active' and len(task_reads)>1 else 'waiting')]
     if kind=='study_owners':return [dict(owner_id=f.task['actor_id'])]
     if kind=='model_dispatches':return [{'original_live_dispatch':True}] if defect=='live-dispatch' else []
     raise AssertionError(kind)
    stack.enter_context(patch.object(store,'rows',side_effect=rows));stack.enter_context(patch.object(repair,'rows',side_effect=f.rows));stack.enter_context(patch.object(repair,'original',side_effect=f.original))
    stack.enter_context(patch.object(recovery,'runtime_diagnostic'));stack.enter_context(patch.object(recovery,'controller_terminal',return_value=None))
    cleanup=stack.enter_context(patch.object(builder,'container_action'));stack.enter_context(patch.object(builder,'normalize_workspace',return_value=b'Original normalization receipt'))
    upload=stack.enter_context(patch.object(store,'artifact'));rpc=stack.enter_context(patch.object(store,'rpc'))
    self.assertEqual(recovery._reconcile_dispatches(root,[f.d]),[]);upload.assert_not_called();rpc.assert_not_called()
    if defect=='unresolved-ledger':cleanup.assert_called_once_with(f.task['id'],'stop')
    else:cleanup.assert_not_called()

 def test_controller_reconciliation_requires_exact_storage_readback(self):
  f=ControllerFixture()
  def rows(kind,query):
   if kind=='model_reconciliations':return []
   if kind=='autonomy_tasks':return [f.task]
   if kind=='study_owners':return [dict(owner_id=f.task['actor_id'])]
   raise AssertionError(kind)
  with tempfile.TemporaryDirectory() as tmp,patch.object(store,'rows',side_effect=rows),patch.object(repair,'original',side_effect=f.original),patch.object(repair,'rows',side_effect=f.rows),patch.object(store,'artifact',side_effect=lambda study,owner,raw,identity,*args:dict(id=identity,sha256=digest(raw))),patch.object(store,'append'),patch.object(store,'artifact_bytes',return_value=b'Changed after storage'),patch.object(store,'rpc') as rpc,self.assertRaisesRegex(ValueError,'readback differs'):
   recovery._reconcile_dispatches(tmp,[f.d])
  rpc.assert_not_called()


if __name__=='__main__':unittest.main()
