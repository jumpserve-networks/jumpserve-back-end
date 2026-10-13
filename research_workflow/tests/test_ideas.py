import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api, ideas, store, workflow

STUDY, ACTOR, PACKET = (str(uuid.uuid4()) for _ in range(3))
def draft(): return dict({field: "" for field in ideas.FIELDS}, protocol=None)
def plan():
    value={field:"Explicit development fixture declaration; not empirical evidence" for field in ideas.FIELDS}
    candidate=workflow.numerical_protocol("a"*64,"latency","ms",0,[dict(identity="control",details={"fixture":True})])
    candidate.update(prior_exposure="Development fixture exposed to implementer; no held-out validation",planned_units=1)
    value['protocol']=candidate
    return value
def event(action=None,method='POST',suffix='idea'):
    return dict(rawPath=f'/research/studies/{STUDY}/{suffix}',requestContext={'http':{'method':method}},body=json.dumps(action or {}))

class Ideas(unittest.TestCase):
    def test_idea_has_missing_paper_not_a_fake_url(self):
        result=workflow.paper_details(dict(title='New hypothesis',domain='Methods',scope='Untested idea',study_kind='idea',paper_url=None))
        self.assertIsNone(result['paper_url']);self.assertEqual(result['study_kind'],'idea')
        for change in ({'paper_url':'https://example.org/fake'},{'study_kind':'unknown'}):
            with self.assertRaises(ValueError):workflow.paper_details(dict(result,**change))
        with self.assertRaises(ValueError):workflow.paper_details(dict(title='Paper',domain='Methods',scope='Scope',paper_url=None))
        old=workflow.paper_details(dict(title='Paper',domain='Methods',scope='Scope',paper_url='https://example.org/paper'))
        self.assertNotIn('study_kind',old)

    def test_draft_missingness_is_explicit_and_does_not_become_a_protocol(self):
        value=draft();self.assertEqual(ideas.document(value),value)
        self.assertIn('hypothesis',ideas.completeness(value))
        self.assertTrue(any('protocol' in item for item in ideas.completeness(value)))
        for bad in (dict(value, invented_result=0),dict(value,protocol='execute code'),dict(value,hypothesis='x'*25000)):
            with self.assertRaises(ValueError):ideas.document(bad)
        self.assertEqual(ideas.completeness(plan()),[])

    def test_packet_identity_rejects_changed_bytes_or_other_study(self):
        value=plan();raw=workflow.canonical(value).decode()
        row=dict(id=PACKET,study_id=STUDY,document=value,raw_document=raw,sha256=workflow.digest(raw.encode()),version=1)
        with patch.object(store,'rows',return_value=[row]) as query:
            self.assertEqual(ideas.packet(STUDY,PACKET),row)
            self.assertEqual(query.call_args.args[1]['study_id'],'eq.'+STUDY)
        with patch.object(store,'rows',return_value=[dict(row,raw_document=raw+' ')]):
            with self.assertRaises(store.StoreError):ideas.packet(STUDY,PACKET)

    def test_owner_and_study_kind_before_private_reads_or_writes(self):
        with patch.object(store,'owns',return_value=False),patch.object(ideas,'status') as read:
            with self.assertRaises(api.HttpError):api.dispatch(event(method='GET'),ACTOR)
            read.assert_not_called()
        with patch.object(store,'owns',return_value=True),patch.object(store,'rows',return_value=[{'study_kind':'paper'}]):
            with self.assertRaises(api.HttpError):api.dispatch(event(method='GET'),ACTOR)
        with patch.object(store,'owns',return_value=True),patch.object(store,'rows',return_value=[{'study_kind':'idea'}]):
            with self.assertRaises(api.HttpError):api.dispatch(event(method='GET',suffix='prepare'),ACTOR)

    def test_original_canonical_packet_is_passed_for_database_hash_check(self):
        value=draft()
        with patch.object(store,'rpc',return_value={}) as rpc:
            ideas.save(STUDY,ACTOR,dict(action='save',request_id=PACKET,supersedes_id=None,document=value,reason='Initial draft'))
            sent=rpc.call_args.args[1]
            self.assertEqual(json.loads(sent['p_raw']),value)
            self.assertEqual(sent['p_sha256'],workflow.digest(sent['p_raw'].encode()))
            self.assertEqual(sent['p_actor'],ACTOR)

    def test_acceptance_cannot_hide_missing_inputs_or_protocol(self):
        with patch.object(ideas,'packet',return_value={'id':PACKET,'document':draft()}),patch.object(store,'rpc') as rpc:
            with self.assertRaises(ValueError):ideas.accept(STUDY,ACTOR,dict(action='accept',request_id=str(uuid.uuid4()),packet_id=PACKET,reason='Approve'))
            rpc.assert_not_called()

    def test_campaign_preparation_has_proposal_and_no_published_data_or_measurements(self):
        value=plan();raw=workflow.canonical(value).decode()
        row=dict(id=PACKET,document=value,raw_document=raw,sha256=workflow.digest(raw.encode()),version=1)
        request=str(uuid.uuid4());study=dict(id=STUDY,title='Fixture proposed hypothesis')
        body=dict(action='prepare-campaign',request_id=request,packet_id=PACKET,title='Fixture pilot',stage='pilot',claim_type='association',experiment_type='new-measurement',planned_units=1)
        artifact=dict(id=str(uuid.uuid4()),sha256=row['sha256'],byte_count=len(raw),storage_path='fixture',media_type='application/json',provenance={},verified_at=workflow.now())
        with patch.object(ideas,'packet',return_value=row),patch.object(store,'artifact',return_value=artifact),patch.object(store,'rpc',return_value={'campaign_id':str(uuid.uuid4())}) as rpc:
            result=ideas.prepare_campaign(study,ACTOR,body)
            records=rpc.call_args.args[1]['p_records'];kinds={entry['kind'] for entry in records}
            self.assertFalse(kinds & {'published_values','measurements','runs','assessments','reviews','publications'})
            source=next(entry['record'] for entry in records if entry['kind']=='sources')
            self.assertEqual(source['kind'],'unpublished-proposal');self.assertIsNone(source['source_url'])
            self.assertEqual(result['queue_request']['execution_mode'],'manual');self.assertIsNone(result['queue_request']['raw_input'])
            changed=dict(body,planned_units=2)
            with self.assertRaises(ValueError):ideas.prepare_campaign(study,ACTOR,changed)

if __name__=='__main__':unittest.main()
