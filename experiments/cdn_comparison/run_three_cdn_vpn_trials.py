#!/usr/bin/env python3
"""Run fresh-key Cloudflare, Fastly, and CloudFront trials via Mullvad."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiments.cdn_comparison import run_vpn_trials as base


EXPERIMENT_DIR = Path(__file__).resolve().parent
PROBE = EXPERIMENT_DIR / "probe_v2.py"
CURL = Path("/usr/bin/curl")
MULLVAD = Path("/usr/local/bin/mullvad")
ORIGIN_HOST = "origin.jumpserve.dev"
ORIGIN_SSH_TARGET = "root@174.138.88.63"
ORIGIN_ROOT = "/srv/jumpserve/objects"
SOURCE_RELATIVE_KEY = "calibration/2f4d7a9c7e034f8aa7d096a292beb386.bin"
EXPECTED_SIZE = 256 * 1024
EXPECTED_SHA256 = "a0f4fb786d29de3765726d8b0a2641608b533c1eba324157b95ede78feae0580"
EXPECTED_CACHE_CONTROL = "public, max-age=3600, immutable"
EXPECTED_CONTENT_TYPE = "application/octet-stream"
TARGETS = {
    "cloudflare": (
        "https://cf-cache-do-origin-probe."
        "jumpserve-cache-study-20260826.workers.dev"
    ),
    "fastly": "https://fastly-do.jumpserve.dev",
    "cloudfront": "https://cloudfront-do.jumpserve.dev",
}
RELATIVE_KEY_PATTERN = re.compile(
    r"^trials/[a-z0-9-]+/trial-[0-9]{2}/[a-z0-9-]+/obj-[0-9]{3}\.bin$"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_origin_source() -> dict[str, Any]:
    url = f"https://{ORIGIN_HOST}/objects/{SOURCE_RELATIVE_KEY}"
    with tempfile.NamedTemporaryFile(prefix="three-cdn-origin-") as body:
        result = subprocess.run(
            [
                str(CURL),
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "60",
                "--output",
                body.name,
                "--write-out",
                "%{http_code}\n%{content_type}\n%{size_download}",
                "--",
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=65,
        )
        body.flush()
        values = result.stdout.splitlines()
        size = Path(body.name).stat().st_size
        digest = sha256_file(Path(body.name))
    if (
        result.returncode != 0
        or len(values) != 3
        or values[0] != "200"
        or values[1] != EXPECTED_CONTENT_TYPE
        or size != EXPECTED_SIZE
        or digest != EXPECTED_SHA256
    ):
        raise RuntimeError(
            "DigitalOcean origin calibration mismatch: "
            f"curl={result.returncode}, metadata={values}, bytes={size}, sha256={digest}"
        )
    return {
        "url": url,
        "content_length": size,
        "content_type": values[1],
        "cache_control": EXPECTED_CACHE_CONTROL,
        "sha256": digest,
    }


def populate_origin(
    designs: dict[tuple[int, str], list[base.TrialObject]],
    *,
    ssh_bin: str,
) -> list[str]:
    relative_keys = [obj.relative_key for design in designs.values() for obj in design]
    invalid = [key for key in relative_keys if not RELATIVE_KEY_PATTERN.fullmatch(key)]
    if invalid:
        raise ValueError(f"refusing invalid origin key: {invalid[0]!r}")
    if len(relative_keys) != len(set(relative_keys)):
        raise ValueError("origin keys are not unique")

    source_path = f"{ORIGIN_ROOT}/{SOURCE_RELATIVE_KEY}"
    remote_command = (
        "set -eu; "
        f"source_path={source_path}; "
        "while IFS= read -r relative_key; do "
        f'target_path="{ORIGIN_ROOT}/$relative_key"; '
        'test ! -e "$target_path"; '
        'mkdir -p -- "${target_path%/*}"; '
        'cp -- "$source_path" "$target_path"; '
        'chmod 0444 -- "$target_path"; '
        "done"
    )
    result = subprocess.run(
        [ssh_bin, "-o", "BatchMode=yes", ORIGIN_SSH_TARGET, remote_command],
        input="\n".join(relative_keys) + "\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=max(180, len(relative_keys) * 3),
    )
    if result.returncode != 0:
        raise RuntimeError(f"origin population failed: {result.stderr.strip()}")
    return relative_keys


def write_manifest(
    path: Path, provider: str, objects: list[base.TrialObject]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("object_id", "treatment", "url"))
        for obj in objects:
            writer.writerow(
                (
                    obj.object_id,
                    obj.treatment,
                    f"{TARGETS[provider]}/objects/{obj.relative_key}",
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
    base.run(command, timeout=max(180, samples * 120 * 35))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare fresh DigitalOcean-origin keys and run matched three-CDN "
            "hot/cold trials through sequential Mullvad exits."
        )
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--objects-per-class", type=int, default=20)
    parser.add_argument("--warm-samples", type=int, default=3)
    parser.add_argument("--delay-ms", type=float, default=50.0)
    parser.add_argument("--seed", type=int, default=20260829)
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
    ):
        print("error: invalid trial, object, warmup, or delay parameter", file=sys.stderr)
        return 2
    try:
        locations = base.parse_locations(args.location)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ssh_bin = shutil.which("ssh")
    if (
        ssh_bin is None
        or not MULLVAD.exists()
        or not CURL.exists()
        or not PROBE.exists()
    ):
        print("error: required ssh, Mullvad, curl, or probe is missing", file=sys.stderr)
        return 2
    try:
        initial_status = base.mullvad_status()
        if initial_status.get("state") != "disconnected":
            raise RuntimeError("disconnect Mullvad before starting the trials")
        source_metadata = validate_origin_source()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: preflight failed: {exc}", file=sys.stderr)
        return 1

    batch_slug = f"three-cdn-vpn-{base.utc_slug()}"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    failures: list[str] = []
    designs: dict[tuple[int, str], list[base.TrialObject]] = {}
    for trial_number in range(1, args.trials + 1):
        for location_index, location in enumerate(locations, start=1):
            assignment_seed = args.seed + trial_number * 10_000 + location_index * 100
            objects = base.build_design(
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
                                "origin_relative_key": obj.relative_key,
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
                write_manifest(location_dir / f"{provider}.manifest.csv", provider, objects)

    try:
        prepared_keys = populate_origin(designs, ssh_bin=ssh_bin)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: could not prepare fresh origin keys: {exc}", file=sys.stderr)
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
                try:
                    relay = [
                        str(MULLVAD),
                        "relay",
                        "set",
                        "location",
                        location.country,
                        location.city,
                    ]
                    if location.hostname is not None:
                        relay.append(location.hostname)
                    base.run(relay, timeout=30)
                    base.run([str(MULLVAD), "connect", "--wait"], timeout=90)
                    time.sleep(2)
                    status = base.mullvad_status()
                    if status.get("state") != "connected":
                        raise RuntimeError(f"Mullvad state is {status.get('state')!r}")
                    (location_dir / "mullvad-status.json").write_text(
                        json.dumps(status, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    exit_result = base.run(
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
                            seed=args.seed + trial_number * 1000 + location_index * 100 + provider_index,
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
                            seed=args.seed + trial_number * 10_000 + location_index * 100 + provider_index,
                        )
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                    failure = f"trial-{trial_number:02d} {location.label}: {exc}"
                    failures.append(failure)
                    print(f"location failed: {failure}", file=sys.stderr, flush=True)
                finally:
                    try:
                        base.run([str(MULLVAD), "disconnect", "--wait"], timeout=90)
                    except subprocess.SubprocessError as exc:
                        failures.append(
                            f"trial-{trial_number:02d} {location.label} disconnect: {exc}"
                        )
                    time.sleep(1)
    finally:
        try:
            base.run(
                [str(MULLVAD), "relay", "set", "location", args.restore_country],
                timeout=30,
            )
        except subprocess.SubprocessError as exc:
            failures.append(f"restore relay preference: {exc}")

    summary = {
        "batch_slug": batch_slug,
        "trials": args.trials,
        "locations": [location.label for location in locations],
        "objects_per_class": args.objects_per_class,
        "warm_samples": args.warm_samples,
        "delay_ms": args.delay_ms,
        "targets": TARGETS,
        "provider_order_randomized": True,
        "same_assignment_across_providers_within_trial_location": True,
        "fresh_digitalocean_origin_keys": len(prepared_keys),
        "origin": source_metadata,
        "failures": failures,
    }
    (output_dir / "batch-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Results: {output_dir}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
