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
NODE=Path(os.environ.get('OPENCLAW_NODE','/opt/jumpserve/node/bin/node'))
PACKAGE=Path(os.environ.get('OPENCLAW_PACKAGE','/opt/jumpserve/openclaw/node_modules/openclaw'))
AUTH=Path(os.environ.get('OPENCLAW_AUTH_DIR','/var/lib/jumpserve-openclaw/auth'))

def environment(home):
 return dict(PATH=str(NODE.parent)+':/usr/bin:/bin',HOME=str(home),LANG='C.UTF-8',TMPDIR=str(home),DO_NOT_TRACK='1',OPENCLAW_SKIP_CHANNELS='1',OPENCLAW_DISABLE_DISCOVERY='1',OPENCLAW_STATE_DIR=str(home/'state'))

def verify_runtime():
 if subprocess.check_output([str(NODE),'--version'],text=True).strip()!='v24.16.0' or json.loads((PACKAGE/'package.json').read_bytes())['version']!='2026.10.5':raise ValueError('Pinned OpenClaw/Node runtime differs.')

def credential():
 AUTH.mkdir(mode=0o700,parents=True,exist_ok=True);AUTH.chmod(0o700)
 with (AUTH/'refresh.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  path=AUTH/'state/openclaw.sqlite'
  def read():
   with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:row=db.execute("select value_json from config_machine_state where state_key='authProfiles.store'").fetchone()
   values=list((json.loads(row[0]) if row else {}).get('profiles',{}).values())
   if len(values)!=1:raise ValueError('Exactly one host-specific subscription grant required.')
   value=values[0];scopes=set((value.get('grantedScope') or '').split())
   if value.get('type')!='oauth' or value.get('provider')!='openai' or value.get('authFlow')!='chatgpt-token-sharing' or value.get('issuer')!='https://auth.openai.com' or not {'resource.invoke','chatgpt.tokens.use.direct'}<=scopes or not isinstance(value.get('access'),str) or value['access'].startswith('sk-'):
    raise ValueError('Native ChatGPT plan-use consent required; API-key fallback is disabled.')
   return value
  value=read()
  if value.get('expires',0)<=time.time()*1000+90000:
   result=subprocess.run([str(NODE),str(Path(__file__).with_name('openclaw_auth.mjs')),str(path),str(PACKAGE/'dist/extensions/openai/token-sharing-oauth.runtime.js')],capture_output=True,timeout=20,env=environment(AUTH))
   if result.returncode:raise ValueError('Native ChatGPT authorization needs sign-in; no fallback.')
   value=read()
  if value.get('expires',0)<=time.time()*1000+30000:raise ValueError('Native subscription authorization is expired.')
  return value['access']

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('Provider redirects are disabled.')

def http_request(method,url,token,body=None):
 if (method,url) not in {('GET','https://api.openai.com/v1/models'),('POST','https://api.openai.com/v1/responses')}:raise ValueError('Unexpected subscription endpoint.')
 ca='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').exists() else None
 context=ssl.create_default_context(cafile=ca);context.keylog_filename=None
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=context))
 request=urllib.request.Request(url,data=body,method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'text/event-stream' if method=='POST' else 'application/json'})
 try:return opener.open(request,timeout=65)
 except urllib.error.HTTPError as response:return response

def catalog(token):
 with http_request('GET','https://api.openai.com/v1/models',token) as response:
  raw=response.read(1048577)
  if response.status!=200 or len(raw)>1048576 or not any(r.get('slug')==MODEL and r.get('visibility')=='list' for r in json.loads(raw).get('models',[])):
   raise ValueError('The exact model is unavailable to the authorized ChatGPT account.')
 return dict(model_id=MODEL,catalog_sha256=digest(raw))

def translate(request,logical):
 if request.get('model')!=MODEL or request.get('tools'):raise ValueError('OpenClaw requested an unexpected model/tool.')
 systems=[];messages=[]
 for item in request['messages']:
  content=item['content']
  if not isinstance(content,str):
   if not isinstance(content,list) or any(p.get('type')!='text' for p in content):raise ValueError('Only text is admitted.')
   content='\n'.join(p['text'] for p in content)
  if item['role'] in ('system','developer'):systems.append(dict(text=content))
  elif item['role'] in ('user','assistant'):messages.append(dict(role=item['role'],content=[dict(text=content)]))
  else:raise ValueError('Unexpected tool/history role.')
 return dict(modelId=MODEL,system=systems,messages=messages,toolConfig=logical['toolConfig'])

