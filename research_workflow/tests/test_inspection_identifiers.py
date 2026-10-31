"""Inspection UUID boundary regressions; synthetic transport, no external calls."""
import copy
import unittest
import uuid
from unittest.mock import patch
from test_autonomy import answer, response, task
import autonomy
import autonomy_model as model

class InspectionIdentifiers(unittest.TestCase):
 def test_reader_reproduces_recorded_error_for_parser_previously_accepted_reference(self):
  value=answer('inspect');value.update(reason='retained-evidence',read_request=dict(kind='runtime-contract',id='bounded-tcp-v1',cursor=0))
  # The old parser admitted this shape as complete. The reader fails before
  # any store lookup, producing the recorded operational diagnostic.
  with patch.object(autonomy,'one') as lookup:
   with self.assertRaisesRegex(ValueError,'A UUID identifier is required.'):
    autonomy.inspect_evidence(task(),value['read_request'])
  lookup.assert_not_called()
 def test_generated_runtime_references_remain_usable_and_hash_bound(self):
  import autonomy_inputs as inputs
  import workflow
  contracts=inputs.runner_contracts();t=task()
  for name,contract in contracts.items():
   reference=inputs.runner_reference(name,contract);value=answer('inspect')
   value.update(reason='retained-evidence',read_request=reference['read_request'])
   parsed=model.parse(response(value))
   with patch.object(autonomy,'one',return_value={}),patch.object(autonomy,'inventory',return_value=[]),patch.object(autonomy,'retained_jobs',return_value=[]):
    read=autonomy.inspect_evidence(t,parsed['read_request'])
   self.assertEqual(read['request'],reference['read_request'])
   self.assertEqual(read['result']['document_sha256'],reference['sha256'])
   self.assertEqual(reference['sha256'],workflow.digest(workflow.canonical(contract)))
 def test_inspection_rejects_non_uuid_references_before_controller_reads(self):
  for kind in model.SCHEMA['properties']['read_request']['properties']['kind']['enum']:
   for record_id in ('bounded-tcp-v1','not-a-uuid',' ', 'a'*100):
    with self.subTest(kind=kind,record_id=record_id):
     value=answer('inspect');value.update(reason='retained-evidence',read_request=dict(kind=kind,id=record_id,cursor=0))
     with self.assertRaisesRegex(ValueError,'UUID'):
      model.parse(response(value))
 def test_invalid_inspection_is_retained_and_feedback_preserves_task_identity(self):
  t=task();t['context']=dict(builder_checkpoint={'id':'original-checkpoint'},decision_lock='a'*64)
  original=copy.deepcopy(t);events=[];value=answer('inspect')
  value.update(reason='retained-evidence',read_request=dict(kind='runtime-contract',id='bounded-tcp-v1',cursor=0))
  def control(t,op,payload):events.append((op,payload));return dict(replayed=False)
  with patch.object(model,'load_prompt',return_value=({'id':str(uuid.uuid4())},'System')):
   result=model.call(t,{'task':'planning'},control,lambda _:response(value))
  self.assertEqual(result['status'],'invalid-output');self.assertEqual(t['attempts'],1)
  receipt=events[-1][1];self.assertEqual(receipt['status'],'invalid-output');self.assertIsNone(receipt['output'])
  self.assertIn('bounded-tcp-v1',receipt['raw_response'])
  with patch.object(autonomy,'build_context',return_value={}),patch.object(model,'call',return_value=result),patch.object(autonomy,'inspect_evidence') as inspect,patch.object(autonomy,'advance',return_value={}) as advance:
   autonomy.step(t)
  inspect.assert_not_called();self.assertEqual(advance.call_args.args[1],'queued')
  context=advance.call_args.kwargs['context'];self.assertIn('UUID',context['last_response_error'])
  for key in ('builder_checkpoint','decision_lock'):self.assertEqual(context[key],original['context'][key])
  for key in ('id','study_id','actor_id','claim_id','lease_token','workflow'):self.assertEqual(t[key],original[key])
 def test_valid_inspection_uuid_is_preserved_without_rewriting_reference(self):
  for kind in model.SCHEMA['properties']['read_request']['properties']['kind']['enum']:
   value=answer('inspect');reference=dict(kind=kind,id=str(uuid.uuid4()),cursor=7)
   value.update(reason='retained-evidence',read_request=reference)
   self.assertEqual(model.parse(response(value))['read_request'],reference)
