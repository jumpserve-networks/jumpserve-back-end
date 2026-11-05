"""Parallel intake preserves source identity, exact coverage and existing limits."""
import copy,json,sys,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy as a,intake_parallel as parallel,paper_selection as selection,autonomy_model as model
from workflow import canonical,digest
from test_autonomy import task,answer
from test_paper_selection import records,output

class IntakeParallel(unittest.TestCase):
 def test_nine_candidates_fit_one_complete_request_including_real_provider_schema(self):
  values=records(9);before=canonical(values)
  with patch.object(model,'OPENCLAW',True),patch.object(model,'MODEL','gpt-6.1-sol'):
   context=selection.context(dict(title='Fixture'),values,0,{},fits=lambda c:model.context_fits('Server policy '*500,c))
   self.assertEqual(len(context['candidates']),9)
   self.assertTrue(model.request_fits(model.request('Server policy '*500,context)))
  self.assertEqual(canonical(values),before)
 def test_complete_byte_budget_preserves_unicode_and_covers_every_candidate_once(self):
  values=records(35)
  for row in values:row['description']='網'*2000
  cursor=0;seen=[];before=canonical(values)
  with patch.object(model,'OPENCLAW',True),patch.object(model,'MODEL','gpt-6.1-sol'):
   while cursor<len(values):
    result=selection.context(dict(title='Fixture'),values,cursor,{},fits=lambda c:model.context_fits('Policy '*1000,c))
    self.assertTrue(model.request_fits(model.request('Policy '*1000,result)))
    seen.extend(row['id'] for row in result['candidates']);cursor+=len(result['candidates'])
  self.assertEqual(seen,[row['id'] for row in values]);self.assertEqual(canonical(values),before)
 def test_parallel_manifest_only_includes_remaining_exact_chunks(self):
  t=task('intake');t['lease_token']=str(uuid.uuid4());context=dict(chunk_index=1)
  chunks=[dict(page=i+1,text='Original '+str(i),offset=0) for i in range(3)]
  with patch.object(parallel,'rpc',return_value=dict(complete=0,remaining=2)) as rpc,patch.object(a,'advance') as advance:
   parallel.coordinate(t,context,chunks)
  self.assertEqual(rpc.call_args.args[1]['chunks'],[dict(index=i,sha256=digest(canonical(chunks[i]))) for i in (1,2)])
  self.assertEqual(advance.call_args.args[1],'waiting');self.assertEqual(context,dict(chunk_index=1))
 def test_unconfirmed_parent_does_not_get_replaced_by_chunk_work(self):
  t=task('intake');t['lease_token']=str(uuid.uuid4())
  with patch.object(parallel,'rpc',return_value=dict(blocked=True,reason='Original call unconfirmed')),patch.object(a,'advance') as advance:
   parallel.coordinate(t,dict(chunk_index=0),[dict(page=1,text='Original',offset=0)])
  self.assertEqual(advance.call_args.args[1],'blocked');self.assertIn('unconfirmed',advance.call_args.kwargs['reason'])
 def test_child_checks_quote_and_page_before_any_claim_write(self):
  t=task('intake');chunk=dict(page=1,text='Latency is 10 ms.',offset=0)
  t.update(intake_parent_id=str(uuid.uuid4()),intake_chunk_index=0)
  t['context']=dict(source_id=str(uuid.uuid4()),text_artifact_id=str(uuid.uuid4()),metadata_examined=True,intake_chunk_sha256=digest(canonical(chunk)))
  value=answer('discover');value['claims']=[dict(page=2,quote=chunk['text'],claim_type='numerical')]
  with patch.object(parallel.store,'artifact_bytes',return_value=canonical(dict(pages=[dict(page=1,text=chunk['text'])]))),patch.object(a,'one',return_value={}),patch.object(a,'rows',return_value=[]),patch.object(model,'call',return_value=dict(id='turn',status='complete',output=value)),patch.object(parallel.store,'append_bundle') as append:
   with self.assertRaisesRegex(ValueError,'exact supplied source'):parallel.extract(t,dict(id=t['study_id']))
  append.assert_not_called()
 def test_warm_grouping_finishes_without_an_extra_controller_turn(self):
  t=task('intake');t['intake_pool']=True;values=records(9)
  review=dict(seeds=[dict(claim_id=c['id']) for c in values],metadata={})
  value=answer('discover');value['paper_review_selection']=output(values)
  with patch.object(model,'load_prompt',return_value=({},'Policy')),patch.object(a,'rows',return_value=[]),patch.object(model,'call',return_value=dict(id='turn',status='complete',output=value)),patch.object(a.store,'rpc'),patch.object(a,'advance') as advance:
   a.organize_review(t,dict(id=t['study_id'],title='Fixture'),{},review,values)
  self.assertEqual(advance.call_args.args[1],'finished');self.assertEqual(advance.call_args.kwargs['context']['review_selection']['cursor'],9)

 def test_restart_reuses_only_the_exact_complete_captured_chunk(self):
  t=task('intake');context=dict(task='discover',source_chunk=dict(page=1,text='Original'),prior_attempt=1)
  raw=canonical(model.request('Policy',dict(context,prior_attempt=0))).decode()
  turn=dict(id='original-turn',status='complete',request_raw=raw,request_sha256=digest(raw.encode()),output=answer('discover'))
  with patch.object(a,'rows',return_value=[turn]),patch.object(model,'call') as infer:
   result=parallel.recorded_call(t,context)
   self.assertEqual(result['id'],'original-turn');self.assertTrue(result['retained_complete']);infer.assert_not_called()
  for changed in (dict(turn,status='unknown'),dict(turn,status='failed')):
   with patch.object(a,'rows',return_value=[changed]),patch.object(model,'call',return_value=dict(denied='in-flight')) as infer:
    self.assertEqual(parallel.recorded_call(t,context),dict(denied='in-flight'));infer.assert_called_once()
  with patch.object(a,'rows',return_value=[dict(turn,request_sha256='f'*64)]),patch.object(model,'call') as infer:
   with self.assertRaisesRegex(ValueError,'hash differs'):parallel.recorded_call(t,context)
   infer.assert_not_called()
  with patch.object(a,'rows',return_value=[turn]),patch.object(model,'call',return_value=dict(status='new-context')) as infer:
   parallel.recorded_call(t,dict(context,source_chunk=dict(page=2,text='Different')));infer.assert_called_once()

if __name__=='__main__':unittest.main()
