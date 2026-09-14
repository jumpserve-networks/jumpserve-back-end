#!/usr/bin/env python3
"""Analyze JSON returned by the geographically placed Cloudflare probe Workers."""

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
    ("headers_ms", "response headers"),
    ("first_body_ms", "first body byte"),
    ("total_ms", "complete 256 KiB body"),
)


def load_documents(paths: Iterable[Path]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("results"), list):
            raise ValueError(f"{path}: expected an object containing a results array")
        documents.append(value)
    return documents


def cloudflare_colo(cf_ray: object) -> str:
    if not isinstance(cf_ray, str) or "-" not in cf_ray:
        return "unknown"
    return cf_ray.rsplit("-", 1)[1]


def flatten_documents(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for document in documents:
        for result in document["results"]:
            if not isinstance(result, dict):
                continue
            records.append(
                {
                    **result,
                    "configured_region": str(document.get("configured_region") or "unknown"),
                    "probe_slug": str(document.get("probe_slug") or "unknown"),
                    "target_colo": cloudflare_colo(result.get("cf_ray")),
                }
            )
    return records


def numeric_values(records: Iterable[dict[str, Any]], field: str) -> list[float]:
    return [
        float(record[field])
        for record in records
        if isinstance(record.get(field), (int, float))
        and not isinstance(record.get(field), bool)
    ]


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


def format_number(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def analyze(records: list[dict[str, Any]], minimum_per_class: int) -> int:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(record["configured_region"], record["target_colo"])].append(record)

    failures = sum(not bool(record.get("ok")) for record in records)
    print(f"Loaded {len(records)} regional measurements ({failures} failed).")
    print(
        "AUC=0.5 is chance; AUC=1.0 means every HIT was faster than every MISS."
    )

    complete_groups = 0
    for key in sorted(groups):
        rows = groups[key]
        hits = [row for row in rows if row.get("cf_cache_status") == "HIT"]
        misses = [row for row in rows if row.get("cf_cache_status") == "MISS"]
        labels = Counter(
            (str(row.get("treatment") or "unknown"), str(row.get("cf_cache_status") or "NONE"))
            for row in rows
        )
        rendered_labels = ", ".join(
            f"{treatment}/{status}={count}"
            for (treatment, status), count in sorted(labels.items())
        )
        print(f"\nregion={key[0]} colo={key[1]}: {rendered_labels}")

        if len(hits) < minimum_per_class or len(misses) < minimum_per_class:
            print(
                f"  insufficient clean labels; need {minimum_per_class} HITs and MISSes"
            )
            continue
        complete_groups += 1

        for field, label in TIMING_METRICS:
            hit_values = numeric_values(hits, field)
            miss_values = numeric_values(misses, field)
            auc = lower_is_positive_auc(hit_values, miss_values)
            hit_median = statistics.median(hit_values) if hit_values else None
            miss_median = statistics.median(miss_values) if miss_values else None
            advantage = (
                miss_median - hit_median
                if hit_median is not None and miss_median is not None
                else None
            )
            print(
                f"  {label:<23} "
                f"hit_med={format_number(hit_median):>8} ms "
                f"miss_med={format_number(miss_median):>8} ms "
                f"advantage={format_number(advantage):>8} ms "
                f"auc={format_number(auc)}"
            )

    if complete_groups == 0:
        print("\nNo region had enough exact HIT and MISS labels.")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze output from the geographically placed Cloudflare probes."
    )
    parser.add_argument("paths", nargs="+", type=Path, help="Regional measurement JSON")
    parser.add_argument(
        "--minimum-per-class",
        type=int,
        default=3,
        help="Minimum HITs and MISSes required for a regional comparison",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.minimum_per_class < 1:
        print("error: --minimum-per-class must be at least 1", file=sys.stderr)
        return 2
    try:
        documents = load_documents(args.paths)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return analyze(flatten_documents(documents), args.minimum_per_class)


if __name__ == "__main__":
    raise SystemExit(main())
