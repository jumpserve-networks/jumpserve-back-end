#!/usr/bin/env python3
"""Analyze paired external Cloudflare/Fastly trials within observed POPs."""

from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


TIMING_METRICS = (
    ("edge_rtt_estimate_ms", "TCP connect RTT estimate"),
    ("request_ttfb_ms", "request-to-first-byte"),
    ("fetch_residual_ms", "TTFB minus RTT estimate"),
)


@dataclass(frozen=True)
class RunSummary:
    trial: str
    provider: str
    vantage: str
    pops: tuple[str, ...]
    successful: int
    failed: int
    hot_hits: int
    hot_total: int
    cold_misses: int
    cold_total: int
    demand_auc: dict[str, float | None]
    cache_auc: dict[str, float | None]


@dataclass(frozen=True)
class ThresholdResult:
    threshold_ms: float
    train_balanced_accuracy: float
    test_accuracy: float
    test_balanced_accuracy: float
    hot_correct: int
    hot_total: int
    cold_correct: int
    cold_total: int


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            records.append(value)
    return records


def numeric_values(records: Iterable[dict[str, Any]], field: str) -> list[float]:
    return [
        float(value)
        for record in records
        if isinstance((value := record.get(field)), (int, float))
        and not isinstance(value, bool)
    ]


def lower_is_positive_auc(positive: list[float], negative: list[float]) -> float | None:
    if not positive or not negative:
        return None
    ordered_negative = sorted(negative)
    wins = 0.0
    for value in positive:
        left = bisect.bisect_left(ordered_negative, value)
        right = bisect.bisect_right(ordered_negative, value)
        wins += len(ordered_negative) - right + 0.5 * (right - left)
    return wins / (len(positive) * len(negative))


def provider_from_path(path: Path) -> str:
    name = path.name
    for provider in ("cloudflare", "fastly"):
        if name.startswith(f"{provider}."):
            return provider
    raise ValueError(f"{path}: filename must start with cloudflare. or fastly.")


def pop_of(record: dict[str, Any], provider: str) -> str:
    field = "cf_colo" if provider == "cloudflare" else "fastly_pop"
    return str(record.get(field) or "unknown")


def cache_status_of(record: dict[str, Any], provider: str) -> str:
    field = "cf_cache_status" if provider == "cloudflare" else "fastly_cache_status"
    return str(record.get(field) or "NONE").upper()


def stratified_auc(
    records: Iterable[dict[str, Any]], provider: str, field: str
) -> float | None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[pop_of(record, provider)].append(record)

    weighted_wins = 0.0
    pair_count = 0
    for rows in groups.values():
        hot = numeric_values(
            (row for row in rows if row.get("treatment") == "hot"), field
        )
        cold = numeric_values(
            (row for row in rows if row.get("treatment") == "cold"), field
        )
        auc = lower_is_positive_auc(hot, cold)
        if auc is None:
            continue
        pairs = len(hot) * len(cold)
        weighted_wins += auc * pairs
        pair_count += pairs
    return None if pair_count == 0 else weighted_wins / pair_count


def stratified_cache_auc(
    records: Iterable[dict[str, Any]], provider: str, field: str
) -> float | None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[pop_of(record, provider)].append(record)

    weighted_wins = 0.0
    pair_count = 0
    for rows in groups.values():
        hits = numeric_values(
            (row for row in rows if cache_status_of(row, provider) == "HIT"), field
        )
        misses = numeric_values(
            (row for row in rows if cache_status_of(row, provider) == "MISS"), field
        )
        auc = lower_is_positive_auc(hits, misses)
        if auc is None:
            continue
        pairs = len(hits) * len(misses)
        weighted_wins += auc * pairs
        pair_count += pairs
    return None if pair_count == 0 else weighted_wins / pair_count


def summarize(path: Path, phase: str) -> RunSummary:
    provider = provider_from_path(path)
    all_records = [record for record in load_records(path) if record.get("phase") == phase]
    successful = [record for record in all_records if record.get("ok")]
    if not successful:
        raise ValueError(f"{path}: no successful {phase!r} records")
    vantages = {str(record.get("vantage") or "unknown") for record in successful}
    if len(vantages) != 1:
        raise ValueError(f"{path}: expected one vantage, found {sorted(vantages)}")
    hot = [record for record in all_records if record.get("treatment") == "hot"]
    cold = [record for record in all_records if record.get("treatment") == "cold"]
    return RunSummary(
        trial=path.parents[1].name,
        provider=provider,
        vantage=next(iter(vantages)),
        pops=tuple(sorted({pop_of(record, provider) for record in successful})),
        successful=len(successful),
        failed=len(all_records) - len(successful),
        hot_hits=sum(cache_status_of(record, provider) == "HIT" for record in hot),
        hot_total=len(hot),
        cold_misses=sum(cache_status_of(record, provider) == "MISS" for record in cold),
        cold_total=len(cold),
        demand_auc={
            field: stratified_auc(successful, provider, field)
            for field, _label in TIMING_METRICS
        },
        cache_auc={
            field: stratified_cache_auc(successful, provider, field)
            for field, _label in TIMING_METRICS
        },
    )


