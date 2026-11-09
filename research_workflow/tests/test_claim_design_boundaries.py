"""Paper-origin selection and adoption preserve source, policy and owner decisions."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import claim_design as design,ideas,store,workflow
import test_gap_queue as fixtures
from test_ideas import plan


class ClaimDesignBoundaries(unittest.TestCase):
    setUp=fixtures.GapQueue.setUp

    def candidate(self):
        value=plan();value['hypothesis']=self.claim['description'];value['falsification']=design.POLICY['decision_rule']
        value['protocol'].update(hypotheses=self.claim['description'],stage='pilot',experiment_type='independent-check',statistics=design.POLICY['statistics'],decision_policy=design.POLICY_IDENTITY)
        value['protocol']['input_versions'].append(dict(identity='Original paper bytes',sha256=self.source['sha256']))
        return value

    def test_gap_selection_requires_current_hash_and_original_bound_context(self):
        preview=design.gap_queue.preview(self.study['id'],fixtures.ACTOR,self.gap['id'])
        body=dict(action='design',request_id=str(uuid.uuid4()),gap_id=self.gap['id'],gap_sha256=preview['gap_sha256'])
        with patch.object(store,'rpc',return_value={}) as rpc,patch.object(ideas,'save'):
            result=design.start(self.study['id'],fixtures.ACTOR,body)
            self.assertEqual(result['version'],design.VERSION)
            original=next(call.args[1]['p_document'] for call in rpc.call_args_list if call.args[0]=='research_initialize_claim_design')
            self.assertEqual(original['gap'],self.gap);self.assertEqual(original['claim']['description'],self.claim['description'])
        for change in [{'gap_sha256':'0'*64},{'action':'execute'},{'extra':True}]:
            with patch.object(store,'rpc') as rpc,self.assertRaises(ValueError):design.start(self.study['id'],fixtures.ACTOR,dict(body,**change))
            rpc.assert_not_called()
        self.source['findings']='x'*48001
        preview=design.gap_queue.preview(self.study['id'],fixtures.ACTOR,self.gap['id']);body['gap_sha256']=preview['gap_sha256']
        with self.assertRaisesRegex(ValueError,'retained design budget'):design.start(self.study['id'],fixtures.ACTOR,body)

    def test_binding_refuses_inferential_stage_changed_hypothesis_or_unidentified_execution(self):
        retained={'document':{'claim':self.claim,'source':self.source}}
        for change in [{'stage':'main'},{'hypotheses':'Changed hypothesis'},{'statistics':'Inferential finding'}]:
            value=self.candidate();value['protocol'].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):design.validate_binding(value,retained)
        value=self.candidate();value['protocol']['execution']={'runner':'custom-package-v1'}
        with self.assertRaisesRegex(ValueError,'original source bytes'):design.validate_binding(value,{'document':{'claim':self.claim,'source':dict(self.source,sha256=None)}})
        value=self.candidate();value['protocol']['input_versions']=[]
        with self.assertRaisesRegex(ValueError,'selected original source hash'):design.validate_binding(value,retained)
        with patch.object(store,'rows',return_value=[]),self.assertRaisesRegex(ValueError,'no retained'):design.context(self.study['id'])

    def test_save_accept_and_status_use_bound_current_packet_and_explicit_missingness(self):
        value=self.candidate();retained={'document':{'claim':self.claim,'source':self.source}}
        with patch.object(design,'context',return_value=retained),patch.object(ideas,'save',return_value={'saved':True}) as save:
            body={'document':value};self.assertEqual(design.save(self.study['id'],fixtures.ACTOR,body),{'saved':True});self.assertEqual(save.call_args.args[2],body)
        with patch.object(design,'context',return_value=retained),patch.object(ideas,'packet',return_value={'document':value}),patch.object(ideas,'accept',return_value={'accepted':True}):
            self.assertEqual(design.accept(self.study['id'],fixtures.ACTOR,{'packet_id':str(uuid.uuid4())}),{'accepted':True})
        value['protocol']=None
        with patch.object(design,'context',return_value=retained),patch.object(ideas,'packet',return_value={'document':value}),self.assertRaisesRegex(ValueError,'remains incomplete'):design.accept(self.study['id'],fixtures.ACTOR,{'packet_id':str(uuid.uuid4())})
        state={'packets':[{'id':'current','document':value}],'current_packet_id':'current','ready':True,'missing':[]}
        with patch.object(design,'context',return_value=retained),patch.object(ideas,'status',side_effect=lambda _:copy.deepcopy(state)):
            result=design.status(self.study['id']);self.assertFalse(result['ready']);self.assertTrue(result['missing']);self.assertEqual(result['claim_design'],retained)
        weakened=dict(value,hypothesis='Changed original claim');self.assertTrue(any('exact paper claim' in item for item in design.missing(weakened,retained)))

    def test_retained_proposal_adoption_rejects_open_questions_and_drift_and_reuses_exact_packets(self):
        packet_id,run_id,request_id=(str(uuid.uuid4()) for _ in range(3));value=self.candidate();retained={'document':{'claim':self.claim,'source':self.source}}
        policy=dict(design.POLICY_IDENTITY,origin='server-policy',model_authored=False)
        output=dict(proposal=value,response_version=design.RESPONSE_VERSION,response_mode='reuse',decision_policy=policy,questions=[])
        row=dict(status='complete',task='planning',packet_id=packet_id,output=output)
        body=dict(action='adopt',request_id=request_id,run_id=run_id,packet_id=packet_id)
        with patch.object(store,'rows',side_effect=lambda *args:[copy.deepcopy(row)]),patch.object(design,'context',return_value=retained),patch.object(ideas,'packet',side_effect=lambda *args:{'document':copy.deepcopy(value)}),patch.object(store,'rpc',return_value={'reused':True}) as rpc:
            self.assertEqual(design.adopt(self.study['id'],fixtures.ACTOR,body),{'reused':True});self.assertEqual(rpc.call_args.args[0],'research_reuse_claim_packet')
            output['questions']=['Original unresolved question']
            with self.assertRaisesRegex(ValueError,'Resolve the retained'):design.adopt(self.study['id'],fixtures.ACTOR,body)
            output['questions']=[];output['proposal']=dict(value,conditions='Changed conditions')
            with self.assertRaisesRegex(ValueError,'entire saved packet'):design.adopt(self.study['id'],fixtures.ACTOR,body)
            output['proposal']=copy.deepcopy(value);output['proposal']['protocol']['planned_units']=2
            with self.assertRaisesRegex(ValueError,'changed saved planned_units'):design.adopt(self.study['id'],fixtures.ACTOR,body)
        for invalid_row in [[],[dict(row,status='failed')],[dict(row,packet_id=str(uuid.uuid4()))]]:
            with patch.object(store,'rows',return_value=invalid_row),self.assertRaisesRegex(ValueError,'complete proposal'):design.adopt(self.study['id'],fixtures.ACTOR,body)
        output['proposal']=self.candidate();output['response_mode']='propose'
        with patch.object(store,'rows',return_value=[row]),patch.object(design,'context',return_value=retained),patch.object(ideas,'packet',return_value={'document':copy.deepcopy(value)}),patch.object(ideas,'save',return_value={'saved':True}) as save:
            self.assertEqual(design.adopt(self.study['id'],fixtures.ACTOR,body),{'saved':True});self.assertEqual(save.call_args.args[2]['supersedes_id'],packet_id)
        executable=self.candidate();executable['protocol']['execution']={'runner':'custom-package-v1'};row['output']['proposal']=executable;bound=dict(executable['protocol'],original_bound_input=True)
        with patch.object(store,'rows',return_value=[row]),patch.object(design,'context',return_value=retained),patch.object(ideas,'packet',return_value={'document':copy.deepcopy(executable)}),patch.object(design.network_spec,'bind_inputs',return_value=bound) as bind,patch.object(ideas,'save',return_value={'saved':True}) as save:
            self.assertEqual(design.adopt(self.study['id'],fixtures.ACTOR,body),{'saved':True});bind.assert_called_once_with(executable['protocol']);self.assertEqual(save.call_args.args[2]['document']['protocol'],bound)
        for output_change,reason in [({'response_version':'old'},'corrected planning'),({'decision_policy':{}},'corrected decision'),({'proposal':None},'clarifying questions')]:
            invalid=copy.deepcopy(row);invalid['output'].update(output_change)
            with patch.object(store,'rows',return_value=[invalid]),self.assertRaisesRegex(ValueError,reason):design.adopt(self.study['id'],fixtures.ACTOR,body)


if __name__=='__main__':unittest.main()