def execute(logical,directory,token_source=credential,transport=http_request):
 """Return parsed answer plus original-byte directory; no database side effects."""
 directory=Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=False)
 if logical['modelId']!=MODEL or len(canonical(logical))>96000:raise ValueError('Invalid complete logical request.')
 token=token_source();workspace=directory/'workspace';workspace.mkdir();state=directory/'state';state.mkdir()
 (workspace/'AGENTS.md').write_text('\n\n'.join(b['text'] for b in logical['system'])+'\nReturn one JSON decision. No tools or shell access are available.\n')
 message=directory/'message.json';message.write_text(logical['messages'][0]['content'][0]['text'])
 (directory/'logical.json').write_bytes(canonical(logical))
 bridge_token=secrets.token_urlsafe(32);result={};lock=threading.Lock();started=False
 def inference(raw):
  nonlocal started
  with lock:
   if started:
    if result.get('request_hash')==digest(raw) and result.get('answer'):return result['answer']
    raise ValueError('A provider operation is already retained; no retry.')
   started=True;result['request_hash']=digest(raw)
  (directory/'openclaw-http-body.bin').write_bytes(raw)
  body=canonical(wire.response_request(translate(json.loads(raw),logical)))
  if len(body)>96000:raise ValueError('Complete provider request exceeds 96000 bytes; no call.')
  (directory/'provider-request.json').write_bytes(body)
  result.update(status='unknown',response_received=False,usage=None,provider_retries=0,paid_fallback=False)
  with transport('POST','https://api.openai.com/v1/responses',token,body) as response:
   result.update(response_received=True,http_status=response.status,request_id=response.headers.get('x-request-id'))
   with (directory/'provider-response.bin').open('wb') as sink:
    if response.status!=200:
     raw=response.read(1048577);sink.write(raw)
     try:code=(json.loads(raw).get('error') or {}).get('code')
     except (ValueError,AttributeError):code=None
     raise wire.failure(code or 'http-'+str(response.status),response.status)
    completed=wire.read_stream(response,sink,time.monotonic()+65,1048576)
  result['usage']=wire.usage(completed)
  try:answer=wire.structured_response(completed)
  except (ValueError,KeyError,TypeError):raise wire.StreamFailure('invalid-structured-decision','invalid-output',result['usage'])
  result.update(status='complete',answer=answer);return answer
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_POST(self):
   try:
    if self.path!='/v1/chat/completions' or self.headers.get('Authorization')!='Bearer '+bridge_token:raise ValueError('Invalid private bridge request.')
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=96000:raise ValueError('Bridge request exceeds its bound.')
    received=self.rfile.read(length);request=json.loads(received);answer=inference(received);text=canonical(model.parse(answer)).decode();usage=answer.get('usage',{})
    item=dict(id='retained-data-only',object='chat.completion',created=int(time.time()),model=MODEL,choices=[dict(index=0,message=dict(role='assistant',content=text),finish_reason='stop')],usage=dict(prompt_tokens=usage.get('inputTokens'),completion_tokens=usage.get('outputTokens'),total_tokens=usage.get('totalTokens')))
    raw=canonical(item);kind='application/json'
    if request.get('stream'):
     chunks=[dict(id=item['id'],object='chat.completion.chunk',created=item['created'],model=MODEL,choices=[dict(index=0,delta=dict(role='assistant',content=text),finish_reason=None)]),dict(id=item['id'],object='chat.completion.chunk',created=item['created'],model=MODEL,choices=[dict(index=0,delta={},finish_reason='stop')],usage=item['usage'])]
     raw=b''.join(b'data: '+canonical(c)+b'\n\n' for c in chunks)+b'data: [DONE]\n\n';kind='text/event-stream'
    self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
   except Exception as error:
    result.update(status=getattr(error,'status','unknown'),error_code=getattr(error,'code','unconfirmed-'+type(error).__name__))
    if getattr(error,'usage',None):result['usage']=error.usage
    self.send_error(400,'Retained model operation failed; no provider retry.')
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 config=dict(models=dict(mode='replace',providers=dict(lab=dict(baseUrl='http://127.0.0.1:'+str(server.server_address[1])+'/v1',apiKey=bridge_token,api='openai-completions',models=[dict(id=MODEL,name='Subscription data-only adapter',reasoning=False,input=['text'],contextWindow=200000,maxTokens=4096,cost=dict(input=0,output=0,cacheRead=0,cacheWrite=0))]))),agents=dict(defaults=dict(model=dict(primary='lab/'+MODEL,fallbacks=[]),workspace=str(workspace),skipBootstrap=True,bootstrapMaxChars=30000,bootstrapTotalMaxChars=40000,compaction=dict(enabled=False))),tools=dict(deny=['*'],toolSearch=False,codeMode=False,elevated=dict(enabled=False)),plugins=dict(enabled=False),skills=dict(allowBundled=[]))
 config_path=directory/'config.json';config_path.write_bytes(canonical(config));env=environment(directory);env.update(OPENCLAW_CONFIG_PATH=str(config_path),OPENCLAW_STATE_DIR=str(state))
 began=time.monotonic()
 try:
  cli=subprocess.run([str(NODE),str(PACKAGE/'openclaw.mjs'),'agent','exec','--config',str(config_path),'--state-dir',str(state),'--cwd',str(workspace),'--message-file',str(message),'--model','lab/'+MODEL,'--code-mode','direct','--thinking','off','--timeout','90','--json'],capture_output=True,timeout=100,env=env)
  (directory/'stdout.bin').write_bytes(cli.stdout);(directory/'stderr.bin').write_bytes(cli.stderr)
  if cli.returncode or not result.get('answer'):raise ValueError('OpenClaw CLI did not complete its retained turn.')
  envelope=json.loads(cli.stdout);payloads=envelope.get('payloads') or envelope.get('result',{}).get('payloads') or [];texts=[p['text'] for p in payloads if p.get('text')]
  if len(texts)!=1 or json.loads(texts[0])!=model.parse(result['answer']):raise ValueError('OpenClaw final answer differs from its original provider decision.')
 except Exception as error:
  if result.get('status')=='complete':result['status']='invalid-output'
  result.setdefault('status','unknown');result.setdefault('error_code','runtime-'+type(error).__name__)
 finally:
  server.shutdown();server.server_close();thread.join(timeout=2)
 result.update(runtime=VERSION,node_version='24.16.0',openclaw_version='2026.10.5',model_id=MODEL,wall_seconds=time.monotonic()-began,subscription_allocation_usd=None,measured_charges_usd=None,estimated_cost_usd=None,catalog_cost_fields='Numeric zeros required by the adapter are non-billing placeholders.',turn_policy=model.TURN_POLICY['version'])
 answer=result.pop('answer',None)
 (directory/'receipt.json').write_bytes(canonical(result))
 return answer if result['status']=='complete' else None,result

