"""Passive original transport and complete bounded project-source delivery."""
import io,json,unittest,zipfile
from platform_supervisor import incident_evidence as evidence,repair
from platform_supervisor.common import canonical,digest

def archive(files):
 output=io.BytesIO()
 with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
  for name,raw in files.items():z.writestr(name,raw)
 return output.getvalue()

class ControllerEvidence(unittest.TestCase):
 def test_controller_originals_include_the_exact_rejected_decision_and_terminal_hash(self):
  decision=dict(action='inspect',read_request=dict(kind='runtime-contract',id='invented-name',cursor=0))
  terminal=dict(id='original-response',status='completed',model='gpt-6.1-sol',output=[dict(type='message',role='assistant',content=[dict(type='output_text',text=json.dumps(decision))])])
  raw=b'data: '+canonical(dict(type='response.completed',response=terminal))+b'\n\n'
  view=repair._artifact_view(raw,'provider-response.bin')
  self.assertTrue(view['terminal_complete']);self.assertFalse(view['decision_valid']);self.assertEqual(view['original_sha256'],digest(raw))
  self.assertEqual(view['terminal_sha256'],digest(canonical(terminal)));self.assertEqual(json.loads(view['assistant_outputs'][0]['value']['content'][0]['text']),decision)
  self.assertEqual(repair._artifact_view(b'{"count":0,"missing":null}','provider-request.json')['value'],dict(count=0,missing=None))
 def test_incomplete_response_is_explicit_data_and_cannot_be_presented_as_completion(self):
  raw=b'data: {"type":"response.created","response":{"status":"in_progress"}}\n\n'
  value=evidence.controller_response_view(raw)
  self.assertFalse(value['terminal_complete']);self.assertNotIn('decision_valid',value);self.assertEqual(value['original_sha256'],digest(raw))
 def test_project_acquisition_and_imported_helpers_are_delivered_without_running_them(self):
  files={'project/acquisition.py':b'raise RuntimeError("never execute")','project/pkg/parser.py':b'def parse(): return 0','project/config.toml':b'count=0','project/.env':b'credential=private','project/data.bin':b'opaque'}
  value=evidence.checkpoint_view(archive(files))
  self.assertEqual(value['project_files']['project/acquisition.py'],files['project/acquisition.py'].decode())
  self.assertEqual(value['delivered_files']['project/pkg/parser.py']['original_sha256'],digest(files['project/pkg/parser.py']))
  self.assertEqual(value['project_file_coverage']['shown'],3);self.assertEqual(len(value['project_file_coverage']['omitted']),2)
  projected,_=repair._project(value);self.assertNotIn('credential=private',json.dumps(projected))
 def test_source_count_omissions_and_large_excerpts_preserve_original_identity(self):
  files={'project/file-'+str(i)+'.py':b'x=0' for i in range(40)}
  value=evidence.checkpoint_view(archive(files));self.assertEqual(value['project_file_coverage']['total'],40)
  self.assertEqual(value['project_file_coverage']['shown'],evidence.PROJECT_FILES);self.assertEqual(len(value['project_file_coverage']['omitted']),8)
  source=b'# original\n'+b'x'*30000;value=evidence.checkpoint_view(archive({'project/acquisition.py':source}))
  self.assertFalse(value['delivered_files']['project/acquisition.py']['complete']);self.assertEqual(value['delivered_files']['project/acquisition.py']['original_sha256'],digest(source))
  self.assertFalse(value['project_files']['project/acquisition.py']['available'])

if __name__=='__main__':unittest.main()
