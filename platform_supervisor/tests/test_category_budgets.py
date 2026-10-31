import unittest
from unittest.mock import Mock
from platform_supervisor.common import Hold
from platform_supervisor.worker import Controller

class CategoryBudgetTests(unittest.TestCase):
    def controller(self,spend=-1,calls=-1,spent=20000,used=200):
        store=Mock();store.task={};controller=Controller(store,Mock(),'image')
        controller.flags=dict(max_reserved_cents=spend,committed_cents=spent,max_model_calls=calls,model_calls=used)
        return controller
    def test_unlimited_budget_keeps_coding_after_previous_default_caps(self):
        self.assertTrue(self.controller().coding_available())
    def test_zero_finite_and_independent_call_caps_apply_to_current_usage(self):
        self.assertFalse(self.controller(spend=0).coding_available())
        self.assertTrue(self.controller(spend=20100,calls=201).coding_available())
        self.assertFalse(self.controller(spend=20099,calls=201).coding_available())
        self.assertFalse(self.controller(calls=200).coding_available())
    def test_invalid_budget_and_usage_values_fail_closed(self):
        for key in ('max_reserved_cents','committed_cents','max_model_calls','model_calls'):
            for value in (None,True,'-1',-2):
                controller=self.controller();controller.flags[key]=value
                with self.subTest(key=key,value=value),self.assertRaises(Hold):controller.coding_available()

if __name__=='__main__':unittest.main()
