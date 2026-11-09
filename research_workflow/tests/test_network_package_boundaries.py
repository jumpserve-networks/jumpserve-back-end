"""Networking-package ingestion and launch contracts with synthetic services.

Original bytes and scientific validation use the production implementations;
cloud provisioning, signed URLs and persistence are isolated adapters.
"""
import base64
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import network_package as package
import network_spec
import store
from workflow import canonical, digest
from test_network_execution import document, STUDY, ACTOR, JOB, PACKAGE


class NetworkPackageBoundaries(unittest.TestCase):
    def setUp(self):
        self.protocol = document('custom-package-v1'); self.spec = network_spec.specs(self.protocol)[0]
        self.state = dict(job_id=JOB, study_id=STUDY, phase='running', deadline='2099-01-01T00:00:00Z', started_at='2026-11-07T00:00:00Z', data={'retained_original': True}, cancel_requested=False, spec=self.spec, spec_sha256=digest(canonical(self.spec)))
        self.output = dict(version='network-observations-v1', observations=[dict(observation_key='zero', metric='response_headers_ms', units='ms', value=0, status='recorded', reason=None, metadata={'original': True}), dict(observation_key='missing', metric='response_headers_ms', units='ms', value=None, status='missing', reason='Original timeout', metadata={})], provenance={'scope': 'synthetic-software-control'}, usage=None)
        self.originals = {'stdout.bin': b'Original binary stdout\x00\xff', 'stderr.bin': b'Original stderr\n', 'output.json': canonical(self.output), 'runtime-receipt.json': canonical(dict(exit_code=0, termination_reason=None))}; self.token = 'a' * 64; self.closed = False; self.artifacts = []; self.updates = []
        self.rows = self.enterContext(patch.object(store, 'rows', side_effect=self.read)); self.rpc = self.enterContext(patch.object(store, 'rpc', side_effect=self.write)); self.retain = self.enterContext(patch.object(store, 'artifact', side_effect=self.artifact)); self.append = self.enterContext(patch.object(store, 'append')); self.sign = self.enterContext(patch.object(store, 'request', return_value={'signedURL': '/object/sign/synthetic-original?token=synthetic'}))
        self.cloud = Mock(); self.cloud.describe_instances.return_value = {'Reservations': []}; self.factory = self.enterContext(patch('boto3.client', return_value=self.cloud)); self.enterContext(patch.dict(os.environ, NETWORK_AWS_ACCOUNT=store.ACCOUNT, NETWORK_PACKAGE_AMI='ami-synthetic', NETWORK_PACKAGE_SUBNET='subnet-synthetic', NETWORK_PACKAGE_SECURITY_GROUP='sg-synthetic', NETWORK_PACKAGE_INGEST_URL='https://example.invalid/synthetic-ingest'))
    def read(self, kind, query):
        if kind == 'study_owners': return [dict(owner_id=ACTOR)]
        if kind == 'queue_jobs': return [dict(id=JOB, campaign_id='synthetic-campaign', input_artifact_id='synthetic-spec')]
        if kind == 'campaigns': return [dict(id='synthetic-campaign', protocol_id='synthetic-protocol')]
        if kind == 'protocols': return [dict(id='synthetic-protocol', document=copy.deepcopy(self.protocol))]
        if kind == 'artifacts':
            identity = query['id'][3:]; sha = digest(identity.encode()); return [dict(id=identity, sha256=sha, storage_path=STUDY + '/' + sha)]
        raise AssertionError('Unexpected relation: ' + kind)
    def write(self, name, payload):
        if name == 'research_network_token': self.assertEqual(payload, dict(p_job=JOB, p_hash=digest(self.token.encode()))); return None if self.closed else {'runner': self.spec['runner']}
        if name == 'research_network_launch': self.assertEqual(payload['p_job'], JOB); return copy.deepcopy(self.state)
        if name == 'research_network_update': self.updates.append(copy.deepcopy(payload)); return {'recorded': True}
        raise AssertionError(name)
    def artifact(self, study, actor, raw, ident, media, provenance):
        self.assertEqual((study, actor, media), (STUDY, ACTOR, 'application/octet-stream')); self.artifacts.append(dict(raw=raw, provenance=copy.deepcopy(provenance), id=ident)); return dict(id=ident, sha256=digest(raw), byte_count=len(raw), provenance=provenance)
    def event(self, files=None): return dict(headers={'Authorization': 'Bearer ' + self.token}, body=json.dumps(dict(job_id=JOB, files={name: base64.b64encode(raw).decode() for name, raw in (self.originals if files is None else files).items()})))
    def ingest(self, files=None): return package.ingest(self.event(files))
    def invocation(self, action='status', runner=None): return dict(source='jumpserve.network-execution', job_id=JOB, action=action, **({'runner': runner} if runner else {}))

    def test_signed_artifacts_require_study_bound_hash_paths_and_an_exact_expiry(self):
        result = package.sign_artifact(STUDY, PACKAGE, 330); row = self.read('artifacts', {'id': 'eq.' + PACKAGE})[0]; self.assertEqual(result, 'https://' + store.PROJECT + '.supabase.co/storage/v1/object/sign/synthetic-original?token=synthetic'); self.sign.assert_called_once_with('/storage/v1/object/sign/' + store.BUCKET + '/' + row['storage_path'], 'POST', {'expiresIn': 330})
        for rows in [[], [dict(storage_path='other/original', sha256='a' * 64)]]:
            with self.subTest(rows=rows), patch.object(store, 'rows', return_value=rows), self.assertRaisesRegex(ValueError, 'unavailable|identity'): package.sign_artifact(STUDY, PACKAGE, 330)

    def test_successful_ingestion_preserves_every_original_and_distinguishes_zero_from_missing(self):
        original = copy.deepcopy(self.originals); result = self.ingest(); self.assertEqual(result['statusCode'], 200); self.assertEqual(json.loads(result['body']), {'retained': True}); self.assertEqual(self.originals, original); self.assertEqual(len(self.artifacts), 4); self.assertEqual(self.append.call_count, 4)
        for row in self.artifacts:
            name = row['provenance']['original_filename']; self.assertEqual(row['raw'], original[name]); self.assertEqual(row['id'], str(uuid.uuid5(uuid.UUID(JOB), 'package:' + name))); self.assertTrue(row['provenance']['complete']); self.assertEqual(row['provenance']['transformation'], 'none; original package bytes')
        data = self.updates[-1]['p_data']; self.assertEqual(self.updates[-1]['p_phase'], 'running'); self.assertTrue(data['retained_original']); self.assertFalse(data['package_failed']); self.assertIsNone(data['reason']); self.assertEqual(data['output']['observations'][0]['value'], 0); self.assertIsNone(data['output']['observations'][1]['value']); self.assertIsNone(data['output']['usage']['measured_charges_usd']); self.assertEqual(data['output']['usage']['runtime_receipt'], {'exit_code': 0, 'termination_reason': None}); self.factory.assert_not_called()

    def test_truncated_output_nonzero_exit_runtime_termination_or_reported_failure_remains_failed(self):
        for failure in ['truncated', 'exit', 'termination', 'provenance']:
            with self.subTest(failure=failure):
                files = dict(self.originals); output = copy.deepcopy(self.output); receipt = dict(exit_code=0, termination_reason=None)
                if failure == 'exit': receipt['exit_code'] = 1
                elif failure == 'termination': receipt['termination_reason'] = 'Recorded wall deadline'
                elif failure == 'provenance': output['provenance']['failure'] = 'Recorded acquisition failure'
                files['output.json'] = canonical(output); files['runtime-receipt.json'] = canonical(receipt); event = self.event(files)
                if failure == 'truncated': payload = json.loads(event['body']); payload['files']['stdout.bin.truncated'] = True; event['body'] = json.dumps(payload)
                self.assertEqual(package.ingest(event)['statusCode'], 200); self.assertTrue(self.updates[-1]['p_data']['package_failed']); self.assertEqual(self.updates[-1]['p_data']['reason'], receipt['termination_reason'] or output['provenance'].get('failure'))
                if failure == 'truncated': self.assertFalse(next(row for row in reversed(self.artifacts) if row['provenance']['original_filename'] == 'stdout.bin')['provenance']['complete'])

    def test_missing_or_invalid_observation_and_receipt_bytes_are_retained_before_reporting_failure(self):
        for failure in ['missing-output', 'invalid-output', 'missing-receipt', 'invalid-receipt', 'receipt-shape', 'wrong-units']:
            with self.subTest(failure=failure):
                files = dict(self.originals)
                if failure == 'missing-output': files.pop('output.json')
                elif failure == 'invalid-output': files['output.json'] = b'Original malformed observation envelope'
                elif failure == 'missing-receipt': files.pop('runtime-receipt.json')
                elif failure == 'invalid-receipt': files['runtime-receipt.json'] = b'Original malformed receipt'
                elif failure == 'receipt-shape': files['runtime-receipt.json'] = b'null'
                else: output = copy.deepcopy(self.output); output['observations'][0]['units'] = 'seconds'; files['output.json'] = canonical(output)
                before = len(self.artifacts); self.assertEqual(self.ingest(files)['statusCode'], 200); self.assertEqual(len(self.artifacts) - before, len(files)); data = self.updates[-1]['p_data']; self.assertTrue(data['package_failed']); self.assertEqual(data['reason'], 'Package output is missing or invalid; original available bytes retained.')

    def test_authentication_and_payload_bounds_reject_before_artifact_or_cloud_access(self):
        for headers in [None, {}, {'Authorization': 'Bearer wrong'}, {'authorization': 'Bearer ' + 'A' * 64}]:
            with self.subTest(headers=headers): self.assertEqual(package.ingest(dict(headers=headers, body='{}'))['statusCode'], 401)
        for change in [dict(isBase64Encoded=True), dict(body=None), dict(body='x' * 15000001), dict(body='[]'), dict(body='not JSON')]:
            with self.subTest(change=str(change)[:80]): self.assertEqual(package.ingest(dict(self.event(), **change))['statusCode'], 400)
        self.rpc.assert_not_called(); self.retain.assert_not_called(); self.factory.assert_not_called()

    def test_output_envelope_file_names_base64_and_combined_byte_budget_are_exact(self):
        invalid = [dict(job_id=JOB, files=[], extra=True), dict(job_id=JOB, files={'private.env': ''}), dict(job_id=JOB, files={'stdout.bin': 0}), dict(job_id=JOB, files={'stdout.bin': 'not-base64!'})]
        for payload in invalid:
            with self.subTest(payload=payload): self.assertEqual(package.ingest(dict(self.event(), body=json.dumps(payload)))['statusCode'], 400)
        self.state['spec']['limits']['max_output_bytes'] = 3; self.assertEqual(self.ingest({'stdout.bin': b'1234'})['statusCode'], 400); self.assertEqual(self.artifacts, []); self.assertEqual(self.updates, [])
        self.state['spec']['limits']['max_output_bytes'] = 4; self.assertEqual(self.ingest({'stdout.bin': b'12', 'stderr.bin': b'345'})['statusCode'], 400); self.assertEqual(len(self.artifacts), 1); self.assertEqual(self.artifacts[0]['raw'], b'12'); self.assertEqual(self.updates, [])

    def test_closed_capabilities_or_missing_owner_cannot_admit_output(self):
        self.closed = True; self.assertEqual(self.ingest()['statusCode'], 403); self.retain.assert_not_called(); self.rows.assert_not_called(); self.closed = False
        with patch.object(store, 'rows', return_value=[]): self.assertEqual(self.ingest()['statusCode'], 400)
        self.retain.assert_not_called()

    def test_private_input_urls_are_bound_to_retained_hashes_and_only_encoded_into_bootstrap_data(self):
        self.state['spec']['config']['private_inputs'] = {'original.csv': dict(artifact_id=PACKAGE, sha256='f' * 64)}; self.cloud.describe_instances.return_value = {'Reservations': [{'Instances': [{'InstanceId': 'i-synthetic', 'State': {'Name': 'running'}}]}]}; self.assertEqual(package.handler(self.invocation('launch'))['status'], 'running'); args = self.cloud.run_instances.call_args.kwargs; self.assertNotIn('IamInstanceProfile', args); self.assertEqual(args['ClientToken'], JOB); script = args['UserData']; settings = json.loads(base64.b64decode(script.splitlines()[-1].split()[-1])); self.assertEqual(settings['private_inputs']['original.csv']['sha256'], 'f' * 64); self.assertTrue(settings['private_inputs']['original.csv']['url'].startswith('https://' + store.PROJECT)); self.assertEqual(settings['input_sha256'], self.state['spec_sha256']); self.assertEqual(len(settings['token']), 64); self.assertEqual(len(self.sign.call_args_list), 3); self.assertEqual(self.updates[0]['p_phase'], 'dispatching'); self.assertEqual(self.updates[0]['p_ingest_hash'], digest(settings['token'].encode()))

    def test_bootstrap_bound_rejects_large_private_input_urls_before_any_cloud_invocation(self):
        with self.assertRaisesRegex(ValueError, 'user-data limit'): package.user_data(self.state, 'https://example.invalid/spec', None, self.token, {'original': {'url': 'x' * 20000, 'sha256': 'a' * 64}})
        self.cloud.run_instances.assert_not_called()

    def test_ingest_without_an_output_keeps_previously_retained_invocation_metadata(self):
        self.assertEqual(self.ingest({})['statusCode'], 200); data = self.updates[-1]['p_data']; self.assertTrue(data['retained_original']); self.assertIsNone(data['output']); self.assertEqual(data['artifacts'], []); self.assertTrue(data['package_failed'])

    def test_handler_rejects_ambiguous_invocations_missing_state_wrong_account_and_duplicate_hosts(self):
        for changes in [dict(unexpected=True), dict(source='wrong'), dict(action='execute'), dict(runner='arbitrary')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): package.handler(dict(self.invocation(), **changes))
        with patch.object(package, 'saved', return_value=None), self.assertRaisesRegex(ValueError, 'unavailable'): package.handler(self.invocation())
        with patch.dict(os.environ, NETWORK_AWS_ACCOUNT='wrong'), self.assertRaisesRegex(ValueError, 'account differs'): package.handler(self.invocation())
        self.cloud.describe_instances.return_value = {'Reservations': [{'Instances': [{'InstanceId': 'i-one'}, {'InstanceId': 'i-two'}]}]}
        with self.assertRaisesRegex(RuntimeError, 'More than one machine'): package.handler(self.invocation())
        self.cloud.run_instances.assert_not_called()

    def test_all_terminal_states_are_returned_without_launch_or_status_calls(self):
        for phase in ['completed', 'failed', 'cancelled']:
            self.state['phase'] = phase
            with self.subTest(phase=phase): self.assertEqual(package.handler(self.invocation()), self.state['data'])
        self.factory.assert_not_called()

    def test_cancelled_or_expired_unseen_hosts_finish_without_invented_resources(self):
        for cancelled in [False, True]:
            with self.subTest(cancelled=cancelled):
                self.state['deadline'] = '2000-01-01T00:00:00Z'; result = package.handler(self.invocation('cancel' if cancelled else 'status')); self.assertEqual(result['status'], 'cancelled' if cancelled else 'failed'); self.assertIsNone(result['external_job_id']); self.assertIsNone(result['resources']['instance_id']); self.assertIsNone(result['resources']['availability_zone']); self.assertIsNone(result['output']); self.assertEqual(self.updates[-1]['p_phase'], result['status'])

    def test_terminated_host_releases_only_complete_retained_observations_as_completed(self):
        self.cloud.describe_instances.return_value = {'Reservations': [{'Instances': [{'InstanceId': 'i-original', 'State': {'Name': 'terminated'}, 'InstanceType': 't3.medium', 'ImageId': 'ami-original', 'Placement': {'AvailabilityZone': 'us-east-1a'}}]}]}
        for failed in [False, True]:
            self.state['data'].update(output=self.output, package_failed=failed, artifacts=[{'id': PACKAGE}]); result = package.handler(self.invocation()); self.assertEqual(result['status'], 'failed' if failed else 'completed'); self.assertEqual(result['started_at'], self.state['started_at']); self.assertEqual(result['external_job_id'], 'i-original'); self.assertEqual(result['resources']['availability_zone'], 'us-east-1a'); self.assertEqual(result['artifacts'], [{'id': PACKAGE}])

    def test_reaper_preserves_explicit_runner_identity_and_records_unconfirmed_cleanup(self):
        rows = [dict(job_id=JOB, runner=runner) for runner in ['netem-iperf-v1', 'built-package-v1']]
        with patch.object(store, 'request', return_value=rows) as request, patch.object(package, 'handler', side_effect=[{'status': 'cleaning'}, TimeoutError('Synthetic original cleanup unconfirmed')]) as handler:
            result = package.reap({})
        self.assertEqual([call.args[0]['runner'] for call in handler.call_args_list], ['netem-iperf-v1', 'built-package-v1']); self.assertEqual(result['outcomes'][1], dict(job_id=JOB, status='cleanup-unconfirmed', error_type='TimeoutError')); self.assertIsNone(result['measured_charges_usd']); self.assertIn('limit=100', request.call_args.args[0])


if __name__ == '__main__': unittest.main()
