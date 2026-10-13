import copy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import scheduler
import store
import worker
from test_workflow import fixture, STUDY, ACTOR
from workflow import canonical, digest

def body():
    return dict(request_id=str(uuid.uuid4()), campaign_id=str(uuid.uuid4()), execution_mode="automatic", priority=3,
      dependencies=[], exclusive_resources=["machine:shared"], raw_input="{}", rationale="Development regression", followup_of=None)

class QueueValidation(unittest.TestCase):
    def test_original_input_and_definition_identity(self):
        value = body(); value['raw_input'] = '{\n "original": 0\n}'
        job_id, request, raw = scheduler.definition(value)
        self.assertEqual(raw, value['raw_input'].encode()); self.assertEqual(request['input_sha256'], digest(raw))
        self.assertNotIn('raw_input', request); self.assertEqual(job_id, value['request_id'])
    def test_limits_cycles_duplicates_and_untyped_prerequisites(self):
        for key, value in [('priority', True), ('priority', 6), ('raw_input', 'x'*256001), ('exclusive_resources',['Machine With Spaces']), ('exclusive_resources',['a','a']), ('dependencies',[{}])]:
            data = body(); data[key] = value
            with self.assertRaises(ValueError): scheduler.definition(data)
        data = body(); data['dependencies']=[dict(job_id=data['request_id'],requirement='complete-run')]
        with self.assertRaises(ValueError): scheduler.definition(data)
    def test_manual_review_has_no_invented_input(self):
        value = body(); value.update(execution_mode='manual',raw_input=None)
        _, request, raw = scheduler.definition(value); self.assertIsNone(raw); self.assertIsNone(request['input_sha256'])
        value['raw_input']='{}'
        with self.assertRaises(ValueError): scheduler.definition(value)
    def test_prerequisites_distinguish_completion_from_review_and_missingness(self):
        previous = str(uuid.uuid4()); job = dict(execution_mode='automatic',dependencies=[dict(job_id=previous,requirement='complete-run')])
        for status in ('failed','cancelled','expired','queued'):
            self.assertTrue(scheduler.blockers(job,{previous:dict(status=status,run_status='complete')}))
        self.assertTrue(scheduler.blockers(job,{previous:dict(status='awaiting-review',run_status='partial')}))
        self.assertEqual(scheduler.blockers(job,{previous:dict(status='awaiting-review',run_status='complete')}),[])
        job['dependencies'][0]['requirement']='reviewed-evidence'
        self.assertTrue(scheduler.blockers(job,{previous:dict(status='awaiting-review',run_status='complete')}))
        self.assertEqual(scheduler.blockers(job,{previous:dict(status='reviewed',run_status='partial')}),[])
    def test_immutable_replay_does_not_reexecute_or_upload(self):
        value = body(); _, request, _ = scheduler.definition(value)
        with patch.object(store,'rows',return_value=[dict(request=request)]), patch.object(store,'artifact') as artifact:
            self.assertTrue(scheduler.enqueue(STUDY, ACTOR, value)['replayed']); artifact.assert_not_called()
            value['rationale']='Changed'
            with self.assertRaises(ValueError): scheduler.enqueue(STUDY, ACTOR, value)
    def test_snapshot_is_private_bounded_and_keeps_failure_states(self):
        data=dict(jobs=[dict(id='j',execution_mode='manual',dependencies=[],status='manual',run_id=None)],events=[dict(details={'actor_id':ACTOR,'usage':None})])
        with patch.object(store,'rpc',return_value=data):
            result=scheduler.snapshot(STUDY,ACTOR)
            self.assertEqual(result['jobs'][0]['readiness'],'blocked'); self.assertNotIn('actor_id',result['events'][0]['details']); self.assertIsNone(result['events'][0]['details']['usage'])
        for kind in ('queue_jobs','queue_events'):
            with self.assertRaises(ValueError): store.rows(kind, public=True)
    def test_queue_routes_require_actual_owner(self):
        event=dict(rawPath=f'/research/studies/{STUDY}/queue',requestContext={'http':{'method':'GET'}},headers={})
        self.assertEqual(api.handler(event)['statusCode'],401)
        with patch.object(store,'owns',return_value=False):
            with self.assertRaises(api.HttpError) as caught: api.dispatch(event,ACTOR)
            self.assertEqual(caught.exception.status,404)
    def test_original_retrieval_rechecks_hash_and_size(self):
        artifact=str(uuid.uuid4()); row=dict(byte_count=3,storage_path=STUDY+'/'+digest(b'abc'),sha256=digest(b'abc'))
        with patch.object(store,'rows',return_value=[row]),patch.object(store,'request',return_value=b'abc'):
            self.assertEqual(store.artifact_bytes(STUDY,artifact),b'abc')
        with patch.object(store,'rows',return_value=[row]),patch.object(store,'request',return_value=b'bad'):
            with self.assertRaises(store.StoreError): store.artifact_bytes(STUDY,artifact)

