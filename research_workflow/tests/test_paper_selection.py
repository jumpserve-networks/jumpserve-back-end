"""Exposed development fixtures; no live AI taxonomy accuracy is asserted."""
import copy
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy, autonomy_model as model, paper_selection as selection, paper_uploads, store
from workflow import canonical, digest
from test_autonomy import task, answer, response

def records(count):
 return [dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL,'selection:'+str(i))),source_id=str(uuid.uuid4()),location='PDF page '+str(i+1),
  description='Retained statement '+str(i+1)+': latency is 10 ms under fixed conditions.',claim_type='numerical') for i in range(count)]
def output(values,central=1):
 return dict(central_claims=[dict(claim_id=c['id'],label=c['description'][:100]) for c in values[:central]],
  testable_findings=[dict(claim_id=c['id'],label=c['description'][:100]) for c in values[central:central+15]],rationale='Exposed unit classification fixture only.')

class PaperSelection(unittest.TestCase):
 def test_policy_revisions_do_not_change_the_storage_envelope(self):
  self.assertEqual(selection.VERSION,'paper-review-selection-v1')
  self.assertEqual(selection.POLICY['version'],'paper-review-selection-policy-v2')
  context=selection.context(dict(title='Fixture'),records(2),0,{})
  self.assertEqual(context['policy_sha256'],digest(canonical(selection.POLICY)))
  self.assertIn('unproved',context['policy']['instruction'])
 def test_184_candidates_are_batched_without_losing_original_units_or_identities(self):
  values=records(184);before=canonical(values);cursor=0;seen=[];prior={};turns=0
  while cursor<len(values):
   context=selection.context(dict(title='Fixture paper'),values,cursor,prior)
   self.assertLessEqual(len(context['candidates']),8);self.assertLessEqual(len(canonical(context['candidates'])),12000)
   self.assertLessEqual(len(canonical(model.request('Server policy '*1000,context))),96000)
   seen.extend(c['id'] for c in context['candidates']);cursor+=len(context['candidates']);turns+=1
   prior=output(values[:3],central=1)
  self.assertEqual(seen,[c['id'] for c in values]);self.assertEqual(turns,23);self.assertEqual(canonical(values),before)
  self.assertEqual(context['candidate_inventory_sha256'],digest(before));self.assertIn('10 ms',values[0]['description'])
 def test_unicode_batches_are_bounded_in_bytes_not_characters_and_never_truncated(self):
  values=records(4)
  for c in values:c['description']='網'*2000
  cursor=0
  while cursor<4:
   batch=selection.batch(values,cursor);self.assertEqual(len(batch),1);self.assertEqual(batch[0]['description'],'網'*2000);cursor+=1
  values[0]['description']='網'*5000
  with self.assertRaisesRegex(ValueError,'manual classification'):selection.batch(values,0)
 def test_no_minimum_quota_and_no_duplicate_cross_paper_or_unexamined_ids(self):
  values=records(5);allowed={c['id'] for c in values}
  self.assertEqual(selection.validate(dict(central_claims=[],testable_findings=[],rationale='No supported original contributions in these statements.'),allowed)['central_claims'],[])
  invalid=[output(values,central=4),dict(output(values),testable_findings=[dict(claim_id=values[0]['id'],label='Duplicate')]),dict(output(values),central_claims=[dict(claim_id=str(uuid.uuid4()),label='Invented ID')])]
  for value in invalid:
   with self.assertRaises(ValueError):selection.validate(value,allowed)
 def test_only_seeded_candidates_are_available_and_missing_seed_identity_fails(self):
  values=records(5);seeds=[dict(claim_id=c['id']) for c in values[:3]]
  self.assertEqual(len(selection.candidates(values,seeds)),3)
  with self.assertRaises(ValueError):selection.candidates(values[:2],seeds)
 def test_owner_selection_supports_either_group_without_forcing_target_counts(self):
  values=records(20);ids=[c['id'] for c in values]
  for central,findings in [([],ids[:1]),(ids[:1],[]),(ids[:4],ids[4:])]:
   self.assertEqual(selection.selection_ids(dict(central_claims=central,testable_findings=findings),ids),central+findings)
  for value in [dict(central_claims=[],testable_findings=[]),dict(central_claims=ids[:1],testable_findings=ids[:1]),dict(central_claims=[str(uuid.uuid4())],testable_findings=[])]:
   with self.assertRaises(ValueError):selection.selection_ids(value,ids)
 def test_only_organization_requests_accept_the_additive_schema(self):
  values=records(2);context=selection.context(dict(title='Fixture'),values,0,{})
  request=model.request('System',context);schema=request['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']
  self.assertIn('paper_review_selection',schema['required']);self.assertEqual(schema['properties']['claims']['maxItems'],0)
  self.assertNotIn('paper_review_selection',model.SCHEMA['properties'])
  with self.assertRaises(ValueError):model.request('System',dict(task='planning',paper_review_selection_requested=True))
 def test_invalid_model_ids_and_findings_are_recorded_as_invalid_output(self):
  values=records(2);context=selection.context(dict(title='Fixture'),values,0,{})
  for change in ('foreign','overlap','verdict'):
   value=answer('discover');value.update(reason='text-extracted',paper_review_selection=output(values))
   if change=='foreign':value['paper_review_selection']['central_claims'][0]['claim_id']=str(uuid.uuid4())
   if change=='overlap':value['paper_review_selection']['testable_findings'].append(value['paper_review_selection']['central_claims'][0])
   if change=='verdict':value.update(assessment_label='reproduced',coverage_complete=True)
   events=[]
   def control(_task,operation,payload):events.append((operation,payload));return dict(replayed=False)
   with patch.object(model,'load_prompt',return_value=(dict(id=str(uuid.uuid4())),'System')):
    result=model.call(task('intake'),context,control,lambda _:response(value))
   self.assertEqual(result['status'],'invalid-output');self.assertEqual(events[-1][0],'finish');self.assertIsNotNone(events[-1][1]['raw_response'])
 def test_grouping_turns_are_retained_and_do_not_seed_experiments(self):
  t=task('intake');t['claim_id']=None;values=records(3);review=dict(seeds=[dict(claim_id=c['id']) for c in values],metadata={})
  response_value=answer('discover');response_value.update(paper_review_selection=output(values))
  with patch.object(model,'call',return_value=dict(id='retained-turn',status='complete',output=response_value)),patch.object(store,'rpc') as rpc,patch.object(autonomy,'seed') as seed,patch.object(autonomy,'advance',return_value={}) as advance:
   autonomy.organize_review(t,dict(id=t['study_id'],title='Fixture'),{},review,values)
  seed.assert_not_called();saved=rpc.call_args.args[1]['p_payload']['selection'];self.assertEqual(saved['cursor'],3);self.assertEqual(saved['status'],'ready');self.assertEqual(saved['last_turn_id'],'retained-turn');self.assertEqual(advance.call_args.args[1],'queued')
 def test_recovery_uses_persisted_group_cursor_without_repeating_a_model_turn(self):
  t=task('intake');values=records(2);saved=dict(output(values),version=selection.VERSION,status='ready',cursor=2,candidate_count=2,candidate_inventory_sha256=digest(canonical(values)))
  review=dict(seeds=[dict(claim_id=c['id']) for c in values],metadata=dict(selection=saved))
  with patch.object(model,'call') as call,patch.object(autonomy,'advance',return_value={}) as advance:
   autonomy.organize_review(t,dict(id=t['study_id'],title='Fixture'),{},review,values)
  call.assert_not_called();self.assertEqual(advance.call_args.args[1],'finished')
 def test_oversized_statement_degrades_to_explicit_manual_selection_without_a_model_call(self):
  t=task('intake');values=records(1);values[0]['description']='網'*5000;review=dict(seeds=[dict(claim_id=values[0]['id'])],metadata={})
  with patch.object(model,'call') as call,patch.object(store,'rpc') as rpc,patch.object(autonomy,'advance',return_value={}) as advance:
   autonomy.organize_review(t,dict(id=t['study_id'],title='Fixture'),{},review,values)
  call.assert_not_called();self.assertEqual(rpc.call_args.args[1]['p_operation'],'metadata');self.assertEqual(advance.call_args.args[1],'finished')
 def test_organization_is_owner_only_before_any_read_or_write(self):
  with patch.object(store,'owns',return_value=False),patch.object(store,'rpc') as rpc,self.assertRaises(ValueError):
   paper_uploads.submit(str(uuid.uuid4()),dict(action='organize',request_id=str(uuid.uuid4())))
  rpc.assert_not_called()

if __name__=='__main__':unittest.main()
