"""Private HTTP, immutable artifacts and fenced pagination without external I/O."""
import base64
import copy
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
import urllib.error
import uuid
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artifacts
import database
import store
from test_real_world import job

IDENTITY = '3705a384-081d-4d8c-b75d-7e576c149151'


class Response(io.BytesIO):
    def __init__(self, raw, headers=None):
        super().__init__(raw); self.headers = headers or {}


class PrivateHTTP(unittest.TestCase):
    def setUp(self):
        database.service_key.cache_clear(); self.addCleanup(database.service_key.cache_clear)
        self.enterContext(patch.dict(os.environ, SUPABASE_URL='https://synthetic.example.test/', SUPABASE_SECRET_ARN='synthetic-service-secret'))
        self.enterContext(patch.object(database.ssl, 'create_default_context', return_value='synthetic-tls-context'))

    def test_secret_is_fetched_once_from_the_role_and_never_written_to_the_body(self):
        import cloud
        aws = MagicMock(); aws.get_secret_value.return_value = {'SecretString': 'synthetic-service-key'}
        with patch.object(cloud, 'client', return_value=aws) as client:
            self.assertEqual(database.service_key(), 'synthetic-service-key'); self.assertEqual(database.service_key(), 'synthetic-service-key')
        client.assert_called_once_with('secretsmanager', 'us-east-1'); aws.get_secret_value.assert_called_once_with(SecretId='synthetic-service-secret')
        with patch.object(database.urllib.request, 'urlopen', return_value=Response(b'{"original_zero":0,"missing":null}')) as open_request:
            result = database.rest('real_world_reports', 'POST', {'zero': 0, 'missing': None}, {'job_id': 'eq.' + IDENTITY}, {'Prefer': 'return=representation'})
        request = open_request.call_args.args[0]
        self.assertEqual(result, {'original_zero': 0, 'missing': None}); self.assertEqual(json.loads(request.data), {'zero': 0, 'missing': None})
        self.assertEqual(request.full_url, 'https://synthetic.example.test/rest/v1/real_world_reports?job_id=eq.' + IDENTITY)
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-service-key'); self.assertEqual(request.get_header('Prefer'), 'return=representation')
        self.assertEqual(open_request.call_args.kwargs, {'timeout': 15, 'context': 'synthetic-tls-context'})
        self.assertNotIn(b'synthetic-service-key', request.data)

    def test_original_bytes_empty_body_and_both_response_size_fences(self):
        with patch.object(database, 'service_key', return_value='synthetic-key'):
            for raw, decode, expected in [(b'\x00\xff', True, b'\x00\xff'), (b'', False, None), (b'false', False, False)]:
                with self.subTest(raw=raw), patch.object(database.urllib.request, 'urlopen', return_value=Response(raw)) as open_request:
                    self.assertEqual(database.request('/storage/v1/object/private', 'POST', b'\x00\xff', raw=decode), expected)
                    self.assertEqual(open_request.call_args.args[0].data, b'\x00\xff')
            for response in [Response(b'not-read', {'Content-Length': '11'}), Response(b'01234567890')]:
                with patch.object(database.urllib.request, 'urlopen', return_value=response), self.assertRaisesRegex(ValueError, 'interactive analysis limits'):
                    database.request('/storage/v1/object/private', max_bytes=10)
            with patch.object(database.urllib.request, 'urlopen') as open_request, self.assertRaises(ValueError):
                database.request('/rest/v1/example', 'POST', {'invalid': float('nan')})
            open_request.assert_not_called()
        with patch.dict(os.environ, SUPABASE_URL='http://synthetic.example.test'), self.assertRaisesRegex(ValueError, 'HTTPS'): database.base_url()
        with patch.object(database, 'rest', return_value='rpc-result') as rest:
            self.assertEqual(database.rpc('real_world_cancel_job', {'target': IDENTITY}), 'rpc-result')
        rest.assert_called_once_with('rpc/real_world_cancel_job', 'POST', {'target': IDENTITY})

    def test_storage_absence_and_http_errors_retain_classification_without_leaking_response_details(self):
        missing = [(404, {}), (400, {'code': 'NoSuchKey'}), (400, {'code': 'NoSuchObject'}), (400, {'statusCode': 404})]
        failures = [(503, {'message': 'PRIVATE SQL and synthetic-key'}), (400, {'statusCode': 403}), (404, {'code': 'NoSuchKey'})]
        with patch.object(database, 'service_key', return_value='synthetic-key'):
            for code, detail in missing:
                error = urllib.error.HTTPError('https://private.invalid/signed-secret', code, 'PRIVATE', {}, io.BytesIO(json.dumps(detail).encode()))
                with self.subTest(detail=detail), patch.object(database.urllib.request, 'urlopen', side_effect=error), self.assertRaises(database.StorageMissing):
                    database.request('/storage/v1/object/private')
            for code, detail in failures:
                error = urllib.error.HTTPError('https://private.invalid/signed-secret', code, 'PRIVATE', {}, io.BytesIO(json.dumps(detail).encode()))
                with self.subTest(detail=detail), patch.object(database.urllib.request, 'urlopen', side_effect=error), self.assertRaisesRegex(RuntimeError, '^Supabase request failed \\(HTTP ' + str(code) + '\\)\\.$'):
                    database.request('/rest/v1/private')
            for raw in [b'PRIVATE non-JSON response', b'\xff', b'[]', b'null', b'0']:
                error = urllib.error.HTTPError('https://private.invalid/signed-secret', 502, 'PRIVATE', {}, io.BytesIO(raw))
                with self.subTest(raw=raw), patch.object(database.urllib.request, 'urlopen', side_effect=error), self.assertRaisesRegex(RuntimeError, '^Supabase request failed \\(HTTP 502\\)\\.$'):
                    database.request('/storage/v1/object/private')

    def test_ca_selection_uses_explicit_or_system_trust_and_never_disables_tls(self):
        for explicit, system, expected in [('synthetic-ca.pem', False, 'synthetic-ca.pem'), ('', True, '/etc/ssl/cert.pem'), ('', False, None)]:
            with self.subTest(explicit=explicit, system=system), patch.dict(os.environ, SSL_CERT_FILE=explicit), patch.object(database.os.path, 'isfile', return_value=system), patch.object(database.ssl, 'create_default_context', return_value='verified-context') as context, patch.object(database, 'service_key', return_value='synthetic-key'), patch.object(database.urllib.request, 'urlopen', return_value=Response(b'{}')):
                database.request('/rest/v1/example'); context.assert_called_once_with(cafile=expected)


