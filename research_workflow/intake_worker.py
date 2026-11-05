"""Dedicated warm OpenClaw intake. No refresh grant, shell tools or public ingress."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from functools import lru_cache
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace
import urllib.request
import uuid
import intake_parallel
import openclaw_runtime as runtime
from openclaw_health import WorkerAvailability
from dispatch_recovery import write_once
from workflow import canonical,digest

PARAMETER='/jumpserve/intake-access-v1'
VERSION=intake_parallel.VERSION
PORT=18891

@lru_cache(maxsize=1)
def ssm():
 import boto3
 session=boto3.Session(region_name='us-east-1')
 if session.get_credentials().method!='iam-role':raise ValueError('Dedicated intake requires an instance role.')
 return session.client('ssm')

def access(client=None,now=None):
 value=json.loads((client or ssm()).get_parameter(Name=PARAMETER,WithDecryption=True)['Parameter']['Value'])
 now=time.time() if now is None else now
 if (value.get('version')!=VERSION or value.get('status')!='ready'
  or value.get('issuer')!='https://auth.openai.com' or not isinstance(value.get('access'),str)
  or not value['access'] or value['access'].startswith('sk-') or len(value['access'])>3500
  or type(value.get('checked_at')) not in (float,int) or not 0<=now-value['checked_at']<=90
  or type(value.get('expires_at')) not in (float,int) or value['expires_at']<=now+30):
  raise runtime.CredentialFailure('intake-access-cache-unavailable')
 return value['access']

def publish_access(client=None):
 """Runs ONLY on the existing refresh owner. Never copies a refresh token."""
 client=client or ssm();now=time.time()
 try:
  captured=[]
  def owner_access(**kwargs):
   token=runtime.credential(**kwargs);captured.append(token);return token
  health=WorkerAvailability(runtime.AUTH,runtime.AUTH,owner_access).check()
  if health['status']!='ready':raise runtime.CredentialFailure('Refresh owner is paused.')
  token=captured[-1]
  path=runtime.AUTH/'state/openclaw.sqlite'
  with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
   row=db.execute("select value_json from config_machine_state where state_key='authProfiles.store'").fetchone()
  profiles=list(json.loads(row[0])['profiles'].values())
  if len(profiles)!=1 or profiles[0].get('access')!=token:raise ValueError('Credential rotated during publication.')
  value=dict(version=VERSION,status='ready',issuer='https://auth.openai.com',access=token,
   expires_at=profiles[0]['expires']/1000,checked_at=now)
  if len(canonical(value))>4096:raise ValueError('Access cache exceeds Standard SecureString size.')
 except Exception:
  value=dict(version=VERSION,status='unavailable',checked_at=now)
 client.put_parameter(Name=PARAMETER,Value=canonical(value).decode(),Type='SecureString',Overwrite=True,Tier='Standard')
 return dict(status=value['status'])

def configuration(home,bridge,bridge_token,gateway_token):
 return dict(gateway=dict(mode='local',bind='loopback',port=PORT,auth=dict(mode='token',token=gateway_token),
  http=dict(endpoints=dict(chatCompletions=dict(enabled=True)))),
  models=dict(mode='replace',providers=dict(lab=dict(baseUrl=bridge+'/v1',apiKey=bridge_token,api='openai-completions',
   models=[dict(id=runtime.MODEL,name='Retained data-only intake',reasoning=False,input=['text'],contextWindow=200000,maxTokens=4096,cost=dict(input=0,output=0,cacheRead=0,cacheWrite=0))]))),
  agents=dict(defaults=dict(model=dict(primary='lab/'+runtime.MODEL,fallbacks=[]),workspace=str(home/'workspace'),
   maxConcurrent=2,skipBootstrap=True,compaction=dict(enabled=False),heartbeat=dict(every='0m'))),
  tools=dict(deny=['*'],toolSearch=False,codeMode=False,elevated=dict(enabled=False)),
  plugins=dict(enabled=False,slots=dict(memory='none')),browser=dict(enabled=False),skills=dict(allowBundled=[]),
  commands=dict(native=False,nativeSkills=False,text=False,config=False,restart=False,bash=False))

class WarmGateway:
 def __init__(self,home):
  self.home=Path(home);self.home.mkdir(mode=0o700,parents=True,exist_ok=True)
  (self.home/'workspace').mkdir(mode=0o700,exist_ok=True)
  self.routes={};self.lock=threading.Lock();self.bridge_token=secrets.token_urlsafe(32);self.token=secrets.token_urlsafe(32)
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),runtime.NoRedirect())
  gateway=self
  class Proxy(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    try:
     if self.path!='/v1/chat/completions' or self.headers.get('Authorization')!='Bearer '+gateway.bridge_token:raise ValueError('Private bridge required.')
     size=int(self.headers.get('Content-Length','0'))
     if not 0<size<=1048576:raise ValueError('Bounded native request required.')
     raw=self.rfile.read(size)
     if len(raw)!=size:raise ValueError('Incomplete native request.')
     tickets=set(re.findall(rb'warm-intake-ticket:([a-f0-9-]{36})',raw))
     if len(tickets)!=1:raise ValueError('One exact intake ticket required.')
     with gateway.lock:route=gateway.routes.get(next(iter(tickets)).decode())
     if route is None:raise ValueError('Original intake ticket is unavailable.')
     target,token=route
     request=urllib.request.Request(target+'/chat/completions',data=raw,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
     # The original per-turn bridge validates model, roles, hash and one-call
     # identity, and retains exact provider bytes. This proxy never infers.
     with gateway.opener.open(request,timeout=runtime.DATA_PROCESS_SECONDS) as response:
      body=response.read(150001);kind=response.headers.get('Content-Type','application/json')
     if len(body)>150000:raise ValueError('Native result exceeds its bound.')
     self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    except Exception:self.send_error(409,'Original intake operation unconfirmed; no provider replay.')
  self.bridge=ThreadingHTTPServer(('127.0.0.1',0),Proxy);self.bridge.daemon_threads=True
  self.thread=threading.Thread(target=self.bridge.serve_forever,daemon=True);self.thread.start()
  self.env=dict(runtime.environment(self.home),OPENCLAW_CONFIG_PATH=str(self.home/'openclaw.json'))
  if not (self.home/'openclaw.json').exists():(self.home/'openclaw.json').write_bytes(b'{}')
  config=configuration(self.home,'http://127.0.0.1:'+str(self.bridge.server_address[1]),self.bridge_token,self.token)
  result=subprocess.run([str(runtime.NODE),str(runtime.PACKAGE/'openclaw.mjs'),'config','patch','--stdin','--replace-path','plugins'],input=canonical(config),capture_output=True,env=self.env,timeout=120)
  (self.home/'configuration-diagnostics.bin').write_bytes(result.stdout+result.stderr)
  if result.returncode:raise ValueError('Pinned native intake configuration failed.')
  observed=json.loads((self.home/'openclaw.json').read_bytes())
  for key in ('gateway','tools','models','plugins'):
   if observed.get(key)!=config[key]:raise ValueError('Native intake configuration readback differs.')
  self.log=(self.home/'gateway.log').open('ab')
  self.process=subprocess.Popen([str(runtime.NODE),str(runtime.PACKAGE/'openclaw.mjs'),'gateway','run','--port',str(PORT),'--bind','loopback'],env=self.env,stdout=self.log,stderr=self.log)
  deadline=time.monotonic()+150
  while time.monotonic()<deadline:
   if self.process.poll() is not None:raise ValueError('Warm intake Gateway exited during startup.')
   try:
    request=urllib.request.Request('http://127.0.0.1:'+str(PORT)+'/v1/models',headers={'Authorization':'Bearer '+self.token})
    with self.opener.open(request,timeout=2) as response:
     if response.status==200:self.verified=True;return
   except Exception:time.sleep(1)
  raise ValueError('Warm intake Gateway did not become ready.')

 def run(self,args,**kwargs):
  config=json.loads(Path(args[args.index('--config')+1]).read_bytes())
  source=config['models']['providers']['lab'];message=Path(args[args.index('--message-file')+1]);ticket=str(uuid.uuid4())
  if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]+/v1',source['baseUrl']):raise ValueError('Local original bridge required.')
  with self.lock:self.routes[ticket]=(source['baseUrl'],source['apiKey'])
  request=dict(model='openclaw',user='intake-'+ticket,stream=False,
   messages=[dict(role='user',content=message.read_text()+'\nwarm-intake-ticket:'+ticket)])
  path=message.parent/'warm-gateway-request.json';write_once(path,canonical(request))
  try:
   call=urllib.request.Request('http://127.0.0.1:'+str(PORT)+'/v1/chat/completions',data=canonical(request),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
   with self.opener.open(call,timeout=runtime.DATA_PROCESS_SECONDS) as response:raw=response.read(150001)
   write_once(message.parent/'warm-gateway-response.bin',raw)
   if len(raw)>150000:raise ValueError('Warm result exceeds original native bound.')
   text=json.loads(raw)['choices'][0]['message']['content']
   return SimpleNamespace(returncode=0,stdout=canonical(dict(payloads=[dict(text=text)])),stderr=b'')
  finally:
   with self.lock:self.routes.pop(ticket,None)

 def close(self):
  self.process.terminate()
  try:self.process.wait(timeout=90)
  except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=10)
  self.bridge.shutdown();self.bridge.server_close();self.thread.join(timeout=2);self.log.close()

def worker_loop():
 import store
 runtime.verify_runtime();os.umask(0o077)
 manifest=json.loads(Path(__file__).with_name('intake-manifest.json').read_bytes())
 if manifest.get('version')!=VERSION or digest(canonical(manifest['files']))!=manifest['source_binding']:
  raise ValueError('Qualified intake source binding differs.')
 for name,sha in manifest['files'].items():
  path=Path(__file__).parent/name
  if '..' in Path(name).parts or Path(name).is_absolute() or path.is_symlink() or digest(path.read_bytes())!=sha:
   raise ValueError('Installed intake runtime source differs.')
 home=Path(os.environ.get('INTAKE_HOME','/var/lib/jumpserve-intake'));spool=home/'spool';spool.mkdir(mode=0o700,parents=True,exist_ok=True)
 gateway=WarmGateway(home);worker=str(uuid.uuid4())
 availability=WorkerAvailability(home/'availability',spool,lambda force_refresh=False:access())
 def dispatch(operation,payload=None):return store.rpc('research_intake_dispatch',dict(p_operation=operation,p_payload=payload or {}))
 def process(job):
  directory=spool/job['id']
  if directory.exists():raise ValueError('Original intake spool exists; no replay.')
  logical=json.loads(job['request_raw'])
  if logical.get('builder'):raise ValueError('Intake cannot execute a builder.')
  answer,receipt=runtime.execute(logical,directory,token_source=access,native_runner=gateway.run)
  availability.observe(receipt)
  receipt['intake_runtime']=dict(version=VERSION,warm_gateway=True,worker_id=worker)
  marker=runtime.save_completion(job,directory,answer,receipt,spool/'pending-completions')
  if runtime.persist_completed(job,directory):marker.unlink()
 try:
  with ThreadPoolExecutor(max_workers=2) as inference,ThreadPoolExecutor(max_workers=1) as controller,ThreadPoolExecutor(max_workers=1) as reconciliation:
   calls=[];control=None;recovering=None;heartbeat=0;recovery=0;next_control=0;next_claim=0;claim_idle=1;idle=1;status='unavailable'
   while True:
    if gateway.process.poll() is not None:raise ValueError('Original warm Gateway stopped; retain all running calls.')
    if time.monotonic()-heartbeat>=10:
     status=availability.check()['status']
     if status=='auth-required':status='paused-auth'
     try:dispatch('heartbeat',dict(version=VERSION,worker_id=worker,status=status,
      configuration=dict(native_version='2026.10.5',gateway_verified=True,warm=True,refresh_owner=False,source_binding=manifest['source_binding'])))
     except Exception:status='unavailable'
     heartbeat=time.monotonic()
    if time.monotonic()-recovery>=15 and (recovering is None or recovering.done()):
     if recovering is not None:
      try:recovering.result()
      except Exception:pass # The durable outbox is retried, never inference.
     recovering=reconciliation.submit(runtime.retry_completions,spool);recovery=time.monotonic()
    pending=[]
    for future in calls:
     if future.done():
      try:future.result()
      except Exception as error:print(json.dumps(dict(status='intake-operation-unconfirmed',error_type=type(error).__name__)),flush=True)
     else:pending.append(future)
    calls=pending
    if control is not None and control.done():
     try:outcomes=control.result();idle=min(15,idle*2) if all(x.get('status')=='idle' for x in outcomes) else 1
     except Exception:idle=min(15,idle*2)
     control=None;next_control=time.monotonic()+idle
    if status=='ready':
     if len(calls)<2 and time.monotonic()>=next_claim:
      try:job=dispatch('claim')
      except Exception:job=None;status='unavailable'
      if job:calls.append(inference.submit(process,job));claim_idle=1
      else:claim_idle=min(10,claim_idle*2)
      next_claim=time.monotonic()+claim_idle
     if status=='ready' and control is None and time.monotonic()>=next_control:
      control=controller.submit(intake_parallel.controller_slots)
    time.sleep(1)
 finally:gateway.close()

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--publish-access',action='store_true');args=parser.parse_args()
 if args.publish_access:
  while True:
   try:print(json.dumps(publish_access()),flush=True)
   except Exception as error:print(json.dumps(dict(status='access-publication-unconfirmed',error_type=type(error).__name__)),flush=True)
   time.sleep(30)
 else:worker_loop()

if __name__=='__main__':main()
