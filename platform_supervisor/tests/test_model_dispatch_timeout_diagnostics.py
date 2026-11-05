"""Qualification fixtures are not installation or reconciliation receipts."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from platform_supervisor import capability_model_dispatch_timeout_diagnostics as diagnostics
from platform_supervisor.capabilities import descriptor
from platform_supervisor.common import Hold


class Evidence:
    def __init__(self):
        self.reads = []
        self.originals = {}
        self.observed = dict(
            context_transport={'original_logical_sha256': 'c' * 64},
            error_code='runtime-TimeoutExpired', wall_seconds=130.34013312100433,
            provider_request_sent=False, provider_calls=0, status='unknown',
            runtime='openclaw-production-v1', node_version='24.16.0', openclaw_version='2026.10.5')
        self.packet = dict(assessment_id=diagnostics.ASSESSMENT, assessment_task_id='task',
                           blocker=dict(source_kind='dispatch', source_id=diagnostics.DISPATCH),
                           artifacts=[], dispatches=[dict(id=diagnostics.DISPATCH, status='unknown',
                           request_sha256='c' * 64, provenance={'artifacts': []})],
                           task=dict(id='task', status='finished', stage='intake'), model_reconciliations=[])
        self.add('logical.json', b'fixture request', 'logical', sha='c' * 64)
        self.receipt()

    def add(self, name, raw, ident, sha=None):
        sha = sha or hashlib.sha256(raw).hexdigest()
        row = dict(id=ident, sha256=sha, byte_count=len(raw))
        self.packet['dispatches'][0]['provenance']['artifacts'].append(dict(row, name=name))
        self.packet['artifacts'].append(dict(row, provenance={'dispatch_id': diagnostics.DISPATCH}))
        self.originals[ident] = raw

    def receipt(self, raw=None):
        for rows in (self.packet['artifacts'], self.packet['dispatches'][0]['provenance']['artifacts']):
            rows[:] = [r for r in rows if r['id'] != 'receipt']
        self.add('receipt.json', raw if raw is not None else json.dumps(self.observed).encode(), 'receipt')

    def original(self, ident):
        self.reads.append(ident)
        return self.originals[ident]


class TimeoutDiagnostics(unittest.TestCase):
    def setUp(self):
        self.context = Evidence()

    def collect(self):
        result = diagnostics.collect(self.context)
        self.assertFalse(result['replay_authorized'])
        self.assertLess(len(json.dumps(result)), 16000)
        return result

    def unavailable(self):
        result = self.collect()
        self.assertEqual(result['availability'], 'unavailable')
        self.assertNotIn('observed', result)
        return result

    def test_receipt_only_timeout_and_finished_task_are_not_reconciliation(self):
        before = copy.deepcopy(self.context.packet)
        result = self.collect()
        self.assertTrue(result['observed_timeout'])
        self.assertEqual(result['observed']['wall_seconds'], 130.34013312100433)
        self.assertIs(result['observed']['provider_request_sent'], False)
        self.assertEqual(result['observed']['provider_calls'], 0)
        self.assertTrue(result['current_task']['terminal'])
        self.assertEqual(result['dispatch_status'], 'unknown')
        self.assertIsNone(result['runtime_source_identity'])
        self.assertEqual(result['existing_controller_reconciliation']['availability'], 'not-in-packet')
        self.assertEqual(result['cli_outputs']['stdout.bin']['availability'], 'not-indexed')
        self.assertEqual(result['cli_outputs']['stderr.bin']['availability'], 'not-indexed')
        self.assertEqual(self.context.reads, ['receipt'])
        self.assertEqual(self.context.packet, before)
        self.assertEqual(result['receipt']['sha256'], hashlib.sha256(self.context.originals['receipt']).hexdigest())

    def test_wrong_assessment_or_dispatch_never_reads_other_evidence(self):
        for key in ('assessment', 'dispatch'):
            with self.subTest(key=key):
                self.context = Evidence()
                if key == 'assessment': self.context.packet['assessment_id'] = 'other'
                else: self.context.packet['blocker']['source_id'] = 'other'
                self.unavailable()
                self.assertEqual(self.context.reads, [])

    def test_cross_dispatch_artifact_is_rejected_before_original_read(self):
        self.context.packet['artifacts'][1]['provenance']['dispatch_id'] = 'other'
        self.unavailable()
        self.assertEqual(self.context.reads, [])

    def test_missing_or_mismatched_request_hash(self):
        for value in (None, 'redacted', 'd' * 64):
            with self.subTest(value=value):
                self.context = Evidence()
                self.context.packet['dispatches'][0]['request_sha256'] = value
                self.unavailable()
                self.assertEqual(self.context.reads, [])

    def test_receipt_request_or_dispatch_mismatch_discards_partial_observations(self):
        for change in ({'context_transport': {}}, {'dispatch_id': 'other'}, {'assessment_id': 'other'}):
            with self.subTest(change=change):
                self.context = Evidence()
                self.context.observed.update(change)
                self.context.receipt()
                self.unavailable()

    def test_malformed_nonobject_and_deep_original(self):
        for raw in (b'not json', b'[]', b'\xff', b'{"status":1,"status":2}', b'[' * 2000 + b']' * 2000):
            with self.subTest(raw=raw[:20]):
                self.context = Evidence()
                self.context.receipt(raw)
                self.unavailable()

    def test_oversized_original_rejected_without_read(self):
        self.context.receipt(b' ' * (diagnostics.MAX_ORIGINAL + 1))
        self.unavailable()
        self.assertEqual(self.context.reads, [])

    def test_returned_original_hash_or_size_mismatch(self):
        self.context.originals['receipt'] = b'{}'
        self.unavailable()

    def test_ambiguous_or_missing_artifact_index(self):
        for mode in ('duplicate', 'missing', 'hash', 'size'):
            with self.subTest(mode=mode):
                self.context = Evidence()
                rows = self.context.packet['artifacts']
                if mode == 'duplicate': rows.append(copy.deepcopy(rows[-1]))
                if mode == 'missing': rows.pop()
                if mode == 'hash': rows[-1]['sha256'] = 'e' * 64
                if mode == 'size': rows[-1]['byte_count'] += 1
                self.unavailable()

    def test_recorded_cli_and_source_identity_are_passive_hash_bound_facts(self):
        self.context.observed.update(source_revision='a' * 40, exit_code=124)
        self.context.receipt()
        self.context.add('stdout.bin', b'private prompt must not appear', 'stdout')
        self.context.add('stderr.bin', b'timeout', 'stderr')
        result = self.collect()
        self.assertEqual(result['cli_outputs']['stdout.bin']['availability'], 'retained')
        self.assertEqual(result['runtime_source_identity'], {'source_revision': 'a' * 40})
        self.assertEqual(result['observed']['exit_code'], 124)
        self.assertNotIn('private prompt', json.dumps(result))

    def test_other_dispatch_outputs_are_never_used_as_missing_diagnostics(self):
        self.context.packet['artifacts'].append(dict(id='other-output', sha256='a' * 64,
            byte_count=12, provenance={'dispatch_id': 'other'}))
        result = self.collect()
        self.assertEqual(result['cli_outputs']['stderr.bin']['availability'], 'not-indexed')
        self.assertNotIn('other-output', self.context.reads)

    def test_existing_reconciliation_is_reported_not_inferred_or_applied(self):
        self.context.packet['model_reconciliations'] = [
            dict(id='other', dispatch_id='other'),
            dict(artifact_id='record', dispatch_id=diagnostics.DISPATCH, study_id=diagnostics.ASSESSMENT,
                 task_id='task', request_sha256='c' * 64, sha256='e' * 64, secret='never return')]
        result = self.collect()
        records = result['existing_controller_reconciliation']['records']
        self.assertEqual([r['artifact_id'] for r in records], ['record'])
        self.assertNotIn('secret', json.dumps(result))

    def test_cross_task_reconciliation_is_not_reported_as_bound(self):
        self.context.packet['model_reconciliations'] = [dict(dispatch_id=diagnostics.DISPATCH,
            study_id=diagnostics.ASSESSMENT, task_id='other', request_sha256='c' * 64)]
        self.unavailable()

    def test_missing_task_binding_fails_closed(self):
        self.context.packet['task']['id'] = 'other'
        self.unavailable()

    def test_persistence_uncertainty_propagates_without_inference(self):
        def uncertain(ident):
            raise Hold('Original identity unconfirmed.', 'reconciling')
        self.context.original = uncertain
        with self.assertRaises(Hold): self.collect()

    def test_invalid_and_oversized_packet_indexes_are_bounded(self):
        for key, value in (('blocker', []), ('dispatches', [None]),
                           ('artifacts', [{}] * 257), ('model_reconciliations', 'bad')):
            with self.subTest(key=key):
                self.context = Evidence()
                self.context.packet[key] = value
                self.unavailable()

    def test_literal_pack_and_delivered_summary_tool(self):
        path = Path(diagnostics.__file__)
        pack = descriptor(path.read_bytes(), path.name)
        self.assertEqual(pack['id'], 'model-dispatch-timeout-diagnostics')
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / 'describe.py'
            tool.write_text(pack['files']['describe.py'])
            result = subprocess.run([sys.executable, str(tool)], input=json.dumps({'view': self.collect()}).encode(),
                                    capture_output=True, timeout=5, check=True)
            self.assertFalse(json.loads(result.stdout)['replay_authorized'])
            rejected = subprocess.run([sys.executable, str(tool)], input=b'{}', capture_output=True, timeout=5)
            self.assertNotEqual(rejected.returncode, 0)


if __name__ == '__main__':
    unittest.main()
