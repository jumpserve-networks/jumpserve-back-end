#!/usr/bin/env python3
"""Run fresh-key CloudFront hot/cold trials through pinned Mullvad relays."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiments.cdn_comparison import run_vpn_trials as base_runner  # noqa: E402


CLOUDFRONT_TARGET = "https://cloudfront.jumpserve.dev"
PROBE_V2 = Path(__file__).resolve().parent / "probe_v2.py"
DEFAULT_LOCATIONS = (
    base_runner.Location("us-los-angeles", "us", "lax", "us-lax-wg-006"),
    base_runner.Location("de-frankfurt", "de", "fra", "de-fra-wg-202"),
    base_runner.Location("sg-singapore", "sg", "sin", "sg-sin-wg-102"),
)


def write_manifest(path: Path, objects: list[base_runner.TrialObject]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("object_id", "treatment", "url"))
        for obj in objects:
            writer.writerow(
                (
                    obj.object_id,
                    obj.treatment,
                    f"{CLOUDFRONT_TARGET}/objects/{obj.relative_key}",
                )
            )


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
        str(PROBE_V2),
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
    base_runner.run(command, timeout=max(180, samples * 80 * 35))


def parse_locations(values: list[str]) -> tuple[base_runner.Location, ...]:
    return base_runner.parse_locations(values) if values else DEFAULT_LOCATIONS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare fresh S3 keys and run exploratory CloudFront hot/cold "
            "trials through pinned Mullvad relays. This is not part of the "
            "frozen Cloudflare/Fastly confirmatory protocol."
        )
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--objects-per-class", type=int, default=20)
    parser.add_argument("--warm-samples", type=int, default=3)
    parser.add_argument("--delay-ms", type=float, default=50.0)
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--s3-copy-workers", type=int, default=8)
    parser.add_argument(
        "--location",
        action="append",
        default=[],
        metavar="LABEL:COUNTRY:CITY[:HOSTNAME]",
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
        print("error: invalid positive count, warmup, delay, or worker value", file=sys.stderr)
        return 2

    try:
        locations = parse_locations(args.location)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    aws = shutil.which("aws")
    if (
        aws is None
        or not base_runner.MULLVAD.exists()
        or not base_runner.CURL.exists()
        or not PROBE_V2.exists()
    ):
        print("error: required AWS CLI, Mullvad, curl, or v2 probe is missing", file=sys.stderr)
        return 2

    try:
        initial_status = base_runner.mullvad_status()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: could not read Mullvad status: {exc}", file=sys.stderr)
        return 2
    if initial_status.get("state") != "disconnected":
        print("error: disconnect Mullvad before starting the pilot", file=sys.stderr)
        return 2

    try:
        source_metadata = base_runner.head_source(aws)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: could not verify the S3 calibration source: {exc}", file=sys.stderr)
        return 1

    batch_slug = f"cloudfront-vpn-{base_runner.utc_slug()}"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    failures: list[str] = []

    designs: dict[tuple[int, str], list[base_runner.TrialObject]] = {}
    for trial_number in range(1, args.trials + 1):
        for location_index, location in enumerate(locations, start=1):
            assignment_seed = args.seed + trial_number * 10_000 + location_index * 100
            objects = base_runner.build_design(
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
                                "sha256": base_runner.EXPECTED_SHA256,
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
            write_manifest(location_dir / "cloudfront.manifest.csv", objects)

    try:
        copied = base_runner.populate_s3(
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
                print(
                    f"\n=== trial-{trial_number:02d} {location.label}: "
                    f"selecting {location.country} {location.city} {location.hostname} ===",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    relay_command = [
                        str(base_runner.MULLVAD),
                        "relay",
                        "set",
                        "location",
                        location.country,
                        location.city,
                    ]
                    if location.hostname is not None:
                        relay_command.append(location.hostname)
                    base_runner.run(relay_command, timeout=30)
                    base_runner.run(
                        [str(base_runner.MULLVAD), "connect", "--wait"], timeout=90
                    )
                    time.sleep(2)
                    connected_status = base_runner.mullvad_status()
                    if connected_status.get("state") != "connected":
                        raise RuntimeError(
                            "Mullvad did not reach connected state: "
                            f"{connected_status.get('state')}"
                        )
                    (location_dir / "mullvad-status.json").write_text(
                        json.dumps(connected_status, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    exit_result = base_runner.run(
                        [
                            str(base_runner.CURL),
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

                    run_probe(
                        manifest=location_dir / "cloudfront.manifest.csv",
                        output=location_dir / "cloudfront.warmup.jsonl",
                        vantage=location.label,
                        phase="warmup",
                        samples=args.warm_samples,
                        delay_ms=args.delay_ms,
                        seed=args.seed + trial_number * 1000 + location_index * 100,
                        treatment="hot",
                    )
                    run_probe(
                        manifest=location_dir / "cloudfront.manifest.csv",
                        output=location_dir / "cloudfront.measure.jsonl",
                        vantage=location.label,
                        phase="measure",
                        samples=1,
                        delay_ms=args.delay_ms,
                        seed=args.seed + trial_number * 10_000 + location_index * 100,
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
                        base_runner.run(
                            [str(base_runner.MULLVAD), "disconnect", "--wait"],
                            timeout=90,
                        )
                    except subprocess.SubprocessError as exc:
                        failure = (
                            f"trial-{trial_number:02d} {location.label} disconnect: {exc}"
                        )
                        failures.append(failure)
                        print(f"disconnect failed: {failure}", file=sys.stderr, flush=True)
                    time.sleep(1)
    finally:
        try:
            base_runner.run(
                [
                    str(base_runner.MULLVAD),
                    "relay",
                    "set",
                    "location",
                    args.restore_country,
                ],
                timeout=30,
            )
        except subprocess.SubprocessError as exc:
            failure = f"restore relay preference: {exc}"
            failures.append(failure)
            print(f"relay preference restore failed: {exc}", file=sys.stderr, flush=True)

    summary = {
        "batch_slug": batch_slug,
        "protocol": "exploratory-cloudfront-s3-origin-v1",
        "confirmatory_v1_unchanged": True,
        "exploratory_same_provider_origin_confound": True,
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
        "target": CLOUDFRONT_TARGET,
        "fresh_s3_keys": len(copied),
        "source_object": {
            "bucket": base_runner.S3_BUCKET,
            "key": base_runner.SOURCE_KEY,
            "content_length": source_metadata.get("ContentLength"),
            "content_type": source_metadata.get("ContentType"),
            "cache_control": source_metadata.get("CacheControl"),
            "etag": source_metadata.get("ETag"),
            "sha256": base_runner.EXPECTED_SHA256,
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
