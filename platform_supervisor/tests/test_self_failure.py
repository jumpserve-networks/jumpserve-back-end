"""Controller faults become bounded self-repair diagnostics, never new grants."""
import json,unittest
from platform_supervisor import self_failure,self_runtime

class SelfFailure(unittest.TestCase):
 def test_private_exception_text_and_locals_do_not_enter_repair_feedback(self):
  state={}
  try:raise ValueError('Bearer never-deliver-this-private-credential; private research prompt')
  except ValueError as error:
   value=self_failure.capture(error,state);original=self_failure.original(error)
  self.assertIn('never-deliver-this-private-credential',json.loads(original)['traceback'])
  self.assertNotIn('never-deliver',json.dumps(state));self.assertNotIn('private research prompt',json.dumps(state))
  self.assertEqual(value['error_type'],'ValueError');self.assertEqual(value['repeated'],1)
  self.assertIn('Existing authority is sufficient',state['feedback']);self.assertIn('do not request new permissions',state['feedback'])
 def test_identical_faults_have_a_stable_fingerprint_and_do_not_create_authority(self):
  state={};values=[]
  for _ in range(2):
   try:raise RuntimeError('Synthetic failure')
   except RuntimeError as error:values.append(self_failure.capture(error,state))
  self.assertEqual(values[0]['fingerprint'],values[1]['fingerprint']);self.assertEqual(values[1]['repeated'],2)
  capabilities=self_runtime.capabilities();self.assertFalse(capabilities['requires_further_owner_approval'])
  self.assertTrue(capabilities['qualification_and_installed_source_proof_required']);self.assertIn('credentials.py',capabilities['frozen_files'])

if __name__=='__main__':unittest.main()
