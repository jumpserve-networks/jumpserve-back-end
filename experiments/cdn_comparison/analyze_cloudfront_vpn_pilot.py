#!/usr/bin/env python3
"""Analyze exploratory CloudFront demand inference within observed POPs."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiments.cdn_comparison import analyze_vpn_trials as base_analysis  # noqa: E402


EXPECTED_BODY_BYTES = 256 * 1024
TIMING_METRICS = base_analysis.TIMING_METRICS


@dataclass(frozen=True)
class RunSummary:
    trial: str
    vantage: str
    pops: tuple[str, ...]
    attempted: int
    eligible: int
    hot_hits: int
    hot_total: int
    cold_misses: int
    cold_total: int
    diagnostic_consistent: int
    diagnostic_compared: int
    hit_layers: tuple[tuple[str, int], ...]
    demand_auc: dict[str, float | None]
    cache_auc: dict[str, float | None]


def load_records(path: Path) -> list[dict[str, Any]]:
    return base_analysis.load_records(path)


def eligible_record(record: dict[str, Any], phase: str) -> bool:
    return (
        record.get("phase") == phase
        and record.get("ok") is True
        and record.get("http_code") == 200
        and record.get("size_download_bytes") == EXPECTED_BODY_BYTES
        and isinstance(record.get("cloudfront_pop"), str)
        and bool(record.get("cloudfront_pop"))
    )


def stratified_auc(records: Iterable[dict[str, Any]], field: str) -> float | None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record["cloudfront_pop"])].append(record)

    weighted_wins = 0.0
    pair_count = 0
    for rows in groups.values():
        hot = base_analysis.numeric_values(
            (row for row in rows if row.get("treatment") == "hot"), field
        )
        cold = base_analysis.numeric_values(
            (row for row in rows if row.get("treatment") == "cold"), field
        )
        auc = base_analysis.lower_is_positive_auc(hot, cold)
        if auc is None:
            continue
        pairs = len(hot) * len(cold)
        weighted_wins += auc * pairs
        pair_count += pairs
    return None if pair_count == 0 else weighted_wins / pair_count


def stratified_cache_auc(
    records: Iterable[dict[str, Any]], field: str
) -> float | None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record["cloudfront_pop"])].append(record)

    weighted_wins = 0.0
    pair_count = 0
    for rows in groups.values():
        hits = base_analysis.numeric_values(
            (
                row
                for row in rows
                if row.get("cloudfront_cache_status") == "HIT"
            ),
            field,
        )
        misses = base_analysis.numeric_values(
            (
                row
                for row in rows
                if row.get("cloudfront_cache_status") == "MISS"
            ),
            field,
        )
        auc = base_analysis.lower_is_positive_auc(hits, misses)
        if auc is None:
            continue
        pairs = len(hits) * len(misses)
        weighted_wins += auc * pairs
        pair_count += pairs
    return None if pair_count == 0 else weighted_wins / pair_count


def summarize(path: Path, phase: str) -> tuple[RunSummary, list[dict[str, Any]]]:
    attempted = [row for row in load_records(path) if row.get("phase") == phase]
    eligible = [row for row in attempted if eligible_record(row, phase)]
    if not eligible:
        raise ValueError(f"{path}: no eligible {phase!r} records")
    vantages = {str(row.get("vantage") or "unknown") for row in eligible}
    if len(vantages) != 1:
        raise ValueError(f"{path}: expected one vantage, found {sorted(vantages)}")
    hot = [row for row in eligible if row.get("treatment") == "hot"]
    cold = [row for row in eligible if row.get("treatment") == "cold"]
    compared = [
        row
        for row in eligible
        if isinstance(row.get("cloudfront_diagnostic_consistent"), bool)
    ]
    layers = Counter(str(row.get("cloudfront_hit_layer") or "NONE") for row in eligible)
    summary = RunSummary(
        trial=path.parents[1].name,
        vantage=next(iter(vantages)),
        pops=tuple(sorted({str(row["cloudfront_pop"]) for row in eligible})),
        attempted=len(attempted),
        eligible=len(eligible),
        hot_hits=sum(row.get("cloudfront_cache_status") == "HIT" for row in hot),
        hot_total=len(hot),
        cold_misses=sum(row.get("cloudfront_cache_status") == "MISS" for row in cold),
        cold_total=len(cold),
        diagnostic_consistent=sum(
            row.get("cloudfront_diagnostic_consistent") is True for row in compared
        ),
        diagnostic_compared=len(compared),
        hit_layers=tuple(sorted(layers.items())),
        demand_auc={field: stratified_auc(eligible, field) for field, _ in TIMING_METRICS},
        cache_auc={
            field: stratified_cache_auc(eligible, field) for field, _ in TIMING_METRICS
        },
    )
    return summary, eligible


def discover_paths(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for value in inputs:
        if value.is_dir():
            paths.extend(sorted(value.glob("trial-*/*/cloudfront.measure.jsonl")))
        else:
            paths.append(value)
    unique = sorted(set(path.resolve() for path in paths))
    if not unique:
        raise ValueError("no CloudFront measurement JSONL files found")
    return unique


def load_recoveries(paths: Iterable[Path]) -> list[tuple[Path, dict[str, Any]]]:
    recoveries: list[tuple[Path, dict[str, Any]]] = []
    for measurement_path in paths:
        metadata_path = measurement_path.parent / "recovery-metadata.json"
        if not metadata_path.is_file():
            continue
        value = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{metadata_path}: expected a JSON object")
        recoveries.append((metadata_path, value))
    return recoveries


def format_auc(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render_report(
    summaries: list[RunSummary],
    records_by_key: dict[tuple[str, str], list[dict[str, Any]]],
    recoveries: list[tuple[Path, dict[str, Any]]],
    *,
    bootstrap_iterations: int,
    seed: int,
) -> str:
    lines = [
        "# Exploratory CloudFront External-VPN Pilot",
        "",
        "CloudFront uses the same Amazon S3 origin in this pilot. The results are "
        "therefore exploratory and are not provider-neutral confirmatory evidence.",
        "The frozen Cloudflare/Fastly confirmatory protocol was not changed.",
        "",
        "## Per-run results",
        "",
        "AUC comparisons are weighted within the observed CloudFront POP. Explicit "
        "CloudFront cache diagnostics are labels only and are not timing features.",
        "",
        "| Trial | Requested vantage | Observed POP | Eligible/attempted | Hot HIT | Cold MISS | RTT AUC | TTFB AUC | Residual AUC | Cache-label TTFB AUC |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        lines.append(
            f"| {summary.trial} | {summary.vantage} | {', '.join(summary.pops)} | "
            f"{summary.eligible}/{summary.attempted} | "
            f"{summary.hot_hits}/{summary.hot_total} | "
            f"{summary.cold_misses}/{summary.cold_total} | "
            f"{format_auc(summary.demand_auc['edge_rtt_estimate_ms'])} | "
            f"{format_auc(summary.demand_auc['request_ttfb_ms'])} | "
            f"{format_auc(summary.demand_auc['fetch_residual_ms'])} | "
            f"{format_auc(summary.cache_auc['request_ttfb_ms'])} |"
        )

    total_hot_hits = sum(run.hot_hits for run in summaries)
    total_hot = sum(run.hot_total for run in summaries)
    total_cold_misses = sum(run.cold_misses for run in summaries)
    total_cold = sum(run.cold_total for run in summaries)
    total_eligible = sum(run.eligible for run in summaries)
    total_attempted = sum(run.attempted for run in summaries)
    total_consistent = sum(run.diagnostic_consistent for run in summaries)
    total_compared = sum(run.diagnostic_compared for run in summaries)
    layers = Counter()
    for run in summaries:
        layers.update(dict(run.hit_layers))
    lines.extend(
        [
            "",
            "## Totals",
            "",
            f"- Eligible responses: {total_eligible}/{total_attempted}",
            f"- Assigned hot objects diagnosed as HIT: {total_hot_hits}/{total_hot}",
            f"- Untouched cold objects diagnosed as MISS: {total_cold_misses}/{total_cold}",
            f"- `X-Cache`/`Server-Timing` diagnostic agreement: {total_consistent}/{total_compared}",
            "- Reported hit layers: "
            + ", ".join(f"{key}={value}" for key, value in sorted(layers.items())),
            "",
            "## Across-run estimates",
            "",
        ]
    )

    grouped: dict[str, list[RunSummary]] = defaultdict(list)
    for summary in summaries:
        grouped[summary.vantage].append(summary)
    for group_index, (vantage, runs) in enumerate(sorted(grouped.items())):
        lines.append(f"- `{vantage}` ({len(runs)} trials):")
        for metric_index, (field, label) in enumerate(TIMING_METRICS):
            values = [
                value
                for run in runs
                if (value := run.demand_auc[field]) is not None
            ]
            if not values:
                continue
            lower, upper = base_analysis.trial_bootstrap_interval(
                values,
                iterations=bootstrap_iterations,
                seed=seed + group_index * 100 + metric_index,
            )
            lines.append(
                f"  - {label}: mean {statistics.fmean(values):.3f}, range "
                f"{min(values):.3f}-{max(values):.3f}, trial-bootstrap 95% "
                f"{lower:.3f}-{upper:.3f}."
            )

    lines.extend(
        [
            "",
            "## Held-out operating point",
            "",
            "Thresholds below were selected on trial 1 and applied unchanged to "
            "trials 2-3 within the same requested vantage.",
            "",
            "| Vantage | Threshold (ms) | Training balanced accuracy | Test accuracy | Test balanced accuracy | Hot correct | Cold correct |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for vantage in sorted(grouped):
        training = records_by_key.get(("trial-01", vantage), [])
        testing = [
            row
            for (trial, candidate_vantage), records in records_by_key.items()
            if trial != "trial-01" and candidate_vantage == vantage
            for row in records
        ]
        if not training or not testing:
            continue
        result = base_analysis.evaluate_threshold(
            training, testing, field="request_ttfb_ms"
        )
        lines.append(
            f"| {vantage} | {result.threshold_ms:.3f} | "
            f"{result.train_balanced_accuracy:.3f} | {result.test_accuracy:.3f} | "
            f"{result.test_balanced_accuracy:.3f} | "
            f"{result.hot_correct}/{result.hot_total} | "
            f"{result.cold_correct}/{result.cold_total} |"
        )

    if recoveries:
        lines.extend(["", "## Collection deviations", ""])
        for metadata_path, recovery in recoveries:
            audit = recovery.get("audit")
            if not isinstance(audit, dict):
                raise ValueError(f"{metadata_path}: missing recovery audit")
            failed_rows = audit.get("failed_rows")
            if not isinstance(failed_rows, list):
                raise ValueError(f"{metadata_path}: invalid failed-row audit")
            location = f"{metadata_path.parent.parent.name}/{metadata_path.parent.name}"
            lines.append(
                f"- `{location}` had {len(failed_rows)} failed warmup transport "
                f"request after {audit.get('successful_rows')}/{audit.get('rows')} "
                "successful warmup requests. The main observation had not started; "
                f"all {audit.get('hot_objects_with_successful_hit')}/"
                f"{audit.get('hot_objects')} hot objects already had a successful "
                f"HIT at `{audit.get('warmup_pop')}`. Recovery created no new keys, "
                "did not repeat warmup or measurement, and required a same-POP "
                f"preflight at `{recovery.get('preflight_pop')}`. Recovery failure: "
                f"`{recovery.get('failure') or 'none'}`."
            )

    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- The S3 origin and CloudFront are operated by the same provider, so "
            "cold-miss timing is not directly comparable with the provider-neutral "
            "Cloudflare/Fastly study.",
            "- `X-Cache`, `Server-Timing`, POP, and hit-layer fields are diagnostic "
            "metadata. Only curl timing fields enter the timing AUC and threshold results.",
            "- Requested VPN locations are not assumed to be serving POPs; analysis "
            "uses the observed CloudFront POP for every AUC comparison.",
            "- This exploratory batch was not preregistered and must not be merged "
            "into the frozen confirmatory-v1 success criterion.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--phase", default="measure")
    parser.add_argument("--bootstrap-iterations", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.bootstrap_iterations < 1:
        parser.error("--bootstrap-iterations must be positive")

    try:
        paths = discover_paths(args.paths)
        recoveries = load_recoveries(paths)
        summarized = [summarize(path, args.phase) for path in paths]
        summaries = [summary for summary, _records in summarized]
        records_by_key = {
            (summary.trial, summary.vantage): records
            for summary, records in summarized
        }
        report = render_report(
            summaries,
            records_by_key,
            recoveries,
            bootstrap_iterations=args.bootstrap_iterations,
            seed=args.seed,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(report)
    if args.report is not None:
        args.report.write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
