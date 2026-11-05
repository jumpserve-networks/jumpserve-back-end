"""Warm native transport uses the existing per-turn one-provider-call boundary."""
import json,sys,tempfile,threading,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import intake_worker as worker,openclaw_runtime as runtime
from workflow import canonical
from test_openclaw import request,Response,stream

class IntakeWorker(unittest.TestCase):
 def test_access_cache_expires_and_never_accepts_a_paid_api_key_or_wrong_issuer(self):
  good=dict(version=worker.VERSION,status='ready',access='synthetic-access',issuer='https://auth.openai.com',checked_at=1000,expires_at=2000)
  def client(value):return SimpleNamespace(get_parameter=lambda **kw:dict(Parameter=dict(Value=json.dumps(value))))
  self.assertEqual(worker.access(client(good),now=1030),'synthetic-access')
  for change in (dict(checked_at=0),dict(checked_at=1100),dict(expires_at=1040),dict(access='sk-synthetic'),dict(issuer='other'),dict(status='unavailable')):
   with self.assertRaises(runtime.CredentialFailure):worker.access(client(dict(good,**change)),now=1030)
 def test_publisher_never_serializes_refresh_grant_and_invalidates_on_auth_failure(self):
  writes=[];client=SimpleNamespace(put_parameter=lambda **kw:writes.append(kw))
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'state').mkdir()
   import sqlite3
   with sqlite3.connect(root/'state/openclaw.sqlite') as db:
    db.execute('create table config_machine_state(state_key text,value_json text)')
    db.execute('insert into config_machine_state values(?,?)',('authProfiles.store',json.dumps(dict(profiles=dict(owner=dict(access='synthetic',refresh='NEVER-PUBLISH',expires=9000000000000))))))
   with patch.object(runtime,'AUTH',root),patch.object(runtime,'credential',return_value='synthetic'):self.assertEqual(worker.publish_access(client)['status'],'ready')
  self.assertEqual(writes[0]['Type'],'SecureString');self.assertNotIn('NEVER-PUBLISH',writes[0]['Value']);self.assertNotIn('refresh',writes[0]['Value'])
  with patch.object(runtime,'credential',side_effect=ValueError):worker.publish_access(client)
  self.assertNotIn('access',json.loads(writes[-1]['Value']))
 def test_configuration_has_loopback_only_no_tools_and_concurrency_two(self):
  value=worker.configuration(Path('/synthetic'),'http://127.0.0.1:1234','synthetic-bridge','synthetic-gateway')
  self.assertEqual(value['gateway']['bind'],'loopback');self.assertEqual(value['tools']['deny'],['*'])
  self.assertEqual(value['agents']['defaults']['maxConcurrent'],2);self.assertFalse(value['plugins']['enabled'])
 def test_persistent_native_runner_still_retains_exact_provider_bytes_and_one_inference(self):
  calls=[]
  def upstream(*args):calls.append(args[3]);return Response(stream())
  # Emulate only the native Gateway endpoint. The original Python loopback
  # bridge, wire validator, original response capture and equality check run.
  def warm_runner(args,**kwargs):
   import urllib.request
   config=json.loads(Path(args[args.index('--config')+1]).read_bytes());settings=config['models']['providers']['lab']
   message=Path(args[args.index('--message-file')+1]).read_text()
   req=urllib.request.Request(settings['baseUrl']+'/chat/completions',data=canonical(dict(model=runtime.MODEL,messages=[dict(role='user',content=message)],stream=False)),headers={'Authorization':'Bearer '+settings['apiKey'],'Content-Type':'application/json'})
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as response:text=json.loads(response.read())['choices'][0]['message']['content']
   return SimpleNamespace(returncode=0,stdout=canonical(dict(payloads=[dict(text=text)])),stderr=b'')
  with tempfile.TemporaryDirectory() as directory:
   answer,receipt=runtime.execute(request(),Path(directory)/'turn',token_source=lambda:'synthetic',transport=upstream,native_runner=warm_runner)
   self.assertEqual(receipt['status'],'complete');self.assertTrue(answer);self.assertEqual(len(calls),1)
   self.assertEqual((Path(directory)/'turn/provider-request.json').read_bytes(),calls[0])

if __name__=='__main__':unittest.main()
