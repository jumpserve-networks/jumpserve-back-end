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
 return dict(action=action,reason='evidence-incomplete',blockers=[],claims=[],check_id=None,proposal=None,ready=None,coverage_complete=False,assessment_label=None,evidence_ids=[])
def response(value):return dict(stopReason='tool_use',output=dict(message=dict(role='assistant',content=[dict(toolUse=dict(name='submit_assessment_step',input=value,toolUseId='test'))])),usage=dict(inputTokens=100,outputTokens=20))
def task(stage='planning'):
 return dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()),attempts=0,stage=stage,context={},workflow=dict(initial_prompt='Exact initial form',max_spend_cents=10000,committed_cents=500,deadline_at='2026-10-25T00:00:00Z'))

def retained_ipv6_contexts():
 raw=(Path(__file__).resolve().parents[1]/'plans/ipv6-archive-v1.json').read_bytes();plan=json.loads(raw)
 compiled=autonomy.preparation.compile_plan(plan,raw,dict(id='f776d397-a3d4-5f44-a558-e249002f0434',paper_url=plan['paper_urls'][0]))
 records={}
 for entry in compiled['records']:records.setdefault(entry['kind'],{})[entry['record']['id']]=entry['record']
 claim=next(c for c in records['claims'].values() if c['description']=='Published absolute folded metrics')
 available=[j for j in compiled['jobs'] if j['execution_mode']=='automatic'];t=task();t.update(study_id=compiled['study_id'],claim_id=claim['id']);t['context']={'prepared_id':compiled['id']};t['workflow']['initial_prompt']='Operational verification of retained archived numerical checks only, under original frozen conditions and complete recorded coverage. No new network measurements or broader validation.'
 with patch.object(autonomy,'one',side_effect=lambda kind,study,key:records[kind][key]),patch.object(autonomy,'rows',return_value=[]),patch.object(autonomy,'choices',return_value=(compiled,available)):
  planning=autonomy.build_context(t);t['stage']='comprehension';t['context']['selected_check_id']=available[0]['id'];review=autonomy.build_context(t)
 return planning,review,compiled,records

