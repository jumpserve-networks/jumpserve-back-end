"""Reconciliation requires original identity, complete coverage and exact bytes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import dispatch_recovery as recovery,store
from test_dispatch_recovery import Fixture
from test_captured_provider_failures import failed_native
from workflow import canonical,digest


class NativeEvidenceContracts(unittest.TestCase):
    def test_ledger_rejects_excess_foreign_transport_and_provider_contracts(self):
        for defect in ['excess','foreign','transport','request','model']:
            f=Fixture(1);call=f.calls[0]
            if defect=='excess':f.calls*=101
            elif defect=='foreign':call['task_id']='foreign'
            elif defect=='transport':call['response_received']=False
            elif defect=='request':
                pointer=f.add(canonical(dict(model='other',store=False,stream=True)));call.update(request_artifact_id=pointer['artifact_id'],request_sha256=pointer['sha256'])
            else:
                raw=b'data: '+canonical(dict(type='response.completed',response=dict(status='completed',model='other',output=[])))+b'\n\n';pointer=f.add(raw);call.update(response_artifact_id=pointer['artifact_id'],response_sha256=pointer['sha256'])
            before=copy.deepcopy(f.calls)
            with self.subTest(defect=defect),f.patches():
                if defect=='transport':self.assertIsNone(recovery.ledger(f.task,f.d,f.original,f.rows))
                else:
                    with self.assertRaises(ValueError):recovery.ledger(f.task,f.d,f.original,f.rows)
            self.assertEqual(f.calls,before)

    def test_native_sidecar_inventory_and_paths_fail_before_reconciliation_writes(self):
        for defect in ['calls','receipts','symlink','oversized','unrelated','duplicate','path','missing-response','pending']:
            f=Fixture(1);call=f.calls[0];call['status']='unknown'
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);raw=f.blobs[call['response_artifact_id']]
                receipt=dict(version=recovery.CALL_VERSION,call_id=call['id'],dispatch_id=call['dispatch_id'],task_id=call['task_id'],study_id=call['study_id'],request_sha256=call['request_sha256'],response_filename='call-1-response.bin',response_sha256=digest(raw),status='complete',response_received=True,http_status=200)
                (root/'call-1-response.bin').write_bytes(raw)
                if defect=='calls':f.calls*=101
                elif defect=='receipts':
                    for index in range(101):(root/f'call-{index+1}-completion.json').write_bytes(canonical(receipt))
                elif defect=='symlink':(root/'call-1-completion.json').symlink_to(root/'call-1-response.bin')
                elif defect=='oversized':(root/'call-1-completion.json').write_bytes(b'x'*16001)
                elif defect=='unrelated':
                    receipt['call_id']='unrelated';call['response_received']=False
                elif defect=='duplicate':(root/'call-2-completion.json').write_bytes(canonical(receipt))
                elif defect=='path':receipt['response_filename']='../private'
                elif defect=='missing-response':(root/'call-1-response.bin').unlink()
                else:call['status']='running'
                if defect not in ['receipts','symlink','oversized']:(root/'call-1-completion.json').write_bytes(canonical(receipt))
                with f.patches(),patch.object(store,'rpc') as rpc:
                    if defect in ['unrelated','missing-response','pending']:recovery.reconcile_native_calls(f.task,f.d,root,Mock())
                    else:
                        with self.assertRaises(ValueError):recovery.reconcile_native_calls(f.task,f.d,root,Mock())
                    rpc.assert_not_called()

    def test_retained_failure_proofs_must_match_the_capture_and_unchanged_request(self):
        f=failed_native()
        def retain(name,raw,kind):return f.add(raw)
        def record(name,payload):
            f.call_proofs.append(dict(call_id=payload['p_call'],artifact_id=payload['p_artifact'],sha256=payload['p_sha256'],proof=payload['p_proof'],request_sha256=f.calls[0]['request_sha256']))
        with tempfile.TemporaryDirectory() as directory,f.patches(),patch.object(store,'rpc',side_effect=record):recovery.reconcile_native_calls(f.task,f.d,Path(directory),retain)
        original=copy.deepcopy(f.call_proofs)
        for defect in ['identity','failure','capture']:
            f.call_proofs=copy.deepcopy(original);row=f.call_proofs[0];proof=row['proof']
            if defect=='identity':proof['task_id']='foreign'
            elif defect=='failure':proof['failure']['error_code']='invented'
            else:proof['completion_record']={}
            raw=canonical(proof);pointer=f.add(raw);row.update(artifact_id=pointer['artifact_id'],sha256=pointer['sha256'])
            with self.subTest(defect=defect),f.patches(),self.assertRaises(ValueError):recovery.reconciled_call(f.task,f.calls[0],f.original,f.rows)

    def test_confirmed_dispatch_requires_one_proof_and_exact_native_call_coverage(self):
        for defect in ['duplicate','row-identity','proof-bytes','version','calls','count','checkpoint','logical']:
            f=Fixture(1);receipt=f.receipt()
            if defect in ['count','checkpoint','logical']:
                if defect=='count':receipt['provider_calls']=0
                elif defect=='checkpoint':receipt['checkpoint']=None
                else:f.d['request_sha256']='a'*64
            else:
                proof=f.proof();row=f.proofs[0]
                if defect=='duplicate':f.proofs*=2
                elif defect=='row-identity':row['task_id']='foreign'
                elif defect=='proof-bytes':proof['container_cleanup']='running'
                else:
                    if defect=='version':proof['version']='unknown'
                    else:proof['calls']=[]
                    pointer=f.add(canonical(proof));row.update(artifact_id=pointer['artifact_id'],sha256=pointer['sha256'])
            with self.subTest(defect=defect),f.patches():
                if defect in ['count','checkpoint','logical']:self.assertIsNone(recovery.confirmed(f.task,f.d,receipt,f.original,f.rows))
                else:
                    with self.assertRaises(ValueError):recovery.confirmed(f.task,f.d,receipt,f.original,f.rows)


if __name__=='__main__':unittest.main()
