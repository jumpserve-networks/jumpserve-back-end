"""100-GB development controls; no cloud traffic, model calls or billing claims."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_costs as costs
import autonomy_inputs
import network_spec
import package_runtime
from workflow import canonical,digest
from test_network_execution import document
import test_builder_work_packet as packet_tests

MAXIMUM=100_000_000_000


def built_protocol():
    p=document('custom-package-v1');p['execution']['runner']='built-package-v1'
    cfg=p['configurations'][0]['details']['test_config']
    cfg['private_inputs']={'project.zip':dict(artifact_id=str(uuid.uuid4()),sha256='a'*64,version='software-control-v1')}
    cfg['package']['sha256']=json.loads(costs.BUILT_POLICY_PATH.read_bytes())['wrapper_sha256']
    return p


class TrafficAllowance(unittest.TestCase):
    def test_exact_ceiling_preserves_inputs_and_rejects_invalid_limits(self):
        for p in (built_protocol(),packet_tests.CounterEnvelope().full_grid()):
            p['execution']['limits']['max_network_bytes']=MAXIMUM;before=copy.deepcopy(p)
            specs=network_spec.specs(p,verify_hashes=False)
            self.assertTrue(all(s['limits']['max_network_bytes']==MAXIMUM for s in specs))
            self.assertEqual(p,before)
            for invalid in (MAXIMUM+1,True,None,str(MAXIMUM),float('nan'),float('inf')):
                with self.subTest(runner=p['execution']['runner'],invalid=invalid):
                    p['execution']['limits']['max_network_bytes']=invalid
                    with self.assertRaisesRegex(ValueError,'max_network_bytes'):network_spec.specs(p,verify_hashes=False)
        p=document('custom-package-v1');p['execution']['limits']['max_network_bytes']=10_000_000_001
        with self.assertRaisesRegex(ValueError,'max_network_bytes'):network_spec.specs(p,verify_hashes=False)

    def test_unapproved_v3_policies_cannot_inherit_old_authorization(self):
        for runner,path in (('built-package-v1',costs.BUILT_POLICY_PATH),('netem-iperf-v1',costs.NETEM_POLICY_PATH)):
            policy=json.loads(path.read_bytes())
            self.assertFalse(policy['approved_for_lab_execution'])
            self.assertEqual(policy['max_network_bytes'],MAXIMUM)
            with self.assertRaisesRegex(ValueError,'operational'):costs.builtin_quote(canonical(dict(runner=runner)))

    def test_approved_fixture_quote_enforces_hash_expiry_and_other_bounds(self):
        for runner,path,proto,prefix,reservation in (
            ('built-package-v1',costs.BUILT_POLICY_PATH,built_protocol(),'BUILT_PACKAGE',2000),
            ('netem-iperf-v1',costs.NETEM_POLICY_PATH,packet_tests.CounterEnvelope().full_grid(),'NETEM_IPERF',100)):
            with self.subTest(runner=runner),tempfile.TemporaryDirectory() as tmp:
                proto['execution']['limits']['max_network_bytes']=MAXIMUM
                spec=network_spec.specs(proto,verify_hashes=False)[0]
                policy=json.loads(path.read_bytes());policy['approved_for_lab_execution']=True
                fixture=Path(tmp)/'approved-software-fixture.json';fixture.write_bytes(canonical(policy))
                attr='BUILT_POLICY_PATH' if runner=='built-package-v1' else 'NETEM_POLICY_PATH'
                env={f'NETWORK_{prefix}_FUNCTION':'arn:aws:lambda:us-east-1:395567831870:function:SoftwareFixture:1',f'NETWORK_{prefix}_POLICY_SHA256':digest(canonical(policy))}
                with patch.object(costs,attr,fixture),patch.dict(os.environ,env):
                    quote=costs.builtin_quote(canonical(spec));self.assertEqual(quote['cents'],reservation)
                    self.assertIsNone(quote['quote']['measured_charges_usd'])
                    for key,bad in (('max_network_bytes',MAXIMUM+1),('max_instances',2),('wall_seconds',1801),('max_output_bytes',3000001)):
                        changed=copy.deepcopy(spec);changed['limits'][key]=bad
                        with self.subTest(key=key),self.assertRaises(ValueError):costs.builtin_quote(canonical(changed))
                    with self.assertRaisesRegex(ValueError,'expired'):costs.builtin_quote(canonical(spec),today=dt.datetime(2026,11,27,tzinfo=dt.timezone.utc))
                    with patch.dict(os.environ,{f'NETWORK_{prefix}_POLICY_SHA256':'0'*64}):
                        with self.assertRaisesRegex(ValueError,'matching server-owned'):costs.builtin_quote(canonical(spec))

    def test_contracts_and_requested_full_grid_allowance_stay_consistent(self):
        contracts=autonomy_inputs.runner_contracts()
        for runner in ('netem-iperf-v1','built-package-v1'):
            self.assertEqual(contracts[runner]['counter_envelope']['maximum_bytes'],MAXIMUM)
            self.assertIn(str(MAXIMUM),' '.join(contracts[runner]['constraints']))
        p=packet_tests.CounterEnvelope().full_grid();specs=network_spec.specs(p,verify_hashes=False)
        self.assertEqual(len(specs),3)
        self.assertTrue(all(s['limits']['max_network_bytes']==18_760_000_000 for s in specs))
        self.assertEqual(specs[0]['config']['loss_levels_pct'],[0,1,2,5,10])
        self.assertEqual(specs[0]['config']['duration_seconds'],30)

    def test_watchdog_stops_real_process_above_100gb_controlled_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=b'import time\ntime.sleep(10)\n'
            spec=network_spec.specs(built_protocol(),verify_hashes=False)[0]
            spec['limits']['max_network_bytes']=MAXIMUM
            spec['config']['package']['sha256']=digest(source)
            (root/'package.py').write_bytes(source);(root/'spec.json').write_bytes(canonical(spec))
            # Baseline is nonzero. The exact limit must be allowed; +1 stops.
            with patch.object(package_runtime,'network_bytes',side_effect=[1234,1234+MAXIMUM,1235+MAXIMUM,1235+MAXIMUM]) as counter:
                result=package_runtime.run(root,time.time()+3,allow_container=False)
            self.assertEqual(counter.call_count,4)
            self.assertEqual(result['network_bytes'],MAXIMUM+1)
            self.assertEqual(result['termination_reason'],'Network byte budget exceeded.')
            self.assertNotEqual(result['exit_code'],0)
            self.assertIn('overshoot',result['enforcement_scope'])
            self.assertIn('not billed traffic',result['network_counter_scope'])


if __name__=='__main__':unittest.main()
