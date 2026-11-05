"""Regressions use synthetic originals, not purported production receipts."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from platform_supervisor import capabilities
from platform_supervisor import capability_assessment_native_failure_evidence as tool
from platform_supervisor.common import Hold


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Context:
    def __init__(self):
        self.reads = []
        self.raw = {}
        # Only hashes are substituted for synthetic fixtures. Exact incident IDs
        # and production contract hashes are independently asserted below.
        self.required = {}
        for kind, (ident, _) in tool.REQUIRED.items():
            raw = ('PRIVATE_API_KEY=never-project-' + kind).encode()
            self.raw[ident] = raw
            self.required[kind] = (ident, sha(raw))
        call = dict(id=tool.CALL, dispatch_id=tool.DISPATCH, number=155,
                    status='failed', http_status=503, reason='subscription_sharing_user_unavailable')
        for kind, (ident, digest) in self.required.items():
            call[kind + '_artifact_id'] = ident
            call[kind + '_sha256'] = digest
        bridge_id = str(uuid.uuid4())
        self.raw[bridge_id] = b'{"private":"never-project-bridge"}'
        bridge = dict(id=bridge_id, sha256=sha(self.raw[bridge_id]), name='completion.json')
        self.packet = dict(assessment_id=tool.ASSESSMENT, assessment_task_id=tool.TASK,
            blocker=dict(id=tool.INCIDENT), task=dict(id=tool.TASK),
            native_calls=[call], artifacts=[bridge.copy()],
            dispatches=[dict(id=tool.DISPATCH, provenance=dict(provider_calls=1,
                provider_request_sent=True, response_received=True, native_tool_cleanup=True,
                container_cleanup='stopped', artifacts=[bridge]))],
            coverage=dict(native_calls_total=1, unresolved_native_total=0))

    def original(self, ident):
        self.reads.append(ident)
        return self.raw[ident]


class NativeFailureEvidence(unittest.TestCase):
    def run_collector(self, context):
        with patch.object(tool, 'REQUIRED', context.required):
            return tool.collect(context)

    def test_literal_pack_and_immutable_requested_fingerprints(self):
        name = 'capability_assessment_native_failure_evidence.py'
        raw = Path(tool.__file__).read_bytes()
        pack = capabilities.descriptor(raw, name)
        self.assertEqual(pack['id'], 'assessment-native-failure-evidence')
        self.assertEqual(pack['revision'], 1)
        self.assertEqual(tool.REQUIRED['request'], ('188d0474-9bf5-5ff0-8c53-d0cc28f63c9b', '1c62acb564fbfc93047b0a2742587855eed7df729af56fc420d91ff0c348fced'))
        self.assertEqual(tool.REQUIRED['response'], ('527d0536-3ebc-5162-96a4-c03beeadc872', '26f8dd29ba2931fa849e1f2fe7d0e63f9aeb03f1286d9e10642cfb70e0ac1ce0'))
        self.assertTrue(all(isinstance(v, str) and v for v in pack['files'].values()))

    def test_503_is_unconfirmed_even_with_cleanup_and_zero_unknown_native_calls(self):
        context = Context(); before = copy.deepcopy(context.packet)
        out = self.run_collector(context)
        self.assertEqual(out['dispatch_outcome'], 'unconfirmed')
        self.assertFalse(out['safe_to_replay']); self.assertFalse(out['reconciled'])
        self.assertFalse(out['scientific_completion'])
        self.assertEqual(out['call_coverage']['native_unknown_total'], 0)
        self.assertTrue(out['call_coverage']['dispatch_unconfirmed'])
        self.assertEqual(out['bridge']['container_cleanup'], 'stopped')
        self.assertEqual(out['bridge']['scope'], 'historical-dispatch-claims-not-current-qualification')
        self.assertEqual(context.packet, before)
        encoded = json.dumps(out)
        self.assertNotIn('never-project', encoded)
        self.assertNotIn('PRIVATE_API_KEY', encoded)
        self.assertLess(len(encoded), 32768)

    def test_identity_mismatches_and_duplicate_selected_rows_prevent_all_reads(self):
        for field in ('assessment_id', 'assessment_task_id', 'incident', 'dispatch', 'call', 'nested-task', 'scoped-task', 'duplicate-call', 'duplicate-dispatch'):
            c = Context()
            if field in ('assessment_id', 'assessment_task_id'): c.packet[field] = str(uuid.uuid4())
            elif field == 'incident': c.packet['blocker']['id'] = str(uuid.uuid4())
            elif field == 'dispatch': c.packet['native_calls'][0]['dispatch_id'] = str(uuid.uuid4())
            elif field == 'call': c.packet['native_calls'][0]['id'] = str(uuid.uuid4())
            elif field == 'nested-task': c.packet['task']['id'] = str(uuid.uuid4())
            elif field == 'scoped-task': c.packet['native_calls'][0]['task_id'] = str(uuid.uuid4())
            elif field == 'duplicate-call': c.packet['native_calls'] *= 2
            else: c.packet['dispatches'] *= 2
            with self.subTest(field=field):
                out = self.run_collector(c)
                self.assertEqual(out['status'], 'missing-prerequisite')
                self.assertEqual(c.reads, [])

    def test_hash_mismatch_and_ambiguous_or_invalid_index_never_substitute_original(self):
        for mode in ('pointer', 'bytes', 'ambiguous', 'invalid-index'):
            c = Context(); ident = c.required['request'][0]
            if mode == 'pointer': c.packet['native_calls'][0]['request_sha256'] = 'f' * 64
            elif mode == 'bytes': c.raw[ident] = b'corrupt original'
            else: c.packet['artifacts'].append(dict(id=ident, sha256='f'*64 if mode == 'ambiguous' else ['bad']))
            with self.subTest(mode=mode):
                out = self.run_collector(c)
                self.assertNotEqual(out['originals']['request']['status'], 'verified-original')
                if mode != 'bytes': self.assertNotIn(ident, c.reads)

    def test_nested_bridge_reference_is_not_an_index_and_is_never_resolved(self):
        c = Context(); bridge_id = c.packet['artifacts'][0]['id']; c.packet['artifacts'] = []
        out = self.run_collector(c)
        self.assertEqual(out['bridge']['artifacts'][0]['status'], 'missing-indexed-original')
        self.assertNotIn(bridge_id, c.reads)
        self.assertEqual(set(c.reads), {pair[0] for pair in c.required.values()})

    def test_missing_indexed_original_does_not_invoke_accessor(self):
        c = Context(); ident, digest = c.required['request']
        # Exercise the index gate directly because native refs are themselves
        # valid indexed originals in the frozen Evidence API.
        out = tool._original(c, {}, ident, digest)
        self.assertEqual(out['status'], 'missing-indexed-original')
        self.assertEqual(c.reads, [])

    def test_accessor_value_error_is_bounded_missing_but_hold_is_not_swallowed(self):
        c = Context()
        with patch.object(c, 'original', side_effect=ValueError('PRIVATE_API_KEY=do-not-echo')):
            out = self.run_collector(c)
        self.assertEqual(out['originals']['request']['status'], 'original-unavailable-or-outside-accessor-bound')
        self.assertNotIn('do-not-echo', json.dumps(out))
        with patch.object(c, 'original', side_effect=Hold('Retain uncertain read.', 'reconciling')):
            with self.assertRaises(Hold): self.run_collector(c)

    def test_truncation_incomplete_counts_duplicate_numbers_and_output_limits(self):
        for mode in ('truncated', 'count', 'missing-total', 'duplicate-number', 'oversize'):
            c = Context()
            if mode == 'truncated': c.packet['projection'] = dict(truncated=1)
            elif mode == 'count': c.packet['dispatches'][0]['provenance']['provider_calls'] = 8
            elif mode == 'missing-total': c.packet['coverage'] = {}
            elif mode == 'duplicate-number':
                row = c.packet['native_calls'][0].copy(); row['id'] = str(uuid.uuid4()); c.packet['native_calls'].append(row)
            else: c.packet['native_calls'] *= tool.MAX_ROWS + 1
            with self.subTest(mode=mode):
                out = self.run_collector(c)
                self.assertEqual(out['status'], 'missing-prerequisite')
                if mode == 'oversize': self.assertEqual(c.reads, [])
                else: self.assertEqual(out['call_coverage']['status'], 'incomplete')
                self.assertLess(len(json.dumps(out)), 32768)

    def test_order_is_native_sequence_not_uuid_or_packet_order(self):
        c = Context(); first = c.packet['native_calls'][0].copy()
        first.update(id=str(uuid.uuid4()), number=154, status='complete', http_status=200)
        c.packet['native_calls'].append(first)
        c.packet['coverage']['native_calls_total'] = 2
        c.packet['dispatches'][0]['provenance']['provider_calls'] = 2
        out = self.run_collector(c)
        self.assertEqual(out['call_coverage']['ordered_native_numbers'], [154, 155])
        self.assertEqual(out['call_coverage']['status'], 'complete-retained-ledger')

    def test_runtime_receipts_are_unavailable_not_inferred_from_historical_provenance(self):
        c = Context()
        c.packet['dispatches'][0]['provenance']['installed_fingerprints'] = dict(runtime='a'*64)
        out = self.run_collector(c)
        self.assertEqual(out['installed_fingerprints']['runtime']['status'], 'unavailable')
        receipt = dict(assessment_id=tool.ASSESSMENT, task_id=tool.TASK,
                       source_sha256='a'*64, qualification_sha256='b'*64, receipt_sha256='c'*64)
        c.packet['installed_fingerprints'] = dict(runtime=receipt, database=receipt.copy())
        out = self.run_collector(c)
        self.assertEqual(out['status'], 'diagnostics-collected')
        self.assertEqual(out['installed_fingerprints']['runtime']['status'], 'task-bound-producer-fingerprints-not-independently-verified')
        self.assertFalse(out['reconciled'])
        c.packet['installed_fingerprints']['database']['task_id'] = str(uuid.uuid4())
        self.assertEqual(self.run_collector(c)['installed_fingerprints']['database']['status'], 'unavailable')

    def test_frozen_evidence_accessor_verifies_scoped_storage_bytes_before_projection(self):
        c = Context(); storage_reads = []

        class Store:
            def request(self, path, raw=False):
                storage_reads.append(path)
                if path.startswith('/rest/v1/research_artifacts?'):
                    ident = path.split('id=eq.')[1].split('&')[0]
                    self_study = '&study_id=eq.' + tool.ASSESSMENT
                    if self_study not in path:
                        raise AssertionError('Assessment filter required')
                    data = c.raw[ident]
                    return [dict(id=ident, sha256=sha(data), byte_count=len(data),
                                 storage_path=tool.ASSESSMENT + '/' + sha(data))]
                if path.startswith('/storage/v1/object/research-workflow-raw/' + tool.ASSESSMENT + '/'):
                    return next(data for data in c.raw.values() if path.endswith(sha(data)))
                raise AssertionError('No other service access allowed')

        evidence = capabilities.Evidence(Store(), c.packet)
        with patch.object(tool, 'REQUIRED', c.required): out = tool.collect(evidence)
        self.assertEqual(out['originals']['response']['status'], 'verified-original')
        self.assertEqual(len(storage_reads), 6)
        before = len(storage_reads)
        with self.assertRaises(ValueError): evidence.original(str(uuid.uuid4()))
        self.assertEqual(len(storage_reads), before)

    def test_503_without_label_and_label_without_503_are_both_unconfirmed(self):
        for http, reason in ((503, 'different reason'), (200, 'subscription_sharing_user_unavailable')):
            c = Context(); c.packet['native_calls'][0].update(http_status=http, reason=reason)
            out = self.run_collector(c)
            self.assertEqual(out['dispatch_outcome'], 'unconfirmed')
            self.assertFalse(out['safe_to_replay']); self.assertFalse(out['reconciled'])

    def test_large_ordered_output_and_bridge_output_are_explicitly_truncated(self):
        c = Context(); template = c.packet['native_calls'][0]
        c.packet['native_calls'] = [dict(template, id=str(uuid.uuid4()), number=n) for n in range(1, 301)]
        c.packet['native_calls'][154]['id'] = tool.CALL
        c.packet['coverage']['native_calls_total'] = 300
        provenance = c.packet['dispatches'][0]['provenance']; provenance['provider_calls'] = 300
        original = provenance['artifacts'][0]
        provenance['artifacts'] = [dict(original, name='call-' + str(n) + '-completion.json') for n in range(1, 34)]
        out = self.run_collector(c)
        self.assertEqual(out['status'], 'missing-prerequisite')
        self.assertTrue(out['call_coverage']['output_truncated'])
        self.assertEqual(len(out['call_coverage']['ordered_native_numbers']), 256)
        self.assertTrue(out['bridge']['output_truncated'])
        self.assertEqual(len(out['bridge']['artifacts']), tool.MAX_BRIDGE)
        self.assertLess(len(json.dumps(out)), 32768)

    def test_recovery_contract_hash_is_not_a_database_qualification_receipt(self):
        c = Context(); c.packet['recovery_contract_sha256'] = 'e' * 64
        database = self.run_collector(c)['installed_fingerprints']['database']
        self.assertEqual(database['observed_recovery_contract_sha256'], 'e' * 64)
        self.assertEqual(database['status'], 'unavailable')
        self.assertFalse(database['recovery_contract_is_qualification_receipt'])

    def test_collector_uses_only_public_evidence_interface(self):
        c = Context()
        # A context with exactly these two attributes is sufficient. Disallow
        # common ambient I/O so a future regression cannot silently add adapters.
        from types import SimpleNamespace
        public = SimpleNamespace(packet=c.packet, original=c.original)
        with patch('builtins.open', side_effect=AssertionError('No filesystem I/O')), \
             patch('socket.socket', side_effect=AssertionError('No network I/O')), \
             patch('subprocess.run', side_effect=AssertionError('No subprocess')):
            with patch.object(tool, 'REQUIRED', c.required): out = tool.collect(public)
        self.assertFalse(out['safe_to_replay'])


if __name__ == '__main__':
    unittest.main()
