"""Security contracts for environment-only secrets and job-scoped reports."""
import base64
import gzip
import importlib
import io
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

from benchmark_ingest import IngestBuffer, ingest_settings, require_persistence
import benchmark_ingest

SETTINGS = {"JUMPSERVE_INGEST_URL": "https://example.test/benchmarks/ingest",
            "JUMPSERVE_JOB_ID": "11111111-1111-4111-8111-111111111111", "JUMPSERVE_JOB_TOKEN": "a"*64}

class IngestionTests(unittest.TestCase):
    def test_no_embedded_credentials(self):
        for path in Path(__file__).parent.glob('*.py'):
            for token in re.findall(r'eyJ[\w-]+\.[\w-]+\.[\w-]+', path.read_text()):
                claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'==='))
                self.assertNotEqual(claims.get('role'), 'service_role', path.name)

    def test_missing_configuration_fails_before_network_setup(self):
        with patch.dict(os.environ, {}, clear=True):
            for module_name in ('netem_cubic_benchmark_hotnets', 'netem_cubic_benchmark_nines', 'netem_nines',
                                'netem_nines_many_clients', 'netem_nines_many_senders'):
                module = importlib.import_module(module_name)
                args = module.build_parser().parse_args([])
                self.assertEqual(args.supabase_service_role_key, '')
                with self.assertRaisesRegex(ValueError, 'SUPABASE_SERVICE_ROLE_KEY'):
                    module.orchestrator_mode(args)
                self.assertNotIn('--supabase-service-role-key', module.build_parser().format_help())

    def test_partial_ingest_configuration_never_falls_back_to_privileged_key(self):
        with patch.dict(os.environ, {"JUMPSERVE_JOB_TOKEN": "a"*64, "SUPABASE_SERVICE_ROLE_KEY":"test-only"}, clear=True):
            with self.assertRaises(ValueError):
                ingest_settings()

    def test_env_only_key_for_local_execution(self):
        with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY":"test-only"}, clear=True):
            from netem_cubic_benchmark_hotnets import build_parser
            args = build_parser().parse_args([])
            self.assertEqual(args.supabase_service_role_key,'test-only')
            require_persistence(args)

    def test_buffer_uploads_one_report_without_database_key(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            buffer = IngestBuffer()
            self.assertEqual(buffer.request('GET','congestion_control_algorithms','name=eq.cubic'),[{'id':1}])
            buffer.request('POST','emulated_parent_runs',payload=[{'number_of_clients':1}])
            buffer.request('POST','emulated_runs',payload=[{'client_number':1,'emulated_parent_run_id':1}])
            buffer.request('POST','emulated_snapshot_stats',payload=[{'emulated_run_id':1,'snapshot_index':0}])
            captured = []
            def respond(request, **kwargs):
                captured.append(request)
                return io.BytesIO(b'{"parent_run_id":2345,"run_ids":{"1":6789}}')
            with patch('urllib.request.urlopen',side_effect=respond):
                self.assertEqual(buffer.commit()['parent_run_id'],2345)
            self.assertEqual(len(captured),1)
            request = captured[0]
            self.assertEqual(request.full_url,SETTINGS['JUMPSERVE_INGEST_URL'])
            self.assertNotIn('Apikey',request.headers)
            body=json.loads(request.data)
            report=json.loads(gzip.decompress(base64.b64decode(body['report_gzip'])))
            self.assertEqual(report['snapshots'][0]['emulated_run_id'],1)
            self.assertNotIn(SETTINGS['JUMPSERVE_JOB_TOKEN'],json.dumps(report))
            with self.assertRaises(ValueError):
                buffer.request('DELETE','emulated_runs')

    def test_http_failures_do_not_echo_private_server_response(self):
        with patch.dict(os.environ,SETTINGS,clear=True):
            buffer=IngestBuffer()
            error=urllib.error.HTTPError(SETTINGS['JUMPSERVE_INGEST_URL'],403,'forbidden',{},io.BytesIO(b'private response'))
            with patch('urllib.request.urlopen',side_effect=error):
                with self.assertRaisesRegex(RuntimeError,r'^Benchmark ingestion rejected the report \(HTTP 403\)$'):
                    buffer.commit()

    def test_local_ids_are_stable_and_parent_or_raw_run_records_cannot_be_overwritten(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            buffer = IngestBuffer()
            for name in ['', 'CUBIC', 'cubic&select=*', 'a' * 33]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    buffer.request('GET', 'congestion_control_algorithms', urllib.parse.urlencode({'name': 'eq.' + name}))
            self.assertEqual(buffer.request('GET', 'congestion_control_algorithms', 'name=eq.cubic'), [{'id': 1}])
            self.assertEqual(buffer.request('GET', 'congestion_control_algorithms', 'name=eq.cubic'), [{'id': 1}])
            self.assertEqual(buffer.request('GET', 'congestion_control_algorithms', 'name=eq.bbr'), [{'id': 2}])
            parent = {'number_of_clients': 2, 'zero': 0}
            self.assertEqual(buffer.request('POST', 'emulated_parent_runs', payload=parent), [{'id': 1}])
            raw = {'raw_log': {'zero': 0, 'missing': None}}
            self.assertEqual(buffer.request('POST', 'runs', payload=raw), [raw])
            for table, payload in [('emulated_parent_runs', parent), ('runs', raw), ('runs', []), ('runs', [raw, raw]), ('private_relation', {})]:
                with self.subTest(table=table, payload=payload), self.assertRaisesRegex(ValueError, 'Unsupported'):
                    buffer.request('POST', table, payload=payload)
            self.assertEqual(buffer.report['parent'], parent)
            self.assertEqual(buffer.report['raw_run'], raw)

    def test_report_and_compressed_byte_limits_stop_before_any_network_request(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            buffer = IngestBuffer()
            for name in ['MAX_REPORT_BYTES', 'MAX_COMPRESSED_BYTES']:
                with self.subTest(limit=name), patch.object(benchmark_ingest, name, 1), patch('urllib.request.urlopen') as upload, self.assertRaisesRegex(ValueError, 'ingestion limit'):
                    buffer.commit()
                upload.assert_not_called()
            buffer.report['raw_run'] = {'value': float('nan')}
            with patch('urllib.request.urlopen') as upload, self.assertRaises(ValueError):
                buffer.commit()
            upload.assert_not_called()

    def test_transient_failures_retry_the_exact_original_report_and_preserve_job_identity(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            for failure in [TimeoutError('private timeout'), urllib.error.URLError('private transport'),
                            urllib.error.HTTPError(SETTINGS['JUMPSERVE_INGEST_URL'], 503, 'private upstream', {}, None)]:
                buffer = IngestBuffer()
                with self.subTest(failure=type(failure).__name__), patch('urllib.request.urlopen', side_effect=[failure, io.BytesIO(b'{"parent_run_id":12}')]) as upload, patch.object(benchmark_ingest.time, 'sleep') as sleep:
                    self.assertEqual(buffer.commit(), {'parent_run_id': 12})
                self.assertEqual(upload.call_count, 2)
                self.assertEqual(upload.call_args_list[0].args[0].data, upload.call_args_list[1].args[0].data)
                sleep.assert_called_once_with(1)

    def test_retry_exhaustion_has_exactly_three_attempts_and_never_exposes_private_failures(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            for failure, expected in [(TimeoutError('private timeout'), 'could not be reached'),
                                      (urllib.error.URLError('private transport'), 'could not be reached'),
                                      (urllib.error.HTTPError(SETTINGS['JUMPSERVE_INGEST_URL'], 503, 'private upstream', {}, None), 'HTTP 503')]:
                with self.subTest(failure=type(failure).__name__), patch('urllib.request.urlopen', side_effect=failure) as upload, patch.object(benchmark_ingest.time, 'sleep') as sleep, self.assertRaisesRegex(RuntimeError, expected) as caught:
                    IngestBuffer().commit()
                self.assertEqual(upload.call_count, 3)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
                self.assertNotIn('private', str(caught.exception))

if __name__=='__main__':
    unittest.main()
