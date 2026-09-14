#!/usr/bin/env python3
"""Analyze frozen-threshold confirmatory CDN days without retuning."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from .analyze_vpn_trials import (
        TIMING_METRICS,
        cache_status_of,
        load_records,
        percentile,
        pop_of,
        provider_from_path,
        stratified_auc,
        stratified_cache_auc,
    )
    from .run_confirmatory_day import (
        CONFIG_PATH,
        LOCK_PATH,
        load_json_object,
        sha256_file,
        validate_config,
        verify_lock,
    )
except ImportError:
    from analyze_vpn_trials import (
        TIMING_METRICS,
        cache_status_of,
        load_records,
        percentile,
        pop_of,
        provider_from_path,
        stratified_auc,
        stratified_cache_auc,
    )
    from run_confirmatory_day import (
        CONFIG_PATH,
        LOCK_PATH,
        load_json_object,
        sha256_file,
        validate_config,
        verify_lock,
    )


@dataclass(frozen=True)
class ThresholdCounts:
    hot_correct: int
    hot_total: int
    cold_correct: int
    cold_total: int

    @property
    def accuracy(self) -> float | None:
        total = self.hot_total + self.cold_total
        return None if total == 0 else (self.hot_correct + self.cold_correct) / total

    @property
    def balanced_accuracy(self) -> float | None:
        if not self.hot_total or not self.cold_total:
            return None
        return (
            self.hot_correct / self.hot_total + self.cold_correct / self.cold_total
        ) / 2.0


@dataclass(frozen=True)
class CellSummary:
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
    header_rule_correct: int
    header_rule_total: int
    demand_auc: dict[str, float | None]
    cache_auc: dict[str, float | None]
    threshold: ThresholdCounts
    cross_provider_threshold: ThresholdCounts


def is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def eligible_record(
    record: dict[str, Any], provider: str, *, metric: str, body_bytes: int
) -> bool:
    return bool(
        record.get("phase") == "measure"
        and record.get("ok")
        and record.get("http_code") == 200
        and record.get("size_download_bytes") == body_bytes
        and is_numeric(record.get(metric))
        and pop_of(record, provider) != "unknown"
    )


def threshold_counts(
    records: Iterable[dict[str, Any]], *, metric: str, threshold_ms: float
) -> ThresholdCounts:
    rows = [record for record in records if is_numeric(record.get(metric))]
    hot = [float(record[metric]) for record in rows if record.get("treatment") == "hot"]
    cold = [float(record[metric]) for record in rows if record.get("treatment") == "cold"]
    return ThresholdCounts(
        hot_correct=sum(value <= threshold_ms for value in hot),
        hot_total=len(hot),
        cold_correct=sum(value > threshold_ms for value in cold),
        cold_total=len(cold),
    )


def summarize_cell(
    path: Path, *, day: int, config: dict[str, Any]
) -> CellSummary:
    provider = provider_from_path(path)
    primary_metric = str(config["analysis"]["primary_metric"])
    body_bytes = int(config["analysis"]["eligible_body_bytes"])
    all_records = [
        record for record in load_records(path) if record.get("phase") == "measure"
    ]
    eligible = [
        record
        for record in all_records
        if eligible_record(
            record, provider, metric=primary_metric, body_bytes=body_bytes
        )
    ]
    if not all_records:
        raise ValueError(f"{path}: no measurement records")
    vantages = {str(record.get("vantage") or "unknown") for record in all_records}
    if len(vantages) != 1:
        raise ValueError(f"{path}: expected one vantage, found {sorted(vantages)}")
    vantage = next(iter(vantages))
    thresholds = config["analysis"]["thresholds_ms"]
    threshold_ms = float(thresholds[provider][vantage])
    other_provider = "fastly" if provider == "cloudflare" else "cloudflare"
    cross_provider_threshold_ms = float(thresholds[other_provider][vantage])
    hot = [record for record in all_records if record.get("treatment") == "hot"]
    cold = [record for record in all_records if record.get("treatment") == "cold"]
    cold_hits = sum(cache_status_of(record, provider) == "HIT" for record in cold)
    return CellSummary(
        day=day,
        provider=provider,
        vantage=vantage,
        pops=tuple(sorted({pop_of(record, provider) for record in eligible})),
        attempted=len(all_records),
        eligible=len(eligible),
        hot_hits=sum(cache_status_of(record, provider) == "HIT" for record in hot),
        hot_total=len(hot),
        cold_misses=sum(cache_status_of(record, provider) == "MISS" for record in cold),
        cold_total=len(cold),
        header_rule_correct=(
            sum(cache_status_of(record, provider) == "HIT" for record in hot)
            + len(cold)
            - cold_hits
        ),
        header_rule_total=len(hot) + len(cold),
        demand_auc={
            field: stratified_auc(eligible, provider, field)
            for field, _label in TIMING_METRICS
        },
        cache_auc={
            field: stratified_cache_auc(eligible, provider, field)
            for field, _label in TIMING_METRICS
        },
        threshold=threshold_counts(
            eligible, metric=primary_metric, threshold_ms=threshold_ms
        ),
        cross_provider_threshold=threshold_counts(
            eligible,
            metric=primary_metric,
            threshold_ms=cross_provider_threshold_ms,
        ),
    )


def load_day(
    path: Path,
    config: dict[str, Any],
    *,
    config_sha256: str,
    lock_sha256: str,
) -> tuple[int, str, list[CellSummary]]:
    metadata = load_json_object(path / "confirmatory-metadata.json")
    if metadata.get("protocol_id") != config["protocol_id"]:
        raise ValueError(f"{path}: protocol_id mismatch")
    day = metadata.get("day_number")
    if not isinstance(day, int):
        raise ValueError(f"{path}: metadata day_number must be an integer")
    utc_date = metadata.get("utc_date")
    if not isinstance(utc_date, str):
        raise ValueError(f"{path}: metadata utc_date must be a string")
    if metadata.get("config_sha256") != config_sha256:
        raise ValueError(f"{path}: config hash does not match the frozen config")
    if metadata.get("lock_sha256") != lock_sha256:
        raise ValueError(f"{path}: lock hash does not match the frozen lock")
    paths = sorted(path.glob("trial-01/*/*.measure.jsonl"))
    if not paths:
        raise ValueError(f"{path}: no measurement files")
    return day, utc_date, [
        summarize_cell(value, day=day, config=config) for value in paths
    ]


def day_cluster_interval(
    cells: Iterable[CellSummary],
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
    estimates: list[float] = []
    for _ in range(iterations):
        sampled_values = [
            value
            for _sample in days
            for value in values_by_day[rng.choice(days)]
        ]
        estimates.append(statistics.fmean(sampled_values))
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def aggregate_threshold(
    cells: Iterable[CellSummary], provider: str, *, cross_provider: bool = False
) -> ThresholdCounts:
    selected = [
        cell.cross_provider_threshold if cross_provider else cell.threshold
        for cell in cells
        if cell.provider == provider
    ]
    return ThresholdCounts(
        hot_correct=sum(value.hot_correct for value in selected),
        hot_total=sum(value.hot_total for value in selected),
        cold_correct=sum(value.cold_correct for value in selected),
        cold_total=sum(value.cold_total for value in selected),
    )


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze locked confirmatory day directories without retuning."
    )
    parser.add_argument("days", nargs="+", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)

    try:
        config = load_json_object(args.config)
        validate_config(config)
        verify_lock()
        config_sha256 = sha256_file(args.config.resolve())
        lock_sha256 = sha256_file(LOCK_PATH)
        loaded = [
            load_day(
                path.resolve(),
                config,
                config_sha256=config_sha256,
                lock_sha256=lock_sha256,
            )
            for path in args.days
        ]
        day_numbers = [day for day, _utc_date, _cells in loaded]
        if len(set(day_numbers)) != len(day_numbers):
            raise ValueError("confirmatory day numbers must be unique")
        dates_by_day = {day: utc_date for day, utc_date, _cells in loaded}
        if len(set(dates_by_day.values())) != len(dates_by_day):
            raise ValueError("confirmatory days must use distinct UTC dates")
        if [dates_by_day[day] for day in sorted(dates_by_day)] != sorted(
            dates_by_day.values()
        ):
            raise ValueError("confirmatory day numbers and UTC dates are out of order")
        cells = [cell for _day, _utc_date, day_cells in loaded for cell in day_cells]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    primary_metric = str(config["analysis"]["primary_metric"])
    print("Locked confirmatory CDN results (assigned-demand AUC is within POP)")
    print(
        "  day | provider | vantage | POPs | eligible/attempted | "
        "hot HIT | cold MISS | RTT AUC | TTFB AUC | residual AUC | "
        "fixed-threshold balanced accuracy"
    )
    for cell in sorted(cells, key=lambda value: (value.day, value.provider, value.vantage)):
        print(
            f"  {cell.day} | {cell.provider} | {cell.vantage} | "
            f"{','.join(cell.pops) or '-'} | {cell.eligible}/{cell.attempted} | "
            f"{cell.hot_hits}/{cell.hot_total} | "
            f"{cell.cold_misses}/{cell.cold_total} | "
            f"{fmt(cell.demand_auc['edge_rtt_estimate_ms'])} | "
            f"{fmt(cell.demand_auc['request_ttfb_ms'])} | "
            f"{fmt(cell.demand_auc['fetch_residual_ms'])} | "
            f"{fmt(cell.threshold.balanced_accuracy)}"
        )

    planned_days = int(config["collection"]["planned_days"])
    minimum_cells = int(config["analysis"]["minimum_primary_cells_per_provider"])
    collected_days = len(set(day_numbers))
    iterations = int(config["analysis"]["bootstrap_iterations"])
    seed = int(config["analysis"]["bootstrap_seed"])
    print("\nProvider-level primary outcome")
    for provider_index, provider in enumerate(("cloudflare", "fastly")):
        values = [
            cell.demand_auc[primary_metric]
            for cell in cells
            if cell.provider == provider and cell.demand_auc[primary_metric] is not None
        ]
        interval = day_cluster_interval(
            cells,
            provider=provider,
            metric=primary_metric,
            iterations=iterations,
            seed=seed + provider_index,
        )
        threshold = aggregate_threshold(cells, provider)
        complete = collected_days == planned_days
        sufficient_coverage = len(values) >= minimum_cells
        success = bool(
            complete
            and sufficient_coverage
            and interval is not None
            and interval[0] > 0.5
        )
        interval_text = "-" if interval is None else f"{interval[0]:.3f}-{interval[1]:.3f}"
        print(
            f"  provider={provider} days={collected_days}/{planned_days} "
            f"cells={len(values)}/{planned_days * 3} "
            f"mean_TTFB_AUC={fmt(statistics.fmean(values) if values else None)} "
            f"day_cluster_95%={interval_text} "
            f"coverage_ok={sufficient_coverage} confirmatory_success={success}"
        )
        print(
            f"    fixed_threshold accuracy={fmt(threshold.accuracy)} "
            f"balanced_accuracy={fmt(threshold.balanced_accuracy)} "
            f"hot={threshold.hot_correct}/{threshold.hot_total} "
            f"cold={threshold.cold_correct}/{threshold.cold_total}"
        )
        header_correct = sum(
            cell.header_rule_correct for cell in cells if cell.provider == provider
        )
        header_total = sum(
            cell.header_rule_total for cell in cells if cell.provider == provider
        )
        print(
            f"    diagnostic_header_rule accuracy="
            f"{fmt(header_correct / header_total if header_total else None)} "
            f"correct={header_correct}/{header_total}"
        )

    print("\nExploratory frozen-threshold cross-provider transfer")
    for source_provider, target_provider in (
        ("cloudflare", "fastly"),
        ("fastly", "cloudflare"),
    ):
        counts = aggregate_threshold(cells, target_provider, cross_provider=True)
        print(
            f"  train={source_provider} test={target_provider} "
            f"accuracy={fmt(counts.accuracy)} "
            f"balanced_accuracy={fmt(counts.balanced_accuracy)} "
            f"hot={counts.hot_correct}/{counts.hot_total} "
            f"cold={counts.cold_correct}/{counts.cold_total}"
        )
    if collected_days != planned_days:
        print("\nProtocol collection is incomplete; success decisions remain false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
