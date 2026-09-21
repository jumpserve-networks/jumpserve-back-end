"""Reporting regression tests use synthetic evidence, never AWS resources."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import config
import reports


def fixture():
    placement = {"region": "us-east-1", "zone_id": "use1-az1", "instance_type": "c6i.large"}
    settings = {"server": placement.copy(), "bottleneck": placement.copy(),
                "receivers": [placement.copy(), placement.copy()], "cca": "bbr",
                "duration_seconds": 10, "rate_mbit": 10, "buffer_kbytes": 125, "notes": "Hypothesis"}
    nodes = config.nodes_for(settings)
    for node in nodes:
        node.update(instance_id="i-" + node["name"], image_id="ami-test")
    job = {"job_id": "test-report", "owner": "researcher", "schema_version": 1, "status": "completed", "config": settings,
           "runtime_revision": "revision-1", "nodes": nodes, "created_at": 1}
    artifacts = {}
    for node in nodes:
        raw = {"node": node["name"], "job_id": job["job_id"], "success": True,
               "runtime_revision": job["runtime_revision"], "configuration": copy.deepcopy(settings),
               "placement": node.copy(), "kernel": "test-kernel", "iperf_version": "iperf 3.16\nhost-specific text",
               "effective_cca": "bbr", "planned_start_epoch": 1000, "started_at": 1000.001, "samples": []}
        if node["role"] == "receiver":
            received = 10_000_000 if node["name"] == "receiver-1" else 2_500_000
            raw["iperf"] = {"start": {"connected": [{"local_host": node["overlay_ip"], "local_port": 40000,
                "remote_host": "10.254.0.2", "remote_port": node["port"]}],
                "test_start": {"protocol": "TCP", "reverse": 1, "num_streams": 1}},
                "end": {"sum_received": {"bytes": received, "seconds": 10}, "sum_sent": {"retransmits": 4}, "sender_tcp_congestion": "bbr"},
                "intervals": [{"sum": {"start": 0, "end": 1, "seconds": 1, "bytes": 0, "sender": False}},
                              {"sum": {"start": 1, "end": 10, "seconds": 9, "bytes": received, "sender": False}}]}
        artifacts[node["name"]] = raw
    artifacts["server"]["samples"] = [{"epoch": 1001, "ss": "\n".join(
        f'ESTAB 0 0 10.254.0.2:{node["port"]} {node["overlay_ip"]}:40000\n\t bbr rtt:78/2 mss:1300 cwnd:20\n'
        f'ESTAB 0 0 10.254.0.2:{node["port"]} {node["overlay_ip"]}:49999\n\t bbr rtt:2/1 mss:1300 cwnd:10'
        for node in nodes if node["role"] == "receiver") }]
    artifacts["bottleneck"]["samples"] = [
        {"epoch": 1000 + second, "qdisc": [{"kind": "htb", "handle": "1:", "backlog": 999999},
            {"kind": "bfifo", "handle": "10:", "backlog": backlog, "drops": second, "bytes": 1000 * second}]}
        for second, backlog in [(0, 0), (1, 125000), (11, 0)]]
    return job, artifacts


class ReportTests(unittest.TestCase):
    def test_units_zero_intervals_and_exact_data_socket(self):
        job, raw = fixture()
        result = reports.build_report(job, raw)
        self.assertTrue(result["comparison"]["eligible"], result["comparison"])
        self.assertEqual(result["summary"]["combined_mean_mbit_per_second"], 10)
        self.assertAlmostEqual(result["summary"]["jain_fairness"], 100 / 136)
        self.assertEqual(result["receivers"][0]["rtt"]["median"], 78)
        self.assertEqual(result["receivers"][0]["tcp"][0]["cwnd_bytes"], 26000)
        self.assertEqual(result["receivers"][0]["throughput"][0]["mbit_per_second"], 0)
        self.assertEqual(result["queue"][1]["queue_delay_ms"], 100)
        self.assertEqual(result["summary"]["queue_delay"]["samples"], 2)  # no post-test dilution

    def test_missing_receiver_does_not_invent_zero_or_full_aggregate(self):
        job, raw = fixture()
        del raw["receiver-2"]
        result = reports.build_report(job, raw)
        self.assertFalse(result["comparison"]["eligible"])
        self.assertIsNone(result["summary"]["combined_mean_mbit_per_second"])
        self.assertIsNone(result["summary"]["jain_fairness"])
        self.assertIsNone(result["receivers"][1]["mean_mbit_per_second"])

    def test_no_rtt_from_control_socket_or_zero_placeholder(self):
        job, raw = fixture()
        raw["server"]["samples"][0]["ss"] = raw["server"]["samples"][0]["ss"].replace('rtt:78/2', 'rtt:0/0')
        result = reports.build_report(job, raw)
        self.assertIsNone(result["receivers"][0]["rtt"]["median"])
        self.assertTrue(any('RTT' in warning for warning in result["warnings"]))
        self.assertIsNone(reports.socket_metrics('ESTAB 0 0 10.254.0.2:5201 10.254.0.10:49999\n bbr rtt:2 mss:1000 cwnd:20',
            '10.254.0.2:5201', '10.254.0.10:40000')["rtt_ms"])

    def test_matching_keeps_all_configuration_and_software_except_cca_notes(self):
        job, raw = fixture()
        original = reports.build_report(job, raw)["comparison"]["key"]
        job["config"]["notes"] = 'Different hypothesis'
        job["config"]["cca"] = 'cubic'
        for artifact in raw.values():
            artifact["configuration"] = copy.deepcopy(job["config"])
            artifact["effective_cca"] = 'cubic'
            if 'iperf' in artifact:
                artifact['iperf']['end']['sender_tcp_congestion'] = 'cubic'
        self.assertEqual(reports.build_report(job, raw)["comparison"]["key"], original)
        for field in ['duration_seconds', 'rate_mbit', 'buffer_kbytes']:
            changed_job, changed_raw = copy.deepcopy(job), copy.deepcopy(raw)
            changed_job['config'][field] += 1
            for artifact in changed_raw.values():
                artifact['configuration'] = copy.deepcopy(changed_job['config'])
            self.assertNotEqual(reports.build_report(changed_job, changed_raw)['comparison']['key'], original)
        raw['server']['kernel'] = 'different-kernel'
        self.assertNotEqual(reports.build_report(job, raw)['comparison']['key'], original)

    def test_inconsistent_provenance_and_failed_tests_are_excluded(self):
        for change in ['config', 'revision', 'placement', 'cca', 'status', 'identity']:
            job, raw = fixture()
            if change == 'config': raw['server']['configuration']['rate_mbit'] += 1
            if change == 'revision': raw['server']['runtime_revision'] = None
            if change == 'placement': raw['server']['placement']['zone_id'] = 'different'
            if change == 'cca': raw['server']['effective_cca'] = 'reno'
            if change == 'status': job['status'] = 'failed'
            if change == 'identity': raw['server']['job_id'] = 'other-test'
            with self.subTest(change=change):
                self.assertFalse(reports.build_report(job, raw)['comparison']['eligible'])

    def test_queue_counter_reset_is_not_reported_as_negative_drops(self):
        job, raw = fixture()
        raw['bottleneck']['samples'][-1]['qdisc'][1]['drops'] = 0
        result = reports.build_report(job, raw)
        self.assertIsNone(result['summary']['observed_queue_drops'])
        self.assertTrue(any('reset' in warning for warning in result['warnings']))

    def test_summary_matches_full_report_without_trace_payload(self):
        job, raw = fixture()
        full, summary = reports.build_report(job, raw), reports.build_report(job, raw, summary_only=True)
        self.assertEqual(full['summary'], summary['summary'])
        self.assertEqual(full['comparison'], summary['comparison'])
        self.assertNotIn('queue', summary)
        self.assertNotIn('tcp', summary['receivers'][0])

    def test_malformed_artifact_is_partial_evidence_and_never_crashes_report(self):
        for key, value in [('samples', None), ('iperf', []), ('preflight', {'ping': None}), ('iperf_version', 42)]:
            job, raw = fixture()
            raw['receiver-1'][key] = value
            result = reports.build_report(job, raw)
            self.assertFalse(result['comparison']['eligible'])
            self.assertIsNone(result['summary']['combined_mean_mbit_per_second'])
            self.assertTrue(any('malformed' in warning for warning in result['warnings']))

    def test_operator_validation_does_not_count_as_research(self):
        job, raw = fixture()
        job['owner'] = 'deployment-smoke-test'
        result = reports.build_report(job, raw)
        self.assertFalse(result['comparison']['eligible'])
        self.assertEqual(result['summary']['combined_mean_mbit_per_second'], 10)

    def test_invalid_or_overlapping_intervals_cannot_invent_samples(self):
        job, raw = fixture()
        intervals = raw['receiver-1']['iperf']['intervals']
        intervals.append({'sum': {'start': 1, 'end': 2, 'seconds': 1, 'bytes': 1000, 'sender': False}})
        intervals.append({'sum': {'start': 10, 'end': 11, 'seconds': 1, 'bytes': float('nan'), 'sender': False}})
        result = reports.build_report(job, raw)
        self.assertEqual(result['receivers'][0]['throughput_intervals'], 2)
        self.assertTrue(any('2 invalid' in warning for warning in result['warnings']))

    def test_artifact_bounds_and_body_closure(self):
        job, raw = fixture()
        streams = []
        def read(**kwargs):
            name = kwargs['Key'].split('/')[-1][:-5]
            content = json.dumps(raw[name]).encode()
            body = io.BytesIO(content)
            streams.append(body)
            return {'Body': body, 'ContentLength': len(content), 'VersionId': 'version-1'}
        client = MagicMock()
        client.get_object.side_effect = read
        with patch.object(reports.cloud, 'client', return_value=client), patch.dict(os.environ, RESULTS_BUCKET='private-results'):
            report = reports.load_report(job)
            self.assertEqual(len(report['sources']), 4)
            self.assertTrue(all(body.closed for body in streams))
            with patch.object(reports, 'MAX_TOTAL_BYTES', 1):
                report = reports.load_report(job)
                self.assertFalse(report['comparison']['eligible'])
                self.assertTrue(all(body.closed for body in streams))

    def test_signed_in_researchers_share_reports_but_only_owner_manages_test(self):
        job, _ = fixture()
        event = {'rawPath': '/real-world/reports/test-report', 'requestContext': {'http': {'method': 'GET'}}}
        with patch.object(api.store, 'load', return_value=job), patch.object(reports, 'load_report', return_value={}) as load:
            self.assertFalse(api.dispatch(event, 'another-researcher')['can_manage'])
            load.assert_called_once_with(job, summary_only=False)
            self.assertTrue(api.dispatch(event, 'researcher')['can_manage'])
            event['rawPath'] = '/real-world/tests/test-report/cancel'
            event['requestContext']['http']['method'] = 'POST'
            with self.assertRaises(api.HttpError) as error:
                api.dispatch(event, 'someone-else')
            self.assertEqual(error.exception.status, 404)

    def test_anonymous_report_reads_do_not_require_authentication(self):
        job, _ = fixture()
        with patch.object(api.store, 'load', return_value=job), patch.object(reports, 'load_report', return_value={}), \
                patch.object(api, 'authenticate') as authenticate:
            result = api.handler({'rawPath': '/real-world/reports/test-report', 'requestContext': {'http': {'method': 'GET'}}}, None)
            self.assertEqual(result['statusCode'], 200)
            self.assertFalse(json.loads(result['body'])['can_manage'])
            authenticate.assert_not_called()

    def test_shared_catalog_paginates_without_exposing_owners_or_commands(self):
        job, _ = fixture()
        job['commands'] = {'private': 'internal-command'}
        page_key = {'job_id': 'test-report', 'schema_version': 1, 'created_at': 1}
        table = MagicMock()
        table.query.return_value = {'Items': [job], 'LastEvaluatedKey': page_key}
        event = {'rawPath': '/real-world/reports', 'requestContext': {'http': {'method': 'GET'}}}
        with patch.object(api.store, 'table', return_value=table):
            first = api.dispatch(event, 'another-researcher')
            self.assertNotIn('owner', first['tests'][0])
            self.assertNotIn('commands', first['tests'][0])
            event['queryStringParameters'] = {'cursor': first['cursor']}
            api.dispatch(event, 'another-researcher')
            self.assertEqual(table.query.call_args.kwargs['ExclusiveStartKey'], page_key)
            self.assertEqual(table.query.call_args.kwargs['IndexName'], 'reports-created')
            event['queryStringParameters'] = {'cursor': 'bad-cursor'}
            with self.assertRaises(ValueError):
                api.dispatch(event, 'another-researcher')

    def test_shared_downloads_only_sign_expected_measurement_objects(self):
        job, _ = fixture()
        client = MagicMock()
        client.list_objects_v2.return_value = {'Contents': [{'Key': 'test-report/server.json'}, {'Key': 'test-report/internal.txt'}]}
        client.generate_presigned_url.return_value = 'temporary-link'
        event = {'rawPath': '/real-world/reports/test-report/artifacts', 'requestContext': {'http': {'method': 'GET'}}}
        with patch.object(api.store, 'load', return_value=job), patch.object(api.cloud, 'client', return_value=client), patch.dict(os.environ, RESULTS_BUCKET='private-results'):
            self.assertEqual(api.dispatch(event, None), {'artifacts': [{'name': 'server.json', 'url': 'temporary-link'}]})
            client.generate_presigned_url.assert_called_once()


if __name__ == '__main__':
    unittest.main()
