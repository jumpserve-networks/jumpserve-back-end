#!/usr/bin/env python3
"""Analyze locked three-CDN confirmatory days without retuning."""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiments.cdn_comparison import analyze_three_cdn_vpn_trials as trial  # noqa: E402
from experiments.cdn_comparison import analyze_vpn_trials as base  # noqa: E402
from experiments.cdn_comparison import run_confirmatory_day as common  # noqa: E402
from experiments.cdn_comparison import run_three_cdn_confirmatory_day as protocol  # noqa: E402


@dataclass(frozen=True)
class Cell:
    day: int
    provider: str
    vantage: str
    pops: tuple[str, ...]
    attempted: int
    eligible: int
    hot_hits: int
    hot_total: int
    cold_misses: int
    cold_total: int
    demand_auc: dict[str, float | None]
    cache_auc: dict[str, float | None]
    threshold_hot_correct: int
    threshold_hot_total: int
    threshold_cold_correct: int
    threshold_cold_total: int


def cache_stratified_auc(
    records: list[dict[str, Any]], provider: str, field: str
) -> float | None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[trial.pop_of(record, provider)].append(record)
    wins = 0.0
    pairs = 0
    for rows in groups.values():
        hits = base.numeric_values(
            (row for row in rows if trial.status_of(row, provider) == "HIT"), field
        )
        misses = base.numeric_values(
            (row for row in rows if trial.status_of(row, provider) == "MISS"), field
        )
        value = base.lower_is_positive_auc(hits, misses)
        if value is not None:
            stratum_pairs = len(hits) * len(misses)
            wins += value * stratum_pairs
            pairs += stratum_pairs
    return None if pairs == 0 else wins / pairs


def summarize(path: Path, *, day: int, config: dict[str, Any]) -> Cell:
    provider = trial.provider_from_path(path)
    attempted = [
        row for row in base.load_records(path) if row.get("phase") == "measure"
    ]
    if not attempted:
        raise ValueError(f"{path}: no measurement records")
    vantages = {str(row.get("vantage") or "unknown") for row in attempted}
    if len(vantages) != 1:
        raise ValueError(f"{path}: expected exactly one vantage")
    vantage = next(iter(vantages))
    eligible = [row for row in attempted if trial.eligible(row, provider)]
    threshold = float(config["analysis"]["thresholds_ms"][provider][vantage])
    hot_eligible = [
        row
        for row in eligible
        if row.get("treatment") == "hot"
        and isinstance(row.get("request_ttfb_ms"), (int, float))
    ]
    cold_eligible = [
        row
        for row in eligible
        if row.get("treatment") == "cold"
        and isinstance(row.get("request_ttfb_ms"), (int, float))
    ]
    hot = [row for row in attempted if row.get("treatment") == "hot"]
    cold = [row for row in attempted if row.get("treatment") == "cold"]
    return Cell(
        day=day,
        provider=provider,
        vantage=vantage,
        pops=tuple(sorted({trial.pop_of(row, provider) for row in eligible})),
        attempted=len(attempted),
        eligible=len(eligible),
        hot_hits=sum(trial.status_of(row, provider) == "HIT" for row in hot),
        hot_total=len(hot),
        cold_misses=sum(trial.status_of(row, provider) == "MISS" for row in cold),
        cold_total=len(cold),
        demand_auc={
            field: trial.stratified_auc(eligible, provider, field)
            for field, _label in base.TIMING_METRICS
        },
        cache_auc={
            field: cache_stratified_auc(eligible, provider, field)
            for field, _label in base.TIMING_METRICS
        },
        threshold_hot_correct=sum(
            float(row["request_ttfb_ms"]) <= threshold for row in hot_eligible
        ),
        threshold_hot_total=len(hot_eligible),
        threshold_cold_correct=sum(
            float(row["request_ttfb_ms"]) > threshold for row in cold_eligible
        ),
        threshold_cold_total=len(cold_eligible),
    )


def load_day(
    path: Path,
    config: dict[str, Any],
    *,
    config_hash: str,
    lock_hash: str,
) -> tuple[int, str, list[Cell]]:
    metadata = common.load_json_object(path / "confirmatory-metadata.json")
    if metadata.get("protocol_id") != config["protocol_id"]:
        raise ValueError(f"{path}: protocol_id mismatch")
    if metadata.get("config_sha256") != config_hash:
        raise ValueError(f"{path}: config hash mismatch")
    if metadata.get("lock_sha256") != lock_hash:
        raise ValueError(f"{path}: lock hash mismatch")
    day = metadata.get("day_number")
    utc_date = metadata.get("utc_date")
    if not isinstance(day, int) or not isinstance(utc_date, str):
        raise ValueError(f"{path}: invalid day metadata")
    paths = sorted(path.glob("trial-01/*/*.measure.jsonl"))
    if len(paths) != 9:
        raise ValueError(f"{path}: expected nine provider/location files")
    return day, utc_date, [summarize(value, day=day, config=config) for value in paths]


