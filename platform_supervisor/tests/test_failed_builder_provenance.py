"""Adversarial provenance and compressed history coverage for the failed builder pack."""
import copy
import gzip
import io
import json
import unittest
from unittest.mock import patch
import zipfile
from platform_supervisor import capability_failed_builder_call_evidence as tool
from platform_supervisor.tests.test_failed_builder_call_evidence import Context, encoded


def compressed_checkpoint(history, **overrides):
    raw = encoded(history)
    compressed = gzip.compress(raw, mtime=0)
    index = dict(version='builder-history-gzip-v1', path='native-tool-history.json.gz',
                 bytes=len(raw), records=len(history), sha256=tool.sha(raw),
                 compressed_sha256=tool.sha(compressed))
    index.update(overrides)
    sink = io.BytesIO()
    with zipfile.ZipFile(sink, 'w') as archive:
        archive.writestr('native-tool-history.json', encoded(index))
        archive.writestr('native-tool-history.json.gz', compressed)
    return sink.getvalue()


class FailedBuilderProvenance(unittest.TestCase):
    def collect(self, ctx):
        with patch.multiple(tool, REQUEST_SHA=ctx.request_sha, RESPONSE_SHA=ctx.response_sha):
            return tool.collect(ctx)

    def test_conflicting_artifact_identity_is_rejected_before_original_read(self):
        for place in ('reference', 'metadata', 'packet'):
            with self.subTest(place=place):
                ctx = Context()
                if place == 'reference':
                    row = ctx.packet['dispatches'][0]['provenance']['artifacts'][0]
                elif place == 'metadata':
                    row = ctx.packet['artifacts'][2]
                else:
                    row = ctx.packet
                row['dispatch_id'] = 'out-of-scope'
                report = self.collect(ctx)
                self.assertEqual(report['status'], 'inconclusive')
                self.assertEqual(ctx.reads, [])

    def test_conflicting_id_alias_is_rejected_before_original_read(self):
        ctx = Context()
        ctx.packet['dispatches'][0]['provenance']['artifacts'][0]['artifact_id'] = tool.RESPONSE_ID
        self.assertEqual(self.collect(ctx)['status'], 'inconclusive')
        self.assertEqual(ctx.reads, [])

    def test_checkpoint_pointer_in_receipt_is_compared_to_selected_original(self):
        ctx = Context()
        receipt = copy.deepcopy(ctx.receipt)
        receipt['checkpoint']['sha256'] = '0' * 64
        ctx.replace('receipt.json', encoded(receipt))
        report = self.collect(ctx)
        self.assertFalse(report['receipt_checkpoint_matches_selected_original'])
        self.assertFalse(report['capture_ordering']['temporal_order_verified'])
        self.assertFalse(report['reconciled'])

    def test_compressed_history_is_verified_and_compared_without_extraction(self):
        ctx = Context()
        ctx.replace('checkpoint', compressed_checkpoint([ctx.prior]))
        report = self.collect(ctx)
        self.assertEqual(report['capture_ordering']['observation'], 'prefix-consistent')
        for overrides in (dict(records=True), dict(records=2), dict(sha256='0'*64),
                          dict(compressed_sha256='0'*64), dict(bytes=tool.MAX_BYTES+1)):
            with self.subTest(overrides=overrides):
                ctx = Context()
                ctx.replace('checkpoint', compressed_checkpoint([ctx.prior], **overrides))
                report = self.collect(ctx)
                self.assertEqual(report['capture_ordering']['observation'], 'checkpoint-history-unavailable-or-malformed')

    def test_missing_parent_transport_fields_do_not_imply_a_match(self):
        ctx = Context()
        ctx.replace('receipt.json', b'{}')
        ctx.packet['dispatches'][0]['provenance'] = dict(
            artifacts=ctx.packet['dispatches'][0]['provenance']['artifacts'],
            checkpoint=ctx.packet['dispatches'][0]['provenance']['checkpoint'])
        report = self.collect(ctx)
        self.assertFalse(report['receipt_matches_parent'])
        self.assertFalse(report['cleanup_confirmed_in_receipt'])

    def test_absent_request_and_response_originals_are_reported_individually(self):
        ctx = Context()
        del ctx.values[tool.REQUEST_ID]
        del ctx.values[tool.RESPONSE_ID]
        report = self.collect(ctx)
        for name in ('request', 'response'):
            self.assertIn(dict(evidence=name, reason='original-unavailable'), report['missing'])
        self.assertFalse(report['non_execution_proven'])

    def test_oversized_indexed_metadata_cannot_be_hidden_by_small_reference(self):
        ctx = Context()
        ctx.packet['artifacts'][2]['byte_count'] = tool.MAX_BYTES+1
        self.assertEqual(self.collect(ctx)['status'], 'inconclusive')
        self.assertEqual(ctx.reads, [])


if __name__ == '__main__':
    unittest.main()
