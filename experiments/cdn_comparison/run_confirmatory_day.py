#!/usr/bin/env python3
"""Run one locked independent-day batch for the confirmatory CDN protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = EXPERIMENT_DIR / "confirmatory-config-v1.json"
LOCK_PATH = EXPERIMENT_DIR / "protocol-lock-v1.json"
BASE_RUNNER = EXPERIMENT_DIR / "run_vpn_trials.py"
RESULTS_ROOT = EXPERIMENT_DIR / "results"
MULLVAD = Path("/usr/local/bin/mullvad")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("confirmatory config schema_version must be 1")
    if config.get("protocol_id") != "cdn-demand-confirmatory-v1":
        raise ValueError("unexpected confirmatory protocol_id")

    collection = config.get("collection")
    analysis = config.get("analysis")
    locations = config.get("locations")
    if not isinstance(collection, dict) or not isinstance(analysis, dict):
        raise ValueError("config requires collection and analysis objects")
    if not isinstance(locations, list) or len(locations) != 3:
        raise ValueError("config requires exactly three locations")
    if collection.get("planned_days") != 5 or collection.get("trials_per_day") != 1:
        raise ValueError("v1 requires five days and one trial per day")
    if collection.get("objects_per_class") != 20 or collection.get("warm_samples") != 3:
        raise ValueError("v1 requires 20 objects per class and three warm samples")
    if analysis.get("primary_metric") != "request_ttfb_ms":
        raise ValueError("v1 primary metric must be request_ttfb_ms")
    if analysis.get("eligible_body_bytes") != 262144:
        raise ValueError("v1 eligible body size must be 262144 bytes")
    if analysis.get("minimum_primary_cells_per_provider") != 12:
        raise ValueError("v1 requires at least 12 primary cells per provider")
    if analysis.get("bootstrap_iterations") != 20000:
        raise ValueError("v1 requires 20000 bootstrap iterations")

    labels: set[str] = set()
    for location in locations:
        if not isinstance(location, dict):
            raise ValueError("each location must be an object")
        label = location.get("label")
        country = location.get("country")
        city = location.get("city")
        hostname = location.get("hostname")
        if not all(
            isinstance(value, str) and value
            for value in (label, country, city, hostname)
        ):
            raise ValueError("each location requires label, country, city, hostname")
        if label in labels:
            raise ValueError(f"duplicate location label {label!r}")
        labels.add(label)

    thresholds = analysis.get("thresholds_ms")
    if not isinstance(thresholds, dict):
        raise ValueError("analysis.thresholds_ms must be an object")
    for provider in ("cloudflare", "fastly"):
        provider_thresholds = thresholds.get(provider)
        if not isinstance(provider_thresholds, dict):
            raise ValueError(f"missing {provider} thresholds")
        if set(provider_thresholds) != labels:
            raise ValueError(f"{provider} thresholds must match location labels")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in provider_thresholds.values()
        ):
            raise ValueError(f"{provider} thresholds must be numeric")

    preflight = config.get("public_target_preflight")
    if not isinstance(preflight, dict):
        raise ValueError("config requires public_target_preflight")
    targets = preflight.get("targets")
    if not isinstance(targets, dict) or set(targets) != {"cloudflare", "fastly"}:
        raise ValueError("preflight targets must contain Cloudflare and Fastly")
    if not all(
        isinstance(value, str) and value.startswith("https://")
        for value in targets.values()
    ):
        raise ValueError("preflight targets must use HTTPS")
    if not isinstance(preflight.get("object_path"), str):
        raise ValueError("preflight object_path must be a string")
    expected_sha256 = preflight.get("expected_sha256")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError("preflight expected_sha256 must be a SHA-256 hex digest")


def verify_lock(lock_path: Path = LOCK_PATH) -> dict[str, Any]:
    lock = load_json_object(lock_path)
    if lock.get("protocol_id") != "cdn-demand-confirmatory-v1":
        raise ValueError("protocol lock has the wrong protocol_id")
    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("protocol lock has no files")
    for relative_path, expected_digest in files.items():
        if not isinstance(relative_path, str) or not isinstance(expected_digest, str):
            raise ValueError("protocol lock file entries must be strings")
        path = REPOSITORY_ROOT / relative_path
        actual_digest = sha256_file(path)
        if actual_digest != expected_digest:
            raise ValueError(
                f"protocol lock mismatch for {relative_path}: "
                f"expected {expected_digest}, got {actual_digest}"
            )
    return lock


def location_argument(location: dict[str, Any]) -> str:
    return ":".join(
        str(location[field]) for field in ("label", "country", "city", "hostname")
    )


def runner_command(
    config: dict[str, Any], *, day_number: int, output_dir: Path
) -> list[str]:
    collection = config["collection"]
    command = [
        sys.executable,
        str(BASE_RUNNER),
        "--output-dir",
        str(output_dir),
        "--trials",
        str(collection["trials_per_day"]),
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
        command.extend(("--location", location_argument(location)))
    return command


def validate_relay_catalog(config: dict[str, Any]) -> None:
    if not MULLVAD.exists():
        raise ValueError(f"Mullvad CLI not found at {MULLVAD}")
    result = subprocess.run(
        [str(MULLVAD), "relay", "list"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise ValueError("could not query the Mullvad relay catalog")
    missing = [
        str(location["hostname"])
        for location in config["locations"]
        if str(location["hostname"]) not in result.stdout
    ]
    if missing:
        raise ValueError(f"pinned Mullvad relay(s) unavailable: {', '.join(missing)}")


def validate_public_targets(config: dict[str, Any]) -> None:
    preflight = config["public_target_preflight"]
    expected_sha256 = str(preflight["expected_sha256"])
    expected_bytes = int(config["analysis"]["eligible_body_bytes"])
    object_path = str(preflight["object_path"])
    for provider, target in sorted(preflight["targets"].items()):
        with tempfile.NamedTemporaryFile(prefix=f"cdn-{provider}-preflight-") as body:
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
                    f"{target}{object_path}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=65,
            )
            body.flush()
            actual_bytes = Path(body.name).stat().st_size
            actual_sha256 = sha256_file(Path(body.name))
        if result.returncode != 0 or result.stdout != "200":
            raise ValueError(f"{provider} public target preflight failed")
        if actual_bytes != expected_bytes or actual_sha256 != expected_sha256:
            raise ValueError(
                f"{provider} calibration body mismatch: "
                f"bytes={actual_bytes}, sha256={actual_sha256}"
            )


def existing_day_directories(day_number: int) -> list[Path]:
    return sorted(RESULTS_ROOT.glob(f"confirmatory-day-{day_number:02d}-*"))


def validate_day_sequence(config: dict[str, Any], day_number: int) -> None:
    planned_days = int(config["collection"]["planned_days"])
    if day_number < 1 or day_number > planned_days:
        raise ValueError(f"day must be between 1 and {planned_days}")
    if existing_day_directories(day_number):
        raise ValueError(f"confirmatory day {day_number} already has an output directory")
    for previous_day in range(1, day_number):
        if len(existing_day_directories(previous_day)) != 1:
            raise ValueError(
                f"confirmatory day {previous_day} must exist exactly once before day {day_number}"
            )


def validate_collection_date(day_number: int, collection_date: str) -> None:
    previous_dates: list[str] = []
    for previous_day in range(1, day_number):
        directories = existing_day_directories(previous_day)
        if len(directories) != 1:
            raise ValueError(f"could not resolve confirmatory day {previous_day}")
        metadata = load_json_object(directories[0] / "confirmatory-metadata.json")
        previous_date = metadata.get("utc_date")
        if not isinstance(previous_date, str):
            raise ValueError(f"confirmatory day {previous_day} has no UTC date")
        previous_dates.append(previous_date)
    if previous_dates and collection_date <= max(previous_dates):
        raise ValueError(
            f"confirmatory day {day_number} must use a UTC date later than "
            f"{max(previous_dates)}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the frozen protocol and run one independent confirmatory day. "
            "Use --dry-run to validate without creating objects or network traffic."
        )
    )
    parser.add_argument("--day", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_json_object(CONFIG_PATH)
        validate_config(config)
        lock = verify_lock()
        validate_day_sequence(config, args.day)
        validate_relay_catalog(config)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))

    now = utc_now()
    minimum_date = datetime.strptime(
        str(config["collection"]["minimum_collection_date_utc"]), "%Y-%m-%d"
    ).date()
    eligible_to_collect = now.date() >= minimum_date
    output_dir = (
        RESULTS_ROOT
        / f"confirmatory-day-{args.day:02d}-{now.strftime('%Y%m%d-%H%M%S')}"
    )
    if output_dir.exists():
        parser.error(f"output directory already exists: {output_dir}")
    command = runner_command(config, day_number=args.day, output_dir=output_dir)

    plan = {
        "protocol_id": config["protocol_id"],
        "day_number": args.day,
        "utc_date": now.date().isoformat(),
        "eligible_to_collect": eligible_to_collect,
        "minimum_collection_date_utc": minimum_date.isoformat(),
        "output_dir": str(output_dir),
        "pinned_relays": [location["hostname"] for location in config["locations"]],
        "objects_per_class": config["collection"]["objects_per_class"],
        "config_sha256": sha256_file(CONFIG_PATH),
        "lock_sha256": sha256_file(LOCK_PATH),
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if not eligible_to_collect:
        parser.error(
            f"collection is frozen until {minimum_date.isoformat()} UTC; "
            "use --dry-run today"
        )
    try:
        validate_collection_date(args.day, now.date().isoformat())
        validate_public_targets(config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    started_at = now.isoformat().replace("+00:00", "Z")
    result = subprocess.run(command, check=False)
    finished_at = utc_now().isoformat().replace("+00:00", "Z")
    if output_dir.is_dir():
        metadata = {
            **plan,
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
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
