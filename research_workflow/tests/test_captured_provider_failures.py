"""Captured errors are accounted for; absent or ambiguous outcomes stay fenced."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import dispatch_recovery as recovery,siwc_protocol as wire,store,openclaw_runtime as runtime,experiment_builder as builder
from workflow import canonical,digest
from test_dispatch_recovery import Fixture
from test_controller_terminal_recovery import Fixture as ControllerFixture

def error_stream(code='server_is_overloaded'):
 return (b'data: '+canonical(dict(type='response.created',response=dict(model='gpt-6.1-sol',status='in_progress')))+b'\n\n'
  +b'data: '+canonical(dict(type='error',error=dict(code=code)))+b'\n\n' +b'data: {"type":"partially-captured-tail"')
def request():return canonical(dict(model='gpt-6.1-sol',store=False,stream=True,tools=[dict(type='function',name='read')]))
def failed_native(status=200):
 f=Fixture(1);c=f.calls[0];req=f.add(request());raw=error_stream() if status==200 else canonical(dict(error=dict(code='subscription_sharing_user_unavailable')));response=f.add(raw)
 c.update(number=1,status='failed',reason='server_is_overloaded' if status==200 else 'subscription_sharing_user_unavailable',request_artifact_id=req['artifact_id'],request_sha256=req['sha256'],response_artifact_id=response['artifact_id'],response_sha256=response['sha256'],http_status=status)
 f.d['status']='failed';return f
def failed_controller():
 f=ControllerFixture();f.d['status']='failed';f.receipt.update(status='failed',error_code='server_is_overloaded')
 f.replace('provider-response.bin',error_stream());f.replace('receipt.json',canonical(f.receipt));f.d['provenance']=dict(f.receipt,artifacts=f.refs)
 return f

class CapturedFailure(unittest.TestCase):
 def test_explicit_complete_error_frame_is_enough_when_bridge_stopped_before_partial_tail(self):
  raw=error_stream();p=recovery.captured_failure(request(),raw,200,'server_is_overloaded')
  self.assertEqual(p['evidence_kind'],'framed-stream-error');self.assertLess(p['inspected_prefix_bytes'],len(raw))
  self.assertFalse(p['response_forwarded_to_agent']);self.assertEqual(p['tool_calls_executed_from_response'],0)
  self.assertTrue(p['provider_processing_unknown']);self.assertIsNone(p['measured_charges_usd'])
  crlf=raw.replace(b'\n',b'\r\n');proof=recovery.captured_failure(request(),crlf,200,'server_is_overloaded')
  self.assertEqual(crlf[:proof['inspected_prefix_bytes']][-4:],b'\r\n\r\n')
  self.assertEqual(crlf[proof['inspected_prefix_bytes']:],b'data: {"type":"partially-captured-tail"')
 def test_incomplete_errors_successes_wrong_models_and_hosted_tools_cannot_be_reclassified(self):
  for defect in ('truncated-error','no-error','success-first','incomplete-first','malformed-first','wrong-code','wrong-model','hosted-tool','http-auth','http-200-json','previous-response','boolean-http','missing-tools'):
   with self.subTest(defect=defect):
    req=json.loads(request());raw=error_stream();code=200;reason='server_is_overloaded'
    if defect=='truncated-error':raw=raw[:raw.index(b'\n\n',raw.index(b'"error"'))+1]
    elif defect=='no-error':raw=b'data: {"type":"response.created"}\n\n'
    elif defect=='success-first':raw=b'data: {"type":"response.completed"}\n\n'+raw
    elif defect=='incomplete-first':raw=b'data: {"type":"response.incomplete"}\n\n'+raw
    elif defect=='malformed-first':raw=b'data: not-json\n\n'+raw
    elif defect=='wrong-code':reason='another-error'
    elif defect=='wrong-model':req['model']='different-model'
    elif defect=='hosted-tool':req['tools']=[dict(type='code_interpreter')]
    elif defect=='http-auth':code=401;raw=canonical(dict(error=dict(code=reason)))
    elif defect=='http-200-json':raw=canonical(dict(error=dict(code=reason)))
    elif defect=='previous-response':req['previous_response_id']='previous'
    elif defect=='boolean-http':code=True
    else:req.pop('tools')
    self.assertIsNone(recovery.captured_failure(canonical(req),raw,code,reason))
 def test_native_http_and_stream_failures_get_one_supplement_without_changing_originals(self):
  for http in (200,503):
   with self.subTest(http=http):
    f=failed_native(http);before=copy.deepcopy(f.calls)
    def retain(name,raw,kind):return f.add(raw,provenance=dict(kind=kind,dispatch_id=f.d['id']))
    def rpc(name,p):
     self.assertEqual(name,'research_builder_call_reconcile');f.call_proofs.append(dict(call_id=p['p_call'],artifact_id=p['p_artifact'],sha256=p['p_sha256'],proof=p['p_proof'],request_sha256=f.calls[0]['request_sha256']))
    with tempfile.TemporaryDirectory() as tmp,f.patches(),patch.object(store,'rpc',side_effect=rpc) as record,patch.object(runtime,'credential') as credentials,patch.object(runtime,'http_request') as provider:
     recovery.reconcile_native_calls(f.task,f.d,Path(tmp),retain);p=recovery.ledger(f.task,f.d,f.original,f.rows)
     recovery.reconcile_native_calls(f.task,f.d,Path(tmp),retain)
    self.assertEqual(record.call_count,1);self.assertEqual(p[0]['status'],'failed');self.assertEqual(f.calls,before)
    credentials.assert_not_called();provider.assert_not_called()
 def test_missing_response_wrong_hash_or_failure_receipt_never_clear_unknown(self):
  f=failed_native();c=f.calls[0];c.update(status='unknown',response_received=None,http_status=None,response_artifact_id=None,response_sha256=None)
  before=copy.deepcopy(c)
  with tempfile.TemporaryDirectory() as tmp,f.patches(),patch.object(store,'rpc') as rpc:
   recovery.reconcile_native_calls(f.task,f.d,Path(tmp),Mock());self.assertIsNone(recovery.ledger(f.task,f.d,f.original,f.rows))
  rpc.assert_not_called();self.assertEqual(c,before)
  f=failed_native();c=f.calls[0];f.blobs[c['response_artifact_id']]+=b'changed'
  with tempfile.TemporaryDirectory() as tmp,f.patches(),patch.object(store,'rpc') as rpc,self.assertRaises(ValueError):recovery.reconcile_native_calls(f.task,f.d,Path(tmp),Mock())
  rpc.assert_not_called()
 def test_controller_error_uses_the_exact_logical_request_and_original_receipt(self):
  f=failed_controller();before=copy.deepcopy(f.d)
  p=recovery.controller_failure(f.task,f.d,f.receipt,f.original,f.rows);self.assertEqual(p['status'],'failed')
  self.assertEqual(p['provider_calls'],1);self.assertTrue(p['failure']['provider_processing_unknown']);self.assertEqual(f.d,before)
  for defect in ('receipt','native-call','missing-original','request','wrong-status'):
   with self.subTest(defect=defect):
    f=failed_controller()
    if defect=='receipt':f.replace('receipt.json',canonical(dict(f.receipt,provider_calls=0)))
    elif defect=='native-call':f.calls=[dict(status='unknown')]
    elif defect=='missing-original':f.refs.pop()
    elif defect=='request':f.replace('provider-request.json',request())
    else:f.d['status']='unknown'
    try:p=recovery.controller_failure(f.task,f.d,f.receipt,f.original,f.rows)
    except ValueError:p=None
    self.assertIsNone(p)
 def test_reconciliation_worker_records_controller_failure_without_provider_or_cleanup(self):
  f=failed_controller();records=[]
  def rows(kind,q):
   if kind=='model_reconciliations':return f.proofs
   if kind=='autonomy_tasks':return [f.task]
   if kind=='study_owners':return [dict(owner_id=f.task['actor_id'])]
   raise AssertionError(kind)
  def artifact(study,owner,raw,ident,media,provenance):f.blobs[ident]=raw;return dict(id=ident,sha256=digest(raw),byte_count=len(raw))
  def rpc(name,p):
   if p.get('p_proof',{}).get('version')=='worker-runtime-diagnostics-v1':return {}
   self.assertEqual(name,'research_controller_dispatch_reconcile');records.append(p);f.proofs.append(dict(dispatch_id=f.d['id'],study_id=f.task['study_id'],task_id=f.task['id'],request_sha256=f.d['request_sha256'],artifact_id=p['p_artifact'],sha256=p['p_sha256'],proof=p['p_proof']))
  with tempfile.TemporaryDirectory() as tmp,patch.object(store,'rows',side_effect=rows),patch.object(__import__('autonomous_repair'),'rows',side_effect=f.rows),patch.object(__import__('autonomous_repair'),'original',side_effect=f.original),patch.object(store,'artifact',side_effect=artifact),patch.object(store,'append'),patch.object(store,'artifact_bytes',side_effect=lambda study,ident,**kw:f.blobs[ident]),patch.object(store,'rpc',side_effect=rpc),patch.object(runtime,'http_request') as provider,patch.object(builder,'container_action') as cleanup:
   self.assertEqual(recovery._reconcile_dispatches(tmp,[f.d]),[f.d['id']]);self.assertEqual(recovery._reconcile_dispatches(tmp,[f.d]),[])
   value=recovery.confirmed(f.task,f.d,f.receipt,f.original,f.rows)
  self.assertEqual(len(records),1);self.assertEqual(value['kind'],'hash-verified-captured-controller-failure');provider.assert_not_called();cleanup.assert_not_called()
 def test_worker_diagnosis_distinguishes_absence_and_links_and_deduplicates_unchanged_observation(self):
  f=failed_native();c=f.calls[0];c.update(status='unknown',number=79,response_artifact_id=None,response_sha256=None)
  captures=[]
  def retain(name,raw,kind):captures.append((name,raw,kind));return f.add(raw)
  with tempfile.TemporaryDirectory() as tmp,f.patches(),patch.object(store,'rpc') as record,patch.object(runtime,'http_request') as provider,patch.object(builder,'container_action') as cleanup:
   root=Path(tmp);recovery.runtime_diagnostic(f.task,f.d,root,retain)
   count=len(captures);recovery.runtime_diagnostic(f.task,f.d,root,retain);self.assertEqual(len(captures),count)
   proof=json.loads(captures[-1][1]);self.assertEqual(proof['calls'][0]['response']['status'],'absent');self.assertEqual(proof['calls'][0]['completion']['status'],'absent')
   directory=root/f.d['id'];directory.mkdir();(directory/'call-79-response.bin').symlink_to(root/'private-response')
   recovery.runtime_diagnostic(f.task,f.d,root,retain);proof=json.loads(captures[-1][1]);self.assertEqual(proof['calls'][0]['response']['status'],'unsafe-link')
  self.assertFalse(proof['reconciliation_performed']);self.assertFalse(proof['cleanup_performed']);self.assertEqual(proof['provider_calls'],0)
  self.assertTrue(all('artifact' in s or s.get('status') in ('absent','inaccessible') for s in proof['sources']));provider.assert_not_called();cleanup.assert_not_called()

if __name__=='__main__':unittest.main()
