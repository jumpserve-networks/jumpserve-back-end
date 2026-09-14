#!/usr/bin/env python3
"""Analyze matched three-CDN trials only within response-observed POPs."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiments.cdn_comparison import analyze_vpn_trials as base  # noqa: E402


PROVIDER_FIELDS = {
    "cloudflare": ("cf_colo", "cf_cache_status"),
    "fastly": ("fastly_pop", "fastly_cache_status"),
    "cloudfront": ("cloudfront_pop", "cloudfront_cache_status"),
}
ELIGIBLE_BODY_BYTES = 262144


@dataclass(frozen=True)
class Cell:
    trial: str
    provider: str
    vantage: str
    pops: tuple[str, ...]
    attempted: int
    eligible: int
    hot_hits: int
    hot_total: int
    cold_misses: int
    cold_total: int
    auc: dict[str, float | None]


def provider_from_path(path: Path) -> str:
    for provider in PROVIDER_FIELDS:
        if path.name.startswith(f"{provider}."):
            return provider
    raise ValueError(f"{path}: unrecognized provider filename")


def pop_of(record: dict[str, Any], provider: str) -> str:
    return str(record.get(PROVIDER_FIELDS[provider][0]) or "unknown")


def status_of(record: dict[str, Any], provider: str) -> str:
    return str(record.get(PROVIDER_FIELDS[provider][1]) or "NONE").upper()


def eligible(record: dict[str, Any], provider: str) -> bool:
    return bool(
        record.get("phase") == "measure"
        and record.get("ok")
        and record.get("http_code") == 200
        and record.get("size_download_bytes") == ELIGIBLE_BODY_BYTES
        and pop_of(record, provider) != "unknown"
    )


def stratified_auc(
    records: list[dict[str, Any]], provider: str, field: str
) -> float | None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[pop_of(record, provider)].append(record)
    wins = 0.0
    pairs = 0
    for rows in groups.values():
        hot = base.numeric_values(
            (row for row in rows if row.get("treatment") == "hot"), field
        )
        cold = base.numeric_values(
            (row for row in rows if row.get("treatment") == "cold"), field
        )
        value = base.lower_is_positive_auc(hot, cold)
        if value is not None:
            stratum_pairs = len(hot) * len(cold)
            wins += value * stratum_pairs
            pairs += stratum_pairs
    return None if pairs == 0 else wins / pairs


def summarize(path: Path) -> tuple[Cell, list[dict[str, Any]]]:
    provider = provider_from_path(path)
    attempted = [
        row for row in base.load_records(path) if row.get("phase") == "measure"
    ]
    rows = [row for row in attempted if eligible(row, provider)]
    if not attempted:
        raise ValueError(f"{path}: no measurement records")
    vantages = {str(row.get("vantage") or "unknown") for row in attempted}
    if len(vantages) != 1:
        raise ValueError(f"{path}: expected one vantage")
    hot = [row for row in attempted if row.get("treatment") == "hot"]
    cold = [row for row in attempted if row.get("treatment") == "cold"]
    return (
        Cell(
            trial=path.parents[1].name,
            provider=provider,
            vantage=next(iter(vantages)),
            pops=tuple(sorted({pop_of(row, provider) for row in rows})),
            attempted=len(attempted),
            eligible=len(rows),
            hot_hits=sum(status_of(row, provider) == "HIT" for row in hot),
            hot_total=len(hot),
            cold_misses=sum(status_of(row, provider) == "MISS" for row in cold),
            cold_total=len(cold),
            auc={
                field: stratified_auc(rows, provider, field)
                for field, _label in base.TIMING_METRICS
            },
        ),
        rows,
    )


def discover(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for value in inputs:
        if value.is_dir():
            paths.extend(value.glob("trial-*/*/*.measure.jsonl"))
        else:
            paths.append(value)
    selected = sorted(
        path.resolve()
        for path in set(paths)
        if any(path.name.startswith(f"{provider}.") for provider in PROVIDER_FIELDS)
    )
    if not selected:
        raise ValueError("no three-CDN measurement files found")
    return selected


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--bootstrap-iterations", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260829)
    args = parser.parse_args(argv)
    if args.bootstrap_iterations < 1:
        parser.error("--bootstrap-iterations must be positive")
    try:
        loaded = [summarize(path) for path in discover(args.paths)]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    cells = [cell for cell, _rows in loaded]
    records = {
        (cell.trial, cell.provider, cell.vantage): rows
        for cell, rows in loaded
    }

    print("Three-CDN assigned-demand results (all AUC comparisons are within POP)")
    print(
        "  trial | provider | vantage | POPs | eligible/attempted | hot HIT | "
        "cold MISS | RTT AUC | TTFB AUC | residual AUC"
    )
    for cell in cells:
        print(
            f"  {cell.trial} | {cell.provider} | {cell.vantage} | "
            f"{','.join(cell.pops) or '-'} | {cell.eligible}/{cell.attempted} | "
            f"{cell.hot_hits}/{cell.hot_total} | {cell.cold_misses}/{cell.cold_total} | "
            f"{fmt(cell.auc['edge_rtt_estimate_ms'])} | "
            f"{fmt(cell.auc['request_ttfb_ms'])} | "
            f"{fmt(cell.auc['fetch_residual_ms'])}"
        )

    print("\nAcross-trial estimates")
    grouped: dict[tuple[str, str], list[Cell]] = defaultdict(list)
    for cell in cells:
        grouped[(cell.provider, cell.vantage)].append(cell)
    for group_index, ((provider, vantage), values) in enumerate(sorted(grouped.items())):
        print(f"  provider={provider} vantage={vantage} trials={len(values)}")
        for metric_index, (field, label) in enumerate(base.TIMING_METRICS):
            aucs = [value for cell in values if (value := cell.auc[field]) is not None]
            if not aucs:
                continue
            lower, upper = base.trial_bootstrap_interval(
                aucs,
                iterations=args.bootstrap_iterations,
                seed=args.seed + group_index * 100 + metric_index,
            )
            print(
                f"    {label:<27} mean={statistics.fmean(aucs):.3f} "
                f"range={min(aucs):.3f}-{max(aucs):.3f} "
                f"trial-bootstrap-95%={lower:.3f}-{upper:.3f}"
            )

    print("\nHeld-out TTFB operating points (train trial-01; apply to later trials)")
    for provider, vantage in sorted({(cell.provider, cell.vantage) for cell in cells}):
        training = records.get(("trial-01", provider, vantage), [])
        testing = [
            row
            for (trial, candidate_provider, candidate_vantage), rows in records.items()
            if trial != "trial-01"
            and candidate_provider == provider
            and candidate_vantage == vantage
            for row in rows
        ]
        if not training or not testing:
            continue
        value = base.evaluate_threshold(
            training, testing, field="request_ttfb_ms"
        )
        print(
            f"  provider={provider} vantage={vantage} "
            f"threshold_ms={value.threshold_ms:.3f} "
            f"test_bal_acc={value.test_balanced_accuracy:.3f} "
            f"hot={value.hot_correct}/{value.hot_total} "
            f"cold={value.cold_correct}/{value.cold_total}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
