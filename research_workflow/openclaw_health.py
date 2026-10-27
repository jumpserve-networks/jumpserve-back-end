"""Private worker availability. No credential values, provider/model retries or DB writes."""
import fcntl,json,math,os,tempfile,threading,time,uuid
from pathlib import Path

class WorkerAvailability:
 """Quota hold is explicit; a repaired grant must not leave an in-memory latch."""
 def __init__(self,auth,spool,token_source,clock=time.time,minimum_free_bytes=512*1024*1024):
  self.auth=Path(auth);self.spool=Path(spool);self.token_source=token_source;self.clock=clock;self.minimum_free_bytes=minimum_free_bytes;self.lock=threading.RLock();self.pending=[]
 def _read(self):
  path=self.auth/'worker-availability.json'
  try:
   stat=path.lstat()
   if not path.is_file() or path.is_symlink() or stat.st_uid!=os.getuid() or stat.st_nlink!=1:raise OSError('Private worker state is invalid')
   state=json.loads(path.read_bytes())
  except FileNotFoundError:return dict(version=1,quota=False,quota_event=0,force_refresh=None,not_before=0,reason='ready')
  if set(state)!={'version','quota','quota_event','force_refresh','not_before','reason'} or state['version']!=1 or type(state['quota']) is not bool or type(state['quota_event']) is not int or state['quota_event']<0 or state['force_refresh'] is not None and not isinstance(state['force_refresh'],str) or type(state['not_before']) not in (int,float) or not math.isfinite(state['not_before']) or state['reason'] not in ('ready','provider-access','provider-temporary'):raise OSError('Private worker state is invalid')
  os.chmod(path,0o600);return state
 def _write(self,state):
  descriptor,name=tempfile.mkstemp(prefix='.worker-health-',dir=self.auth)
  try:
   with os.fdopen(descriptor,'wb') as stream:stream.write(json.dumps(state).encode());stream.flush();os.fsync(stream.fileno())
   os.replace(name,self.auth/'worker-availability.json')
   directory=os.open(self.auth,os.O_RDONLY)
   try:os.fsync(directory)
   finally:os.close(directory)
  finally:
   try:os.unlink(name)
   except FileNotFoundError:pass
 def _edit(self,change=None):
  self.auth.mkdir(mode=0o700,parents=True,exist_ok=True);self.auth.chmod(0o700)
  with self.lock,(self.auth/'worker-health.lock').open('a') as lock:
   os.chmod(self.auth/'worker-health.lock',0o600);fcntl.flock(lock,fcntl.LOCK_EX)
   state=self._read()
   if change is not None:change(state);self._write(state)
   return state
 def observe(self,receipt):
  # Only fixed diagnostic classifications leave this process. The actual
  # rejected dispatch remains terminal; availability recovery never replays it.
  if receipt.get('status')=='paused-quota':
   observed=time.time_ns()
   def change(state):
    if observed>state['quota_event']:state.update(quota=True,quota_event=observed)
  elif receipt.get('status')=='paused-auth' and receipt.get('credential_status')!='auth-required':
   change=lambda state:state.update(force_refresh=str(uuid.uuid4()),not_before=self.clock()+30,reason='provider-access')
  elif receipt.get('http_status') in (401,403):change=lambda state:state.update(not_before=self.clock()+300,reason='provider-access')
  elif receipt.get('http_status') in (429,500,502,503,504) or receipt.get('error_code') in ('subscription_sharing_usage_unavailable','subscription_sharing_user_unavailable'):
   change=lambda state:state.update(not_before=self.clock()+30,reason='provider-temporary')
  else:return
  try:self._edit(change)
  except (OSError,ValueError):
   # Do not lose an observed pause if its durable write temporarily fails.
   with self.lock:self.pending.append(change)
 def resume_quota(self):
  self._edit(lambda state:state.update(quota=False,quota_event=time.time_ns()));return dict(status='quota-hold-cleared',provider_allowance_verified=False)
 def check(self):
  try:
   with self.lock:
    while self.pending:self._edit(self.pending[0]);self.pending.pop(0)
   state=self._edit();forced=state['force_refresh']
   self.token_source(force_refresh=forced is not None)
   if forced is not None:
    def clear(current):
     if current['force_refresh']==forced:current['force_refresh']=None
    state=self._edit(clear)
   space=os.statvfs(self.spool)
   if space.f_bavail*space.f_frsize<self.minimum_free_bytes:return dict(status='unavailable',reason='storage-low',retry_after_seconds=10)
   if state['quota']:return dict(status='paused-quota',reason='quota-paused',retry_after_seconds=None)
   if state['not_before']>self.clock():return dict(status='unavailable',reason=state['reason'],retry_after_seconds=math.ceil(state['not_before']-self.clock()))
   return dict(status='ready',reason='ready',retry_after_seconds=0)
  except Exception as error:
   status=getattr(error,'status','unavailable');reason=getattr(error,'reason','storage-unavailable');delay=getattr(error,'retry_after_seconds',10)
   return dict(status=status if status in ('unavailable','auth-required') else 'unavailable',reason=reason,retry_after_seconds=delay)
