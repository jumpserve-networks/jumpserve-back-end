"""Actual rejected terminal decisions, immutable proofs and no provider replay."""
import copy,io,json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import autonomous_repair as repair,dispatch_recovery as recovery,store,siwc_protocol as wire,openclaw_runtime as runtime,experiment_builder as builder
from workflow import canonical,digest
from test_autonomy import answer,task

class Fixture:
 def __init__(self):
  self.task=dict(task(),status='waiting');self.d=dict(id=str(uuid.uuid4()),task_id=self.task['id'],study_id=self.task['study_id'],status='invalid-output',runtime='openclaw-production-v1',created_at='2026-11-01T00:00:00Z',ended_at='2026-11-01T00:01:00Z')
  self.blobs={};self.proofs=[];self.calls=[];self.record=None
  self.logical=dict(modelId='gpt-6.1-sol',system=[dict(text='Synthetic frozen policy')],messages=[dict(role='user',content=[dict(text='Synthetic context')])],toolConfig=dict(tools=[dict(toolSpec=dict(inputSchema=dict(json={}))) ]))
  logical=canonical(self.logical);self.d.update(request_raw=logical.decode(),request_sha256=digest(logical))
  decision=answer('inspect');decision['read_request']=dict(kind='runtime-contract',id='invented-runner-name',cursor=0)
  self.terminal=dict(id='response-original',model='gpt-6.1-sol',status='completed',output=[dict(id='message-original',type='message',role='assistant',status='completed',content=[dict(type='output_text',text=canonical(decision).decode())])])
  self.receipt=dict(runtime='openclaw-production-v1',model_id='gpt-6.1-sol',status='invalid-output',error_code='invalid-structured-decision',provider_calls=1,provider_request_sent=True,response_received=True,http_status=200,provider_retries=0,paid_fallback=False)
  self.refs=[self.add('logical.json',logical),self.add('provider-request.json',canonical(wire.response_request(self.logical))),self.add('provider-response.bin',self.stream()),self.add('receipt.json',canonical(self.receipt))]
  self.d['provenance']=dict(self.receipt,artifacts=self.refs)
 def stream(self):return b'data: '+canonical(dict(type='response.completed',response=self.terminal))+b'\n\n'
 def add(self,name,raw):
  ident=str(uuid.uuid4());self.blobs[ident]=raw
  return dict(id=ident,name=name,sha256=digest(raw),byte_count=len(raw))
 def replace(self,name,raw):
  ref=next(r for r in self.refs if r['name']==name);self.blobs[ref['id']]=raw;ref.update(sha256=digest(raw),byte_count=len(raw))
 def original(self,t,p,maximum=150000):
  if t['study_id']!=self.task['study_id']:raise ValueError('Study differs')
  raw=self.blobs[p.get('id') or p.get('artifact_id')]
  if len(raw)>maximum or digest(raw)!=p['sha256']:raise ValueError('Original bytes/hash differ')
  return raw
 def rows(self,t,kind,**q):
  if kind=='builder_calls':return self.calls
  if kind=='model_reconciliations':return self.proofs
  if kind=='model_dispatches':return [self.d]
  if kind=='autonomy_repairs':return [self.record]
  return []
 def proof(self):return recovery.controller_terminal(self.task,self.d,self.receipt,self.original,self.rows)
 def retain(self):
  proof=self.proof();p=self.add('proof.json',canonical(proof));self.proofs=[dict(dispatch_id=self.d['id'],study_id=self.task['study_id'],task_id=self.task['id'],request_sha256=self.d['request_sha256'],artifact_id=p['id'],sha256=p['sha256'],proof=proof)];return proof