def worker_loop():
 import model_dispatch as dispatch,store
 from workflow import now
 os.umask(0o077);verify_runtime();spool=Path(os.environ.get('OPENCLAW_SPOOL','/var/lib/jumpserve-openclaw/spool'));spool.mkdir(mode=0o700,parents=True,exist_ok=True)
 paused=False
 def process(job):
  nonlocal paused
  directory=spool/job['id']
  if directory.exists():raise ValueError('Original spool exists; reconcile retained request, never replay.')
  answer,receipt=execute(json.loads(job['request_raw']),directory)
  artifacts=[];captured={}
  for path in sorted(directory.iterdir()):
   if path.is_file() and path.name not in ('config.json',):
    raw=path.read_bytes();identity=str(uuid.uuid5(uuid.UUID(job['id']),'runtime:'+path.name))
    record=store.artifact(job['study_id'],job['actor_id'],raw,identity,'application/octet-stream',dict(runtime=VERSION,kind='model-runtime-original',dispatch_id=job['id'],filename=path.name,response_received=receipt.get('response_received'),transformation='Original captured bytes; not scientific measurements.'))
    store.append(job['study_id'],job['actor_id'],'artifacts',record);artifacts.append(dict(id=record['id'],name=path.name,sha256=record['sha256'],byte_count=record['byte_count']))
  response_id=None;response_sha=None
  if answer:
   raw=canonical(answer);response_id=str(uuid.uuid5(uuid.UUID(job['id']),'controller-response'));record=store.artifact(job['study_id'],job['actor_id'],raw,response_id,'application/json',dict(runtime=VERSION,kind='derived-controller-model-response',dispatch_id=job['id'],transformation='Completed SSE to typed decision; original SSE and native CLI output retained separately.'))
   store.append(job['study_id'],job['actor_id'],'artifacts',record);response_sha=record['sha256']
  provenance=dict(receipt,artifacts=artifacts)
  dispatch.rpc('finish',dict(id=job['id'],token=job['lease_token'],status=receipt['status'],response_artifact_id=response_id,response_sha256=response_sha,provenance=provenance))
  if receipt['status'] in ('paused-quota','paused-auth'):
   paused=True;dispatch.rpc('heartbeat',dict(status='paused-quota' if receipt['status']=='paused-quota' else 'auth-required',configuration=dict(runtime=VERSION,paid_fallback=False)))
 with ThreadPoolExecutor(max_workers=2) as pool:
  futures=[];last_health=0;ready=False
  while True:
   try:
    if time.monotonic()-last_health>5:
     try:credential();ready=not paused
     except Exception:ready=False
     dispatch.rpc('heartbeat',dict(status='ready' if ready else 'paused-quota' if paused else 'auth-required',configuration=dict(runtime=VERSION,model_id=MODEL,paid_fallback=False,actual_cost_usd=None)));last_health=time.monotonic()
    active=[]
    for future in futures:
     if future.done():
      try:future.result()
      except Exception as error:print(json.dumps(dict(runtime=VERSION,status='unconfirmed',error_type=type(error).__name__)),flush=True)
     else:active.append(future)
    futures=active
    if ready and len(futures)<2:
     job=dispatch.rpc('claim',{})
     if job:futures.append(pool.submit(process,job))
   except Exception as error:print(json.dumps(dict(runtime=VERSION,status='unavailable',error_type=type(error).__name__)),flush=True)
   time.sleep(1)

if __name__=='__main__':worker_loop()
