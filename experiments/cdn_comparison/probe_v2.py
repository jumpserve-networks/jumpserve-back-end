#!/usr/bin/env python3
"""Extend the frozen v1 curl probe with CloudFront diagnostic metadata."""

from __future__ import annotations

import json
import random
import re
import shutil
import sys
import time
import uuid
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiments.cloudflare_cache import probe as base_probe  # noqa: E402


CLOUDFRONT_RESPONSE_HEADERS = (
    "server-timing",
    "x-amz-cf-id",
    "x-amz-cf-pop",
)
base_probe.SAFE_RESPONSE_HEADERS = tuple(
    dict.fromkeys((*base_probe.SAFE_RESPONSE_HEADERS, *CLOUDFRONT_RESPONSE_HEADERS))
)


def cloudfront_x_cache_status(value: str | None) -> str | None:
    """Normalize CloudFront's X-Cache diagnostic into a small status vocabulary."""
    if not value:
        return None
    normalized = value.split(",", 1)[0].strip().lower()
    if normalized.startswith("hit from cloudfront"):
        return "HIT"
    if normalized.startswith("miss from cloudfront"):
        return "MISS"
    if normalized.startswith("refreshhit from cloudfront"):
        return "REFRESH_HIT"
    if normalized.startswith("error from cloudfront"):
        return "ERROR"
    return normalized.upper() or None


def server_timing_value(value: str | None, metric: str) -> str | None:
    """Extract a quoted desc value for one CloudFront Server-Timing metric."""
    if not value:
        return None
    pattern = rf"(?:^|,)\s*{re.escape(metric)};desc=\"([^\"]+)\""
    match = re.search(pattern, value, flags=re.IGNORECASE)
    return match.group(1) if match else None


def server_timing_cache_status(value: str | None) -> str | None:
    if not value:
        return None
    metrics = {
        part.split(";", 1)[0].strip().lower()
        for part in value.split(",")
        if part.strip()
    }
    if "cdn-cache-hit" in metrics:
        return "HIT"
    if "cdn-cache-miss" in metrics:
        return "MISS"
    if "cdn-cache-refresh" in metrics:
        return "REFRESH_HIT"
    return None


def enrich_record(record: dict[str, object]) -> dict[str, object]:
    headers_value = record.get("response_headers")
    headers = headers_value if isinstance(headers_value, dict) else {}
    x_cache = headers.get("x-cache")
    server_timing = headers.get("server-timing")
    x_cache_text = x_cache if isinstance(x_cache, str) else None
    server_timing_text = server_timing if isinstance(server_timing, str) else None
    header_pop = headers.get("x-amz-cf-pop")
    pop = header_pop if isinstance(header_pop, str) and header_pop else None
    if pop is None:
        pop = server_timing_value(server_timing_text, "cdn-pop")

    x_cache_status = cloudfront_x_cache_status(x_cache_text)
    timing_status = server_timing_cache_status(server_timing_text)
    status = x_cache_status or timing_status
    record.update(
        {
            "schema_version": 2,
            "cloudfront_cache_status": status,
            "cloudfront_pop": pop.upper() if pop else None,
            "cloudfront_hit_layer": server_timing_value(
                server_timing_text, "cdn-hit-layer"
            ),
            "cloudfront_x_cache": x_cache_text,
            "cloudfront_x_amz_cf_id": headers.get("x-amz-cf-id"),
            "cloudfront_server_timing": server_timing_text,
            "cloudfront_diagnostic_consistent": (
                None
                if x_cache_status is None or timing_status is None
                else x_cache_status == timing_status
            ),
        }
    )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = base_probe.build_parser()
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        print("error: --timeout-seconds must be positive", file=sys.stderr)
        return 2

    try:
        targets = base_probe.targets_from_args(args.manifest, args.urls)
        targets = base_probe.select_treatments(targets, args.treatment)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if shutil.which(args.curl_bin) is None:
        print(f"error: curl executable {args.curl_bin!r} was not found", file=sys.stderr)
        return 2

    run_id = str(uuid.uuid4())
    rng = random.Random(args.seed)
    stream, should_close = base_probe.output_stream(args.output)
    failures = 0
    request_count = len(targets) * args.samples
    completed = 0

    print(
        f"run_id={run_id} vantage={args.vantage} targets={len(targets)} "
        f"samples={args.samples}",
        file=sys.stderr,
    )
    try:
        for sample_index in range(1, args.samples + 1):
            round_targets = list(targets)
            if args.shuffle:
                rng.shuffle(round_targets)
            for target in round_targets:
                record = base_probe.probe_once(
                    curl_bin=args.curl_bin,
                    target=target,
                    run_id=run_id,
                    vantage=args.vantage,
                    phase=args.phase,
                    sample_index=sample_index,
                    timeout_seconds=args.timeout_seconds,
                    ip_version=args.ip_version,
                    byte_range=args.byte_range,
                )
                enrich_record(record)
                json.dump(record, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                completed += 1
                if not record["ok"]:
                    failures += 1
                observed_cache = (
                    record["cloudfront_cache_status"]
                    or record["fastly_cache_status"]
                    or record["cf_cache_status"]
                )
                observed_colo = (
                    record["cloudfront_pop"]
                    or record["fastly_pop"]
                    or record["cf_colo"]
                )
                print(
                    f"[{completed}/{request_count}] {target.object_id} "
                    f"status={record['http_code']} cache={observed_cache} "
                    f"colo={observed_colo} ttfb_ms={record['request_ttfb_ms']}",
                    file=sys.stderr,
                )
                if completed < request_count and args.delay_ms:
                    time.sleep(args.delay_ms / 1000.0)
    finally:
        if should_close:
            stream.close()

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
