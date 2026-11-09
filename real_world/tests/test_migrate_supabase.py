"""Migration verification using synthetic AWS/storage adapters and retained original bytes."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import migrate_supabase as migration


def job():
    placement = dict(region='us-east-1', zone_id='use1-az1', instance_type='t3.medium')
    config = dict(server=placement.copy(), bottleneck=placement.copy(), receivers=[placement.copy()], cca='bbr',
                  duration_seconds=10, rate_mbit=10, buffer_kbytes=125, notes='Synthetic migration unit fixture')
    return dict(job_id='fixture-job', config=config, status='completed', active='no', lease_until=123)


class Migration(unittest.TestCase):
    def test_configuration_uses_cli_credentials_only_in_memory_and_discovers_exact_stack_resources(self):
        import boto3
        for secret in [None, 'fixture-secret-arn']:
            environment = {'SUPABASE_URL': 'https://fixture.supabase.co'}
            if secret: environment['SUPABASE_SECRET_ARN'] = secret
            resources = [dict(ResourceType=kind, LogicalResourceId=logical, PhysicalResourceId=physical) for kind, logical, physical in [
                ('AWS::Lambda::Function', 'RealWorldTestsApiFixture', 'fixture-api'), ('AWS::DynamoDB::Table', 'RealWorldTestsJobsFixture', 'fixture-table'), ('AWS::S3::Bucket', 'RealWorldTestsResultsFixture', 'fixture-bucket')]]
            services = {name: MagicMock() for name in ['cloudformation', 'lambda']}
            services['cloudformation'].describe_stack_resources.return_value = {'StackResources': resources}
            services['lambda'].get_function_configuration.return_value = {'Environment': {'Variables': environment}}
            credentials = dict(AccessKeyId='fixture-access', SecretAccessKey='fixture-secret')
            if secret: credentials['SessionToken'] = 'fixture-session'
            with self.subTest(secret=secret), patch.dict(os.environ, {}, clear=True), patch.object(migration.subprocess, 'check_output', return_value=json.dumps(credentials)) as cli, patch.object(boto3, 'setup_default_session') as session, patch.object(boto3, 'resource') as resource, patch.object(migration.cloud, 'client', side_effect=lambda name: services[name]), contextlib.redirect_stdout(io.StringIO()) as output:
                table, bucket = migration.configure('fixture-stack')
                self.assertIs(table, resource.return_value.Table.return_value); self.assertEqual(bucket, 'fixture-bucket')
                resource.return_value.Table.assert_called_once_with('fixture-table')
                self.assertEqual(os.environ['SUPABASE_SECRET_ARN'], secret or 'jumpserve/supabase-service-key')
                self.assertEqual(session.call_args.kwargs['aws_secret_access_key'], 'fixture-secret')
                self.assertIn('export-credentials', cli.call_args.args[0]); self.assertEqual(output.getvalue(), '')

    def test_inventory_reads_consistently_across_all_pages_and_refuses_nonterminal_or_active_jobs(self):
        table = MagicMock(); table.scan.side_effect = [dict(Items=[job()], LastEvaluatedKey={'id': 'next'}), dict(Items=[dict(job(), job_id='second')])]
        self.assertEqual(len(migration.inventory(table)), 2)
        self.assertEqual(table.scan.call_args_list[0].kwargs, {'ConsistentRead': True})
        self.assertEqual(table.scan.call_args_list[1].kwargs, {'ConsistentRead': True, 'ExclusiveStartKey': {'id': 'next'}})
        for change in [dict(status='running'), dict(active='yes'), dict(active=None)]:
            table.scan.side_effect = None; table.scan.return_value = dict(Items=[dict(job(), **change)])
            with self.assertRaisesRegex(RuntimeError, 'Legacy tests are active'): migration.inventory(table)

    def fixture(self, *, apply=False, change=None):
        change = change or {}; value = job(); saved = {k: v for k, v in value.items() if k != 'lease_until'}; saved['cancel_requested'] = False
        raw = b'{"zero":0}'; digest = hashlib.sha256(raw).hexdigest()
        record = dict(node_name='server', sha256=digest, version_id='original-version')
        stream = io.BytesIO(raw); s3 = MagicMock(); s3.get_object.return_value = dict(Body=stream, ContentLength=len(raw), VersionId='original-version')
        if change.get('s3'):
            error = RuntimeError('fixture S3 failure'); error.response = {'Error': {'Code': change['s3']}}; s3.get_object.side_effect = error
        if change.get('length'): s3.get_object.return_value['ContentLength'] += 1
        archived = [dict(record, sha256='different')] if change.get('archive_hash') else [record] if change.get('existing') else []
        verified = (b'different', record) if change.get('verified_bytes') else (raw, dict(record, version_id='different')) if change.get('version') else None if change.get('missing') else (raw, record)
        if change.get('metadata'): saved['status'] = 'different'
        expected_report = {field: {'fixture': field} for field in ['summary', 'receivers', 'queue', 'comparison', 'sources', 'provenance']}
        actual = copy.deepcopy(expected_report)
        if change.get('report'): actual[change['report']] = {'different': True}
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(migration.cloud, 'client', return_value=s3))
        stack.enter_context(patch.object(migration, 'nodes_for', return_value=[{'name': 'server'}]))
        create = stack.enter_context(patch.object(migration.store, 'create'))
        stack.enter_context(patch.object(migration.store, 'load', return_value=saved))
        stack.enter_context(patch.object(migration.artifacts, 'records', return_value=archived))
        archive = stack.enter_context(patch.object(migration.artifacts, 'archive'))
        stack.enter_context(patch.object(migration.artifacts, 'read', return_value=verified))
        persist = stack.enter_context(patch.object(migration.reports, 'persist_report'))
        stack.enter_context(patch.object(migration.reports, 'load_report', return_value=actual))
        build = stack.enter_context(patch.object(migration.reports, 'build_report', return_value=expected_report))
        return value, raw, stream, create, archive, persist, build, stack

    def test_copy_and_verify_preserve_original_bytes_versions_zero_values_and_legacy_backups(self):
        for apply, existing in [(False, False), (True, False), (True, True)]:
            with self.subTest(apply=apply, existing=existing):
                value, raw, stream, create, archive, persist, build, stack = self.fixture(change={'existing': existing})
                with stack, contextlib.redirect_stdout(io.StringIO()) as output:
                    result = migration.migrate([value], 'fixture-bucket', apply)
                    self.assertEqual(result, {'jobs': 1, 'artifacts': 1, 'bytes': len(raw), 'checksums': 'verified', 'legacy_stores': 'retained'})
                    self.assertEqual(create.call_count, int(apply)); self.assertEqual(persist.call_count, int(apply))
                    self.assertEqual(archive.call_count, int(apply and not existing))
                    if apply and not existing: archive.assert_called_once_with('fixture-job', 'server', raw, 'original-version')
                    self.assertEqual(build.call_args.args[1], {'server': {'zero': 0}})
                    self.assertNotIn('lease_until', build.call_args.args[0]); self.assertTrue(stream.closed)
                    self.assertTrue(json.loads(output.getvalue())['verified'])

    def test_mismatch_or_unreadable_originals_abort_before_a_verified_result(self):
        cases = [({'metadata': True}, 'metadata verification'), ({'length': True}, 'truncated'), ({'archive_hash': True}, 'refusing overwrite'),
                 ({'verified_bytes': True}, 'Artifact verification'), ({'version': True}, 'Artifact verification'), ({'missing': True}, 'Artifact verification')]
        cases.extend(({'report': name}, 'Analysis changed during migration: ' + name) for name in ['summary', 'receivers', 'queue', 'comparison', 'sources', 'provenance'])
        for change, message in cases:
            with self.subTest(change=change):
                value, _, _, _, _, _, _, stack = self.fixture(change=change)
                with stack, contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaisesRegex(RuntimeError, message): migration.migrate([value], 'fixture-bucket', False)
                self.assertEqual(output.getvalue(), '')
        value, _, _, _, _, _, _, stack = self.fixture()
        with stack, patch.object(migration.artifacts, 'MAX_BYTES', 1), self.assertRaisesRegex(RuntimeError, 'supported size'): migration.migrate([value], 'fixture-bucket', True)

    def test_only_explicit_missing_s3_objects_are_skipped_and_transport_errors_propagate(self):
        for code in ['NoSuchKey', 'AccessDenied']:
            value, _, _, _, _, _, build, stack = self.fixture(change={'s3': code})
            with stack, contextlib.redirect_stdout(io.StringIO()):
                if code == 'NoSuchKey':
                    result = migration.migrate([value], 'fixture-bucket', False)
                    self.assertEqual(result['artifacts'], 0); self.assertEqual(build.call_args.args[1:], ({}, []))
                else:
                    with self.assertRaisesRegex(RuntimeError, 'S3 failure'): migration.migrate([value], 'fixture-bucket', False)

    def test_storage_probe_retries_identical_capability_and_cleans_only_its_object_on_success_or_failure(self):
        for failure in [None, 'upload', 'stored', 'download']:
            uploads = []; requests = []
            def upload(url, value):
                uploads.append((url, value.copy()))
                if failure == 'upload': raise RuntimeError('fixture upload failure')
            def request(path, method='GET', body=None, **options):
                requests.append((path, method, body, options))
                if method == 'DELETE': return None
                if method == 'POST': return {'signedURL': '/fixture-private-download'}
                return json.dumps({'wrong': True} if failure == 'stored' else uploads[0][1]).encode()
            def download(*args, **kwargs): return io.BytesIO(b'different' if failure == 'download' else json.dumps(uploads[0][1]).encode())
            with self.subTest(failure=failure), patch.object(migration.artifacts, 'signed_upload', return_value='https://fixture.invalid/upload'), patch.object(migration.runtime, 'upload', side_effect=upload), patch.object(migration.database, 'request', side_effect=request), patch.object(migration.database, 'base_url', return_value='https://fixture.invalid'), patch.object(migration.urllib.request, 'urlopen', side_effect=download), contextlib.redirect_stdout(io.StringIO()) as output:
                if failure:
                    with self.assertRaises(RuntimeError): migration.check_storage()
                    self.assertEqual(output.getvalue(), '')
                else:
                    migration.check_storage(); self.assertEqual(uploads[0], uploads[1]); self.assertEqual(json.loads(output.getvalue())['signed_download'], 'passed')
                cleanup = requests[-1]; self.assertEqual(cleanup[1], 'DELETE')
                self.assertEqual(len(cleanup[2]['prefixes']), 1); self.assertTrue(cleanup[2]['prefixes'][0].startswith('_verification/'))
                self.assertTrue(cleanup[2]['prefixes'][0].endswith('/server.json'))

    def test_cli_defaults_to_inventory_and_separates_import_verify_and_storage_probe(self):
        for flag in [None, '--apply', '--verify', '--check-storage']:
            with self.subTest(flag=flag), patch.object(sys, 'argv', ['migration'] + ([flag] if flag else [])), patch.object(migration, 'configure', return_value=('table', 'bucket')) as configure, patch.object(migration, 'inventory', return_value=[job()]) as inventory, patch.object(migration, 'migrate', return_value={'jobs': 1}) as migrate, patch.object(migration, 'check_storage') as storage, contextlib.redirect_stdout(io.StringIO()):
                migration.main(); configure.assert_called_once_with('JumpServeBenchmarkStack')
                if flag == '--check-storage': storage.assert_called_once(); migrate.assert_not_called(); inventory.assert_not_called()
                elif flag: migrate.assert_called_once_with([job()], 'bucket', flag == '--apply')
                else: migrate.assert_not_called(); storage.assert_not_called()
        with patch.object(sys, 'argv', ['migration', '--help']), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
            runpy.run_path(migration.__file__, run_name='__main__')
        self.assertEqual(result.exception.code, 0)


if __name__ == '__main__': unittest.main()
