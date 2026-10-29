"""Observed failure regressions, synthetic streams; no cloud writes/inference."""
import copy,io,json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomous_repair as repair,dispatch_recovery as recovery,openclaw_runtime as runtime,model_dispatch,store,experiment_builder as builder
from workflow import canonical,digest

def uid():return str(uuid.uuid4())

class Fixture:
 def __init__(self,count=2):
  self.task=dict(id=uid(),study_id=uid(),actor_id=uid(),status='waiting',context={})
  self.logical=canonical(dict(builder=dict(context={})))
  self.d=dict(id=uid(),task_id=self.task['id'],study_id=self.task['study_id'],status='unknown',request_raw=self.logical.decode(),request_sha256=digest(self.logical),created_at='2026-10-29T00:00:00Z',ended_at='2026-10-29T00:04:00Z',provenance={})
  self.blobs={};self.records={};self.calls=[];self.proofs=[]
  self.checkpoint=self.add(b'exact original checkpoint')
  for _ in range(count):
   req=self.add(canonical(dict(model='gpt-6.1-sol',store=False,stream=True,input=[])))
   raw=b'data: '+canonical(dict(type='response.completed',response=dict(status='completed',model='gpt-6.1-sol',output=[],usage={})))+b'\n\n'
   response=self.add(raw)
   self.calls.append(dict(id=uid(),dispatch_id=self.d['id'],study_id=self.task['study_id'],task_id=self.task['id'],status='complete',request_artifact_id=req['artifact_id'],request_sha256=req['sha256'],response_artifact_id=response['artifact_id'],response_sha256=response['sha256'],response_received=True,http_status=200))
 def add(self,raw,identity=None,provenance=None):
  identity=identity or uid();self.blobs[identity]=raw;self.records[identity]=dict(id=identity,sha256=digest(raw),byte_count=len(raw),provenance=provenance or {})
  return dict(artifact_id=identity,sha256=digest(raw))
 def rows(self,task,kind,**q):
  if kind=='builder_calls':return self.calls
  if kind=='model_reconciliations':return self.proofs
  if kind=='artifacts':return [self.records[q['id'][3:]]]
  return []
 def original(self,task,pointer,maximum=150000):
  return repair.original(task,pointer,maximum)
 def receipt(self):
  logical=self.add(self.logical);receipt=self.add(canonical(dict(provider_calls=len(self.calls),native_tool_cleanup=True,container_cleanup='stopped',checkpoint=self.checkpoint)))
  self.d['provenance']['artifacts']=[dict(id=logical['artifact_id'],sha256=logical['sha256'],name='logical.json'),dict(id=receipt['artifact_id'],sha256=receipt['sha256'],name='receipt.json')]
  return json.loads(self.blobs[receipt['artifact_id']])
 def proof(self):
  proof=dict(version=recovery.VERSION,dispatch_id=self.d['id'],request_sha256=self.d['request_sha256'],container_cleanup='stopped',checkpoint=self.checkpoint,calls=[{k:c[k] for k in ('id','request_sha256','response_sha256')} for c in self.calls])
  p=self.add(canonical(proof));self.proofs=[dict(task_id=self.task['id'],request_sha256=self.d['request_sha256'],artifact_id=p['artifact_id'],sha256=p['sha256'],proof=proof,created_at='2026-10-29T01:00:00Z')]
  return proof
 def patches(self):
  from contextlib import ExitStack
  stack=ExitStack();stack.enter_context(patch.object(repair,'rows',side_effect=self.rows));stack.enter_context(patch.object(store,'artifact_bytes',side_effect=lambda study,ident,**kw:self.blobs[ident]));return stack

