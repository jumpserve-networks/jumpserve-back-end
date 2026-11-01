"""The queued proof table has a dispatch key, not an id column."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store


class QueuedProofQuery(unittest.TestCase):
    def test_real_client_orders_private_proof_by_dispatch_key(self):
        with patch.object(store,'request',return_value=[]) as transport:
            store.rows('queued_dispatch_reconciliations',dict(dispatch_id='eq.original',limit=2))
        params=parse_qs(urlsplit(transport.call_args.args[0]).query)
        self.assertEqual(params['order'],['created_at.asc,dispatch_id.asc'])
        self.assertIn('snapshot_raw',params['select'][0].split(','))
        self.assertEqual(params['dispatch_id'],['eq.original'])
        self.assertEqual(transport.call_args.kwargs,dict(public=False))
        with self.assertRaisesRegex(ValueError,'Private relations'):
            store.rows('queued_dispatch_reconciliations',public=True)

    def test_explicit_proof_order_is_retained(self):
        with patch.object(store,'request',return_value=[]) as transport:
            store.rows('queued_dispatch_reconciliations',dict(order='dispatch_id.desc'))
        self.assertEqual(parse_qs(urlsplit(transport.call_args.args[0]).query)['order'],['dispatch_id.desc'])


if __name__=='__main__':unittest.main()
