"""Development regressions with synthetic AWS, SQL and reports; no cloud calls."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import research_execution as r

ID='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
def event(action='launch'):return dict(source='jumpserve.network-execution',job_id=ID,action=action)
def saved():return dict(owner_id='saved-owner',started_at='2026-01-01T00:00:00Z',deadline='2099-01-01T00:00:00Z',spec=dict(config={'frozen':True},metrics=[dict(name='throughput_mbit_s',units='Mbit/s'),dict(name='rtt_median_ms',units='ms')]))

class ResearchExecution(unittest.TestCase):
    def test_http_envelope_cannot_bypass_original_authentication(self):
        self.assertFalse(r.envelope(dict(event(),requestContext={'http':{'method':'POST'}})))
        self.assertTrue(r.envelope(event()))
        with self.assertRaises(ValueError):r.envelope(dict(event(),owner_id='forged'))
    def test_duplicate_saved_launch_never_replays_and_reports_unconfirmed(self):
        launch=Mock()
        with patch.object(r.database,'base_url',return_value='https://regphejnlvfpyokpniny.supabase.co'),patch.object(r.database,'rpc',side_effect=[None,saved()]),patch.object(r.store,'load',return_value=None):
            result=r.handle(event(),launch)
        launch.assert_not_called();self.assertEqual(result['status'],'launch-unconfirmed')
    def test_launcher_receives_only_frozen_owner_configuration_and_original_request_id(self):
        launch=Mock()
        with patch.object(r.database,'base_url',return_value='https://regphejnlvfpyokpniny.supabase.co'),patch.object(r.database,'rpc',return_value=saved()),patch.object(r.store,'load',return_value=None):r.handle(event(),launch)
        launch.assert_called_once_with(dict(config={'frozen':True},request_id=ID),'saved-owner')
    def test_missing_receiver_values_stay_missing_and_private_nodes_are_not_exposed(self):
        job=dict(status='completed',created_at=1,updated_at=2)
        report=dict(receivers=[dict(name='receiver-1',placement={'region':'test'},mean_mbit_per_second=0,rtt={'median':None})],analysis_version='fixture-v1')
        with patch.object(r.database,'base_url',return_value='https://regphejnlvfpyokpniny.supabase.co'),patch.object(r.database,'rpc',return_value=saved()),patch.object(r.store,'load',return_value=job),patch.object(r,'public_job',return_value={'nodes':[{'name':'receiver-1'}]}),patch.object(r,'load_report',return_value=report):
            answer=r.handle(event('status'),Mock())
        rows=answer['output']['observations'];self.assertEqual(rows[0]['value'],0);self.assertEqual(rows[1]['status'],'missing');self.assertIsNone(rows[1]['value']);self.assertEqual(answer['resources']['nodes'],[{'name':'receiver-1'}])
    def test_target_guard_precedes_launch_or_database_mutation(self):
        with patch.object(r.database,'base_url',return_value='https://wrong.invalid'),patch.object(r.database,'rpc') as rpc:
            with self.assertRaises(ValueError):r.handle(event(),Mock())
            rpc.assert_not_called()

if __name__=='__main__':unittest.main()