class Recovery(unittest.TestCase):
 def test_damaged_candidate_does_not_starve_other_claims_and_scan_cursor_advances(self):
  candidates=[dict(id=uid()),dict(id=uid())]
  with tempfile.TemporaryDirectory() as tmp:
   with patch.object(store,'rpc',return_value=candidates),patch.object(recovery,'_reconcile_dispatches',side_effect=[ValueError('hash differs'),[candidates[1]['id']]]):
    self.assertEqual(recovery.reconcile_spool(tmp),[candidates[1]['id']])
   self.assertEqual(json.loads((Path(tmp)/'reconciliation-scan.json').read_bytes())['after'],candidates[1]['id'])
 def test_completed_native_calls_after_local_failure_are_accounted_for_without_replay(self):
  f=Fixture(6);receipt=f.receipt();f.d['status']='failed';before=copy.deepcopy(f.d)
  with f.patches(),patch.object(runtime,'credential') as token:
   result=recovery.confirmed(f.task,f.d,receipt,f.original,f.rows)
  self.assertEqual(result['native_calls'],6);self.assertEqual(f.d,before);token.assert_not_called()
 def test_unknown_parent_needs_verified_cleanup_proof_even_when_calls_completed(self):
  f=Fixture()
  with f.patches():self.assertIsNone(recovery.confirmed(f.task,f.d,{},f.original,f.rows))
  f.proof()
  with f.patches():self.assertEqual(recovery.confirmed(f.task,f.d,{},f.original,f.rows)['native_calls'],2)
 def test_zero_native_ledger_needs_worker_proof_not_absence_of_outputs(self):
  f=Fixture(0)
  with f.patches():self.assertIsNone(recovery.confirmed(f.task,f.d,{},f.original,f.rows))
  f.proof()
  with f.patches():self.assertEqual(recovery.confirmed(f.task,f.d,{},f.original,f.rows)['native_calls'],0)
 def test_unknown_calls_missing_terminal_and_hash_mismatches_cannot_resume(self):
  f=Fixture();f.proof();f.calls[0]['status']='unknown'
  with f.patches():self.assertIsNone(recovery.confirmed(f.task,f.d,{},f.original,f.rows))
  f.calls[0]['status']='complete';identity=f.calls[0]['response_artifact_id'];raw=f.blobs[identity]
  f.blobs[identity]=raw[:-2]
  with f.patches(),self.assertRaisesRegex(ValueError,'hash differs'):recovery.confirmed(f.task,f.d,{},f.original,f.rows)
  f.blobs[identity]=b'data: '+canonical(dict(type='response.created',response={})) +b'\n\n';f.records[identity].update(sha256=digest(f.blobs[identity]),byte_count=len(f.blobs[identity]));f.calls[0]['response_sha256']=digest(f.blobs[identity]);f.proofs=[];f.proof()
  with f.patches(),self.assertRaisesRegex(Exception,'incomplete-provider-stream'):recovery.confirmed(f.task,f.d,{},f.original,f.rows)
 def test_distinct_original_call_coverage_is_required(self):
  f=Fixture();f.proof();proof=json.loads(f.blobs[f.proofs[0]['artifact_id']]);proof['calls']=proof['calls'][:1]
  raw=canonical(proof);ident=f.proofs[0]['artifact_id'];f.blobs[ident]=raw;f.records[ident].update(sha256=digest(raw),byte_count=len(raw));f.proofs[0].update(sha256=digest(raw),proof=proof)
  with f.patches(),self.assertRaisesRegex(ValueError,'coverage differs'):recovery.confirmed(f.task,f.d,{},f.original,f.rows)
 def test_worker_recovers_interrupted_spool_using_only_persistence_and_fixed_cleanup(self):
  f=Fixture();before=copy.deepcopy(f.d);events=[]
  def rows(kind,q):
   if kind=='model_dispatches':return [] if q.get('status')=='in.(queued,running)' else [f.d]
   if kind=='autonomy_tasks':return [f.task]
   if kind=='study_owners':return [dict(owner_id=f.task['actor_id'])]
   if kind=='model_reconciliations':return f.proofs
   raise AssertionError(kind)
  def artifact(study,actor,raw,identity,media,provenance):f.add(raw,identity,provenance);return f.records[identity]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);directory=root/f.d['id'];directory.mkdir();(directory/'logical.json').write_bytes(f.logical)
   taskroot=root/'builder-workspaces'/f.task['id'];workspace=taskroot/'workspace';workspace.mkdir(parents=True);(workspace/'experiment.py').write_text('print(42)')
   def rpc(name,payload):
    if name=='research_model_reconciliation_candidates':return [f.d]
    events.append((name,payload));return {}
   with f.patches(),patch.object(store,'rows',side_effect=rows),patch.object(store,'artifact',side_effect=artifact),patch.object(store,'append'),patch.object(store,'rpc',side_effect=rpc),patch.object(builder,'container_action') as cleanup,patch.object(builder,'normalize_workspace',return_value=b'{"bytes_unchanged":true}'),patch.object(runtime,'credential') as token:
    result=recovery.reconcile_spool(root)
   self.assertEqual(result,[f.d['id']]);cleanup.assert_called_once_with(f.task['id'],'stop');token.assert_not_called()
   proof=events[0][1]['p_proof'];self.assertEqual(proof['original_status'],'unknown');self.assertEqual(len(proof['calls']),2)
   self.assertEqual(json.loads((taskroot/'checkpoint-reference.json').read_bytes()),proof['checkpoint']);self.assertEqual(f.d,before)

class Outbox(unittest.TestCase):
 def test_storage_failure_retries_exact_bytes_without_any_model_transport(self):
  logical=canonical(dict(test='original'));job=dict(id=uid(),study_id=uid(),actor_id=uid(),lease_token=uid(),request_sha256=digest(logical));calls=[];saved={};finished={}
  def rpc(op,payload):
   calls.append(op)
   if op=='result':return finished or dict(status='running')
   if op=='finish':finished.update(status=payload['status'],provenance=payload['provenance']);return finished
   raise AssertionError(op)
  def artifact(study,actor,raw,identity,media,provenance):
   if not saved: saved['failure']=True;raise store.StoreError('Synthetic transient storage failure')
   saved[identity]=raw;return dict(id=identity,sha256=digest(raw),byte_count=len(raw))
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);directory=root/job['id'];directory.mkdir();(directory/'logical.json').write_bytes(logical);(directory/'config.json').write_text('private runtime token must not be archived')
   marker=runtime.save_completion(job,directory,{'decision':'retained'},dict(status='complete',provider_calls=1),root/'pending-completions')
   original=(directory/'completion.json').read_bytes()
   with patch.object(model_dispatch,'rpc',side_effect=rpc),patch.object(store,'artifact',side_effect=artifact),patch.object(store,'append'),patch.object(runtime,'credential') as token,patch.object(runtime,'execute') as invoke:
    runtime.retry_completions(root);self.assertTrue(marker.exists());self.assertNotIn('finish',calls)
    runtime.retry_completions(root);self.assertFalse(marker.exists());self.assertEqual(calls.count('finish'),1)
    self.assertTrue(runtime.persist_completed(job,directory));self.assertEqual(calls.count('finish'),1)
   self.assertEqual((directory/'completion.json').read_bytes(),original);self.assertNotIn(b'private runtime token must not be archived',saved.values());token.assert_not_called();invoke.assert_not_called()

if __name__=='__main__':unittest.main()
