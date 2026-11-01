"""Exercise working evidence through the production query adapter, without I/O."""
import tempfile,unittest,urllib.parse,uuid
from pathlib import Path
from unittest.mock import Mock
from platform_supervisor.store import Store
from platform_supervisor import working_evidence
from platform_supervisor.common import digest

class WorkingStore(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=Store('synthetic-test-only',Path(self.tmp.name),str(uuid.uuid4()))
  self.store.task=dict(id=str(uuid.uuid4()));self.store.request=Mock(return_value=[])
 def test_explicit_bound_replaces_default_without_losing_task_filter(self):
  self.store.rows('artifacts',limit=2,name='eq.working-checkpoint.zip')
  query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.store.request.call_args.args[0]).query)
  self.assertEqual(query,dict(select=['*'],task_id=['eq.'+self.store.task['id']],limit=['2'],name=['eq.working-checkpoint.zip']))
  self.store.rows('artifacts');query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.store.request.call_args.args[0]).query)
  self.assertEqual(query['limit'],['1000'])
 def test_callers_cannot_override_task_binding(self):
  with self.assertRaises(TypeError):self.store.rows('artifacts',limit=2,task_id='eq.'+str(uuid.uuid4()))
  self.store.request.assert_not_called()
 def test_original_metadata_and_bytes_use_actual_rows_and_passive_storage_get(self):
  original=b'Exact immutable original';h=digest(original);path=self.store.task['id']+'/'+h
  row=dict(id=str(uuid.uuid4()),task_id=self.store.task['id'],storage_path=path,sha256=h,name='working-checkpoint.zip',byte_count=len(original))
  calls=[]
  def request(url,method='GET',body=None,raw=False):
   self.assertEqual(method,'GET');self.assertIsNone(body);calls.append(url)
   if raw:self.assertEqual(url,'/storage/v1/object/platform-supervisor-private/'+path);return original
   parsed=urllib.parse.urlsplit(url);self.assertEqual(parsed.path,'/rest/v1/platform_artifacts');query=urllib.parse.parse_qs(parsed.query)
   self.assertEqual(query,dict(select=['*'],task_id=['eq.'+self.store.task['id']],limit=['2'],name=['eq.working-checkpoint.zip'],sha256=['eq.'+h]));return [row]
  self.store.request=request
  self.assertEqual(working_evidence.original(self.store,dict(path=path,sha256=h),'working-checkpoint.zip'),(original,row))
  self.assertEqual(len(calls),2)

if __name__=='__main__':unittest.main()
