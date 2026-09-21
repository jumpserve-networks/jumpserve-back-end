"""Job-scoped ingestion for EC2 benchmarks. No database credential is needed.

Existing metric writers build a report using temporary IDs. The server inserts
the complete report in one transaction and returns the actual database IDs.
"""
import base64
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_COMPRESSED_BYTES = 3 * 1024 * 1024


def ingest_settings():
    values = tuple(os.environ.get(name, "") for name in (
        "JUMPSERVE_INGEST_URL", "JUMPSERVE_JOB_ID", "JUMPSERVE_JOB_TOKEN"))
    if not any(values):
        return None
    url, job_id, token = values
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or
            not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id) or
            not re.fullmatch(r"[0-9a-f]{64}", token)):
        raise ValueError("A complete HTTPS JumpServe ingestion configuration is required")
    return values


def require_persistence(args):
    if ingest_settings() is None and not (
        args.supabase_project_id and args.supabase_service_role_key
    ):
        raise ValueError("Configure JumpServe job ingestion or set SUPABASE_SERVICE_ROLE_KEY in the environment before running a benchmark")


class IngestBuffer:
    def __init__(self):
        self.settings = ingest_settings()
        self.report = {"parent": None, "algorithms": {}, "runs": [], "snapshots": [], "raw_run": None}

    def request(self, method, table, query="", payload=None):
        if method == "GET" and table == "congestion_control_algorithms":
            name = urllib.parse.parse_qs(query).get("name", [""])[0].removeprefix("eq.")
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
                raise ValueError("Invalid congestion control algorithm")
            algorithms = self.report["algorithms"]
            if name not in algorithms:
                algorithms[name] = len(algorithms) + 1
            return [{"id": algorithms[name]}]
        if method != "POST":
            raise ValueError("Job ingestion supports only result inserts")
        rows = payload if isinstance(payload, list) else [payload]
        if table == "emulated_parent_runs" and len(rows) == 1 and self.report["parent"] is None:
            self.report["parent"] = dict(rows[0])
            return [{"id": 1}]
        if table == "emulated_runs":
            result = []
            for row in rows:
                local_id = len(self.report["runs"]) + 1
                self.report["runs"].append({**row, "id": local_id})
                result.append({"id": local_id})
            return result
        if table == "emulated_snapshot_stats":
            self.report["snapshots"].extend(rows)
            return []
        if table == "runs" and len(rows) == 1 and self.report["raw_run"] is None:
            self.report["raw_run"] = dict(rows[0])
            return rows
        raise ValueError("Unsupported job ingestion operation")

    def commit(self):
        url, job_id, token = self.settings
        raw = json.dumps(self.report, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("Benchmark report exceeds the 32 MiB ingestion limit")
        compressed = gzip.compress(raw, mtime=0)
        if len(compressed) > MAX_COMPRESSED_BYTES:
            raise ValueError("Compressed benchmark report exceeds the 3 MiB ingestion limit")
        body = json.dumps({"job_id": job_id, "action": "results",
                           "report_gzip": base64.b64encode(compressed).decode()}).encode()
        for attempt in range(3):
            request = urllib.request.Request(url, data=body, method="POST", headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code < 500 or attempt == 2:
                    raise RuntimeError(f"Benchmark ingestion rejected the report (HTTP {error.code})") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise RuntimeError("Benchmark ingestion could not be reached") from None
            time.sleep(2 ** attempt)
