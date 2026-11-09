"""Controller branches preserve immutable identities and stop incomplete admissions."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy,autonomy_model as model,store
from workflow import canonical,digest
from test_autonomy import task,answer
from test_autonomy_methods import Recorded
from test_paper_selection import records
from test_network_traffic_allowance import built_protocol


class AutonomyAdmissionContracts(unittest.TestCase):
    def test_queued_job_without_a_run_remains_uncovered_in_the_manifest(self):
        current=task();job=dict(id='original-job',title='Original check',execution_mode='automatic',request={'original':True})
        with patch.object(autonomy,'inventory',return_value=[job]),patch.object(autonomy,'retained_jobs',return_value=[job]),patch.object(autonomy,'one') as one:
            value=autonomy.coverage_manifest(current)
        one.assert_not_called();self.assertIn('original-job',str(value));self.assertEqual(value['runs'],[])

    def test_initial_history_read_has_no_cursor_and_retained_check_context_does_not_invent_numeric_input(self):
        current=task();current['attempts']=0
        with patch.object(autonomy,'one',return_value=current),patch.object(autonomy,'rows',return_value=[]) as rows:
            history=autonomy.history(current['study_id'],current['id'])
        self.assertEqual(history['turns'],[]);self.assertNotIn('number',rows.call_args.kwargs)
        job=dict(id='original',title='Original network check',execution_mode='automatic',campaign_id='campaign',request=dict(raw_input='{}'))
        with patch.object(autonomy,'one',side_effect=[dict(id='campaign',protocol_id='protocol',adapter='network-execution-v1'),dict(id='protocol',document={})]):value=autonomy.check_context(job,current['study_id'])
        self.assertNotIn('retained_input_inventory',value)

    def test_review_organization_preserves_changed_inventory_and_noncomplete_admissions(self):
        values=records(1);review=dict(seeds=[dict(claim_id=values[0]['id'])],metadata={});current=task('intake');study=dict(id=current['study_id'],title='Original paper')
        with patch.object(model,'call') as call,self.assertRaisesRegex(ValueError,'inventory changed'):autonomy.organize_review(current,study,dict(review_selection=dict(cursor=0,candidate_inventory_sha256='a'*64)),review,values)
        call.assert_not_called()
        for result in [dict(denied='budget'),dict(status='invalid-output',reason='Original invalid grouping'),dict(status='complete',output=dict(action='blocked',reason='Original missing source',blockers=[]))]:
            with self.subTest(result=result),patch.object(model,'call',return_value=result),patch.object(autonomy,'advance',side_effect=lambda t,status,**kw:dict(status=status,**kw)),patch.object(store,'rpc') as rpc:
                outcome=autonomy.organize_review(current,study,{},review,values)
                self.assertEqual(outcome['status'],'budget' if result.get('denied') else 'queued' if result['status']=='invalid-output' else 'blocked');rpc.assert_not_called()

    def test_network_admission_rejects_unknown_methods_and_undeclared_metric_coverage(self):
        for defect in ['method','coverage']:
            db=Recorded(arithmetic_mode=False);proposal=copy.deepcopy(db.proposal)
            if defect=='method':proposal['experiment_type']='unknown-method'
            else:proposal['protocol']['metrics'][-1].pop('planned_observations')
            with self.subTest(defect=defect),patch.object(autonomy,'one',side_effect=db.one),patch.object(store,'artifact') as upload,self.assertRaisesRegex(ValueError,'applicable networking method|five TCP sweep metrics'):autonomy.validate_proposal(db.task,proposal)
            upload.assert_not_called()

    def test_frozen_built_package_uses_the_pinned_executor_without_inventing_builtin_source(self):
        for missing in [False,True]:
            db=Recorded(arithmetic_mode=False);proposal=copy.deepcopy(db.proposal);p=proposal['protocol'];built=built_protocol();p['execution']=built['execution'];p['resource_limits']['wall_seconds']=max(p['resource_limits']['wall_seconds'],p['execution']['limits']['wall_seconds']);p['configurations'][0]['details']['test_config']=built['configurations'][0]['details']['test_config']
            if missing:p['metrics'][-1].pop('planned_observations')
            with self.subTest(missing=missing),patch.object(autonomy,'one',side_effect=db.one),patch.object(autonomy,'rows',return_value=[]),patch.object(store,'artifact') as upload:
                if missing:
                    with self.assertRaisesRegex(ValueError,'explicit planned coverage'):autonomy.validate_proposal(db.task,proposal)
                else:self.assertEqual(autonomy.validate_proposal(db.task,proposal)['protocol']['execution']['runner'],'built-package-v1')
                upload.assert_not_called()

    def test_empty_network_configuration_cannot_bypass_registered_specification_admission(self):
        db=Recorded(arithmetic_mode=False);proposal=copy.deepcopy(db.proposal);proposal['protocol']['configurations']=[]
        with patch.object(autonomy,'one',side_effect=db.one),patch.object(store,'artifact') as upload,self.assertRaises(ValueError):autonomy.validate_proposal(db.task,proposal)
        upload.assert_not_called()

    def test_queue_prerequisites_use_their_actual_criterion_and_do_not_require_an_absent_proposal(self):
        current=task();jobs=[dict(id='original',campaign_id='campaign',execution_mode='automatic',request=dict(request_id='original',dependencies=[dict(job_id='parent',requirement='complete-run')],raw_input='{}'))]
        def one(kind,*args):return dict(protocol_id='protocol') if kind=='campaigns' else dict(document=dict(resource_limits=dict(wall_seconds=60)))
        with patch.object(autonomy,'frozen_jobs',return_value=jobs),patch.object(autonomy,'one',side_effect=one),patch.object(autonomy,'rows') as rows,patch.object(autonomy,'price',return_value=dict(cents=1,quote={})),patch.object(autonomy,'control',return_value={}),patch.object(autonomy.scheduler,'definition',return_value=(None,{},None)),patch.object(autonomy.scheduler,'enqueue') as enqueue,patch.object(autonomy,'advance',side_effect=lambda t,status,**kw:dict(status=status,**kw)):
            result=autonomy.enqueue(current)
        enqueue.assert_called_once();rows.assert_not_called();self.assertNotIn('decision_lock',result['context'])
        jobs[0]['request']['dependencies'][0]['requirement']='reviewed-evidence'
        with patch.object(autonomy,'frozen_jobs',return_value=jobs),patch.object(autonomy,'rows',return_value=[dict(status='reviewed')]),patch.object(autonomy,'one',side_effect=one),patch.object(autonomy,'price',return_value=dict(cents=1,quote={})),patch.object(autonomy,'control',return_value=dict(denied='budget')),patch.object(autonomy.scheduler,'definition',return_value=(None,{},None)),patch.object(autonomy,'advance',return_value=dict(status='budget')):
            self.assertEqual(autonomy.enqueue(current)['status'],'budget')

    def test_step_delegates_intake_and_refuses_missing_queue_identities(self):
        current=task('intake')
        with patch.object(autonomy,'intake',return_value=dict(status='original-intake')) as intake:self.assertEqual(autonomy.step(current),dict(status='original-intake'))
        intake.assert_called_once_with(current);current=task('execution');current['context']['job_ids']=['missing']
        with patch.object(autonomy,'rows',return_value=[]),self.assertRaisesRegex(ValueError,'Queue identity coverage'):autonomy.step(current)

    def test_historical_environment_check_without_incident_continues_the_same_controller_step(self):
        current=task();result=dict(denied='budget')
        with patch.object(autonomy,'build_context',return_value=dict(operational_environment_check=dict(checked_job_ids=['original'],incident=None))),patch.object(model,'call',return_value=result) as call,patch.object(autonomy,'advance',return_value=dict(status='budget')):
            self.assertEqual(autonomy.step(current)['status'],'budget')
        self.assertNotIn('operational_environment_check',call.call_args.args[1])


if __name__=='__main__':unittest.main()