class ControllerTerminal(unittest.TestCase):
 def test_rejected_terminal_is_known_without_reusing_or_replaying_its_decision(self):
  f=Fixture();before=copy.deepcopy((f.task,f.d,f.blobs))
  with patch.object(runtime,'credential') as credentials,patch.object(runtime,'http_request') as transport,patch.object(builder,'container_action') as cleanup:
   proof=f.retain();result=recovery.confirmed(f.task,f.d,f.receipt,f.original,f.rows)
  self.assertEqual(proof['provider_calls'],1);self.assertFalse(proof['decision_accepted']);self.assertEqual(result['kind'],'hash-verified-terminal-controller-failure')
  self.assertEqual((f.task,f.d),before[:2]);credentials.assert_not_called();transport.assert_not_called();cleanup.assert_not_called()
 def test_receipt_alone_and_a_truncated_or_conflicting_stream_never_establish_completion(self):
  for defect in ('missing-original','truncated','wrong-model','stream-disagreement','native-admission','unknown-status','receipt-boolean','receipt-drift','duplicate-original','different-provider-request','accepted-decision','wrong-task'):
   with self.subTest(defect=defect):
    f=Fixture()
    if defect=='missing-original':f.refs.pop()
    elif defect=='truncated':f.replace('provider-response.bin',b'data: {"type":"response.created"}\n\n')
    elif defect=='wrong-model':f.terminal['model']='different';f.replace('provider-response.bin',f.stream())
    elif defect=='stream-disagreement':
     f.replace('provider-response.bin',b'data: '+canonical(dict(type='response.output_item.done',output_index=0,item=dict(f.terminal['output'][0],content=[])))+b'\n\n'+f.stream())
    elif defect=='native-admission':f.calls=[dict(status='unknown')]
    elif defect=='unknown-status':f.d['status']='unknown'
    elif defect=='receipt-boolean':f.receipt['provider_calls']=True
    elif defect=='receipt-drift':f.replace('receipt.json',canonical(dict(f.receipt,http_status=503)))
    elif defect=='duplicate-original':f.refs.append(copy.deepcopy(f.refs[0]))
    elif defect=='different-provider-request':f.replace('provider-request.json',canonical(dict(wire.response_request(f.logical),tools=[dict(type='function')])))
    elif defect=='accepted-decision':
     accepted=answer('inspect');accepted['reason']='retained-evidence';accepted['read_request']=dict(kind='runtime-contract',id=str(uuid.uuid4()),cursor=0)
     f.terminal['output'][0]['content'][0]['text']=canonical(accepted).decode();wire.structured_response(f.terminal);f.replace('provider-response.bin',f.stream())
    elif defect=='wrong-task':f.d['task_id']=str(uuid.uuid4())
    try:proof=f.proof()
    except (ValueError,KeyError,TypeError):proof=None
    self.assertIsNone(proof)
 def test_changed_original_or_forged_supplement_is_rejected_on_every_read(self):
  f=Fixture();f.retain();before=copy.deepcopy(f.d)
  ref=next(r for r in f.refs if r['name']=='provider-response.bin');f.blobs[ref['id']]+=b'changed'
  with self.assertRaises(ValueError):recovery.confirmed(f.task,f.d,f.receipt,f.original,f.rows)
  self.assertEqual(f.d,before)
  f=Fixture();f.retain();f.proofs[0]['proof']['provider_calls']=0
  with self.assertRaises(ValueError):recovery.confirmed(f.task,f.d,f.receipt,f.original,f.rows)
 def test_worker_records_once_using_original_storage_only(self):
  f=Fixture();before=copy.deepcopy(f.d);events=[]
  def rows(kind,q):
   if kind=='model_reconciliations':return f.proofs
   if kind=='autonomy_tasks':return [f.task]
   if kind=='study_owners':return [dict(owner_id=f.task['actor_id'])]
   raise AssertionError(kind)
  def artifact(study,actor,raw,ident,media,provenance):f.blobs[ident]=raw;return dict(id=ident,sha256=digest(raw),byte_count=len(raw))
  def rpc(name,payload):
   self.assertEqual(name,'research_controller_dispatch_reconcile');events.append(payload)
   f.proofs.append(dict(artifact_id=payload['p_artifact'],sha256=payload['p_sha256'],proof=payload['p_proof']))
  with tempfile.TemporaryDirectory() as spool,patch.object(store,'rows',side_effect=rows),patch.object(repair,'rows',side_effect=f.rows),patch.object(repair,'original',side_effect=f.original),patch.object(store,'artifact',side_effect=artifact),patch.object(store,'append'),patch.object(store,'artifact_bytes',side_effect=lambda study,ident,**kw:f.blobs[ident]),patch.object(store,'rpc',side_effect=rpc),patch.object(runtime,'http_request') as transport,patch.object(builder,'container_action') as cleanup:
   self.assertEqual(recovery._reconcile_dispatches(spool,[f.d]),[f.d['id']]);self.assertEqual(recovery._reconcile_dispatches(spool,[f.d]),[])
  self.assertEqual(len(events),1);self.assertEqual(f.d,before);transport.assert_not_called();cleanup.assert_not_called()
 def test_repair_waits_for_supplement_then_rechecks_originals_without_model_calls(self):
  f=Fixture()
  with patch.object(repair,'rows',side_effect=f.rows),patch.object(repair,'original',side_effect=f.original),patch.object(runtime,'http_request') as transport:
   self.assertTrue(repair.diagnose(f.task,dict(category='runtime'))['waiting'])
   f.retain();result=repair.diagnose(f.task,dict(category='runtime'))
  self.assertFalse(result['waiting']);self.assertFalse(result['validated']);self.assertEqual(result['reconciled_dispatches'][0]['provider_calls'],1);transport.assert_not_called()

if __name__=='__main__':unittest.main()
