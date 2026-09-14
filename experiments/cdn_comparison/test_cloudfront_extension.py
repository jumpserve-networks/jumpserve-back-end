from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from experiments.cdn_comparison import analyze_cloudfront_vpn_pilot
from experiments.cdn_comparison import probe_v2
from experiments.cdn_comparison import run_confirmatory_day
from experiments.cdn_comparison import run_cloudfront_vpn_pilot


class CloudFrontProbeTest(unittest.TestCase):
    def test_x_cache_status_is_normalized(self) -> None:
        self.assertEqual(
            probe_v2.cloudfront_x_cache_status("Hit from cloudfront"), "HIT"
        )
        self.assertEqual(
            probe_v2.cloudfront_x_cache_status("Miss from cloudfront"), "MISS"
        )
        self.assertEqual(
            probe_v2.cloudfront_x_cache_status("RefreshHit from cloudfront"),
            "REFRESH_HIT",
        )

    def test_server_timing_metadata_is_parsed(self) -> None:
        value = (
            'cdn-cache-hit,cdn-pop;desc="SEA19-C1",'
            'cdn-hit-layer;desc="REC",cdn-downstream-fbl;dur=12'
        )
        self.assertEqual(probe_v2.server_timing_cache_status(value), "HIT")
        self.assertEqual(probe_v2.server_timing_value(value, "cdn-pop"), "SEA19-C1")
        self.assertEqual(probe_v2.server_timing_value(value, "cdn-hit-layer"), "REC")

    def test_record_enrichment_prefers_explicit_pop_header(self) -> None:
        record: dict[str, object] = {
            "response_headers": {
                "x-cache": "Miss from cloudfront",
                "x-amz-cf-pop": "FRA60-P3",
                "server-timing": 'cdn-cache-miss,cdn-pop;desc="FRA60-P1"',
            }
        }
        probe_v2.enrich_record(record)
        self.assertEqual(record["cloudfront_cache_status"], "MISS")
        self.assertEqual(record["cloudfront_pop"], "FRA60-P3")
        self.assertTrue(record["cloudfront_diagnostic_consistent"])


class CloudFrontAnalysisTest(unittest.TestCase):
    def test_eligibility_requires_full_body_and_observed_pop(self) -> None:
        base = {
            "phase": "measure",
            "ok": True,
            "http_code": 200,
            "size_download_bytes": 262144.0,
            "cloudfront_pop": "FRA60-P3",
        }
        self.assertTrue(
            analyze_cloudfront_vpn_pilot.eligible_record(base, "measure")
        )
        self.assertFalse(
            analyze_cloudfront_vpn_pilot.eligible_record(
                {**base, "size_download_bytes": 1.0}, "measure"
            )
        )
        self.assertFalse(
            analyze_cloudfront_vpn_pilot.eligible_record(
                {**base, "cloudfront_pop": None}, "measure"
            )
        )

    def test_auc_is_stratified_by_observed_pop(self) -> None:
        rows = [
            {
                "cloudfront_pop": "FRA60-P3",
                "treatment": "hot",
                "request_ttfb_ms": 10.0,
            },
            {
                "cloudfront_pop": "FRA60-P3",
                "treatment": "cold",
                "request_ttfb_ms": 20.0,
            },
            {
                "cloudfront_pop": "SIN2-P1",
                "treatment": "hot",
                "request_ttfb_ms": 100.0,
            },
            {
                "cloudfront_pop": "SIN2-P1",
                "treatment": "cold",
                "request_ttfb_ms": 200.0,
            },
        ]
        self.assertEqual(
            analyze_cloudfront_vpn_pilot.stratified_auc(
                rows, "request_ttfb_ms"
            ),
            1.0,
        )

    def test_recovery_metadata_is_discovered_next_to_measurement(self) -> None:
        with TemporaryDirectory() as value:
            location = Path(value)
            measurement = location / "cloudfront.measure.jsonl"
            measurement.write_text("", encoding="utf-8")
            (location / "recovery-metadata.json").write_text(
                '{"audit": {"failed_rows": []}}\n', encoding="utf-8"
            )
            recoveries = analyze_cloudfront_vpn_pilot.load_recoveries(
                [measurement]
            )
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0][1]["audit"]["failed_rows"], [])


class CloudFrontRunnerTest(unittest.TestCase):
    def test_default_locations_are_pinned(self) -> None:
        locations = run_cloudfront_vpn_pilot.parse_locations([])
        self.assertEqual(len(locations), 3)
        self.assertTrue(all(location.hostname for location in locations))

    def test_frozen_confirmatory_lock_still_verifies(self) -> None:
        lock = run_confirmatory_day.verify_lock()
        self.assertEqual(lock["protocol_id"], "cdn-demand-confirmatory-v1")


if __name__ == "__main__":
    unittest.main()
