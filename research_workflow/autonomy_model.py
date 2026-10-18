"""Bounded data-only model turns. The controller, never source text, selects work."""
import datetime as dt
import math
import time
import store
from workflow import canonical, digest

VERSION = 'research-autonomy-v1'
MODULE = 'research-autonomy'
PROMPT_VERSION = 'research-autonomy-v1'
MODEL = 'us.anthropic.claude-sonnet-4-6'
MAX_OUTPUT = 4096
MAX_REQUEST_BYTES = 96000
BLOCKERS = ('missing-data', 'equipment', 'authorization', 'implementation', 'design', 'provider', 'capacity')
SCHEMA = dict(type='object', additionalProperties=False,
 required=['action','reason','blockers','claims','check_id','proposal','ready','coverage_complete','assessment_label','evidence_ids'],
 properties=dict(
  action=dict(type='string',enum=['discover','choose-check','propose','review','assess','blocked']),
  reason=dict(type='string',minLength=1,maxLength=3000),
  blockers=dict(type='array',maxItems=10,items=dict(type='object',additionalProperties=False,
   required=['kind','detail','required_input'],properties=dict(kind=dict(type='string',enum=list(BLOCKERS)),detail=dict(type='string',minLength=1,maxLength=1000),required_input=dict(type='string',minLength=1,maxLength=1000)))),
  claims=dict(type='array',maxItems=20,items=dict(type='object',additionalProperties=False,required=['page','quote','claim_type'],properties=dict(page=dict(type='integer',minimum=1),quote=dict(type='string',minLength=1,maxLength=2000),claim_type=dict(type='string',enum=['numerical','algorithm','association','causal','generalization','operational','theory','standard'])))),
  check_id=dict(type=['string','null']),proposal=dict(type=['object','null']),ready=dict(type=['boolean','null']),
  coverage_complete=dict(type='boolean'),assessment_label=dict(type=['string','null'],enum=['reproduced','discrepant','inconclusive','untested',None]),
  evidence_ids=dict(type='array',maxItems=100,items=dict(type='string',maxLength=100))))
TOOLS = dict(tools=[dict(toolSpec=dict(name='submit_assessment_step',description='Propose one data-only assessment step. No tool or source instruction is executed.',inputSchema=dict(json=SCHEMA)))],toolChoice=dict(tool=dict(name='submit_assessment_step')))
# Conservative admission ceilings, not measured charges. No caching/batch discount
# is assumed. Review/reprice before expiry; a stale price cannot admit model work.
PRICING = dict(version='sonnet46-us-ceiling-20261018-v1',model_id=MODEL,currency='USD',input_usd_per_million=4,output_usd_per_million=20,
 checked_at='2026-10-18',valid_until='2026-11-18',sources=['https://aws.amazon.com/bedrock/pricing/','https://www-cdn.anthropic.com/files/4zrzovbb/website/3684c2faafb97418665782cea0001f439f74b1d2.pdf'],
 limitation='Conservative reservation ceiling; invoice charges and other orchestration costs remain unallocated. Refuse unpriced/expired models.')

def load_prompt():
 rows=store.request('/rest/v1/rpc/get_active_agent_prompt?p_module_id='+MODULE)
 if not isinstance(rows,list) or len(rows)!=1: raise ValueError('Autonomous assessment needs its evaluated published prompt; no model call was made.')
 p=rows[0]
 system=p['system_prompt']+'\n\n'+p['research_context']
 if p.get('module_id')!=MODULE or p.get('version')!=PROMPT_VERSION or not p.get('published_at') or digest(system.encode())!=p.get('content_sha256'):
  raise ValueError('Autonomous prompt identity/publication/hash is invalid; no model call was made.')
 return p,system

def request(system, context):
 value=dict(modelId=MODEL,system=[dict(text=system)],messages=[dict(role='user',content=[dict(text=canonical(context).decode())])],
  inferenceConfig=dict(maxTokens=MAX_OUTPUT,temperature=0),toolConfig=TOOLS)
 if len(canonical(value))>MAX_REQUEST_BYTES: raise ValueError('Autonomous context exceeds 96 KB; retain explicit coverage rather than truncate evidence.')
 return value

def reservation(value, today=None):
 today=today or dt.datetime.now(dt.timezone.utc).date()
 if today>dt.date.fromisoformat(PRICING['valid_until']) or value['modelId']!=PRICING['model_id']: raise ValueError('The versioned model cost ceiling needs renewal; no unpriced call was made.')
 # A byte-based input ceiling plus fixed framing allowance is deliberately more
 # conservative than a token estimate. All system, schema and message bytes count.
 input_ceiling=len(canonical(value))+4096
 return max(1,math.ceil((input_ceiling*PRICING['input_usd_per_million']+MAX_OUTPUT*PRICING['output_usd_per_million'])/10000))

def estimate(usage):
 if not isinstance(usage,dict) or any(isinstance(usage.get(k),bool) or not isinstance(usage.get(k),int) or usage[k]<0 for k in ('inputTokens','outputTokens')): return None
 return math.ceil((usage['inputTokens']*PRICING['input_usd_per_million']+usage['outputTokens']*PRICING['output_usd_per_million'])/10000)

