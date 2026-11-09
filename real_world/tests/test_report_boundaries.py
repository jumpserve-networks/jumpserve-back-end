"""Malformed, incomplete and stale measurement evidence stays distinguishable."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reports
from test_reports import fixture


class EvidenceBoundaries(unittest.TestCase):
    def test_nested_container_failures_are_explicit_missing_evidence_without_aborting_other_nodes(self):
        malformed = [None, [], {'samples': [{'ss': None}]}, {'samples': [{'qdisc': [None]}]}, {'iperf': {'start': []}}, {'iperf': {'end': []}}, {'iperf': {'intervals': [None]}}, {'iperf': {'intervals': [{'sum': None}]}}, {'iperf': {'start': {'connected': [None]}}}, {'iperf': {'end': {'sum_received': []}}}]
        for value in malformed:
            job, raw = fixture(); raw['receiver-1'] = value
            with self.subTest(value=value):
                self.assertFalse(reports.valid_shape(value)); result = reports.build_report(job, raw)
            self.assertFalse(result['comparison']['eligible']); self.assertIsNone(result['summary']['combined_mean_mbit_per_second'])
            self.assertIsNotNone(result['receivers'][1]['mean_mbit_per_second'])
            self.assertTrue(any('malformed' in warning for warning in result['warnings']))

    def test_configuration_software_start_and_execution_provenance_each_prevent_false_comparisons(self):
        changes = [lambda job, raw: job.pop('runtime_revision'), lambda job, raw: raw['server'].pop('kernel'), lambda job, raw: raw['server'].pop('iperf_version'), lambda job, raw: job['nodes'][0].pop('image_id'), lambda job, raw: raw['server'].update(success=False), lambda job, raw: raw['server'].update(planned_start_epoch=None), lambda job, raw: raw['receiver-1'].update(planned_start_epoch=999), lambda job, raw: raw['receiver-1']['iperf']['end']['sum_received'].update(seconds=3)]
        for index, change in enumerate(changes):
            job, raw = fixture(); change(job, raw)
            with self.subTest(index=index):
                result = reports.build_report(job, raw)
                self.assertFalse(result['comparison']['eligible'])
                self.assertTrue(result['comparison']['exclusions'] or result['warnings'])
        self.assertEqual(reports.socket_metrics('', 'unrecorded-local', 'unrecorded-peer'), {'rtt_ms': None, 'cwnd_bytes': None})
        job, raw = fixture(); raw['receiver-1']['started_at'] = None
        result = reports.build_report(job, raw)
        self.assertIsNone(result['summary']['start_skew_ms'])
        self.assertTrue(any('actual measurement start timestamp is missing' in warning for warning in result['warnings']))
        self.assertTrue(result['comparison']['eligible'], 'Missing measured skew is a warning under the existing comparison contract')

    def test_tcp_samples_require_the_exact_data_tuple_and_finite_recorded_timestamps(self):
        job, raw = fixture(); node = next(node for node in job['nodes'] if node['role'] == 'receiver')
        original = raw['receiver-1']['iperf']
        for change in [{'local_host': '192.0.2.1'}, {'remote_host': '192.0.2.2'}, {'remote_port': 5999}, {'local_port': 0}]:
            iperf = copy.deepcopy(original); iperf['start']['connected'][0].update(change)
            with self.subTest(change=change): self.assertEqual(reports.tcp_trace(raw['server'], iperf, node, 1000), [])
        raw['server']['samples'].append({'epoch': None, 'ss': 'Unusable timestamp'})
        trace = reports.tcp_trace(raw['server'], original, node, 1000)
        self.assertEqual(len(trace), 1); self.assertEqual(trace[0]['rtt_ms'], 78)
        raw['bottleneck']['samples'].append({'epoch': None, 'qdisc': []})
        self.assertEqual(len(reports.queue_trace(raw['bottleneck'], 1000, 10)), 3)

    def test_missing_and_invalid_artifacts_keep_per_node_issues_and_other_original_sources(self):
        job, raw = fixture()
        originals = []
        def read(job_id, name, sources, max_bytes):
            if name == 'server': return None
            payload = b'[]' if name == 'bottleneck' else json.dumps(raw[name]).encode()
            original = {'name': name + '.json', 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload), 'version_id': None}
            originals.append(original); return payload, original
        with patch.object(reports.evidence, 'records', return_value=[]), patch.object(reports.evidence, 'read', side_effect=read):
            result = reports.read_evidence(job)
        self.assertFalse(result['comparison']['eligible']); self.assertEqual(result['sources'], originals)
        self.assertTrue(any('server: raw report has not been stored' in warning for warning in result['warnings']))
        self.assertTrue(any('bottleneck: Artifact is not a valid JSON object' in warning for warning in result['warnings']))
        self.assertIsNone(result['receivers'][0]['rtt']['median']); self.assertIsNotNone(result['receivers'][0]['mean_mbit_per_second'])

    def test_persistence_and_cache_accept_only_the_matching_recorded_job_version(self):
        job, raw = fixture(); job['updated_at'] = 20
        def read(job_id, name, sources, max_bytes):
            payload = json.dumps(raw[name]).encode(); return payload, {'name': name + '.json', 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload), 'version_id': None}
        with patch.object(reports.evidence, 'records', return_value=[]), patch.object(reports.evidence, 'read', side_effect=read), patch.object(reports.database, 'rest') as rest:
            result = reports.persist_report(job)
        rest.assert_called_once_with('real_world_reports', 'POST', {'job_id': job['job_id'], 'report': result}, headers={'Prefer': 'resolution=merge-duplicates'})
        for status, rows in [('running', []), ('completed', []), ('completed', [{'report': {'job': {'updated_at': 19}}}])]:
            with self.subTest(status=status, rows=rows), patch.object(reports.database, 'rest', return_value=rows), patch.object(reports.evidence, 'records', return_value=[]), patch.object(reports.evidence, 'read', side_effect=read) as reads:
                refreshed = reports.load_report(dict(job, status=status), summary_only=True)
            self.assertEqual(reads.call_count, 4); self.assertNotIn('queue', refreshed)
        with patch.object(reports.database, 'rest', return_value=[{'report': result}]), patch.object(reports, 'read_evidence') as reads:
            self.assertEqual(reports.load_report(job), result)
        reads.assert_not_called(); self.assertIn('queue', result); self.assertIn('throughput', result['receivers'][0])
        without_receivers = dict(result, receivers=[])
        with patch.object(reports.database, 'rest', return_value=[{'report': without_receivers}]):
            self.assertEqual(reports.load_report(job, summary_only=True)['receivers'], [])


if __name__ == '__main__': unittest.main()
