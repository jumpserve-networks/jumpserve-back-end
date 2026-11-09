"""Controller transitions cannot skip source-bound planning and complete reviews."""
import contextlib
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy,experiment_builder
import autonomy_read_progress
from test_autonomy import task,answer


class AutonomyReviewBoundaries(unittest.TestCase):
    def step(self,stage,context,output,**options):
        current=task(stage);current['context']=copy.deepcopy(context)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(autonomy,'build_context',return_value={}))
            stack.enter_context(patch.object(autonomy.model,'call',return_value={'id':'retained-turn','status':'complete','output':output}))
            advance=stack.enter_context(patch.object(autonomy,'advance',side_effect=lambda *args,**kwargs:{'status':args[1],**kwargs}))
            enqueue=stack.enter_context(patch.object(autonomy,'enqueue',return_value={'enqueued':True}))
            stack.enter_context(patch.object(autonomy,'choices',return_value=({},[{'id':'original-check'}])))
            stack.enter_context(patch.object(autonomy,'validate_proposal',side_effect=lambda task,value:copy.deepcopy(value)))
            stack.enter_context(patch.object(autonomy,'inventory',return_value=[{'id':'original-check'}]))
            stack.enter_context(patch.object(autonomy,'check_context',return_value={'protocol':options.get('retained_protocol',{})}))
            stack.enter_context(patch.object(experiment_builder,'project_document',return_value=options.get('package')))
            result=autonomy.step(current)
        return result,current,advance,enqueue

    def test_planning_selects_an_unused_check_then_requires_fresh_reviews(self):
        output=dict(answer('choose-check'),check_id='original-check')
        for context in [{},{'prepared_id':'original-plan','tried_check_ids':['original-check']}]:
            with self.assertRaises(ValueError):self.step('planning',context,output)
        result,_task,_advance,_enqueue=self.step('planning',{'prepared_id':'original-plan','proposal':{'old':True},'reviews':['old'],'protocol_reads':{'0':None},'package_reads':{'0':None}},output)
        self.assertEqual(result['stage'],'comprehension');self.assertEqual(result['context']['selected_check_id'],'original-check');self.assertNotIn('proposal',result['context']);self.assertEqual(result['context']['reviews'],[]);self.assertNotIn('protocol_reads',result['context'])
        output['check_id']='foreign-check'
        with self.assertRaises(ValueError):self.step('planning',{'prepared_id':'original-plan'},output)

    def test_identical_design_cannot_repeat_without_addressing_retained_feedback(self):
        candidate={'experiment_type':'independent-check','protocol':{'hypotheses':'Original claim','configurations':[]}}
        output=dict(answer('propose'),proposal=candidate)
        result,*_=self.step('planning',{},output)
        self.assertEqual(result['stage'],'comprehension');self.assertEqual(result['context']['proposal'],candidate)
        repeated,*_=self.step('planning',{'prior_design_hashes':result['context']['prior_design_hashes']},output)
        self.assertEqual(repeated['status'],'blocked');self.assertIn('same design',repeated['reason'])
        with self.assertRaisesRegex(ValueError,'Planning requires'):self.step('planning',{},answer('review'))
        built,*_=self.step('building',{},output);self.assertEqual(built['stage'],'comprehension');self.assertEqual(built['context']['proposal'],candidate)

    def test_large_prospective_protocol_must_be_read_to_its_recorded_end_before_readiness(self):
        protocol={'original':'x'*16001};candidate={'protocol':protocol};output=dict(answer('review'),ready=True,coverage_complete=True)
        result,*_=self.step('comprehension',{'proposal':candidate},output)
        self.assertIn('protocol review is incomplete',result['reason'])
        result,*_=self.step('comprehension',{'proposal':candidate,'protocol_reads':{'0':500,'500':None}},output)
        self.assertEqual(result['stage'],'critic');self.assertEqual(len(result['context']['reviews']),1);self.assertNotIn('protocol_reads',result['context'])
        result,*_=self.step('critic',{'proposal':candidate,'protocol_reads':{'0':None}},output)
        self.assertEqual(result,{'enqueued':True})

    def test_large_candidate_package_requires_its_own_complete_source_and_lock_review(self):
        output=dict(answer('review'),ready=True,coverage_complete=True);context={'proposal':{'protocol':{}}}
        result,*_=self.step('critic',context,output,package={'original':'x'*16001})
        self.assertIn('Package source/lock review is incomplete',result['reason'])
        context['package_reads']={'0':100,'100':None}
        result,*_=self.step('critic',context,output,package={'original':'x'*16001})
        self.assertEqual(result,{'enqueued':True})

    def test_retained_check_protocol_is_not_assumed_reviewed_from_a_summary(self):
        output=dict(answer('review'),ready=True,coverage_complete=True);context={'selected_check_id':'original-check'}
        result,*_=self.step('comprehension',context,output,retained_protocol={'original':'x'*32001})
        self.assertIn('Protocol review coverage is incomplete',result['reason'])
        context['protocol_reads']={'0':15000,'15000':None}
        result,*_=self.step('comprehension',context,output,retained_protocol={'original':'x'*32001})
        self.assertEqual(result['stage'],'critic')
        result,*_=self.step('comprehension',{'selected_check_id':'original-check'},output,retained_protocol={'original':'Small complete protocol'})
        self.assertEqual(result['stage'],'critic')

    def test_assessment_rejects_nonjudgment_actions_and_keeps_an_unregistered_gap_open(self):
        with self.assertRaisesRegex(ValueError,'Assessment requires'):self.step('assessment',{},answer('review'))
        with patch.object(autonomy,'numeric_finding',return_value=None):result,*_=self.step('assessment',{'job_ids':['original']},answer('assess'))
        self.assertEqual(result['stage'],'planning');self.assertEqual(result['label'],'inconclusive');self.assertEqual(result['context']['completed_job_ids'],['original']);self.assertNotIn('selected_check_id',result['context'])

    def test_rejected_reviews_return_specific_feedback_to_retained_builder_or_planner(self):
        output=dict(answer('review'),ready=False,blockers=[{'kind':'design','detail':'Original missing control'}],reason='Original source review finding')
        context={'builder_checkpoint':{'artifact_id':'original-checkpoint'},'proposal':{'protocol':{'execution':{'runner':'built-package-v1'}}}}
        result,*_=self.step('critic',context,output)
        self.assertEqual(result['stage'],'building');self.assertEqual(result['context']['builder_feedback']['reason'],output['reason']);self.assertEqual(result['context']['builder_checkpoint'],context['builder_checkpoint'])
        result,*_=self.step('comprehension',{},output)
        self.assertEqual(result['stage'],'planning');self.assertEqual(result['context']['last_feedback'],output['reason'])
        with self.assertRaisesRegex(ValueError,'Fresh review'):self.step('critic',{},answer('propose'))

    def test_explicit_protocol_and_package_reads_record_the_original_cursor_without_claiming_review_completion(self):
        for kind,key in [('protocol','protocol_reads'),('package','package_reads')]:
            request={'kind':kind,'cursor':0,'record_id':'original'};output=dict(answer('inspect'),read_request=request);read={'result':{'next_cursor':1500}}
            with patch.object(autonomy,'inspect_evidence',return_value=read) as inspect,patch.object(autonomy.inputs,'retain_source_delivery'),patch.object(autonomy_read_progress,'retain',return_value=None):result,current,*_=self.step('comprehension',{},output)
            self.assertEqual(result['context'][key],{'0':1500});self.assertEqual(result['context']['evidence_read']['requested_by_turn_id'],'retained-turn');self.assertEqual(result['status'],'queued');self.assertNotIn('reviews',result['context']);inspect.assert_called_once_with(current,request)
        with self.assertRaisesRegex(ValueError,'Unsupported durable claim stage'):self.step('unsupported',{},answer('review'))

    def test_execution_waits_on_original_jobs_then_moves_settled_records_to_assessment_without_relaunching(self):
        current=task('execution');current['context']={'job_ids':['original-job']}
        with patch.object(autonomy,'rows',return_value=[{'id':'original-job','status':'queued'}]),patch.object(autonomy,'advance',side_effect=lambda *args,**kwargs:{'status':args[1],**kwargs}):
            result=autonomy.step(current);self.assertEqual(result['status'],'waiting');self.assertEqual(result['delay'],30)
        with patch.object(autonomy,'rows',return_value=[{'id':'original-job','status':'failed'}]),patch.object(autonomy,'advance',side_effect=lambda *args,**kwargs:{'status':args[1],**kwargs}):
            result=autonomy.step(current);self.assertEqual(result['stage'],'assessment');self.assertEqual(result['label'],'inconclusive')


if __name__=='__main__':unittest.main()
