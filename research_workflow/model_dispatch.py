"""Private durable model handoff. No provider retry or paid fallback."""
import json,time
import store
from workflow import canonical,digest

VERSION='openclaw-production-v1'
MODEL='gpt-6.1-sol'
WAIT_SECONDS=250
BUILDER_WAIT_SECONDS=260

class DispatchError(ValueError):
 def __init__(self,result):
  self.provenance=result.get('provenance',{})
  self.public_reason='OpenClaw stopped: '+result['status']+'; the original outcome is retained. No paid fallback.'
  super().__init__(self.public_reason)

def rpc(operation,payload):
 return store.rpc('research_model_dispatch',dict(p_operation=operation,p_payload=payload))

def health():return rpc('health',{})

def invoke(task,turn_id,request):
 if request['modelId']!=MODEL:raise ValueError('OpenClaw model identity mismatch; no fallback.')
 raw=canonical(request).decode()
 admitted=rpc('enqueue',dict(id=turn_id,study_id=task['study_id'],task_id=task['id'],request_raw=raw,request_sha256=digest(raw.encode()),runtime=VERSION))
 if admitted.get('replayed'):raise ValueError('Retained model dispatch must be reconciled, never replayed.')
 until=time.monotonic()+(BUILDER_WAIT_SECONDS if task.get('stage')=='building' else WAIT_SECONDS)
 while time.monotonic()<until:
  result=rpc('result',dict(id=turn_id,study_id=task['study_id']))
  if result['status']=='complete':
   raw=store.artifact_bytes(task['study_id'],result['response_artifact_id'],maximum=150000)
   if digest(raw)!=result['response_sha256']:raise ValueError('OpenClaw response artifact identity mismatch.')
   answer=json.loads(raw);answer.setdefault('usage',{})['research_runtime']=result['provenance']
   return answer
  if result['status'] not in ('queued','running'):
   raise DispatchError(result)
  time.sleep(1)
 raise ValueError('OpenClaw completion is unconfirmed; retain the dispatch identity and do not retry it.')
