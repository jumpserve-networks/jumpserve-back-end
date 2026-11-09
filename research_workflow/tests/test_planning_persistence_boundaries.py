"""Versioned planning histories and immutable campaign preparation persistence."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ideas,preparation,store,workflow,polling
import claim_design,network_spec
from test_ideas import plan,draft,STUDY,ACTOR,PACKET
from test_preparation import small_plan


class PlanningPersistence(unittest.TestCase):
    def packet(self):
        value=plan();raw=workflow.canonical(value).decode()
        return dict(id=PACKET,version=1,document=value,raw_document=raw,sha256=workflow.digest(raw.encode()))

    def test_readiness_requires_latest_complete_reviews_of_the_current_packet(self):
        packet=self.packet();runs=[dict(id='old',packet_id='old-packet',task='critic',status='complete'),dict(id='comprehension',packet_id=PACKET,task='comprehension',status='complete'),dict(id='critic',packet_id=PACKET,task='critic',status='complete')]
        decision=dict(packet_id=PACKET,decision='ready',comprehension_run_id='comprehension',critic_run_id='critic')
        records={'idea_packets':[packet],'idea_runs':runs,'idea_readiness':[decision],'idea_campaign_links':[]}
        with patch.object(store,'rows',side_effect=lambda kind,query:copy.deepcopy(records[kind])):
            self.assertTrue(ideas.status(STUDY)['ready'])
            runs.append(dict(id='new-failed',packet_id=PACKET,task='critic',status='failed'))
            result=ideas.status(STUDY);self.assertFalse(result['ready']);self.assertEqual(result['current_packet_id'],PACKET)
            records['idea_packets']=[];self.assertFalse(ideas.status(STUDY)['ready']);self.assertIn('Candidate protocol',ideas.status(STUDY)['missing'])
        for kind,bound in [('idea_packets',100),('idea_runs',200),('idea_readiness',100),('idea_campaign_links',100)]:
            with patch.object(store,'rows',side_effect=lambda name,query:[{}]*(bound+1) if name==kind else []),self.assertRaisesRegex(store.StoreError,'history exceeds'):ideas.status(STUDY)

    def test_packet_absence_unknown_fields_and_incomplete_campaign_cannot_write(self):
        with patch.object(store,'rows',return_value=[]),self.assertRaisesRegex(ValueError,'unavailable'):ideas.packet(STUDY,PACKET)
        for operation,args in [(ideas.save,(STUDY,ACTOR,{'invented':True})),(ideas.accept,(STUDY,ACTOR,{'invented':True})),(ideas.prepare_campaign,({'id':STUDY},ACTOR,{'invented':True})),(ideas.resume_campaign,({'id':STUDY},ACTOR,{}))]:
            with patch.object(store,'rpc') as write,self.assertRaises(ValueError):operation(*args)
            write.assert_not_called()
        with patch.object(ideas,'packet',return_value=dict(id=PACKET,document=draft())),patch.object(store,'rpc') as write,self.assertRaisesRegex(ValueError,'Complete and review'):ideas.prepare_campaign({'id':STUDY},ACTOR,{'packet_id':PACKET})
        write.assert_not_called()

    def test_readiness_acceptance_and_campaign_resume_preserve_exact_owner_scoped_identities(self):
        request=str(uuid.uuid4());body=dict(action='accept',request_id=request,packet_id=PACKET,comprehension_run_id=str(uuid.uuid4()),critic_run_id=str(uuid.uuid4()),reason='Original review rationale')
        with patch.object(ideas,'packet',return_value=self.packet()),patch.object(store,'rpc',return_value={'saved':True}) as rpc:
            self.assertEqual(ideas.accept(STUDY,ACTOR,body),{'saved':True});payload=rpc.call_args.args[1];self.assertEqual(payload['p_actor'],ACTOR);self.assertEqual(payload['p_packet'],PACKET)
        prepared=str(uuid.uuid4());saved={'packet_id':PACKET,'request_id':request,'title':'Original pilot'}
        with patch.object(store,'rows',return_value=[{'request':saved}]) as rows,patch.object(ideas,'prepare_campaign',return_value={'original':True}) as prepare:
            self.assertEqual(ideas.resume_campaign({'id':STUDY},ACTOR,{'action':'resume','prepared_id':prepared}),{'original':True});self.assertEqual(prepare.call_args.args[2],dict(saved,action='prepare-campaign'));self.assertEqual(rows.call_args.args[1]['study_id'],'eq.'+STUDY)
        with patch.object(store,'rows',return_value=[]),self.assertRaisesRegex(ValueError,'unavailable'):ideas.resume_campaign({'id':STUDY},ACTOR,{'action':'resume','prepared_id':prepared})

    def test_preparation_persists_original_and_compiled_plan_bytes_then_replays_without_duplicate_artifacts(self):
        study=dict(id=STUDY,paper_url='https://pure.mpg.de/pubman/item/item_3670144_1');value=small_plan();raw=workflow.canonical(value)
        body=dict(action='prepare',request_id=str(uuid.uuid4()),plan_input=raw.decode());uploaded=[];saved={}
        def artifact(study,actor,original,identity,media,provenance):
            uploaded.append((original,copy.deepcopy(provenance)));return dict(id=identity,sha256=workflow.digest(original),byte_count=len(original))
        def rpc(name,payload):
            saved.update(id=payload['p_id'],**payload['p_plan']);return copy.deepcopy(saved)
        def rows(kind,params):return [copy.deepcopy(saved)] if kind=='prepared_plans' and saved else []
        with patch.object(store,'rows',side_effect=rows),patch.object(store,'artifact',side_effect=artifact),patch.object(store,'rpc',side_effect=rpc):
            first=preparation.prepare(study,ACTOR,body);second=preparation.prepare(study,ACTOR,body)
        self.assertFalse(first['replayed']);self.assertTrue(second['replayed']);self.assertEqual(len(uploaded),2);self.assertEqual(uploaded[0][0],raw);self.assertEqual(workflow.digest(raw),saved['manifest_sha256']);self.assertEqual(json.loads(uploaded[1][0])['id'],saved['id'])

    def test_preparation_missing_registered_plan_invalid_text_and_storage_budget_stop_before_further_writes(self):
        study=dict(id=STUDY,paper_url='https://example.test/unregistered');request=str(uuid.uuid4())
        for body in [dict(request_id=request,extra=True),dict(request_id=request,plan_input={}),dict(request_id=request)]:
            with patch.object(store,'artifact') as artifact,self.assertRaises(ValueError):preparation.prepare(study,ACTOR,body)
            artifact.assert_not_called()
        raw=workflow.canonical(small_plan());study['paper_url']='https://pure.mpg.de/pubman/item/item_3670144_1'
        with patch.object(store,'rows',return_value=[]),patch.object(preparation.time,'monotonic',side_effect=[0,19]),patch.object(store,'artifact') as artifact,self.assertRaisesRegex(store.StoreError,'storage budget'):preparation.prepare(study,ACTOR,dict(request_id=request,plan_input=raw.decode()))
        artifact.assert_not_called()

    def test_prepared_history_and_job_selection_require_complete_bound_originals(self):
        study={'id':STUDY,'paper_url':'https://example.test/paper'}
        with patch.object(store,'rows',return_value=[{}]*101),self.assertRaisesRegex(store.StoreError,'history exceeds'):preparation.status(study)
        with patch.object(store,'rows',return_value=[]):self.assertIsNone(preparation.status(study)['available_plan'])
        for body in [{},dict(action='enqueue',prepared_id=PACKET,job_id=PACKET,extra=True)]:
            with self.assertRaises(ValueError):preparation.enqueue(study,ACTOR,body)
        body=dict(action='enqueue',prepared_id=PACKET,job_id=PACKET)
        with patch.object(store,'rows',return_value=[]),self.assertRaisesRegex(ValueError,'Prepare this paper'):preparation.enqueue(study,ACTOR,body)
        with patch.object(store,'rows',return_value=[{'id':PACKET}]),patch.object(preparation,'compiled',return_value={'jobs':[]}),self.assertRaisesRegex(ValueError,'does not belong'):preparation.enqueue(study,ACTOR,body)

    def test_polling_rejects_invalid_bounds_and_cannot_extend_an_expired_deadline(self):
        for initial,maximum in [(0,1),(2,1),(-1,2)]:
            with self.assertRaises(ValueError):polling.Backoff(initial,maximum)
        with patch.object(polling.time,'monotonic',return_value=10),patch.object(polling.time,'sleep') as sleep:polling.wait(5,9)
        sleep.assert_not_called()

    def test_claim_campaign_preparation_preserves_original_source_and_claim_and_prepares_each_bound_network_specification(self):
        selected=self.packet();selected['document']['protocol'].update(stage='pilot',experiment_type='new-measurement',execution={'runner':'synthetic-network-adapter','exclusive_resources':['machine:original']});registered=small_plan();records={entry['kind']:entry['record'] for entry in registered['records']};retained=dict(source_id=records['sources']['id'],claim_id=records['claims']['id']);body=dict(action='prepare-campaign',request_id=str(uuid.uuid4()),packet_id=PACKET,title='Original claim pilot',stage='pilot',claim_type='numerical',experiment_type='new-measurement',planned_units=1);study=dict(id=STUDY,title='Original retained paper');specifications=[{'version':'synthetic-spec-v1','original':n} for n in range(2)]
        with patch.object(ideas,'packet',return_value=selected),patch.object(claim_design,'validate_binding') as binding,patch.object(network_spec,'specs',return_value=specifications),patch.object(store,'rows',side_effect=lambda kind,query:[dict(records[kind],private_metadata='must be omitted')]),patch.object(store,'artifact',return_value={'id':str(uuid.uuid4())}),patch.object(store,'rpc',return_value={'campaign_id':str(uuid.uuid4())}) as rpc:
            result=ideas.prepare_campaign(study,ACTOR,body,retained=retained);binding.assert_called_once_with(selected['document'],retained);entries=rpc.call_args.args[1]['p_records'];self.assertEqual(next(e['record'] for e in entries if e['kind']=='sources'),records['sources']);self.assertEqual(next(e['record'] for e in entries if e['kind']=='claims'),records['claims']);self.assertEqual(len(result['queue_requests']),2);self.assertEqual([json.loads(item['raw_input']) for item in result['queue_requests']],specifications);self.assertEqual(result['queue_request']['exclusive_resources'],['machine:original']);self.assertEqual(result['execution']['jobs'],2)
            with self.assertRaisesRegex(ValueError,'stage and method must match'):ideas.prepare_campaign(study,ACTOR,dict(body,stage='main'),retained=retained)
        with patch.object(ideas,'packet',return_value=self.packet()),patch.object(store,'rpc') as rpc,self.assertRaisesRegex(ValueError,'Declare a pilot/main'):ideas.prepare_campaign(study,ACTOR,dict(body,stage='retrospective-import'))
        rpc.assert_not_called()


if __name__=='__main__':unittest.main()
