"""Private durable model handoff. No provider retry or paid fallback."""
import json,time,urllib.error
import store
from workflow import canonical,digest

VERSION='openclaw-production-v1'
MODEL='gpt-6.1-sol'
WAIT_SECONDS=250
BUILDER_WAIT_SECONDS=350

class DispatchError(ValueError):
 def __init__(self,result):
  self.provenance=result.get('provenance',{})
  self.public_reason='OpenClaw stopped: '+result['status']+'; the original outcome is retained. No paid fallback.'
  super().__init__(self.public_reason)

def rpc(operation,payload):
 return store.rpc('research_model_dispatch',dict(p_operation=operation,p_payload=payload))

def health():return rpc('health',{})

def transient_read(error):
 if isinstance(error,urllib.error.HTTPError):return error.code in (429,500,502,503,504)
 if isinstance(error,(TimeoutError,ConnectionError,urllib.error.URLError)):return True
 return isinstance(error,store.StoreError) and any('HTTP '+str(code) in str(error) for code in (429,500,502,503,504))

def invoke(task,turn_id,request):
 if request['modelId']!=MODEL:raise ValueError('OpenClaw model identity mismatch; no fallback.')
 raw=canonical(request).decode()
 until=time.monotonic()+(BUILDER_WAIT_SECONDS if task.get('stage')=='building' else WAIT_SECONDS)
 try:admitted=rpc('enqueue',dict(id=turn_id,study_id=task['study_id'],task_id=task['id'],request_raw=raw,request_sha256=digest(raw.encode()),runtime=VERSION))
 except Exception as error:
  if not transient_read(error):raise
  # A timed-out POST may already have committed. Read its exact identity; never
  # resend enqueue. Absent, conflicting or unreadable admission stays uncertain.
  retained=rpc('result',dict(id=turn_id,study_id=task['study_id']))
  if any(retained.get(k)!=v for k,v in dict(id=turn_id,study_id=task['study_id'],task_id=task['id'],request_sha256=digest(raw.encode()),runtime=VERSION).items()):raise ValueError('Unconfirmed dispatch admission does not match the original request.')
  admitted=dict(replayed=False)
 if admitted.get('replayed'):raise ValueError('Retained model dispatch must be reconciled, never replayed.')
 while time.monotonic()<until:
  try:result=rpc('result',dict(id=turn_id,study_id=task['study_id']))
  except Exception as error:
   if not transient_read(error):raise
   # Repeat only this bounded read, never enqueue or provider transport. The
   # original request remains admitted and fenced throughout reconciliation.
   time.sleep(2);continue
  if result['status']=='complete':
   try:raw=store.artifact_bytes(task['study_id'],result['response_artifact_id'],maximum=150000)
   except Exception as error:
    if not transient_read(error):raise
    time.sleep(2);continue
   if digest(raw)!=result['response_sha256']:raise ValueError('OpenClaw response artifact identity mismatch.')
   answer=json.loads(raw);answer.setdefault('usage',{})['research_runtime']=result['provenance']
   return answer
  if result['status'] not in ('queued','running'):
   raise DispatchError(result)
  time.sleep(1)
 raise ValueError('OpenClaw completion is unconfirmed; retain the dispatch identity and do not retry it.')
