"""Bounded worker scheduling and unknown completion preserve original job IDs."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import worker,shared_campaigns,store,workflow


class WorkerCompletionBoundaries(unittest.TestCase):
    def test_missing_owner_or_excessive_resources_finish_only_the_original_failed_job(self):
        for defect in ['owner','resource']:
            job=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),campaign_id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()))
            def rows(kind,params):return [] if defect=='owner' else dict(study_owners=[dict(owner_id='original-owner')],campaigns=[dict(protocol_id='original-protocol',adapter='matched-numeric-v1')],protocols=[dict(document=dict(resource_limits=dict(max_observations=1001,wall_seconds=10)))])[kind]
            with self.subTest(defect=defect),patch.object(store,'rows',side_effect=rows),patch.object(store,'queue_input_bytes',return_value=b'Original input'),patch.object(store,'rpc',return_value=dict(status='failed')) as finish,patch.object(worker.runner,'execute') as execute:
                self.assertEqual(worker.execute_job(job)['status'],'failed')
            execute.assert_not_called();payload=finish.call_args.args[1];self.assertEqual(payload['p_job'],job['id']);self.assertIsNone(payload['p_run']);self.assertEqual(payload['p_records'],[]);self.assertIn('Original input and attempt are retained',payload['p_error'])
        with patch.object(store,'rows',return_value=[]),self.assertRaisesRegex(ValueError,'Required frozen'):worker.one('protocols','study','protocol')

    def test_shared_acquisition_uses_an_explicit_original_source_without_an_extra_claim_lookup(self):
        from test_autonomy_methods import Recorded
        db=Recorded();db.task['context']['source_id']=db.claim['source_id']
        with patch.object(store,'rows') as read,patch.object(store,'rpc',return_value=dict(shared=False)) as bind:shared_campaigns.register(db.task,db.proposal,[],'campaign','protocol')
        read.assert_not_called();self.assertEqual(bind.call_args.args[1]['p_acquisition']['source_id'],db.claim['source_id'])
        db.task['context'].pop('source_id')
        with patch.object(store,'rows',return_value=[]),patch.object(store,'rpc') as bind,self.assertRaisesRegex(ValueError,'original claim/source'):shared_campaigns.register(db.task,db.proposal,[],'campaign','protocol')
        bind.assert_not_called()
    def test_pending_completion_failures_and_new_claim_failures_remain_distinct(self):
        pending=[{'id':'original-pending'}]
        with patch.object(worker.network_execution,'pending',return_value=pending),patch.object(worker,'execute_job',side_effect=TimeoutError('Synthetic private completion detail')),patch.object(store,'rpc',side_effect=TimeoutError('Synthetic private claim detail')),contextlib.redirect_stdout(io.StringIO()) as output:
            result=worker.handler({})
        self.assertEqual(result['slots'],2)
        self.assertEqual(result['outcomes'][0],{'job_id':'original-pending','status':'completion-unconfirmed','error_type':'TimeoutError'})
        self.assertEqual([row['status'] for row in result['outcomes'][1:]],['claim-unconfirmed']*2)
        self.assertNotIn('private',output.getvalue())

    def test_newly_claimed_job_completion_unknowns_do_not_schedule_replacement_jobs(self):
        with patch.object(worker.network_execution,'pending',return_value=[]),patch.object(store,'rpc',return_value={'id':'original-newly-claimed'}) as rpc,patch.object(worker,'execute_job',side_effect=TimeoutError('Synthetic completion unconfirmed')) as execute,contextlib.redirect_stdout(io.StringIO()):
            result=worker.handler({})
        self.assertEqual(rpc.call_count,2);self.assertTrue(all(call.args[0]=='research_queue_claim' for call in rpc.call_args_list));self.assertEqual(execute.call_count,2);self.assertTrue(all(row['job_id']=='original-newly-claimed' and row['status']=='completion-unconfirmed' for row in result['outcomes']))

    def test_network_executor_uncertainty_keeps_the_original_lease_running_without_finishing_or_replaying(self):
        identities=[str(uuid.uuid4()) for _ in range(4)]
        job=dict(id=identities[0],study_id=identities[1],campaign_id=identities[2],lease_token=identities[3])
        def rows(kind,params):
            return {'study_owners':[{'owner_id':identities[3]}],'campaigns':[{'protocol_id':identities[3],'adapter':'network-execution-v1'}],'protocols':[{'document':{'resource_limits':{}}}]}[kind]
        with patch.object(store,'rows',side_effect=rows),patch.object(store,'queue_input_bytes',return_value=b'original-input'),patch.object(worker.network_execution,'advance',side_effect=TimeoutError('Synthetic private transport detail')) as advance,patch.object(store,'rpc') as finish:
            result=worker.execute_job(job)
        self.assertEqual(result['status'],'running');self.assertEqual(result['job_id'],job['id']);self.assertEqual(result['error_type'],'TimeoutError');finish.assert_not_called();self.assertEqual(advance.call_count,1)

    def test_shared_acquisition_catalog_bounds_inline_delivery_and_never_treats_references_as_missing_inputs(self):
        task={'study_id':'study','context':{'source_id':'original-source'}}
        items=[{'id':'small','campaign_id':'campaign','acquisition_sha256':'a'*64,'acquisition':{'source_id':'original-source','retained_zero':0}},
            {'id':'large','campaign_id':'other-campaign','acquisition_sha256':'b'*64,'acquisition':{'source_id':'original-source','original':'x'*5001}},
            {'id':'foreign','campaign_id':'foreign','acquisition_sha256':'c'*64,'acquisition':{'source_id':'another-source'}}]
        with patch.object(store,'rows',return_value=items) as read:
            result=shared_campaigns.available(task)
        self.assertEqual(read.call_args.args[1]['limit'],8);self.assertEqual(len(result),2);self.assertEqual(result[0]['acquisition']['retained_zero'],0);self.assertEqual(result[1]['representation'],'retained-acquisition-reference');self.assertEqual(result[1]['read_request'],{'kind':'acquisition','id':'large','cursor':0});self.assertNotIn('acquisition',result[1])
        fetch=Mock(return_value=items);self.assertEqual(shared_campaigns.available(task,fetch),result)


if __name__=='__main__':unittest.main()
