"""Saved experiment dispatch and evidence receipts with synthetic external services."""
import base64
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomy_costs
import generated_package
import network_execution as execution
import network_spec
import store
from workflow import canonical, digest
from test_network_execution import document, SOURCE
from test_autonomy_methods import Recorded
from test_network_traffic_allowance import built_protocol

STAMP = '2026-11-01T00:00:00+00:00'
TARGET = 'arn:aws:lambda:us-east-1:' + store.ACCOUNT + ':function:UnitExecutor:7'
def uid(): return str(uuid.uuid4())


class NetworkOperations(unittest.TestCase):
    def setUp(self):
        self.study, self.actor = uid(), uid(); self.job = dict(id=uid(), study_id=self.study, lease_token=uid())
        self.state = dict(phase='pending', cancel_requested=False, deadline='2099-01-01T00:00:00Z', started_at=STAMP, data={})
        self.records, self.blobs, self.calls, self.appended, self.order = {}, {}, [], [], []
        self.enterContext(patch.object(store, 'rows', side_effect=self.rows)); self.enterContext(patch.object(store, 'artifact_bytes', side_effect=lambda study, ident, **kw: self.blobs[ident]))
        self.enterContext(patch.object(store, 'rpc', side_effect=self.rpc)); self.enterContext(patch.object(store, 'artifact', side_effect=self.artifact)); self.enterContext(patch.object(store, 'append', side_effect=self.append))
        self.client = MagicMock(); self.enterContext(patch('boto3.client', return_value=self.client))
        self.launch_claimed = True

    def rows(self, table, params):
        self.assertEqual(params['study_id'], 'eq.' + self.study)
        if table == 'study_owners': return [{'owner_id': self.actor}]
        self.assertIn(table, ['sources', 'artifacts']); return copy.deepcopy(self.records.get((table, params['id'].removeprefix('eq.')), []))
    def artifact(self, study, actor, raw, identity, media, provenance):
        self.assertEqual((study, actor), (self.study, self.actor)); self.order.append('capture')
        if identity in self.blobs: self.assertEqual(self.blobs[identity], raw, 'Fixture cannot rewrite an original artifact identity')
        self.blobs[identity] = raw
        return dict(id=identity, sha256=digest(raw), byte_count=len(raw), storage_path=study + '/' + digest(raw), media_type=media, provenance=provenance, verified_at=STAMP)
    def append(self, study, actor, kind, row):
        self.assertEqual((study, actor, kind), (self.study, self.actor, 'artifacts')); self.appended.append(row); self.records[kind, row['id']] = [row]; return row
    def rpc(self, name, payload):
        self.calls.append((name, copy.deepcopy(payload))); self.order.append(name)
        if name == 'research_network_launch': return self.launch_claimed
        if name == 'research_network_begin': return copy.deepcopy(self.state)
        if name in ['research_network_update', 'research_network_cancel']: return {'recorded': True}
        if name == 'research_network_pending': return [{'job_id': self.job['id']}]
        if name == 'research_queue_finish': return {'status': 'awaiting-review', 'job_id': payload['p_job']}
        raise AssertionError('Unexpected RPC: ' + name)

    def package(self, built=False):
        protocol = network_spec.bind_inputs(built_protocol() if built else document('custom-package-v1'))
        spec = network_spec.specs(protocol)[0]; package = spec['config']['package']; raw = Path(generated_package.__file__).read_bytes() if built else SOURCE.read_bytes()
        package['sha256'] = digest(raw)
        protocol['configurations'][0]['details']['test_config']['package']['sha256'] = digest(raw); protocol = network_spec.bind_inputs(protocol); spec = network_spec.specs(protocol)[0]
        self.records['artifacts', package['artifact_id']] = [dict(provenance={'kind': 'builder-server-wrapper' if built else 'network-python-package', 'version': package['version']})]
        self.blobs[package['artifact_id']] = raw
        for item in spec['config'].get('private_inputs', {}).values():
            self.records['artifacts', item['artifact_id']] = [dict(provenance={'kind': 'builder-frozen-project', 'version': item['version']})]
            item['sha256'] = digest(b'Original project archive'); self.blobs[item['artifact_id']] = b'Original project archive'
            protocol['configurations'][0]['details']['test_config']['private_inputs']['project.zip']['sha256'] = item['sha256']
        protocol = network_spec.bind_inputs(protocol); spec = network_spec.specs(protocol)[0]
        return dict(id=uid(), sha256=digest(canonical(protocol)), document=protocol), spec

    def test_capability_inventory_reports_disabled_executors_without_substituting_another_runner(self):
        with patch.dict(os.environ, {}, clear=True):
            result = execution.capabilities(); self.assertEqual({r['id'] for r in result['runners'] if r['enabled']}, {'source-arithmetic-v1'})
        self.assertEqual(result['version'], network_spec.VERSION); self.assertEqual(result['max_jobs'], network_spec.MAX_JOBS)

    def test_package_and_private_input_uploads_preserve_original_bytes_and_declared_versions(self):
        identity = uid(); source = 'print("évidence")\n'
        package = execution.upload(self.study, self.actor, dict(request_id=identity, source=source, version='fixture-v1'))
        self.assertEqual(self.blobs[identity], source.encode()); self.assertEqual(package, dict(kind='python-script', version='fixture-v1', artifact_id=identity, sha256=digest(source.encode())))
        identity = uid(); raw = b'\x00\xff original private input'; result = execution.upload_input(self.study, self.actor, dict(request_id=identity, original_base64=base64.b64encode(raw).decode(), version='input-v1'))
        self.assertEqual(self.blobs[identity], raw); self.assertEqual(result['sha256'], digest(raw)); self.assertEqual(self.appended[-1]['provenance']['kind'], 'network-private-input')
        for body in [dict(request_id=uid(), source='', version='v1'), dict(request_id=uid(), source='é' * 6001, version='v1'), dict(request_id=uid(), source='x', version=''), dict(request_id=uid(), source=1, version='v1'), dict(request_id=uid(), source='x', version=1), {'unexpected': True}]:
            with self.subTest(body=str(body)[:40]), self.assertRaises(ValueError): execution.upload(self.study, self.actor, body)
        for body in [dict(request_id=uid(), original_base64='', version='v1'), dict(request_id=uid(), original_base64='not base64', version='v1'), dict(request_id=uid(), original_base64=base64.b64encode(b'x' * 256001).decode(), version='v1'), dict(request_id=uid(), original_base64='eA==', version=1), dict(request_id=uid(), original_base64='eA==', version=''), {'unexpected': True}]:
            with self.subTest(body=str(body)[:40]), self.assertRaises(ValueError): execution.upload_input(self.study, self.actor, body)

    def test_package_validation_binds_frozen_spec_type_version_and_original_hash(self):
        protocol, spec = self.package(); env = {'NETWORK_PACKAGE_FUNCTION': TARGET}
        with patch.dict(os.environ, env):
            raw = canonical(spec); self.assertEqual(execution.validate_input(self.study, protocol, raw), spec)
            with self.assertRaisesRegex(ValueError, 'exact frozen'): execution.validate_input(self.study, protocol, json.dumps(spec).encode())
            package = spec['config']['package']; record = self.records['artifacts', package['artifact_id']][0]
            for kind, version in [('unreviewed', package['version']), ('network-python-package', 'changed')]:
                record['provenance'] = {'kind': kind, 'version': version}
                with self.assertRaisesRegex(ValueError, 'type/version'): execution.validate_input(self.study, protocol, raw)
            record['provenance'] = {'kind': 'network-python-package', 'version': package['version']}; self.blobs[package['artifact_id']] += b'# changed'
            with self.assertRaisesRegex(ValueError, 'reviewed version'): execution.validate_input(self.study, protocol, raw)

    def test_built_package_requires_current_wrapper_and_frozen_original_project_before_dispatch(self):
        protocol, spec = self.package(True); package = spec['config']['package']; item = spec['config']['private_inputs']['project.zip']; raw = canonical(spec)
        with patch.dict(os.environ, {'NETWORK_BUILT_PACKAGE_FUNCTION': TARGET}), patch.object(generated_package, 'members') as members:
            self.assertEqual(execution.validate_input(self.study, protocol, raw), spec); members.assert_called_once_with(b'Original project archive')
            self.records['artifacts', item['artifact_id']][0]['provenance']['version'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'Private input version'): execution.validate_input(self.study, protocol, raw)
            self.records['artifacts', item['artifact_id']][0]['provenance']['version'] = item['version']; self.blobs[item['artifact_id']] += b'changed'
            with self.assertRaisesRegex(ValueError, 'hashes'): execution.validate_input(self.study, protocol, raw)
            self.blobs[item['artifact_id']] = b'Original project archive'; protocol['document']['resource_limits']['max_input_bytes'] = 1
            with self.assertRaisesRegex(ValueError, 'byte budget'): execution.validate_input(self.study, protocol, raw)
        # The hash may match a retained stale wrapper while differing from this
        # executor revision. It must still be rejected before running anything.
        protocol, spec = self.package(True); package = spec['config']['package']; stale = b'# stale trusted wrapper\n'; self.blobs[package['artifact_id']] = stale
        protocol['document']['configurations'][0]['details']['test_config']['package']['sha256'] = digest(stale); protocol['document'] = network_spec.bind_inputs(protocol['document']); raw = canonical(network_spec.specs(protocol['document'])[0])
        with patch.dict(os.environ, {'NETWORK_BUILT_PACKAGE_FUNCTION': TARGET}), self.assertRaisesRegex(ValueError, 'server revision'): execution.validate_input(self.study, protocol, raw)

    def test_private_inputs_in_custom_package_require_their_own_type_and_hash(self):
        protocol = document('custom-package-v1'); cfg = protocol['configurations'][0]['details']['test_config']; identity = uid(); raw = b'Original private data'
        cfg['private_inputs'] = {'sample.bin': dict(artifact_id=identity, sha256=digest(raw), version='input-v1')}; protocol = network_spec.bind_inputs(protocol); spec = network_spec.specs(protocol)[0]
        package = spec['config']['package']; self.blobs[package['artifact_id']] = SOURCE.read_bytes(); self.records['artifacts', package['artifact_id']] = [dict(provenance={'kind': 'network-python-package', 'version': package['version']})]
        self.blobs[identity] = raw; self.records['artifacts', identity] = [dict(provenance={'kind': 'network-private-input', 'version': 'input-v1'})]
        with patch.dict(os.environ, {'NETWORK_PACKAGE_FUNCTION': TARGET}): self.assertEqual(execution.validate_input(self.study, {'document': protocol}, canonical(spec)), spec)

    def test_invoke_uses_only_saved_identity_and_account_bound_function_not_browser_credentials(self):
        self.client.invoke.return_value = {'Payload': io.BytesIO(b'{"body":"{\\"status\\":\\"running\\"}"}')}
        with patch.dict(os.environ, {'NETWORK_NETEM_IPERF_FUNCTION': TARGET}): self.assertEqual(execution.invoke({'runner': 'netem-iperf-v1'}, self.job['id'], 'status'), {'status': 'running'})
        request = self.client.invoke.call_args.kwargs; self.assertEqual(json.loads(request['Payload']), {'source': 'jumpserve.network-execution', 'job_id': self.job['id'], 'action': 'status', 'runner': 'netem-iperf-v1'})
        for target in ['plain-function-name', TARGET.replace(store.ACCOUNT, '000000000000')]:
            with patch.dict(os.environ, {'NETWORK_EMULATED_FUNCTION': target}), self.assertRaisesRegex(store.StoreError, 'target account'): execution.invoke({'runner': 'emulated-netem-v1'}, self.job['id'], 'launch')
        for response in [{'Payload': io.BytesIO(b'x' * 12000001)}, {'Payload': io.BytesIO(b'failure'), 'FunctionError': 'Unhandled'}, {'Payload': io.BytesIO(b'{"statusCode":403}') }]:
            self.client.invoke.return_value = response
            with patch.dict(os.environ, {'NETWORK_EMULATED_FUNCTION': TARGET}), self.assertRaises(store.StoreError): execution.invoke({'runner': 'emulated-netem-v1'}, self.job['id'], 'status')
        self.client.invoke.return_value = {'Payload': io.BytesIO(b'{"status":"cleaning"}')}
        with patch.dict(os.environ, {'NETWORK_EMULATED_FUNCTION': TARGET}): self.assertEqual(execution.invoke({'runner': 'emulated-netem-v1'}, self.job['id'], 'cancel'), {'status': 'cleaning'})

    def test_bounded_call_is_reserved_once_and_original_response_is_retained_before_interpretation(self):
        spec = dict(runner='bounded-tcp-v1', config=dict(payload_bytes=32, exchanges=2, application_delay_ms=0))
        raw = canonical(dict(version='network-observations-v1', observations=[], provenance={}, usage=None))
        self.client.get_function_configuration.return_value = dict(MemorySize=128, Timeout=5, Runtime='python3.12', Handler='index.handler')
        self.client.invoke.return_value = {'Payload': io.BytesIO(raw), 'StatusCode': 200}
        with patch.object(autonomy_costs, 'bounded_quote', return_value={'quote': {'function_version': TARGET}, 'cents': 2}):
            result = execution.bounded_response(self.job, self.state, spec)
        self.assertEqual(result['status'], 'completed'); self.assertEqual(result['resources']['reserved_cents'], 2); self.assertIsNone(result['resources']['measured_charges_usd'])
        self.assertEqual(self.order, ['research_network_launch', 'capture', 'research_network_update'])
        self.assertEqual(self.appended[0]['provenance']['spec_sha256'], digest(canonical(spec))); self.assertTrue(self.appended[0]['provenance']['complete']); self.client.invoke.assert_called_once()
        self.state['phase'] = 'dispatching'; self.client.reset_mock(); self.calls.clear()
        restored = execution.bounded_response(self.job, self.state, spec); self.assertEqual(restored['output'], json.loads(raw)); self.client.invoke.assert_not_called()

    def test_bounded_configuration_drift_never_invokes_and_unconfirmed_claim_does_not_create_a_client(self):
        spec = {'runner': 'bounded-tcp-v1', 'config': {}}
        config = dict(MemorySize=128, Timeout=5, Runtime='python3.12', Handler='index.handler')
        with patch.object(autonomy_costs, 'bounded_quote', return_value={'quote': {'function_version': TARGET}, 'cents': 1}):
            for change in [dict(MemorySize=256), dict(Timeout=6), dict(Runtime='python3.13'), dict(Handler='other.handler'), dict(EphemeralStorage={'Size': 1024}), dict(VpcConfig={'VpcId': 'vpc-unapproved'})]:
                self.client.get_function_configuration.return_value = dict(config, **change)
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'resource configuration'): execution.bounded_response(self.job, self.state, spec)
            self.client.invoke.assert_not_called(); self.launch_claimed = False
            self.assertEqual(execution.bounded_response(self.job, self.state, spec), {'status': 'launch-unconfirmed'})

    def test_bounded_failures_and_oversized_payloads_keep_originals_without_a_success_receipt(self):
        spec = {'runner': 'bounded-tcp-v1', 'config': {}}; self.client.get_function_configuration.return_value = dict(MemorySize=128, Timeout=5, Runtime='python3.12', Handler='index.handler', EphemeralStorage={'Size': 512}, VpcConfig={})
        with patch.object(autonomy_costs, 'bounded_quote', return_value={'quote': {'function_version': TARGET}, 'cents': 1}):
            self.client.invoke.return_value = {'Payload': io.BytesIO(b'Original function failure'), 'StatusCode': 200, 'FunctionError': 'Unhandled'}
            self.assertEqual(execution.bounded_response(self.job, self.state, spec)['status'], 'failed')
            self.job['id'] = uid()
            self.client.invoke.return_value = {'Payload': io.BytesIO(b'x' * 32001), 'StatusCode': 200}
            with self.assertRaisesRegex(ValueError, 'transport bound'): execution.bounded_response(self.job, self.state, spec)
            self.assertFalse(self.appended[-1]['provenance']['complete']); self.assertEqual(len(self.blobs[self.appended[-1]['id']]), 32001)
            self.job['id'] = uid()
            self.client.invoke.return_value = {'Payload': io.BytesIO(b'not JSON'), 'StatusCode': 200}
            with self.assertRaises(ValueError): execution.bounded_response(self.job, self.state, spec)
            self.assertEqual(self.blobs[self.appended[-1]['id']], b'not JSON')

    def test_non_successful_synchronous_transport_cannot_be_reported_as_completed(self):
        raw = canonical(dict(version='network-observations-v1', observations=[], provenance={}, usage=None))
        self.client.get_function_configuration.return_value = dict(MemorySize=128, Timeout=5, Runtime='python3.12', Handler='index.handler')
        self.client.invoke.return_value = {'Payload': io.BytesIO(raw), 'StatusCode': 202}
        with patch.object(autonomy_costs, 'bounded_quote', return_value={'quote': {'function_version': TARGET}, 'cents': 1}), self.assertRaisesRegex(ValueError, 'transport'):
            execution.bounded_response(self.job, self.state, {'runner': 'bounded-tcp-v1', 'config': {}})
        self.assertEqual(self.appended[0]['provenance']['response_status_code'], 202)
        self.assertFalse(any(name == 'research_network_update' for name, _ in self.calls))
        self.state['phase'] = 'dispatching'; self.client.reset_mock()
        with self.assertRaisesRegex(ValueError, 'transport receipt'): execution.bounded_response(self.job, self.state, {'runner': 'bounded-tcp-v1', 'config': {}})
        self.client.invoke.assert_not_called()

    def test_bounded_terminal_cancelled_expired_and_ambiguous_captures_never_relaunch(self):
        for phase in ['completed', 'failed', 'cancelled']:
            self.state.update(phase=phase, data={'original': phase}); self.assertEqual(execution.bounded_response(self.job, self.state, {}), {'original': phase})
        for cancelled in [True, False]:
            self.state.update(phase='pending', cancel_requested=cancelled, deadline='2000-01-01T00:00:00Z'); self.assertEqual(execution.bounded_response(self.job, self.state, {})['status'], 'cancelled' if cancelled else 'failed')
        self.records['artifacts', str(uuid.uuid5(uuid.UUID(self.job['id']), 'bounded-original-response'))] = [{}, {}]
        with self.assertRaisesRegex(ValueError, 'Ambiguous'): execution.retained_bounded_response(self.job, self.state, {})
        self.client.invoke.assert_not_called()

    def test_arithmetic_uses_bound_original_text_and_has_no_cloud_call_or_imputed_source_value(self):
        recorded = Recorded(); spec = recorded.input_spec(recorded.jobs[0]); config = spec['config']
        self.blobs.update(recorded.bytes); self.records['sources', config['source_id']] = [recorded.data['sources', config['source_id']]]
        self.records['artifacts', config['text_artifact_id']] = [dict(provenance={'kind': 'derived-pdf-text'}, sha256=config['text_sha256'])]
        result = execution.arithmetic_response(self.job, self.state, spec); self.assertEqual(result['status'], 'completed'); self.assertFalse(result['resources']['external_network_experiment']); self.client.invoke.assert_not_called()
        for state, expected in [(dict(phase='dispatching'), 'launch-unconfirmed'), (dict(phase='pending', cancel_requested=True), 'cancelled'), (dict(phase='pending', deadline='2000-01-01T00:00:00Z'), 'failed')]:
            self.assertEqual(execution.arithmetic_response(self.job, dict(self.state, **state), spec)['status'], expected)
        self.assertEqual(execution.arithmetic_response(self.job, dict(self.state, phase='completed', data={'original': True}), spec), {'original': True})
        self.launch_claimed = False; self.assertEqual(execution.arithmetic_response(self.job, self.state, spec)['status'], 'launch-unconfirmed'); self.launch_claimed = True
        self.records['artifacts', config['text_artifact_id']][0]['sha256'] = '0' * 64
        result = execution.arithmetic_response(self.job, self.state, spec); self.assertEqual(result['status'], 'failed'); self.assertIsNone(result['output'])

    def test_cancel_and_pending_keep_original_job_and_request_identity(self):
        self.assertEqual(execution.pending(), [{'job_id': self.job['id']}]); request = uid()
        execution.cancel(self.study, self.actor, {'request_id': request, 'job_id': self.job['id'], 'reason': 'Owner stopped the test'})
        self.assertEqual(self.calls[-1], ('research_network_cancel', dict(p_study=self.study, p_actor=self.actor, p_job=self.job['id'], p_id=request, p_reason='Owner stopped the test')))
        with self.assertRaisesRegex(ValueError, 'original job'): execution.cancel(self.study, self.actor, {'job_id': self.job['id']})

    def output(self, protocol):
        return dict(version='network-observations-v1', observations=[dict(observation_key='sample-' + str(i), metric=m['name'], units=m['units'], value=0, status='recorded', reason=None, metadata={}) for m in protocol['document']['metrics'] for i in range(m.get('planned_observations', 1))], provenance={'fixture': 'Software test only'}, usage=None)

    def test_lost_launch_acknowledgement_polls_original_identity_and_preserves_zero_measurements(self):
        protocol = {'id': uid(), 'document': document(), 'sha256': 'a' * 64}; spec = network_spec.specs(protocol['document'])[0]
        response = dict(status='completed', started_at=STAMP, ended_at=None, output=self.output(protocol), artifacts=[{'id': uid(), 'sha256': 'b' * 64}], resources={'instances': 1})
        with patch.dict(os.environ, {'NETWORK_EMULATED_FUNCTION': TARGET}), patch.object(execution, 'invoke', side_effect=[TimeoutError('Original launch acknowledgement lost'), response]) as invoke:
            result = execution.advance(self.job, {'id': uid()}, protocol, canonical(spec))
        self.assertEqual([c.args for c in invoke.call_args_list], [(spec, self.job['id'], 'launch'), (spec, self.job['id'], 'status')])
        self.assertEqual(result['status'], 'awaiting-review'); finish = self.calls[-1][1]; records = finish['p_records']
        self.assertEqual(finish['p_job'], self.job['id']); self.assertEqual(finish['p_token'], self.job['lease_token'])
        run = next(v['record'] for v in records if v['kind'] == 'runs'); measurement = next(v['record'] for v in records if v['kind'] == 'measurements')
        self.assertEqual(run['status'], 'complete'); self.assertEqual(measurement['value'], 0); self.assertIsNone(run['usage']['measured_charges_usd']); self.assertEqual(run['provenance']['automatic_retries'], 0)
        self.assertTrue(run['ended_at']); self.assertEqual(run['provenance']['original_artifacts'], response['artifacts'])
        artifact = records[0]['record']; self.assertEqual(run['output_sha256'], digest(self.blobs[artifact['id']]))

    def test_nonterminal_work_only_polls_or_cancels_and_never_publishes_partial_measurements(self):
        protocol = {'document': document()}; spec = network_spec.specs(protocol['document'])[0]
        for cancelled in [False, True]:
            self.state.update(phase='running', cancel_requested=cancelled)
            with patch.dict(os.environ, {'NETWORK_EMULATED_FUNCTION': TARGET}), patch.object(execution, 'invoke', return_value={'status': 'cleaning' if cancelled else 'running'}) as invoke:
                result = execution.advance(self.job, {'id': uid()}, protocol, canonical(spec))
            self.assertEqual(result['status'], 'running'); invoke.assert_called_once_with(spec, self.job['id'], 'cancel' if cancelled else 'status')
        self.assertFalse(self.blobs); self.assertFalse(any(name == 'research_queue_finish' for name, _ in self.calls))

    def test_terminal_failure_retains_empty_observation_envelope_without_relaunch_or_imputed_values(self):
        protocol = {'document': document()}; spec = network_spec.specs(protocol['document'])[0]
        self.state.update(phase='failed', data=dict(status='failed', started_at=STAMP, ended_at=STAMP, output=None, reason='Recorded executor failure'))
        with patch.dict(os.environ, {'NETWORK_EMULATED_FUNCTION': TARGET}), patch.object(execution, 'invoke') as invoke:
            execution.advance(self.job, {'id': uid()}, protocol, canonical(spec))
        invoke.assert_not_called(); records = self.calls[-1][1]['p_records']; run = next(r['record'] for r in records if r['kind'] == 'runs')
        self.assertEqual(run['status'], 'failed'); self.assertEqual(run['reason'], 'Recorded executor failure'); self.assertFalse(any(r['kind'] == 'measurements' for r in records))
        self.assertFalse(any(name == 'research_network_update' for name, _ in self.calls)); envelope = json.loads(next(iter(self.blobs.values())))
        self.assertEqual(envelope['observations'], []); self.assertEqual(envelope['provenance']['failure'], 'Recorded executor failure')

    def arithmetic_protocol(self, mode='numeric'):
        recorded = Recorded(); protocol = copy.deepcopy(recorded.protocol); cfg = protocol['configurations'][0]['details']['test_config']; raw = recorded.bytes[cfg['text_artifact_id']]
        if mode == 'strict-increase':
            cfg['calculations'] = [dict(id='trend', operation='strict-increase', inputs=['control', 'treatment'], reported_cell=None, decimal_places=0)]
            protocol['metrics'] = [dict(name='trend', units='boolean', planned_observations=1)]
        elif mode == 'high-precision':
            packet = json.loads(raw); packet['pages'][0]['text'] = packet['pages'][0]['text'].replace('35.0%', '35.000000000000000000000000000001%')
            for cell in cfg['cells']: cell['quote'] = packet['pages'][0]['text']
            raw = canonical(packet); cfg['text_sha256'] = digest(raw)
        self.blobs[cfg['text_artifact_id']] = raw
        self.records['sources', cfg['source_id']] = [recorded.data['sources', cfg['source_id']]]
        self.records['artifacts', cfg['text_artifact_id']] = [dict(provenance={'kind': 'derived-pdf-text'}, sha256=cfg['text_sha256'])]
        protocol = network_spec.bind_inputs(protocol)
        return {'id': uid(), 'sha256': digest(canonical(protocol)), 'document': protocol}, network_spec.specs(protocol)[0]

    def test_source_calculations_keep_published_numeric_cells_distinct_from_computed_observations(self):
        protocol, spec = self.arithmetic_protocol(); self.assertEqual(execution.advance(self.job, {'id': uid()}, protocol, canonical(spec))['status'], 'awaiting-review')
        records = self.calls[-1][1]['p_records']; published = [r['record'] for r in records if r['kind'] == 'published_values']; measurements = [r['record'] for r in records if r['kind'] == 'measurements']
        self.assertEqual([r['value'] for r in published], [35, 25.2]); self.assertEqual([r['published_id'] for r in measurements], [r['id'] for r in published])
        self.assertTrue(all(r['source_id'] == spec['config']['source_id'] for r in published)); self.client.invoke.assert_not_called()
        summaries = [r['record'] for r in records if r['kind'] == 'summaries']; self.assertTrue(all(r['statistics']['numerical_consistency']['consistent'] == 1 for r in summaries))
        self.assertIn('not an empirical paper verdict', summaries[0]['statistics']['numerical_consistency']['scope'])

    def test_boolean_criteria_and_inexact_float_roundtrips_do_not_invent_published_numeric_cells(self):
        for mode, expected in [('strict-increase', 0), ('high-precision', 1)]:
            self.job['id'] = uid(); protocol, spec = self.arithmetic_protocol(mode)
            execution.advance(self.job, {'id': uid()}, protocol, canonical(spec)); records = self.calls[-1][1]['p_records']
            self.assertEqual(sum(r['kind'] == 'published_values' for r in records), expected)
            if mode == 'strict-increase':
                measurement = next(r['record'] for r in records if r['kind'] == 'measurements'); self.assertEqual((measurement['value'], measurement['published_id']), (1, None))

    def test_bounded_saved_terminal_state_uses_its_original_output_without_a_fresh_lambda_call(self):
        protocol = document(); protocol['execution']['runner'] = 'bounded-tcp-v1'; protocol['execution']['limits'].update(wall_seconds=60, max_output_bytes=32000, max_network_bytes=1024)
        protocol['configurations'][0]['details']['test_config'] = dict(payload_bytes=32, exchanges=2, application_delay_ms=0)
        protocol['metrics'] = [dict(name=name, units=units, planned_observations=2) for name, units in [('round_trip_time_ms', 'ms'), ('received_bytes', 'bytes')]]
        protocol = {'document': network_spec.bind_inputs(protocol)}; spec = network_spec.specs(protocol['document'])[0]
        self.state.update(phase='completed', data=dict(status='completed', started_at=STAMP, ended_at=STAMP, output=self.output(protocol)))
        with patch.dict(os.environ, {'NETWORK_BOUNDED_FUNCTION': TARGET}): execution.advance(self.job, {'id': uid()}, protocol, canonical(spec))
        self.client.invoke.assert_not_called(); self.assertEqual(next(r['record'] for r in self.calls[-1][1]['p_records'] if r['kind'] == 'runs')['status'], 'complete')

    def test_network_command_failure_details_reference_the_unchanged_original_instead_of_repeating_command_outputs(self):
        recorded = Recorded(arithmetic_mode=False); protocol = {'id': uid(), 'sha256': digest(canonical(recorded.protocol)), 'document': recorded.protocol}; spec = recorded.input_spec(recorded.jobs[0])
        command = dict(argv=['tc', 'qdisc', 'add'], returncode=127, stderr='Synthetic tc command unavailable', stdout='Original retained output')
        metric = recorded.protocol['metrics'][0]
        envelope = dict(version='network-observations-v1', observations=[dict(observation_key='loss:0', metric=metric['name'], units=metric['units'], value=0, status='recorded', reason=None, metadata={})], provenance={'commands': [command], 'implementation_sha256': 'a' * 64}, usage=None)
        original = canonical(envelope)
        run, rows, summaries = execution.evidence(self.job, {'id': uid()}, protocol, spec, original, dict(status='completed', started_at=STAMP, ended_at=STAMP))
        producer = run['provenance']['producer']; self.assertEqual(producer['command_count'], 1); self.assertNotIn('commands', producer)
        self.assertEqual(producer['commands_sha256'], digest(canonical([command]))); self.assertEqual(producer['raw_envelope_sha256'], digest(original))
        self.assertEqual(producer['raw_artifact_id'], str(uuid.uuid5(uuid.UUID(self.job['id']), 'network-output')))
        self.assertEqual(producer['environment_failures'][0]['code'], 'network-command-failed'); self.assertEqual(rows[0]['value'], 0); self.assertEqual(run['status'], 'partial')
        self.assertGreater(sum(row['missing'] for row in summaries), 0); self.assertEqual(json.loads(original)['provenance']['commands'], [command])


if __name__ == '__main__': unittest.main()
