"""Structured controller replies remain data until the exact phase contract passes."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_model as model
import experiment_builder as builder
from test_autonomy import answer,response,task


def inspect():
    return dict(answer('inspect'),reason='retained-evidence',read_request={'kind':'source','id':str(uuid.uuid4()),'cursor':0})


class ControllerResponseContracts(unittest.TestCase):
    def test_multiple_exact_source_candidates_and_explicit_missing_metadata_remain_data(self):
        value=dict(answer('discover'),reason='text-extracted',claims=[dict(page=1,quote='Original first statement',claim_type='numerical'),dict(page=2,quote='Original second statement',claim_type='numerical')],paper_metadata=dict(title=None,domain=None))
        self.assertEqual(len(model.parse(response(value))['claims']),2)
        with patch.object(model,'load_prompt',return_value=(dict(id=str(uuid.uuid4())),'Original system')):
            result=model.call(task('discover'),dict(task='discover',paper_metadata_requested=True),lambda *_:dict(replayed=False),lambda _:response(value))
        self.assertEqual(result['status'],'complete');self.assertEqual(result['output']['paper_metadata'],dict(title=None,domain=None))

    def test_oversized_native_builder_context_stops_before_turn_admission(self):
        admission=Mock();policy=dict(version='original',sha256='a'*64,document=dict(instruction='Original fixed authority'))
        with patch.object(model,'OPENCLAW',True),patch.object(builder,'enabled',return_value=True),patch.object(builder,'action_policy',return_value=policy),patch.object(model,'load_prompt',return_value=(dict(id=str(uuid.uuid4())),'Original system')),self.assertRaisesRegex(ValueError,'Builder context exceeds'):
            model.call(task('building'),dict(task='building',original='x'*100000),admission,Mock())
        admission.assert_not_called()

    def test_malformed_or_action_bearing_inspection_is_rejected_before_execution(self):
        for defect in ['envelope','claim','read-shape','inspection-readiness','noninspection-read','evidence-id','empty-blocker','rationale']:
            with self.subTest(defect=defect):
                value=inspect();result=response(value)
                if defect=='envelope':result['stopReason']='end_turn'
                elif defect=='claim':value.update(action='discover',reason='text-extracted',read_request=None,claims=[{'page':True,'quote':'Original','claim_type':'numerical'}])
                elif defect=='read-shape':value['read_request']['cursor']=-1
                elif defect=='inspection-readiness':value['ready']=True
                elif defect=='noninspection-read':value.update(action='propose',reason='prospective-pilot')
                elif defect=='evidence-id':value['evidence_ids']=[42]
                elif defect=='empty-blocker':value.update(action='blocked',reason='missing-required-inputs',read_request=None)
                elif defect=='rationale':value['reason']='evidence-incomplete'
                before=copy.deepcopy(result)
                with self.assertRaises(ValueError):model.parse(result)
                self.assertEqual(result,before)

    def test_phase_claims_readiness_and_model_or_prompt_identity_cannot_be_substituted(self):
        for patch_value in [{'claims':[{'page':1,'quote':'Original claim','claim_type':'numerical'}]},{'ready':True}]:
            value=dict(answer('propose'),**patch_value)
            with self.assertRaises(ValueError):model.validate_phase(value,{'task':'planning'})
        with patch.object(model,'OPENCLAW',True),self.assertRaisesRegex(ValueError,'Unexpected subscription model'):model.reservation({'modelId':'different'})
        with patch.object(model.store,'request',return_value=[]),self.assertRaisesRegex(ValueError,'evaluated published prompt'):model.load_prompt()

    def test_missing_phase_metadata_grouping_and_readiness_remain_retained_invalid_output(self):
        cases=[({'task':'discover','paper_metadata_requested':True},dict(answer('discover'),reason='text-extracted'),'requires paper metadata'),
            ({'task':'paper-review-selection','paper_review_selection_requested':True},dict(answer('discover'),reason='text-extracted'),'explicit review groups'),
            ({'task':'planning'},dict(inspect(),paper_review_selection={'central_claims':[],'testable_findings':[]}),'grouping was not requested'),
            ({'task':'planning'},dict(answer('review'),reason='unresolved-method'),'requires one of'),
            ({'task':'critic'},dict(answer('review'),reason='unresolved-method'),'explicit boolean readiness')]
        for context,value,message in cases:
            with self.subTest(message=message):
                provider=Mock(return_value=response(value));receipts=[]
                def control(current,action,payload):receipts.append((action,copy.deepcopy(payload)));return {'replayed':False}
                with patch.object(model,'load_prompt',return_value=({'id':'synthetic-prompt'},'Original controller policy')):
                    result=model.call(task(context['task']),context,control,provider)
                self.assertEqual(result['status'],'invalid-output');self.assertIn(message,result['reason']);self.assertIsNone(result['output']);provider.assert_called_once();self.assertIsNotNone(receipts[-1][1]['raw_response'])

    def test_inflight_admission_and_oversized_resource_authorization_never_call_a_provider(self):
        provider=Mock()
        with patch.object(model,'load_prompt',return_value=({'id':'synthetic'},'Policy')),self.assertRaisesRegex(ValueError,'unconfirmed for this claim'):
            model.call(task(),{},lambda *args:{'denied':'in-flight'},provider)
        with patch.object(model,'OPENCLAW',True),patch.object(builder,'enabled',return_value=True),patch.object(model,'load_prompt',return_value=({'id':'synthetic'},'Original builder policy')),patch.object(builder,'action_policy',return_value={'document':{'instruction':'Fixed authorized actions'},'version':'synthetic','sha256':'a'*64}),patch.object(model,'resource_instruction',return_value='x'*96001),self.assertRaisesRegex(ValueError,'including resource authorization'):
            model.call(task('building'),{'task':'building'},lambda *args:None,provider)
        provider.assert_not_called()


if __name__=='__main__':unittest.main()