class ArtifactBoundaries(unittest.TestCase):
    def setUp(self):
        self.raw = b'Original measurement: zero=0, missing=null\n'; self.sha = hashlib.sha256(self.raw).hexdigest()
        self.source = dict(node_name='server', object_path=IDENTITY + '/archive/server/' + self.sha + '.json', sha256=self.sha, bytes=len(self.raw), legacy_version_id='retained-legacy-version')
        self.enterContext(patch.object(database, 'base_url', return_value='https://synthetic.example.test'))

    def test_staged_and_archived_reads_verify_original_bytes_and_distinguish_missing_evidence(self):
        with patch.object(database, 'rest', return_value=[self.source]) as rest:
            self.assertEqual(artifacts.records(IDENTITY), [self.source])
        rest.assert_called_once_with('real_world_artifacts', params={'job_id': 'eq.' + IDENTITY, 'select': '*'})
        with patch.object(artifacts, 'records', return_value=[self.source]), patch.object(database, 'request', return_value=self.raw) as request:
            raw, metadata = artifacts.read(IDENTITY, 'server', max_bytes=1234)
        self.assertEqual(raw, self.raw); self.assertEqual(metadata, dict(name='server.json', sha256=self.sha, bytes=len(self.raw), version_id='retained-legacy-version'))
        request.assert_called_once_with('/storage/v1/object/real-world-results/' + self.source['object_path'], raw=True, max_bytes=1234)
        with patch.object(database, 'request', return_value=self.raw):
            self.assertIsNone(artifacts.read(IDENTITY, 'server', [])[1]['version_id'])
        for change in [dict(sha256='0' * 64), dict(bytes=len(self.raw) + 1)]:
            with self.subTest(change=change), patch.object(database, 'request', return_value=self.raw), self.assertRaisesRegex(RuntimeError, 'checksum differs'):
                artifacts.read(IDENTITY, 'server', [dict(self.source, **change)])
        with patch.object(database, 'request', side_effect=database.StorageMissing):
            self.assertIsNone(artifacts.read(IDENTITY, 'server', []))
            with self.assertRaisesRegex(RuntimeError, 'Archived measurement object is missing'): artifacts.read(IDENTITY, 'server', [self.source])

    def test_freezing_skips_retained_objects_and_only_archives_available_originals(self):
        value = job(); value['job_id'] = IDENTITY
        with patch.object(artifacts, 'records', return_value=[self.source]), patch.object(artifacts, 'read', side_effect=[(self.raw, {}), None, (b'', {})]) as read, patch.object(artifacts, 'archive') as archive:
            artifacts.freeze(value)
        self.assertEqual([call.args[1] for call in read.call_args_list], ['bottleneck', 'receiver-1', 'receiver-2'])
        self.assertEqual(archive.call_args_list[0].args, (IDENTITY, 'bottleneck', self.raw)); self.assertEqual(archive.call_args_list[1].args, (IDENTITY, 'receiver-2', b''))
        with patch.object(database, 'request') as request, patch.object(database, 'rest') as rest:
            artifacts.archive(IDENTITY, 'server', self.raw, 'retained-legacy-version')
        self.assertEqual(request.call_args.args, ('/storage/v1/object/real-world-results/' + self.source['object_path'], 'POST', self.raw, {'x-upsert': 'true'}))
        self.assertEqual(rest.call_args.args[2], dict(job_id=IDENTITY, **self.source)); self.assertEqual(rest.call_args.kwargs, {'headers': {'Prefer': 'resolution=merge-duplicates'}})

    def test_download_capabilities_cover_only_known_test_nodes_with_five_minute_expiry(self):
        value = job(); value['job_id'] = IDENTITY; requests = []
        def request(path, method, body, headers=None):
            requests.append((path, method, body, headers))
            if '/object/list/' in path: return [{'name': 'receiver-1.json', 'id': 'staged'}, {'name': 'private-unrelated.json', 'id': 'never-sign'}, {'name': 'receiver-2.json', 'id': None}, {'name': 'archive', 'id': None}]
            if '/object/upload/sign/' in path: return {'url': '/object/upload/synthetic-capability'}
            return {'signedURL': '/object/sign/synthetic-capability'}
        with patch.object(artifacts, 'records', return_value=[self.source]), patch.object(database, 'request', side_effect=request):
            result = artifacts.downloads(value); self.assertEqual(artifacts.signed_upload(IDENTITY, 'server'), 'https://synthetic.example.test/storage/v1/object/upload/synthetic-capability')
        self.assertEqual([entry['name'] for entry in result['artifacts']], ['server.json', 'receiver-1.json'])
        self.assertTrue(all(entry['url'] == 'https://synthetic.example.test/storage/v1/object/sign/synthetic-capability' for entry in result['artifacts']))
        self.assertEqual(requests[0], ('/storage/v1/object/list/real-world-results', 'POST', {'prefix': IDENTITY, 'limit': 100, 'offset': 0}, None))
        self.assertEqual([item[2] for item in requests[1:3]], [{'expiresIn': 300}, {'expiresIn': 300}])
        self.assertEqual(requests[-1][3], {'x-upsert': 'true'}); self.assertNotIn('private-unrelated', repr(requests))
        self.assertEqual(artifacts.object_path('folder/Ω and ?#.json'), 'real-world-results/folder/%CE%A9%20and%20%3F%23.json')


