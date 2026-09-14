#!/usr/bin/env python3
"""Run paired Cloudflare/Fastly fresh-key trials through Mullvad exits."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MULLVAD = Path("/usr/local/bin/mullvad")
CURL = Path("/usr/bin/curl")
PROBE = Path(__file__).parents[1] / "cloudflare_cache" / "probe.py"

AWS_ACCOUNT_ID = "395567831870"
S3_BUCKET = "jumpserve-cdn-origin-395567831870-us-east-1"
SOURCE_KEY = "objects/calibration/2f4d7a9c7e034f8aa7d096a292beb386.bin"
EXPECTED_SIZE = 256 * 1024
EXPECTED_SHA256 = "a0f4fb786d29de3765726d8b0a2641608b533c1eba324157b95ede78feae0580"
EXPECTED_CACHE_CONTROL = "public, max-age=3600, immutable"
EXPECTED_CONTENT_TYPE = "application/octet-stream"

TARGETS = {
    "cloudflare": (
        "https://cf-cache-neutral-origin-probe."
        "jumpserve-cache-study-20260826.workers.dev"
    ),
    "fastly": "https://fastly.jumpserve.dev",
}


@dataclass(frozen=True)
class Location:
    label: str
    country: str
    city: str
    hostname: str | None = None


@dataclass(frozen=True)
class TrialObject:
    object_id: str
    treatment: str
    relative_key: str

    @property
    def s3_key(self) -> str:
        return f"objects/{self.relative_key}"


DEFAULT_LOCATIONS = (
    Location("us-los-angeles", "us", "lax"),
    Location("de-frankfurt", "de", "fra"),
    Location("sg-singapore", "sg", "sin"),
)


def utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower()


def run(
    command: list[str],
    *,
    timeout: float,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(command)}", file=sys.stderr, flush=True)
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture_output,
        timeout=timeout,
    )


def mullvad_status() -> dict[str, Any]:
    result = run([str(MULLVAD), "status", "--json"], timeout=30, capture_output=True)
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Mullvad returned a non-object JSON status")
    return value


def parse_locations(values: list[str]) -> tuple[Location, ...]:
    if not values:
        return DEFAULT_LOCATIONS
    locations: list[Location] = []
    for value in values:
        parts = value.split(":")
        if len(parts) not in {3, 4}:
            raise ValueError(
                f"location {value!r} must use "
                "LABEL:COUNTRY:CITY[:HOSTNAME] format"
            )
        label, country, city = parts[:3]
        hostname = parts[3] if len(parts) == 4 else None
        if (
            not label
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in label)
            or len(country) != 2
            or not country.isalpha()
            or len(city) != 3
            or not city.isalpha()
            or (
                hostname is not None
                and (
                    not hostname
                    or any(
                        character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                        for character in hostname
                    )
                )
            )
        ):
            raise ValueError(f"invalid location {value!r}")
        locations.append(
            Location(
                label,
                country.lower(),
                city.lower(),
                hostname.lower() if hostname is not None else None,
            )
        )
    return tuple(locations)


def build_design(
    *,
    batch_slug: str,
    trial_number: int,
    location: Location,
    objects_per_class: int,
    seed: int,
) -> list[TrialObject]:
    treatments = ["hot"] * objects_per_class + ["cold"] * objects_per_class
    random.Random(seed).shuffle(treatments)
    return [
        TrialObject(
            object_id=f"obj-{index:03d}",
            treatment=treatment,
            relative_key=(
                f"trials/{batch_slug}/trial-{trial_number:02d}/"
                f"{location.label}/obj-{index:03d}.bin"
            ),
        )
        for index, treatment in enumerate(treatments, start=1)
    ]


def write_manifest(path: Path, provider: str, objects: list[TrialObject]) -> None:
    target = TARGETS[provider]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("object_id", "treatment", "url"))
        for obj in objects:
            writer.writerow(
                (
                    obj.object_id,
                    obj.treatment,
                    f"{target}/objects/{obj.relative_key}",
                )
            )


def head_source(aws: str) -> dict[str, Any]:
    result = run(
        [
            aws,
            "s3api",
            "head-object",
            "--bucket",
            S3_BUCKET,
            "--key",
            SOURCE_KEY,
            "--expected-bucket-owner",
            AWS_ACCOUNT_ID,
        ],
        timeout=30,
        capture_output=True,
    )
    value = json.loads(result.stdout)
    expected = {
        "ContentLength": EXPECTED_SIZE,
        "CacheControl": EXPECTED_CACHE_CONTROL,
        "ContentType": EXPECTED_CONTENT_TYPE,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": value.get(key)}
        for key, expected_value in expected.items()
        if value.get(key) != expected_value
    }
    if mismatches:
        raise RuntimeError(f"calibration source metadata mismatch: {mismatches}")
    return value


def copy_object(aws: str, obj: TrialObject) -> dict[str, str]:
    result = subprocess.run(
        [
            aws,
            "s3api",
            "copy-object",
            "--bucket",
            S3_BUCKET,
            "--copy-source",
            f"{S3_BUCKET}/{SOURCE_KEY}",
            "--key",
            obj.s3_key,
            "--metadata-directive",
            "COPY",
            "--expected-bucket-owner",
            AWS_ACCOUNT_ID,
            "--expected-source-bucket-owner",
            AWS_ACCOUNT_ID,
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"S3 copy failed for {obj.s3_key}: {result.stderr.strip()}"
        )
    value = json.loads(result.stdout)
    return {
        "key": obj.s3_key,
        "etag": str(value.get("CopyObjectResult", {}).get("ETag") or ""),
    }


def populate_s3(
    aws: str,
    designs: dict[tuple[int, str], list[TrialObject]],
    *,
    workers: int,
) -> list[dict[str, str]]:
    objects = [obj for design in designs.values() for obj in design]
    print(
        f"Preparing {len(objects)} fresh S3 object keys with {workers} workers",
        file=sys.stderr,
        flush=True,
    )
    copied: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(copy_object, aws, obj): obj for obj in objects}
        for completed, future in enumerate(
            concurrent.futures.as_completed(futures), start=1
        ):
            copied.append(future.result())
            if completed % 20 == 0 or completed == len(objects):
                print(
                    f"  S3 keys ready: {completed}/{len(objects)}",
                    file=sys.stderr,
                    flush=True,
                )
    return copied


def run_probe(
    *,
    manifest: Path,
    output: Path,
    vantage: str,
    phase: str,
    samples: int,
    delay_ms: float,
    seed: int,
    treatment: str | None = None,
) -> None:
    command = [
        sys.executable,
        str(PROBE),
        "--manifest",
        str(manifest),
        "--output",
        str(output),
        "--vantage",
        vantage,
        "--phase",
        phase,
        "--samples",
        str(samples),
        "--delay-ms",
        str(delay_ms),
        "--timeout-seconds",
        "30",
        "--ip-version",
        "4",
        "--seed",
        str(seed),
        "--shuffle",
    ]
    if treatment is not None:
        command.extend(("--treatment", treatment))
    run(command, timeout=max(180, samples * 80 * 35))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare fresh provider-neutral S3 keys, then run paired Cloudflare "
            "and Fastly hot/cold trials through sequential Mullvad exits."
        )
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--objects-per-class", type=int, default=20)
    parser.add_argument("--warm-samples", type=int, default=3)
    parser.add_argument("--delay-ms", type=float, default=50.0)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--s3-copy-workers", type=int, default=8)
    parser.add_argument(
        "--location",
        action="append",
        default=[],
        metavar="LABEL:COUNTRY:CITY[:HOSTNAME]",
        help=(
            "Run only this Mullvad location, optionally pinned to one relay "
            "hostname; repeat for multiple locations (default: Los Angeles, "
            "Frankfurt, and Singapore)"
        ),
    )
    parser.add_argument("--restore-country", default="us")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (
        args.trials < 1
        or args.objects_per_class < 1
        or args.warm_samples < 2
        or args.delay_ms < 0
        or args.s3_copy_workers < 1
    ):
        print(
            "error: trials and objects-per-class must be positive, warm-samples "
            "at least 2, delay-ms nonnegative, and s3-copy-workers positive",
            file=sys.stderr,
        )
        return 2

    try:
        locations = parse_locations(args.location)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    aws = shutil.which("aws")
    if aws is None or not MULLVAD.exists() or not CURL.exists() or not PROBE.exists():
        print("error: required AWS CLI, Mullvad, curl, or probe is missing", file=sys.stderr)
        return 2

    try:
        initial_status = mullvad_status()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: could not read Mullvad status: {exc}", file=sys.stderr)
        return 2
    if initial_status.get("state") != "disconnected":
        print("error: disconnect Mullvad before starting the trials", file=sys.stderr)
        return 2

    try:
        source_metadata = head_source(aws)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: could not verify the S3 calibration source: {exc}", file=sys.stderr)
        return 1

    batch_slug = f"neutral-vpn-{utc_slug()}"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    failures: list[str] = []

    designs: dict[tuple[int, str], list[TrialObject]] = {}
    for trial_number in range(1, args.trials + 1):
        for location_index, location in enumerate(locations, start=1):
            assignment_seed = args.seed + trial_number * 10_000 + location_index * 100
            objects = build_design(
                batch_slug=batch_slug,
                trial_number=trial_number,
                location=location,
                objects_per_class=args.objects_per_class,
                seed=assignment_seed,
            )
            designs[(trial_number, location.label)] = objects
            location_dir = output_dir / f"trial-{trial_number:02d}" / location.label
            location_dir.mkdir(parents=True)
            (location_dir / "design.json").write_text(
                json.dumps(
                    {
                        "assignment_seed": assignment_seed,
                        "objects": [
                            {
                                "object_id": obj.object_id,
                                "treatment": obj.treatment,
                                "s3_key": obj.s3_key,
                                "sha256": EXPECTED_SHA256,
                            }
                            for obj in objects
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            for provider in TARGETS:
                write_manifest(
                    location_dir / f"{provider}.manifest.csv", provider, objects
                )

    try:
        copied = populate_s3(
            aws,
            designs,
            workers=args.s3_copy_workers,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: could not prepare fresh S3 keys: {exc}", file=sys.stderr)
        return 1

    try:
        for trial_number in range(1, args.trials + 1):
            for location_index, location in enumerate(locations, start=1):
                location_dir = output_dir / f"trial-{trial_number:02d}" / location.label
                phase_rng = random.Random(
                    args.seed + trial_number * 1000 + location_index * 10
                )
                warm_order = list(TARGETS)
                measure_order = list(TARGETS)
                phase_rng.shuffle(warm_order)
                phase_rng.shuffle(measure_order)
                (location_dir / "phase-order.json").write_text(
                    json.dumps(
                        {"warm_order": warm_order, "measure_order": measure_order},
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )

                print(
                    f"\n=== trial-{trial_number:02d} {location.label}: "
                    f"selecting {location.country} {location.city}"
                    f"{f' {location.hostname}' if location.hostname else ''} ===",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    relay_command = [
                        str(MULLVAD),
                        "relay",
                        "set",
                        "location",
                        location.country,
                        location.city,
                    ]
                    if location.hostname is not None:
                        relay_command.append(location.hostname)
                    run(relay_command, timeout=30)
                    run([str(MULLVAD), "connect", "--wait"], timeout=90)
                    time.sleep(2)
                    connected_status = mullvad_status()
                    if connected_status.get("state") != "connected":
                        raise RuntimeError(
                            "Mullvad did not reach connected state: "
                            f"{connected_status.get('state')}"
                        )
                    (location_dir / "mullvad-status.json").write_text(
                        json.dumps(connected_status, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    exit_result = run(
                        [
                            str(CURL),
                            "--fail",
                            "--silent",
                            "--show-error",
                            "--ipv4",
                            "https://am.i.mullvad.net/json",
                        ],
                        timeout=30,
                        capture_output=True,
                    )
                    (location_dir / "mullvad-exit.json").write_text(
                        exit_result.stdout, encoding="utf-8"
                    )

                    for provider_index, provider in enumerate(warm_order, start=1):
                        run_probe(
                            manifest=location_dir / f"{provider}.manifest.csv",
                            output=location_dir / f"{provider}.warmup.jsonl",
                            vantage=location.label,
                            phase="warmup",
                            samples=args.warm_samples,
                            delay_ms=args.delay_ms,
                            seed=(
                                args.seed
                                + trial_number * 1000
                                + location_index * 100
                                + provider_index
                            ),
                            treatment="hot",
                        )

                    for provider_index, provider in enumerate(measure_order, start=1):
                        run_probe(
                            manifest=location_dir / f"{provider}.manifest.csv",
                            output=location_dir / f"{provider}.measure.jsonl",
                            vantage=location.label,
                            phase="measure",
                            samples=1,
                            delay_ms=args.delay_ms,
                            seed=(
                                args.seed
                                + trial_number * 10_000
                                + location_index * 100
                                + provider_index
                            ),
                        )
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    subprocess.SubprocessError,
                ) as exc:
                    failure = f"trial-{trial_number:02d} {location.label}: {exc}"
                    failures.append(failure)
                    print(f"location failed: {failure}", file=sys.stderr, flush=True)
                finally:
                    try:
                        run([str(MULLVAD), "disconnect", "--wait"], timeout=90)
                    except subprocess.SubprocessError as exc:
                        failure = (
                            f"trial-{trial_number:02d} {location.label} disconnect: {exc}"
                        )
                        failures.append(failure)
                        print(f"disconnect failed: {failure}", file=sys.stderr, flush=True)
                    time.sleep(1)
    finally:
        try:
            run(
                [str(MULLVAD), "relay", "set", "location", args.restore_country],
                timeout=30,
            )
        except subprocess.SubprocessError as exc:
            failure = f"restore relay preference: {exc}"
            failures.append(failure)
            print(f"relay preference restore failed: {exc}", file=sys.stderr, flush=True)

    summary = {
        "batch_slug": batch_slug,
        "trials": args.trials,
        "locations": [location.label for location in locations],
        "requested_relays": {
            location.label: {
                "country": location.country,
                "city": location.city,
                "hostname": location.hostname,
            }
            for location in locations
        },
        "objects_per_class": args.objects_per_class,
        "warm_samples": args.warm_samples,
        "delay_ms": args.delay_ms,
        "targets": TARGETS,
        "provider_order_randomized": True,
        "same_assignment_across_providers_within_trial_location": True,
        "fresh_s3_keys": len(copied),
        "source_object": {
            "bucket": S3_BUCKET,
            "key": SOURCE_KEY,
            "content_length": source_metadata.get("ContentLength"),
            "content_type": source_metadata.get("ContentType"),
            "cache_control": source_metadata.get("CacheControl"),
            "etag": source_metadata.get("ETag"),
            "sha256": EXPECTED_SHA256,
        },
        "failures": failures,
    }
    (output_dir / "batch-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"\nResults: {output_dir}", file=sys.stderr, flush=True)
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
