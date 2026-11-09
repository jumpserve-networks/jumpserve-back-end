"""Malformed native originals remain fenced without provider or cleanup calls."""
import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from platform_supervisor import call_recovery as recovery
from platform_supervisor.common import Hold
from platform_supervisor.tests.test_call_recovery import Store


class NativeReconciliationContracts(unittest.TestCase):
    def test_receipts_and_artifact_references_need_exact_retained_identities(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root);before=copy.deepcopy(store.run)
            with self.assertRaisesRegex(Hold,'receipt is invalid'):recovery.http_failure(store.run,[],store.request,store.response)
            receipt=dict(store.receipt,http_status=401)
            self.assertIsNone(recovery.http_failure(store.run,receipt,store.request,store.response))
            self.assertIsNone(recovery.reconcile_http(store,store.run,store.directory,receipt,store.response,b'{}'))
            with self.assertRaisesRegex(Hold,'artifact identity'):recovery.reference(store,'missing',store.request)
            self.assertEqual(store.run,before);self.assertEqual(store.calls,[])

    def test_task_local_backoff_distinguishes_missing_unsafe_and_oversized_original_files(self):
        for defect in ['missing','unsafe-link','oversized']:
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as root:
                store=Store(root);path=store.directory/'request.json';path.unlink()
                if defect=='unsafe-link':path.symlink_to(store.directory/'response.bin')
                elif defect=='oversized':
                    with path.open('wb') as stream:stream.truncate(5000001)
                before=copy.deepcopy(store.run);result=recovery.defer(store,{},dt.datetime(2026,11,1,tzinfo=dt.timezone.utc),cleanup_verified=True)
                self.assertEqual(result['pending_run_ids'],[store.run['id']]);self.assertEqual(result['retry_after_seconds'],60);self.assertEqual(store.run,before);self.assertEqual(store.calls,[])


if __name__=='__main__':unittest.main()
