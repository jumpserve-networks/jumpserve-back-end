from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from experiments.cdn_comparison import analyze_confirmatory
from experiments.cdn_comparison import analyze_vpn_trials
from experiments.cdn_comparison import run_confirmatory_day
from experiments.cdn_comparison import run_vpn_trials


class ConfirmatoryConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = run_confirmatory_day.load_json_object(
            run_confirmatory_day.CONFIG_PATH
        )

    def test_frozen_config_is_valid(self) -> None:
        run_confirmatory_day.validate_config(self.config)

    def test_runner_command_pins_all_relays(self) -> None:
        command = run_confirmatory_day.runner_command(
            self.config,
            day_number=1,
            output_dir=Path("/tmp/confirmatory-day-01-test"),
        )

        self.assertEqual(command.count("--location"), 3)
        for location in self.config["locations"]:
            self.assertIn(run_confirmatory_day.location_argument(location), command)

    def test_paired_runner_parses_pinned_relay_hostname(self) -> None:
        self.assertEqual(
            run_vpn_trials.parse_locations(
                ["us-los-angeles:US:LAX:us-lax-wg-006"]
            ),
            (
                run_vpn_trials.Location(
                    "us-los-angeles", "us", "lax", "us-lax-wg-006"
                ),
            ),
        )

    def test_thresholds_reproduce_training_trial_one(self) -> None:
        training_root = (
            run_confirmatory_day.REPOSITORY_ROOT / self.config["training_batch"]
        )
        paths = analyze_vpn_trials.discover_paths([training_root])
        summaries = [analyze_vpn_trials.summarize(path, "measure") for path in paths]
        records = {
            (summary.trial, summary.provider, summary.vantage):
            analyze_vpn_trials.successful_phase_records(path, "measure")
            for path, summary in zip(paths, summaries)
        }
        for provider, provider_thresholds in self.config["analysis"][
            "thresholds_ms"
        ].items():
            for vantage, expected in provider_thresholds.items():
                rows = records[("trial-01", provider, vantage)]
                hot = analyze_vpn_trials.numeric_values(
                    (
                        row
                        for row in rows
                        if row.get("treatment") == "hot"
                    ),
                    "request_ttfb_ms",
                )
                cold = analyze_vpn_trials.numeric_values(
                    (
                        row
                        for row in rows
                        if row.get("treatment") == "cold"
                    ),
                    "request_ttfb_ms",
                )
                actual, _balanced_accuracy = analyze_vpn_trials.choose_lower_threshold(
                    hot, cold
                )
                self.assertEqual(actual, expected)

    def test_collection_days_must_use_later_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            day_one = root / "confirmatory-day-01-example"
            day_one.mkdir()
            (day_one / "confirmatory-metadata.json").write_text(
                json.dumps({"utc_date": "2026-08-28"}), encoding="utf-8"
            )
            with mock.patch.object(run_confirmatory_day, "RESULTS_ROOT", root):
                run_confirmatory_day.validate_collection_date(2, "2026-08-29")
                with self.assertRaises(ValueError):
                    run_confirmatory_day.validate_collection_date(2, "2026-08-28")


class ConfirmatoryAnalysisTest(unittest.TestCase):
    def test_eligibility_requires_complete_body_and_observed_pop(self) -> None:
        base = {
            "phase": "measure",
            "ok": True,
            "http_code": 200,
            "size_download_bytes": 262144.0,
            "request_ttfb_ms": 10.0,
            "cf_colo": "FRA",
        }
        self.assertTrue(
            analyze_confirmatory.eligible_record(
                base, "cloudflare", metric="request_ttfb_ms", body_bytes=262144
            )
        )
        self.assertFalse(
            analyze_confirmatory.eligible_record(
                {**base, "size_download_bytes": 100.0},
                "cloudflare",
                metric="request_ttfb_ms",
                body_bytes=262144,
            )
        )
        self.assertFalse(
            analyze_confirmatory.eligible_record(
                {**base, "cf_colo": None},
                "cloudflare",
                metric="request_ttfb_ms",
                body_bytes=262144,
            )
        )

    def test_frozen_threshold_counts_both_classes(self) -> None:
        counts = analyze_confirmatory.threshold_counts(
            [
                {"treatment": "hot", "request_ttfb_ms": 10.0},
                {"treatment": "hot", "request_ttfb_ms": 30.0},
                {"treatment": "cold", "request_ttfb_ms": 20.0},
                {"treatment": "cold", "request_ttfb_ms": 40.0},
            ],
            metric="request_ttfb_ms",
            threshold_ms=25.0,
        )

        self.assertEqual(counts.hot_correct, 1)
        self.assertEqual(counts.cold_correct, 1)
        self.assertEqual(counts.accuracy, 0.5)
        self.assertEqual(counts.balanced_accuracy, 0.5)

    def test_day_cluster_interval_of_constant_cells_is_constant(self) -> None:
        cells = [
            analyze_confirmatory.CellSummary(
                day=day,
                provider="fastly",
                vantage="example",
                pops=("FRA",),
                attempted=40,
                eligible=40,
                hot_hits=20,
                hot_total=20,
                cold_misses=20,
                cold_total=20,
                header_rule_correct=40,
                header_rule_total=40,
                demand_auc={
                    "edge_rtt_estimate_ms": 0.5,
                    "request_ttfb_ms": 0.75,
                    "fetch_residual_ms": 0.75,
                },
                cache_auc={
                    "edge_rtt_estimate_ms": 0.5,
                    "request_ttfb_ms": 1.0,
                    "fetch_residual_ms": 1.0,
                },
                threshold=analyze_confirmatory.ThresholdCounts(20, 20, 20, 20),
                cross_provider_threshold=analyze_confirmatory.ThresholdCounts(
                    20, 20, 20, 20
                ),
            )
            for day in (1, 2, 3)
        ]

        self.assertEqual(
            analyze_confirmatory.day_cluster_interval(
                cells,
                provider="fastly",
                metric="request_ttfb_ms",
                iterations=100,
                seed=1,
            ),
            (0.75, 0.75),
        )


if __name__ == "__main__":
    unittest.main()
