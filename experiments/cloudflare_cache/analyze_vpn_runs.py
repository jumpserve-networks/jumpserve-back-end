#!/usr/bin/env python3
"""Summarize external VPN trials without comparing timing across colos or runs."""

from __future__ import annotations

import argparse
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

if __package__:
    from .analyze import (
        TIMING_METRICS,
        load_records,
        lower_is_positive_auc,
        numeric_values,
    )
else:
    from analyze import (  # type: ignore[no-redef]
        TIMING_METRICS,
        load_records,
        lower_is_positive_auc,
        numeric_values,
    )


@dataclass(frozen=True)
class TrialSummary:
    trial: str
    vantage: str
    colos: tuple[str, ...]
    hot_hits: int
    hot_total: int
    cold_misses: int
    cold_total: int
    demand_auc: dict[str, float | None]


def stratified_auc(
    records: Iterable[dict[str, Any]],
    field: str,
    *,
    label_field: str = "treatment",
    positive_label: str = "hot",
    negative_label: str = "cold",
) -> float | None:
    """Pair-weighted AUC using comparisons only within each observed colo."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record.get("cf_colo") or "unknown")].append(record)

    weighted_wins = 0.0
    pair_count = 0
    for rows in groups.values():
        positive = numeric_values(
            (row for row in rows if row.get(label_field) == positive_label), field
        )
        negative = numeric_values(
            (row for row in rows if row.get(label_field) == negative_label), field
        )
        auc = lower_is_positive_auc(positive, negative)
        if auc is None:
            continue
        pairs = len(positive) * len(negative)
        weighted_wins += auc * pairs
        pair_count += pairs
    return None if pair_count == 0 else weighted_wins / pair_count


def summarize_trial(path: Path, phase: str) -> TrialSummary:
    records = [
        record
        for record in load_records([path])
        if record.get("phase") == phase and record.get("ok")
    ]
    vantages = {str(record.get("vantage") or "unknown") for record in records}
    if not records:
        raise ValueError(f"{path}: no successful records for phase {phase!r}")
    if len(vantages) != 1:
        raise ValueError(f"{path}: expected one vantage, found {sorted(vantages)}")

    hot = [record for record in records if record.get("treatment") == "hot"]
    cold = [record for record in records if record.get("treatment") == "cold"]
    return TrialSummary(
        trial=path.parent.name,
        vantage=next(iter(vantages)),
        colos=tuple(sorted({str(record.get("cf_colo") or "unknown") for record in records})),
        hot_hits=sum(record.get("cf_cache_status") == "HIT" for record in hot),
        hot_total=len(hot),
        cold_misses=sum(record.get("cf_cache_status") == "MISS" for record in cold),
        cold_total=len(cold),
        demand_auc={
            field: stratified_auc(records, field) for field, _label in TIMING_METRICS
        },
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
    """Percentile interval from resampling independent trial estimates."""
    if not values:
        raise ValueError("cannot bootstrap an empty value list")
    rng = random.Random(seed)
    samples = [
        statistics.fmean(rng.choice(values) for _ in values) for _ in range(iterations)
    ]
    return percentile(samples, 0.025), percentile(samples, 0.975)


def format_auc(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report one assigned-demand timing estimate per VPN trial, using only "
            "within-colo positive/negative comparisons."
        )
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--phase", default="measure")
    parser.add_argument("--bootstrap-iterations", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260827)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.bootstrap_iterations < 1:
        print("error: --bootstrap-iterations must be positive")
        return 2
    try:
        trials = [summarize_trial(path, args.phase) for path in args.paths]
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 2

    print("Run-level assigned-demand results (AUC comparisons are within colo)")
    print("  trial | vantage | colos | hot HIT | cold MISS | RTT AUC | TTFB AUC | residual AUC")
    for trial in trials:
        print(
            f"  {trial.trial} | {trial.vantage} | {','.join(trial.colos)} | "
            f"{trial.hot_hits}/{trial.hot_total} | {trial.cold_misses}/{trial.cold_total} | "
            f"{format_auc(trial.demand_auc['edge_rtt_estimate_ms'])} | "
            f"{format_auc(trial.demand_auc['request_ttfb_ms'])} | "
            f"{format_auc(trial.demand_auc['fetch_residual_ms'])}"
        )

    by_vantage: dict[str, list[TrialSummary]] = defaultdict(list)
    for trial in trials:
        by_vantage[trial.vantage].append(trial)
    print("\nAcross-run assigned-demand estimates")
    for vantage in sorted(by_vantage):
        vantage_trials = by_vantage[vantage]
        print(f"  vantage={vantage} trials={len(vantage_trials)}")
        for index, (field, label) in enumerate(TIMING_METRICS):
            values = [
                value
                for trial in vantage_trials
                if (value := trial.demand_auc[field]) is not None
            ]
            if not values:
                continue
            lower, upper = trial_bootstrap_interval(
                values,
                iterations=args.bootstrap_iterations,
                seed=args.seed + index,
            )
            print(
                f"    {label:<27} mean={statistics.fmean(values):.3f} "
                f"range={min(values):.3f}-{max(values):.3f} "
                f"trial-bootstrap-95%={lower:.3f}-{upper:.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
