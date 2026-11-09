#!/usr/bin/env python3
"""Matched whole-trial analysis; never resample seconds as independent trials."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import uuid


def configuration_id(campaign, config):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, campaign+":"+json.dumps(config, sort_keys=True)))


def sensitivity(rates):
    if not rates or any(rate < 0 or not math.isfinite(rate) for rate in rates):
        raise ValueError("Sensitivity requires finite nonnegative rates")
    smallest, largest = min(rates), max(rates)
    if largest == 0:
        return None
    if smallest == 0:
        return math.inf
    return math.log2(largest/smallest)


def percentile(values, probability):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered)-1)*probability
    lo, hi = math.floor(index), math.ceil(index)
    if lo == hi or ordered[lo] == ordered[hi]:
        return ordered[lo]
    return ordered[lo]+(ordered[hi]-ordered[lo])*(index-lo)


def finite(value):
    return value if value is not None and math.isfinite(value) else None


def analyze(manifest, results, bootstrap_replicates=2000):
    planned = {t["id"]: t for t in manifest["trials"]}
    by_id = {}
    for result in results:
        if result["id"] not in planned:
            raise ValueError("Unplanned result must not enter campaign analysis")
        if result["config"] != planned[result["id"]]["config"]:
            raise ValueError("Result configuration differs from frozen schedule")
        if result["id"] in by_id:
            raise ValueError("Multiple attempts require explicit adjudication, not silent selection")
        by_id[result["id"]] = result
    groups = defaultdict(lambda: defaultdict(list))
    for trial in planned.values():
        c = trial["config"]
        groups[(c["family"], "/".join(c["ccas"]))][trial["block_index"]].append(trial)
    summaries, cells = [], []
    source_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for (family, cca_group), blocks in groups.items():
        strata = defaultdict(list)
        expected_strata = set()
        ccas = cca_group.split("/")
        for pair in blocks.values():
            if len(pair) != 2:
                raise ValueError("Every scheduled block must contain exactly two conditions")
            base = next(t for t in pair if t["config"]["treatment"] == "baseline")
            treatment = next(t for t in pair if t["id"] != base["id"])
            stratum = tuple(base["config"]["delays_ms"])
            expected_strata.add(stratum)
            if any(t["id"] not in by_id or by_id[t["id"]]["status"] != "completed" for t in pair):
                continue
            actual = [by_id[base["id"]], by_id[treatment["id"]]]
            rates = []
            for result in actual:
                flows = sorted(result["flows"], key=lambda f: f["flow"])
                if [f["cca"] for f in flows] != ccas or len(flows) != len(ccas):
                    raise ValueError("Measured flow identity differs from scheduled CCA assignment")
                rates.append([f["goodput_mbps"] for f in flows])
            strata[stratum].append((rates, [base["config"], treatment["config"]]))
        complete = sum(map(len, strata.values())) == len(blocks)
        full_grid = len(strata) == len(expected_strata)
        if not strata:
            continue
        means = [[], []]
        cell_bootstrap = {}
        for stratum, pairs in strata.items():
            for condition in (0, 1):
                means[condition].append([statistics.mean(p[0][condition][f] for p in pairs) for f in range(len(ccas))])
                for f in range(len(ccas)):
                    cell_bootstrap[(stratum, condition, f)] = []
        rng = random.Random(str(manifest["seed"])+family+cca_group)
        boot = [[[] for _ in ccas] for _ in range(2)]
        improvements = [[] for _ in ccas]
        for _ in range(bootstrap_replicates):
            sampled_means = [[], []]
            for stratum, pairs in strata.items():
                # One index selects both conditions and all flows in the whole pair.
                chosen = [pairs[rng.randrange(len(pairs))][0] for _ in pairs]
                for condition in (0, 1):
                    flow_means = [statistics.mean(p[condition][f] for p in chosen) for f in range(len(ccas))]
                    sampled_means[condition].append(flow_means)
                    for f, value in enumerate(flow_means):
                        cell_bootstrap[(stratum, condition, f)].append(value)
            for f in range(len(ccas)):
                ds = [sensitivity([cell[f] for cell in sampled_means[c]]) for c in (0, 1)]
                for c in (0, 1):
                    if ds[c] is not None:
                        boot[c][f].append(ds[c])
                if all(x is not None and math.isfinite(x) for x in ds):
                    improvements[f].append(ds[0]-ds[1])
        for f, cca in enumerate(ccas):
            delta = [sensitivity([cell[f] for cell in means[c]]) for c in (0, 1)]
            intervals = [[percentile(boot[c][f], q) for q in (0.025, 0.975)] for c in (0, 1)]
            improvement_ci = [percentile(improvements[f], q) for q in (0.025, 0.975)]
            bounded = all(d is not None and math.isfinite(d) for d in delta)
            all_bootstrap_valid = len(improvements[f]) == bootstrap_replicates
            assessment = "incomplete" if not complete else "inconclusive"
            if complete and bounded and all_bootstrap_valid:
                if improvement_ci[0] > 0:
                    assessment = "supports_reduction"
                elif improvement_ci[1] < 0:
                    assessment = "contrary_evidence"
            summaries.append({"campaign_id": manifest["id"], "family": family, "cca_group": cca_group,
                              "flow_index": f+1, "cca": cca,
                              "baseline_delta": finite(delta[0]) if full_grid else None,
                              "treatment_delta": finite(delta[1]) if full_grid else None,
                              "baseline_ci_low": finite(intervals[0][0]) if complete else None,
                              "baseline_ci_high": finite(intervals[0][1]) if complete else None,
                              "treatment_ci_low": finite(intervals[1][0]) if complete else None,
                              "treatment_ci_high": finite(intervals[1][1]) if complete else None,
                              "improvement": delta[0]-delta[1] if bounded and full_grid else None,
                              "improvement_ci_low": finite(improvement_ci[0]) if complete and all_bootstrap_valid else None,
                              "improvement_ci_high": finite(improvement_ci[1]) if complete and all_bootstrap_valid else None,
                              "assessment": assessment, "matched_pairs": sum(map(len, strata.values())),
                              "expected_pairs": len(blocks), "observed_assignments": len(strata),
                              "expected_assignments": len(expected_strata),
                              "unbounded": any(d == math.inf for d in delta), "analysis_sha256": source_sha})
        for stratum, pairs in strata.items():
            for condition in (0, 1):
                config_id = configuration_id(manifest["id"], pairs[0][1][condition])
                for f in range(len(ccas)):
                    values = [p[0][condition][f] for p in pairs]
                    shares = [p[0][condition][f]/sum(p[0][condition]) for p in pairs if sum(p[0][condition]) > 0]
                    cells.append({"configuration_id": config_id, "flow_index": f+1,
                                  "matched_repetitions": len(values), "mean_goodput_mbps": statistics.mean(values),
                                  "ci_low_mbps": percentile(cell_bootstrap[(stratum, condition, f)], 0.025) if len(pairs) >= 2 else None,
                                  "ci_high_mbps": percentile(cell_bootstrap[(stratum, condition, f)], 0.975) if len(pairs) >= 2 else None,
                                  "mean_share": statistics.mean(shares) if len(shares) == len(pairs) else None})
    return {"summaries": summaries, "cells": cells,
            "counts": {status: sum(r["status"] == status for r in results) for status in ("completed", "invalid", "failed")},
            "missing": len(planned)-len(by_id)}
