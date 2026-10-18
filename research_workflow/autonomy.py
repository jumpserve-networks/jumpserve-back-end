"""Durable autonomous claim orchestration; progress and scientific findings differ."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal,InvalidOperation
import copy
import datetime as dt
import json
import os
import time
from pathlib import Path
import uuid
import autonomy_model as model
import autonomy_sources as sources
import network_spec
import preparation
import scheduler
import store
from workflow import canonical,digest,identifier,now,paper_details,protocol_record,validate_protocol

VERSION='research-autonomy-v1'
DEFAULTS=dict(maximum_spend_usd=100,elapsed_days=7,attempts_per_claim=25,parallel_claims_per_paper=2,global_parallel_claims=4)
REVIEWER=dict(identity='Autonomous evidence controller with fresh AI reviews',type='AI',independence='Shared model and inputs; primary implementation is not independent or human scientific review.')
EXTERNAL={'missing-data','equipment','authorization','implementation','provider'}

def uid(parent,key):return str(uuid.uuid5(uuid.UUID(parent),key))
def control(task,operation,payload):
 return store.rpc('research_autonomy_control',dict(p_operation=operation,p_actor=task.get('actor_id'),p_study=task.get('study_id'),p_payload=dict(payload,task_id=task['id'],token=task.get('lease_token'))))

def start(actor,body):
 if set(body)!={'request_id','paper','autonomous'} or body['autonomous'] is not True:raise ValueError('Submit one paper and its initial autonomous request; application limits cannot be overridden.')
 paper=paper_details(body['paper'])
 if paper.get('study_kind','paper')!='paper':raise ValueError('Autonomous paper verification requires a published paper identity.')
 paper['study_kind']='paper';study=identifier(body['request_id'])
 initial=canonical(dict(instruction='Assess this paper autonomously within application defaults. Preserve unresolved findings and explain external blockers; never force a desired result.',paper=paper,defaults=DEFAULTS)).decode()
 result=store.rpc('research_autonomy_control',dict(p_operation='start',p_actor=actor,p_study=study,p_payload=dict(paper=paper,initial_prompt=initial,intake_id=uid(study,'autonomous-intake'))))
 result['dispatch']=wake()
 return result

def wake():
 target=os.environ.get('RESEARCH_AUTONOMY_WORKER_FUNCTION','')
 import re
 if os.environ.get('RESEARCH_AUTONOMY_ENABLED')!='true' or not re.fullmatch(r'arn:aws:lambda:us-east-1:395567831870:function:[A-Za-z0-9_-]+',target):return dict(status='deferred',reason='Background controller is unavailable; the initial request is durably saved.')
 try:
  if scheduler.invoke_worker(target).get('StatusCode')==202:return dict(status='requested')
 except Exception:pass
 return dict(status='deferred',reason='Notification unconfirmed; the scheduled controller can resume the retained request.')

def capability():return dict(version=VERSION,defaults=DEFAULTS,enabled=os.environ.get('RESEARCH_AUTONOMY_ENABLED')=='true',prompt_module=model.MODULE,attempt_definition='One model invocation, including failures; polling does not consume an attempt.',limit_kind='Conservative aggregate commitments; unknown charges are retained, not zero.')

def rows(kind,study,**params):return store.rows(kind,dict(study_id='eq.'+study,**params))
def one(kind,study,value):
 found=store.rows(kind,dict(id='eq.'+value,limit=1)) if kind=='studies' else rows(kind,study,id='eq.'+value,limit=1)
 if len(found)!=1:raise ValueError('Required retained '+kind+' identity is unavailable.')
 return found[0]

def overview(studies):
 ids=[s['id'] for s in studies]
 workflows=store.rows('autonomy_workflows',{'study_id':'in.('+','.join(ids)+')','limit':101}) if ids else []
 tasks=store.rows('autonomy_tasks',{'study_id':'in.('+','.join(ids)+')','select':'id,study_id,claim_id,stage,status,label,attempts,reason,required_inputs,next_at,created_at,updated_at','limit':20001}) if workflows else []
 if len(workflows)>100 or len(tasks)>20000:raise store.StoreError('Autonomous coverage exceeds the interactive bound; use paginated study records.')
 # Full prompts are fetched only for the selected task, never loaded for every poll.
 claim_ids=[t['claim_id'] for t in tasks if t['claim_id']]
 claims=store.rows('claims',{'id':'in.('+','.join(claim_ids)+')','study_id':'in.('+','.join(ids)+')','select':'id,description','limit':20001}) if claim_ids else []
 names={c['id']:c['description'] for c in claims}
 if set(claim_ids)!=set(names):raise store.StoreError('Autonomous claim identity coverage is incomplete.')
 for task in tasks:
  task.pop('context',None);task['claim_title']=names.get(task['claim_id'])
 return dict(version=VERSION,defaults=DEFAULTS,enabled=os.environ.get('RESEARCH_AUTONOMY_ENABLED')=='true',workflows=workflows,tasks=tasks,coverage=dict(workflows=len(workflows),tasks=len(tasks)))

def history(study,task_id,before=None):
 task=one('autonomy_tasks',study,identifier(task_id))
 params=dict(task_id='eq.'+task['id'],order='number.desc',limit=10)
 if before is not None:
  if not str(before).isdigit() or not 1<=int(before)<=26:raise ValueError('Turn cursor must be an attempt number.')
  params['number']='lt.'+str(int(before))
 turns=rows('autonomy_turns',study,**params)
 return dict(version=VERSION,study_id=study,task_id=task['id'],turns=turns,next_before=min((r['number'] for r in turns),default=None) if len(turns)==10 else None,coverage=dict(returned=len(turns),total=task['attempts']),access='owner-private')

def advance(task,status,stage=None,context=None,reason=None,label=None,delay=0):
 usage=dict(controller_wall_seconds=max(0,time.monotonic()-task['_tick']) if '_tick' in task else None,hosting_configured_memory_mb=os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE'),measured_charges_usd=None,estimated_charges_usd=None)
 return control(task,'advance',dict(status=status,stage=stage or task['stage'],context=context if context is not None else task['context'],reason=reason or status,label=label,delay_seconds=delay,usage=usage))

def seed(task,claims,context):
 return control(task,'seed',dict(claims=[dict(id=uid(task['study_id'],'autonomous-claim:'+c['id']),claim_id=c['id'],context=copy.deepcopy(context)) for c in claims]))

def choices(study,claim_id,prepared_id):
 prepared=one('prepared_plans',study,prepared_id);value=preparation.compiled(study,prepared)
 allowed={r['record']['campaign_id'] for r in value['records'] if r['kind']=='claim_checks' and r['record']['claim_id']==claim_id and r['record']['applicability']=='applicable'}
 return value,[j for j in value['jobs'] if j['campaign_id'] in allowed]

def intake(task):
 study=one('studies',task['study_id'],task['study_id']);context=copy.deepcopy(task['context'])
 if not context:
  plan,raw=preparation.registered(study)
  if plan:
   prepared=preparation.prepare(study,task['actor_id'],dict(action='prepare',request_id=uid(task['id'],'prepare')))
   claims=rows('claims',study['id'],limit=201)
   if len(claims)>200:raise ValueError('Claim inventory exceeds 200; source coverage is incomplete.')
   seed(task,claims,dict(prepared_id=prepared['prepared']['id']))
   return advance(task,'finished',reason='Registered source-grounded claim inventory retained; per-claim attempts continue independently.',context=dict(prepared_id=prepared['prepared']['id'],inventory_origin='registered-source-grounded-plan',coverage='Original plan scope only; not a fresh full-paper review.'))
  context=sources.original(study,task['actor_id'])
  return advance(task,'queued',context=context,reason='Original paper and derived text retained; candidate extraction is pending.')
 packet=json.loads(store.artifact_bytes(study['id'],context['text_artifact_id'],maximum=10_000_000))
 chunks=sources.chunks(packet);index=context['chunk_index']
 if index>=len(chunks):
  count=len(rows('claims',study['id'],limit=201))
  return advance(task,'finished' if count else 'blocked',context=dict(context,text_chunks_examined=index,text_chunks_total=len(chunks),missing_text_pages=[p['page'] for p in packet['pages'] if not p['text'].strip()],coverage='Extracted text only; figures, tables, equations, scans and supplements remain unreviewed.'),reason='Extracted-text candidate inventory finished; visual and supplement coverage remains incomplete.' if count else 'No source-grounded candidate claims found; claim coverage is unknown, not complete.')
 prompt_context=dict(task='discover',initial_prompt=task['workflow']['initial_prompt'],source=one('sources',study['id'],context['source_id']),source_chunk=chunks[index],chunk_index=index,total_chunks=len(chunks),prior_attempt=task['attempts'],previous_feedback=context.get('last_failure'),instructions='Return exact candidate claim quotes from this page chunk. Omitted visual and supplement evidence remains uncovered.')
 answer=model.call(task,prompt_context,control)
 if answer.get('denied'):return advance(task,answer['denied'],reason='Autonomous '+answer['denied']+' limit reached.',context=dict(context,text_chunks_examined=index,text_chunks_total=len(chunks)))
 if answer['status']!='complete':return advance(task,'queued',context=dict(context,last_failure=answer['reason']),reason='Invalid extraction response retained; next prompt includes the failure.')
 output=answer['output']
 if output['action']=='blocked':return blocked(task,output)
 try:
  if output['action']!='discover':raise ValueError('Extraction must return candidate quotes, not a model-selected action.')
  claims=sources.candidates(study['id'],context['source_id'],chunks[index],output['claims'])
 except ValueError as error:
  return advance(task,'queued',context=dict(context,last_failure=str(error),last_turn_id=answer['id']),reason='Source grounding failed; the next prompt includes the exact rejected quote/page error.')
 existing={c['id'] for c in rows('claims',study['id'],limit=201)}
 if len(existing|{c['id'] for c in claims})>200:
  return advance(task,'blocked',context=dict(context,text_chunks_examined=index,text_chunks_total=len(chunks),unadmitted_candidates=len({c['id'] for c in claims}-existing),last_turn_id=answer['id']),reason='The bounded inventory reached 200 claims. Additional grounded candidates are retained in the last response; remaining paper coverage is incomplete and requires a separately scoped campaign.')
 if claims:
  store.append_bundle(study['id'],task['actor_id'],[dict(kind='claims',record=c) for c in claims]);seed(task,claims,dict(source_id=context['source_id'],text_artifact_id=context['text_artifact_id']))
 context.pop('last_failure',None)
 context.update(chunk_index=index+1,last_turn_id=answer['id'])
 return advance(task,'queued',context=context,reason='Grounded candidates recorded; next extracted-text chunk will be examined.')

def blocked(task,output):
 context=dict(task['context'],blockers=output['blockers'],last_feedback=output['reason'])
 return advance(task,'blocked',context=context,reason=output['reason'])

def build_context(task):
 claim=one('claims',task['study_id'],task['claim_id']);source=one('sources',task['study_id'],claim['source_id']);context=task['context']
 value=dict(task=task['stage'],initial_prompt=task['workflow']['initial_prompt'],claim=claim,source=source,attempt_number=task['attempts']+1,limits=DEFAULTS,
  source_coverage='Recorded source metadata and supplied text only; retrieval is not complete review.',registered_checks=[],registered_runners=list(network_spec.RUNNERS),previous_feedback=context.get('last_feedback'),remaining_commitment_cents=task['workflow']['max_spend_cents']-task['workflow']['committed_cents'],deadline_at=task['workflow']['deadline_at'],
  prior_attempts=[{k:r.get(k) for k in ('number','stage','status','output','reason','request_sha256','response_sha256')} for r in rows('autonomy_turns',task['study_id'],task_id='eq.'+task['id'],order='number.desc',limit=3)])
 if context.get('prepared_id'):
  _,available=choices(task['study_id'],claim['id'],context['prepared_id'])
  value['registered_checks']=[dict(id=j['id'],title=j['title'],execution_mode=j['execution_mode'],campaign=one('campaigns',task['study_id'],j['campaign_id']),protocol=one('protocols',task['study_id'],one('campaigns',task['study_id'],j['campaign_id'])['protocol_id'])['document']) for j in available if j['id'] not in context.get('tried_check_ids',[])]
 if context.get('proposal'):value['candidate']=context['proposal']
 if context.get('selected_check_id'):
  _,available=choices(task['study_id'],claim['id'],context['prepared_id']);job=next(j for j in available if j['id']==context['selected_check_id'])
  value['candidate']=dict(registered_job_id=job['id'],campaign=one('campaigns',task['study_id'],job['campaign_id']),protocol=one('protocols',task['study_id'],one('campaigns',task['study_id'],job['campaign_id'])['protocol_id'])['document'])
 if task['stage'] in ('comprehension','critic'):
  # Fresh reviews receive the candidate/source only, no earlier AI verdict/history.
  value.pop('prior_attempts');value.pop('previous_feedback');value.pop('attempt_number')
  value['review_role']=task['stage']
 job_ids=list(dict.fromkeys(context.get('job_ids',[])+context.get('completed_job_ids',[])))
 if job_ids:
  run_ids=[r['run_id'] for r in rows('queue_jobs',task['study_id'],id='in.('+','.join(job_ids)+')',limit=1000) if r.get('run_id')]
  value['runs']=rows('runs',task['study_id'],id='in.('+','.join(run_ids)+')',limit=1000) if run_ids else []
  value['measurements']=rows('measurements',task['study_id'],run_id='in.('+','.join(run_ids)+')',limit=1001) if run_ids else []
  if len(value['measurements'])>1000:raise ValueError('Evidence context exceeds the interactive measurement bound; retain unresolved status without truncation.')
 return value

def validate_proposal(task,proposal):
 if not isinstance(proposal,dict) or set(proposal)!={'protocol','experiment_type'}:raise ValueError('Networking proposals need protocol and experiment_type only; recorded values cannot be model supplied.')
 claim=one('claims',task['study_id'],task['claim_id']);p=proposal['protocol']
 if p.get('hypotheses')!=claim['description']:raise ValueError('The original claim cannot be changed or weakened.')
 if p.get('stage')!='pilot' or p.get('data_origin') not in ('simulation','new-empirical'):raise ValueError('New model proposals are scoped prospective pilots with explicit data origin.')
 if proposal['experiment_type'] not in ('new-measurement','independent-check','broader-validation'):raise ValueError('Choose an applicable networking method.')
 p=network_spec.bind_inputs(p)
 validate_protocol(p)
 if not network_spec.specs(p):raise ValueError('A registered executable networking specification is required.')
 if any(m.get('planned_observations') is None for m in p['metrics']):raise ValueError('Every metric needs explicit planned coverage.')
 source=one('sources',task['study_id'],claim['source_id'])
 if source.get('sha256') not in [i['sha256'] for i in p['input_versions']]:raise ValueError('The exact retained paper source hash is required.')
 known={r['sha256'] for r in rows('artifacts',task['study_id'],limit=1000)}|{source['sha256']}|{digest(canonical(s)) for s in network_spec.specs(p)}
 if any(v['sha256'] not in known for v in p['input_versions']):raise ValueError('A claimed input has no retained source/artifact; no invented implementation or dataset hash is allowed.')
 return dict(proposal,protocol=p)

def price(job,protocol):
 if protocol['document'].get('execution') is None:
  return dict(cents=1,quote=dict(version='bounded-arithmetic-overhead-v1',kind='conservative-reservation',compute='Existing 10-second/512-MB bounded arithmetic; paper overhead separately reserved.',measured_charges_usd=None))
 # Cost envelopes are operator-owned configuration, never model/user output.
 path=Path(__file__).parent/'policies/autonomy-experiment-costs-v1.json'
 catalog=json.loads(path.read_bytes());spec_hash=digest(job['raw_input'].encode())
 selected=next((q for q in catalog['quotes'] if q['spec_sha256']==spec_hash),None)
 if not selected or selected.get('currency')!='USD' or selected.get('hard_resource_bounds_verified') is not True or dt.datetime.fromisoformat(selected['valid_until'].replace('Z','+00:00'))<=dt.datetime.now(dt.timezone.utc):
  raise ValueError('This configuration needs a current server-approved cost envelope with verified resource bounds before autonomous cloud execution.')
 cents=selected['maximum_cents']
 if isinstance(cents,bool) or not isinstance(cents,int) or not 1<=cents<=10000:raise ValueError('Invalid experiment price ceiling.')
 return dict(cents=cents,quote=selected)

def frozen_jobs(task):
 context=task['context'];study=task['study_id'];actor=task['actor_id']
 if context.get('selected_check_id'):
  value,available=choices(study,task['claim_id'],context['prepared_id']);selected=next(j for j in available if j['id']==context['selected_check_id'])
  by_id={j['id']:j for j in value['jobs']};order=[];seen=set()
  def visit(job):
   if job['id'] in seen:return
   seen.add(job['id'])
   for d in job['request']['dependencies']:visit(by_id[d['job_id']])
   order.append(job)
  visit(selected);return order
 proposal=context['proposal'];p=proposal['protocol'];fingerprint=digest(canonical(proposal));campaign_id=uid(task['id'],'campaign:'+fingerprint);protocol_id=uid(task['id'],'protocol:'+fingerprint)
 records=[dict(kind='protocols',record=protocol_record(p,protocol_id))]
 for c in p['configurations']:records.append(dict(kind='configurations',record=dict(id=uid(campaign_id,c['identity']),identity=c['identity'],details=c['details'],input_versions=p['input_versions'],requested_resources=p['resource_limits'])))
 records.extend([dict(kind='campaigns',record=dict(id=campaign_id,protocol_id=protocol_id,followup_of=None,title='Autonomous pilot: '+one('claims',study,task['claim_id'])['description'][:200],stage='pilot',experiment_type=proposal['experiment_type'],adapter=network_spec.VERSION,planned_units=len(network_spec.specs(p)),coverage_limits=p['coverage_limits'])),
  dict(kind='claim_checks',record=dict(id=uid(campaign_id,'claim-check'),claim_id=task['claim_id'],campaign_id=campaign_id,method=proposal['experiment_type'],applicability='applicable',rationale='Prospective pilot reviewed in separate AI calls; original claim and inference limits remain unchanged.'))])
 store.append_bundle(study,actor,records)
 jobs=[]
 for i,spec in enumerate(network_spec.specs(p)):
  job_id=uid(campaign_id,'job:'+str(i));jobs.append(dict(id=job_id,campaign_id=campaign_id,title='Reviewed autonomous networking pilot',execution_mode='automatic',request=dict(request_id=job_id,campaign_id=campaign_id,execution_mode='automatic',priority=3,dependencies=[],exclusive_resources=p['execution'].get('exclusive_resources',[]),raw_input=canonical(spec).decode(),rationale='Exact prospective reviewed specification; automated acquisition does not decide the wider paper claim.',followup_of=None)))
 return jobs

def enqueue(task):
 jobs=frozen_jobs(task)
 for job in jobs:
  for dependency in job['request']['dependencies']:
   if dependency['requirement']=='reviewed-evidence':
    previous=rows('queue_jobs',task['study_id'],id='eq.'+dependency['job_id'],limit=1)
    if not previous or previous[0]['status']!='reviewed':raise ValueError('A prerequisite requires substantive reviewed evidence that is not available; its review cannot be fabricated by autonomous queueing.')
 # Price the complete dependency closure before creating any experiment.
 priced=[]
 for job in jobs:
  if job['execution_mode']!='automatic':raise ValueError('This retained check requires an unavailable domain implementation or operator.')
  campaign=one('campaigns',task['study_id'],job['campaign_id']);protocol=one('protocols',task['study_id'],campaign['protocol_id'])
  priced.append((job,price(job['request'],protocol),protocol['document']['resource_limits']['wall_seconds']+(180 if protocol['document'].get('execution') is not None else 0)))
 for job,quote,required_seconds in priced:
  _,definition,_=scheduler.definition(job['request'])
  reservation=control(task,'reserve-job',dict(job_id=job['id'],definition_sha256=digest(canonical(definition)),reserved_cents=quote['cents'],required_seconds=required_seconds,quote=dict(quote['quote'],required_seconds=required_seconds)))
  if reservation.get('denied'):return advance(task,'budget',reason='Paper budget or elapsed limit prevents another experiment.')
  scheduler.enqueue(task['study_id'],task['actor_id'],job['request'])
 context=dict(task['context'],job_ids=[j['id'] for j in jobs],campaign_ids=list(dict.fromkeys(j['campaign_id'] for j in jobs)))
 return advance(task,'waiting',stage='execution',context=context,reason='Frozen experiments queued; poll original identities without restarting them.',delay=30)

def numeric_finding(task):
 """Independent Decimal check of recorded matched cells; never trust an AI label."""
 claim=one('claims',task['study_id'],task['claim_id']);context=task['context']
 if claim['claim_type']!='numerical' or not claim['metrics'] or len(context.get('reviews',[]))!=2 or {r['role'] for r in context['reviews']}!={'comprehension','critic'} or not all(r['coverage_complete'] for r in context['reviews']):return None
 for review in context['reviews']:
  recorded=one('autonomy_turns',task['study_id'],review['turn_id'])
  if recorded['task_id']!=task['id'] or recorded['status']!='complete' or recorded['stage']!=review['role'] or recorded['output'].get('ready') is not True:return None
 labels=[];evidence=[];coverage=dict(decision_runs=0,prior_partial_or_failed_runs=0,prior_other_methods=0)
 current=set(context['job_ids']);all_jobs=list(dict.fromkeys(context['job_ids']+context.get('completed_job_ids',[])))
 jobs=rows('queue_jobs',task['study_id'],id='in.('+','.join(all_jobs)+')',limit=1000)
 if {j['id'] for j in jobs}!=set(all_jobs):return None
 for job in jobs:
  if not job.get('run_id'):return None
  run=one('runs',task['study_id'],job['run_id']);campaign=one('campaigns',task['study_id'],job['campaign_id']);protocol=one('protocols',task['study_id'],campaign['protocol_id'])
  evidence.extend([dict(kind='runs',id=run['id']),dict(kind='protocols',id=protocol['id'])])
  if run['status']!='complete' or campaign['adapter']!='matched-numeric-v1':
   if job['id'] in current:return None
   coverage['prior_partial_or_failed_runs' if run['status']!='complete' else 'prior_other_methods']+=1
   continue
  coverage['decision_runs']+=1
  metrics={m['name']:m for m in protocol['document']['metrics']};measurements=rows('measurements',task['study_id'],run_id='eq.'+run['id'],limit=1001)
  if not measurements or len(measurements)>1000:return None
  claimed={m.get('name') if isinstance(m,dict) else m for m in claim['metrics']}
  if claimed!=set(metrics):return None
  for name,m in metrics.items():
   if m.get('planned_observations') is None or sum(r['metric']==name for r in measurements)!=m['planned_observations']:return None
  for r in measurements:
   if r['status']!='recorded' or not r.get('published_id'):return None
   p=one('published_values',task['study_id'],r['published_id']);m=metrics[r['metric']]
   if p['status']!='recorded' or p['source_id']!=claim['source_id'] or any(r[k]!=p[k] for k in ('observation_id','configuration_identity','metric','units')) or r['units']!=m['units']:return None
   try:
    observed=Decimal(str(r['value']));published=Decimal(str(p['value']));tolerance=Decimal(str(m['tolerance_absolute']))
    if not all(v.is_finite() for v in (observed,published,tolerance)) or tolerance<0:return None
   except (InvalidOperation,KeyError):return None
   labels.append('reproduced' if abs(observed-published)<=tolerance else 'discrepant');evidence.extend([dict(kind='measurements',id=r['id']),dict(kind='published_values',id=p['id'])])
 unique={(r['kind'],r['id']):r for r in evidence}
 return ('discrepant' if 'discrepant' in labels else 'reproduced',list(unique.values()),coverage) if labels else None

def step(task):
 if task['stage']=='intake':return intake(task)
 context=copy.deepcopy(task['context'])
 if task['stage']=='execution':
  jobs=rows('queue_jobs',task['study_id'],id='in.('+','.join(context['job_ids'])+')',limit=1000)
  if len(jobs)!=len(context['job_ids']):raise ValueError('Queue identity coverage is incomplete; do not create replacement jobs.')
  if any(j['status'] in ('queued','running','manual') for j in jobs):return advance(task,'waiting',reason='Original experiment identities are still pending; no replacement invocation.',delay=30)
  return advance(task,'queued',stage='assessment',reason='Recorded experiment outcomes are ready for evidence assessment.',label='inconclusive')
 if task['attempts']>=25:return advance(task,'attempt-limit',reason='25 attempts reached; unresolved evidence retained.')
 answer=model.call(task,build_context(task),control)
 if answer.get('denied'):return advance(task,answer['denied'],reason='Paper admission stopped by '+answer['denied']+'.')
 if answer['status']!='complete':return advance(task,'queued',context=dict(context,last_feedback=answer['reason']),reason='Invalid answer retained; next prompt addresses the exact failure.')
 output=answer['output'];context['last_turn_id']=answer['id']
 if output['action']=='blocked' or any(b['kind'] in EXTERNAL for b in output['blockers']):return blocked(dict(task,context=context),output)
 if task['stage']=='planning':
  if output['action']=='choose-check':
   if not context.get('prepared_id'):raise ValueError('No retained checks exist for this claim.')
   _,available=choices(task['study_id'],task['claim_id'],context['prepared_id'])
   if output['check_id'] not in [j['id'] for j in available] or output['check_id'] in context.get('tried_check_ids',[]):raise ValueError('Choose an unused retained check for this exact claim.')
   context['selected_check_id']=output['check_id'];context.pop('proposal',None)
  elif output['action']=='propose':
   try:context['proposal']=validate_proposal(task,output['proposal'])
   except (ValueError,KeyError,TypeError) as error:
    return advance(task,'queued',stage='planning',context=dict(context,last_feedback=str(error),last_turn_id=answer['id']),reason='Candidate failed semantic validation; the next prompt must address that specific error without changing the claim.')
   context.pop('selected_check_id',None)
  else:raise ValueError('Planning requires a source-bound check, explicit prospective pilot or blocker.')
  if context.get('proposal'):
   candidate=context['proposal'];p=candidate['protocol']
   semantic=dict(experiment_type=candidate['experiment_type'],**{k:p.get(k) for k in ('hypotheses','configurations','metrics','execution','input_versions','replication_unit','statistics','dependence','missing_data','exclusions')})
  else:semantic=dict(check_id=context.get('selected_check_id'))
  fingerprint=digest(canonical(semantic))
  if fingerprint in context.get('prior_design_hashes',[]):return advance(task,'blocked',context=dict(context,last_feedback=output['reason']),reason='The same design was proposed again without addressing the retained evidence gap. A new input or method is required.')
  context['prior_design_hashes']=context.get('prior_design_hashes',[])+[fingerprint];context['reviews']=[]
  context.pop('blockers',None)
  return advance(task,'queued',stage='comprehension',context=context,reason='Candidate retained; fresh comprehension and critic reviews are pending.')
 if task['stage'] in ('comprehension','critic'):
  if output['action']!='review':raise ValueError('Fresh review must return a review judgment only.')
  if output['ready'] is not True:
   context.update(last_feedback=output['reason'],blockers=output['blockers'])
   return advance(task,'queued',stage='planning',context=context,reason='Review finding retained; next planning prompt includes the specific design gap.')
  context['reviews']=context.get('reviews',[])+[dict(turn_id=answer['id'],role=task['stage'],coverage_complete=output['coverage_complete'],reason=output['reason'])]
  if task['stage']=='comprehension':return advance(task,'queued',stage='critic',context=context,reason='Comprehension recorded; fresh critic review pending.')
  task['context']=context;return enqueue(task)
 if task['stage']=='assessment':
  if output['action']!='assess':raise ValueError('Assessment requires an evidence judgment, not another action.')
  finding=numeric_finding(task)
  if finding and (not output['coverage_complete'] or output['assessment_label']!=finding[0]):
   return advance(task,'queued',stage='assessment',context=dict(context,last_feedback=output['reason']+' The independent matched-cell calculation is '+finding[0]+'. Reassess coverage and the retained evidence under the original criterion; no replacement experiment or favorable-result search is authorized.'),reason='Model interpretation disagrees with the recorded numerical check; retry assessment using the same evidence.',label='inconclusive')
  if finding and output['coverage_complete'] and output['assessment_label']==finding[0]:
   label,evidence,coverage=finding
   prior=rows('assessments',task['study_id'],claim_id='eq.'+task['claim_id'],order='created_at.desc',limit=1)
   assessment=dict(id=uid(answer['id'],'assessment'),claim_id=task['claim_id'],campaign_id=context['campaign_ids'][-1],supersedes_id=prior[0]['id'] if prior else None,label=label,
    tested_conditions=dict(scope='Matched recorded numerical cells under the exact frozen criterion; no broader inference. Earlier valid comparable discrepancies remain part of the decision; prior partial/failed and other-method runs remain referenced separately.',protocols=[one('campaigns',task['study_id'],c)['protocol_id'] for c in context['campaign_ids']],coverage=coverage,automatic_controller=VERSION),evidence=evidence,
    justification=output['reason'],limitations='Separate AI reviews share model and inputs. Numerical agreement is not correctness, causality, compliance or generalization. Source review remains as recorded.',reviewer=REVIEWER)
   store.append(task['study_id'],task['actor_id'],'assessments',assessment)
   return advance(task,'resolved',context=dict(context,assessment_id=assessment['id']),reason=output['reason'],label=label)
  context.update(last_feedback=output['reason']+' The recorded evidence does not meet the controller\'s frozen full-coverage criterion; no conclusive label was assigned.',blockers=output['blockers'])
  context['completed_job_ids']=list(dict.fromkeys(context.get('completed_job_ids',[])+context.get('job_ids',[])))
  if context.get('selected_check_id'):context['tried_check_ids']=context.get('tried_check_ids',[])+[context['selected_check_id']]
  context.pop('job_ids',None);context.pop('campaign_ids',None)
  return advance(task,'queued',stage='planning',context=context,reason='Evidence remains inconclusive; next prompt targets the remaining gap.',label='inconclusive')
 raise ValueError('Unsupported durable claim stage.')

def process(task):
 task['_tick']=time.monotonic()
 try:return step(task)
 except Exception as error:
  # The stage/prompt/raw answer and original artifacts remain retained. No lost
  # launch or failed provider call is converted into a replacement invocation.
  reason=str(error) if isinstance(error,ValueError) else 'Autonomous operation unconfirmed ('+type(error).__name__+'); reconcile retained identities before continuing.'
  try:return advance(task,'blocked',reason=reason)
  except Exception:return dict(status='unconfirmed',task_id=task['id'],reason='Lease/evidence persistence must be reconciled; no retry was dispatched.')

def handler(event,context=None):
 def slot():
  task=store.rpc('research_autonomy_control',dict(p_operation='lease',p_actor=None,p_study=None,p_payload={}))
  return process(task) if task else dict(status='idle')
 with ThreadPoolExecutor(max_workers=2) as pool:
  futures=[pool.submit(slot) for _ in range(2)]
  results=[]
  for future in futures:
   try:r=future.result();results.append({k:r.get(k) for k in ('id','status','reason')})
   except Exception as error:results.append(dict(status='unconfirmed',error_type=type(error).__name__))
 return dict(version=VERSION,slots=2,outcomes=results)
