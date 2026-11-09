"""Reject incomplete source, review and repair contracts without external calls."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy,autonomy_inputs as inputs,model_dispatch as dispatch,paper_selection as selection
import repair_validation,source_project,runtime_retry,store
import bridge,budget_policy,prompt_catalog,ideas,claim_design
import autonomy_evidence,autonomy_sources,runner
from workflow import canonical,digest
from test_autonomy import task
from test_autonomy_inputs import source_fixture
from test_paper_selection import records,output
from test_native_repair_handoff import context as repair_context


class RetainedInputContracts(unittest.TestCase):
    def test_nonmeasurement_evidence_keeps_its_hash_and_never_invents_metric_counts_or_source_metadata(self):
        original=[dict(id='original',status='retained',zero=0)];manifest=autonomy_evidence.manifest(original);self.assertEqual(manifest['metric_counts'],[]);self.assertEqual(manifest['records_sha256'],digest(canonical(original)))
        with self.assertRaisesRegex(ValueError,'explicit title and domain'):autonomy_sources.paper_metadata({}, {}, {})

    def test_a_full_owner_record_page_is_followed_by_an_explicit_end_page(self):
        current=task();read=[]
        def rows(kind,query,**options):
            read.append((kind,query))
            if kind=='studies':return [dict(id=current['study_id'])]
            if kind=='sources' and query['offset']==0:return [dict(id=str(i)) for i in range(1000)]
            return []
        with patch.object(store,'rows',side_effect=rows):snapshot=store.snapshot(current['study_id'],owner=True,maximum=1000)
        self.assertEqual(snapshot['coverage']['sources'],1000);self.assertEqual([query['offset'] for kind,query in read if kind=='sources'],[0,1000])

    def test_observed_rows_without_published_cells_cannot_claim_comparison_coverage(self):
        from test_workflow import fixture
        raw,protocol,campaign=fixture(published=[]);result=runner.execute(raw,protocol,campaign,task()['id'])
        self.assertEqual(result['published_values'],[]);self.assertTrue(all(row['published_id'] is None for row in result['measurements']))
        self.assertEqual(result['run']['status'],'failed');self.assertIn('No published comparison observations',result['run']['reason'])

    def test_distinct_published_and_observed_cells_share_the_unique_observation_budget(self):
        import json
        from test_workflow import fixture
        raw,protocol,campaign=fixture();data=json.loads(raw);data['observed'][0]['observation_id']='different-original-cell';raw=canonical(data)
        protocol['document']['input_versions'][0]['sha256']=digest(raw);protocol['document']['resource_limits']['max_observations']=1;protocol['sha256']=digest(canonical(protocol['document']))
        result=runner.execute(raw,protocol,campaign,task()['id']);self.assertEqual(result['run']['status'],'failed');self.assertIn('Unique observation budget exceeded',result['run']['reason']);self.assertEqual(result['measurements'],[])

    def test_empty_source_page_still_bounds_its_complete_provenance_envelope(self):
        current,claim,source,_,_,lookup,_=source_fixture();material=dict(representation='hash-bound-extracted-paper-text',provenance='')
        envelope=dict(source_material=material,record_kind='extracted-paper-text');material['provenance']='x'*(autonomy.evidence.READ_BYTES-len(canonical(envelope))-200)
        with patch.object(autonomy,'one',side_effect=lookup),patch.object(autonomy,'inventory',return_value=[]),patch.object(autonomy,'retained_jobs',return_value=[]),patch.object(inputs,'source_text',return_value=(material,[])),self.assertRaisesRegex(ValueError,'Source page and provenance exceed'):
            autonomy.inspect_evidence(current,dict(kind='source',id=source['id'],cursor=0))
    def test_assessment_read_contracts_reject_foreign_budget_categories_nonlist_prompts_and_missing_claims(self):
        with self.assertRaisesRegex(store.StoreError,'Wrong category'):budget_policy.terms(dict(category='other'))
        with patch.object(prompt_catalog,'read',return_value={}),self.assertRaisesRegex(store.StoreError,'Invalid prompt catalogue'):prompt_catalog.collect('agent_prompt_versions','id')
        current=task()
        with patch.object(store,'owns',return_value=True),patch.object(store,'snapshot',return_value=dict(study=dict(study_kind='paper'),records=dict(claims=[]))),self.assertRaisesRegex(ValueError,'recorded claim'):claim_design.selection(current['study_id'],current['actor_id'],current['claim_id'])
        draft=dict.fromkeys(ideas.FIELDS,'');draft['protocol']=None
        for field in ideas.FIELDS[:5]:draft[field]='x'*5000
        with self.assertRaisesRegex(ValueError,'24 KB'):ideas.document(draft)
        with self.assertRaisesRegex(ValueError,'identities only'):claim_design.adopt(current['study_id'],current['actor_id'],{})

    def test_legacy_assessment_byte_limit_applies_before_decoding_or_importing_any_record(self):
        with patch.object(Path,'read_bytes',return_value=b'x'*100_000_001),self.assertRaisesRegex(ValueError,'100 MB'):bridge.ipv6('original.json',task()['study_id'])
    def test_source_page_character_quote_and_delivery_bounds_preserve_originals(self):
        for defect in ['pages','duplicate','characters','quote','unanchored']:
            with self.subTest(defect=defect):
                t,c,s,_,rows,lookup,packet=source_fixture()
                if defect=='pages':packet['pages']=[dict(page=i+1,text='') for i in range(301)]
                elif defect=='duplicate':packet['pages'][1]['page']=1
                elif defect=='characters':packet['pages']=[dict(page=1,text='x'*2_000_001)]
                elif defect=='quote':
                    c['description']='x'*2501;c['scope']['exact_quote']=c['description'];packet['pages']=[dict(page=1,text=c['description'])]
                else:c['scope']={}
                raw=canonical(packet);rows['artifacts'][t['context']['text_artifact_id']]['sha256']=digest(raw)
                with patch.object(store,'artifact_bytes',return_value=raw):
                    if defect=='unanchored':
                        material,_=inputs.source_text(t,c,s,lookup);self.assertIsNone(material['inline_passage'])
                    else:
                        with self.assertRaises(ValueError):inputs.source_text(t,c,s,lookup)
        t=task();material=dict(source_id='source',text_artifact_id='text',text_artifact_sha256='a'*64,fragment_count=2)
        t['context']['source_deliveries']={t['stage']:dict(identity=material,ranges=[[0,3]])}
        with self.assertRaisesRegex(ValueError,'delivery ranges'):inputs.delivery_progress(t,material)
        t['context']={};before=copy.deepcopy(t)
        read=dict(request=dict(kind='source',cursor=0),result=dict(record_kind='extracted-paper-text',source_material=material,offset=1,returned_records=1))
        with self.assertRaisesRegex(ValueError,'fragment offset'):inputs.retain_source_delivery(t,read)
        self.assertEqual(t,before)

    def test_lossless_evidence_decoder_refuses_incomplete_rows_and_details(self):
        packet=json.loads((Path(__file__).parent/'fixtures/legacy-evidence-columns-v1.json').read_bytes())['cases']['details']['packet']
        for defect in ['representation','count','row','details']:
            broken=copy.deepcopy(packet)
            if defect=='representation':broken['representation']='unknown'
            elif defect=='count':broken['record_count']+=1
            elif defect=='row':broken['rows'][0].append('unbound value')
            else:broken['rows'][0][broken['columns'].index('details')]=[]
            with self.subTest(defect=defect),self.assertRaises(ValueError):autonomy.evidence_records(broken)
        t=task();t['context']['job_ids']=['missing']
        with patch.object(autonomy,'rows',return_value=[]),self.assertRaisesRegex(ValueError,'identity coverage'):autonomy.retained_jobs(t)

    def test_review_groups_require_complete_bounded_labels_rationale_and_model_envelope(self):
        values=records(1);allowed={values[0]['id']};valid=output(values)
        for broken in [None,{},dict(valid,rationale=''),dict(valid,central_claims=[None]),dict(valid,central_claims=[dict(claim_id=values[0]['id'],label=' '*3)])]:
            with self.subTest(broken=broken),self.assertRaises(ValueError):selection.validate(broken,allowed)
        for broken in [None,{},dict(central_claims='not a list',testable_findings=[]),dict(central_claims=[values[0]['id']]*201,testable_findings=[])]:
            with self.assertRaises(ValueError):selection.selection_ids(broken,allowed)
        with self.assertRaisesRegex(ValueError,'complete review statement'):selection.context(dict(title='Original'),values,0,{},fits=lambda _:False)

    def test_source_sealing_rejects_a_linked_root_and_the_real_aggregate_byte_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);project=root/'project';project.mkdir();link=root/'linked';link.symlink_to(project,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'root cannot be a symlink'):source_project.freeze({},link,lambda *_:self.fail('No artifact may be retained'))
            for i in range(4):(project/f'file-{i}.txt').write_bytes(b'x'*256000)
            with self.assertRaisesRegex(ValueError,'expanded bound'):source_project.freeze({},project,lambda *_:self.fail('No artifact may be retained'))

    def test_repair_checks_need_a_final_object_metadata_and_original_baseline(self):
        for raw in [b'',b'[]\n']:
            with self.assertRaises(ValueError):repair_validation.result_line(raw)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError,'required'):repair_validation.test_plan(Path(root),{})
        self.assertIsNone(repair_validation.source_change({}, {}, b'Original historical package'))
        context=repair_context();context['native_repair_handoff']={}
        with patch.object(repair_validation,'store') as storage,patch('experiment_builder.repair_handoff',return_value={}):
            with self.assertRaisesRegex(ValueError,'baseline checkpoint'):repair_validation.source_change({},context,b'Original package')
            storage.artifact_bytes.assert_not_called()

    def test_nontransient_dispatch_errors_never_retry_or_admit_an_alternate_model(self):
        request=dict(modelId=dispatch.MODEL);t=task();turn=t['id']
        self.assertTrue(dispatch.transient_read(urllib.error.HTTPError('https://example.test',503,'failure',{},None)))
        self.assertFalse(dispatch.transient_read(urllib.error.HTTPError('https://example.test',403,'denied',{},None)))
        with patch.object(dispatch,'rpc') as rpc,self.assertRaisesRegex(ValueError,'model identity'):dispatch.invoke(t,turn,dict(modelId='other'))
        rpc.assert_not_called()
        for stage in ['enqueue','result','artifact']:
            calls=[]
            def rpc(operation,payload):
                calls.append(operation)
                if operation==stage:raise ValueError('Original definitive contract failure')
                return dict(replayed=False) if operation=='enqueue' else dict(status='complete',response_artifact_id='response',response_sha256='a'*64)
            with self.subTest(stage=stage),patch.object(dispatch,'rpc',side_effect=rpc),patch.object(store,'artifact_bytes',side_effect=ValueError('Original definitive artifact failure')):
                with self.assertRaisesRegex(ValueError,'Original definitive'):dispatch.invoke(t,turn,request)
            self.assertEqual(calls,['enqueue'] if stage=='enqueue' else ['enqueue','result'])
        provenance=dict(status='failed',response_received=True,http_status=503,error_code='http-503')
        for streak in [-1,True,'1']:
            self.assertIsNone(runtime_retry.decision(provenance,dict(consecutive_failures=streak),'2030-01-01T00:00:00Z',now=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)))


if __name__=='__main__':unittest.main()
