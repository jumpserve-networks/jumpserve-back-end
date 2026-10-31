"""Pinned native OpenClaw + SIWC; private one-inference-per-turn bridge."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import fcntl,io,json,os,secrets,sqlite3,ssl,subprocess,threading,time,urllib.error,urllib.request,uuid
from pathlib import Path
import autonomy_model as model
import siwc_protocol as wire
from workflow import canonical,digest

VERSION='openclaw-production-v1'
MODEL='gpt-6.1-sol'
DATA_RESPONSE_SECONDS=90
DATA_CLI_SECONDS=120
DATA_PROCESS_SECONDS=130
REQUEST_VALIDATION_VERSION='openclaw-request-validation-v1'
HANDOFF_VERSION='openclaw-retained-turn-handoff-v1'
NODE=Path(os.environ.get('OPENCLAW_NODE','/opt/jumpserve/node/bin/node'))
PACKAGE=Path(os.environ.get('OPENCLAW_PACKAGE','/opt/jumpserve/openclaw/node_modules/openclaw'))
AUTH=Path(os.environ.get('OPENCLAW_AUTH_DIR','/var/lib/jumpserve-openclaw/auth'))

def environment(home):
 return dict(PATH=str(NODE.parent)+':/usr/bin:/bin',HOME=str(home),LANG='C.UTF-8',TMPDIR=str(home),DO_NOT_TRACK='1',OPENCLAW_SKIP_CHANNELS='1',OPENCLAW_DISABLE_DISCOVERY='1',OPENCLAW_STATE_DIR=str(home/'state'))

def verify_runtime():
 if subprocess.check_output([str(NODE),'--version'],text=True).strip()!='v24.16.0' or json.loads((PACKAGE/'package.json').read_bytes())['version']!='2026.10.5':raise ValueError('Pinned OpenClaw/Node runtime differs.')

class CredentialFailure(ValueError):
 def __init__(self,reason,status='unavailable',retry_after_seconds=0):
  super().__init__(reason);self.reason=reason;self.status=status;self.retry_after_seconds=retry_after_seconds

def credential(force_refresh=False):
 """One host owner, including recovery/backoff checks before returning a token."""
 try:
  AUTH.mkdir(mode=0o700,parents=True,exist_ok=True);AUTH.chmod(0o700)
  with (AUTH/'refresh.lock').open('a') as lock:
   os.chmod(AUTH/'refresh.lock',0o600);fcntl.flock(lock,fcntl.LOCK_EX)
   path=AUTH/'state/openclaw.sqlite'
   command=[str(NODE),str(Path(__file__).with_name('openclaw_auth.mjs')),str(path),str(PACKAGE/'dist/extensions/openai/token-sharing-oauth.runtime.js')]
   if force_refresh:command.append('--force')
   try:result=subprocess.run(command,capture_output=True,timeout=75,env=environment(AUTH))
   except subprocess.TimeoutExpired:raise CredentialFailure('refresh-unconfirmed') from None
   try:state=json.loads(result.stdout)
   except (ValueError,UnicodeError):raise CredentialFailure('refresh-unconfirmed') from None
   reasons={'credential-missing','grant-invalid','client-configuration','permission-required','storage-unavailable','storage-low','refresh-temporary','refresh-unconfirmed','refresh-backoff','refresh-rejected','credential-size'}
   if result.returncode or state.get('status')!='ready':
    reason=state.get('reason');status=state.get('status');delay=state.get('retry_after_seconds',0)
    if reason not in reasons or status not in ('auth-required','unavailable') or type(delay) is not int or not 0<=delay<=900:raise CredentialFailure('refresh-unconfirmed')
    raise CredentialFailure(reason,status,delay)
   with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:row=db.execute("select value_json from config_machine_state where state_key='authProfiles.store'").fetchone()
   values=list((json.loads(row[0]) if row else {}).get('profiles',{}).values())
   if len(values)!=1:raise CredentialFailure('client-configuration')
   value=values[0];scopes=set((value.get('grantedScope') or '').split())
   if value.get('type')!='oauth' or value.get('provider')!='openai' or value.get('authFlow')!='chatgpt-token-sharing' or value.get('issuer')!='https://auth.openai.com' or not {'resource.invoke','chatgpt.tokens.use.direct'}<=scopes or not isinstance(value.get('access'),str) or value['access'].startswith('sk-'):raise CredentialFailure('client-configuration')
   if value.get('expires',0)<=time.time()*1000+30000:raise CredentialFailure('refresh-backoff',retry_after_seconds=1)
   return value['access']
 except CredentialFailure:raise
 except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):raise CredentialFailure('storage-unavailable') from None

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('Provider redirects are disabled.')

def http_request(method,url,token,body=None,timeout_seconds=65):
 if (method,url) not in {('GET','https://api.openai.com/v1/models'),('POST','https://api.openai.com/v1/responses')}:raise ValueError('Unexpected subscription endpoint.')
 ca='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').exists() else None
 context=ssl.create_default_context(cafile=ca);context.keylog_filename=None
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=context))
 request=urllib.request.Request(url,data=body,method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'text/event-stream' if method=='POST' else 'application/json'})
 if not 1<=timeout_seconds<=65:raise ValueError('Provider transport timeout is outside its bound.')
 try:return opener.open(request,timeout=timeout_seconds)
 except urllib.error.HTTPError as response:return response

def catalog(token):
 with http_request('GET','https://api.openai.com/v1/models',token) as response:
  raw=response.read(1048577)
  if response.status!=200 or len(raw)>1048576 or not any(r.get('slug')==MODEL and r.get('visibility')=='list' for r in json.loads(raw).get('models',[])):
   raise ValueError('The exact model is unavailable to the authorized ChatGPT account.')
 return dict(model_id=MODEL,catalog_sha256=digest(raw))

class RequestRejected(ValueError):
 def __init__(self,code,field,rule,observed=None,limit=None):
  super().__init__(code);self.code=code
  self.validation=dict(version=REQUEST_VALIDATION_VERSION,code=code,field=field,rule=rule,observed=observed,limit=limit)

def translate(request,logical):
 if not isinstance(request,dict):raise RequestRejected('native-request-shape','native','JSON object required')
 if request.get('model')!=MODEL:raise RequestRejected('native-model-mismatch','native.model','Pinned model required')
 if request.get('tools'):raise RequestRejected('native-tools-forbidden','native.tools','Data-only adapter admits no tools')
 if not isinstance(request.get('messages'),list) or not request['messages']:raise RequestRejected('native-messages-shape','native.messages','Nonempty message array required')
 systems=[];messages=[]
 for item in request['messages']:
  if not isinstance(item,dict) or 'content' not in item:raise RequestRejected('native-message-shape','native.messages[]','Object with content required')
  content=item['content']
  if not isinstance(content,str):
   if not isinstance(content,list) or any(not isinstance(p,dict) or p.get('type')!='text' or not isinstance(p.get('text'),str) for p in content):raise RequestRejected('native-content-type','native.messages[].content','Only text is admitted')
   content='\n'.join(p['text'] for p in content)
  if item.get('role') in ('system','developer'):systems.append(dict(text=content))
  elif item.get('role') in ('user','assistant'):messages.append(dict(role=item['role'],content=[dict(text=content)]))
  else:raise RequestRejected('native-message-role','native.messages[].role','Only system/developer/user/assistant roles are admitted')
 return dict(modelId=MODEL,system=systems,messages=messages,toolConfig=logical['toolConfig'])

def execute(logical,directory,token_source=credential,transport=http_request):
 """Return parsed answer plus original-byte directory; no database side effects."""
 directory=Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=False)
 original=canonical(logical);(directory/'logical.json').write_bytes(original)
 result=dict(provider_request_sent=False,provider_calls=0,paid_fallback=False,request_sizes=dict(logical_bytes=len(original)))
 try:
  if logical.get('modelId')!=MODEL:raise RequestRejected('logical-model-mismatch','logical.modelId','Pinned model required')
  if len(original)>96000:raise RequestRejected('logical-request-byte-limit','logical','Complete UTF-8 JSON bytes',len(original),96000)
 except RequestRejected as error:
  result.update(runtime=VERSION,model_id=MODEL,status='failed',error_code=error.code,validation=error.validation,usage=None,measured_charges_usd=None,subscription_allocation_usd=None)
  (directory/'receipt.json').write_bytes(canonical(result));return None,result
 try:token=token_source()
 except CredentialFailure as error:
  receipt=dict(runtime=VERSION,model_id=MODEL,status='paused-auth' if error.status=='auth-required' else 'failed',error_code=error.reason,credential_status=error.status,retry_after_seconds=error.retry_after_seconds,provider_request_sent=False,provider_calls=0,usage=None,paid_fallback=False,measured_charges_usd=None,subscription_allocation_usd=None)
  (directory/'receipt.json').write_bytes(canonical(receipt));return None,receipt
 workspace=directory/'workspace';workspace.mkdir();state=directory/'state';state.mkdir()
 (workspace/'AGENTS.md').write_text('\n\n'.join(b['text'] for b in logical['system'])+'\nReturn one JSON decision. No tools or shell access are available.\n')
 message=directory/'message.json';message.write_text(logical['messages'][0]['content'][0]['text'])
 # Native OpenClaw is the authenticated transport, not an additional model
 # interpretation. Its wrapper need not duplicate the entire source context:
 # the bridge sends wire.response_request(logical) unchanged exactly once.
 binding='controller-retained-request-sha256:'+digest(original)
 handoff=canonical(dict(version=HANDOFF_VERSION,reference=binding,logical_bytes=len(original),instruction='Execute this retained data-only controller turn. The local bridge supplies its complete original policy, messages and decision schema unchanged. Return that JSON decision unchanged; no tools or additional provider calls.'))
 native_message=directory/'native-message.json';native_message.write_bytes(handoff)
 result['context_transport']=dict(version=HANDOFF_VERSION,original_logical_sha256=digest(original),original_message_sha256=digest(message.read_bytes()),native_message_sha256=digest(handoff),scope='Hash-bound native handoff only; complete upstream logical authority/context/schema remain unchanged, not summarized.')
 result['request_sizes'].update(original_message_bytes=message.stat().st_size,native_handoff_bytes=len(handoff))
 bridge_token=secrets.token_urlsafe(32);lock=threading.Lock();started=False
 def inference(raw):
  nonlocal started
  with lock:
   if started:
    if result.get('request_hash')==digest(raw) and result.get('answer'):return result['answer']
    raise RequestRejected('retained-provider-operation','native','A provider operation is already retained; no retry')
   started=True;result['request_hash']=digest(raw)
  (directory/'openclaw-http-body.bin').write_bytes(raw)
  # OpenClaw adds its own bootstrap and schema wrapper. Forward the admitted
  # logical request, not that duplicate wrapper, preserving its exact authority,
  # context and decision schema once. Retain both original transports.
  translated=translate(json.loads(raw),logical)  # Reject unexpected roles/tools/models.
  if not any(binding in p['text'] for m in translated['messages'] if m['role']=='user' for p in m['content']):raise RequestRejected('native-logical-reference','native.messages','Exact retained logical SHA256 reference required')
  body=canonical(wire.response_request(logical))
  result['request_sizes']['provider_bytes']=len(body)
  if len(body)>96000:
   result.update(status='failed',error_code='provider-request-byte-limit',provider_request_sent=False,provider_calls=0)
   raise RequestRejected('provider-request-byte-limit','provider','Complete UTF-8 JSON bytes',len(body),96000)
  (directory/'provider-request.json').write_bytes(body)
  result.update(status='unknown',response_received=False,usage=None,provider_retries=0,paid_fallback=False,provider_request_sent=True,provider_calls=1)
  with transport('POST','https://api.openai.com/v1/responses',token,body) as response:
   result.update(response_received=True,http_status=response.status,request_id=response.headers.get('x-request-id'))
   with (directory/'provider-response.bin').open('wb') as sink:
    if response.status!=200:
     raw=response.read(1048577);sink.write(raw)
     try:code=(json.loads(raw).get('error') or {}).get('code')
     except (ValueError,AttributeError):code=None
     raise wire.failure(code or 'http-'+str(response.status),response.status)
    completed=wire.read_stream(response,sink,time.monotonic()+DATA_RESPONSE_SECONDS,1048576)
  result['usage']=wire.usage(completed)
  try:answer=wire.structured_response(completed)
  except (ValueError,KeyError,TypeError):raise wire.StreamFailure('invalid-structured-decision','invalid-output',result['usage'])
  result.update(status='complete',answer=answer);return answer
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_POST(self):
   try:
    if self.path!='/v1/chat/completions' or self.headers.get('Authorization')!='Bearer '+bridge_token:raise RequestRejected('native-bridge-authorization','native.authorization','Expected private path and bearer token required')
    try:length=int(self.headers.get('Content-Length','0'))
    except ValueError:raise RequestRejected('native-content-length','native.content_length','Integer UTF-8 body length required') from None
    result['request_sizes']['native_bridge_bytes']=length
    if not 0<length<=96000:raise RequestRejected('native-bridge-byte-limit','native.content_length','Complete UTF-8 JSON bytes',length,96000)
    received=self.rfile.read(length)
    if len(received)!=length:raise RequestRejected('native-incomplete-body','native.body','Body must match Content-Length',len(received),length)
    try:request=json.loads(received)
    except (ValueError,UnicodeError):
     (directory/'rejected-native-body.bin').write_bytes(received)
     raise RequestRejected('native-json-invalid','native.body','Valid UTF-8 JSON required') from None
    answer=inference(received);text=canonical(model.parse(answer)).decode();usage=answer.get('usage',{})
    item=dict(id='retained-data-only',object='chat.completion',created=int(time.time()),model=MODEL,choices=[dict(index=0,message=dict(role='assistant',content=text),finish_reason='stop')],usage=dict(prompt_tokens=usage.get('inputTokens'),completion_tokens=usage.get('outputTokens'),total_tokens=usage.get('totalTokens')))
    raw=canonical(item);kind='application/json'
    if request.get('stream'):
     chunks=[dict(id=item['id'],object='chat.completion.chunk',created=item['created'],model=MODEL,choices=[dict(index=0,delta=dict(role='assistant',content=text),finish_reason=None)]),dict(id=item['id'],object='chat.completion.chunk',created=item['created'],model=MODEL,choices=[dict(index=0,delta={},finish_reason='stop')],usage=item['usage'])]
     raw=b''.join(b'data: '+canonical(c)+b'\n\n' for c in chunks)+b'data: [DONE]\n\n';kind='text/event-stream'
    self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
   except Exception as error:
    if result['provider_request_sent'] is False:
     result.setdefault('status','failed');result.setdefault('error_code','local-pre-provider-'+type(error).__name__)
    elif not isinstance(error,RequestRejected) or error.code!='retained-provider-operation':result.update(status=getattr(error,'status','unknown'),error_code=getattr(error,'code','unconfirmed-'+type(error).__name__))
    if getattr(error,'usage',None):result['usage']=error.usage
    if isinstance(error,RequestRejected):
     result['validation']=error.validation
     if not result['provider_request_sent']:result['error_code']=error.code
    payload=canonical(dict(error=dict(type='invalid_request_error',code=error.code if isinstance(error,RequestRejected) else result['error_code'],message='Retained model operation rejected; no provider retry.',param=error.validation['field'] if isinstance(error,RequestRejected) else None)))
    self.send_response(400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 config=dict(models=dict(mode='replace',providers=dict(lab=dict(baseUrl='http://127.0.0.1:'+str(server.server_address[1])+'/v1',apiKey=bridge_token,api='openai-completions',models=[dict(id=MODEL,name='Subscription data-only adapter',reasoning=False,input=['text'],contextWindow=200000,maxTokens=4096,cost=dict(input=0,output=0,cacheRead=0,cacheWrite=0))]))),agents=dict(defaults=dict(model=dict(primary='lab/'+MODEL,fallbacks=[]),workspace=str(workspace),skipBootstrap=True,bootstrapMaxChars=30000,bootstrapTotalMaxChars=40000,compaction=dict(enabled=False))),tools=dict(deny=['*'],toolSearch=False,codeMode=False,elevated=dict(enabled=False)),plugins=dict(enabled=False),skills=dict(allowBundled=[]))
 config_path=directory/'config.json';config_path.write_bytes(canonical(config));env=environment(directory);env.update(OPENCLAW_CONFIG_PATH=str(config_path),OPENCLAW_STATE_DIR=str(state))
 began=time.monotonic()
 try:
  cli=subprocess.run([str(NODE),str(PACKAGE/'openclaw.mjs'),'agent','exec','--config',str(config_path),'--state-dir',str(state),'--cwd',str(workspace),'--message-file',str(native_message),'--model','lab/'+MODEL,'--code-mode','direct','--thinking','off','--timeout',str(DATA_CLI_SECONDS),'--json'],capture_output=True,timeout=DATA_PROCESS_SECONDS,env=env)
  (directory/'stdout.bin').write_bytes(cli.stdout);(directory/'stderr.bin').write_bytes(cli.stderr)
  if cli.returncode or not result.get('answer'):raise ValueError('OpenClaw CLI did not complete its retained turn.')
  envelope=json.loads(cli.stdout);payloads=envelope.get('payloads') or envelope.get('result',{}).get('payloads') or [];texts=[p['text'] for p in payloads if p.get('text')]
  if len(texts)!=1 or json.loads(texts[0])!=model.parse(result['answer']):raise ValueError('OpenClaw final answer differs from its original provider decision.')
 except Exception as error:
  if result.get('status')=='complete':result['status']='invalid-output'
  result.setdefault('status','unknown');result.setdefault('error_code','runtime-'+type(error).__name__)
 finally:
  server.shutdown();server.server_close();thread.join(timeout=2)
 result.update(runtime=VERSION,node_version='24.16.0',openclaw_version='2026.10.5',model_id=MODEL,wall_seconds=time.monotonic()-began,subscription_allocation_usd=None,measured_charges_usd=None,estimated_cost_usd=None,catalog_cost_fields='Numeric zeros required by the adapter are non-billing placeholders.',turn_policy=model.TURN_POLICY['version'],timing_policy=dict(version='data-only-window-v2',stream_seconds=DATA_RESPONSE_SECONDS,cli_seconds=DATA_CLI_SECONDS,process_seconds=DATA_PROCESS_SECONDS))
 answer=result.pop('answer',None)
 (directory/'receipt.json').write_bytes(canonical(result))
 return answer if result['status']=='complete' else None,result

def persist_completed(job,directory):
 directory=Path(directory)
 with (directory/'completion.lock').open('a+b') as lock:
  try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return False
  return _persist_completed(job,directory)

def _persist_completed(job,directory):
 """Idempotent persistence only. This function cannot invoke model transport."""
 import model_dispatch as dispatch,store
 directory=Path(directory);completed=json.loads((directory/'completion.json').read_bytes())
 receipt=completed['receipt'];answer=completed['answer']
 if digest((directory/'logical.json').read_bytes())!=job['request_sha256']:raise ValueError('Pending completion original request hash differs.')
 prior=dispatch.rpc('result',dict(id=job['id'],study_id=job['study_id']))
 if prior['status'] not in ('queued','running'):
  # A missed lease is reconciled separately, never overwrite a terminal row.
  return prior.get('provenance',{}).get('completion_sha256')==digest((directory/'completion.json').read_bytes())
 artifacts=[]
 for path in sorted(directory.iterdir()):
  if path.is_file() and not path.is_symlink() and path.name not in ('config.json','completion.lock'):
   raw=path.read_bytes();identity=str(uuid.uuid5(uuid.UUID(job['id']),'runtime:'+path.name))
   record=store.artifact(job['study_id'],job['actor_id'],raw,identity,'application/octet-stream',dict(runtime=VERSION,kind='model-runtime-original',dispatch_id=job['id'],filename=path.name,response_received=receipt.get('response_received'),transformation='Original captured bytes; not scientific measurements.'))
   store.append(job['study_id'],job['actor_id'],'artifacts',record);artifacts.append(dict(id=record['id'],name=path.name,sha256=record['sha256'],byte_count=record['byte_count']))
 response_id=None;response_sha=None
 if answer:
  raw=canonical(answer);response_id=str(uuid.uuid5(uuid.UUID(job['id']),'controller-response'));record=store.artifact(job['study_id'],job['actor_id'],raw,response_id,'application/json',dict(runtime=VERSION,kind='derived-controller-model-response',dispatch_id=job['id'],transformation='Completed SSE to typed decision; original SSE and native CLI output retained separately.'))
  store.append(job['study_id'],job['actor_id'],'artifacts',record);response_sha=record['sha256']
 provenance=dict(receipt,artifacts=artifacts,completion_sha256=digest((directory/'completion.json').read_bytes()))
 dispatch.rpc('finish',dict(id=job['id'],token=job['lease_token'],status=receipt['status'],response_artifact_id=response_id,response_sha256=response_sha,provenance=provenance))
 return True

def save_completion(job,directory,answer,receipt,pending):
 """Capture before transport; the outbox contains private worker metadata."""
 from dispatch_recovery import write_once
 raw=canonical(dict(answer=answer,receipt=receipt));path=directory/'completion.json'
 pending.mkdir(mode=0o700,exist_ok=True)
 marker=pending/(str(uuid.UUID(job['id']))+'.json')
 write_once(marker,canonical(job))
 # Durable intent precedes the payload: a crash after completion publication
 # can never strand captured results without an outbox entry.
 write_once(path,raw)
 return marker

def retry_completions(spool):
 import model_dispatch as dispatch
 pending=Path(spool)/'pending-completions'
 if not pending.exists():return
 for marker in sorted(pending.glob('*.json'))[:4]:
  if marker.is_symlink():raise ValueError('Completion outbox cannot follow symlinks.')
  job=json.loads(marker.read_bytes());directory=Path(spool)/str(uuid.UUID(job['id']))
  try:
   if persist_completed(job,directory):marker.unlink()
   elif store_reconciliation_present(job):marker.rename(marker.with_suffix('.reconciled'))
   elif dispatch.rpc('result',dict(id=job['id'],study_id=job['study_id']))['status'] not in ('queued','running'):
    # Preserve a terminal mismatch for reconciliation without starving later
    # pending saves. Renaming metadata does not replay or resolve that outcome.
    marker.rename(marker.with_suffix('.awaiting-reconciliation'))
  except Exception as error:print(json.dumps(dict(runtime=VERSION,status='completion-persistence-pending',dispatch_id=job['id'],error_type=type(error).__name__)),flush=True)

def store_reconciliation_present(job):
 import store
 return bool(store.rows('model_reconciliations',dict(dispatch_id='eq.'+job['id'],limit=1)))

def worker_loop():
 import model_dispatch as dispatch,store
 from openclaw_health import WorkerAvailability
 from workflow import now
 os.umask(0o077);verify_runtime();spool=Path(os.environ.get('OPENCLAW_SPOOL','/var/lib/jumpserve-openclaw/spool'));spool.mkdir(mode=0o700,parents=True,exist_ok=True)
 availability=WorkerAvailability(AUTH,spool,credential)
 def process(job):
  directory=spool/job['id']
  if directory.exists():raise ValueError('Original spool exists; reconcile retained request, never replay.')
  logical=json.loads(job['request_raw'])
  if logical.get('builder'):
   import experiment_builder
   answer,receipt=experiment_builder.execute(logical,directory,job)
  else:answer,receipt=execute(logical,directory)
  availability.observe(receipt)
  marker=save_completion(job,directory,answer,receipt,spool/'pending-completions')
  if persist_completed(job,directory):marker.unlink()
 with ThreadPoolExecutor(max_workers=2) as pool:
  futures=[];last_health=0;last_builder_reap=0;last_reconciliation=0;recovery=None;ready=False
  while True:
   try:
    if recovery and recovery.done():
     try:recovery.result()
     except Exception as error:print(json.dumps(dict(runtime=VERSION,status='reconciliation-pending',error_type=type(error).__name__)),flush=True)
     recovery=None
    if recovery is None and time.monotonic()-last_reconciliation>30:
     def recover():
      retry_completions(spool)
      if os.environ.get('RESEARCH_BUILDER_ENABLED')=='true':
       import dispatch_recovery
       dispatch_recovery.reconcile_spool(spool)
     # Separate recovery executor keeps storage/hash reads off the heartbeat
     # loop and does not consume either of the two inference worker slots.
     if 'recovery_pool' not in locals():recovery_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='retained-result-recovery')
     recovery=recovery_pool.submit(recover)
     last_reconciliation=time.monotonic()
    if os.environ.get('RESEARCH_BUILDER_ENABLED')=='true' and time.monotonic()-last_builder_reap>60:
     import experiment_builder
     experiment_builder.reap(spool);last_builder_reap=time.monotonic()
    if time.monotonic()-last_health>5:
     health=availability.check();ready=health['status']=='ready'
     dispatch.rpc('heartbeat',dict(status=health['status'],configuration=dict(runtime=VERSION,model_id=MODEL,paid_fallback=False,actual_cost_usd=None,auth_recovery='openclaw-auth-recovery-v1',reason=health['reason'],retry_after_seconds=health['retry_after_seconds'])));last_health=time.monotonic()
    active=[]
    for future in futures:
     if future.done():
      try:future.result()
      except Exception as error:print(json.dumps(dict(runtime=VERSION,status='unconfirmed',error_type=type(error).__name__)),flush=True)
     else:active.append(future)
    futures=active
    if ready and len(futures)<2:
     # A completed concurrent turn may have recorded a quota/provider pause
     # since the periodic heartbeat. Check again before claiming more work.
     admission=availability.check()
     if admission['status']!='ready':ready=False;continue
     job=dispatch.rpc('claim',{})
     if job:futures.append(pool.submit(process,job))
   except Exception as error:print(json.dumps(dict(runtime=VERSION,status='unavailable',error_type=type(error).__name__)),flush=True)
   time.sleep(1)

if __name__=='__main__':
 import sys
 if sys.argv[1:]==['--resume-quota']:
  from openclaw_health import WorkerAvailability
  print(json.dumps(WorkerAvailability(AUTH,Path(os.environ.get('OPENCLAW_SPOOL','/var/lib/jumpserve-openclaw/spool')),credential).resume_quota()))
 elif sys.argv[1:]:raise SystemExit('Only --resume-quota or the worker loop is supported.')
 else:worker_loop()
