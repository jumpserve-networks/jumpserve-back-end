"""Software regressions for the observed stalled assessment; no scientific claims."""
import copy,json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy,autonomy_inputs,autonomy_methods,autonomy_model,experiment_builder as builder,model_dispatch,netem_iperf,workflow
from test_executable_methods import sweep_config

class Recovery(unittest.TestCase):
 def test_condition_contract_matches_missing_observations_and_preserves_numeric_representation(self):
  cfg=sweep_config();contract=netem_iperf.observation_contract(cfg)
  self.assertEqual([r['observation_key'] for r in contract['observations']],['loss:0','loss:1','loss:2','loss:5','loss:10'])
  self.assertIn('Median successful',contract['idle_rtt_ms']);self.assertIn('null',contract['missing'])
  cfg['loss_levels_pct']=[0.0,5.0];self.assertEqual([r['observation_key'] for r in netem_iperf.observation_contract(cfg)['observations']],['loss:0.0','loss:5.0'])
  with patch.object(autonomy_inputs,'os'),patch('network_execution.enabled',return_value=True):self.assertEqual(autonomy_inputs.runner_contracts()['netem-iperf-v1']['output_contract']['version'],contract['version'])

 def test_netem_decisions_reject_nonexistent_keys_before_acquisition(self):
  claim=dict(id=str(uuid.uuid4()),claim_type='association')
  decision=dict(version='recorded-decision-v1',claim_id=claim['id'],kind='recorded-comparisons',coverage='complete-under-tested-conditions',rationale='Descriptive fixed condition contrast',
   groups=[dict(id=name,configuration='grid',metric='throughput_mbit_s',units='Mbit/s',observation_ids=[key]) for name,key in [('control','loss:0'),('treatment','loss:5')]],
   rules=[dict(id='gain',operation='relative-percent-change',groups=['control','treatment'],target='35',tolerance_absolute='1')])
  document=dict(execution=dict(runner='netem-iperf-v1'),decision=decision,metrics=[dict(name='throughput_mbit_s',units='Mbit/s',planned_observations=5)],configurations=[dict(identity='grid',details=dict(test_config=sweep_config()))])
  self.assertEqual(autonomy_methods.validate(document,claim),decision)
  decision['groups'][1]['observation_ids']=['replication-1:loss:5']
  with self.assertRaisesRegex(ValueError,'exact netem output contract'):autonomy_methods.validate(document,claim)

 def test_exact_relative_and_compound_reader_deliveries_are_credited_without_trusting_notes(self):
  context={'source_material':'text '*2000}
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'research-context.json').write_bytes(workflow.canonical(context));(root/'read-retained.py').write_text(builder.READ_RETAINED)
   import subprocess
   lines=[subprocess.check_output([sys.executable,str(root/'read-retained.py'),'context','source_material',str(page)],text=True) for page in (0,1)]
   call=dict(type='function_call',name='exec',call_id='reader',arguments=json.dumps(dict(command='python3 read-retained.py context source_material 0; python3 ./read-retained.py context source_material 1')))
   output=dict(type='function_call_output',call_id='reader',output=''.join(lines));history=[call,output]
   self.assertEqual(sorted(builder.delivered_pages(context,history)['source_material']),[0,1])
   changed=copy.deepcopy(context);changed['source_material']='changed';self.assertEqual(builder.delivered_pages(changed,history),{})
   malicious=dict(call,arguments=json.dumps(dict(command='echo invented; python3 read-retained.py context source_material 0')))
   self.assertEqual(builder.delivered_pages(context,[malicious,output]),{})
   (root/'progress.json').write_text('I completed everything');first=builder.work_progress(root,context,history)
   (root/'progress.json').write_text('Now complete again');self.assertEqual(builder.work_progress(root,context,history)['sha256'],first['sha256'])
   (root/'experiment.py').write_text('print(1)');self.assertNotEqual(builder.work_progress(root,context,history)['sha256'],first['sha256'])

 def test_read_timeouts_reconcile_one_dispatch_without_reenqueuing_or_recalling_provider(self):
  task=dict(study_id=str(uuid.uuid4()),id=str(uuid.uuid4()));turn=str(uuid.uuid4());raw=workflow.canonical(dict(output={},usage={}))
  complete=dict(status='complete',response_artifact_id=str(uuid.uuid4()),response_sha256=workflow.digest(raw),provenance={'status':'complete'})
  with patch.object(model_dispatch,'rpc',side_effect=[dict(replayed=False),TimeoutError('status read only'),complete,complete]) as rpc,patch.object(model_dispatch.store,'artifact_bytes',side_effect=[TimeoutError('artifact read only'),raw]),patch.object(model_dispatch.time,'sleep'):
   result=model_dispatch.invoke(task,turn,dict(modelId=model_dispatch.MODEL))
  self.assertEqual([c.args[0] for c in rpc.call_args_list],['enqueue','result','result','result']);self.assertEqual(result['output'],{})
  bad=dict(complete,response_sha256='0'*64)
  with patch.object(model_dispatch,'rpc',side_effect=[dict(replayed=False),bad]),patch.object(model_dispatch.store,'artifact_bytes',return_value=raw),self.assertRaisesRegex(ValueError,'identity mismatch'):model_dispatch.invoke(task,turn,dict(modelId=model_dispatch.MODEL))

 def test_uncertain_enqueue_reconciles_only_its_exact_stored_identity_without_a_second_post(self):
  task=dict(study_id=str(uuid.uuid4()),id=str(uuid.uuid4()));turn=str(uuid.uuid4());request=dict(modelId=model_dispatch.MODEL)
  raw=workflow.canonical(dict(output={},usage={}))
  stored=dict(id=turn,study_id=task['study_id'],task_id=task['id'],runtime=model_dispatch.VERSION,request_sha256=workflow.digest(workflow.canonical(request)),status='complete',response_artifact_id=str(uuid.uuid4()),response_sha256=workflow.digest(raw),provenance={})
  with patch.object(model_dispatch,'rpc',side_effect=[TimeoutError('Lost admission acknowledgement'),stored,stored]) as rpc,patch.object(model_dispatch.store,'artifact_bytes',return_value=raw):model_dispatch.invoke(task,turn,request)
  self.assertEqual([c.args[0] for c in rpc.call_args_list],['enqueue','result','result'])
  stored['request_sha256']='0'*64
  with patch.object(model_dispatch,'rpc',side_effect=[TimeoutError('Lost admission acknowledgement'),stored]),self.assertRaisesRegex(ValueError,'does not match'):model_dispatch.invoke(task,turn,request)

 def test_stalled_build_replans_once_then_stops_same_fingerprint_without_resetting_evidence(self):
  checkpoint=dict(artifact_id=str(uuid.uuid4()),sha256='b'*64)
  task=dict(stage='building',study_id=str(uuid.uuid4()),id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),attempts=38,context={'builder_work_progress':dict(sha256='a'*64,unchanged_slices=2)},workflow={})
  answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(checkpoint=checkpoint,work_progress=dict(version='builder-work-progress-v1',sha256='a'*64)))
  with patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:
   autonomy.step(task);self.assertEqual(advance.call_args.kwargs['stage'],'planning');context=advance.call_args.kwargs['context'];self.assertEqual(context['builder_checkpoint'],checkpoint)
   task['context']=context;autonomy.step(task);self.assertEqual(advance.call_args.args[1],'blocked')
   answer['runtime']['work_progress']['sha256']='c'*64;autonomy.step(task);self.assertEqual(advance.call_args.kwargs['stage'],'building');self.assertEqual(advance.call_args.kwargs['context']['builder_work_progress']['unchanged_slices'],0)
  self.assertEqual(task['attempts'],38)

 def test_authorized_budget_is_explicit_without_changing_the_intake_default(self):
  context=dict(resource_authorization=dict(version='assessment-budget-amendment-v1',max_spend_cents=100000,committed_cents=9980,deadline_at='2030-01-01T00:00:00Z'))
  self.assertIn('100000 cents',autonomy_model.resource_instruction(context));self.assertIsNone(autonomy.DEFAULTS['maximum_spend_usd'])
  context['resource_authorization']['max_spend_cents']=100001
  with self.assertRaises(ValueError):autonomy_model.resource_instruction(context)

if __name__=='__main__':unittest.main()
