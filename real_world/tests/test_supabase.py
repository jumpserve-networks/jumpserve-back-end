"""Storage migration regressions, all offline. SQL races are tested in infra."""
import base64
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artifacts
import controller
import database
import migrate_supabase
import reports
import store
from test_real_world import job
from test_reports import fixture

ID = '00000000-0000-4000-8000-000000000001'


class SupabaseTests(unittest.TestCase):
    def test_corrupt_cursor_and_cross_owner_cursor_never_query(self):
        cursors = ['bad', {'job_id': ID, 'created_at': 1, 'owner': 'other'},
                   {'job_id': ID + ',owner.eq.secret', 'created_at': 1, 'owner': 'me'},
                   {'job_id': ID, 'created_at': True, 'owner': 'me'}]
        for cursor in cursors:
            if isinstance(cursor, dict):
                cursor = base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode()
            with patch.object(database, 'rest') as request, self.assertRaises(ValueError):
                store.page(cursor, 'me')
            request.assert_not_called()

    def test_same_timestamp_pagination_and_cancellation_visibility(self):
        rows = [{'record': dict(job(), job_id=ID, schema_version=1), 'cancel_requested': True}] * 51
        with patch.object(database, 'rest', return_value=rows) as request:
            page = store.page(owner='me')
            self.assertEqual(len(page['tests']), 50)
            self.assertTrue(page['tests'][0]['cancel_requested'])
            store.page(page['cursor'], 'me')
            query = request.call_args.kwargs['params']
            self.assertEqual(query['order'], 'created_at.desc,job_id.desc')
            self.assertEqual(query['or'], f'(created_at.lt.1,and(created_at.eq.1,job_id.lt.{ID}))')

    def test_invalid_job_id_does_not_reach_database(self):
        with patch.object(database, 'rest') as request:
            self.assertIsNone(store.load('bad,owner.eq.private'))
        request.assert_not_called()

    def test_stale_lease_failure_is_not_silently_accepted(self):
        value = dict(job(), _lease_token=ID)
        with patch.object(database, 'rpc', return_value=False) as rpc, self.assertRaisesRegex(RuntimeError, 'lease'):
            store.save(value)
        self.assertNotIn('_lease_token', rpc.call_args.args[1]['payload'])

    def test_storage_missing_is_distinct_from_permissions_and_network_errors(self):
        def error(status, body):
            return urllib.error.HTTPError('https://example.test', status, '', {}, io.BytesIO(json.dumps(body).encode()))
        with patch.object(database, 'service_key', return_value='secret'), patch.dict(os.environ, SUPABASE_URL='https://example.test'):
            for status, body, exception in [(400, {'statusCode': '404'}, database.StorageMissing),
                    (404, {}, database.StorageMissing), (403, {'message': 'secret'}, RuntimeError), (500, {}, RuntimeError)]:
                with patch.object(database.urllib.request, 'urlopen', side_effect=error(status, body)), self.assertRaises(exception) as raised:
                    database.request('/storage/v1/object/real-world-results/test')
                self.assertNotIn('secret', str(raised.exception))

    def test_bounded_reads_close_http_body(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {'Content-Length': '3'}
        response.read.return_value = b'123'
        with patch.object(database, 'service_key', return_value='secret'), patch.dict(os.environ, SUPABASE_URL='https://example.test'), \
                patch.object(database.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'limits'):
                database.request('/storage/v1/object/test', raw=True, max_bytes=2)
        response.__exit__.assert_called_once()

    def test_checksum_mismatch_never_enters_analysis(self):
        source = {'node_name': 'server', 'object_path': 'archive.json', 'sha256': 'wrong', 'bytes': 2}
        with patch.object(database, 'request', return_value=b'{}'), self.assertRaisesRegex(RuntimeError, 'checksum'):
            artifacts.read(ID, 'server', [source])

    def test_missing_archived_object_is_not_treated_as_unfinished_upload(self):
        source = {'node_name': 'server', 'object_path': 'archive.json'}
        with patch.object(database, 'request', side_effect=database.StorageMissing):
            self.assertIsNone(artifacts.read(ID, 'server', []))
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                artifacts.read(ID, 'server', [source])

    def test_upload_capability_is_scoped_and_supports_retries(self):
        with patch.object(database, 'base_url', return_value='https://example.test'), \
                patch.object(database, 'request', return_value={'url': '/object/upload/sign/bucket/node?token=scoped'}) as request:
            signed = artifacts.signed_upload(ID, 'server')
        self.assertEqual(signed, 'https://example.test/storage/v1/object/upload/sign/bucket/node?token=scoped')
        self.assertTrue(request.call_args.args[0].endswith(ID + '/server.json'))
        self.assertEqual(request.call_args.args[3], {'x-upsert': 'true'})

    def test_terminal_worker_retries_archiving_without_ec2_work(self):
        value = job('completed')
        with patch.object(store, 'claim', return_value=ID), patch.object(store, 'load', return_value=value), \
                patch.object(store, 'release') as release, patch.object(controller, 'step') as step, \
                patch.object(controller, 'finalize', side_effect=[RuntimeError('storage unavailable'), None]) as finalize:
            with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
                controller.tick(ID)
            self.assertTrue(controller.tick(ID)['finished'])
        self.assertEqual(finalize.call_count, 2)
        step.assert_not_called()
        release.assert_called_with(ID, ID)

    def test_stored_terminal_report_serves_summary_without_storage_reads(self):
        value, raw = fixture()
        full = reports.build_report(value, raw)
        with patch.object(database, 'rest', return_value=[{'report': copy.deepcopy(full)}]), patch.object(artifacts, 'read') as read:
            summary = reports.load_report(value, summary_only=True)
        self.assertEqual(summary['summary'], full['summary'])
        self.assertNotIn('queue', summary)
        self.assertNotIn('tcp', summary['receivers'][0])
        read.assert_not_called()

    def test_migration_refuses_active_jobs_before_any_destination_write(self):
        table = MagicMock()
        table.scan.return_value = {'Items': [job()]}
        with patch.object(database, 'rest') as rest, self.assertRaisesRegex(RuntimeError, 'active'):
            migrate_supabase.inventory(table)
        rest.assert_not_called()

    def test_migration_checks_all_legacy_pages(self):
        table = MagicMock()
        table.scan.side_effect = [{'Items': [dict(job('completed'), active='no')], 'LastEvaluatedKey': {'job_id': ID}},
                                  {'Items': [dict(job('cancelled'), active='no')]}]
        self.assertEqual(len(migrate_supabase.inventory(table)), 2)
        self.assertEqual(table.scan.call_args.kwargs['ExclusiveStartKey'], {'job_id': ID})


if __name__ == '__main__':
    unittest.main()
