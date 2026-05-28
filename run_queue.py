#!/usr/bin/env python3
"""
Run queued shell commands from a YAML file, sequentially.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_DIR = Path(__file__).resolve().parent
QUEUE_DIR = PROJECT_DIR / "queues"
DEFAULT_QUEUE_FILE = "default.yaml"


@dataclass
class Job:
    name: str
    run: str
    cwd: Path
    env: dict[str, str]
    retries: int
    continue_on_error: bool
    timeout_seconds: float | None


def _as_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"'{field_name}' must be a boolean.")


def _as_int(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{field_name}' must be an integer.")
    if value < minimum:
        raise ValueError(f"'{field_name}' must be >= {minimum}.")
    return value


def _as_float_or_none(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"'{field_name}' must be numeric or null.")
    if isinstance(value, (int, float)):
        if value <= 0:
            raise ValueError(f"'{field_name}' must be > 0 when set.")
        return float(value)
    raise ValueError(f"'{field_name}' must be numeric or null.")


def _as_str_dict(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"'{field_name}' must be a map of string keys/values.")
    out: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            raise ValueError(f"'{field_name}' keys must be strings.")
        if not isinstance(raw, (str, int, float, bool)):
            raise ValueError(f"'{field_name}.{key}' must be scalar.")
        out[key] = str(raw)
    return out


def _resolve_cwd(base_dir: Path, raw_cwd: Any) -> Path:
    if raw_cwd is None:
        return base_dir
    if not isinstance(raw_cwd, str) or not raw_cwd.strip():
        raise ValueError("'cwd' must be a non-empty string.")
    candidate = Path(raw_cwd)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def _normalize_arg_key(key: str) -> str:
    text = key.strip()
    if text.startswith("--"):
        text = text[2:]
    elif text.startswith("-"):
        text = text[1:]
    if not text:
        raise ValueError("Argument key cannot be empty.")
    return text.replace("_", "-")


def _format_arg_values(raw: Any, field_name: str) -> tuple[bool, str]:
    if raw is None:
        return False, ""
    if isinstance(raw, bool):
        return (raw, "")
    if isinstance(raw, (str, int, float)):
        return (True, str(raw))
    if isinstance(raw, list):
        if not raw:
            raise ValueError(f"'{field_name}' cannot be an empty list.")
        parts: list[str] = []
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, (str, int, float, bool)):
                raise ValueError(f"'{field_name}[{idx}]' must be scalar.")
            parts.append(str(item))
        return (True, ",".join(parts))
    raise ValueError(f"'{field_name}' must be scalar, list, bool, or null.")


def _as_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' must be a non-empty string.")
    return value.strip()


def _as_map(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"'{field_name}' must be a map.")
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"'{field_name}' keys must be strings.")
    return value


def _build_params_command(
    params: dict[str, Any],
    *,
    python_bin: str,
    script: str,
    use_sudo: bool,
) -> str:
    parts: list[str] = []
    if use_sudo:
        parts.append("sudo")
    parts.extend([python_bin, script])

    for raw_key, raw_value in params.items():
        arg_key = _normalize_arg_key(raw_key)
        include, value = _format_arg_values(raw_value, f"params.{raw_key}")
        if not include:
            continue
        parts.append(f"--{arg_key}")
        if value:
            parts.append(value)
    return shlex.join(parts)


def _default_base_dir(config_path: Path) -> Path:
    config_dir = config_path.parent.resolve()
    if config_dir == QUEUE_DIR.resolve():
        return PROJECT_DIR
    return config_dir


def _config_candidates(config_arg: str | None) -> list[Path]:
    if config_arg is None:
        return [QUEUE_DIR / DEFAULT_QUEUE_FILE]

    raw = Path(config_arg)
    candidates = [raw]

    if raw.suffix == "":
        candidates.append(raw.with_suffix(".yaml"))
        candidates.append(raw.with_suffix(".yml"))

    if not raw.is_absolute() and len(raw.parts) == 1:
        queue_candidate = QUEUE_DIR / raw.name
        candidates.append(queue_candidate)
        if raw.suffix == "":
            candidates.append(queue_candidate.with_suffix(".yaml"))
            candidates.append(queue_candidate.with_suffix(".yml"))

    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        normalized = candidate.resolve(strict=False)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_candidates.append(candidate)
    return unique_candidates


def resolve_config_path(config_arg: str | None) -> tuple[Path, list[Path]]:
    candidates = _config_candidates(config_arg)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve(), candidates
    return candidates[0].resolve(strict=False), candidates


def list_queue_scenarios() -> list[Path]:
    if not QUEUE_DIR.is_dir():
        return []
    return sorted(
        path
        for path in QUEUE_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    )


def matching_queue_scenarios(prefix: str | None) -> list[Path]:
    scenarios = list_queue_scenarios()
    if prefix is None:
        return scenarios
    return [scenario for scenario in scenarios if scenario.stem.startswith(prefix)]


def load_jobs(config_path: Path) -> list[Job]:
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("Config root must be a map.")

    defaults = data.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise ValueError("'defaults' must be a map.")

    jobs_raw = data.get("jobs")
    if not isinstance(jobs_raw, list) or not jobs_raw:
        raise ValueError("'jobs' must be a non-empty list.")

    base_dir = _default_base_dir(config_path)
    default_env = _as_str_dict(defaults.get("env", {}), "defaults.env")
    default_retries = _as_int(defaults.get("retries", 0), "defaults.retries", minimum=0)
    default_continue = _as_bool(defaults.get("continue_on_error", False), "defaults.continue_on_error")
    default_timeout = _as_float_or_none(defaults.get("timeout_seconds", None), "defaults.timeout_seconds")
    default_cwd = _resolve_cwd(base_dir, defaults.get("cwd"))

    jobs: list[Job] = []
    for idx, item in enumerate(jobs_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"jobs[{idx}] must be a map.")

        raw_run = item.get("run")
        raw_params = item.get("params")
        if raw_run is not None and raw_params is not None:
            raise ValueError(f"jobs[{idx}] may set only one of 'run' or 'params'.")
        if raw_run is None and raw_params is None:
            raise ValueError(f"jobs[{idx}] must set either 'run' or 'params'.")

        if raw_run is not None:
            if not isinstance(raw_run, str) or not raw_run.strip():
                raise ValueError(f"jobs[{idx}].run must be a non-empty string.")
            command = raw_run.strip()
        else:
            params = _as_map(raw_params, f"jobs[{idx}].params")
            python_bin = _as_str(item.get("python", "python3"), f"jobs[{idx}].python")
            script = _as_str(item.get("script", "netem_cubic_benchmark.py"), f"jobs[{idx}].script")
            use_sudo = _as_bool(item.get("sudo", False), f"jobs[{idx}].sudo")
            command = _build_params_command(params, python_bin=python_bin, script=script, use_sudo=use_sudo)

        name = item.get("name", f"job-{idx}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"jobs[{idx}].name must be a non-empty string when set.")
        env = default_env.copy()
        env.update(_as_str_dict(item.get("env", {}), f"jobs[{idx}].env"))
        retries = _as_int(item.get("retries", default_retries), f"jobs[{idx}].retries", minimum=0)
        continue_on_error = _as_bool(
            item.get("continue_on_error", default_continue),
            f"jobs[{idx}].continue_on_error",
        )
        timeout_seconds = _as_float_or_none(
            item.get("timeout_seconds", default_timeout),
            f"jobs[{idx}].timeout_seconds",
        )
        cwd = _resolve_cwd(default_cwd, item.get("cwd"))
        jobs.append(
            Job(
                name=name.strip(),
                run=command,
                cwd=cwd,
                env=env,
                retries=retries,
                continue_on_error=continue_on_error,
                timeout_seconds=timeout_seconds,
            )
        )
    return jobs


def run_jobs(jobs: list[Job], dry_run: bool) -> int:
    failures = 0
    for idx, job in enumerate(jobs, start=1):
        print(f"[{idx}/{len(jobs)}] {job.name}")
        print(f"  run: {job.run}")
        print(f"  cwd: {job.cwd}")
        if dry_run:
            continue

        attempts = job.retries + 1
        for attempt in range(1, attempts + 1):
            merged_env = os.environ.copy()
            merged_env.update(job.env)
            try:
                proc = subprocess.run(
                    job.run,
                    shell=True,
                    executable="/bin/bash",
                    cwd=job.cwd,
                    env=merged_env,
                    timeout=job.timeout_seconds,
                )
                return_code = proc.returncode
            except subprocess.TimeoutExpired:
                print(f"  attempt {attempt}/{attempts} timed out")
                return_code = 124

            if return_code == 0:
                print("  status: ok")
                break

            print(f"  attempt {attempt}/{attempts} failed (exit {return_code})")
            if attempt == attempts:
                failures += 1
                if not job.continue_on_error:
                    print(f"Stopped at '{job.name}'.")
                    return return_code
                print("  continuing due to continue_on_error=true")

    if failures:
        print(f"Queue complete with {failures} failure(s).")
        return 1
    print("Queue complete.")
    return 0


def _display_config_path(config_path: Path) -> str:
    try:
        return str(config_path.relative_to(PROJECT_DIR))
    except ValueError:
        return str(config_path)


def run_scenarios(config_paths: list[Path], dry_run: bool) -> int:
    total = len(config_paths)
    for idx, config_path in enumerate(config_paths, start=1):
        if total > 1:
            if idx > 1:
                print()
            print(f"Scenario [{idx}/{total}]: {config_path.stem} ({_display_config_path(config_path)})")
        try:
            jobs = load_jobs(config_path)
        except Exception as exc:
            print(f"Invalid queue config '{config_path}': {exc}", file=sys.stderr)
            return 2
        return_code = run_jobs(jobs, dry_run=dry_run)
        if return_code != 0:
            return return_code
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run shell commands from a YAML queue.")
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="Scenario name or path to queue YAML file (default: queues/default.yaml). Incompatible with --prefix.",
    )
    parser.add_argument("--list", action="store_true", help="List scenario files in queues/ and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print jobs without executing commands.")
    parser.add_argument(
        "--prefix",
        help="Run every queue scenario in queues/ whose filename stem starts with this prefix.",
    )
    args = parser.parse_args()
    if args.prefix is not None and args.config is not None:
        parser.error("config may not be used together with --prefix")
    return args


def main() -> int:
    args = parse_args()
    if args.list:
        scenarios = matching_queue_scenarios(args.prefix)
        if not scenarios:
            if args.prefix is None:
                print(f"No queue scenarios found in {QUEUE_DIR}.")
            else:
                print(f"No queue scenarios found in {QUEUE_DIR} starting with prefix {args.prefix!r}.")
            return 0
        if args.prefix is None:
            print("Available queue scenarios:")
        else:
            print(f"Available queue scenarios starting with prefix {args.prefix!r}:")
        for scenario in scenarios:
            print(f"  - {scenario.stem}: {scenario.relative_to(PROJECT_DIR)}")
        return 0

    if args.prefix is not None:
        scenarios = matching_queue_scenarios(args.prefix)
        if not scenarios:
            print(f"No queue scenarios found in {QUEUE_DIR} starting with prefix {args.prefix!r}.", file=sys.stderr)
            return 2
        return run_scenarios(scenarios, dry_run=args.dry_run)

    config_path, candidates = resolve_config_path(args.config)
    if not config_path.exists():
        print("Config file not found.", file=sys.stderr)
        for candidate in candidates:
            print(f"  checked: {candidate.resolve(strict=False)}", file=sys.stderr)
        return 2
    return run_scenarios([config_path], dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
