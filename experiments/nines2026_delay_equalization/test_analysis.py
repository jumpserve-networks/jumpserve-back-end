import copy
import math
import unittest

from analysis import analyze, sensitivity
from runner import summarize_receiver


class ScientificAnalysisTests(unittest.TestCase):
    def test_cross_configuration_sensitivity_is_not_within_trial_ratio(self):
        # Equal shares within each trial can still vary fourfold across assignments.
        self.assertEqual(sensitivity([10, 40]), 2)
        self.assertEqual(sensitivity([10, 10]), 0)
        self.assertEqual(sensitivity([0, 10]), math.inf)
        self.assertIsNone(sensitivity([0, 0]))

    def test_goodput_weights_receiver_bytes_by_actual_duration(self):
        document = {"intervals": [{"sum": {"start": 0, "end": 1, "seconds": 1, "bytes": 1000000, "sender": False}},
                                  {"sum": {"start": 1, "end": 3, "seconds": 2, "bytes": 1000000, "sender": False}}]}
        result = summarize_receiver(document, 0, 3)
        self.assertAlmostEqual(result["goodput_mbps"], 16/3)
        document["intervals"][0]["sum"]["sender"] = True
        with self.assertRaises(ValueError):
            summarize_receiver(document, 0, 3)

    def test_matched_pairs_and_finite_grid_bootstrap(self):
        manifest = {"id": "test", "seed": 7, "trials": []}
        results = []
        for assignment, delay in enumerate([6, 54]):
            for rep in range(3):
                for treatment in ("baseline", "equalized"):
                    config = {"family": "test", "ccas": ["bbr", "bbr"], "delays_ms": [delay, delay], "treatment": treatment}
                    trial = {"id": f"{assignment}-{rep}-{treatment}", "block_index": assignment*3+rep, "config": config}
                    manifest["trials"].append(trial)
                    rate = [10, 40][assignment] if treatment == "baseline" else 25
                    results.append({**trial, "status": "completed", "flows": [{"flow": f, "cca": "bbr", "goodput_mbps": rate} for f in (1, 2)]})
        output = analyze(manifest, results, 100)
        self.assertTrue(all(s["baseline_delta"] == 2 and s["treatment_delta"] == 0 for s in output["summaries"]))
        self.assertTrue(all(s["assessment"] == "supports_reduction" for s in output["summaries"]))
        early = analyze(manifest, results[:2], 100)
        self.assertTrue(all(s['baseline_delta'] is None and s['improvement_ci_low'] is None
                            for s in early['summaries']))
        self.assertTrue(all(c['matched_repetitions'] == 1 and c['ci_low_mbps'] is None
                            for c in early['cells']))
        results[0]["status"] = "invalid"
        output = analyze(manifest, results, 100)
        self.assertTrue(all(s["matched_pairs"] == 5 and s["assessment"] == "incomplete" for s in output["summaries"]))
        self.assertTrue(all(s['baseline_delta'] == 2 and s['baseline_ci_low'] is None
                            for s in output['summaries']))
        with self.assertRaises(ValueError):
            analyze(manifest, results+[copy.deepcopy(results[1])], 10)


if __name__ == "__main__":
    unittest.main()
