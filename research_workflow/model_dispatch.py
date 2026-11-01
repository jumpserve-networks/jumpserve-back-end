"""Private durable model handoff. No provider retry or paid fallback."""
import json,time,urllib.error
import store
from polling import Backoff, wait
from workflow import canonical,digest

VERSION='openclaw-production-v1'
MODEL='gpt-6.1-sol'
WAIT_SECONDS=250
BUILDER_WAIT_SECONDS=350
VALIDATION_LABELS={
 'logical-model-mismatch':'logical model identity',
 'logical-request-byte-limit':'complete logical request size',
 'native-model-mismatch':'native model identity',
 'native-tools-forbidden':'native tool restrictions',
 'native-request-shape':'native request structure',
 'native-messages-shape':'native message structure',
 'native-message-shape':'native message structure',
 'native-content-type':'native text content',
 'native-message-role':'native message role',
 'native-logical-reference':'retained request reference',
 'native-bridge-authorization':'private native bridge authorization',
 'native-content-length':'native content length',
 'native-bridge-byte-limit':'native bridge request size',
 'native-incomplete-body':'complete native request body',
 'native-json-invalid':'native JSON encoding',
 'provider-request-byte-limit':'complete provider request size'}

class DispatchError(ValueError):
 def __init__(self,result):
  self.provenance=result.get('provenance',{})
  self.public_reason='OpenClaw stopped: '+result['status']+'; the original outcome is retained. No paid fallback.'
  validation=self.provenance.get('validation',{});code=self.provenance.get('error_code')
  if result['status']=='failed' and self.provenance.get('provider_request_sent') is False and type(self.provenance.get('provider_calls')) is int and self.provenance['provider_calls']==0 and isinstance(validation,dict) and validation.get('version')=='openclaw-request-validation-v1' and validation.get('code')==code and code in VALIDATION_LABELS:
   # Use only server-owned labels and bounded integers. Never echo a provider
   # message, source text, arbitrary field value, token or original prompt.
   observed,limit=validation.get('observed'),validation.get('limit')
   sizes=' ('+str(observed)+' bytes; limit '+str(limit)+' bytes)' if code.endswith('byte-limit') and type(observed) is int and type(limit) is int and 0<=observed<=1000000000 and 1<=limit<=1000000000 else ''
   self.public_reason='Local AI request rejected at '+VALIDATION_LABELS[code]+sizes+'. No model request was sent; the original outcome is retained. No paid fallback.'
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
 pending=Backoff(2,5);unavailable=Backoff(2,10);previous_status=None
 while time.monotonic()<until:
  try:result=rpc('result',dict(id=turn_id,study_id=task['study_id']))
  except Exception as error:
   if not transient_read(error):raise
   # Repeat only this bounded read, never enqueue or provider transport. The
   # original request remains admitted and fenced throughout reconciliation.
   wait(unavailable.next_delay(),until);continue
  unavailable.reset()
  if result['status']!=previous_status:pending.reset();previous_status=result['status']
  if result['status']=='complete':
   try:raw=store.artifact_bytes(task['study_id'],result['response_artifact_id'],maximum=150000)
   except Exception as error:
    if not transient_read(error):raise
    wait(pending.next_delay(),until);continue
   if digest(raw)!=result['response_sha256']:raise ValueError('OpenClaw response artifact identity mismatch.')
   answer=json.loads(raw);answer.setdefault('usage',{})['research_runtime']=result['provenance']
   return answer
  if result['status'] not in ('queued','running'):
   raise DispatchError(result)
  wait(pending.next_delay(),until)
 raise ValueError('OpenClaw completion is unconfirmed; retain the dispatch identity and do not retry it.')
