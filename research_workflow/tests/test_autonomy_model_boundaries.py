"""Controller response and request bounds; no live provider invocations."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_model as model
import experiment_builder,model_dispatch,openclaw_runtime
from test_native_repair_handoff import context as repair_context
import workflow
from test_autonomy import answer,response,task


class AutonomyModelBoundaries(unittest.TestCase):
    def call(self,context,provider):
        receipts=[]
        def control(task,action,payload):
            receipts.append((action,copy.deepcopy(payload)))
            return {'replayed':False}
        with patch.object(model,'load_prompt',return_value=({'id':'synthetic-prompt'},'Trusted controller system')):
            result=model.call(task(context.get('task','planning')),context,control,provider)
        return result,receipts

    def test_metadata_requires_exact_grounded_shape_and_cannot_enter_an_unrequested_phase(self):
        for metadata in [{},[],{'title':0,'domain':None},{'title':{'text':'Title','quote':'Original quote','page':True},'domain':None}]:
            with self.subTest(metadata=metadata),self.assertRaisesRegex(ValueError,'metadata'):model.parse(response(dict(answer(),paper_metadata=metadata)))
        grounded={'title':{'text':'Original title','quote':'Original source title','page':1},'domain':None}
        original=dict(answer('discover'),reason='text-extracted',paper_metadata=grounded);self.assertEqual(model.parse(response(original)),dict(original,reason=model.REASONS['text-extracted']))
        result,receipts=self.call({'task':'planning'},lambda _:response(original));self.assertEqual(result['status'],'invalid-output');self.assertIn('not requested',result['reason']);self.assertIsNotNone(receipts[-1][1]['raw_response'])
        result,_=self.call({'task':'discover','paper_metadata_requested':True},lambda _:response(dict(answer('discover'),reason='text-extracted')));self.assertEqual(result['status'],'invalid-output');self.assertIn('requires paper metadata',result['reason'])

    def test_response_schema_rejects_unknown_actions_rationales_bad_flags_and_excessive_collections(self):
        changes=[{'action':'execute-shell'},{'reason':'Invented reason'},{'coverage_complete':None},{'ready':'true'},{'assessment_label':'proved'},{'claims':None},{'claims':[{}]*21},{'blockers':[{}]*11},{'evidence_ids':[{}]*101},{'check_id':0},{'check_id':'x'*101},{'proposal':[]},
            {'claims':[{'page':False,'quote':'Original','claim_type':'numerical'}]},
            {'blockers':[{'kind':'unknown','detail':'x','required_input':'x'}]},
            {'blockers':[{'kind':'design','detail':'invented-detail','required_input':'Original'}]},
            {'blockers':[{'kind':'capacity','detail':'resource-exhausted','required_input':'Reset the frozen limits'}]}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):model.parse(response(dict(answer(),**change)))

    def test_large_planning_context_references_exact_source_fragments_and_prior_outputs_without_mutation(self):
        context={'task':'planning','source_material':{'inline_fragments':[{'text':'x'*100000}], 'inline_fragment_coverage':{'original_bytes':100000},'inline_reading_guidance':'Original scope'},'prior_attempts':[{'id':'original-turn','output':{'original':'x'*20000}},{'id':'original-unanswered-turn','output':None}]}
        original=copy.deepcopy(context);value=model.request('Trusted system',context);compacted=__import__('json').loads(value['messages'][0]['content'][0]['text'])
        self.assertNotIn('inline_fragments',compacted['source_material']);self.assertEqual(compacted['source_material']['retained_fragment_coverage'],original['source_material']['inline_fragment_coverage']);self.assertEqual(compacted['prior_attempts'][0]['output_reference']['sha256'],workflow.digest(workflow.canonical(original['prior_attempts'][0]['output'])));self.assertEqual(context,original)
        with patch.object(model,'request',side_effect=ValueError('Invalid unrelated request')),self.assertRaisesRegex(ValueError,'unrelated'):model.context_fits('Trusted system',{})

    def test_provider_adapter_uses_pinned_region_single_attempt_and_the_exact_request(self):
        client=Mock();original={'modelId':'synthetic-model','messages':[]}
        with patch('boto3.client',return_value=client) as factory:
            self.assertIs(model.invoke(original),client.converse.return_value)
        self.assertEqual(factory.call_args.kwargs['region_name'],'us-east-1');self.assertEqual(factory.call_args.kwargs['config'].retries['total_max_attempts'],1);client.converse.assert_called_once_with(**original)

    def test_output_byte_overflow_is_failed_retained_and_never_truncated_into_a_valid_response(self):
        receipts=[]
        def control(task,action,payload):receipts.append((action,payload));return{'replayed':False}
        oversized=response(answer());oversized['output']['message']['content']=[{'text':'x'*100001}]
        with patch.object(model,'load_prompt',return_value=({'id':'synthetic'},'System')),self.assertRaises(model.ModelFailure):model.call(task(),{},control,lambda _:oversized)
        self.assertEqual(receipts[-1][0],'finish');self.assertEqual(receipts[-1][1]['status'],'failed');self.assertIsNone(receipts[-1][1]['raw_response'])

    def test_provider_http_failure_keeps_typed_runtime_metadata_and_does_not_retry(self):
        failure=RuntimeError('Synthetic private provider detail');failure.response={'ResponseMetadata':{'HTTPStatusCode':503}}
        receipts=[];provider=Mock(side_effect=failure)
        def control(task,action,payload):receipts.append((action,payload));return{'replayed':False}
        with patch.object(model,'load_prompt',return_value=({'id':'synthetic'},'System')),self.assertRaises(model.ModelFailure):model.call(task(),{},control,provider)
        self.assertEqual(provider.call_count,1);receipt=receipts[-1][1];self.assertEqual(receipt['usage']['tokens']['research_runtime']['http_status'],503);self.assertNotIn('private provider detail',receipt['reason']);self.assertEqual(receipt['usage']['automatic_retries'],0)


    def test_native_data_turns_use_the_durable_dispatch_adapter_once_and_retain_its_completion(self):
        decision=dict(answer(),reason='missing-required-inputs',blockers=[dict(kind='missing-data',detail='missing-source-data',required_input='Original source dataset')])
        with patch.object(model,'OPENCLAW',True),patch.object(model,'MODEL',openclaw_runtime.MODEL),patch.object(model_dispatch,'invoke',return_value=response(decision)) as invoke:
            result,receipts=self.call({'task':'planning'},None)
        self.assertEqual(result['status'],'complete',result);self.assertEqual(invoke.call_count,1);self.assertEqual(receipts[-1][0],'finish');self.assertEqual(receipts[-1][1]['status'],'complete')

    def test_native_repair_handoff_is_verified_before_an_unavailable_builder_can_admit_a_model_request(self):
        context=repair_context();context['task']='building';context['native_repair_handoff']=experiment_builder.repair_handoff(context);control=Mock()
        with patch.object(model,'OPENCLAW',False),patch.object(experiment_builder,'repair_handoff',wraps=experiment_builder.repair_handoff) as handoff,self.assertRaisesRegex(ValueError,'builder is unavailable'):model.call(task('building'),context,control)
        handoff.assert_called_once_with(context);control.assert_not_called()


if __name__=='__main__':unittest.main()
