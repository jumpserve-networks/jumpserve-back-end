"""Rejected preparation inputs must fail before any records or jobs are written."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import preparation
import store
import workflow
import test_preparation as fixtures


class PreparationContracts(unittest.TestCase):
    def test_an_assessment_gap_cannot_reference_a_claim_outside_the_source_plan(self):
        from test_workflow_validation_boundaries import records
        plan=fixtures.small_plan();gap=records()['gaps'];gap.update(id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()));plan['records'].append(dict(kind='gaps',record=gap))
        with self.assertRaisesRegex(ValueError,'unavailable claim'):preparation.compile_plan(plan,workflow.canonical(plan),dict(id=str(uuid.uuid4()),paper_url=plan['paper_urls'][0]))

    def test_repeated_preparation_reads_back_the_same_preserved_plan_without_republishing_inputs(self):
        plan=fixtures.small_plan();raw=workflow.canonical(plan);study=dict(id=str(uuid.uuid4()),paper_url=plan['paper_urls'][0]);compiled=preparation.compile_plan(plan,raw,study)
        with patch.object(preparation,'registered',return_value=(plan,raw)),patch.object(store,'rows',side_effect=[[compiled],[]]),patch.object(store,'artifact') as artifact,patch.object(store,'append_bundle') as append:
            result=preparation.prepare(study,str(uuid.uuid4()),dict(action='prepare',request_id=str(uuid.uuid4())))
        self.assertTrue(result['replayed']);self.assertEqual(result['prepared']['id'],compiled['id']);artifact.assert_not_called();append.assert_not_called()
    def test_manifest_shape_names_provenance_and_graph_are_validated(self):
        base=fixtures.small_plan()
        mutations=[
            ('schema',lambda p:p.update(schema_version=2)),
            ('name',lambda p:p.update(plan_id='../unsafe')),
            ('urls',lambda p:p.update(paper_urls=[])),
            ('provenance',lambda p:p.update(provenance=[])),
            ('records',lambda p:p.update(records=[])),
            ('record-kind',lambda p:p['records'][0].update(kind='queue_jobs')),
            ('duplicate-record',lambda p:p['records'].append(copy.deepcopy(p['records'][0]))),
            ('campaigns',lambda p:p.update(campaigns=[])),
            ('campaign-fields',lambda p:p['campaigns'][0].update(unexpected=True)),
            ('campaign-key',lambda p:p['campaigns'][0].update(key='../unsafe')),
            ('graph-budget',lambda p:p['campaigns'][0].update(dependencies=[{}]*33)),
            ('graph-identity',lambda p:p['campaigns'][0].update(dependencies=[dict(key='missing',requirement='complete-run')])),
        ]
        for name,mutate in mutations:
            with self.subTest(name=name),patch.object(store,'request') as write:
                plan=copy.deepcopy(base);mutate(plan)
                with self.assertRaises(ValueError):preparation.parse(workflow.canonical(plan))
                write.assert_not_called()

    def test_compiler_rejects_missing_claims_protocols_and_unsupported_inputs(self):
        base=fixtures.small_plan();study=dict(id=str(uuid.uuid4()),paper_url=base['paper_urls'][0])
        def input_value(plan,value):
            item=plan['campaigns'][0];item['raw_input']=json.dumps(value)
            item['protocol']['input_versions'][0]['sha256']=workflow.digest(item['raw_input'].encode())
        mutations=[
            ('no-claims',lambda p:p.update(records=[row for row in p['records'] if row['kind']=='sources']),'genuinely sourced claim'),
            ('claim-source',lambda p:p['records'][1]['record'].update(source_id=str(uuid.uuid4())),'reference a source'),
            ('no-campaign-claims',lambda p:p['campaigns'][0].update(claim_ids=[]),'applicable claim'),
            ('duplicate-claim',lambda p:p['campaigns'][0]['claim_ids'].append(p['campaigns'][0]['claim_ids'][0]),'unique applicable'),
            ('protocol',lambda p:p['campaigns'][0].update(protocol=None),'inspected design'),
            ('input-type',lambda p:p['campaigns'][0].update(raw_input=None),'original input text'),
            ('input-bytes',lambda p:p['campaigns'][0].update(raw_input=' '*300000),'256 KB'),
            ('input-shape',lambda p:input_value(p,[]),'published and observed arrays'),
            ('input-arrays',lambda p:input_value(p,dict(published={},observed=[])),'explicit arrays'),
            ('input-observations',lambda p:input_value(p,dict(published=[],observed=[])),'observation coverage'),
            ('source-identity',lambda p:input_value(p,dict(published=[dict(source_id=str(uuid.uuid4()))],observed=[])),'unavailable source'),
            ('manual-input',lambda p:p['campaigns'][0].update(execution_mode='manual'),'no fabricated'),
        ]
        for name,mutate,message in mutations:
            with self.subTest(name=name),patch.object(store,'request') as write:
                plan=copy.deepcopy(base);mutate(plan)
                with self.assertRaisesRegex(ValueError,message):preparation.compile_plan(plan,workflow.canonical(plan),study)
                write.assert_not_called()

    def test_unknown_paper_and_oversized_compilation_do_not_upload(self):
        study=dict(id=str(uuid.uuid4()),paper_url='https://example.invalid/unregistered-paper')
        request=dict(action='prepare',request_id=str(uuid.uuid4()))
        with patch.object(store,'artifact') as upload,self.assertRaisesRegex(ValueError,'No registered preparation plan'):
            preparation.prepare(study,str(uuid.uuid4()),request)
        upload.assert_not_called()
        plan=fixtures.small_plan();request['plan_input']=workflow.canonical(plan).decode();study['paper_url']=plan['paper_urls'][0]
        with patch.object(preparation,'compile_plan',return_value={'id':str(uuid.uuid4()),'large':'x'*4000000}),patch.object(store,'rows',return_value=[]),patch.object(store,'artifact') as upload,self.assertRaisesRegex(ValueError,'private artifact budget'):
            preparation.prepare(study,str(uuid.uuid4()),request)
        upload.assert_not_called()


if __name__=='__main__':unittest.main()
