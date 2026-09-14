from __future__ import annotations

import json
import unittest

from experiments.cdn_comparison import analyze_three_cdn_vpn_trials as analysis
from experiments.cdn_comparison import run_confirmatory_day as frozen_v1
from experiments.cdn_comparison import run_three_cdn_confirmatory_day as confirmatory
from experiments.cdn_comparison import run_three_cdn_vpn_trials as runner
from experiments.cdn_comparison import run_vpn_trials as base_runner


class ThreeCdnRunnerTest(unittest.TestCase):
    def test_targets_are_isolated_successors(self) -> None:
        self.assertEqual(set(runner.TARGETS), set(confirmatory.PROVIDERS))
        self.assertTrue(all("do" in value for value in runner.TARGETS.values()))

    def test_design_is_balanced_and_uses_fresh_trial_path(self) -> None:
        objects = base_runner.build_design(
            batch_slug="three-cdn-vpn-test",
            trial_number=2,
            location=base_runner.DEFAULT_LOCATIONS[0],
            objects_per_class=20,
            seed=123,
        )
        self.assertEqual(sum(obj.treatment == "hot" for obj in objects), 20)
        self.assertEqual(sum(obj.treatment == "cold" for obj in objects), 20)
        self.assertEqual(len({obj.relative_key for obj in objects}), 40)
        self.assertTrue(all("/trial-02/" in obj.relative_key for obj in objects))

    def test_origin_population_rejects_invalid_key_before_ssh(self) -> None:
        invalid = base_runner.TrialObject("obj-001", "hot", "../../bad.bin")
        with self.assertRaisesRegex(ValueError, "invalid origin key"):
            runner.populate_origin({(1, "test"): [invalid]}, ssh_bin="ssh")


class ThreeCdnAnalysisTest(unittest.TestCase):
    def test_cloudfront_eligibility_requires_observed_pop(self) -> None:
        row = {
            "phase": "measure",
            "ok": True,
            "http_code": 200,
            "size_download_bytes": 262144,
            "cloudfront_pop": "MSP50-P4",
        }
        self.assertTrue(analysis.eligible(row, "cloudfront"))
        self.assertFalse(analysis.eligible({**row, "cloudfront_pop": None}, "cloudfront"))

    def test_auc_is_stratified_by_provider_pop(self) -> None:
        rows = [
            {"cloudfront_pop": "A", "treatment": "hot", "request_ttfb_ms": 10},
            {"cloudfront_pop": "A", "treatment": "cold", "request_ttfb_ms": 20},
            {"cloudfront_pop": "B", "treatment": "hot", "request_ttfb_ms": 100},
            {"cloudfront_pop": "B", "treatment": "cold", "request_ttfb_ms": 200},
        ]
        self.assertEqual(
            analysis.stratified_auc(rows, "cloudfront", "request_ttfb_ms"), 1.0
        )


class ThreeCdnProtocolTest(unittest.TestCase):
    def test_config_has_three_providers_and_validates(self) -> None:
        config = json.loads(confirmatory.CONFIG_PATH.read_text(encoding="utf-8"))
        confirmatory.validate_config(config)
        self.assertEqual(
            set(config["public_target_preflight"]["targets"]),
            set(confirmatory.PROVIDERS),
        )

    def test_both_protocol_locks_verify(self) -> None:
        self.assertEqual(
            frozen_v1.verify_lock()["protocol_id"], "cdn-demand-confirmatory-v1"
        )
        self.assertEqual(
            confirmatory.verify_lock()["protocol_id"],
            "cdn-demand-three-cdn-do-origin-v1",
        )


if __name__ == "__main__":
    unittest.main()
