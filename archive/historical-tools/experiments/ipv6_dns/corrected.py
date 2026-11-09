"""Finite-control correction v1; never substituted for historical author summaries.

Preserve each observation's status and duration; avoid mutating input records.
This is a separately versioned semantic specification, not a validated full ZDNS pipeline.
"""
import copy,hashlib,json,statistics
def preserve_observations(records):
 groups={}
 for row in records:
  value=copy.deepcopy(row['results']);state={}
  for kind,result in value.items():
   data=result.get('data',{});answers=copy.deepcopy(data.get('answers',[]));authorities=copy.deepcopy(data.get('authorities',[]))
   for r in answers+authorities:
    if 'ttl' in r:r['ttl']=-1
    if r.get('type')=='SOA' and 'serial' in r:r['serial']=-1
   state[kind]=dict(answers=answers,authorities=authorities,status=result.get('status'),additionals=data.get('additionals'))
  digest=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
  group=groups.setdefault(digest,dict(count=0,status=value['NS'].get('status'),durations=[]));group['count']+=1
  if 'duration' in value['NS']:group['durations'].append(value['NS']['duration'])
 return list(groups.values())
def duration_summary(values):
 return dict(mean=statistics.fmean(values) if values else None,min=min(values) if values else None,max=max(values) if values else None,status='recorded' if values else 'missing')
