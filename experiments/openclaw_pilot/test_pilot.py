"""Purpose-specific offline regressions; no provider or production operations."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('openclaw_pilot', Path(__file__).with_name('run.py'))
pilot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pilot
spec.loader.exec_module(pilot)


class Pilot(unittest.TestCase):
    def setUp(self):
        self.logical = pilot.model.request('Fixed system instructions', {'task': 'assessment', 'value': None, 'zero': 0})
        self.packet = {'id': '7f1400b7-5b79-40bb-8eb6-d0787a12cc47', 'coordinator_database': 'local-test',
                       'protocol': pilot.load_protocol()}

    def test_wrong_or_duplicate_arm_identities_fail_before_admission(self):
        correct = {'arms': [{'name': name, 'study_id': str(index)} for index, name in enumerate(pilot.ARM_NAMES)]}
        pilot.validate_arms(correct)
        for field, value in (('name', '202610200002_context_budget_recovery.sql'), ('study_id', '0')):
            changed = copy.deepcopy(correct); changed['arms'][1][field] = value
            with self.assertRaises(ValueError): pilot.validate_arms(changed)

    def test_translation_preserves_null_zero_and_exact_decision_schema(self):
        request = {'model': pilot.model.MODEL, 'messages': [{'role': 'system', 'content': 'Runtime policy'},
                   {'role': 'user', 'content': self.logical['messages'][0]['content'][0]['text']}]}
        before = copy.deepcopy(request)
        translated = pilot.translate(request, self.logical)
        self.assertEqual(request, before)
        self.assertEqual(translated['toolConfig'], self.logical['toolConfig'])
        data = json.loads(translated['messages'][0]['content'][0]['text'])
        self.assertIsNone(data['value']); self.assertEqual(data['zero'], 0)
        self.assertEqual(translated['inferenceConfig'], {'maxTokens': 4096, 'temperature': 0})

    def test_agent_tools_other_models_and_nontext_inputs_are_rejected(self):
        base = {'model': pilot.model.MODEL, 'messages': [{'role': 'user', 'content': 'fixed'}]}
        for changed in (dict(base, model='other'), dict(base, tools=[{'function': {'name': 'exec'}}]),
                        dict(base, messages=[{'role': 'tool', 'content': 'result'}]),
                        dict(base, messages=[{'role': 'user', 'content': [{'type': 'image_url'}]}])):
            with self.assertRaises(ValueError):
                pilot.translate(changed, self.logical)

    def test_budget_unknown_outcome_and_call_cap_prevent_dispatch(self):
        for unsafe, reserved, number in ((1, 0, 1), (0, 10000, 1), (0, 0, 26)):
            provider = pilot.Provider(self.packet); provider.logical = self.logical; provider.arm = 'jumpserve-direct'
            values = [json.dumps({'unsafe': unsafe, 'reserved': reserved, 'count': 0}), str(number)]
            provider.query = lambda sql: values.pop(0)
            with patch.object(pilot.subprocess, 'run') as call:
                with self.assertRaises(ValueError): provider.call(self.logical)
                call.assert_not_called()

    def test_wire_budget_counts_openclaw_wrapper_and_fixed_schema(self):
        provider = pilot.Provider(self.packet); provider.logical = self.logical; provider.arm = 'openclaw-runtime'
        with patch.object(pilot.subprocess, 'run') as call:
            with self.assertRaises(ValueError): provider.call(self.logical, b'x' * 96001)
            call.assert_not_called()
        changed = copy.deepcopy(self.logical); changed['system'] = [{'text': 'x' * 96000}]
        with self.assertRaises(ValueError): provider.call(changed)

    def test_smoke_is_explicitly_synthetic_and_does_not_dispatch(self):
        provider = pilot.Provider(self.packet, smoke=True)
        with patch.object(pilot.subprocess, 'run') as call:
            result = provider.call(self.logical)
            call.assert_not_called()
        self.assertEqual(pilot.model.parse(result)['action'], 'blocked')
        self.assertEqual(len(provider.smoke_requests), 1)


if __name__ == '__main__':
    unittest.main()
