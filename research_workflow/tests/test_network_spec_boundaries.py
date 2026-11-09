"""Frozen networking input and observation contracts, without executing jobs."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import network_spec
import netem_iperf
from workflow import canonical
from test_executable_methods import arithmetic_fixture, sweep_config
from test_network_execution import document
from test_network_traffic_allowance import built_protocol


PLACEMENT = dict(region='us-east-1', zone_id='use1-az1', instance_type='t3.medium')


def emulated():
    return dict(num_clients=2, client_delays_ms=[0, 20], client_ccas=['cubic', 'bbr'],
        client_file_sizes_mbytes=[1, 2], client_start_delays_ms=[0, 10],
        bottleneck_all_client_rate_mbit=100, bottleneck_buffer_kbytes=125,
        loss_pct=0, snapshot_interval_ms=100, snapshot_metrics_source='kernel')


def real_world():
    return dict(server=copy.deepcopy(PLACEMENT), bottleneck=copy.deepcopy(PLACEMENT),
        receivers=[copy.deepcopy(PLACEMENT)], cca='cubic', duration_seconds=60,
        rate_mbit=100, buffer_kbytes=125, notes='Synthetic contract only; no machines launched.')


def protocol(runner):
    value = document()
    value['execution']['runner'] = runner
    if runner == 'bounded-tcp-v1':
        config = dict(payload_bytes=1024, exchanges=2, application_delay_ms=0)
        value['metrics'] = [dict(name=name, units=units, planned_observations=2) for name, units in network_spec.BUILTIN_METRICS[runner].items()]
        value['execution']['limits'].update(wall_seconds=60, max_output_bytes=32000, max_network_bytes=4096)
    elif runner == 'source-arithmetic-v1':
        config, _ = arithmetic_fixture()
        value['metrics'] = [dict(name='gain', units='%', planned_observations=1), dict(name='delta', units='Mbit/s', planned_observations=1)]
        value['execution']['limits']['wall_seconds'] = 60
    elif runner == 'netem-iperf-v1':
        config = dict(sweep_config(), duration_seconds=10)
        value['metrics'] = [dict(name=name, units=units, planned_observations=len(config['loss_levels_pct'])) for name, units in netem_iperf.METRICS.items()]
        value['execution']['limits'].update(wall_seconds=600, max_output_bytes=1000000, max_network_bytes=10000000000)
    else:
        raise AssertionError(runner)
    value['configurations'][0]['details']['test_config'] = config
    return value


class NetworkSpecificationBoundaries(unittest.TestCase):
    def rejects_config(self, runner, original, cases):
        for label, fields in cases:
            value = copy.deepcopy(original)
            value.update(fields)
            before = copy.deepcopy(value)
            with self.subTest(runner=runner, failure=label), self.assertRaises(ValueError):
                network_spec.config(runner, value)
            self.assertEqual(value, before, 'Validation must retain the submitted configuration.')

    def test_numeric_and_placement_boundaries_reject_coercion_and_unsupported_identities(self):
        for value in [True, '1', None, float('nan'), float('inf'), -1, 11]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                network_spec.number(value, 'measurement', 0, 10)
        self.assertEqual(network_spec.number(0, 'measurement', 0, 10), 0)
        self.assertEqual(network_spec.number(10, 'measurement', 0, 10), 10)
        for value in [None, [], {}, dict(PLACEMENT, extra='unreviewed'), dict(PLACEMENT, region='invalid'), dict(PLACEMENT, zone_id='invalid'), dict(PLACEMENT, instance_type='t3.xlarge')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                network_spec.placement(value)
        network_spec.placement(PLACEMENT)
        with self.assertRaisesRegex(ValueError, 'object'):
            network_spec.config('emulated-netem-v1', [])
        with self.assertRaisesRegex(ValueError, 'registered runner'):
            network_spec.config('unknown-runner', {})

    def test_emulated_configuration_checks_counts_arrays_workloads_and_snapshot_source(self):
        self.assertEqual(network_spec.config('emulated-netem-v1', emulated()), 1)
        cases = [('extra', dict(unreviewed=True)), ('client-count', dict(num_clients=True)),
            ('delay-array', dict(client_delays_ms=[])), ('delay-type', dict(client_delays_ms=None)),
            ('payload-array', dict(client_file_sizes_mbytes=[1])), ('start-array', dict(client_start_delays_ms=[0])),
            ('ccas-type', dict(client_ccas=None)), ('ccas-count', dict(client_ccas=['cubic'])),
            ('ccas-name', dict(client_ccas=['cubic', 'unknown'])), ('script', dict(script='execute.py')),
            ('script-count', dict(script='netem_nines.py', num_clients=1, client_delays_ms=[0], client_file_sizes_mbytes=[1], client_start_delays_ms=[0], client_ccas=['cubic'])),
            ('source', dict(snapshot_metrics_source='invented'))]
        self.rejects_config('emulated-netem-v1', emulated(), cases)
        for script in ['netem_nines.py', 'netem_cubic_benchmark_nines.py', 'netem_cubic_benchmark_hotnets.py']:
            self.assertEqual(network_spec.config('emulated-netem-v1', dict(emulated(), script=script)), 1)

    def test_multiple_bottlenecks_require_two_bounded_rates_buffers_and_a_client_partition(self):
        value = dict(emulated(), script='netem_multi_bottleneck.py', topology='parking-lot', bottleneck_rates_mbit=[100, 50], bottleneck_buffers_kbytes=[0, 125])
        self.assertEqual(network_spec.config('emulated-netem-v1', value), 1)
        dumbbell = dict(value, topology='dumbbell', client_groups=[1, 1])
        self.assertEqual(network_spec.config('emulated-netem-v1', dumbbell), 1)
        cases = [('topology', dict(topology='unknown')), ('rates-count', dict(bottleneck_rates_mbit=[1])),
            ('rates-type', dict(bottleneck_rates_mbit=None)), ('buffers-count', dict(bottleneck_buffers_kbytes=[])),
            ('rate-bound', dict(bottleneck_rates_mbit=[0, 100])), ('buffer-bound', dict(bottleneck_buffers_kbytes=[-1, 125]))]
        cases += [('groups-' + str(i), dict(client_groups=groups)) for i, groups in enumerate([None, [], [1], [True, 1], [0, 2], [1, 2]])]
        self.rejects_config('emulated-netem-v1', dumbbell, cases)

    def test_real_world_configuration_requires_explicit_topology_machine_count_cca_and_notes(self):
        self.assertEqual(network_spec.config('real-world-iperf-v1', real_world()), 3)
        self.assertEqual(network_spec.config('real-world-iperf-v1', dict(real_world(), receivers=[PLACEMENT] * 16)), 18)
        self.rejects_config('real-world-iperf-v1', real_world(), [
            ('extra', dict(extra=True)), ('receivers-type', dict(receivers=None)), ('receivers-empty', dict(receivers=[])),
            ('receivers-bound', dict(receivers=[PLACEMENT] * 17)), ('cca', dict(cca='unknown')),
            ('notes-type', dict(notes=None)), ('notes-bound', dict(notes='x' * 4001)),
        ])

    def test_packages_require_safe_private_input_names_hashes_versions_and_bounded_arguments(self):
        config = document('custom-package-v1')['configurations'][0]['details']['test_config']
        reference = dict(artifact_id=config['package']['artifact_id'], sha256='a' * 64, version='retained-v1')
        cases = [('inputs-type', dict(private_inputs=[])), ('inputs-count', dict(private_inputs={str(i): reference for i in range(9)})),
            ('package-type', dict(package=[])), ('package-kind', dict(package={'kind': 'shell'})),
            ('arguments-type', dict(arguments='run command')), ('arguments-count', dict(arguments=['x'] * 33)),
            ('arguments-value', dict(arguments=[1])), ('arguments-bound', dict(arguments=['x' * 1001])),
            ('parameters', dict(parameters=[]))]
        for label, item in [('type', None), ('shape', {}), ('hash', dict(reference, sha256='invalid')), ('version-type', dict(reference, version=1)), ('version-empty', dict(reference, version=''))]:
            cases.append(('input-' + label, dict(private_inputs={'provider.json': item})))
        cases.append(('input-name', dict(private_inputs={'../credentials': reference})))
        for version in [None, '', 'x' * 201]:
            cases.append(('package-version-' + str(version)[:10], dict(package=dict(config['package'], version=version))))
        self.rejects_config('custom-package-v1', config, cases)
        container = dict(kind='container', version='retained-container-v1', image='public.ecr.aws/synthetic/experiment@sha256:' + 'a' * 64)
        self.assertEqual(network_spec.config('custom-package-v1', dict(config, package=container, private_inputs={'provider.json': reference})), 1)
        self.rejects_config('built-package-v1', config, [('missing-project', {}), ('container', dict(package=container, private_inputs={'project.zip': reference}))])

    def test_execution_contract_rejects_unreviewed_resources_and_limit_shapes(self):
        self.assertEqual(network_spec.specs({}), [])
        for execution in [[], {}, dict(document()['execution'], version='future'), dict(document()['execution'], runner='unknown'), dict(document()['execution'], extra=True)]:
            with self.subTest(execution=execution), self.assertRaises(ValueError):
                network_spec.specs(dict(document(), execution=execution), verify_hashes=False)
        for resources in [None, 'exclusive', ['a'] * 2, [str(i) for i in range(17)], [1], ['../bad'], ['']]:
            value = document(); value['execution']['exclusive_resources'] = resources
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                network_spec.specs(value, verify_hashes=False)
        for limits in [None, {}, dict(document()['execution']['limits'], extra=1)]:
            value = document(); value['execution']['limits'] = limits
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                network_spec.specs(value, verify_hashes=False)
        for runner, field, invalid in [('custom-package-v1', 'max_output_bytes', 3000001), ('built-package-v1', 'wall_seconds', 1801)]:
            value = document(runner) if runner == 'custom-package-v1' else built_protocol()
            value['execution']['limits'][field] = invalid
            with self.subTest(runner=runner), self.assertRaises(ValueError):
                network_spec.specs(value, verify_hashes=False)
        value = document(); value['resource_limits']['wall_seconds'] = 60
        with self.assertRaisesRegex(ValueError, 'reviewed protocol wall'):
            network_spec.specs(value, verify_hashes=False)

    def test_bounded_tcp_requires_complete_diagnostic_metrics_counts_and_bidirectional_budget(self):
        original = protocol('bounded-tcp-v1')
        self.assertEqual(len(network_spec.specs(network_spec.bind_inputs(original))), 1)
        for fields in [dict(wall_seconds=61), dict(max_output_bytes=31999), dict(max_network_bytes=4095)]:
            value = copy.deepcopy(original); value['execution']['limits'].update(fields)
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, 'complete bidirectional payload budget'):
                network_spec.specs(value, verify_hashes=False)
        value = copy.deepcopy(original); value['metrics'][0]['planned_observations'] = 1
        with self.assertRaisesRegex(ValueError, 'equal exchanges'):
            network_spec.specs(value, verify_hashes=False)

    def test_arithmetic_and_tcp_sweeps_require_their_exact_declared_metric_contract(self):
        for runner in ['source-arithmetic-v1', 'netem-iperf-v1']:
            original = protocol(runner)
            self.assertEqual(len(network_spec.specs(network_spec.bind_inputs(original))), 1)
            for change in ['metric', 'count']:
                value = copy.deepcopy(original)
                if change == 'metric': value['metrics'].pop()
                else: value['metrics'][0]['planned_observations'] = 2
                with self.subTest(runner=runner, change=change), self.assertRaises(ValueError):
                    network_spec.specs(value, verify_hashes=False)
        value = protocol('source-arithmetic-v1'); value['execution']['replications'] = 2
        with self.assertRaisesRegex(ValueError, 'one invocation'):
            network_spec.specs(value, verify_hashes=False)

    def test_real_world_topology_cannot_exceed_the_frozen_instance_budget(self):
        value = document(); value['execution']['runner'] = 'real-world-iperf-v1'
        value['configurations'][0]['details']['test_config'] = real_world()
        value['metrics'] = [{'name': 'received_bytes', 'units': 'bytes'}]
        with self.assertRaisesRegex(ValueError, 'topology exceeds instance budget'):
            network_spec.specs(value, verify_hashes=False)

    def test_observation_schema_status_units_and_missing_reasons_are_explicit(self):
        metric = [dict(name='latency', units='ms', lower_bound=0, upper_bound=10)]
        row = dict(observation_key='first', metric='latency', units='ms', value=0, status='recorded', reason=None, metadata={})
        envelope = dict(version='network-observations-v1', observations=[row], provenance={}, usage=None)
        cases = [('rows-type', dict(observations={})), ('row-type', dict(observations=[None])),
            ('row-shape', dict(observations=[{}])), ('rows-budget', dict(observations=[row] * 3)),
            ('provenance', dict(provenance=[])), ('usage', dict(usage=[]))]
        cases += [('row-' + str(i), dict(observations=[dict(row, **change)])) for i, change in enumerate([
            dict(observation_key=''), dict(status='future'), dict(metadata=None), dict(status='missing', value=1, reason='not missing'),
            dict(status='missing', value=None, reason=None), dict(status='missing', value=None, reason='  '), dict(metric='undeclared')])]
        for label, fields in cases:
            value = copy.deepcopy(envelope); value.update(fields)
            with self.subTest(failure=label), self.assertRaises(ValueError):
                network_spec.observations(canonical(value), metric, 2)
        for status in ['missing', 'invalid', 'ambiguous', 'excluded']:
            value = dict(envelope, observations=[dict(row, status=status, value=None, reason='Original unavailable observation')])
            result = network_spec.observations(canonical(value), metric, 2)
            self.assertIsNone(result['observations'][0]['value'])
            self.assertEqual(result['observations'][0]['status'], status)
        raw = canonical(envelope).replace(b'"value":0', b'"value":1e-1000')
        result = network_spec.observations(raw, metric, 2)['observations'][0]
        self.assertEqual(result['status'], 'invalid')
        self.assertIsNone(result['value'])
        self.assertEqual(result['metadata']['original_numeric_lexeme'], '1e-1000')


if __name__ == '__main__':
    unittest.main()