def successful_phase_records(path: Path, phase: str) -> list[dict[str, Any]]:
    return [
        record
        for record in load_records(path)
        if record.get("phase") == phase and record.get("ok")
    ]


def choose_lower_threshold(
    positive: list[float], negative: list[float]
) -> tuple[float, float]:
    if not positive or not negative:
        raise ValueError("threshold training requires both hot and cold observations")
    values = sorted(set(positive + negative))
    candidates = [values[0] - 1.0]
    candidates.extend((left + right) / 2.0 for left, right in zip(values, values[1:]))
    candidates.append(values[-1] + 1.0)

    best_threshold = candidates[0]
    best_balanced_accuracy = -1.0
    for threshold in candidates:
        sensitivity = sum(value <= threshold for value in positive) / len(positive)
        specificity = sum(value > threshold for value in negative) / len(negative)
        balanced_accuracy = (sensitivity + specificity) / 2.0
        if balanced_accuracy > best_balanced_accuracy:
            best_threshold = threshold
            best_balanced_accuracy = balanced_accuracy
    return best_threshold, best_balanced_accuracy


def evaluate_threshold(
    training: Iterable[dict[str, Any]],
    testing: Iterable[dict[str, Any]],
    *,
    field: str,
) -> ThresholdResult:
    training_rows = list(training)
    testing_rows = list(testing)
    train_hot = numeric_values(
        (row for row in training_rows if row.get("treatment") == "hot"), field
    )
    train_cold = numeric_values(
        (row for row in training_rows if row.get("treatment") == "cold"), field
    )
    threshold, train_balanced_accuracy = choose_lower_threshold(train_hot, train_cold)
    test_hot = numeric_values(
        (row for row in testing_rows if row.get("treatment") == "hot"), field
    )
    test_cold = numeric_values(
        (row for row in testing_rows if row.get("treatment") == "cold"), field
    )
    if not test_hot or not test_cold:
        raise ValueError("threshold testing requires both hot and cold observations")
    hot_correct = sum(value <= threshold for value in test_hot)
    cold_correct = sum(value > threshold for value in test_cold)
    accuracy = (hot_correct + cold_correct) / (len(test_hot) + len(test_cold))
    balanced_accuracy = (
        hot_correct / len(test_hot) + cold_correct / len(test_cold)
    ) / 2.0
    return ThresholdResult(
        threshold_ms=threshold,
        train_balanced_accuracy=train_balanced_accuracy,
        test_accuracy=accuracy,
        test_balanced_accuracy=balanced_accuracy,
        hot_correct=hot_correct,
        hot_total=len(test_hot),
        cold_correct=cold_correct,
        cold_total=len(test_cold),
    )


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def trial_bootstrap_interval(
    values: list[float], *, iterations: int, seed: int
) -> tuple[float, float]:
    rng = random.Random(seed)
    estimates = [
        statistics.fmean(rng.choice(values) for _ in values) for _ in range(iterations)
    ]
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def format_auc(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def discover_paths(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for value in inputs:
        if value.is_dir():
            paths.extend(sorted(value.glob("trial-*/*/*.measure.jsonl")))
        else:
            paths.append(value)
    unique = sorted(set(path.resolve() for path in paths))
    if not unique:
        raise ValueError("no measurement JSONL files found")
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report assigned-demand timing within observed Cloudflare colos and "
            "Fastly POPs, with one estimate per trial/provider/vantage."
        )
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--phase", default="measure")
    parser.add_argument("--bootstrap-iterations", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260827)
    args = parser.parse_args(argv)
    if args.bootstrap_iterations < 1:
        parser.error("--bootstrap-iterations must be positive")

    try:
        paths = discover_paths(args.paths)
        summaries = [summarize(path, args.phase) for path in paths]
        records_by_key = {
            (summary.trial, summary.provider, summary.vantage): successful_phase_records(
                path, args.phase
            )
            for path, summary in zip(paths, summaries)
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 2

    print("Paired external assigned-demand results (AUC comparisons are within POP)")
    print(
        "  trial | provider | vantage | POPs | ok/fail | hot HIT | cold MISS | "
        "RTT AUC | TTFB AUC | residual AUC | cache-label TTFB AUC"
    )
    for summary in summaries:
        print(
            f"  {summary.trial} | {summary.provider} | {summary.vantage} | "
            f"{','.join(summary.pops)} | {summary.successful}/{summary.failed} | "
            f"{summary.hot_hits}/{summary.hot_total} | "
            f"{summary.cold_misses}/{summary.cold_total} | "
            f"{format_auc(summary.demand_auc['edge_rtt_estimate_ms'])} | "
            f"{format_auc(summary.demand_auc['request_ttfb_ms'])} | "
            f"{format_auc(summary.demand_auc['fetch_residual_ms'])} | "
            f"{format_auc(summary.cache_auc['request_ttfb_ms'])}"
        )

    by_provider: dict[str, list[RunSummary]] = defaultdict(list)
    for summary in summaries:
        by_provider[summary.provider].append(summary)
    print("\nTreatment integrity totals (cache labels use all attempted observations)")
    for provider, runs in sorted(by_provider.items()):
        print(
            f"  provider={provider} "
            f"ok/fail={sum(run.successful for run in runs)}/"
            f"{sum(run.failed for run in runs)} "
            f"hot_HIT={sum(run.hot_hits for run in runs)}/"
            f"{sum(run.hot_total for run in runs)} "
            f"cold_MISS={sum(run.cold_misses for run in runs)}/"
            f"{sum(run.cold_total for run in runs)}"
        )

    grouped: dict[tuple[str, str], list[RunSummary]] = defaultdict(list)
    for summary in summaries:
        grouped[(summary.provider, summary.vantage)].append(summary)

    print("\nAcross-run assigned-demand estimates")
    for group_index, ((provider, vantage), runs) in enumerate(sorted(grouped.items())):
        print(f"  provider={provider} vantage={vantage} trials={len(runs)}")
        for metric_index, (field, label) in enumerate(TIMING_METRICS):
            values = [
                value
                for run in runs
                if (value := run.demand_auc[field]) is not None
            ]
            if not values:
                continue
            lower, upper = trial_bootstrap_interval(
                values,
                iterations=args.bootstrap_iterations,
                seed=args.seed + group_index * 100 + metric_index,
            )
            print(
                f"    {label:<27} mean={statistics.fmean(values):.3f} "
                f"range={min(values):.3f}-{max(values):.3f} "
                f"trial-bootstrap-95%={lower:.3f}-{upper:.3f}"
            )

    paired: dict[tuple[str, str], dict[str, RunSummary]] = defaultdict(dict)
    for summary in summaries:
        paired[(summary.trial, summary.vantage)][summary.provider] = summary
    print("\nMatched Fastly minus Cloudflare AUC differences")
    for field, label in TIMING_METRICS:
        differences = []
        for providers in paired.values():
            if set(providers) != {"cloudflare", "fastly"}:
                continue
            cloudflare_auc = providers["cloudflare"].demand_auc[field]
            fastly_auc = providers["fastly"].demand_auc[field]
            if cloudflare_auc is not None and fastly_auc is not None:
                differences.append(fastly_auc - cloudflare_auc)
        if differences:
            print(
                f"  {label:<27} n={len(differences)} "
                f"mean_delta={statistics.fmean(differences):+.3f} "
                f"range={min(differences):+.3f}-{max(differences):+.3f}"
            )

    print("\nHeld-out TTFB operating point (train trial-01; apply unchanged to 02+03)")
    provider_vantages = sorted({(run.provider, run.vantage) for run in summaries})
    for provider, vantage in provider_vantages:
        training = records_by_key.get(("trial-01", provider, vantage), [])
        testing = [
            record
            for (trial, candidate_provider, candidate_vantage), records in records_by_key.items()
            if trial != "trial-01"
            and candidate_provider == provider
            and candidate_vantage == vantage
            for record in records
        ]
        if not training or not testing:
            continue
        result = evaluate_threshold(training, testing, field="request_ttfb_ms")
        print(
            f"  provider={provider} vantage={vantage} "
            f"threshold_ms={result.threshold_ms:.3f} "
            f"train_bal_acc={result.train_balanced_accuracy:.3f} "
            f"test_acc={result.test_accuracy:.3f} "
            f"test_bal_acc={result.test_balanced_accuracy:.3f} "
            f"hot={result.hot_correct}/{result.hot_total} "
            f"cold={result.cold_correct}/{result.cold_total}"
        )

    print("\nExploratory cross-provider TTFB threshold transfer")
    for source_provider, target_provider in (("cloudflare", "fastly"), ("fastly", "cloudflare")):
        for vantage in sorted({run.vantage for run in summaries}):
            training = records_by_key.get(("trial-01", source_provider, vantage), [])
            testing = [
                record
                for (trial, provider, candidate_vantage), records in records_by_key.items()
                if trial != "trial-01"
                and provider == target_provider
                and candidate_vantage == vantage
                for record in records
            ]
            if not training or not testing:
                continue
            result = evaluate_threshold(training, testing, field="request_ttfb_ms")
            print(
                f"  train={source_provider} test={target_provider} vantage={vantage} "
                f"threshold_ms={result.threshold_ms:.3f} "
                f"test_acc={result.test_accuracy:.3f} "
                f"test_bal_acc={result.test_balanced_accuracy:.3f} "
                f"hot={result.hot_correct}/{result.hot_total} "
                f"cold={result.cold_correct}/{result.cold_total}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
