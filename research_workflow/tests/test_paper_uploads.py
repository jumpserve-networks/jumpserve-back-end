import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import api, autonomy, paper_uploads, preparation, store, workflow

RAW=b'%PDF-1.4\nExplicit software fixture\n%%EOF'
ACTOR=str(uuid.uuid4()); ID=str(uuid.uuid4())
def request():return dict(action='reserve',request_id=ID,filename='Paper.pdf',sha256=workflow.digest(RAW),byte_count=len(RAW),domain='Networking',scope='Declared test conditions')
def row():
 body=request();return dict(id=ID,owner_id=ACTOR,status='pending',paper=workflow.paper_details(dict(title='Paper',paper_url=None,paper_upload={k:body[k] for k in ('filename','sha256','byte_count')},domain=body['domain'],scope=body['scope'],study_kind='paper',origin_module=None)))

class PaperUploads(unittest.TestCase):
 def test_reservation_is_owner_scoped_and_only_returns_a_permission_for_the_exact_file(self):
  value=row();path=ID+'/'+workflow.digest(RAW)
  with patch.object(store,'rpc',return_value=value) as rpc,patch.object(store,'request',return_value=dict(url='/object/upload/sign/research-paper-uploads/'+path+'?token=explicit-local-upload-permission')):
   ticket=paper_uploads.submit(ACTOR,request())
  self.assertEqual(ticket['path'],path);self.assertEqual(ticket['bucket'],paper_uploads.BUCKET)
  self.assertEqual(rpc.call_args.args[1]['p_actor'],ACTOR);self.assertNotIn('token',json.dumps(rpc.call_args.args[1]))
 def test_oversize_false_size_path_name_and_overridden_resources_cannot_be_reserved(self):
  for change in [dict(byte_count=10_000_001),dict(byte_count=True),dict(byte_count=0),dict(filename='../Paper.pdf'),dict(filename='Paper.txt'),dict(filename='A\n.pdf'),dict(max_spend_cents=99999)]:
   with patch.object(store,'rpc') as rpc,self.assertRaises(ValueError):paper_uploads.submit(ACTOR,dict(request(),**change))
   rpc.assert_not_called()
 def test_completion_checks_owner_before_reading_private_storage(self):
  with patch.object(store,'rows',return_value=[]) as rows,patch.object(store,'request') as get,self.assertRaisesRegex(ValueError,'Private paper upload'):
   paper_uploads.submit(ACTOR,dict(action='start',request_id=ID))
  get.assert_not_called();self.assertEqual(rows.call_args.args[1]['owner_id'],'eq.'+ACTOR)
 def test_hash_size_or_signature_mismatch_is_recorded_and_never_starts(self):
  for data in [RAW+b'changed',b'invalid-PDF']:
   value=row()
   if data==b'invalid-PDF':value['paper']['paper_upload'].update(sha256=workflow.digest(data),byte_count=len(data))
   with patch.object(store,'rows',return_value=[value]),patch.object(store,'request',return_value=data),patch.object(store,'rpc') as rpc,patch.object(store,'artifact') as artifact,self.assertRaises(ValueError):
    paper_uploads.submit(ACTOR,dict(action='start',request_id=ID))
   artifact.assert_not_called();self.assertEqual(rpc.call_args.args[1]['p_operation'],'reject')
 def test_verified_bytes_are_retained_before_atomic_workflow_start(self):
  events=[];value=row()
  def artifact(*args):events.append('artifact-hash-verified');self.assertEqual(args[2],RAW);return dict(id=str(uuid.uuid4()),sha256=workflow.digest(RAW),byte_count=len(RAW))
  def rpc(name,payload):events.append('atomic-start');self.assertEqual(payload['p_payload']['paper'],value['paper']);initial=json.loads(payload['p_payload']['initial_prompt']);self.assertIsNone(initial['defaults']['attempts_per_claim']);return dict(id=ID)
  with patch.object(store,'rows',return_value=[value]),patch.object(store,'request',return_value=RAW),patch.object(store,'artifact',side_effect=artifact),patch.object(store,'rpc',side_effect=rpc),patch.object(autonomy,'wake',return_value={}):
   paper_uploads.submit(ACTOR,dict(action='start',request_id=ID))
  self.assertEqual(events,['artifact-hash-verified','atomic-start'])
 def test_anonymous_or_held_upload_route_is_denied_before_storage(self):
  event=dict(rawPath='/research/paper-uploads',body=json.dumps(request()),requestContext=dict(http=dict(method='POST')))
  with patch.object(paper_uploads,'submit') as submit:
   with self.assertRaises(api.HttpError):api.dispatch(event,None)
   with patch.dict('os.environ',{'RESEARCH_AUTONOMY_ENABLED':'false'}),self.assertRaises(api.HttpError):api.dispatch(event,ACTOR)
  submit.assert_not_called()
 def test_exact_main_paper_hash_and_size_can_reuse_a_retained_plan_but_filename_cannot(self):
  plan=json.loads((Path(__file__).resolve().parents[1]/'plans/ipv6-archive-v1.json').read_bytes())
  original=next(e['record'] for e in plan['records'] if e['kind']=='sources' and e['record']['source_url'] in plan['paper_urls'])
  study=dict(id=ID,paper_url=None,paper_upload=dict(sha256=original['sha256'],byte_count=original['byte_count'],filename='Any name.pdf'))
  self.assertTrue(preparation.matches_paper(plan,study));compiled=preparation.compile_plan(plan,workflow.canonical(plan),study);self.assertEqual(compiled['provenance']['paper_match']['sha256'],original['sha256'])
  for change in (dict(sha256='0'*64),dict(byte_count=original['byte_count']+1)):
   other=copy.deepcopy(study);other['paper_upload'].update(change);self.assertFalse(preparation.matches_paper(plan,other))
   with self.assertRaises(ValueError):preparation.compile_plan(plan,workflow.canonical(plan),other)

if __name__=='__main__':unittest.main()
