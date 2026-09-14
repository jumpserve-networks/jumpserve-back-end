#!/usr/bin/env python3
"""Run one locked UTC-day batch of the three-CDN confirmatory protocol."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiments.cdn_comparison import run_confirmatory_day as common  # noqa: E402


EXPERIMENT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = EXPERIMENT_DIR / "three-cdn-confirmatory-config-v1.json"
LOCK_PATH = EXPERIMENT_DIR / "three-cdn-protocol-lock-v1.json"
RUNNER = EXPERIMENT_DIR / "run_three_cdn_vpn_trials.py"
RESULTS_ROOT = EXPERIMENT_DIR / "results"
MULLVAD = Path("/usr/local/bin/mullvad")
PROVIDERS = ("cloudflare", "fastly", "cloudfront")


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("three-CDN config schema_version must be 1")
    if config.get("protocol_id") != "cdn-demand-three-cdn-do-origin-v1":
        raise ValueError("unexpected three-CDN protocol_id")
    collection = config.get("collection")
    analysis = config.get("analysis")
    locations = config.get("locations")
    if not isinstance(collection, dict) or not isinstance(analysis, dict):
        raise ValueError("config requires collection and analysis objects")
    if not isinstance(locations, list) or len(locations) != 3:
        raise ValueError("config requires exactly three locations")
    if collection.get("planned_days") != 5 or collection.get("trials_per_day") != 1:
        raise ValueError("protocol requires five days and one trial per day")
    if collection.get("objects_per_class") != 20 or collection.get("warm_samples") != 3:
        raise ValueError("protocol requires 20 objects per class and three warmups")
    if analysis.get("primary_metric") != "request_ttfb_ms":
        raise ValueError("primary metric must be request_ttfb_ms")
    if analysis.get("eligible_body_bytes") != 262144:
        raise ValueError("eligible body size must be 262144 bytes")
    if analysis.get("minimum_primary_cells_per_provider") != 12:
        raise ValueError("minimum cell count must be 12 per provider")
    if analysis.get("bootstrap_iterations") != 20000:
        raise ValueError("bootstrap iteration count must be 20000")
    labels = {location.get("label") for location in locations if isinstance(location, dict)}
    if len(labels) != 3 or None in labels:
        raise ValueError("location labels must be unique and nonempty")
    for location in locations:
        if not all(
            isinstance(location.get(field), str) and location[field]
            for field in ("label", "country", "city", "hostname")
        ):
            raise ValueError("each location requires label, country, city, hostname")
    thresholds = analysis.get("thresholds_ms")
    if not isinstance(thresholds, dict) or set(thresholds) != set(PROVIDERS):
        raise ValueError("thresholds must contain exactly three providers")
    for provider in PROVIDERS:
        values = thresholds[provider]
        if not isinstance(values, dict) or set(values) != labels:
            raise ValueError(f"{provider} thresholds must match locations")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in values.values()
        ):
            raise ValueError(f"{provider} thresholds must be numeric")
    preflight = config.get("public_target_preflight")
    targets = preflight.get("targets") if isinstance(preflight, dict) else None
    if not isinstance(targets, dict) or set(targets) != set(PROVIDERS):
        raise ValueError("preflight must contain exactly three targets")


def verify_lock() -> dict[str, Any]:
    lock = common.load_json_object(LOCK_PATH)
    if lock.get("protocol_id") != "cdn-demand-three-cdn-do-origin-v1":
        raise ValueError("three-CDN protocol lock has the wrong protocol_id")
    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("three-CDN protocol lock has no files")
    for relative_path, expected_digest in files.items():
        path = REPOSITORY_ROOT / str(relative_path)
        actual = common.sha256_file(path)
        if actual != expected_digest:
            raise ValueError(
                f"protocol lock mismatch for {relative_path}: expected "
                f"{expected_digest}, got {actual}"
            )
    return lock


def runner_command(
    config: dict[str, Any], *, day_number: int, output_dir: Path
) -> list[str]:
    collection = config["collection"]
    command = [
        sys.executable,
        str(RUNNER),
        "--output-dir",
        str(output_dir),
        "--trials",
        "1",
        "--objects-per-class",
        str(collection["objects_per_class"]),
        "--warm-samples",
        str(collection["warm_samples"]),
        "--delay-ms",
        str(collection["delay_ms"]),
        "--seed",
        str(int(collection["seed_base"]) + day_number),
        "--restore-country",
        str(collection["restore_country"]),
    ]
    for location in config["locations"]:
        command.extend(
            (
                "--location",
                ":".join(
                    str(location[field])
                    for field in ("label", "country", "city", "hostname")
                ),
            )
        )
    return command


def validate_relays(config: dict[str, Any]) -> None:
    if not MULLVAD.exists():
        raise ValueError(f"Mullvad CLI not found at {MULLVAD}")
    result = subprocess.run(
        [str(MULLVAD), "relay", "list"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    missing = [
        location["hostname"]
        for location in config["locations"]
        if location["hostname"] not in result.stdout
    ]
    if result.returncode != 0 or missing:
        raise ValueError(f"pinned Mullvad relay validation failed: {missing}")


def validate_targets(config: dict[str, Any]) -> None:
    preflight = config["public_target_preflight"]
    expected_sha = str(preflight["expected_sha256"])
    expected_bytes = int(config["analysis"]["eligible_body_bytes"])
    for provider, target in sorted(preflight["targets"].items()):
        with tempfile.NamedTemporaryFile(prefix=f"three-cdn-{provider}-") as body:
            result = subprocess.run(
                [
                    "/usr/bin/curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    "60",
                    "--output",
                    body.name,
                    "--write-out",
                    "%{http_code}",
                    "--",
                    f"{target}{preflight['object_path']}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=65,
            )
            body.flush()
            size = Path(body.name).stat().st_size
            digest = common.sha256_file(Path(body.name))
        if result.returncode != 0 or result.stdout != "200":
            raise ValueError(f"{provider} target preflight failed")
        if size != expected_bytes or digest != expected_sha:
            raise ValueError(f"{provider} target returned a non-frozen body")


def day_directories(day: int) -> list[Path]:
    return sorted(RESULTS_ROOT.glob(f"three-cdn-confirmatory-day-{day:02d}-*"))


def validate_sequence(config: dict[str, Any], day: int, utc_date: str) -> None:
    planned = int(config["collection"]["planned_days"])
    if day < 1 or day > planned or day_directories(day):
        raise ValueError(f"day {day} is invalid or already exists")
    prior_dates: list[str] = []
    for prior in range(1, day):
        directories = day_directories(prior)
        if len(directories) != 1:
            raise ValueError(f"day {prior} must exist exactly once before day {day}")
        metadata = common.load_json_object(
            directories[0] / "confirmatory-metadata.json"
        )
        prior_dates.append(str(metadata.get("utc_date") or ""))
    if prior_dates and utc_date <= max(prior_dates):
        raise ValueError("confirmatory days must use increasing UTC dates")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        config = common.load_json_object(CONFIG_PATH)
        validate_config(config)
        lock = verify_lock()
        validate_sequence(config, args.day, now.date().isoformat())
        validate_relays(config)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))

    minimum_date = datetime.strptime(
        str(config["collection"]["minimum_collection_date_utc"]), "%Y-%m-%d"
    ).date()
    output_dir = RESULTS_ROOT / (
        f"three-cdn-confirmatory-day-{args.day:02d}-"
        f"{now.strftime('%Y%m%d-%H%M%S')}"
    )
    plan = {
        "protocol_id": config["protocol_id"],
        "day_number": args.day,
        "utc_date": now.date().isoformat(),
        "eligible_to_collect": now.date() >= minimum_date,
        "minimum_collection_date_utc": minimum_date.isoformat(),
        "output_dir": str(output_dir),
        "config_sha256": common.sha256_file(CONFIG_PATH),
        "lock_sha256": common.sha256_file(LOCK_PATH),
        "pinned_relays": [location["hostname"] for location in config["locations"]],
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if now.date() < minimum_date:
        parser.error(f"collection is frozen until {minimum_date.isoformat()} UTC")
    try:
        validate_targets(config)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    started = now.isoformat().replace("+00:00", "Z")
    result = subprocess.run(
        runner_command(config, day_number=args.day, output_dir=output_dir),
        check=False,
    )
    if output_dir.is_dir():
        metadata = {
            **plan,
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "runner_exit_code": result.returncode,
            "verified_lock_files": lock["files"],
        }
        (output_dir / "confirmatory-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
