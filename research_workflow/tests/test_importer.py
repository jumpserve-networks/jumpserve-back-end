import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import io
import os
import subprocess
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import importer, workflow, intake

class ExternalEvidence(unittest.TestCase):
    def test_invalid_retrieval_keeps_captured_body_and_all_attempts(self):
        class Response(io.BytesIO):
            status=200;url='https://example.org/paper.pdf'
            class Headers:
                def get_content_type(self):return 'text/html'
                def get(self,name):return None
            headers=Headers()
        class Opener:
            def open(self,*args,**kwargs):return Response(b'<html>landing page</html>')
        with tempfile.TemporaryDirectory() as directory,patch.object(intake.urllib.request,'build_opener',return_value=Opener()):
            result=intake.retrieve('https://example.org/paper.pdf',Path(directory)/'retrieve')
            self.assertEqual(result['status'],'unavailable');self.assertIsNone(result['sha256']);self.assertEqual(len(result['attempts']),2)
            for attempt in result['attempts']:
                captured=Path(directory)/'retrieve'/attempt['captured_response_path']
                self.assertEqual(workflow.digest(captured.read_bytes()),attempt['captured_response_sha256'])
    def fixture(self,directory):
        original=Path(directory)/'input.bin';original.write_bytes(b'abc')
        run=dict(id=str(uuid.uuid4()),campaign_id=str(uuid.uuid4()),configuration_id=None,status='failed',reason='External equipment unavailable',started_at='2026-10-07T10:00:00Z',ended_at='2026-10-07T10:00:01Z',analysis_version='external-v1',input_sha256=workflow.digest(b'abc'),output_sha256=None,requested_resources={},actual_resources={},usage={'measured_charges_usd':None},provenance={'execution':'operator-declared'})
        data=dict(version=1,records=[dict(kind='runs',record=run)],artifacts=[dict(path='input.bin',sha256=workflow.digest(b'abc'),media_type='application/octet-stream',version='original v1',transformation='none')],provenance=dict(producer='Development fixture',execution_revision='fixture-v1',execution_dates='2026-10-07',limitations='Not scientific validation'))
        path=Path(directory)/'bundle.json';path.write_bytes(workflow.canonical(data));return path,data
    def test_failed_external_run_and_original_identity_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path,_=self.fixture(directory);prepared,raw,originals=importer.prepare(path,str(uuid.uuid4()))
            self.assertEqual(prepared['records'][0]['record']['status'],'failed');self.assertEqual(originals[0]['raw'],b'abc')
            self.assertEqual(prepared['original_sha256'],workflow.digest(raw));self.assertEqual(prepared['scientific_validation'],'Not established by import')
    def test_hash_mismatch_paths_and_fabricated_run_input_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for mutation in ('path','hash','input','publication'):
                path,data=self.fixture(directory)
                if mutation=='path':data['artifacts'][0]['path']='../outside.bin'
                if mutation=='hash':data['artifacts'][0]['sha256']='0'*64
                if mutation=='input':data['records'][0]['record']['input_sha256']='0'*64
                if mutation=='publication':data['records'][0]['kind']='publications'
                path.write_bytes(workflow.canonical(data))
                with self.assertRaises(ValueError,msg=mutation):importer.prepare(path,str(uuid.uuid4()))
    def test_retrieval_rejects_credential_urls_before_creating_artifacts(self):
        for url in ('http://example.org/paper','https://user:secret@example.org/paper','https://example.org/paper?access_token=secret'):
            with self.assertRaises(ValueError):intake.validate_url(url)
    def test_invalid_pdf_preserves_failed_parser_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf=Path(directory)/'invalid.pdf';pdf.write_bytes(b'invalid pdf');output=Path(directory)/'new-folder/text.json'
            with self.assertRaises(ValueError):intake.text_packet(pdf,output)
            failure=json.loads(Path(str(output)+'.failure.json').read_bytes())
            self.assertEqual(failure['status'],'failed');self.assertEqual(failure['original_sha256'],workflow.digest(pdf.read_bytes()))
            self.assertIsNone(failure['measured_charges_usd']);self.assertFalse(output.exists())
            with self.assertRaises(ValueError):intake.text_packet(pdf,output)
    def test_parser_subprocess_inherits_parent_only_dependency_directory(self):
        # Dependency-discovery regression, not a PDF parser correctness fixture.
        # The separate integration campaign uses the actual pinned pypdf bytes.
        with tempfile.TemporaryDirectory() as directory:
            layer=Path(directory)/'python';package=layer/'pypdf';package.mkdir(parents=True)
            (layer/'jumpserve_parent_path_only.py').write_text('VALUE = 1\n')
            (package/'__init__.py').write_text("import jumpserve_parent_path_only\n__version__='parent-path-only-fixture'\nclass Page:\n def extract_text(self): return 'Dependency path control'\nclass PdfReader:\n def __init__(self, stream):\n  self.is_encrypted=False\n  self.pages=[Page()]\n")
            pdf=Path(directory)/'input.pdf';pdf.write_bytes(b'%PDF-1.4\nDependency discovery control only')
            output=Path(directory)/'packet.json'
            with patch.dict(os.environ),patch.object(sys,'path',[str(layer),*sys.path]):
                os.environ.pop('PYTHONPATH',None)
                baseline=subprocess.run([sys.executable,'-B','-c','import jumpserve_parent_path_only'],capture_output=True,timeout=5)
                self.assertNotEqual(baseline.returncode,0)
                result=intake.text_packet(pdf,output)
            packet=json.loads(output.read_bytes())
            self.assertEqual(result['pages'],1)
            self.assertEqual(packet['tool_version'],'parent-path-only-fixture')
            self.assertEqual(packet['pages'][0]['text'],'Dependency path control')
            self.assertEqual(packet['original_sha256'],workflow.digest(pdf.read_bytes()))
if __name__=='__main__':unittest.main()
