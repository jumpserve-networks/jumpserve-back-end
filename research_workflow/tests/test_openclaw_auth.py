"""Local auth/storage regressions. Synthetic grants, no cloud or provider calls."""
import json,os,sqlite3,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import openclaw_runtime as runtime,siwc_protocol as wire
from openclaw_health import WorkerAvailability
ROOT=Path(__file__).resolve().parents[2]
NODE=ROOT/'experiments/openclaw_pilot/runtime/node_modules/node/bin/node'

class NativeRecovery(unittest.TestCase):
 def test_native_sqlite_rotation_and_crash_recovery(self):
  if not NODE.exists():self.skipTest('Pinned local Node required for native auth cases; no real credentials used.')
  result=subprocess.run([str(NODE),'--test',str(Path(__file__).with_name('openclaw_auth_test.mjs'))],capture_output=True,text=True,timeout=30)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('pass 17',result.stdout)
 def test_two_processes_refresh_one_rotating_grant_once(self):
  if not NODE.exists():self.skipTest('Pinned local Node required for actual multiprocess lock check.')
  with tempfile.TemporaryDirectory() as tmp:
   auth=Path(tmp)/'auth';(auth/'state').mkdir(parents=True);package=Path(tmp)/'package';module=package/'dist/extensions/openai';module.mkdir(parents=True);(package/'package.json').write_text('{"type":"module"}')
   counter=Path(tmp)/'refresh-calls'
   (module/'token-sharing-oauth.runtime.js').write_text("import {appendFileSync} from 'node:fs';export async function refreshTokenSharingCredential(c){appendFileSync("+json.dumps(str(counter))+",'refresh\\n');await new Promise(r=>setTimeout(r,100));return {...c,access:'synthetic-access-next',refresh:'synthetic-refresh-next',expires:Date.now()+3600000};}")
   grant=dict(type='oauth',provider='openai',authFlow='chatgpt-token-sharing',issuer='https://auth.openai.com',accountId='synthetic-account',clientId='synthetic-client',access='synthetic-access-old',refresh='synthetic-refresh-old',expires=time.time()*1000-1,grantedScope='resource.invoke chatgpt.tokens.use.direct')
   with sqlite3.connect(auth/'state/openclaw.sqlite') as db:db.execute('create table config_machine_state(state_key text primary key,value_json text)');db.execute('insert into config_machine_state values(?,?)',('authProfiles.store',json.dumps(dict(profiles=dict(main=grant)))))
   code="import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import openclaw_runtime as r;r.AUTH=Path(sys.argv[2]);r.NODE=Path(sys.argv[3]);r.PACKAGE=Path(sys.argv[4]);assert r.credential()=='synthetic-access-next';print('OK')"
   command=[sys.executable,'-B','-c',code,str(ROOT/'research_workflow'),str(auth),str(NODE),str(package)]
   children=[subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
   for child in children:
    stdout,stderr=child.communicate(timeout=20);self.assertEqual(child.returncode,0,stderr.decode());self.assertEqual(stdout.strip(),b'OK');self.assertNotIn(b'synthetic-access',stderr)
   self.assertEqual(counter.read_text().splitlines(),['refresh'])
 def test_pre_inference_credential_failures_are_retained_and_never_call_provider(self):
  import test_openclaw as fixture
  for status,reason in [('auth-required','grant-invalid'),('unavailable','storage-unavailable'),('unavailable','refresh-backoff')]:
   with self.subTest(status=status,reason=reason),tempfile.TemporaryDirectory() as tmp:
    source=Mock(side_effect=runtime.CredentialFailure(reason,status,10));transport=Mock()
    answer,receipt=runtime.execute(fixture.request(),Path(tmp)/'turn',token_source=source,transport=transport)
    self.assertIsNone(answer);transport.assert_not_called();self.assertEqual(receipt['provider_calls'],0);self.assertFalse(receipt['provider_request_sent']);self.assertIsNone(receipt['usage']);self.assertEqual(receipt['credential_status'],status);self.assertFalse(receipt['paid_fallback']);self.assertTrue((Path(tmp)/'turn/logical.json').exists())
 def test_helper_output_and_local_errors_never_turn_into_credentials_or_spurious_sign_in(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'AUTH',Path(tmp)):
   for output,expected in [(b'{"status":"unavailable","reason":"refresh-temporary","retry_after_seconds":10}','refresh-temporary'),(b'{"status":"auth-required","reason":"synthetic-secret","retry_after_seconds":0}','refresh-unconfirmed'),(b'synthetic-secret','refresh-unconfirmed')]:
    with self.subTest(output=output),patch.object(runtime.subprocess,'run',return_value=SimpleNamespace(stdout=output,returncode=1)):
     with self.assertRaises(runtime.CredentialFailure) as failure:runtime.credential()
     self.assertEqual(failure.exception.reason,expected);self.assertEqual(failure.exception.status,'unavailable');self.assertNotIn('synthetic-secret',str(failure.exception))
   with patch.object(runtime.subprocess,'run',side_effect=subprocess.TimeoutExpired('private-helper',75)):
    with self.assertRaises(runtime.CredentialFailure) as failure:runtime.credential()
    self.assertEqual(failure.exception.reason,'refresh-unconfirmed')
 def test_inference_permissions_and_transient_availability_are_not_refresh_revocation(self):
  self.assertEqual(wire.failure('subscription_sharing_usage_limit_exceeded',429).status,'paused-quota')
  self.assertEqual(wire.failure('subscription_sharing_usage_unavailable',503).status,'failed')
  self.assertEqual(wire.failure('subscription_sharing_user_unavailable',503).status,'failed')
  self.assertEqual(wire.failure('chatpass_v2_scope_not_authorized',403).status,'failed')
  self.assertEqual(wire.failure('subscription_sharing_user_not_eligible',403).status,'failed')
  self.assertEqual(wire.failure('subscription_sharing_invalid_user',401).status,'paused-auth')

class Availability(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.auth=Path(self.tmp.name)/'auth';self.spool=Path(self.tmp.name)/'spool';self.spool.mkdir();self.now=1000;self.source=Mock(return_value='synthetic-token');self.gate=WorkerAvailability(self.auth,self.spool,self.source,clock=lambda:self.now)
 def test_repaired_authorization_resumes_without_restart(self):
  self.source.side_effect=runtime.CredentialFailure('grant-invalid','auth-required')
  self.assertEqual(self.gate.check()['status'],'auth-required')
  self.source.side_effect=None;self.assertEqual(self.gate.check()['status'],'ready')
 def test_temporary_network_storage_and_client_failures_do_not_request_sign_in(self):
  for reason in ('refresh-temporary','storage-unavailable','storage-low','client-configuration','refresh-unconfirmed'):
   with self.subTest(reason=reason):
    self.source.side_effect=runtime.CredentialFailure(reason);health=self.gate.check();self.assertEqual(health['status'],'unavailable');self.assertEqual(health['reason'],reason)
  self.source.side_effect=None;self.assertEqual(self.gate.check()['status'],'ready')
 def test_quota_pause_survives_restart_and_reauthorization_until_explicit_resume(self):
  self.gate.observe(dict(status='paused-quota',error_code='subscription_sharing_usage_limit_exceeded'))
  restarted=WorkerAvailability(self.auth,self.spool,self.source,clock=lambda:self.now)
  self.assertEqual(restarted.check()['status'],'paused-quota')
  self.source.return_value='different-new-grant';self.assertEqual(restarted.check()['status'],'paused-quota')
  self.assertFalse(self.gate.resume_quota()['provider_allowance_verified']);self.assertEqual(restarted.check()['status'],'ready')
  self.assertEqual((self.auth/'worker-availability.json').stat().st_mode&0o777,0o600)
 def test_model_rejection_forces_one_refresh_without_replaying_the_dispatch(self):
  self.gate.observe(dict(status='paused-auth',http_status=401));health=self.gate.check();self.assertEqual(health['status'],'unavailable');self.assertEqual(health['reason'],'provider-access');self.source.assert_called_with(force_refresh=True)
  self.now+=31;self.assertEqual(self.gate.check()['status'],'ready');self.source.assert_called_with(force_refresh=False)
 def test_genuine_terminal_grant_needs_no_automatic_forced_refresh(self):
  self.gate.observe(dict(status='paused-auth',credential_status='auth-required',error_code='grant-invalid'))
  self.source.side_effect=runtime.CredentialFailure('grant-invalid','auth-required');self.assertEqual(self.gate.check()['status'],'auth-required');self.source.assert_called_with(force_refresh=False)
 def test_concurrent_rejection_during_refresh_is_not_lost(self):
  self.gate.observe(dict(status='paused-auth',http_status=401))
  self.source.side_effect=lambda **_:self.gate.observe(dict(status='paused-auth',http_status=401)) or 'synthetic-token'
  self.gate.check();self.source.side_effect=None;self.gate.check();self.source.assert_called_with(force_refresh=True)
 def test_transient_provider_failure_recovers_after_our_backoff_not_a_claimed_plan_reset(self):
  self.gate.observe(dict(status='failed',http_status=503));health=self.gate.check();self.assertEqual(health['reason'],'provider-temporary');self.assertEqual(health['retry_after_seconds'],30)
  self.now+=31;self.assertEqual(self.gate.check()['status'],'ready')
 def test_low_spool_space_stops_admission_without_deleting_evidence(self):
  evidence=self.spool/'original';evidence.write_bytes(b'keep bytes')
  with patch('openclaw_health.os.statvfs',return_value=SimpleNamespace(f_bavail=0,f_frsize=4096)):health=self.gate.check()
  self.assertEqual(health['status'],'unavailable');self.assertEqual(health['reason'],'storage-low');self.assertEqual(evidence.read_bytes(),b'keep bytes')
 def test_failed_pause_write_stays_paused_until_recovered(self):
  with patch.object(self.gate,'_write',side_effect=OSError('disk full')):
   self.gate.observe(dict(status='paused-quota'));self.assertEqual(self.gate.check()['status'],'unavailable')
  self.assertEqual(self.gate.check()['status'],'paused-quota')
 def test_later_explicit_quota_resume_is_not_overwritten_by_an_older_pending_write(self):
  with patch.object(self.gate,'_write',side_effect=OSError('disk full')):self.gate.observe(dict(status='paused-quota'))
  self.gate.resume_quota();self.assertEqual(self.gate.check()['status'],'ready')
 def test_corrupt_state_fails_closed_without_reading_a_token(self):
  self.auth.mkdir();(self.auth/'worker-availability.json').write_text('broken')
  self.assertEqual(self.gate.check()['status'],'unavailable');self.source.assert_not_called()

if __name__=='__main__':unittest.main()
