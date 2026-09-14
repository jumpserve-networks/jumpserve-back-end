from __future__ import annotations

import unittest

from experiments.cloudflare_cache.analyze import lower_is_positive_auc
from experiments.cloudflare_cache.analyze_regional import (
    cloudflare_colo as regional_cloudflare_colo,
)
from experiments.cloudflare_cache.analyze_regional import flatten_documents
from experiments.cloudflare_cache.analyze_vpn_runs import (
    stratified_auc,
    trial_bootstrap_interval,
)
from experiments.cloudflare_cache.probe import Target, cf_colo, parse_header_blocks, select_treatments
from experiments.cloudflare_cache.run_vpn_pilot import DEFAULT_LOCATIONS, parse_locations


class HeaderParsingTest(unittest.TestCase):
    def test_uses_final_http_header_block(self) -> None:
        raw = (
            "HTTP/1.1 200 Connection established\r\n\r\n"
            "HTTP/2 200\r\n"
            "cf-cache-status: HIT\r\n"
            "cf-ray: abcdef123456-DFW\r\n"
            "age: 10\r\n"
            "set-cookie: deliberately-not-recorded\r\n\r\n"
        )

        status, headers = parse_header_blocks(raw)

        self.assertEqual(status, "HTTP/2 200")
        self.assertEqual(headers["cf-cache-status"], "HIT")
        self.assertEqual(headers["age"], "10")
        self.assertNotIn("set-cookie", headers)

    def test_extracts_colo_from_ray_id(self) -> None:
        self.assertEqual(cf_colo("230b030023ae2822-SJC"), "SJC")
        self.assertIsNone(cf_colo(None))


class AucTest(unittest.TestCase):
    def test_perfect_faster_hits(self) -> None:
        self.assertEqual(lower_is_positive_auc([1.0, 2.0], [3.0, 4.0]), 1.0)

    def test_ties_are_half_credit(self) -> None:
        self.assertEqual(lower_is_positive_auc([2.0], [2.0]), 0.5)

    def test_requires_both_classes(self) -> None:
        self.assertIsNone(lower_is_positive_auc([], [1.0]))

    def test_stratified_auc_uses_only_within_colo_pairs(self) -> None:
        records = [
            {"cf_colo": "A", "treatment": "hot", "metric": 1.0},
            {"cf_colo": "A", "treatment": "cold", "metric": 2.0},
            {"cf_colo": "B", "treatment": "hot", "metric": 100.0},
            {"cf_colo": "B", "treatment": "cold", "metric": 101.0},
        ]

        self.assertEqual(stratified_auc(records, "metric"), 1.0)

    def test_trial_bootstrap_of_constant_values_is_constant(self) -> None:
        self.assertEqual(
            trial_bootstrap_interval([0.75, 0.75], iterations=20, seed=1),
            (0.75, 0.75),
        )


class TreatmentSelectionTest(unittest.TestCase):
    def test_selects_only_requested_treatment(self) -> None:
        targets = [
            Target("hot-1", "hot", "https://example.com/hot"),
            Target("cold-1", "cold", "https://example.com/cold"),
        ]

        selected = select_treatments(targets, ["hot"])

        self.assertEqual([target.object_id for target in selected], ["hot-1"])


class VpnLocationParsingTest(unittest.TestCase):
    def test_uses_defaults_when_no_locations_are_supplied(self) -> None:
        self.assertEqual(parse_locations([]), DEFAULT_LOCATIONS)

    def test_parses_one_location(self) -> None:
        self.assertEqual(
            parse_locations(["us-new-york:US:NYC"]),
            (DEFAULT_LOCATIONS[0].__class__("us-new-york", "us", "nyc"),),
        )

    def test_rejects_malformed_location(self) -> None:
        with self.assertRaises(ValueError):
            parse_locations(["us-new-york:nyc"])


class RegionalOutputTest(unittest.TestCase):
    def test_flattens_probe_context_into_results(self) -> None:
        documents = [
            {
                "configured_region": "aws:eu-west-2",
                "probe_slug": "euw2",
                "results": [
                    {
                        "object_id": "run-euw2-h-01",
                        "cf_ray": "abcdef123456-LHR",
                        "cf_cache_status": "HIT",
                    }
                ],
            }
        ]

        records = flatten_documents(documents)

        self.assertEqual(records[0]["configured_region"], "aws:eu-west-2")
        self.assertEqual(records[0]["probe_slug"], "euw2")
        self.assertEqual(records[0]["target_colo"], "LHR")

    def test_regional_colo_handles_missing_ray(self) -> None:
        self.assertEqual(regional_cloudflare_colo("abcdef123456-SYD"), "SYD")
        self.assertEqual(regional_cloudflare_colo(None), "unknown")


if __name__ == "__main__":
    unittest.main()
