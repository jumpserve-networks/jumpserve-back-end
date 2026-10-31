import copy,json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy,budget_policy,autonomy_model as model,store

def packet(spend=-1,calls=-1):
    return dict(version='autonomy-category-budgets-v1',categories=[dict(category=category,max_spend_cents=spend,max_model_calls=calls,revision=2) for category in sorted(budget_policy.CATEGORIES)])

class CategoryBudgetTests(unittest.TestCase):
    def test_current_defaults_use_explicit_unlimited_and_preserve_nonbudget_limits(self):
        with patch.object(store,'rpc',return_value=packet()),patch.dict(os.environ,RESEARCH_CATEGORY_BUDGETS_ENABLED='true'):
            defaults=autonomy.current_defaults()
        self.assertIsNone(defaults['maximum_spend_usd']);self.assertIsNone(defaults['maximum_model_calls_per_assessment'])
        self.assertEqual(defaults['budget_policy']['max_spend_cents'],-1)
        for key in ('elapsed_days','attempts_per_claim','parallel_claims_per_paper','global_parallel_claims'):self.assertEqual(defaults[key],autonomy.DEFAULTS[key])
    def test_policy_is_read_fresh_and_zero_is_a_real_budget(self):
        with patch.object(store,'rpc',side_effect=[packet(500,3),packet(0,0),packet()]) as rpc:
            self.assertEqual(budget_policy.assessment_defaults({})['maximum_spend_usd'],5)
            self.assertEqual(budget_policy.assessment_defaults({})['maximum_model_calls_per_assessment'],0)
            self.assertIsNone(budget_policy.assessment_defaults({})['maximum_spend_usd'])
        self.assertEqual(rpc.call_count,3)
    def test_missing_null_boolean_and_other_negative_policy_values_fail_closed(self):
        variants=[None,{},dict(version='wrong',categories=[]),packet()]
        variants[-1]['categories'].pop()
        for value in (None,True,-2,'-1'):
            invalid=packet();invalid['categories'][0]['max_spend_cents']=value;variants.append(invalid)
        for invalid in variants:
            with self.subTest(value=invalid),patch.object(store,'rpc',return_value=invalid),self.assertRaises(store.StoreError):budget_policy.snapshot()
    def test_lowered_cap_retains_usage_only_with_exact_current_category_authority(self):
        row=next(row for row in packet(0,0)['categories'] if row['category']=='research_assessment')
        terms=dict(version='assessment-budget-amendment-v2',max_spend_cents=0,committed_cents=500,deadline_at='2026-11-07T00:00:00Z',budget_policy=budget_policy.terms(row))
        self.assertIn('500',model.resource_instruction(dict(resource_authorization=terms)))
        for invalid in (dict(terms,budget_policy=None),dict(terms,budget_policy=dict(terms['budget_policy'],max_spend_cents=-1)),dict(terms,max_spend_cents=-1)):
            with self.assertRaises(ValueError):model.resource_instruction(dict(resource_authorization=invalid))

if __name__=='__main__':unittest.main()