class BoundedWorkers(unittest.TestCase):
    def test_two_slots_really_overlap_with_a_barrier(self):
        barrier=threading.Barrier(2,timeout=3); lock=threading.Lock(); observed=[]
        def execution(job):
            with lock: observed.append(threading.get_ident())
            barrier.wait(); return dict(status='awaiting-review')
        with patch.object(store,'rpc',side_effect=lambda name,payload:dict(id=str(uuid.uuid4()))), patch.object(worker,'execute_job',side_effect=execution):
            result=worker.handler({})
        self.assertEqual(len(set(observed)),2); self.assertEqual(len(result['outcomes']),2)
        self.assertTrue(all(r['status']=='awaiting-review' for r in result['outcomes']))
    def test_idle_and_failed_calls_are_not_retried(self):
        with patch.object(store,'rpc',return_value=None) as rpc:
            result=worker.handler({});self.assertEqual(rpc.call_count,2);self.assertTrue(all(r['status']=='idle' for r in result['outcomes']))
        with patch.object(store,'rpc',return_value=dict(id=str(uuid.uuid4()))) as rpc,patch.object(worker,'execute_job',side_effect=store.StoreError('failure')) as run:
            result=worker.handler({});self.assertEqual(rpc.call_count,2);self.assertEqual(run.call_count,2);self.assertTrue(all(r['status']=='completion-unconfirmed' for r in result['outcomes']))
    def test_discrepancy_is_recorded_for_review_without_assessing_claims(self):
        raw, protocol, campaign=fixture(); data=json.loads(raw); data['observed'][0]['value']=4
        raw=canonical(data); protocol['document']['input_versions'][0]['sha256']=digest(raw); protocol['sha256']=digest(canonical(protocol['document']))
        job=dict(id=str(uuid.uuid4()),study_id=STUDY,campaign_id=campaign['id'],input_artifact_id=str(uuid.uuid4()),request={'input_sha256':digest(raw)},definition_sha256='f'*64,dependencies=[],exclusive_resources=[],lease_token=str(uuid.uuid4()))
        def rows(kind,params):return [{'owner_id':ACTOR}] if kind=='study_owners' else [campaign] if kind=='campaigns' else [protocol]
        with patch.object(store,'rows',side_effect=rows),patch.object(store,'artifact_bytes',return_value=raw),patch.object(store,'artifact',return_value=dict(id=str(uuid.uuid4()),sha256='a'*64,byte_count=1,storage_path='private',media_type='application/json',provenance={},verified_at='2026-10-07T00:00:00Z')),patch.object(store,'rpc',return_value={'status':'awaiting-review'}) as rpc:
            worker.execute_job(job)
            arguments=rpc.call_args.args[1]; entries=arguments['p_records']
            run=next(e['record'] for e in entries if e['kind']=='runs')
            self.assertEqual(run['status'],'complete'); self.assertNotIn('assessments',[e['kind'] for e in entries]);self.assertIsNone(arguments['p_usage']['measured_charges_usd'])
            measurement=next(e['record'] for e in entries if e['kind']=='measurements'); self.assertEqual(measurement['details']['label'],'discrepant')
    def test_retrieval_failure_is_durable_job_failure_with_missing_usage(self):
        job=dict(id=str(uuid.uuid4()),study_id=STUDY,lease_token=str(uuid.uuid4()))
        with patch.object(store,'rows',side_effect=store.StoreError('private detail')),patch.object(store,'rpc',return_value={}) as rpc:
            worker.execute_job(job); args=rpc.call_args.args[1]
            self.assertIsNone(args['p_run']);self.assertEqual(args['p_records'],[]);self.assertNotIn('private detail',args['p_error'])

if __name__=='__main__': unittest.main()
