"""Two bounded workers; durable fenced leases and no automatic experiment retries."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import time
import uuid
import runner
import store
from workflow import canonical, digest, identifier, validate_record

def one(kind, study_id, record_id):
    rows = store.rows(kind, {"study_id": "eq." + study_id, "id": "eq." + record_id, "limit": 1})
    if not rows: raise ValueError("Required frozen " + kind + " record is unavailable.")
    return rows[0]

def execute_job(job):
    tick = time.monotonic(); result = None; error = None; entries = []
    study_id, job_id = identifier(job["study_id"]), identifier(job["id"])
    try:
        owners = store.rows("study_owners", {"study_id": "eq." + study_id, "limit": 1})
        if not owners: raise ValueError("Study owner record is unavailable.")
        actor = owners[0]["owner_id"]
        campaign = one("campaigns", study_id, job["campaign_id"])
        protocol = one("protocols", study_id, campaign["protocol_id"])
        raw = store.artifact_bytes(study_id, job["input_artifact_id"])
        if digest(raw) != job["request"]["input_sha256"]: raise ValueError("Job input hash differs from its immutable definition.")
        limits = protocol["document"]["resource_limits"]
        if limits["max_observations"] > 1000 or limits["wall_seconds"] > 10: raise ValueError("Protocol exceeds bounded worker resources.")
        result = runner.execute(raw, protocol, campaign, job_id)
        result["run"]["provenance"]["queue"] = dict(job_id=job_id, definition_sha256=job["definition_sha256"],
          dependencies=job["dependencies"], exclusive_resources=job["exclusive_resources"],
          worker_implementation_sha256=digest(Path(__file__).read_bytes()), attempt=1, automatic_retries=0)
        output = store.artifact(study_id, actor, canonical(result), str(uuid.uuid5(uuid.UUID(job_id), "queue-output")), "application/json", {"job_id": job_id, "run_id": job_id, "input_sha256": digest(raw), "transformation": "research-queue-v1; " + result["run"]["analysis_version"]})
        entries = [dict(kind="artifacts", record=output), dict(kind="runs", record=result["run"])]
        for kind in ("published_values", "measurements", "summaries"):
            entries.extend(dict(kind=kind, record=r) for r in result[kind])
        entries = [dict(kind=e["kind"], record=validate_record(e["kind"], e["record"])) for e in entries]
    except Exception as exception:
        # Durable failure records avoid leaking storage, SQL or credential details.
        error = "Worker failed during frozen-input retrieval, computation or output persistence (" + type(exception).__name__ + "). Original input and attempt are retained; no automatic retry."
        entries = []; result = None
    usage = dict(worker_wall_seconds=max(0, time.monotonic()-tick), measured_charges_usd=None, model_usage=None)
    # Atomic evidence append + lease completion. A lost response/timeout leaves the
    # original lease/history; expiry never starts another experiment automatically.
    return store.rpc("research_queue_finish", dict(p_job=job_id, p_token=job["lease_token"], p_records=entries,
      p_run=job_id if result is not None else None, p_error=error, p_usage=usage))

def handler(event, context=None):
    tick = time.monotonic(); worker_id = str(uuid.uuid4()); outcomes = []
    # Claim inside each slot so work starts promptly; no unbounded in-memory queue.
    def slot():
        job = store.rpc("research_queue_claim", dict(p_worker=worker_id))
        if not job: return dict(status="idle")
        try:
            finished = execute_job(job)
            return dict(job_id=job["id"], status=finished["status"])
        except Exception as exception:
            return dict(job_id=job["id"], status="completion-unconfirmed", error_type=type(exception).__name__)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(slot) for _ in range(2)]
        for future in futures:
            try: outcomes.append(future.result())
            except Exception as exception: outcomes.append(dict(status="claim-unconfirmed", error_type=type(exception).__name__))
    receipt = dict(version="research-queue-v1", slots=2, outcomes=outcomes, wall_seconds=max(0,time.monotonic()-tick),
      measured_charges_usd=None, model_usage=None)
    print(json.dumps(receipt, allow_nan=False))
    return receipt

if __name__ == "__main__":
    handler({"invocation": "trusted operator single bounded poll"})
