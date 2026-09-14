#!/usr/bin/env python3
"""Run controlled Cloudflare cache probes through sequential Mullvad exits."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MULLVAD = Path("/usr/local/bin/mullvad")
CURL = Path("/usr/bin/curl")
PROBE = Path(__file__).with_name("probe.py")
TARGET_ORIGIN = (
    "https://cf-cache-local-probe.jumpserve-cache-study-20260826.workers.dev"
)


@dataclass(frozen=True)
class Location:
    label: str
    country: str
    city: str


DEFAULT_LOCATIONS = (
    Location("us-los-angeles", "us", "lax"),
    Location("de-frankfurt", "de", "fra"),
    Location("sg-singapore", "sg", "sin"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def mullvad_status() -> dict[str, object]:
    result = run([str(MULLVAD), "status", "--json"], timeout=30, capture_output=True)
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Mullvad returned a non-object JSON status")
    return value


def write_manifest(path: Path, run_slug: str, count: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("object_id", "treatment", "url"))
        for treatment in ("hot", "cold"):
            for index in range(1, count + 1):
                object_id = f"{treatment}-{index:03d}"
                object_key = f"{run_slug}-{treatment}-{index:02d}.bin"
                writer.writerow(
                    (object_id, treatment, f"{TARGET_ORIGIN}/object/{object_key}")
                )


def parse_locations(values: list[str]) -> tuple[Location, ...]:
    if not values:
        return DEFAULT_LOCATIONS
    locations: list[Location] = []
    for value in values:
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"location {value!r} must use LABEL:COUNTRY:CITY format"
            )
        label, country, city = parts
        if (
            not label
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in label)
            or len(country) != 2
            or not country.isalpha()
            or len(city) != 3
            or not city.isalpha()
        ):
            raise ValueError(f"invalid location {value!r}")
        locations.append(Location(label, country.lower(), city.lower()))
    return tuple(locations)


def run_probe(
    *,
    manifest: Path,
    output: Path,
    vantage: str,
    phase: str,
    samples: int,
    delay_ms: float,
    treatment: str | None = None,
    shuffle: bool = False,
    seed: int = 20260827,
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
    ]
    if treatment is not None:
        command.extend(("--treatment", treatment))
    if shuffle:
        command.append("--shuffle")
    run(command, timeout=max(120, samples * 40 * 32))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Switch through Mullvad exits and run a fresh randomized Cloudflare "
            "hot/cold cache trial at each exit."
        )
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--objects-per-class", type=int, default=20)
    parser.add_argument("--warm-samples", type=int, default=5)
    parser.add_argument("--delay-ms", type=float, default=50.0)
    parser.add_argument(
        "--location",
        action="append",
        default=[],
        metavar="LABEL:COUNTRY:CITY",
        help=(
            "Run only this Mullvad location; repeat for multiple locations "
            "(default: Los Angeles, Frankfurt, and Singapore)"
        ),
    )
    parser.add_argument(
        "--restore-country",
        default="us",
        help="Relay country preference to restore after the pilot",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.objects_per_class < 1 or args.warm_samples < 2 or args.delay_ms < 0:
        print(
            "error: objects-per-class must be positive, warm-samples at least 2, "
            "and delay-ms nonnegative",
            file=sys.stderr,
        )
        return 2
    try:
        locations = parse_locations(args.location)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not MULLVAD.exists() or not CURL.exists() or not PROBE.exists():
        print("error: required Mullvad, curl, or probe executable is missing", file=sys.stderr)
        return 2

    initial_status = mullvad_status()
    if initial_status.get("state") != "disconnected":
        print("error: disconnect Mullvad before starting the pilot", file=sys.stderr)
        return 2

    batch_id = utc_now()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    failures: list[str] = []

    try:
        for index, location in enumerate(locations, start=1):
            run_slug = f"vpn-{batch_id.lower()}-{location.city}"
            prefix = output_dir / location.label
            manifest = prefix.with_suffix(".manifest.csv")
            status_path = prefix.with_suffix(".mullvad-status.json")
            trace_path = prefix.with_suffix(".cloudflare-trace.txt")
            warm_path = prefix.with_suffix(".warmup.jsonl")
            measure_path = prefix.with_suffix(".measure.jsonl")
            write_manifest(manifest, run_slug, args.objects_per_class)

            print(
                f"\n=== {location.label}: selecting {location.country} {location.city} ===",
                file=sys.stderr,
                flush=True,
            )
            try:
                run(
                    [
                        str(MULLVAD),
                        "relay",
                        "set",
                        "location",
                        location.country,
                        location.city,
                    ],
                    timeout=30,
                )
                run([str(MULLVAD), "connect", "--wait"], timeout=90)
                time.sleep(2)

                connected_status = mullvad_status()
                if connected_status.get("state") != "connected":
                    raise RuntimeError(
                        f"Mullvad did not reach connected state: {connected_status.get('state')}"
                    )
                status_path.write_text(
                    json.dumps(connected_status, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                trace = run(
                    [str(CURL), "--fail", "--silent", "--show-error", "--ipv4", "https://www.cloudflare.com/cdn-cgi/trace"],
                    timeout=30,
                    capture_output=True,
                )
                trace_path.write_text(trace.stdout, encoding="utf-8")

                run_probe(
                    manifest=manifest,
                    output=warm_path,
                    vantage=location.label,
                    phase="warmup",
                    samples=args.warm_samples,
                    delay_ms=args.delay_ms,
                    treatment="hot",
                    seed=20260827 + index,
                )
                run_probe(
                    manifest=manifest,
                    output=measure_path,
                    vantage=location.label,
                    phase="measure",
                    samples=1,
                    delay_ms=args.delay_ms,
                    shuffle=True,
                    seed=20260827 + index,
                )
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                failures.append(f"{location.label}: {exc}")
                print(f"location failed: {exc}", file=sys.stderr, flush=True)
            finally:
                try:
                    run([str(MULLVAD), "disconnect", "--wait"], timeout=90)
                except subprocess.SubprocessError as exc:
                    failures.append(f"{location.label} disconnect: {exc}")
                    print(f"disconnect failed: {exc}", file=sys.stderr, flush=True)
                time.sleep(1)
    finally:
        try:
            run(
                [str(MULLVAD), "relay", "set", "location", args.restore_country],
                timeout=30,
            )
        except subprocess.SubprocessError as exc:
            failures.append(f"restore relay preference: {exc}")
            print(f"relay preference restore failed: {exc}", file=sys.stderr, flush=True)

    summary = {
        "batch_id": batch_id,
        "locations": [location.label for location in locations],
        "objects_per_class": args.objects_per_class,
        "warm_samples": args.warm_samples,
        "failures": failures,
    }
    (output_dir / "pilot-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"\nResults: {output_dir}", file=sys.stderr, flush=True)
    if failures:
        for failure in failures:
            print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
