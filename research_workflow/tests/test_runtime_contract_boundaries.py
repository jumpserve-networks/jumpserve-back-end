"""Admission, resource and original-identity guards without cloud operations."""
import contextlib
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_costs as costs,model_dispatch,paper_selection,paper_titles,runner,source_project,store,worker,network_execution
import autonomy_read_progress as reading
from test_autonomy_read_progress import stalled_context
from workflow import canonical,digest


class RuntimeContractBoundaries(unittest.TestCase):
    def test_source_only_reservations_and_executor_policy_hashes_are_explicit_before_admission(self):
        raw=canonical({'runner':'source-arithmetic-v1'});quote=costs.builtin_quote(raw);self.assertEqual(quote['cents'],1);self.assertEqual(quote['quote']['experiment_instances'],0);self.assertIsNone(quote['quote']['measured_charges_usd'])
        bounded=canonical(dict(runner='bounded-tcp-v1',config=dict(payload_bytes=100,exchanges=2,application_delay_ms=0)))
        with patch.dict(os.environ,NETWORK_BOUNDED_FUNCTION='arn:aws:lambda:us-east-1:395567831870:function:OriginalExecutor:1',NETWORK_BOUNDED_POLICY_SHA256='0'*64),self.assertRaisesRegex(ValueError,'policy identity differs'):costs.bounded_quote(bounded,today=dt.datetime(2026,10,19,tzinfo=dt.timezone.utc))
        path=Path(costs.__file__).parent/'policies/built-package-costs-v2.json';policy=json.loads(path.read_bytes());spec=dict(runner='built-package-v1',config=dict(package=dict(sha256='0'*64)),limits=dict(max_instances=1,wall_seconds=1800,max_network_bytes=10000000000,max_output_bytes=3000000))
        with patch.object(costs,'BUILT_POLICY_PATH',path),patch.dict(os.environ,NETWORK_BUILT_PACKAGE_FUNCTION='arn:aws:lambda:us-east-1:395567831870:function:OriginalExecutor:1',NETWORK_BUILT_PACKAGE_POLICY_SHA256=digest(canonical(policy))),self.assertRaisesRegex(ValueError,'wrapper differs'):costs.builtin_quote(canonical(spec),today=dt.datetime(2026,10,19,tzinfo=dt.timezone.utc))

    def test_dispatch_rpc_and_retry_classification_use_only_the_fixed_operation_and_transient_read_codes(self):
        with patch.object(store,'rpc',return_value={'original':True}) as rpc:self.assertEqual(model_dispatch.health(),{'original':True})
        rpc.assert_called_once_with('research_model_dispatch',dict(p_operation='health',p_payload={}))
        for error,expected in [(store.StoreError('HTTP 503 original failure'),True),(store.StoreError('HTTP 400 original failure'),False),(ValueError('Original invalid request'),False)]:self.assertEqual(model_dispatch.transient_read(error),expected)

    def test_source_sealing_refuses_an_oversized_original_before_any_retention_or_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'experiment.py').write_bytes(b'x'*256001);retain=Mock()
            with self.assertRaisesRegex(ValueError,'file bound'):source_project.freeze({},root,retain)
            retain.assert_not_called();self.assertEqual((root/'experiment.py').stat().st_size,256001)
        for row in [dict(observation_id='',configuration_identity='configuration',metric='latency'),dict(observation_id='observation',configuration_identity=0,metric='latency')]:
            with self.assertRaisesRegex(ValueError,'exact observation'):runner.key(row)

    def test_local_single_poll_does_not_admit_a_job_when_both_original_slots_are_idle(self):
        output=io.StringIO()
        with patch.object(store,'rpc',return_value=None) as rpc,patch.object(network_execution,'pending',return_value=[]),contextlib.redirect_stdout(output):runpy.run_path(worker.__file__,run_name='__main__')
        self.assertEqual(json.loads(output.getvalue())['outcomes'],[{'status':'idle'}]*2);self.assertEqual(rpc.call_count,2);self.assertTrue(all(call.args[0]=='research_queue_claim' for call in rpc.call_args_list))

    def test_reading_recovery_rejects_another_scope_and_mismatched_retained_page_identity(self):
        with self.assertRaisesRegex(ValueError,'original phase/candidate'):reading.recover({},'planning')
        context=stalled_context();context['evidence_read_progress']['retained_pages'][0]['key']='changed'
        with self.assertRaisesRegex(ValueError,'page identity mismatch'):reading.recover(context,'planning')

    def test_claim_groups_preserve_empty_selections_and_invalid_owner_author_declarations_remain_unavailable(self):
        selected=paper_selection.groups({'central_claims':[{'claim_id':'original-claim'}]});self.assertTrue(any(value==['original-claim'] for value in selected.values()));self.assertTrue(any(value==[] for value in selected.values()))
        self.assertIsNone(paper_titles.review_authors({'owner_authors':{'version':paper_titles.REVIEW_AUTHORS_VERSION,'names':['private@example.test']}}))


if __name__=='__main__':unittest.main()
