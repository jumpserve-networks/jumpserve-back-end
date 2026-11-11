"""Persistence boundaries with original bytes and synthetic service adapters."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.parse
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import store
from workflow import PUBLIC_KINDS, canonical, digest

def uid(): return str(uuid.uuid4())


class PersistenceOperations(unittest.TestCase):
    def setUp(self):
        self.study, self.actor, self.identity = uid(), uid(), uid(); store.service_key.cache_clear(); self.addCleanup(store.service_key.cache_clear)
        self.enterContext(patch.dict(os.environ, {'SUPABASE_URL': 'https://' + store.PROJECT + '.supabase.co', 'SUPABASE_SERVICE_ROLE_KEY': 'synthetic-private-key', 'SUPABASE_ANON_KEY': 'synthetic-public-key'}, clear=True))

    def test_service_credential_is_cached_and_external_secret_is_scoped_to_the_fixed_account(self):
        boto = MagicMock(); boto.client.return_value.get_secret_value.return_value = {'SecretString': 'synthetic-role-secret'}
        with patch.dict(sys.modules, {'boto3': boto}):
            self.assertEqual(store.service_key(), 'synthetic-private-key'); boto.client.assert_not_called(); store.service_key.cache_clear()
            del os.environ['SUPABASE_SERVICE_ROLE_KEY']; arn = 'arn:aws:secretsmanager:us-east-1:' + store.ACCOUNT + ':secret:synthetic'; os.environ['SUPABASE_SECRET_ARN'] = arn
            self.assertEqual(store.service_key(), 'synthetic-role-secret'); self.assertEqual(store.service_key(), 'synthetic-role-secret'); boto.client.assert_called_once_with('secretsmanager', region_name='us-east-1'); boto.client.return_value.get_secret_value.assert_called_once_with(SecretId=arn)
            store.service_key.cache_clear(); os.environ['SUPABASE_SECRET_ARN'] = arn.replace(store.ACCOUNT, '000000000000')
            with self.assertRaisesRegex(store.StoreError, 'account mismatch'): store.service_key()
            self.assertEqual(boto.client.call_count, 1)

    def test_http_adapter_preserves_exact_binary_and_canonical_json_payloads_and_keeps_keys_in_headers(self):
        calls = []
        def open(request, **options):
            calls.append((request, options)); return io.BytesIO(b'{"stored":true}' if request.get_method() == 'POST' else b'')
        with patch.object(store.urllib.request, 'urlopen', side_effect=open):
            self.assertEqual(store.request('/synthetic', 'POST', {'zero': 0}), {'stored': True}); request, options = calls[-1]; self.assertEqual(request.data, b'{"zero":0}'); self.assertEqual(request.get_header('Content-type'), 'application/json'); self.assertEqual(options, {'timeout': 12}); self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-private-key'); self.assertNotIn('synthetic-private-key', request.full_url)
            self.assertEqual(store.request('/synthetic', 'POST', b'Original binary\x00', raw=True, timeout=4), b'{"stored":true}'); request, options = calls[-1]; self.assertEqual(request.data, b'Original binary\x00'); self.assertEqual(request.get_header('Content-type'), 'application/octet-stream'); self.assertEqual(options, {'timeout': 4})
            self.assertIsNone(store.request('/synthetic', public=True)); self.assertIsNone(calls[-1][0].data); self.assertEqual(calls[-1][0].get_header('Apikey'), 'synthetic-public-key')

    def test_wrong_project_oversized_response_and_private_service_error_never_become_confirmed_storage(self):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://other.supabase.co'}), patch.object(store.urllib.request, 'urlopen') as open:
            with self.assertRaisesRegex(store.StoreError, 'project mismatch'): store.request('/synthetic')
            open.assert_not_called()
        with patch.object(store.urllib.request, 'urlopen', return_value=io.BytesIO(b'x' * 20000001)), self.assertRaisesRegex(store.StoreError, 'bounded size'): store.request('/synthetic')
        failure = urllib.error.HTTPError('https://private.example.test/signed?key=private', 503, 'private owner SQL details', {'secret': 'private'}, None)
        with patch.object(store.urllib.request, 'urlopen', side_effect=failure), self.assertRaises(store.StoreError) as error: store.request('/synthetic', 'POST', {'private': True})
        self.assertIn('HTTP 503', str(error.exception)); self.assertIn('Storage is not confirmed', str(error.exception)); self.assertNotIn('private', str(error.exception)); self.assertIsNone(error.exception.__cause__)

    def test_private_relation_projections_remain_explicit_and_browser_reads_are_denied(self):
        private = ['paper_uploads', 'paper_reviews', 'study_owners', 'audit_events', 'profiles', 'autonomy_workflows', 'autonomy_tasks', 'autonomy_turns', 'autonomy_repairs', 'autonomy_repair_events', 'autonomy_events', 'model_dispatches', 'model_reconciliations', 'builder_call_reconciliations', 'queued_dispatch_reconciliations', 'builder_calls', 'builder_packages', 'queue_jobs', 'queue_events', 'prepared_plans', 'idea_packets', 'shared_campaigns', 'shared_campaign_members', 'claim_designs', 'idea_runs', 'idea_readiness', 'idea_campaign_links']
        with patch.object(store, 'request', return_value=[]) as read:
            for kind in private:
                with self.subTest(kind=kind):
                    store.rows(kind, {'limit': 2}); query = urllib.parse.parse_qs(urllib.parse.urlsplit(read.call_args.args[0]).query); self.assertEqual(query['limit'], ['2']); self.assertNotIn('*', query['select'][0]); self.assertFalse(read.call_args.kwargs['public'])
                    if kind == 'study_owners': self.assertNotIn('order', query); self.assertEqual(set(query['select'][0].split(',')), {'study_id', 'owner_id'})
                    elif kind == 'profiles': self.assertEqual(query['order'], ['id.asc']); self.assertEqual(query['select'], ['id,first_name,last_name'])
                    elif kind in ['autonomy_workflows', 'paper_reviews']: self.assertEqual(query['order'], ['created_at.asc,study_id.asc'])
                    elif kind in ['model_reconciliations', 'queued_dispatch_reconciliations']: self.assertEqual(query['order'], ['created_at.asc,dispatch_id.asc'])
                    elif kind == 'builder_call_reconciliations': self.assertEqual(query['order'], ['created_at.asc,call_id.asc'])
                    with self.assertRaises(ValueError): store.rows(kind, public=True)
            count = read.call_count
            with self.assertRaisesRegex(ValueError, 'Unsupported research relation'): store.rows('unregistered_relation')
            self.assertEqual(read.call_count, count)

    def test_public_projections_cast_scientific_decimals_before_transport_and_preserve_zero_null_and_text(self):
        with patch.object(store, 'request', return_value=[{'value': '1.234567890123456789'}, {'value': None}, {'value': 0}]) as read:
            for kind in ['measurements', 'published_values']:
                result = store.rows(kind, {'select': 'id,value,status', 'order': 'id.desc'}, public=True); self.assertEqual(result, [{'value': '1.234567890123456789'}, {'value': None}, {'value': '0'}]); query = urllib.parse.parse_qs(urllib.parse.urlsplit(read.call_args.args[0]).query); self.assertEqual(query['select'], ['id,value::text,status']); self.assertEqual(query['order'], ['id.desc'])
        with patch.object(store, 'request', return_value=[]) as read:
            store.rows('studies', public=True); query = urllib.parse.parse_qs(urllib.parse.urlsplit(read.call_args.args[0]).query); self.assertNotIn('owner_id', query['select'][0]); self.assertNotIn('email', query['select'][0]); self.assertTrue(read.call_args.kwargs['public'])

    def test_only_declared_rpc_operations_reach_the_postgrest_adapter(self):
        operations = ['research_intake_control', 'research_intake_dispatch', 'research_controller_dispatch_reconcile', 'research_autonomy_runtime_observe', 'autonomy_budget_snapshot', 'research_model_reconcile', 'research_model_reconciliation_candidates', 'research_builder_call_reconcile', 'research_model_reconcile_queued', 'research_autonomy_repair', 'research_autonomy_repair_candidates', 'research_builder_call', 'research_builder_package', 'research_shared_campaign_bind', 'research_model_dispatch', 'research_autonomy_resume_turns', 'research_autonomy_budget_candidates', 'research_autonomy_resume_budget', 'research_paper_upload', 'research_paper_review', 'research_autonomy_resume_context', 'research_autonomy_context_candidates', 'research_initialize_claim_design', 'research_reuse_claim_packet', 'research_autonomy_control', 'research_create_study', 'research_network_launch']
        with patch.object(store, 'request', return_value={'recorded': True}) as request:
            for name in operations:
                payload = {'original': 'unchanged'}; self.assertEqual(store.rpc(name, payload), {'recorded': True}); request.assert_called_with('/rest/v1/rpc/' + name, 'POST', payload)
            count = request.call_count
            with self.assertRaisesRegex(ValueError, 'Unsupported research operation'): store.rpc('execute_arbitrary_sql', {})
            self.assertEqual(request.call_count, count)

    def test_deleted_assessments_cannot_reenter_public_owner_lists_or_shared_snapshots(self):
        import api
        with patch.object(store,'request',return_value=[]) as read:
            # Caller options cannot undo the application-wide visibility filter.
            store.rows('studies',{'deleted_at':'not.is.null'},public=True)
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(read.call_args.args[0]).query)
            self.assertEqual(query['deleted_at'],['is.null'])
            for suffix,query in [('',{}),('/queue',{'public':'1'}),('/queue',{'public':'1','records':'sources'}),('/autonomy',{'public':'1','task':uid()})]:
                event=dict(rawPath='/research/studies/'+self.study+suffix,requestContext={'http':{'method':'GET'}},queryStringParameters=query)
                with self.subTest(suffix=suffix,query=query),self.assertRaises(api.HttpError) as error:api.dispatch(event)
                self.assertEqual(error.exception.status,404)
            self.assertFalse(store.owns(self.study,self.actor))
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(read.call_args.args[0]).query)
            self.assertEqual(query['active_study.deleted_at'],['is.null'])
            self.assertIn('active_study:research_studies!inner(id)',query['select'][0])

    def test_validated_bundle_is_atomic_and_owner_check_uses_both_current_identities(self):
        record = dict(id=uid(), identity='Synthetic configuration', details={'zero': 0}, input_versions=[{'identity': 'original', 'sha256': 'a' * 64}], requested_resources={'measured': None}); original = canonical(record)
        with patch.object(store, 'rpc', return_value={'recorded': True}) as rpc:
            store.append(self.study, self.actor, 'configurations', record); self.assertEqual(rpc.call_args.args[1]['p_record'], record)
            store.append_bundle(self.study, self.actor, [{'kind': 'configurations', 'record': record}]); self.assertEqual(rpc.call_args.args[1]['p_records'], [{'kind': 'configurations', 'record': record}]); self.assertEqual(canonical(record), original)
            count = rpc.call_count
            with self.assertRaises(ValueError): store.append_bundle(self.study, self.actor, [{'kind': 'configurations', 'record': record}, {'kind': 'unknown', 'record': record}])
            self.assertEqual(rpc.call_count, count)
        with patch.object(store, 'rows', side_effect=[[], [{'owner_id': self.actor}]]) as rows:
            self.assertFalse(store.owns(self.study, self.actor)); self.assertTrue(store.owns(self.study, self.actor)); rows.assert_called_with('study_owners', {'study_id': 'eq.' + self.study, 'owner_id': 'eq.' + self.actor, 'select': 'study_id,active_study:research_studies!inner(id)', 'active_study.deleted_at':'is.null','limit': 1})

    def test_artifact_upload_failures_never_overwrite_existing_bytes_or_reuse_changed_record_identity(self):
        args = (self.study, self.actor, b'Original retained bytes', self.identity, 'text/plain', {'scope': 'original'})
        for responses in [[store.StoreError('HTTP 503')], [store.StoreError('HTTP 404'), store.StoreError('HTTP 503')]]:
            with patch.object(store, 'request', side_effect=responses) as request, self.subTest(responses=len(responses)), self.assertRaisesRegex(store.StoreError, 'HTTP 503'): store.artifact(*args)
            self.assertEqual(request.call_count, len(responses))
        with patch.object(store, 'request', side_effect=[store.StoreError('HTTP 400'), None, args[2]]) as request, patch.object(store, 'rows', return_value=[]):
            record = store.artifact(*args); self.assertEqual(request.call_args_list[1].args[1:], ('POST', args[2])); self.assertEqual(record['storage_path'], self.study + '/' + digest(args[2]))
        with patch.object(store, 'request', return_value=args[2]), patch.object(store, 'rows', return_value=[dict(record, provenance={'scope': 'changed'})]), self.assertRaisesRegex(store.StoreError, 'different bytes or provenance'): store.artifact(*args)

    def test_artifact_reads_validate_identity_size_length_and_hash_before_returning_original_bytes(self):
        raw = b'Original retained input'; record = dict(byte_count=len(raw), sha256=digest(raw), storage_path=self.study + '/' + digest(raw))
        with patch.object(store, 'rows', return_value=[record]), patch.object(store, 'request', return_value=raw) as request:
            self.assertEqual(store.artifact_bytes(self.study, self.identity, maximum=len(raw)), raw); request.assert_called_once_with('/storage/v1/object/' + store.BUCKET + '/' + record['storage_path'], raw=True)
        for rows, message in [([], 'unavailable'), ([dict(record, byte_count=len(raw) + 1)], 'exceeds the bound'), ([dict(record, storage_path='other/' + digest(raw))], 'invalid content identity')]:
            with patch.object(store, 'rows', return_value=rows), patch.object(store, 'request') as request, self.subTest(rows=rows), self.assertRaisesRegex(store.StoreError, message): store.artifact_bytes(self.study, self.identity, maximum=len(raw))
            request.assert_not_called()
        for changed in [raw + b'changed', b'X' * len(raw)]:
            with patch.object(store, 'rows', return_value=[record]), patch.object(store, 'request', return_value=changed), self.subTest(changed=changed), self.assertRaisesRegex(store.StoreError, 'hash differs'): store.artifact_bytes(self.study, self.identity)

    def test_queue_definition_identity_and_input_bounds_never_reconstruct_or_round_original_json(self):
        raw = b'{ "value": 1.234567890123456789, "zero": 0 }\n'; request = {'raw_input': raw.decode(), 'input_sha256': digest(raw)}; original = copy.deepcopy(request)
        self.assertEqual(store.queue_input_bytes(self.study, {'request': request, 'definition_sha256': digest(canonical(request))}), raw); self.assertEqual(request, original)
        for job, message in [({'request': request, 'definition_sha256': 'a' * 64}, 'definition hash differs'), ({'request': {'raw_input': 'x' * 256001}}, 'retained input bound'), ({'request': {}}, 'Original queue input is unavailable')]:
            with self.subTest(message=message), self.assertRaises((ValueError, store.StoreError)) as error: store.queue_input_bytes(self.study, job)
            self.assertIn(message, str(error.exception))
        for definition in [None, [], 'not a request object']:
            with self.subTest(definition=definition), self.assertRaisesRegex(ValueError, 'definition is unavailable'): store.queue_input_bytes(self.study, {'request': definition})
        for inline in ['', 0, [], b'Non-text transport']:
            with self.subTest(inline=inline), self.assertRaisesRegex(ValueError, 'original UTF-8 text'): store.queue_input_bytes(self.study, {'request': {'raw_input': inline}})

    def test_snapshot_pagination_keeps_one_boundary_and_never_silently_truncates_or_mixes_publications(self):
        boundary = '2026-11-01T00:00:00Z'; calls = []
        def rows(kind, params=None, public=False):
            calls.append((kind, params, public))
            if kind == 'studies': return [{'id': self.study}]
            if kind == 'claims': return [{'id': 'claim-' + str(i)} for i in range(1000)] if params['offset'] == 0 else [{'id': 'claim-1000'}]
            return []
        with patch.object(store, 'rows', side_effect=rows), patch.object(store, 'now', return_value=boundary): result = store.snapshot(self.study, owner=True, maximum=1001)
        self.assertEqual(result['coverage']['claims'], 1001); self.assertEqual(len(result['records']['claims']), 1001); self.assertTrue(all(not public for _, _, public in calls)); self.assertEqual([params['offset'] for kind, params, _ in calls if kind == 'claims'], [0, 1000]); self.assertTrue(all(params.get('created_at') == 'lte.' + boundary for kind, params, _ in calls if kind != 'studies'))
        with patch.object(store, 'rows', side_effect=rows), self.assertRaisesRegex(store.StoreError, 'No truncated dataset'): store.snapshot(self.study, owner=True, maximum=1000)

    def test_absent_unpublished_and_racing_public_snapshots_are_never_returned_as_complete(self):
        with patch.object(store, 'rows', return_value=[]): self.assertIsNone(store.snapshot(self.study))
        for publication in [[], [{'status': 'draft'}]]:
            with patch.object(store, 'rows', side_effect=[[{'id': self.study}], publication]): self.assertIsNone(store.snapshot(self.study))
        current = dict(id=uid(), status='published', record_ids={kind: [] for kind in PUBLIC_KINDS})
        for latest in [[], [dict(current, id=uid())]]:
            def rows(kind, params=None, public=False):
                self.assertTrue(public)
                if kind == 'studies': return [{'id': self.study}]
                if kind == 'publications' and 'offset' not in params:
                    self.publication_reads += 1; return [current] if self.publication_reads == 1 else latest
                return []
            self.publication_reads = 0
            with patch.object(store, 'rows', side_effect=rows), self.subTest(latest=latest), self.assertRaisesRegex(store.StoreError, 'Publication changed during retrieval'): store.snapshot(self.study)


if __name__ == '__main__': unittest.main()
