"""Retained transport failures cannot clear a fence with inconsistent originals."""
import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import dispatch_recovery as recovery
from workflow import canonical,digest
import test_captured_provider_failures as failed
import test_controller_terminal_recovery as terminal


class ControllerEvidenceContracts(unittest.TestCase):
    def test_missing_failure_reason_oversized_body_and_wrong_stream_model_remain_uncertain(self):
        request=failed.request()
        for reason,response in [(None,b''),('',b''),('failure',b'x'*1048577),('failure',b'data: {"type":"error","response":{"model":"other"}}\n\n')]:
            with self.subTest(reason=reason,size=len(response)):
                self.assertIsNone(recovery.captured_failure(request,response,200,reason))

    def test_controller_failure_checks_task_receipt_duplicate_refs_and_original_logical_hash(self):
        for defect in ['task','receipt','duplicate','logical-hash','builder-logical','unproved-error']:
            with self.subTest(defect=defect):
                f=failed.failed_controller()
                if defect=='task':f.d['task_id']='foreign'
                elif defect=='receipt':f.receipt['provider_calls']=True
                elif defect=='duplicate':f.refs.append(copy.deepcopy(f.refs[0]))
                elif defect=='logical-hash':f.d['request_sha256']='a'*64
                elif defect=='builder-logical':
                    raw=canonical({'builder':{}});f.replace('logical.json',raw);f.d['request_sha256']=digest(raw)
                else:
                    f.receipt['http_status']=401;f.replace('receipt.json',canonical(f.receipt));f.d['provenance'].update(http_status=401)
                before=copy.deepcopy(f.d)
                if defect in ['duplicate','logical-hash']:
                    with self.assertRaises(ValueError):recovery.controller_failure(f.task,f.d,f.receipt,f.original,f.rows)
                else:self.assertIsNone(recovery.controller_failure(f.task,f.d,f.receipt,f.original,f.rows))
                self.assertEqual(f.d,before)

    def test_unrelated_refs_do_not_change_a_verified_original_failure(self):
        f=failed.failed_controller();f.refs.insert(0,{'name':'unrelated-private-file'})
        result=recovery.controller_failure(f.task,f.d,f.receipt,f.original,f.rows)
        self.assertEqual(set(result['references']),{'logical.json','provider-request.json','provider-response.bin','receipt.json'})

    def test_terminal_fence_requires_an_original_logical_request_and_bounded_response_identity(self):
        for defect in ['logical-hash','builder-logical','missing-response-id','unrelated-ref']:
            with self.subTest(defect=defect):
                f=terminal.Fixture()
                if defect=='logical-hash':f.d['request_sha256']='a'*64
                elif defect=='builder-logical':
                    raw=canonical({'builder':{}});f.replace('logical.json',raw);f.d['request_sha256']=digest(raw)
                elif defect=='missing-response-id':
                    f.terminal.pop('id');f.replace('provider-response.bin',f.stream())
                else:f.refs.insert(0,{'name':'unrelated-private-file'})
                if defect=='logical-hash':
                    with self.assertRaisesRegex(ValueError,'logical hash differs'):f.proof()
                elif defect=='unrelated-ref':self.assertFalse(f.proof()['decision_accepted'])
                else:self.assertIsNone(f.proof())


if __name__=='__main__':unittest.main()
