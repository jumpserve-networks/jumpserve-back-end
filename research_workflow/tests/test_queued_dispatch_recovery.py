"""Unclaimed queue recovery cannot manufacture a provider/cleanup outcome."""
import copy,json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomous_repair as repair,dispatch_recovery as recovery,openclaw_runtime as runtime,store,experiment_builder as builder
from workflow import canonical,digest

def fixture():
    task=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),context={})
    logical=canonical(dict(modelId='gpt-6.1-sol',builder=dict(context={}))).decode()
    dispatch=dict(id=str(uuid.uuid4()),task_id=task['id'],study_id=task['study_id'],runtime='openclaw-production-v1',status='queued',
        request_raw=logical,request_sha256=digest(logical.encode()),lease_token=None,lease_until=None,response_artifact_id=None,response_sha256=None,ended_at=None,provenance={})
    snapshot=canonical(dispatch).decode();h=digest(snapshot.encode())
    proof=dict(version=recovery.QUEUED_VERSION,dispatch_id=dispatch['id'],study_id=task['study_id'],task_id=task['id'],
        request_sha256=dispatch['request_sha256'],snapshot_sha256=h,parent_turn_status='failed',parent_turn_request_sha256=dispatch['request_sha256'],
        original_status='queued',terminal_status='failed',provider_calls=0,provider_request_sent=False,admission_state='pristine-unclaimed')
    row=dict(dispatch_id=dispatch['id'],study_id=task['study_id'],task_id=task['id'],request_sha256=dispatch['request_sha256'],
        snapshot_raw=snapshot,snapshot_sha256=h,parent_turn_status='failed',proof=proof)
    return task,dispatch,row

class QueuedRecovery(unittest.TestCase):
    def test_worker_closes_original_queued_identity_without_spool_provider_or_container_actions(self):
        task,dispatch,row=fixture();before=copy.deepcopy(dispatch)
        with tempfile.TemporaryDirectory() as tmp,patch.object(store,'request',return_value=dict(reconciled=True)) as request,patch.object(runtime,'credential') as credential,patch.object(runtime,'http_request') as transport,patch.object(builder,'container_action') as cleanup:
            self.assertEqual(recovery._reconcile_dispatches(tmp,[dispatch]),[dispatch['id']])
        request.assert_called_once_with('/rest/v1/rpc/research_model_reconcile_queued','POST',dict(p_dispatch=dispatch['id'],p_request_sha256=dispatch['request_sha256']))
        self.assertEqual(dispatch,before);credential.assert_not_called();transport.assert_not_called();cleanup.assert_not_called()

    def test_actual_repair_diagnosis_waits_then_accepts_only_server_recorded_closure(self):
        task,dispatch,row=fixture();original_task=copy.deepcopy(task)
        def rows(task,kind,**kwargs):
            if kind=='model_dispatches':return [dispatch]
            if kind=='queued_dispatch_reconciliations':return [row]
            return []
        with patch.object(repair,'rows',side_effect=rows),patch.object(runtime,'credential') as credential,patch.object(runtime,'http_request') as transport:
            self.assertTrue(repair.diagnose(task,dict(category='runtime'))['waiting'])
            dispatch.update(status='failed',provenance=dict(queued_admission_reconciliation=copy.deepcopy(row['proof'])))
            result=repair.diagnose(task,dict(category='runtime'))
        self.assertFalse(result['waiting']);self.assertFalse(result['validated'])
        self.assertEqual(result['reconciled_dispatches'][0]['kind'],'atomic-unclaimed-dispatch')
        self.assertEqual(result['reconciled_dispatches'][0]['native_calls'],0)
        self.assertEqual(task,original_task);credential.assert_not_called();transport.assert_not_called()

    def test_missing_forged_or_wrong_scope_proof_never_unlocks_diagnosis(self):
        for defect in ('missing','different-marker','hash','task','transport','running-parent','leased-snapshot','unknown-dispatch'):
            with self.subTest(defect=defect):
                task,dispatch,row=fixture();dispatch.update(status='failed',provenance=dict(queued_admission_reconciliation=copy.deepcopy(row['proof'])))
                if defect=='different-marker':dispatch['provenance']['queued_admission_reconciliation']['provider_calls']=1
                if defect=='hash':row['snapshot_sha256']='a'*64
                if defect=='task':row['task_id']=str(uuid.uuid4())
                if defect=='transport':row['proof']['provider_request_sent']=True;dispatch['provenance']['queued_admission_reconciliation']=copy.deepcopy(row['proof'])
                if defect=='running-parent':row['proof']['parent_turn_status']='running';dispatch['provenance']['queued_admission_reconciliation']=copy.deepcopy(row['proof'])
                if defect=='leased-snapshot':
                    value=json.loads(row['snapshot_raw']);value['lease_token']=str(uuid.uuid4());row['snapshot_raw']=canonical(value).decode()
                    row['snapshot_sha256']=digest(row['snapshot_raw'].encode());row['proof']['snapshot_sha256']=row['snapshot_sha256'];dispatch['provenance']['queued_admission_reconciliation']=copy.deepcopy(row['proof'])
                if defect=='unknown-dispatch':dispatch['status']='unknown'
                with self.assertRaises(ValueError):recovery.queued_reconciliation(task,dispatch,lambda *a,**kw:[] if defect=='missing' else [row])

    def test_unknown_native_outcome_still_fences_a_task_with_queued_admission_proof(self):
        task,dispatch,row=fixture();dispatch.update(status='failed',provenance=dict(queued_admission_reconciliation=row['proof']))
        def rows(task,kind,**kwargs):
            if kind=='builder_calls':return [dict(id=str(uuid.uuid4()),status='unknown')]
            if kind=='model_dispatches':return [dispatch]
            return []
        with patch.object(repair,'rows',side_effect=rows):self.assertTrue(repair.diagnose(task,dict(category='runtime'))['waiting'])

    def test_private_snapshot_is_not_a_public_relation(self):
        with patch.object(store,'request') as request:
            with self.assertRaisesRegex(ValueError,'Private relations'):store.rows('queued_dispatch_reconciliations',public=True)
            request.assert_not_called()

if __name__=='__main__':unittest.main()
