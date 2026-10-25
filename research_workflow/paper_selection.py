"""Bounded review suggestions over immutable candidate identities, not findings."""
import json
from pathlib import Path
from workflow import canonical, digest, identifier

# The response/storage envelope stays compatible with the reviewed v1 RPC.
# Policy revisions are separately named and hashed in every retained request.
POLICY = json.loads((Path(__file__).parent/'policies/paper-review-selection-policy-v2.json').read_bytes())
VERSION = 'paper-review-selection-v1'
GROUPS = {'central_claims': 3, 'testable_findings': 15}
SCHEMA = dict(type='object', additionalProperties=False, required=[*GROUPS, 'rationale'], properties={
 **{name:dict(type='array', maxItems=maximum, items=dict(type='object', additionalProperties=False,
  required=['claim_id','label'], properties=dict(claim_id=dict(type='string',maxLength=36),label=dict(type='string',minLength=1,maxLength=160)))) for name,maximum in GROUPS.items()},
 'rationale':dict(type='string',minLength=1,maxLength=1200)})

def candidates(claims, seeds):
 allowed={s['claim_id'] for s in seeds}
 result=[{k:c[k] for k in ('id','source_id','location','description','claim_type')} for c in claims if c['id'] in allowed]
 if len(result)!=len(allowed) or len(result)>200:raise ValueError('Review candidate identity coverage is incomplete.')
 return result

def batch(values, cursor):
 result=[]
 for row in values[cursor:cursor+8]:
  if len(canonical(result+[row]))>12000:
   if not result:raise ValueError('A review statement exceeds its 12 KB organization batch. Preserve it for manual classification.')
   break
  result.append(row)
 return result

def validate(value, allowed):
 if not isinstance(value,dict) or set(value)!={*GROUPS,'rationale'}:raise ValueError('Review selection needs both groups and an explicit rationale.')
 seen=set()
 for name,maximum in GROUPS.items():
  if not isinstance(value[name],list) or len(value[name])>maximum:raise ValueError('Review shortlist exceeds its declared group limit.')
  for item in value[name]:
   if not isinstance(item,dict) or set(item)!={'claim_id','label'} or not isinstance(item['label'],str) or not 1<=len(item['label'].strip())<=160:raise ValueError('Review suggestions need bounded labels and retained claim IDs.')
   key=identifier(item['claim_id'])
   if key not in allowed or key in seen:raise ValueError('Review groups contain a duplicate or unexamined claim identity.')
   seen.add(key)
 if not isinstance(value['rationale'],str) or not 1<=len(value['rationale'].strip())<=1200:raise ValueError('Review organization rationale is missing.')
 return value

def groups(value):
 return {name:[v['claim_id'] for v in value.get(name,[])] for name in GROUPS}

def selection_ids(value, allowed):
 if not isinstance(value,dict) or set(value)!=set(GROUPS):raise ValueError('Select central claims and testable findings separately.')
 ids=[]
 for name in GROUPS:
  if not isinstance(value[name],list) or len(value[name])>200:raise ValueError('Invalid selected review group.')
  ids.extend(identifier(v) for v in value[name])
 if not ids or len(ids)>200 or len(set(ids))!=len(ids) or not set(ids)<=set(allowed):raise ValueError('Select distinct retained statements from this paper.')
 return ids

def context(study, values, cursor, previous, feedback=None):
 incoming=batch(values,cursor)
 manifest=digest(canonical(values))
 return dict(task='paper-review-selection',paper_review_selection_requested=True,
  paper=dict(title=study['title']),policy=POLICY,policy_sha256=digest(canonical(POLICY)),
  candidate_inventory_sha256=manifest,total_candidates=len(values),cursor=cursor,
  candidates=incoming,prior_shortlist={name:previous.get(name,[]) for name in GROUPS},previous_feedback=feedback,
  input_scope='Full immutable statement text for this batch; prior shortlist labels are derived summaries. This is candidate organization, not complete source review.')
