"""Incomplete or conflicting originals never become a conclusive numeric finding."""
import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy
import test_autonomy_followups as fixtures


class NumericFindingContracts(unittest.TestCase):
    def test_a_completed_job_outside_the_frozen_check_inventory_cannot_close_the_claim(self):
        db=fixtures.Retained();extra=copy.deepcopy(db.data['queue_jobs'][db.jobs[0]['id']]);extra['id']='unregistered-original-job';db.data['queue_jobs'][extra['id']]=extra;db.task['context']['completed_job_ids'].append(extra['id']);a,b,c=db.mocks()
        with a,b,c:self.assertIsNone(autonomy.numeric_finding(db.task))

    def test_each_missing_identity_review_or_original_criterion_prevents_closure(self):
        defects=['non-numerical','manual-check','unregistered-adapter','published-source','published-status','published-metric','conflicting-cell','missing-review','wrong-review-task','missing-job','missing-run','failed-current-run','empty-measurements','changed-input','changed-protocol','missing-published','missing-published-link','invalid-publication','nonfinite-value','excessive-precision']
        for defect in defects:
            with self.subTest(defect=defect):
                db=fixtures.Retained();first=db.jobs[0];row=next(iter(db.data['measurements'].values()));claim=db.data['claims'][db.claim['id']]
                campaign=db.data['campaigns'][first['campaign_id']];protocol=db.data['protocols'][campaign['protocol_id']]
                if defect=='non-numerical':claim['claim_type']='qualitative'
                elif defect=='manual-check':first['execution_mode']='manual'
                elif defect=='unregistered-adapter':
                    next(e for e in db.compiled['records'] if e['kind']=='campaigns' and e['record']['id']==first['campaign_id'])['record']['adapter']='other-adapter'
                elif defect.startswith('published-'):
                    data=json.loads(first['request']['raw_input']);published=data['published'][0]
                    published[{'published-source':'source_id','published-status':'status','published-metric':'metric'}[defect]]='different'
                    first['request']['raw_input']=json.dumps(data)
                elif defect=='conflicting-cell':
                    data=json.loads(db.jobs[1]['request']['raw_input']);original=json.loads(first['request']['raw_input'])['published'][0];data['published'].append(dict(original,value=float(original['value'])+1));db.jobs[1]['request']['raw_input']=json.dumps(data)
                elif defect=='missing-review':db.task['context']['reviews']=[]
                elif defect=='wrong-review-task':next(iter(db.data['autonomy_turns'].values()))['task_id']='foreign-task'
                elif defect=='missing-job':db.data['queue_jobs'].pop(first['id'])
                elif defect=='missing-run':db.data['queue_jobs'][first['id']]['run_id']=None
                elif defect=='failed-current-run':db.data['runs'][db.jobs[-1]['id']]['status']='failed'
                elif defect=='empty-measurements':db.data['measurements']={key:value for key,value in db.data['measurements'].items() if value['run_id']!=first['id']}
                elif defect=='changed-input':db.data['runs'][first['id']]['input_sha256']='a'*64
                elif defect=='changed-protocol':protocol['document']['statistics']='Changed after the frozen check'
                elif defect=='missing-published':db.data['published_values'].pop(row['published_id'])
                elif defect=='missing-published-link':row['published_id']=None
                elif defect=='invalid-publication':db.data['published_values'][row['published_id']]['status']='ambiguous'
                elif defect=='nonfinite-value':row['value']='NaN'
                elif defect=='excessive-precision':row['value']='1e10001'
                before=copy.deepcopy((db.task,db.data));a,b,c=db.mocks()
                with a,b,c:self.assertIsNone(autonomy.numeric_finding(db.task))
                self.assertEqual((db.task,db.data),before)

    def unregistered(self):
        db=fixtures.Retained();first=db.jobs[0];context=db.task['context'];context.pop('prepared_id');context.update(job_ids=[first['id']],completed_job_ids=[],reviews=context['completed_reviews'][first['id']])
        for review in context['reviews']:review['coverage_complete']=True
        campaign=db.data['campaigns'][first['campaign_id']];protocol=db.data['protocols'][campaign['protocol_id']]
        claim=db.data['claims'][db.claim['id']];claim['metrics']=[m['name'] for m in protocol['document']['metrics']]
        measurements=[row for row in db.data['measurements'].values() if row['run_id']==first['id']]
        for metric in protocol['document']['metrics']:metric['planned_observations']=sum(row['metric']==metric['name'] for row in measurements)
        return db,protocol

    def test_unregistered_comparison_requires_complete_reviews_matching_metrics_and_declared_sample_counts(self):
        db,_=self.unregistered();a,b,c=db.mocks()
        with a,b,c:self.assertIsNotNone(autonomy.numeric_finding(db.task))
        for defect in ['review-coverage','metric-set','sample-count','empty-metric-declaration']:
            with self.subTest(defect=defect):
                db,protocol=self.unregistered()
                if defect=='review-coverage':db.task['context']['reviews'][0]['coverage_complete']=False
                elif defect=='metric-set':db.data['claims'][db.claim['id']]['metrics']=['foreign-metric']
                elif defect=='sample-count':protocol['document']['metrics'][0]['planned_observations']=None
                else:db.data['claims'][db.claim['id']]['metrics']=[]
                a,b,c=db.mocks()
                with a,b,c:self.assertIsNone(autonomy.numeric_finding(db.task))


if __name__=='__main__':unittest.main()
