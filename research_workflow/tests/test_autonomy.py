import copy
import datetime as dt
import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import api,autonomy,autonomy_model as model,autonomy_sources as sources,store,workflow

def answer(action='blocked'):
 return dict(action=action,reason='Specific recorded evidence gap.',blockers=[],claims=[],check_id=None,proposal=None,ready=None,coverage_complete=False,assessment_label=None,evidence_ids=[])
def response(value):return dict(stopReason='tool_use',output=dict(message=dict(role='assistant',content=[dict(toolUse=dict(name='submit_assessment_step',input=value,toolUseId='test'))])),usage=dict(inputTokens=100,outputTokens=20))
def task(stage='planning'):
 return dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()),attempts=0,stage=stage,context={},workflow=dict(initial_prompt='Exact initial form',max_spend_cents=10000,committed_cents=500,deadline_at='2026-10-25T00:00:00Z'))

class Autonomy(unittest.TestCase):
 def test_intake_is_atomic_and_server_owns_defaults(self):
  body=dict(request_id=str(uuid.uuid4()),autonomous=True,paper=dict(title='Paper',paper_url='https://example.org/main.pdf',domain='Networking',scope='Declared scope'))
  with patch.object(store,'rpc',return_value={'id':body['request_id']}) as rpc,patch.object(autonomy,'wake',return_value={'status':'deferred'}):
   autonomy.start(str(uuid.uuid4()),body)
   payload=rpc.call_args.args[1]['p_payload'];initial=json.loads(payload['initial_prompt'])
   self.assertEqual(initial['defaults']['maximum_spend_usd'],100);self.assertEqual(initial['defaults']['elapsed_days'],7);self.assertEqual(initial['defaults']['attempts_per_claim'],25)
   with self.assertRaises(ValueError):autonomy.start(str(uuid.uuid4()),dict(body,max_spend=1000))
 def test_prompt_precedes_provider_and_failure_counts_and_is_retained(self):
  t=task();events=[];system='Server-owned prompt';prompt=dict(id=str(uuid.uuid4()))
  def control(task,op,payload):
   events.append((op,payload));return dict(replayed=False)
  def invoke(value):
   self.assertEqual(events[0][0],'begin');self.assertEqual(json.loads(events[0][1]['request_raw']),value);raise TimeoutError()
  with patch.object(model,'load_prompt',return_value=(prompt,system)):
   with self.assertRaisesRegex(ValueError,'unconfirmed'):model.call(t,{},control,invoke)
  self.assertEqual(t['attempts'],1);self.assertEqual(events[-1][0],'finish');self.assertEqual(events[-1][1]['status'],'failed');self.assertIsNone(events[-1][1]['estimated_cents'])
 def test_unknown_spend_and_expired_pricing_are_never_zero(self):
  self.assertIsNone(model.estimate({}));self.assertIsNone(model.estimate({'inputTokens':True,'outputTokens':1}))
  value=model.request('System',{})
  self.assertGreater(model.reservation(value,today=dt.date(2026,10,18)),0)
  with self.assertRaises(ValueError):model.reservation(value,today=dt.date(2027,1,1))
 def test_model_output_validation_does_not_repair_blocked_readiness(self):
  v=answer('review');v.update(ready=True,blockers=[dict(kind='missing-data',detail='Dataset absent',required_input='Original dataset bytes')])
  with self.assertRaises(ValueError):model.parse(response(v))
  v=answer();v['claims']=[dict(page=True,quote='Text',claim_type='numerical')]
  with self.assertRaises(ValueError):model.parse(response(v))
 def test_source_quotes_need_exact_page_and_original_text(self):
  study=str(uuid.uuid4());source=str(uuid.uuid4());chunk=dict(page=2,text='Observed latency is 0 ms.',offset=0)
  row=dict(page=2,quote='Observed latency is 0 ms.',claim_type='numerical')
  result=sources.candidates(study,source,chunk,[row]);self.assertEqual(result[0]['description'],row['quote'])
  with self.assertRaises(ValueError):sources.candidates(study,source,chunk,[dict(row,page=1)])
  with self.assertRaises(ValueError):sources.candidates(study,source,chunk,[dict(row,quote='Latency is proven zero.')])
 def test_source_retrieval_rejects_private_ipv6_metadata_and_credentials(self):
  for ip in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','fd00::1'):
   with patch.object(sources.socket,'getaddrinfo',return_value=[(2,1,6,'',(ip,443))]):
    with self.assertRaises(ValueError):sources.address('https://paper.example/main.pdf')
  for url in ('http://example.org/paper','https://example.org:8443/paper','https://user:password@example.org/paper','https://example.org/paper?token=secret'):
   with self.assertRaises(ValueError):sources.address(url)
 def test_failed_retrieval_retains_prefix_and_missing_full_paper_hash(self):
  error=TimeoutError('Synthetic retrieval timeout');error.captured_response=b'%PDF-partial';error.retrieval_attempts=[{'status':'failed'}]
  study=dict(id=str(uuid.uuid4()),title='Paper',paper_url='https://example.org/paper.pdf')
  with patch.object(sources,'fetch',side_effect=error),patch.object(store,'rows',return_value=[]),patch.object(store,'artifact',return_value={'id':str(uuid.uuid4())}) as artifact,patch.object(store,'append') as append:
   with self.assertRaises(TimeoutError):sources.original(study,str(uuid.uuid4()))
  self.assertEqual(artifact.call_args.args[2],b'%PDF-partial')
  source=next(call.args[3] for call in append.call_args_list if call.args[2]=='sources')
  self.assertEqual(source['review_status'],'unavailable-full-text');self.assertIsNone(source['sha256']);self.assertIsNone(source['byte_count'])
 def test_external_blocker_stops_only_the_selected_claim(self):
  t=task();v=answer();v['blockers']=[dict(kind='equipment',detail='Optical terminal absent',required_input='Authorized optical terminal measurements')]
  with patch.object(autonomy,'advance',return_value={}) as advance:
   autonomy.blocked(t,v);self.assertEqual(advance.call_args.args[:2],(t,'blocked'));self.assertEqual(advance.call_args.kwargs['context']['blockers'],v['blockers'])
 def test_budget_denial_makes_no_model_request(self):
  t=task();invoke=lambda _:self.fail('Provider must not run after denial')
  with patch.object(model,'load_prompt',return_value=({'id':str(uuid.uuid4())},'System')):
   self.assertEqual(model.call(t,{},lambda *a:dict(denied='budget'),invoke),dict(denied='budget'));self.assertEqual(t['attempts'],0)
 def test_model_cannot_invent_a_cloud_price(self):
  with self.assertRaisesRegex(ValueError,'cost envelope'):
   autonomy.price(dict(raw_input='{}'),dict(document={'execution':{}}))
 def test_attempt_limit_stops_before_more_prompts(self):
  t=task();t['attempts']=25
  with patch.object(model,'call') as call,patch.object(autonomy,'advance',return_value={}) as advance:
   autonomy.step(t);call.assert_not_called();self.assertEqual(advance.call_args.args[1],'attempt-limit')
 def test_reviews_omit_prior_verdicts_and_use_exact_candidate(self):
  t=task('critic');t['context']['proposal']={'protocol':{'hypotheses':'Exact original'}}
  claim=dict(id=t['claim_id'],source_id=str(uuid.uuid4()),description='Exact original')
  with patch.object(autonomy,'one',side_effect=[claim,{'id':claim['source_id']}]),patch.object(autonomy,'rows',return_value=[{'output':{'ready':True}}]):
   v=autonomy.build_context(t)
  self.assertNotIn('prior_attempts',v);self.assertNotIn('previous_feedback',v);self.assertEqual(v['candidate'],t['context']['proposal'])
 def test_anonymous_and_wrong_owner_cannot_read_prompt_history(self):
  study=str(uuid.uuid4());event=dict(rawPath='/research/studies/'+study+'/autonomy',requestContext={'http':{'method':'GET'}})
  with self.assertRaises(api.HttpError):api.dispatch(event)
  with patch.object(store,'owns',return_value=False):
   with self.assertRaises(api.HttpError):api.dispatch(event,str(uuid.uuid4()))
 def test_provider_unknown_turn_never_becomes_an_automatic_replay(self):
  t=task()
  with patch.object(model,'load_prompt',return_value=({'id':str(uuid.uuid4())},'System')):
   with self.assertRaisesRegex(ValueError,'never blindly'):model.call(t,{},lambda *a:dict(replayed=True),lambda _:self.fail('No provider replay'))
 def test_interpretation_disagreement_reuses_existing_evidence_without_new_experiments(self):
  for covered,label in ((True,'reproduced'),(False,'inconclusive')):
   t=task('assessment');t['context']={'job_ids':['original-job'],'campaign_ids':['original-campaign']}
   v=answer('assess');v.update(coverage_complete=covered,assessment_label=label)
   with patch.object(autonomy,'build_context',return_value={}),patch.object(model,'call',return_value=dict(id='turn',status='complete',output=v)),patch.object(autonomy,'numeric_finding',return_value=('discrepant',['original-evidence'],{})),patch.object(autonomy,'advance',return_value={}) as advance,patch.object(autonomy,'enqueue') as enqueue:
    autonomy.step(t)
   self.assertEqual(advance.call_args.kwargs['stage'],'assessment');self.assertEqual(advance.call_args.kwargs['context']['job_ids'],['original-job']);self.assertIn('no replacement experiment',advance.call_args.kwargs['context']['last_feedback']);enqueue.assert_not_called()
 def test_earlier_valid_discrepancy_cannot_be_hidden_by_later_agreement(self):
  t=task('assessment');t['context']=dict(job_ids=['new'],completed_job_ids=['old','partial'],reviews=[dict(turn_id=r,role=r,coverage_complete=True) for r in ('comprehension','critic')])
  records={('claims',t['claim_id']):dict(claim_type='numerical',metrics=['latency'],source_id='source')}
  for role in ('comprehension','critic'):records['autonomy_turns',role]=dict(task_id=t['id'],stage=role,status='complete',output={'ready':True})
  for job in ('old','new','partial'):
   records['runs',job]=dict(id=job,status='failed' if job=='partial' else 'complete');records['campaigns',job]=dict(protocol_id=job,adapter='matched-numeric-v1')
   records['protocols',job]=dict(id=job,document={'metrics':[dict(name='latency',planned_observations=1,units='ms',tolerance_absolute='0.1')]})
   records['published_values',job]=dict(id=job,status='recorded',source_id='source',value='10',observation_id=job,configuration_identity=job,metric='latency',units='ms')
  def rows(kind,study,**params):
   if kind=='queue_jobs':return [dict(id=j,run_id=j,campaign_id=j) for j in ('new','old','partial')]
   job=params['run_id'][3:];return [dict(records['published_values',job],published_id=job,value='12' if job=='old' else '10')]
  with patch.object(autonomy,'one',side_effect=lambda kind,study,key:records[kind,key]),patch.object(autonomy,'rows',side_effect=rows):
   label,evidence,coverage=autonomy.numeric_finding(t)
  self.assertEqual(label,'discrepant');self.assertIn(dict(kind='runs',id='old'),evidence);self.assertIn(dict(kind='runs',id='partial'),evidence);self.assertIn(dict(kind='published_values',id='new'),evidence);self.assertEqual(coverage,dict(decision_runs=2,prior_partial_or_failed_runs=1,prior_other_methods=0))
 def test_invalid_proposal_returns_specific_error_to_next_planning_prompt(self):
  t=task();v=answer('propose');v['proposal']={'protocol':{}}
  with patch.object(autonomy,'build_context',return_value={}),patch.object(model,'call',return_value=dict(id='turn',status='complete',output=v)),patch.object(autonomy,'validate_proposal',side_effect=ValueError('Original units must remain ms')),patch.object(autonomy,'advance',return_value={}) as advance:
   autonomy.step(t)
  self.assertEqual(advance.call_args.args[1],'queued');self.assertEqual(advance.call_args.kwargs['stage'],'planning');self.assertEqual(advance.call_args.kwargs['context']['last_feedback'],'Original units must remain ms')

if __name__=='__main__':unittest.main()
