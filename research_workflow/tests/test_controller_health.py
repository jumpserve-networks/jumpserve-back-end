"""Optional housekeeping faults cannot suppress ordinary fenced leases."""
import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
import autonomy,store
class ControllerHealth(unittest.TestCase):
 def exercise(self,failed):
  calls=[]
  def rpc(name,payload):
   calls.append((name,payload))
   if name=='research_autonomy_resume_turns':
    if failed=='turn-recovery':raise store.StoreError('Synthetic policy incompatibility')
    return []
   if name=='research_autonomy_runtime_observe':return {'recorded':True}
   if name=='research_autonomy_control':self.assertEqual(payload['p_operation'],'lease');return None
   raise AssertionError(name)
  discover=store.StoreError('Synthetic discovery outage') if failed=='repair-discovery' else None
  with patch.object(autonomy.model,'OPENCLAW',False),patch.object(autonomy.model,'load_prompt'),patch('autonomous_repair.discover',side_effect=discover,return_value=[]),patch.object(autonomy.store,'rpc',side_effect=rpc),patch.dict('os.environ',RESEARCH_AUTONOMY_CONTEXT_RECOVERY=''):
   result=autonomy.handler({},None)
  self.assertEqual(result['slots'],2);self.assertEqual(sum(n=='research_autonomy_control' for n,_ in calls),2)
  self.assertEqual(result['maintenance_faults'][0]['component'],failed)
  observations=[p for n,p in calls if n=='research_autonomy_runtime_observe'];self.assertEqual(len(observations),1)
  self.assertNotIn('outage',str(observations));self.assertNotIn('incompatibility',str(observations))
 def test_turn_recovery_fault_preserves_ordinary_admission(self):self.exercise('turn-recovery')
 def test_repair_discovery_fault_preserves_ordinary_admission(self):self.exercise('repair-discovery')
