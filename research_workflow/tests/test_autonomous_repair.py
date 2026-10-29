"""Exposed software regressions; synthetic provider failures, no live model calls."""
import copy,json,sys,tempfile,unittest,uuid,subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomous_repair as repair,autonomy,autonomy_inputs as inputs,autonomy_model as model,experiment_builder as builder,workflow,repair_validation

def task():
 return dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()),stage='planning',attempts=54,context={},workflow=dict(max_spend_cents=100000,committed_cents=15170,deadline_at='2030-01-01T00:00:00Z'))

class Repair(unittest.TestCase):
 def test_disabled_or_external_stops_do_not_admit_repairs(self):
  t=task()
  with patch.dict('os.environ',RESEARCH_AUTONOMY_REPAIR_ENABLED='false'),patch.object(repair,'rpc') as rpc:
   self.assertIsNone(repair.enqueue(t,{},'runtime failed'));rpc.assert_not_called()
  for kind in ('authorization','equipment','capacity'):
   self.assertIsNone(repair.category(t,'Missing required input',dict(blockers=[dict(kind=kind)])))
  self.assertEqual(repair.category(t,'Permission denied writing a development file',{}),'runtime')
  self.assertIsNone(repair.category(t,'Original hash differs',{}))

 def test_incident_identity_is_idempotent_and_keeps_original_snapshot_and_checkpoint(self):
  t=task();t['context']={'builder_checkpoint':{'artifact_id':str(uuid.uuid4()),'sha256':'a'*64}}
  before=copy.deepcopy(t);context=dict(t['context'],repair_failure_turn_id=str(uuid.uuid4()))
  with patch.dict('os.environ',RESEARCH_AUTONOMY_REPAIR_ENABLED='true'),patch.object(repair,'rpc',return_value={'task':{'status':'queued'}}) as rpc:
   repair.enqueue(t,context,'Required retained shared_campaigns identity is unavailable.');first=rpc.call_args.args[2]
   repair.enqueue(t,context,'Required retained shared_campaigns identity is unavailable.');self.assertEqual(first,rpc.call_args.args[2])
  self.assertEqual(first['original_context'],t['context']);self.assertEqual(json.loads(first['incident_raw'])['checkpoint'],t['context']['builder_checkpoint']);self.assertEqual(t,before)

 def diagnostic(self,dispatch,receipt=None,logical=None):
  t=task();d=dict(id=str(uuid.uuid4()),task_id=t['id'],study_id=t['study_id'],status='unknown',request_sha256='a'*64,provenance={});d.update(dispatch)
  blobs={};artifacts=[]
  for name,value in [('receipt.json',receipt),('logical.json',logical)]:
   if value is None:continue
   raw=workflow.canonical(value);p=dict(id=str(uuid.uuid4()),name=name,sha256=workflow.digest(raw));blobs[p['id']]=raw;artifacts.append(p)
  d['provenance']['artifacts']=artifacts
  if logical is not None:d['request_sha256']=workflow.digest(workflow.canonical(logical))
  def rows(task,kind,**kwargs):
   if kind=='model_dispatches':return [d]
   if kind=='artifacts':
    ident=kwargs['id'][3:];raw=blobs[ident];return [dict(sha256=workflow.digest(raw),byte_count=len(raw))]
   return []
  with patch.object(repair,'rows',side_effect=rows),patch.object(repair.store,'artifact_bytes',side_effect=lambda study,ident,**kw:blobs[ident]):return repair.diagnose(t,dict(category='runtime'))

 def test_confirmed_zero_transport_can_recover_without_refunding_or_replaying(self):
  value=self.diagnostic(dict(status='failed'),dict(provider_calls=0,provider_request_sent=False),dict(modelId='fixed',messages=[]))
  self.assertFalse(value['waiting']);self.assertEqual(value['reconciled_dispatches'][0]['kind'],'hash-verified-zero-provider-transport');self.assertFalse(value['validated'])

 def test_uncertain_and_malformed_zero_transport_outcomes_remain_fenced(self):
  for receipt in ({'provider_calls':1,'provider_request_sent':True},{'provider_calls':False,'provider_request_sent':False},{'provider_calls':0},{}):
   self.assertTrue(self.diagnostic({},receipt,dict(modelId='fixed'))['waiting'])

 def test_inflight_native_call_blocks_diagnosis_before_any_provider_action(self):
  with patch.object(repair,'rows',side_effect=[[{'status':'unknown'}],[]]):
   self.assertTrue(repair.diagnose(task(),dict(category='implementation'))['waiting'])

 def test_complete_original_response_requires_original_bytes_not_model_claims(self):
  t=task();raw=workflow.canonical({'output':{'message':{}},'usage':{}})
  d=dict(id=str(uuid.uuid4()),task_id=t['id'],study_id=t['study_id'],status='complete',request_sha256='a'*64,response_artifact_id=str(uuid.uuid4()),response_sha256=workflow.digest(raw),provenance={})
  def rows(task,kind,**kw):return [d] if kind=='model_dispatches' else []
  with patch.object(repair,'rows',side_effect=rows),patch.object(repair,'original',return_value=raw) as original:
   result=repair.diagnose(t,dict(category='runtime'));self.assertFalse(result['waiting']);self.assertEqual(original.call_count,1)
  with patch.object(repair,'rows',side_effect=rows),patch.object(repair,'original',side_effect=ValueError('hash differs')):
   with self.assertRaisesRegex(ValueError,'hash differs'):repair.diagnose(t,dict(category='runtime'))

 def test_wire_preflight_indexes_retrievable_contracts_and_keeps_exact_netem_keys(self):
  contracts=inputs.runner_contracts();ctx=dict(task='planning',runner_contracts=contracts,source_material=dict(filler='z'*60000),prior_attempts=[],resource_authorization=dict(version='assessment-budget-amendment-v1',max_spend_cents=100000,committed_cents=15170,deadline_at='2030-01-01T00:00:00Z'))
  before=copy.deepcopy(ctx)
  with patch.object(model,'OPENCLAW',True),patch.object(model,'MODEL','gpt-6.1-sol'):
   value=model.request('Policy '*2000,ctx);self.assertTrue(model.request_fits(value))
  delivered=json.loads(value['messages'][0]['content'][0]['text']);self.assertEqual(set(delivered['runner_contracts']),set(contracts))
  netem=delivered['runner_contracts']['netem-iperf-v1'];self.assertEqual(netem['output_contract'],contracts['netem-iperf-v1']['output_contract']);self.assertEqual(netem['read_request']['kind'],'runtime-contract')
  self.assertEqual(netem['sha256'],workflow.digest(workflow.canonical(contracts['netem-iperf-v1'])));self.assertEqual(ctx,before)

 def test_authorization_is_included_before_wire_admission_exactly_once(self):
  ctx=dict(task='planning',resource_authorization=dict(version='assessment-budget-amendment-v1',max_spend_cents=100000,committed_cents=15170,deadline_at='2030-01-01T00:00:00Z'))
  value=model.request('Policy',ctx);self.assertEqual(sum('Trusted current server resource authorization' in s['text'] for s in value['system']),1)
  self.assertIn('100000 cents',value['system'][-1]['text'])

 def test_runner_reads_are_version_bound_and_fresh_reviews_cannot_read_prior_turns(self):
  t=task();claim=dict(source_id=str(uuid.uuid4()));contract=inputs.runner_contracts()['netem-iperf-v1'];ref=inputs.runner_reference('netem-iperf-v1',contract)['read_request']
  with patch.object(autonomy,'one',return_value=claim),patch.object(autonomy,'inventory',return_value=[]),patch.object(autonomy,'retained_jobs',return_value=[]):
   result=autonomy.inspect_evidence(t,ref);self.assertIn('result',result)
   with self.assertRaisesRegex(ValueError,'identity/version'):autonomy.inspect_evidence(t,dict(ref,id=str(uuid.uuid4())))
   t['stage']='critic'
   with self.assertRaisesRegex(ValueError,'Fresh scientific reviews'):autonomy.inspect_evidence(t,dict(kind='controller-turn',id=str(uuid.uuid4()),cursor=0))

 def test_native_repair_focus_is_explicit_and_full_packet_stays_retrievable(self):
  context=dict(task='building',repair_directive=repair.REPAIR_INSTRUCTION,repair_packet=dict(incident='Controlled development failure',required='Do not fabricate a result.'),claim=dict(description='Original mechanism claim'))
  logical=dict(system=[dict(text='Actual server policy')],builder=dict(context=context))
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);workspace=root/'workspace';workspace.mkdir();directory=root/'dispatch';directory.mkdir()
   message,manifest=builder.bootstrap(logical,workspace,directory,[])
   self.assertIn('operational-repair mode',message);self.assertIn('repair_packet',[f['key'] for f in json.loads((workspace/'context-index.json').read_bytes())['fields']])
   self.assertEqual(json.loads((workspace/'research-context.json').read_bytes()),context)

 def test_actual_server_test_runner_exercises_corrected_code_and_rejects_no_op_checks(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'repair-test-plan.json').write_text('{}')
   (root/'experiment.py').write_text('def value(x):\n return x * 2\n')
   (root/'repair_test.py').write_text('import unittest,experiment\nclass Checks(unittest.TestCase):\n def test_original_failure(self):\n  with self.assertRaises(TypeError):experiment.value(None)\n def test_corrected_behavior(self):\n  self.assertEqual(experiment.value(7),14)\n')
   good=subprocess.run([sys.executable,'-I','-c',repair_validation.RUNNER],cwd=root,capture_output=True,timeout=10)
   self.assertEqual(good.returncode,0,good.stderr);report=json.loads(good.stdout);self.assertEqual(report['tests_run'],2);self.assertEqual(report['experiment_functions_executed'],['value'])
   (root/'experiment.py').write_text('def value(x):\n return x\n')
   bad=subprocess.run([sys.executable,'-I','-c',repair_validation.RUNNER],cwd=root,capture_output=True,timeout=10)
   self.assertNotEqual(bad.returncode,0);self.assertFalse(json.loads(bad.stdout)['passed'])
   (root/'repair_test.py').write_text('import unittest\nclass Checks(unittest.TestCase):\n def test_original_failure(self):pass\n def test_corrected_behavior(self):pass\n')
   noop=subprocess.run([sys.executable,'-I','-c',repair_validation.RUNNER],cwd=root,capture_output=True,timeout=10)
   self.assertNotEqual(noop.returncode,0);self.assertEqual(json.loads(noop.stdout)['experiment_functions_executed'],[])

 def test_tight_bootstrap_keeps_actual_next_action_ahead_of_long_repair_directive(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);work=root/'workspace';work.mkdir();directory=root/'dispatch';directory.mkdir()
   logical=dict(system=[dict(text='Policy')],builder=dict(context=dict(repair_directive='x'*3100)))
   baseline,_=builder.bootstrap(logical,work,directory,[])
   policy='Policy'+'P'*(builder.BOOTSTRAP_BYTES-len(baseline.encode())-700)
   feedback='The retained implementation and tests are complete. Write the exact incident plan and execute the existing tests; do not repeat completed inspection.'
   context=dict(task='building',builder_feedback=feedback,claim=dict(description='Original claim'),repair_directive=repair.REPAIR_INSTRUCTION)
   logical=dict(system=[dict(text=policy)],builder=dict(context=context))
   message,manifest=builder.bootstrap(logical,work,directory,[])
   self.assertLessEqual(len(message.encode()),builder.BOOTSTRAP_BYTES)
   self.assertIn('builder_feedback',manifest['inline_fields']);self.assertIn(feedback,message)
   self.assertTrue(message.startswith(policy));self.assertEqual(json.loads((work/'research-context.json').read_bytes()),context)

 def test_production_sized_policies_keep_long_feedback_and_real_read_loop_inventory(self):
  # Matches the observed policy byte sizes, including the formerly lost 1565+
  # byte directive. These are development fixtures, not independent validation.
  systems=[dict(text='P'*size) for size in (6889,969,2036,1871,546)]
  systems[2]['text']=builder.action_policy()['document']['instruction']
  ctx=dict(task='building',repair_directive=repair.REPAIR_INSTRUCTION,builder_feedback=repair.REPAIR_INSTRUCTION)
  history=[dict(type='function_call',call_id=str(i),name='read',arguments='{"path":"context-index.json"}') for i in range(121)]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);message,manifest=builder.bootstrap(dict(system=systems,builder=dict(context=ctx)),root,root,history)
   self.assertLessEqual(len(message.encode()),16000)
   self.assertIn('builder_feedback',manifest['inline_fields']);self.assertIn('No implementation files exist',message)
   self.assertIn('121',message)
   for system in systems:self.assertIn(system['text'],message)
   self.assertEqual(json.loads((root/'research-context.json').read_bytes()),ctx)

 def test_metadata_locations_bind_exact_incident_bytes_and_reject_ambiguity(self):
  with tempfile.TemporaryDirectory() as tmp:
   work=Path(tmp);(work/'project').mkdir();ctx=dict(repair_packet=dict(incident_sha256='a'*64))
   raw=workflow.canonical(dict(version=repair_validation.VERSION,incident_sha256='a'*64,scope='software-development-only'))
   (work/'repair-test-plan.json').write_bytes(raw)
   self.assertEqual(repair_validation.test_plan(work,ctx),[('repair-test-plan.json',raw)])
   (work/'project/repair-test-plan.json').write_bytes(raw)
   self.assertEqual(len(repair_validation.test_plan(work,ctx)),2)
   (work/'project/repair-test-plan.json').write_bytes(raw+b'\n')
   with self.assertRaisesRegex(ValueError,'Conflicting'):repair_validation.test_plan(work,ctx)
   (work/'project/repair-test-plan.json').write_bytes(raw.replace(b'a'*64,b'b'*64))
   with self.assertRaisesRegex(ValueError,'exact incident'):repair_validation.test_plan(work,ctx)
   (work/'project/repair-test-plan.json').unlink();(work/'project/repair-test-plan.json').symlink_to(work/'repair-test-plan.json')
   with self.assertRaisesRegex(ValueError,'regular file'):repair_validation.test_plan(work,ctx)

 def test_repair_cannot_skip_executed_check_receipt_before_fresh_scientific_review(self):
  t=task();ref=dict(id=str(uuid.uuid4()),state='repairing');t['context']['repair_progress']=ref
  record=dict(ref,version=repair.VERSION,revision=1,incident_sha256='a'*64,evidence={})
  ctx=dict(t['context'],proposal=dict(protocol=dict(configurations=[])))
  with patch.dict('os.environ',RESEARCH_AUTONOMY_REPAIR_ENABLED='true'),patch.object(repair,'rows',return_value=[record]),patch.object(repair,'transition') as transition:
   with self.assertRaisesRegex(ValueError,'validation receipt required'):repair.route(t,'queued',ctx,'comprehension','Fresh reviews pending')
   transition.assert_not_called()

 def test_diagnosis_is_actually_delivered_with_original_hash_and_admitted_request_unchanged(self):
  raw=workflow.canonical(dict(version=repair.VERSION,failure_details=[dict(error='Explicit synthetic stderr')]))
  pointer=dict(artifact_id=str(uuid.uuid4()),sha256=workflow.digest(raw),byte_count=len(raw))
  logical=dict(builder=dict(context=dict(repair_packet=dict(diagnosis=pointer))))
  before=copy.deepcopy(logical)
  with patch.object(builder.store,'artifact_bytes',return_value=raw):
   delivered=builder.deliver_repair_diagnosis(logical,dict(study_id=str(uuid.uuid4())))
  self.assertEqual(logical,before);self.assertEqual(delivered['builder']['context']['repair_diagnostics']['document']['failure_details'][0]['error'],'Explicit synthetic stderr')
  with patch.object(builder.store,'artifact_bytes',return_value=b'changed'):
   with self.assertRaisesRegex(ValueError,'identity mismatch'):builder.deliver_repair_diagnosis(logical,dict(study_id=str(uuid.uuid4())))

 def test_uncertain_repair_waits_without_model_call_and_uses_backoff(self):
  t=task();record=dict(id=str(uuid.uuid4()),version=repair.VERSION,state='diagnosing',category='runtime',revision=0,updated_at='2026-10-28T00:00:00Z')
  t['context']['repair_progress']=repair.progress(record)
  def transition(task,record,state,reason,evidence):return dict(record,state=state,revision=record['revision']+1,reason=reason,evidence=evidence)
  with patch.object(repair,'rows',return_value=[record]),patch.object(repair,'diagnose',return_value=dict(waiting=True,reason='Original provider outcome unconfirmed')),patch.object(repair,'save_diagnosis',return_value={'sha256':'a'*64}),patch.object(repair,'transition',side_effect=transition),patch.object(autonomy,'advance') as advance,patch.object(autonomy,'step') as step:
   repair.drive(t,step,advance);step.assert_not_called();self.assertEqual(advance.call_args.args[1],'waiting');self.assertEqual(advance.call_args.kwargs['delay'],60);self.assertEqual(advance.call_args.kwargs['context']['repair_progress']['state'],'waiting')

if __name__=='__main__':unittest.main()
