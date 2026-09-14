#!/usr/bin/env python3
"""Resume an unstarted CloudFront measurement after auditing warmup integrity."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from experiments.cdn_comparison import probe_v2  # noqa: E402
from experiments.cdn_comparison import run_cloudfront_vpn_pilot as pilot  # noqa: E402
from experiments.cdn_comparison import run_vpn_trials as base_runner  # noqa: E402


CALIBRATION_URL = (
    f"{pilot.CLOUDFRONT_TARGET}/objects/calibration/"
    "2f4d7a9c7e034f8aa7d096a292beb386.bin"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}: expected JSON objects")
                rows.append(value)
    return rows


def audit_warmup(path: Path) -> dict[str, Any]:
    rows = load_jsonl(path)
    hot_objects = {str(row["object_id"]) for row in rows}
    objects_with_hit = {
        str(row["object_id"])
        for row in rows
        if row.get("ok") is True and row.get("cloudfront_cache_status") == "HIT"
    }
    successful_pops = {
        str(row["cloudfront_pop"])
        for row in rows
        if row.get("ok") is True and row.get("cloudfront_pop")
    }
    failed = [
        {
            "object_id": row.get("object_id"),
            "sample_index": row.get("sample_index"),
            "curl_exit_code": row.get("curl_exit_code"),
            "curl_stderr": row.get("curl_stderr"),
        }
        for row in rows
        if row.get("ok") is not True
    ]
    missing_hits = sorted(hot_objects - objects_with_hit)
    if missing_hits:
        raise ValueError(f"hot objects without a successful HIT: {missing_hits}")
    if len(successful_pops) != 1:
        raise ValueError(f"warmup did not use exactly one POP: {successful_pops}")
    return {
        "rows": len(rows),
        "successful_rows": len(rows) - len(failed),
        "failed_rows": failed,
        "hot_objects": len(hot_objects),
        "hot_objects_with_successful_hit": len(objects_with_hit),
        "warmup_pop": next(iter(successful_pops)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--location-dir", required=True, type=Path)
    parser.add_argument("--country", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--vantage", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--restore-country", default="us")
    args = parser.parse_args(argv)

    location_dir = args.location_dir.resolve()
    manifest = location_dir / "cloudfront.manifest.csv"
    warmup = location_dir / "cloudfront.warmup.jsonl"
    measurement = location_dir / "cloudfront.measure.jsonl"
    preflight = location_dir / "cloudfront.resume-preflight.jsonl"
    metadata_path = location_dir / "recovery-metadata.json"
    for path in (manifest, warmup):
        if not path.is_file():
            parser.error(f"required file missing: {path}")
    for path in (measurement, preflight, metadata_path):
        if path.exists():
            parser.error(f"refusing to replace existing recovery output: {path}")

    try:
        audit = audit_warmup(warmup)
        initial_status = base_runner.mullvad_status()
        if initial_status.get("state") != "disconnected":
            raise ValueError("Mullvad must be disconnected before recovery")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))

    started_at = utc_now()
    failure: str | None = None
    observed_preflight_pop: str | None = None
    try:
        base_runner.run(
            [
                str(base_runner.MULLVAD),
                "relay",
                "set",
                "location",
                args.country,
                args.city,
                args.hostname,
            ],
            timeout=30,
        )
        base_runner.run([str(base_runner.MULLVAD), "connect", "--wait"], timeout=90)
        time.sleep(2)
        connected_status = base_runner.mullvad_status()
        if connected_status.get("state") != "connected":
            raise RuntimeError("Mullvad did not reach connected state")
        (location_dir / "mullvad-resume-status.json").write_text(
            json.dumps(connected_status, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        preflight_record = probe_v2.enrich_record(
            probe_v2.base_probe.probe_once(
                curl_bin="curl",
                target=probe_v2.base_probe.Target(
                    "calibration", "calibration", CALIBRATION_URL
                ),
                run_id="cloudfront-recovery-preflight",
                vantage=args.vantage,
                phase="recovery-preflight",
                sample_index=1,
                timeout_seconds=30,
                ip_version="4",
                byte_range=None,
            )
        )
        preflight.write_text(
            json.dumps(preflight_record, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        if preflight_record.get("ok") is not True:
            raise RuntimeError("CloudFront recovery preflight failed")
        observed_preflight_pop = str(preflight_record.get("cloudfront_pop") or "")
        if observed_preflight_pop != audit["warmup_pop"]:
            raise RuntimeError(
                f"POP changed from {audit['warmup_pop']} to {observed_preflight_pop}"
            )

        pilot.run_probe(
            manifest=manifest,
            output=measurement,
            vantage=args.vantage,
            phase="measure",
            samples=1,
            delay_ms=50.0,
            seed=args.seed,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        failure = str(exc)
    finally:
        try:
            base_runner.run(
                [str(base_runner.MULLVAD), "disconnect", "--wait"], timeout=90
            )
        except subprocess.SubprocessError as exc:
            failure = f"{failure}; disconnect: {exc}" if failure else str(exc)
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
            failure = f"{failure}; restore relay: {exc}" if failure else str(exc)

    metadata = {
        "protocol": "exploratory-cloudfront-s3-origin-v1",
        "deviation": "one failed third warmup request; main observation had not started",
        "recovery_started_at_utc": started_at,
        "recovery_finished_at_utc": utc_now(),
        "audit": audit,
        "preflight_pop": observed_preflight_pop,
        "same_pop_required": True,
        "new_object_keys_created": False,
        "warmup_repeated": False,
        "measurement_repeated": False,
        "failure": failure,
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if failure:
        print(f"recovery failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