class FencedPersistence(unittest.TestCase):
    def test_load_and_detail_handle_missing_rows_and_invalid_identities_without_private_reads(self):
        with patch.object(database, 'rest', return_value=[]) as rest:
            for invalid in [None, {}, 'invalid']: self.assertIsNone(store.load(invalid))
            rest.assert_not_called(); self.assertIsNone(store.load(IDENTITY)); self.assertIsNone(store.detail(IDENTITY))
        value = job(); value.update(job_id=IDENTITY, cancel_requested=False, upload_url='private-capability')
        with patch.object(database, 'rest', return_value=[{'record': value, 'cancel_requested': True}]) as rest:
            self.assertTrue(store.load(IDENTITY)['cancel_requested'])
            self.assertEqual(rest.call_args.kwargs['params']['select'], 'record,cancel_requested')
        history = [{'id': 1, 'status': 'running', 'started_at': 'retained-original-time'}]
        with patch.object(database, 'rest', return_value=[{'record': value, 'status_history': history}]) as rest:
            detail = store.detail(IDENTITY)
        self.assertEqual(detail['status_history'], history); self.assertNotIn('upload_url', detail)
        self.assertEqual(rest.call_args.kwargs['params']['status_history.order'], 'id.asc')

    def test_atomic_writes_require_the_original_lease_and_do_not_overwrite_cancellation(self):
        value = job(); value.update(job_id=IDENTITY, _lease_token='original-fence', lease_until=123, cancel_requested=True)
        original = copy.deepcopy(value)
        with patch.object(database, 'rpc', return_value=True) as rpc, patch.object(store.time, 'time', return_value=999):
            store.create(value); self.assertTrue(rpc.call_args.args[1]['create_only'])
            store.save(value); payload = rpc.call_args.args[1]
            self.assertEqual(payload['token'], 'original-fence'); self.assertFalse(payload['create_only']); self.assertEqual(payload['payload']['updated_at'], 999)
            for private in ['_lease_token', 'lease_until', 'cancel_requested']: self.assertNotIn(private, payload['payload'])
            self.assertTrue(payload['visible']['cancel_requested'])
            self.assertTrue(store.claim(IDENTITY)); store.release(IDENTITY, 'original-fence'); store.cancel(IDENTITY)
            self.assertEqual(rpc.call_args.args, ('real_world_cancel_job', {'target': IDENTITY}))
        with patch.object(database, 'rpc', return_value=False), self.assertRaisesRegex(RuntimeError, 'lease expired'): store.save(value)
        self.assertEqual(value['_lease_token'], original['_lease_token']); self.assertTrue(value['cancel_requested'])
        self.assertEqual(store.native({'count': Decimal('2'), 'fraction': Decimal('2.5')}), {'count': 2, 'fraction': 2.5})

    def test_keyset_pagination_uses_exact_owner_scope_and_never_exposes_the_fifty_first_row(self):
        rows = [{'record': dict(job(), job_id=str(uuid.uuid5(uuid.NAMESPACE_URL, str(index))), created_at=1000 - index, schema_version=1), 'cancel_requested': index % 2 == 0} for index in range(51)]
        for owner in [None, 'synthetic-owner']:
            with self.subTest(owner=owner), patch.object(database, 'rest', return_value=rows) as rest:
                first = store.page(owner=owner); self.assertEqual(len(first['tests']), 50)
                cursor = json.loads(base64.urlsafe_b64decode(first['cursor'])); self.assertEqual(cursor['job_id'], rows[49]['record']['job_id'])
                self.assertEqual(cursor['created_at'], 951)
                second = store.page(cursor=first['cursor'], owner=owner)
                table = 'real_world_jobs' if owner else 'real_world_runs'; self.assertEqual(rest.call_args.args, (table,))
                params = rest.call_args.kwargs['params']; self.assertEqual(params['limit'], '51')
                self.assertEqual(params['or'], '(created_at.lt.951,and(created_at.eq.951,job_id.lt.' + cursor['job_id'] + '))')
                if owner: self.assertEqual(params['owner'], 'eq.synthetic-owner'); self.assertTrue(second['tests'][0]['cancel_requested'])
                else: self.assertNotIn('owner', params)
        with patch.object(database, 'rest', return_value=[]): self.assertEqual(store.page(), {'tests': [], 'cursor': None})

    def test_forged_cursor_cannot_cross_owner_schema_or_predicate_boundaries(self):
        for owner in [None, 'synthetic-owner']:
            good = dict(job_id=IDENTITY, created_at=123, **({'owner': owner} if owner else {'schema_version': 1}))
            bad = [b'not-base64', json.dumps([]).encode(), json.dumps(dict(good, extra=True)).encode(), json.dumps(dict(good, created_at=True)).encode(), json.dumps(dict(good, job_id='x),or(private.eq.true)')).encode(), json.dumps(dict(good, **({'owner': 'other-owner'} if owner else {'schema_version': 2}))).encode()]
            for raw in bad:
                cursor = base64.urlsafe_b64encode(raw).decode()
                with self.subTest(owner=owner, raw=raw), patch.object(database, 'rest') as rest, self.assertRaisesRegex(ValueError, 'Invalid pagination cursor'): store.page(cursor, owner)
                rest.assert_not_called()

    def test_expired_jobs_use_keyset_batches_while_the_active_set_is_shrinking(self):
        first = [{'job_id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'expired-' + str(i)))} for i in range(50)]
        with patch.object(database, 'rpc', side_effect=[first, [{'job_id': IDENTITY}]]) as rpc:
            self.assertEqual(list(store.expired()), [row['job_id'] for row in first] + [IDENTITY])
        self.assertEqual(rpc.call_args_list[0].args, ('real_world_reap_candidates', {'after_id': None})); self.assertEqual(rpc.call_args_list[1].args, ('real_world_reap_candidates', {'after_id': first[-1]['job_id']}))


if __name__ == '__main__': unittest.main()
