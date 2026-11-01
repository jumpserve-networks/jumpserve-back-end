"""Typed controller admission proof, real diagnosis and worker routing guards."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import autonomous_repair as repair
import dispatch_recovery as recovery
import experiment_builder as builder
import openclaw_runtime as runtime
from workflow import canonical,digest


def fixture(builder_request=False):
    task=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),context={})
    request=dict(modelId='gpt-6.1-sol',messages=[dict(role='user',content='Synthetic controller request')])
    if builder_request:request['builder']=dict(context=dict(synthetic=True))
    raw=canonical(request).decode();ident=str(uuid.uuid4())
    snapshot=dict(id=ident,study_id=task['study_id'],task_id=task['id'],request_raw=raw,request_sha256=digest(raw.encode()),
        runtime='openclaw-production-v1',status='queued',provenance={},lease_token=None,lease_until=None,response_artifact_id=None,response_sha256=None,ended_at=None)
    original=canonical(snapshot).decode();h=digest(original.encode())
    proof=dict(version=recovery.QUEUED_VERSION if builder_request else recovery.QUEUED_CONTROLLER_VERSION,
        dispatch_id=ident,study_id=task['study_id'],task_id=task['id'],request_sha256=snapshot['request_sha256'],
        snapshot_sha256=h,original_status='queued',terminal_status='failed',parent_turn_status='failed',
        parent_turn_request_sha256=snapshot['request_sha256'],provider_calls=0,provider_request_sent=False,admission_state='pristine-unclaimed')
    if not builder_request:proof['request_kind']='controller'
    row=dict(dispatch_id=ident,study_id=task['study_id'],task_id=task['id'],request_sha256=snapshot['request_sha256'],
        snapshot_raw=original,snapshot_sha256=h,parent_turn_status='failed',proof=proof)
    dispatch=dict(snapshot,status='failed',provenance=dict(queued_admission_reconciliation=proof))
    return task,dispatch,row


class ControllerQueuedRecovery(unittest.TestCase):
    def test_worker_routes_ordinary_queued_controller_to_atomic_adapter_without_native_tools(self):
        task,dispatch,row=fixture();dispatch=json.loads(row['snapshot_raw'])
        with tempfile.TemporaryDirectory() as tmp,patch.object(recovery.store,'rpc',return_value=dict(reconciled=True)) as rpc,patch.object(builder,'container_action') as containers,patch.object(runtime,'credential') as token,patch.object(runtime,'http_request') as transport:
            self.assertEqual(recovery._reconcile_dispatches(Path(tmp),[dispatch]),[dispatch['id']])
        rpc.assert_called_once_with('research_model_reconcile_queued',dict(p_dispatch=dispatch['id'],p_request_sha256=dispatch['request_sha256']))
        containers.assert_not_called();token.assert_not_called();transport.assert_not_called()

    def test_immutable_v1_builder_and_v2_controller_proofs_pass_actual_read_only_diagnosis(self):
        for is_builder in (False,True):
            with self.subTest(builder=is_builder):
                task,dispatch,row=fixture(is_builder);before=copy.deepcopy((task,dispatch,row))
                def rows(t,kind,**query):
                    if kind=='model_dispatches':return [dispatch]
                    if kind=='queued_dispatch_reconciliations':return [row]
                    return []
                with patch.object(repair,'rows',side_effect=rows),patch.object(repair.store,'rpc') as rpc:
                    diagnosis=repair.diagnose(task,dict(category='implementation'))
                self.assertFalse(diagnosis['waiting']);self.assertFalse(diagnosis['validated'])
                self.assertEqual(diagnosis['reconciled_dispatches'][0]['kind'],'atomic-unclaimed-dispatch')
                self.assertEqual((task,dispatch,row),before);rpc.assert_not_called()

    def test_controller_proof_cannot_masquerade_as_builder_or_hide_transport(self):
        for defect in ('v1-controller','wrong-kind','paid-or-uncertain','boolean-zero','claimed','request-mismatch','unknown-status'):
            with self.subTest(defect=defect):
                task,dispatch,row=fixture();proof=row['proof']
                if defect=='v1-controller':proof['version']=recovery.QUEUED_VERSION
                elif defect=='wrong-kind':proof['request_kind']='builder'
                elif defect=='paid-or-uncertain':proof['provider_request_sent']=True
                elif defect=='boolean-zero':proof['provider_calls']=False
                elif defect=='unknown-status':dispatch['status']='unknown'
                else:
                    original=json.loads(row['snapshot_raw'])
                    if defect=='claimed':original['lease_token']=str(uuid.uuid4())
                    else:original['request_raw']='{"changed":true}'
                    row['snapshot_raw']=canonical(original).decode();row['snapshot_sha256']=digest(row['snapshot_raw'].encode());proof['snapshot_sha256']=row['snapshot_sha256']
                with self.assertRaises(ValueError):recovery.queued_reconciliation(task,dispatch,lambda *a,**k:[row])

    def test_reconciled_controller_does_not_unfence_a_different_unknown_native_call(self):
        task,dispatch,row=fixture();unknown=dict(id=str(uuid.uuid4()),status='unknown',request_sha256='a'*64)
        before=copy.deepcopy(unknown)
        def rows(t,kind,**query):
            if kind=='builder_calls':return [unknown]
            if kind=='model_dispatches':return [dispatch]
            if kind=='queued_dispatch_reconciliations':return [row]
            return []
        with patch.object(repair,'rows',side_effect=rows),patch.object(repair.store,'rpc') as rpc:
            diagnosis=repair.diagnose(task,dict(category='implementation'))
        self.assertTrue(diagnosis['waiting']);self.assertIn('unresolved',diagnosis['reason'])
        self.assertEqual(unknown,before);rpc.assert_not_called()

    def test_nonqueued_ordinary_unknown_never_enters_builder_recovery_or_transport(self):
        _,dispatch,_=fixture();dispatch['status']='unknown';dispatch['provenance']={}
        with tempfile.TemporaryDirectory() as tmp,patch.object(recovery.store,'rpc') as rpc,patch.object(builder,'container_action') as containers:
            self.assertEqual(recovery._reconcile_dispatches(Path(tmp),[dispatch]),[])
        rpc.assert_not_called();containers.assert_not_called()


if __name__=='__main__':unittest.main()
