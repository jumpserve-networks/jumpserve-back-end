"""Original-byte persistence, bounded retrieval, and completion reconciliation."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import importer,intake,openclaw_runtime as runtime,model_dispatch,preparation,scheduler,store,workflow
import test_importer as import_fixtures
from test_preparation import small_plan


class RetainedIO(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)

    def test_import_persistence_checks_ownership_retains_exact_bytes_and_stops_with_original_uploads_intact(self):
        path,_=import_fixtures.ExternalEvidence().fixture(self.root);study,actor,identity=(str(uuid.uuid4()) for _ in range(3));prepared,raw,originals=importer.prepare(path,identity)
        with patch.object(store,'owns',return_value=False),patch.object(store,'artifact') as upload,self.assertRaisesRegex(ValueError,'ownership'):importer.persist(prepared,raw,originals,study,actor)
        upload.assert_not_called()
        with patch.object(store,'owns',return_value=True),patch.object(store,'artifact',side_effect=lambda *args:dict(id=args[3],sha256=workflow.digest(args[2]))) as upload,patch.object(store,'append_bundle') as append:
            importer.persist(prepared,raw,originals,study,actor);self.assertEqual([call.args[2] for call in upload.call_args_list],[raw,b'abc']);self.assertEqual(upload.call_args_list[0].args[3],str(uuid.uuid5(uuid.UUID(identity),'bundle')));self.assertEqual(append.call_args.args[2][-1]['record']['status'],'failed')
            upload.reset_mock();append.reset_mock()
            with patch.object(importer.time,'monotonic',side_effect=[0,0,181]),self.assertRaisesRegex(ValueError,'already uploaded immutable bytes remain preserved'):importer.persist(prepared,raw,originals,study,actor)
            self.assertEqual(upload.call_count,1);append.assert_not_called();self.assertEqual(path.read_bytes(),raw)

    def test_retrieval_retains_complete_pdf_originals_and_validates_redirects_before_following(self):
        raw=b'%PDF-1.4\nOriginal synthetic retrieval control; not a real paper.'
        class Response(io.BytesIO):
            status=200;url='https://example.test/paper.pdf'
            headers=SimpleNamespace(get_content_type=lambda:'application/pdf',get=lambda name:str(len(raw)))
            def read1(self,n):return self.read(n)
        with patch.object(intake.urllib.request,'build_opener',return_value=SimpleNamespace(open=lambda *args,**kwargs:Response(raw))):
            audit=intake.retrieve('https://example.test/paper.pdf',self.root/'retrieval')
        self.assertEqual(audit['status'],'retrieved');self.assertEqual(audit['bytes'],len(raw));self.assertEqual(audit['sha256'],workflow.digest(raw));self.assertEqual(len(audit['attempts']),1);self.assertEqual((self.root/'retrieval/original.bin').read_bytes(),raw)
        redirects=intake.PublicRedirects();request=urllib.request.Request('https://example.test/start')
        self.assertEqual(redirects.redirect_request(request,None,302,'Found',{},'https://example.test/end').full_url,'https://example.test/end')
        with self.assertRaisesRegex(ValueError,'HTTPS'):redirects.redirect_request(request,None,302,'Found',{},'http://example.test/end')
        with patch.object(intake.threading,'current_thread',return_value=object()),self.assertRaisesRegex(ValueError,'main-thread deadline'):
            with intake.retrieval_deadline():self.fail('Non-main-thread retrieval must not start')

    def test_optional_pdf_metadata_failure_does_not_destroy_valid_extracted_text(self):
        class Reader:
            is_encrypted=False;pages=[SimpleNamespace(extract_text=lambda:'Original retained page')]
            @property
            def metadata(self):raise ValueError('Original malformed optional metadata')
        fake=SimpleNamespace(PdfReader=lambda raw:Reader(),__version__='synthetic-parser-boundary')
        with patch.dict(sys.modules,{'pypdf':fake}):packet=intake._extract(b'%PDF original bytes')
        self.assertEqual(packet['pages'][0]['text'],'Original retained page');self.assertEqual(packet['document_metadata'],{'title':None,'author':None});self.assertEqual(packet['review_status'],'retrieved-unreviewed')

    def test_prepared_plan_original_binding_is_checked_before_enqueuing_the_saved_request(self):
        study=dict(id=str(uuid.uuid4()),paper_url='https://example.org/synthetic-preparation-paper');actor=str(uuid.uuid4());plan=small_plan();raw=workflow.canonical(plan);compiled=preparation.compile_plan(plan,raw,study);row=dict(id=compiled['id'],compiled_artifact_id=str(uuid.uuid4()),manifest_sha256=compiled['manifest_sha256'])
        with patch.object(store,'artifact_bytes',return_value=workflow.canonical(compiled)) as read:
            self.assertEqual(preparation.compiled(study['id'],row),compiled);self.assertEqual(read.call_args.kwargs,{'maximum':4000000})
            with self.assertRaisesRegex(store.StoreError,'identity differs'):preparation.compiled(str(uuid.uuid4()),row)
        with patch.object(store,'rows',return_value=[row]),patch.object(store,'artifact_bytes',return_value=workflow.canonical(compiled)),patch.object(scheduler,'enqueue',return_value={'saved':True}) as enqueue:
            result=preparation.enqueue(study,actor,dict(action='enqueue',prepared_id=row['id'],job_id=compiled['jobs'][0]['id']));self.assertEqual(result,{'saved':True});self.assertEqual(enqueue.call_args.args,(study['id'],actor,compiled['jobs'][0]['request']))
        for invalid in [[],dict(plan,schema_version=2),dict(plan,extra=True)]:
            with self.assertRaisesRegex(ValueError,'schema-version'):preparation.parse(workflow.canonical(invalid))
        plan['campaigns'][0]['dependencies']=[{'key':'absent','requirement':'complete-run'}]
        with self.assertRaisesRegex(ValueError,'Dependencies need'):preparation.parse(workflow.canonical(plan))

    def test_completion_lock_contention_and_terminal_mismatches_preserve_original_work_without_provider_calls(self):
        with patch.object(runtime.fcntl,'flock',side_effect=BlockingIOError),patch.object(runtime,'_persist_completed') as persist:self.assertFalse(runtime.persist_completed({},self.root))
        persist.assert_not_called();pending=self.root/'pending-completions';pending.mkdir();job=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()));marker=pending/(job['id']+'.json');original=workflow.canonical(job)
        for reconciled,status,suffix in [(True,'failed','.reconciled'),(False,'failed','.awaiting-reconciliation'),(False,'running','.json')]:
            with self.subTest(reconciled=reconciled,status=status):
                marker.write_bytes(original)
                with patch.object(runtime,'persist_completed',return_value=False),patch.object(store,'rows',return_value=[{'original':True}] if reconciled else []) as rows,patch.object(model_dispatch,'rpc',return_value={'status':status}),patch.object(runtime,'http_request') as transport:runtime.retry_completions(self.root)
                target=marker.with_suffix(suffix);self.assertEqual(target.read_bytes(),original);transport.assert_not_called();self.assertEqual(rows.call_args.args[1],{'dispatch_id':'eq.'+job['id'],'limit':1});target.unlink()


if __name__=='__main__':unittest.main()
