#!/usr/bin/env python3
"""Analyze assigned-demand Fastly measurements within each observed POP."""

from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


TIMING_METRICS = (
    ("edge_rtt_estimate_ms", "TCP connect RTT estimate"),
    ("request_ttfb_ms", "request-to-first-byte"),
    ("fetch_residual_ms", "TTFB minus RTT estimate"),
)


def load_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
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


def format_auc(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def bootstrap_auc_interval(
    positive: list[float],
    negative: list[float],
    *,
    iterations: int,
    seed: int,
) -> tuple[float, float] | None:
    if not positive or not negative:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(iterations):
        sampled_positive = [rng.choice(positive) for _ in positive]
        sampled_negative = [rng.choice(negative) for _ in negative]
        estimate = lower_is_positive_auc(sampled_positive, sampled_negative)
        if estimate is not None:
            estimates.append(estimate)
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--phase", default="measure")
    parser.add_argument("--bootstrap-iterations", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260827)
    args = parser.parse_args(argv)
    if args.bootstrap_iterations < 1:
        print("error: --bootstrap-iterations must be positive")
        return 2
    try:
        records = [
            record
            for record in load_records(args.paths)
            if record.get("phase") == args.phase and record.get("ok")
        ]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 2
    if not records:
        print(f"error: no successful {args.phase!r} records")
        return 2

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[
            (
                str(record.get("vantage") or "unknown"),
                str(record.get("fastly_pop") or "unknown"),
            )
        ].append(record)

    print("Fastly assigned-demand results (comparisons are within observed POP)")
    for group_index, ((vantage, pop), rows) in enumerate(sorted(groups.items())):
        hot = [row for row in rows if row.get("treatment") == "hot"]
        cold = [row for row in rows if row.get("treatment") == "cold"]
        statuses = Counter(str(row.get("fastly_cache_status") or "NONE") for row in rows)
        print(
            f"  vantage={vantage} pop={pop} hot={len(hot)} cold={len(cold)} "
            f"statuses={dict(sorted(statuses.items()))}"
        )
        print(
            "    treatment integrity: "
            f"hot HIT={sum(row.get('fastly_cache_status') == 'HIT' for row in hot)}/{len(hot)}, "
            f"cold MISS={sum(row.get('fastly_cache_status') == 'MISS' for row in cold)}/{len(cold)}"
        )
        for metric_index, (field, label) in enumerate(TIMING_METRICS):
            hot_values = numeric_values(hot, field)
            cold_values = numeric_values(cold, field)
            auc = lower_is_positive_auc(hot_values, cold_values)
            interval = bootstrap_auc_interval(
                hot_values,
                cold_values,
                iterations=args.bootstrap_iterations,
                seed=args.seed + 100 * group_index + metric_index,
            )
            hot_median = "-" if not hot_values else f"{statistics.median(hot_values):.2f}"
            cold_median = "-" if not cold_values else f"{statistics.median(cold_values):.2f}"
            interval_text = (
                "-"
                if interval is None
                else f"{interval[0]:.3f}-{interval[1]:.3f}"
            )
            print(
                f"    {label:<25} hot_med={hot_median:>8} ms "
                f"cold_med={cold_median:>8} ms auc={format_auc(auc)} "
                f"bootstrap95={interval_text}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
