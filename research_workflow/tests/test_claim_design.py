import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api, claim_design, ideas, store
from workflow import canonical, digest
import test_gap_queue as fixtures
ACTOR=fixtures.ACTOR


class ClaimDesign(unittest.TestCase):
    setUp=fixtures.GapQueue.setUp
    def test_every_recorded_claim_can_be_selected_without_a_gap_or_registered_plan(self):
        self.snapshot['records']['gaps'] = []
        with patch('preparation.registered', return_value=(None, None)):
            value, origin = claim_design.selection(self.study['id'], ACTOR, self.claim['id'])
        self.assertIsNone(origin['gap']); self.assertEqual(value['claim_id'], self.claim['id'])
        with patch.object(store, 'snapshot', return_value=None):
            with self.assertRaises(ValueError): claim_design.selection(self.study['id'], ACTOR, self.claim['id'])

    def test_design_is_owner_scoped_idempotent_and_preserves_paper_origin(self):
        preview, origin = claim_design.selection(self.study['id'], ACTOR, self.claim['id'])
        body = dict(action='design', request_id=str(uuid.uuid4()), claim_id=self.claim['id'], claim_sha256=preview['claim_sha256'])
        before = canonical(self.snapshot)
        with patch.object(store, 'rpc', return_value={}) as rpc, patch.object(ideas, 'save') as save:
            a = claim_design.start(self.study['id'], ACTOR, body)
            b = claim_design.start(self.study['id'], ACTOR, dict(body, request_id=str(uuid.uuid4())))
            c = claim_design.start(self.study['id'], str(uuid.uuid4()), body)
        self.assertEqual(a, b); self.assertNotEqual(a, c); self.assertEqual(canonical(self.snapshot), before)
        self.assertEqual(rpc.call_args_list[0].args[1]['p_document']['study_kind'], 'paper')
        copied = rpc.call_args_list[1].args[1]
        self.assertEqual(copied['p_document'], origin)
        self.assertEqual(copied['p_source']['sha256'], self.source['sha256'])
        self.assertEqual(copied['p_claim']['description'], self.claim['description'])
        self.assertNotEqual(copied['p_claim']['id'], self.claim['id'])
        self.assertEqual(save.call_args.args[2]['document']['protocol'], None)

    def test_changed_claim_selection_fails_before_any_write(self):
        body = dict(action='design', request_id=str(uuid.uuid4()), claim_id=self.claim['id'], claim_sha256='0'*64)
        with patch.object(store, 'rpc') as rpc:
            with self.assertRaisesRegex(ValueError, 'changed'): claim_design.start(self.study['id'], ACTOR, body)
            rpc.assert_not_called()

    def test_original_claim_and_source_hash_cannot_be_weakened(self):
        _, origin = claim_design.selection(self.study['id'], ACTOR, self.claim['id'])
        context = dict(document=origin)
        doc = dict.fromkeys(ideas.FIELDS, '')
        doc.update(hypothesis=self.claim['description'], protocol=None)
        claim_design.validate_binding(doc, context)
        with self.assertRaises(ValueError): claim_design.validate_binding(dict(doc, hypothesis='A weaker claim'), context)
        with self.assertRaises(ValueError): claim_design.validate_binding(dict(doc, protocol={'input_versions':[]}), context)
        claim_design.validate_binding(dict(doc, falsification=claim_design.POLICY['decision_rule'],protocol={'hypotheses':self.claim['description'], 'input_versions':[{'sha256':self.source['sha256']}],
            'decision_policy':claim_design.POLICY_IDENTITY,'statistics':claim_design.POLICY['statistics'],'stage':'pilot'}), context)

    def test_context_hash_verification_does_not_imply_full_text_review(self):
        _, origin = claim_design.selection(self.study['id'], ACTOR, self.claim['id'])
        raw=canonical(origin).decode();row=dict(document=origin,raw_document=raw,sha256=digest(raw.encode()))
        with patch.object(store, 'rows', return_value=[row]): self.assertEqual(claim_design.context(self.study['id']),row)
        with patch.object(store, 'rows', return_value=[dict(row,raw_document=raw+' ')]):
            with self.assertRaises(store.StoreError): claim_design.context(self.study['id'])

    def test_anonymous_or_other_owner_cannot_read_design_packets(self):
        event=dict(rawPath='/research/studies/'+self.study['id']+'/claim-design',requestContext={'http':{'method':'GET'}})
        with self.assertRaises(api.HttpError): api.dispatch(event)
        with patch.object(store, 'owns', return_value=False):
            with self.assertRaises(api.HttpError): api.dispatch(event,ACTOR)

    def test_paper_readiness_requires_a_reviewable_stage_and_experimental_method(self):
        from test_ideas import plan
        _, origin = claim_design.selection(self.study['id'], ACTOR, self.claim['id'])
        value=plan();value['hypothesis']=self.claim['description'];value['protocol']['hypotheses']=self.claim['description']
        value['protocol']['input_versions'].append(dict(identity='Selected source',sha256=self.source['sha256']))
        value['falsification']=claim_design.POLICY['decision_rule']
        value['protocol'].update(decision_policy=claim_design.POLICY_IDENTITY,statistics=claim_design.POLICY['statistics'])
        required=claim_design.missing(value,dict(document=origin))
        self.assertTrue(any('stage' in item for item in required));self.assertTrue(any('method' in item for item in required))
        value['protocol'].update(stage='pilot',experiment_type='independent-check')
        self.assertEqual(claim_design.missing(value,dict(document=origin)),[])

    def test_bad_decision_rule_is_rejected_even_when_the_claim_and_source_match(self):
        from test_ideas import plan
        _,origin=claim_design.selection(self.study['id'],ACTOR,self.claim['id'])
        value=plan();value['hypothesis']=self.claim['description'];value['protocol'].update(hypotheses=self.claim['description'],stage='pilot',
            decision_policy=claim_design.POLICY_IDENTITY,statistics=claim_design.POLICY['statistics'])
        value['protocol']['input_versions'].append(dict(identity='Source',sha256=self.source['sha256']))
        value['falsification']='Failing to reject establishes equivalence.'
        with self.assertRaisesRegex(ValueError,'descriptive decision policy'):claim_design.validate_binding(value,dict(document=origin))

    def test_old_model_proposals_cannot_bypass_the_corrected_contract(self):
        with patch.object(store,'rows',return_value=[dict(status='complete',task='planning',packet_id=self.claim['id'],output={'proposal':{}})]),patch.object(ideas,'save') as save:
            with self.assertRaisesRegex(ValueError,'earlier proposal'):claim_design.adopt(self.study['id'],ACTOR,dict(action='adopt',request_id=self.claim['id'],run_id=self.claim['id'],packet_id=self.claim['id']))
            save.assert_not_called()

    def test_adoption_independently_rejects_configuration_input_and_budget_drift(self):
        from test_ideas import plan
        _,origin=claim_design.selection(self.study['id'],ACTOR,self.claim['id'])
        before=plan();before['hypothesis']=self.claim['description'];before['falsification']=claim_design.POLICY['decision_rule']
        before['protocol'].update(hypotheses=self.claim['description'],stage='pilot',statistics=claim_design.POLICY['statistics'],decision_policy=claim_design.POLICY_IDENTITY)
        before['protocol']['input_versions'].append(dict(identity='Source',sha256=self.source['sha256']))
        for key in ('configurations','execution','planned_units','resource_limits','input_versions','metrics'):
            proposal=copy.deepcopy(before)
            if key=='execution':proposal['protocol'][key]={'runner':'changed'}
            elif key=='planned_units':proposal['protocol'][key]=2
            elif key=='configurations':proposal['protocol'][key][0]['identity']='changed'
            elif key=='resource_limits':proposal['protocol'][key]['wall_seconds']+=1
            elif key=='metrics':proposal['protocol'][key][0]['planned_observations']=2
            else:proposal['protocol'][key].append(dict(identity='Unexpected input',sha256='e'*64))
            policy=dict(claim_design.POLICY_IDENTITY,origin='server-policy',model_authored=False)
            run=dict(status='complete',task='planning',packet_id=self.claim['id'],output=dict(proposal=proposal,decision_policy=policy,response_version=claim_design.RESPONSE_VERSION,response_mode='propose'))
            with self.subTest(key=key),patch.object(store,'rows',return_value=[run]),patch.object(claim_design,'context',return_value=dict(document=origin)),patch.object(ideas,'packet',return_value={'document':before}),patch.object(ideas,'save') as save:
                with self.assertRaisesRegex(ValueError,'changed saved'):
                    claim_design.adopt(self.study['id'],ACTOR,dict(action='adopt',request_id=self.claim['id'],run_id=self.claim['id'],packet_id=self.claim['id']))
                save.assert_not_called()

    def test_clarification_cannot_be_adopted_or_create_a_packet(self):
        run=dict(status='complete',task='planning',packet_id=self.claim['id'],output=dict(proposal=None,response_version=claim_design.RESPONSE_VERSION,response_mode='clarify',questions=['Which workload is available?']))
        with patch.object(store,'rows',return_value=[run]),patch.object(ideas,'save') as save:
            with self.assertRaisesRegex(ValueError,'asks clarifying questions'):
                claim_design.adopt(self.study['id'],ACTOR,dict(action='adopt',request_id=self.claim['id'],run_id=self.claim['id'],packet_id=self.claim['id']))
            save.assert_not_called()


if __name__ == '__main__': unittest.main()
