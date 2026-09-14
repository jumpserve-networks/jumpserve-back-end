#!/usr/bin/env python3
"""Summarize Cloudflare cache probe JSON Lines output."""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


TIMING_METRICS = (
    ("edge_rtt_estimate_ms", "TCP connect RTT estimate"),
    ("request_ttfb_ms", "request-to-first-byte"),
    ("fetch_residual_ms", "TTFB minus RTT estimate"),
    ("starttransfer_total_ms", "DNS+connect+TLS+TTFB"),
)


def load_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    path_list = list(paths)
    sources = [("<stdin>", sys.stdin)] if not path_list else []
    opened = []
    try:
        for path in path_list:
            stream = path.open("r", encoding="utf-8")
            opened.append(stream)
            sources.append((str(path), stream))
        for source_name, stream in sources:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{source_name}:{line_number}: {exc}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"{source_name}:{line_number}: expected a JSON object")
                records.append(value)
    finally:
        for stream in opened:
            stream.close()
    return records


def numeric_values(records: Iterable[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for record in records:
        raw = record.get(field)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            values.append(float(raw))
    return values


def format_median(values: list[float]) -> str:
    return "-" if not values else f"{statistics.median(values):.2f}"


def lower_is_positive_auc(positive: list[float], negative: list[float]) -> float | None:
    """Probability that a random positive observation is lower than a negative."""
    if not positive or not negative:
        return None
    sorted_negative = sorted(negative)
    wins = 0.0
    for value in positive:
        left = bisect.bisect_left(sorted_negative, value)
        right = bisect.bisect_right(sorted_negative, value)
        wins += len(sorted_negative) - right
        wins += 0.5 * (right - left)
    return wins / (len(positive) * len(negative))


def group_key(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("vantage") or "unknown"), str(record.get("cf_colo") or "unknown"))


def clean_cache_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record.get("cf_cache_status") in {"HIT", "MISS"}]


def assigned_demand_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if record.get("ok") and record.get("treatment") in {"hot", "cold"}
    ]


def print_status_summary(records: list[dict[str, Any]]) -> None:
    print("Cache status by vantage and Cloudflare colo")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[group_key(record)].append(record)
    for key in sorted(groups):
        statuses = Counter(str(record.get("cf_cache_status") or "NONE") for record in groups[key])
        rendered = ", ".join(f"{status}={count}" for status, count in sorted(statuses.items()))
        print(f"  vantage={key[0]} colo={key[1]}: {rendered}")


def print_treatment_summary(records: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in clean_cache_records(records):
        treatment = str(record.get("treatment") or "unspecified")
        groups[(*group_key(record), treatment)].append(record)
    if not groups:
        return
    print("\nObserved cache-hit rate (exact HIT / [HIT + MISS])")
    for key in sorted(groups):
        rows = groups[key]
        hits = sum(record.get("cf_cache_status") == "HIT" for record in rows)
        print(
            f"  vantage={key[0]} colo={key[1]} treatment={key[2]}: "
            f"{hits}/{len(rows)} = {hits / len(rows):.1%}"
        )


def print_assigned_demand_timing_summary(
    records: list[dict[str, Any]], minimum_per_class: int
) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in assigned_demand_records(records):
        groups[group_key(record)].append(record)

    print(
        "\nAssigned recent-demand discrimination within each vantage/colo "
        "(positive=hot; no cache-status feature)"
    )
    printed = False
    for key in sorted(groups):
        hot = [row for row in groups[key] if row.get("treatment") == "hot"]
        cold = [row for row in groups[key] if row.get("treatment") == "cold"]
        if len(hot) < minimum_per_class or len(cold) < minimum_per_class:
            continue
        printed = True
        print(f"  vantage={key[0]} colo={key[1]} (hot={len(hot)}, cold={len(cold)})")
        for field, label in TIMING_METRICS:
            hot_values = numeric_values(hot, field)
            cold_values = numeric_values(cold, field)
            auc = lower_is_positive_auc(hot_values, cold_values)
            auc_text = "-" if auc is None else f"{auc:.3f}"
            print(
                f"    {label:<27} hot_med={format_median(hot_values):>8} ms "
                f"cold_med={format_median(cold_values):>8} ms auc={auc_text}"
            )
    if not printed:
        print(
            f"  Not enough assigned hot and cold observations in the same group "
            f"(need at least {minimum_per_class} of each)."
        )


def print_cache_timing_summary(records: list[dict[str, Any]], minimum_per_class: int) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in clean_cache_records(records):
        groups[group_key(record)].append(record)

    print(
        "\nCache-mechanism discrimination within each vantage/colo "
        "(AUC=0.5 is chance; AUC=1.0 means every HIT was faster)"
    )
    printed = False
    for key in sorted(groups):
        hits = [row for row in groups[key] if row.get("cf_cache_status") == "HIT"]
        misses = [row for row in groups[key] if row.get("cf_cache_status") == "MISS"]
        if len(hits) < minimum_per_class or len(misses) < minimum_per_class:
            continue
        printed = True
        print(f"  vantage={key[0]} colo={key[1]} (HIT={len(hits)}, MISS={len(misses)})")
        for field, label in TIMING_METRICS:
            hit_values = numeric_values(hits, field)
            miss_values = numeric_values(misses, field)
            auc = lower_is_positive_auc(hit_values, miss_values)
            auc_text = "-" if auc is None else f"{auc:.3f}"
            print(
                f"    {label:<27} hit_med={format_median(hit_values):>8} ms "
                f"miss_med={format_median(miss_values):>8} ms auc={auc_text}"
            )
    if not printed:
        print(
            f"  Not enough labeled HIT and MISS observations in the same group "
            f"(need at least {minimum_per_class} of each)."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze JSON Lines emitted by the Cloudflare cache probe."
    )
    parser.add_argument("paths", nargs="*", type=Path, help="JSONL paths (default: stdin)")
    parser.add_argument(
        "--phase",
        help="Only analyze records with this phase label (recommended: measure)",
    )
    parser.add_argument(
        "--minimum-per-class",
        type=int,
        default=3,
        help="Minimum HITs and MISSes for a timing comparison",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.minimum_per_class < 1:
        print("error: --minimum-per-class must be at least 1", file=sys.stderr)
        return 2
    try:
        loaded_records = load_records(args.paths)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    records = loaded_records
    if args.phase is not None:
        records = [record for record in records if record.get("phase") == args.phase]
    if not loaded_records:
        print("error: no records found", file=sys.stderr)
        return 2
    if not records:
        print(f"error: no records matched phase {args.phase!r}", file=sys.stderr)
        return 2

    ok_count = sum(bool(record.get("ok")) for record in records)
    selection = "" if args.phase is None else f" for phase={args.phase}"
    print(
        f"Analyzing {len(records)} of {len(loaded_records)} loaded records{selection} "
        f"({ok_count} successful, {len(records) - ok_count} failed)."
    )
    print_status_summary(records)
    print_treatment_summary(records)
    print_assigned_demand_timing_summary(records, args.minimum_per_class)
    print_cache_timing_summary(records, args.minimum_per_class)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
