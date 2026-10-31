"""Development checks: real archived arithmetic and loopback TCP, no AWS calls."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomy, autonomy_evidence as evidence, autonomy_model as model, autonomy_costs
import bounded_network, network_execution, preparation, runner, workflow
from test_autonomy import task


class Retained:
    def __init__(self):
        raw=(Path(preparation.__file__).parent/'plans/ipv6-archive-v1.json').read_bytes()
        plan=json.loads(raw);self.task=task('assessment')
        self.compiled=preparation.compile_plan(plan,raw,dict(id=self.task['study_id'],paper_url=plan['paper_urls'][0]))
        self.data={}
        for entry in self.compiled['records']:
            self.put(entry['kind'],entry['record'])
        self.claim=next(r for r in self.data['claims'].values() if r['description']=='Published absolute folded metrics')
        self.task['claim_id']=self.claim['id'];self.jobs=[j for j in self.compiled['jobs'] if j['execution_mode']=='automatic']
        self.task['context']=dict(prepared_id=self.compiled['id'],job_ids=[],completed_job_ids=[],completed_reviews={})
        for job in self.jobs:
            campaign=self.one('campaigns',None,job['campaign_id']);protocol=self.one('protocols',None,campaign['protocol_id'])
            result=runner.execute(job['request']['raw_input'].encode(),protocol,campaign,job['id'])
            if result['run']['status']!='complete':raise AssertionError(result['run'])
            self.put('runs',result['run'])
            self.put('queue_jobs',dict(id=job['id'],campaign_id=job['campaign_id'],run_id=job['id']))
            for kind in ('measurements','published_values'):
                for row in result[kind]:self.put(kind,row)
            reviews=[]
            for role in ('comprehension','critic'):
                turn=str(uuid.uuid5(uuid.UUID(job['id']),role))
                self.put('autonomy_turns',dict(id=turn,task_id=self.task['id'],stage=role,status='complete',output={'ready':True}))
                reviews.append(dict(turn_id=turn,role=role,coverage_complete=False))
            self.task['context']['completed_reviews'][job['id']]=reviews
        current=self.jobs[-1]['id'];self.task['context'].update(job_ids=[current],completed_job_ids=[j['id'] for j in self.jobs[:-1]],selected_check_id=current,reviews=self.task['context']['completed_reviews'][current])

    def put(self,kind,row):self.data.setdefault(kind,{})[row['id']]=copy.deepcopy(row)
    def one(self,kind,study,key):return copy.deepcopy(self.data[kind][key])
    def rows(self,kind,study,**params):
        result=list(self.data.get(kind,{}).values())
        for key,value in params.items():
            if key in ('order','limit','select','created_at'):continue
            if key=='and':result=[r for r in result if r['id']>value.removeprefix('(id.gt.').removesuffix(')')]
            elif value.startswith('eq.'):result=[r for r in result if r.get(key)==value[3:]]
            elif value.startswith('in.'):result=[r for r in result if r.get(key) in value[4:-1].split(',')]
            else:raise AssertionError(params)
        return sorted(copy.deepcopy(result),key=lambda r:r['id'])[:params.get('limit',1000)]
    def mocks(self):
        return (patch.object(autonomy,'one',side_effect=self.one),patch.object(autonomy,'rows',side_effect=self.rows),patch.object(autonomy,'choices',return_value=(self.compiled,self.jobs)))

    def partial_alternative(self):
        """Controlled development mutation, never an archived-paper result."""
        first=self.jobs[0];alternative=copy.deepcopy(first);alternative['id']=str(uuid.uuid5(uuid.UUID(self.task['study_id']),'controlled-alternative'));alternative['request']['request_id']=alternative['id']
        modified=json.loads(first['request']['raw_input']);modified['observed'][0]['value']=float(modified['published'][0]['value'])+1
        modified['observed'].pop(1);first['request']['raw_input']=workflow.canonical(modified).decode()
        campaign=self.one('campaigns',None,first['campaign_id']);protocol=self.one('protocols',None,campaign['protocol_id'])
        protocol['document']['input_versions'].append(dict(identity='Controlled development mutation; not scientific evidence',sha256=workflow.digest(first['request']['raw_input'].encode())))
        protocol['document']['prior_exposure']='Exposed controlled partial-run mutation to test preservation of earlier discrepancies; no new paper finding.'
        protocol['sha256']=workflow.digest(workflow.canonical(protocol['document']));self.put('protocols',protocol)
        for entry in self.compiled['records']:
            if entry['kind']=='protocols' and entry['record']['id']==protocol['id']:entry['record']=copy.deepcopy(protocol)
        self.jobs.append(alternative)
        for job in (first,alternative):
            result=runner.execute(job['request']['raw_input'].encode(),protocol,campaign,job['id'])
            self.put('runs',result['run']);self.put('queue_jobs',dict(id=job['id'],campaign_id=job['campaign_id'],run_id=job['id']))
            for kind in ('measurements','published_values'):
                for row in result[kind]:self.put(kind,row)
        reviews=[]
        for role in ('comprehension','critic'):
            turn=str(uuid.uuid5(uuid.UUID(alternative['id']),role));self.put('autonomy_turns',dict(id=turn,task_id=self.task['id'],stage=role,status='complete',output={'ready':True}))
            reviews.append(dict(turn_id=turn,role=role,coverage_complete=False))
        self.task['context'].update(job_ids=[alternative['id']],completed_job_ids=[j['id'] for j in self.jobs[:-1]],selected_check_id=alternative['id'],reviews=reviews)
        return self


class Followups(unittest.TestCase):
    def test_valid_discrepancy_in_partial_run_survives_later_complete_agreement(self):
        db=Retained().partial_alternative();a,b,c=db.mocks()
        with a,b,c:result=autonomy.numeric_finding(db.task)
        self.assertIsNotNone(result);self.assertEqual(result[0],'discrepant')
        self.assertEqual(result[2]['required_cells'],1152);self.assertEqual(result[2]['covered_cells'],1152)
        self.assertEqual(db.data['runs'][db.jobs[0]['id']]['status'],'partial')
        self.assertEqual(db.data['runs'][db.jobs[-1]['id']]['status'],'complete')
        self.assertTrue(any(r['kind']=='runs' and r['id']==db.jobs[0]['id'] for r in result[1]))
    def test_actual_six_figure_arithmetic_covers_1152_cells_without_claim_metric_invention(self):
        db=Retained();self.assertEqual(db.claim['metrics'],[])
        a,b,c=db.mocks()
        with a,b,c:result=autonomy.numeric_finding(db.task)
        self.assertIsNotNone(result);label,refs,coverage=result
        self.assertIn(label,('reproduced','discrepant'));self.assertEqual(coverage['required_cells'],1152);self.assertEqual(coverage['covered_cells'],1152)
        self.assertEqual(sum(v['record_count'] for v in coverage['record_inventories']),1152)
        self.assertEqual(len([r for r in refs if r['kind']=='measurements']),1152)
        self.assertEqual(db.claim['metrics'],[])

    def test_one_figure_followup_has_no_stale_candidate_or_measurement_payload(self):
        db=Retained();t=db.task;t['stage']='planning';first=db.jobs[0]['id']
        t['context'].update(job_ids=[],completed_job_ids=[first],selected_check_id=first,tried_check_ids=[first])
        a,b,c=db.mocks()
        with a,b,c:context=autonomy.build_context(t)
        self.assertNotIn('candidate',context);self.assertNotIn('measurements',context)
        self.assertEqual(len(context['registered_checks']),5);self.assertEqual(context['evidence_coverage']['completed_count'],1)
        request=model.request('Server-owned scientific policy '*1000,context)
        self.assertLessEqual(len(workflow.canonical(request)),96000)
        self.assertEqual(len(db.data['measurements']),1152)

    def test_incomplete_missing_invalid_units_or_modified_published_values_prevent_closure(self):
        for mode in ('one-figure','missing','invalid','units','published-value'):
            with self.subTest(mode=mode):
                db=Retained();row=next(iter(db.data['measurements'].values()))
                if mode=='one-figure':db.task['context']['completed_job_ids']=[]
                elif mode=='missing':del db.data['measurements'][row['id']]
                elif mode=='invalid':row.update(status='invalid',value=None)
                elif mode=='units':row['units']='ms'
                else:db.data['published_values'][row['published_id']]['value']='987654321'
                a,b,c=db.mocks()
                with a,b,c:self.assertIsNone(autonomy.numeric_finding(db.task))

    def test_paginated_exact_inventory_keeps_null_zero_and_precision(self):
        records=[dict(id=f'{i:08}',metric='latency',units='ms',value=None if i==0 else '0' if i==1 else '0.00000000000000000000001',status='missing' if i==0 else 'recorded') for i in range(1152)]
        calls=[]
        def read(kind,study,**params):
            calls.append(params);cursor=params.get('and','(id.gt.)')[7:-1]
            return [r for r in records if r['id']>cursor][:params['limit']]
        result=evidence.collect(read,'measurements','study');self.assertEqual(result,records);self.assertEqual(len(calls),4)
        self.assertEqual(evidence.manifest(result)['records_sha256'],workflow.digest(workflow.canonical(records)))
        self.assertIsNone(result[0]['value']);self.assertEqual(result[1]['value'],'0')
        with self.assertRaises(ValueError):evidence.collect(lambda *a,**k:records[:500],'measurements','study')

    def test_scoped_readers_reject_cross_claim_ids_and_report_pages_explicitly(self):
        db=Retained();a,b,c=db.mocks()
        with a,b,c:
            with self.assertRaisesRegex(ValueError,'outside'):autonomy.inspect_evidence(db.task,dict(kind='measurements',id=str(uuid.uuid4()),cursor=0))
            result=autonomy.inspect_evidence(db.task,dict(kind='measurements',id=db.jobs[0]['id'],cursor=0))
        page=result['result'];self.assertEqual(page['total_records'],192);self.assertLessEqual(page['returned_records'],20);self.assertIsNotNone(page['next_cursor']);self.assertFalse(page['complete'])
        self.assertLessEqual(len(workflow.canonical(page)),evidence.READ_BYTES)

    def test_cost_admission_requires_current_policy_and_immutable_version(self):
        spec=workflow.canonical(dict(runner='bounded-tcp-v1',config=dict(payload_bytes=100,exchanges=2,application_delay_ms=0)))
        policy=json.loads(autonomy_costs.POLICY_PATH.read_bytes())['bounded_tcp']
        env=dict(NETWORK_BOUNDED_FUNCTION='arn:aws:lambda:us-east-1:395567831870:function:BoundedTcp:1',NETWORK_BOUNDED_POLICY_SHA256=workflow.digest(workflow.canonical(policy)))
        with patch.dict(os.environ,env):
            quote=autonomy_costs.bounded_quote(spec,today=dt.datetime(2026,10,19,tzinfo=dt.timezone.utc));self.assertEqual(quote['cents'],1);self.assertEqual(quote['quote']['spec_sha256'],workflow.digest(spec))
            with self.assertRaisesRegex(ValueError,'expired'):autonomy_costs.bounded_quote(spec,today=dt.datetime(2027,1,1,tzinfo=dt.timezone.utc))
        with patch.dict(os.environ,dict(env,NETWORK_BOUNDED_FUNCTION=env['NETWORK_BOUNDED_FUNCTION'].removesuffix(':1'))):
            with self.assertRaisesRegex(ValueError,'immutable'):autonomy_costs.bounded_quote(spec)

    def test_actual_tcp_exchanges_preserve_bytes_units_and_close_sockets(self):
        for size,count,delay in ((1,1,0),(65536,16,1)):
            result=bounded_network.handler(dict(version='bounded-tcp-v1',config=dict(payload_bytes=size,exchanges=count,application_delay_ms=delay)))
            self.assertEqual(len(result['observations']),count*2)
            self.assertTrue(all(r['value']==size for r in result['observations'] if r['metric']=='received_bytes'))
            self.assertTrue(all(r['value']>=0 for r in result['observations'] if r['metric']=='round_trip_time_ms'))
            self.assertEqual(result['usage']['application_payload_bytes'],2*size*count);self.assertEqual(result['usage']['remote_network_bytes'],0)
            self.assertLess(len(workflow.canonical(result)),32000);self.assertIn('closed',result['provenance']['cleanup'])
        for bad in (dict(payload_bytes=65537,exchanges=1,application_delay_ms=0),dict(payload_bytes=1,exchanges=True,application_delay_ms=0),dict(url='https://example.org')):
            with self.assertRaises(ValueError):bounded_network.validate(bad)

    def test_uncertain_bounded_dispatch_never_invokes_again(self):
        state=dict(phase='dispatching',deadline='2099-01-01T00:00:00Z',cancel_requested=False)
        with patch.object(store:=network_execution.store,'rpc') as rpc,patch.object(store,'rows',return_value=[]):
            self.assertEqual(network_execution.bounded_response({'id':str(uuid.uuid4()),'study_id':str(uuid.uuid4())},state,{'runner':'bounded-tcp-v1'}),dict(status='launch-unconfirmed'));rpc.assert_not_called()

if __name__=='__main__':unittest.main()