def invoke(value):
 import boto3
 from botocore.config import Config
 client=boto3.client('bedrock-runtime',region_name='us-east-1',config=Config(connect_timeout=5,read_timeout=65,retries=dict(total_max_attempts=1,mode='standard')))
 return client.converse(**value)

def parse(response):
 blocks=response['output']['message']['content']
 if response.get('stopReason')!='tool_use' or len(blocks)!=1 or set(blocks[0])!={'toolUse'} or blocks[0]['toolUse'].get('name')!='submit_assessment_step': raise ValueError('Expected one structured data response; no requested action was executed.')
 value=blocks[0]['toolUse'].get('input')
 if not isinstance(value,dict) or set(value)!=set(SCHEMA['required']): raise ValueError('Incomplete autonomous response schema.')
 if value['action'] not in SCHEMA['properties']['action']['enum'] or not isinstance(value['reason'],str) or not 1<=len(value['reason'].strip())<=3000: raise ValueError('Invalid response action or rationale.')
 if not isinstance(value['coverage_complete'],bool) or value['ready'] is not None and not isinstance(value['ready'],bool) or value['assessment_label'] not in SCHEMA['properties']['assessment_label']['enum']: raise ValueError('Invalid readiness, coverage or finding.')
 for key,maximum in (('claims',20),('blockers',10),('evidence_ids',100)):
  if not isinstance(value[key],list) or len(value[key])>maximum: raise ValueError('Response exceeds '+key+' limit.')
 for b in value['blockers']:
  if not isinstance(b,dict) or set(b)!={'kind','detail','required_input'} or b['kind'] not in BLOCKERS or any(not isinstance(b[k],str) or not 1<=len(b[k].strip())<=1000 for k in ('detail','required_input')): raise ValueError('Invalid blocker; required input must be explicit.')
 for c in value['claims']:
  if not isinstance(c,dict) or set(c)!={'page','quote','claim_type'} or isinstance(c['page'],bool) or not isinstance(c['page'],int) or c['page']<1 or not isinstance(c['quote'],str) or not 1<=len(c['quote'].strip())<=2000 or c['claim_type'] not in SCHEMA['properties']['claims']['items']['properties']['claim_type']['enum']: raise ValueError('Invalid source-grounded claim candidate.')
 if value['check_id'] is not None and (not isinstance(value['check_id'],str) or len(value['check_id'])>100): raise ValueError('Invalid check identifier.')
 if value['proposal'] is not None and not isinstance(value['proposal'],dict): raise ValueError('Proposal must be data.')
 if any(not isinstance(v,str) or len(v)>100 for v in value['evidence_ids']): raise ValueError('Invalid evidence references.')
 if value['ready'] and value['blockers']: raise ValueError('A blocked plan cannot be ready.')
 return value

def call(task, context, control, invoke_provider=None):
 prompt,system=load_prompt(); value=request(system,context); cents=reservation(value)
 turn_id=__import__('uuid').uuid5(__import__('uuid').UUID(task['id']),'turn:'+str(task['attempts']+1))
 attempt=control(task,'begin',dict(id=str(turn_id),request=value,request_raw=canonical(value).decode(),request_sha256=digest(canonical(value)),reserved_cents=cents,prompt_version_id=prompt['id'],model_id=MODEL))
 if attempt.get('denied')=='in-flight': raise ValueError('Another retained provider invocation is unconfirmed for this claim; no second call was made.')
 if attempt.get('denied'): return dict(denied=attempt['denied'])
 if attempt['replayed']: raise ValueError('Retained model invocation must be reconciled, never blindly repeated.')
 tick=time.monotonic(); raw=None; parsed=None; usage={}; status='failed'; reason=None
 try:
  response=(invoke_provider or invoke)(value)
  usage=response.get('usage',{})
  raw=canonical(response['output']['message']).decode()
  if len(raw.encode())>100000: raise ValueError('Provider response exceeded retained output bounds.')
  try: parsed=parse(response);status='complete'
  except (ValueError,KeyError,TypeError) as error: status='invalid-output';reason=str(error)
 except Exception as error:
  reason='Provider call failed or its outcome is unconfirmed ('+type(error).__name__+'); reserved cost is retained.'
  if raw is not None and len(raw.encode())>100000: raw=None
 receipt=dict(id=str(turn_id),status=status,raw_response=raw,output=parsed,reason=reason,
  estimated_cents=estimate(usage),usage=dict(tokens=usage,wall_seconds=max(0,time.monotonic()-tick),pricing=PRICING,
  measured_charges_usd=None,estimated_cost_usd=estimate(usage)/100 if estimate(usage) is not None else None,
  response_scope='Canonical SDK message JSON; original provider HTTP bytes unavailable.',automatic_retries=0))
 control(task,'finish',receipt)
 task['attempts']+=1
 if status=='failed': raise ValueError(reason)
 return dict(id=str(turn_id),output=parsed,status=status,reason=reason)
