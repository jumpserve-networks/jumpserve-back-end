#!/usr/bin/env python3
"""Measure CDN cache behavior and HTTP timing from one vantage point."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO
from urllib.parse import urlsplit


SAFE_RESPONSE_HEADERS = (
    "accept-ranges",
    "age",
    "cache-control",
    "cdn-cache-control",
    "cf-cache-status",
    "cf-ray",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
    "server",
    "vary",
    "via",
    "x-cache",
    "x-cache-hits",
    "x-served-by",
    "x-timer",
)

CURL_TIME_FIELDS = (
    "time_namelookup",
    "time_connect",
    "time_appconnect",
    "time_pretransfer",
    "time_starttransfer",
    "time_total",
    "time_redirect",
)


@dataclass(frozen=True)
class Target:
    object_id: str
    treatment: str
    url: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def stable_object_id(url: str) -> str:
    return f"url-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}"


def validate_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Expected an absolute HTTP(S) URL, got {url!r}.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not accepted.")
    return url.strip()


def read_manifest(path: Path) -> list[Target]:
    with path.open("r", encoding="utf-8", newline="") as manifest_file:
        reader = csv.DictReader(manifest_file)
        if reader.fieldnames is None or "url" not in reader.fieldnames:
            raise ValueError("Manifest must have a 'url' column.")
        targets: list[Target] = []
        for line_number, row in enumerate(reader, start=2):
            url = validate_url(row.get("url", ""))
            object_id = (row.get("object_id") or stable_object_id(url)).strip()
            treatment = (row.get("treatment") or "unspecified").strip()
            if not object_id:
                raise ValueError(f"Manifest line {line_number} has an empty object_id.")
            targets.append(Target(object_id, treatment, url))
    return targets


def targets_from_args(manifest: Path | None, urls: Iterable[str]) -> list[Target]:
    targets = read_manifest(manifest) if manifest is not None else []
    for url_number, raw_url in enumerate(urls, start=1):
        url = validate_url(raw_url)
        targets.append(Target(f"cli-{url_number:03d}", "unspecified", url))

    if not targets:
        raise ValueError("Supply --manifest or at least one URL.")

    object_ids: set[str] = set()
    for target in targets:
        if target.object_id in object_ids:
            raise ValueError(f"Duplicate object_id {target.object_id!r}.")
        object_ids.add(target.object_id)
    return targets


def select_treatments(targets: Iterable[Target], treatments: Iterable[str]) -> list[Target]:
    selected_treatments = {treatment.strip() for treatment in treatments if treatment.strip()}
    if not selected_treatments:
        return list(targets)
    selected = [target for target in targets if target.treatment in selected_treatments]
    if not selected:
        rendered = ", ".join(sorted(selected_treatments))
        raise ValueError(f"No manifest rows matched treatment(s): {rendered}.")
    return selected


def parse_header_blocks(raw_headers: str) -> tuple[str | None, dict[str, str]]:
    """Return the final HTTP status line and a small, safe header subset."""
    normalized = raw_headers.replace("\r\n", "\n")
    blocks = [block for block in normalized.split("\n\n") if block.strip()]
    for block in reversed(blocks):
        lines = [line for line in block.splitlines() if line]
        if not lines or not lines[0].upper().startswith("HTTP/"):
            continue
        values: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line or line[:1].isspace():
                continue
            name, value = line.split(":", 1)
            lower_name = name.strip().lower()
            if lower_name in SAFE_RESPONSE_HEADERS:
                values[lower_name] = value.strip()
        return lines[0].strip(), values
    return None, {}


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def milliseconds(value: Any) -> float | None:
    parsed = number(value)
    return None if parsed is None else round(parsed * 1000.0, 3)


def nonnegative_difference(end: float | None, start: float | None) -> float | None:
    if end is None or start is None:
        return None
    return round(max(0.0, end - start), 3)


def parse_age(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def cf_colo(cf_ray: str | None) -> str | None:
    if not cf_ray:
        return None
    match = re.search(r"-([A-Za-z]{3})$", cf_ray)
    return match.group(1).upper() if match else None


def fastly_cache_status(x_cache: str | None) -> str | None:
    """Return the client-facing Fastly cache status from X-Cache."""
    if not x_cache:
        return None
    status = x_cache.split(",", 1)[0].strip().upper()
    return status or None


def fastly_pop(x_served_by: str | None) -> str | None:
    """Extract the client-facing POP code from the first X-Served-By value."""
    if not x_served_by:
        return None
    server = x_served_by.split(",", 1)[0].strip()
    match = re.search(r"-([A-Za-z]{3})$", server)
    return match.group(1).upper() if match else None


def extract_curl_metrics(raw_stdout: str) -> dict[str, Any]:
    text = raw_stdout.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse curl --write-out JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("curl --write-out did not return a JSON object.")
    return parsed


def build_record(
    *,
    target: Target,
    run_id: str,
    vantage: str,
    phase: str,
    sample_index: int,
    started_at: str,
    wall_ms: float,
    curl_result: subprocess.CompletedProcess[str] | None,
    curl_metrics: dict[str, Any],
    status_line: str | None,
    headers: dict[str, str],
    error: str | None,
) -> dict[str, Any]:
    times = {field: milliseconds(curl_metrics.get(field)) for field in CURL_TIME_FIELDS}
    dns_ms = times["time_namelookup"]
    connect_ms = times["time_connect"]
    appconnect_ms = times["time_appconnect"]
    pretransfer_ms = times["time_pretransfer"]
    starttransfer_ms = times["time_starttransfer"]

    edge_rtt_estimate_ms = nonnegative_difference(connect_ms, dns_ms)
    tls_handshake_ms = nonnegative_difference(appconnect_ms, connect_ms)
    request_ttfb_ms = nonnegative_difference(starttransfer_ms, pretransfer_ms)
    fetch_residual_ms = None
    if request_ttfb_ms is not None and edge_rtt_estimate_ms is not None:
        fetch_residual_ms = round(request_ttfb_ms - edge_rtt_estimate_ms, 3)

    cache_status = headers.get("cf-cache-status")
    cache_status = cache_status.upper() if cache_status else None
    fastly_status = fastly_cache_status(headers.get("x-cache"))
    curl_code = None if curl_result is None else curl_result.returncode

    return {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started_at,
        "vantage": vantage,
        "phase": phase,
        "sample_index": sample_index,
        "object_id": target.object_id,
        "treatment": target.treatment,
        "url": target.url,
        "ok": curl_code == 0 and error is None,
        "error": error,
        "curl_exit_code": curl_code,
        "curl_stderr": (
            curl_result.stderr.strip() if curl_result is not None and curl_result.stderr else None
        ),
        "http_status_line": status_line,
        "http_code": int(curl_metrics.get("http_code") or 0),
        "http_version": curl_metrics.get("http_version"),
        "remote_ip": curl_metrics.get("remote_ip"),
        "remote_port": curl_metrics.get("remote_port"),
        "local_ip": curl_metrics.get("local_ip"),
        "local_port": curl_metrics.get("local_port"),
        "num_redirects": int(curl_metrics.get("num_redirects") or 0),
        "size_download_bytes": number(curl_metrics.get("size_download")),
        "speed_download_bytes_per_second": number(curl_metrics.get("speed_download")),
        "wall_ms": round(wall_ms, 3),
        "dns_ms": dns_ms,
        "connect_total_ms": connect_ms,
        "tls_total_ms": appconnect_ms,
        "pretransfer_total_ms": pretransfer_ms,
        "starttransfer_total_ms": starttransfer_ms,
        "total_ms": times["time_total"],
        "redirect_ms": times["time_redirect"],
        "edge_rtt_estimate_ms": edge_rtt_estimate_ms,
        "tls_handshake_ms": tls_handshake_ms,
        "request_ttfb_ms": request_ttfb_ms,
        "fetch_residual_ms": fetch_residual_ms,
        "cf_cache_status": cache_status,
        "age_seconds": parse_age(headers.get("age")),
        "cf_ray": headers.get("cf-ray"),
        "cf_colo": cf_colo(headers.get("cf-ray")),
        "fastly_cache_status": fastly_status,
        "fastly_pop": fastly_pop(headers.get("x-served-by")),
        "fastly_x_cache": headers.get("x-cache"),
        "fastly_x_cache_hits": headers.get("x-cache-hits"),
        "fastly_x_served_by": headers.get("x-served-by"),
        "response_headers": headers,
    }


def probe_once(
    *,
    curl_bin: str,
    target: Target,
    run_id: str,
    vantage: str,
    phase: str,
    sample_index: int,
    timeout_seconds: float,
    ip_version: str,
    byte_range: str | None,
) -> dict[str, Any]:
    started_at = utc_now()
    started_monotonic = time.monotonic()
    curl_result: subprocess.CompletedProcess[str] | None = None
    curl_metrics: dict[str, Any] = {}
    status_line: str | None = None
    headers: dict[str, str] = {}
    error: str | None = None

    with tempfile.NamedTemporaryFile(prefix="cdn-probe-headers-") as header_file:
        command = [
            curl_bin,
            "--silent",
            "--show-error",
            "--request",
            "GET",
            "--connect-timeout",
            str(timeout_seconds),
            "--max-time",
            str(timeout_seconds),
            "--output",
            os.devnull,
            "--dump-header",
            header_file.name,
            "--write-out",
            "%{json}",
            "--user-agent",
            "jumpserve-cdn-cache-probe/2",
        ]
        if ip_version == "4":
            command.append("--ipv4")
        elif ip_version == "6":
            command.append("--ipv6")
        if byte_range is not None:
            command.extend(["--range", byte_range])
        command.extend(["--", target.url])

        try:
            curl_result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds + 5.0,
                env={**os.environ, "LC_ALL": "C"},
            )
            try:
                curl_metrics = extract_curl_metrics(curl_result.stdout)
            except ValueError as exc:
                error = str(exc)
            header_file.seek(0)
            raw_headers = header_file.read().decode("iso-8859-1", errors="replace")
            status_line, headers = parse_header_blocks(raw_headers)
            if curl_result.returncode != 0 and error is None:
                error = f"curl exited with status {curl_result.returncode}"
        except subprocess.TimeoutExpired:
            error = "probe process exceeded its deadline"

    wall_ms = (time.monotonic() - started_monotonic) * 1000.0
    return build_record(
        target=target,
        run_id=run_id,
        vantage=vantage,
        phase=phase,
        sample_index=sample_index,
        started_at=started_at,
        wall_ms=wall_ms,
        curl_result=curl_result,
        curl_metrics=curl_metrics,
        status_line=status_line,
        headers=headers,
        error=error,
    )


def output_stream(path: Path | None) -> tuple[TextIO, bool]:
    if path is None:
        return sys.stdout, False
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8"), True


def parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch cacheable objects with curl and write one JSON Lines record per request. "
            "Use GET, not HEAD, because a cold HEAD can populate a CDN cache."
        )
    )
    parser.add_argument("urls", nargs="*", help="HTTP(S) object URLs to probe")
    parser.add_argument("--manifest", type=Path, help="CSV with url and optional object_id/treatment")
    parser.add_argument("--output", type=Path, help="Append JSON Lines to this path (default: stdout)")
    parser.add_argument("--vantage", default=socket.gethostname(), help="Stable location/host label")
    parser.add_argument("--phase", default="measure", help="Experiment phase label, such as warmup or measure")
    parser.add_argument(
        "--treatment",
        action="append",
        default=[],
        help="Only probe this manifest treatment; repeat to select more than one",
    )
    parser.add_argument("--samples", type=parse_positive_int, default=2, help="Requests per object")
    parser.add_argument(
        "--delay-ms",
        type=parse_nonnegative_float,
        default=250.0,
        help="Delay between requests",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Per-request curl timeout",
    )
    parser.add_argument("--ip-version", choices=("auto", "4", "6"), default="4")
    parser.add_argument(
        "--range",
        dest="byte_range",
        help="Optional byte range such as 0-65535; full GET is the controlled default",
    )
    parser.add_argument("--shuffle", action="store_true", help="Shuffle targets within each sample round")
    parser.add_argument("--seed", type=int, default=20260826, help="Shuffle seed")
    parser.add_argument("--curl-bin", default="curl", help="curl executable")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout_seconds <= 0:
        print("error: --timeout-seconds must be positive", file=sys.stderr)
        return 2
    if shutil.which(args.curl_bin) is None:
        print(f"error: curl executable {args.curl_bin!r} was not found", file=sys.stderr)
        return 2

    try:
        targets = targets_from_args(args.manifest, args.urls)
        targets = select_treatments(targets, args.treatment)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run_id = str(uuid.uuid4())
    rng = random.Random(args.seed)
    stream, should_close = output_stream(args.output)
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
                record = probe_once(
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
                json.dump(record, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                completed += 1
                if not record["ok"]:
                    failures += 1
                observed_cache = record["fastly_cache_status"] or record["cf_cache_status"]
                observed_colo = record["fastly_pop"] or record["cf_colo"]
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