def day_cluster_interval(
    cells: list[Cell],
    *,
    provider: str,
    metric: str,
    iterations: int,
    seed: int,
) -> tuple[float, float] | None:
    values_by_day: dict[int, list[float]] = defaultdict(list)
    for cell in cells:
        value = cell.demand_auc[metric]
        if cell.provider == provider and value is not None:
            values_by_day[cell.day].append(value)
    days = sorted(values_by_day)
    if not days:
        return None
    rng = random.Random(seed)
    estimates = [
        statistics.fmean(
            value
            for _sample in days
            for value in values_by_day[rng.choice(days)]
        )
        for _ in range(iterations)
    ]
    return base.percentile(estimates, 0.025), base.percentile(estimates, 0.975)


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("days", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        config = common.load_json_object(protocol.CONFIG_PATH)
        protocol.validate_config(config)
        protocol.verify_lock()
        config_hash = common.sha256_file(protocol.CONFIG_PATH)
        lock_hash = common.sha256_file(protocol.LOCK_PATH)
        loaded = [
            load_day(
                path.resolve(),
                config,
                config_hash=config_hash,
                lock_hash=lock_hash,
            )
            for path in args.days
        ]
        day_numbers = [day for day, _date, _cells in loaded]
        dates = [date for _day, date, _cells in sorted(loaded)]
        if len(day_numbers) != len(set(day_numbers)) or len(dates) != len(set(dates)):
            raise ValueError("day numbers and UTC dates must be unique")
        if dates != sorted(dates):
            raise ValueError("day numbers and UTC dates are out of order")
        cells = [cell for _day, _date, values in loaded for cell in values]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print("Locked three-CDN results (assigned-demand AUC is within observed POP)")
    print(
        "  day | provider | vantage | POPs | eligible/attempted | hot HIT | "
        "cold MISS | RTT AUC | TTFB AUC | residual AUC | cache TTFB AUC"
    )
    for cell in sorted(cells, key=lambda value: (value.day, value.provider, value.vantage)):
        print(
            f"  {cell.day} | {cell.provider} | {cell.vantage} | "
            f"{','.join(cell.pops) or '-'} | {cell.eligible}/{cell.attempted} | "
            f"{cell.hot_hits}/{cell.hot_total} | {cell.cold_misses}/{cell.cold_total} | "
            f"{fmt(cell.demand_auc['edge_rtt_estimate_ms'])} | "
            f"{fmt(cell.demand_auc['request_ttfb_ms'])} | "
            f"{fmt(cell.demand_auc['fetch_residual_ms'])} | "
            f"{fmt(cell.cache_auc['request_ttfb_ms'])}"
        )

    collected_days = len(set(day_numbers))
    planned_days = int(config["collection"]["planned_days"])
    minimum_cells = int(config["analysis"]["minimum_primary_cells_per_provider"])
    metric = str(config["analysis"]["primary_metric"])
    print("\nProvider-level primary outcome")
    for provider_index, provider in enumerate(protocol.PROVIDERS):
        selected = [cell for cell in cells if cell.provider == provider]
        aucs = [
            value for cell in selected if (value := cell.demand_auc[metric]) is not None
        ]
        interval = day_cluster_interval(
            cells,
            provider=provider,
            metric=metric,
            iterations=int(config["analysis"]["bootstrap_iterations"]),
            seed=int(config["analysis"]["bootstrap_seed"]) + provider_index,
        )
        hot_correct = sum(cell.threshold_hot_correct for cell in selected)
        hot_total = sum(cell.threshold_hot_total for cell in selected)
        cold_correct = sum(cell.threshold_cold_correct for cell in selected)
        cold_total = sum(cell.threshold_cold_total for cell in selected)
        balanced = (
            None
            if not hot_total or not cold_total
            else (hot_correct / hot_total + cold_correct / cold_total) / 2.0
        )
        success = bool(
            collected_days == planned_days
            and len(aucs) >= minimum_cells
            and interval is not None
            and interval[0] > 0.5
        )
        interval_text = "-" if interval is None else f"{interval[0]:.3f}-{interval[1]:.3f}"
        print(
            f"  provider={provider} days={collected_days}/{planned_days} "
            f"cells={len(aucs)}/{planned_days * 3} "
            f"mean_TTFB_AUC={fmt(statistics.fmean(aucs) if aucs else None)} "
            f"day_cluster_95%={interval_text} success={success} "
            f"frozen_threshold_bal_acc={fmt(balanced)} "
            f"hot={hot_correct}/{hot_total} cold={cold_correct}/{cold_total}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
