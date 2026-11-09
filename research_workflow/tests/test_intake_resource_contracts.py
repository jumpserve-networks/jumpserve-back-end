"""Bounded operator acquisition retains failed originals without reviewing prefixes."""
import contextlib
from email.message import Message
import io
from pathlib import Path
import runpy
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import intake


class Response(io.BytesIO):
    def __init__(self,body,length=None):
        super().__init__(body);self.status=200;self.url='https://example.test/paper';self.headers=Message();self.headers['Content-Type']='application/pdf'
        if length is not None:self.headers['Content-Length']=str(length)


class IntakeResourceContracts(unittest.TestCase):
    def test_deadline_handler_interrupts_retrieval_and_restores_the_prior_signal_contract(self):
        with patch.object(signal,'signal') as install,patch.object(signal,'setitimer',return_value=(0,0)),patch.object(signal,'getsignal',return_value=signal.SIG_DFL):
            with self.assertRaisesRegex(TimeoutError,'40-second'),intake.retrieval_deadline():install.call_args.args[1](signal.SIGALRM,None)
            self.assertEqual(install.call_args.args,(signal.SIGALRM,signal.SIG_DFL))

    def test_invalid_resource_limits_do_not_create_an_output_or_attempt_download(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(intake.urllib.request,'build_opener') as opener:
            for limits in [dict(max_bytes=0),dict(max_bytes=10_000_001),dict(attempts=0),dict(attempts=3)]:
                with self.assertRaisesRegex(ValueError,'10 MB and two attempts'):intake.retrieve('https://example.test/paper',Path(directory)/'download',**limits)
            opener.assert_not_called();self.assertEqual(list(Path(directory).iterdir()),[])

    def test_declared_oversized_incomplete_and_non_pdf_responses_remain_unavailable_with_original_captures(self):
        for defect in ['declared','overflow','incomplete','not-pdf','unreadable']:
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as directory:
                raw=b'x'*11 if defect=='overflow' else b'Landing' if defect=='not-pdf' else b'%PDF-data';response=Response(raw,11 if defect=='declared' else 10 if defect=='incomplete' else None)
                if defect=='unreadable':response.read1=Mock(side_effect=OSError('Original transport unavailable'))
                opener=Mock();opener.open.return_value=response;target=Path(directory)/'download'
                with patch.object(intake.urllib.request,'build_opener',return_value=opener),patch.object(intake,'retrieval_deadline',side_effect=contextlib.nullcontext):result=intake.retrieve('https://example.test/paper',target,max_bytes=10,attempts=1)
                self.assertEqual(result['status'],'unavailable');self.assertIsNone(result['sha256']);self.assertFalse((target/'original.bin').exists())
                if defect in ['overflow','incomplete','not-pdf']:self.assertEqual((target/'attempt-1-response.bin').read_bytes(),raw)
                else:self.assertNotIn('captured_response_path',result['attempts'][0])

    def test_pdf_encryption_page_and_character_bounds_stop_before_a_text_packet_is_certified(self):
        for defect in ['encrypted','pages','characters']:
            reader=SimpleNamespace(is_encrypted=defect=='encrypted',pages=[Mock(extract_text=Mock(return_value='x'*2_000_001))] if defect=='characters' else [None]*301 if defect=='pages' else [])
            with self.subTest(defect=defect),patch('pypdf.PdfReader',return_value=reader),self.assertRaises(ValueError):intake._extract(b'Original PDF bytes')
        with tempfile.TemporaryDirectory() as directory,patch.object(intake.subprocess,'run') as process:
            root=Path(directory);source=root/'original.pdf';source.write_bytes(b'x'*10_000_001)
            with self.assertRaisesRegex(ValueError,'10 MB'):intake.text_packet(source,root/'derived.json')
            process.assert_not_called();self.assertFalse((root/'derived.json').exists())

    def test_parser_worker_rejects_an_oversized_stdin_before_calling_the_pdf_library(self):
        stderr=io.StringIO()
        with patch.object(sys,'argv',[intake.__file__,'--text-worker']),patch.object(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'x'*10_000_001))),patch('resource.setrlimit'),contextlib.redirect_stderr(stderr),self.assertRaises(SystemExit) as stopped:runpy.run_path(intake.__file__,run_name='__main__')
        self.assertEqual(stopped.exception.code,1);self.assertIn('Input byte limit exceeded.',stderr.getvalue())


if __name__=='__main__':unittest.main()
