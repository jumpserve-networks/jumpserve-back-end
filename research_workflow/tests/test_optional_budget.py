"""Explicit aggregate-budget semantics; no provider, cloud or scientific calls."""
import copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy,autonomy_model as model,experiment_builder,shared_campaigns
from test_autonomy import task

class OptionalBudget(unittest.TestCase):
 def test_only_explicit_versioned_authority_can_remove_the_aggregate_ceiling(self):
  authority=dict(version='assessment-budget-amendment-v2',max_spend_cents=None,committed_cents=150000,deadline_at='2030-01-01T00:00:00Z')
  instruction=model.resource_instruction(dict(resource_authorization=authority))
  for retained in ('removed the aggregate spending ceiling','150000 cents','2030-01-01','Per-call and per-experiment','uncertain-call fences','No paid fallback'):self.assertIn(retained,instruction)
  for change in (dict(version='assessment-budget-amendment-v1'),dict(max_spend_cents=0),dict(max_spend_cents=True),dict(committed_cents=None),dict(committed_cents=-1),dict(max_spend_cents=100000),dict(deadline_at=None)):
   with self.subTest(change=change),self.assertRaises(ValueError):model.resource_instruction(dict(resource_authorization=dict(authority,**change)))
  missing=dict(authority);missing.pop('max_spend_cents')
  with self.assertRaises(ValueError):model.resource_instruction(dict(resource_authorization=missing))
  restored=dict(authority,max_spend_cents=200000)
  self.assertIn('200000 cents',model.resource_instruction(dict(resource_authorization=restored)))

 def test_actual_context_preserves_null_separately_from_exhaustion_and_original_defaults(self):
  t=task();original=copy.deepcopy(t);t['workflow'].update(max_spend_cents=None,committed_cents=150000)
  with patch.object(autonomy,'one',side_effect=lambda kind,*_:dict(source_id='source') if kind=='claims' else dict(sha256='a'*64)),patch.object(autonomy,'rows',return_value=[]),patch.object(autonomy.inputs,'source_text',return_value=('Retained source passage',None)),patch.object(autonomy.inputs,'runner_contracts',return_value={}),patch.object(autonomy.inputs,'protocol_contract',return_value={}),patch.object(shared_campaigns,'available',return_value=[]),patch.object(experiment_builder,'enabled',return_value=False),patch.object(autonomy,'coverage_manifest',return_value={}):
   context=autonomy.build_context(t)
   self.assertIsNone(context['remaining_commitment_cents']);self.assertIsNone(context['resource_authorization']['max_spend_cents'])
   self.assertIn('propose',model.request_actions(context));self.assertIn('removed the aggregate',model.resource_instruction(context))
   self.assertEqual(context['initial_prompt'],original['workflow']['initial_prompt']);self.assertEqual(autonomy.DEFAULTS['maximum_spend_usd'],100)
   t['workflow']['max_spend_cents']=150000;exhausted=autonomy.build_context(t)
   self.assertEqual(exhausted['remaining_commitment_cents'],0);self.assertEqual(model.request_actions(exhausted),['blocked'])
   t['workflow']['max_spend_cents']=200000;bounded=autonomy.build_context(t)
   self.assertEqual(bounded['remaining_commitment_cents'],50000);self.assertIn('propose',model.request_actions(bounded))

if __name__=='__main__':unittest.main()