class Autonomy(unittest.TestCase):
 def test_lossless_evidence_packet_preserves_null_zero_details_and_all_original_fields(self):
  records=[dict(id='r'+str(i),run_id='run',study_id='study',created_at='fixed',configuration_identity='config'+str(i%2),observation_id='cell'+str(i),metric='latency',units='ms',value=v,status='missing' if v is None else 'recorded',reason=None,details=dict(metric='latency',units='ms',value=v,reference='0.00000000000000000001')) for i,v in enumerate(['0',None,'0.00000000000000000001','1000000000000000000000000001'])]
  original=workflow.canonical(records);packet=autonomy.evidence_packet(records)
  self.assertEqual(workflow.canonical(autonomy.evidence_records(packet)),original);self.assertEqual(workflow.canonical(records),original)
  self.assertEqual(packet['record_count'],4);self.assertEqual(packet['original_records_sha256'],workflow.digest(original));self.assertEqual(autonomy.evidence_records(packet)[0]['value'],'0');self.assertIsNone(autonomy.evidence_records(packet)[1]['value'])
 def test_lossless_transport_never_fills_absent_fields_or_invalid_dictionary_indices(self):
  with self.assertRaises(ValueError):autonomy.evidence_packet([{'value':None},{}])
  records=[{'id':str(i),'value':str(i%2),'label':'Recorded long repeated status '+str(i%2)} for i in range(50)];packet=autonomy.evidence_packet(records);self.assertTrue(packet['dictionaries'])
  column=next(iter(packet['dictionaries']));packet['rows'][0][packet['columns'].index(column)]=True
  with self.assertRaises(ValueError):autonomy.evidence_records(packet)
 def test_all_six_retained_checks_fit_the_bound_without_dropping_full_selected_conditions(self):
  planning,review,compiled,records=retained_ipv6_contexts();before=workflow.digest(workflow.canonical(compiled));choices=planning['registered_checks']
  self.assertEqual(len(choices),6);self.assertEqual({j['id'] for j in choices},{j['id'] for j in compiled['jobs'] if j['execution_mode']=='automatic'})
  for item in choices:
   original=records['protocols'][item['protocol_reference']['id']]['document'];self.assertEqual(item['protocol_reference']['document_sha256'],workflow.digest(workflow.canonical(original)))
   self.assertEqual([c['identity'] for c in item['protocol']['configurations']],[c['identity'] for c in original['configurations']])
   self.assertTrue(all(c['details_sha256']==workflow.digest(workflow.canonical(o['details'])) for c,o in zip(item['protocol']['configurations'],original['configurations'])))
  selected=review['candidate'];self.assertEqual(selected['protocol'],records['protocols'][selected['protocol_reference']['id']]['document']);self.assertEqual(review['registered_checks'],[])
  for value in (planning,review):self.assertLessEqual(len(workflow.canonical(model.request('Server policy '*1000,value))),96000)
  self.assertEqual(before,workflow.digest(workflow.canonical(compiled)))
 def test_each_phase_request_binds_actions_without_mutating_other_requests(self):
  planning=model.request('System',{'task':'planning'});review=model.request('System',{'task':'critic'})
  schema=lambda r:r['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']['properties']['action']['enum']
  self.assertEqual(schema(review),['review','blocked']);self.assertEqual(schema(planning),['choose-check','propose','blocked'])
  self.assertIn('discover',model.SCHEMA['properties']['action']['enum']);self.assertIn('Controller phase: critic',review['system'][-1]['text'])
 def test_confirmed_wrong_phase_answer_is_retained_invalid_and_can_receive_a_numbered_correction(self):
  t=task('comprehension');events=[];v=answer('choose-check');v.update(reason='retained-check',check_id='original-check')
  def control(t,op,payload):events.append((op,payload));return dict(replayed=False)
  with patch.object(model,'load_prompt',return_value=({'id':str(uuid.uuid4())},'System')):
   result=model.call(t,{'task':'comprehension'},control,lambda _:response(v))
  self.assertEqual(result['status'],'invalid-output');self.assertEqual(t['attempts'],1)
  receipt=events[-1][1];self.assertEqual(receipt['status'],'invalid-output');self.assertIsNone(receipt['output']);self.assertIn('choose-check',receipt['raw_response'])
  with patch.object(autonomy,'build_context',return_value={}),patch.object(model,'call',return_value=result),patch.object(autonomy,'advance',return_value={}) as advance:
   autonomy.step(t)
  self.assertEqual(advance.call_args.args[1],'queued');self.assertIn('requires one of review',advance.call_args.kwargs['context']['last_response_error'])
 def test_protocol_feedback_survives_fresh_review_without_prior_ai_verdicts(self):
  t=task('critic');t['context']['last_response_error']='Controller phase critic requires review; choose-check was not executed.';claim=dict(id=t['claim_id'],source_id=str(uuid.uuid4()))
  with patch.object(autonomy,'one',side_effect=[claim,{'id':claim['source_id']}]),patch.object(autonomy,'rows',return_value=[]):value=autonomy.build_context(t)
  self.assertEqual(value['response_contract_feedback'],t['context']['last_response_error']);self.assertNotIn('prior_attempts',value);self.assertNotIn('previous_feedback',value)
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
  v=answer('review');v.update(reason='ready-within-scope',ready=True,blockers=[dict(kind='missing-data',detail='missing-source-data',required_input='Original dataset bytes')])
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
 def test_only_the_exact_corrective_published_prompt_can_load(self):
  p=dict(id=str(uuid.uuid4()),module_id=model.MODULE,version='research-autonomy-v1',system_prompt='System',research_context='Context',content_sha256=workflow.digest(b'System\n\nContext'),published_at='2026-10-18T00:00:00Z')
  with patch.object(store,'request',return_value=[p]):
   with self.assertRaisesRegex(ValueError,'identity/publication/hash'):model.load_prompt()
   p['version']=model.PROMPT_VERSION;self.assertEqual(model.load_prompt()[1],'System\n\nContext')
   p['published_at']=None
   with self.assertRaises(ValueError):model.load_prompt()
 def test_partial_coverage_cannot_recommend_a_conclusive_label(self):
  v=answer('assess');v.update(assessment_label='discrepant',coverage_complete=False)
  with self.assertRaisesRegex(ValueError,'complete matched evidence'):model.parse(response(v))
 def test_typed_rationales_preserve_raw_input_and_reject_unbounded_assertions(self):
  v=answer('assess');v['assessment_label']='inconclusive';raw=response(v);parsed=model.parse(raw)
  self.assertEqual(raw['output']['message']['content'][0]['toolUse']['input']['reason'],'evidence-incomplete');self.assertEqual(parsed['reason'],model.REASONS['evidence-incomplete'])
  v['reason']='Adjacent samples are necessarily correlated.'
  with self.assertRaises(ValueError):model.parse(response(v))
 def test_resource_questions_cannot_request_waiving_fixed_limits(self):
  v=answer();v['reason']='resource-limit';v['blockers']=[dict(kind='capacity',detail='resource-exhausted',required_input='Reset the attempt counter')]
  with self.assertRaisesRegex(ValueError,'reset or waived'):model.parse(response(v))
  v['blockers'][0]['required_input']=model.LIMIT_INPUT;self.assertEqual(model.parse(response(v))['reason'],model.REASONS['resource-limit'])
 def test_model_cannot_recommend_a_conclusion_without_controller_evidence(self):
  t=task('assessment');v=answer('assess');v.update(reason='matched-numeric-check',assessment_label='reproduced',coverage_complete=True)
  def control(*args):return dict(replayed=False)
  with patch.object(model,'load_prompt',return_value=({'id':str(uuid.uuid4())},'System')):
   result=model.call(t,{},control,lambda _:response(v))
  self.assertEqual(result['status'],'invalid-output');self.assertIsNone(result['output']);self.assertIn('controller matched-evidence',result['reason'])
 def test_assessment_context_contains_only_the_recorded_controller_numeric_check(self):
  t=task('assessment');claim=dict(id=t['claim_id'],source_id=str(uuid.uuid4()),description='Exact original')
  finding=('discrepant',[dict(kind='runs',id='recorded-run')],dict(decision_runs=1))
  with patch.object(autonomy,'one',side_effect=[claim,{'id':claim['source_id']}]),patch.object(autonomy,'rows',return_value=[]),patch.object(autonomy,'numeric_finding',return_value=finding):v=autonomy.build_context(t)
  self.assertEqual(v['controller_numeric_check']['label'],'discrepant');self.assertEqual(v['controller_numeric_check']['evidence'],finding[1]);self.assertIn('shared inputs',v['controller_numeric_check']['scope'])
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
