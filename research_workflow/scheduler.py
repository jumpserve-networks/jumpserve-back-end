"""Dependency-aware scheduling. Parallel execution does not imply independence."""
import uuid
import os
import store
from workflow import canonical, digest, identifier, text

VERSION = "research-queue-v1"
LIMITS = dict(global_jobs=4, study_jobs=2, invocation_jobs=2, lease_seconds=600,
              input_bytes=256000, observations=1000, algorithm_wall_seconds=10,
              study_queue_jobs=1000, automatic_retries=0)
TERMINAL = ("awaiting-review", "reviewed", "failed", "cancelled", "expired")

def definition(body):
    allowed = {"request_id", "campaign_id", "execution_mode", "priority", "dependencies", "exclusive_resources", "raw_input", "rationale", "followup_of"}
    if not isinstance(body, dict) or set(body) - allowed: raise ValueError("Unsupported queue fields.")
    job_id = identifier(body.get("request_id"))
    mode = body.get("execution_mode")
    if mode not in ("automatic", "manual"): raise ValueError("Choose automatic or manual execution.")
    priority = body.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5: raise ValueError("Priority must be 1 through 5.")
    dependencies = body.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) > 32: raise ValueError("At most 32 explicitly typed dependencies are allowed.")
    seen = set(); validated = []
    for item in dependencies:
        if not isinstance(item, dict) or set(item) != {"job_id", "requirement"} or item["requirement"] not in ("complete-run", "reviewed-evidence"):
            raise ValueError("Each dependency needs job_id and complete-run or reviewed-evidence.")
        predecessor = identifier(item["job_id"])
        if predecessor == job_id or predecessor in seen: raise ValueError("Self or duplicate dependency is invalid.")
        seen.add(predecessor); validated.append(dict(job_id=predecessor, requirement=item["requirement"]))
    resources = body.get("exclusive_resources")
    import re
    if not isinstance(resources, list) or len(resources) > 16 or any(not isinstance(r, str) or not re.fullmatch(r"[a-z0-9][a-z0-9:._/-]{0,99}", r) for r in resources):
        raise ValueError("Exclusive resources need at most 16 stable lowercase identities.")
    if len(set(resources)) != len(resources): raise ValueError("Duplicate exclusive resource identity.")
    raw = body.get("raw_input")
    if mode == "automatic":
        if not isinstance(raw, str) or not raw: raise ValueError("Supply the exact original UTF-8 input text.")
        raw = raw.encode()
        if len(raw) > LIMITS["input_bytes"]: raise ValueError("Queue input exceeds 256 KB.")
    elif raw not in (None, ""):
        raise ValueError("Manual tasks retain originals through the domain importer; do not submit an automatic input.")
    else: raw = None
    request = dict(campaign_id=identifier(body.get("campaign_id")), execution_mode=mode, priority=priority,
      dependencies=validated, exclusive_resources=sorted(resources), rationale=text(body.get("rationale"), "Job rationale", 5000),
      followup_of=identifier(body["followup_of"]) if body.get("followup_of") else None,
      input_artifact_id=str(uuid.uuid5(uuid.UUID(job_id), "queue-input")) if raw is not None else None,
      input_sha256=digest(raw) if raw is not None else None)
    return job_id, request, raw

def enqueue(study_id, actor, body):
    job_id, request, raw = definition(body)
    prior = store.rows("queue_jobs", {"study_id": "eq." + study_id, "id": "eq." + job_id, "limit": 1})
    if prior:
        if prior[0]["request"] != request: raise ValueError("Queue ID already used; create a follow-up rather than changing the original.")
        return dict(job=prior[0], replayed=True)
    if raw is not None:
        campaigns = store.rows("campaigns", {"study_id": "eq." + study_id, "id": "eq." + request["campaign_id"], "limit": 1})
        if not campaigns: raise ValueError("Campaign not found.")
        campaign = campaigns[0]
        protocols = store.rows("protocols", {"study_id": "eq." + study_id, "id": "eq." + campaign["protocol_id"], "limit": 1})
        if not protocols: raise ValueError("A frozen protocol is required.")
        p = protocols[0]["document"]; limits = p["resource_limits"]
        if campaign["adapter"] != "matched-numeric-v1" or campaign["experiment_type"] not in ("reanalysis", "independent-check"):
            raise ValueError("This campaign needs a domain implementation or manual task.")
        if limits["max_observations"] > 1000 or limits["wall_seconds"] > 10 or len(raw) > limits["max_input_bytes"]:
            raise ValueError("Frozen protocol exceeds queue execution limits; preserve it and use a domain operator.")
        if request["input_sha256"] not in [i["sha256"] for i in p["input_versions"]]: raise ValueError("Input hash differs from the frozen protocol.")
        artifact = store.artifact(study_id, actor, raw, request["input_artifact_id"], "application/json", {"job_id": job_id, "transformation": "none; original UTF-8 queue input"})
        store.append(study_id, actor, "artifacts", artifact)
    result = store.rpc("research_queue_enqueue", dict(p_study=study_id, p_actor=actor, p_id=job_id, p_request=request, p_sha256=digest(canonical(request))))
    return dict(job=result, replayed=False)

def blockers(job, indexed):
    reasons = []
    if job["execution_mode"] == "manual": reasons.append("Requires a domain operator or substantive review; automatic execution is inapplicable.")
    for dep in job["dependencies"]:
        prior = indexed.get(dep["job_id"])
        if not prior:
            reasons.append("Required predecessor is unavailable: " + dep["job_id"])
        elif dep["requirement"] == "reviewed-evidence" and prior["status"] != "reviewed":
            reasons.append("Awaiting reviewed evidence: " + dep["job_id"])
        elif dep["requirement"] == "complete-run" and (prior["status"] not in ("awaiting-review", "reviewed") or prior.get("run_status") != "complete"):
            reasons.append("Awaiting a complete recorded run: " + dep["job_id"])
    return reasons

def snapshot(study_id, actor):
    data = store.rpc("research_queue_snapshot", dict(p_study=study_id, p_actor=actor))
    jobs, events = data["jobs"], data["events"]
    if len(jobs) > 1000 or len(events) > 5000: raise store.StoreError("Queue export exceeds the interactive budget; no truncated history is returned.")
    indexed = {j["id"]: j for j in jobs}
    for job in jobs:
        job["blockers"] = blockers(job, indexed)
        job["readiness"] = "blocked" if job["status"] in ("queued", "manual") and job["blockers"] else "ready" if job["status"] == "queued" else job["status"]
        job.pop("lease_token", None)
    for event in events:
        event["details"] = {k: v for k, v in event["details"].items() if k != "actor_id"}
    result = dict(version=VERSION, study_id=study_id, access="owner-private", limits=LIMITS, workers_enabled=os.environ.get("RESEARCH_QUEUE_WORKERS_ENABLED") == "true", jobs=jobs, events=events,
      coverage=dict(jobs=len(jobs), events=len(events)),
      limitations="Readiness means prerequisites are satisfied; global/study slots and exclusive resources are checked atomically at claim time. Execution and review status do not establish scientific validity or independent evidence.")
    if len(canonical(result)) > 4_000_000: raise store.StoreError("Queue export exceeds the byte budget; history was not truncated.")
    return result

def action(study_id, actor, body):
    if set(body) - {"request_id", "job_id", "action", "reason", "assessment_ids", "run_id"}: raise ValueError("Unsupported queue action fields.")
    ids = body.get("assessment_ids", [])
    if not isinstance(ids, list) or len(ids) > 1000: raise ValueError("Assessment IDs must be a bounded array.")
    action = body.get("action")
    if action not in ("cancel", "review", "attach-run"): raise ValueError("Choose cancel, review or attach-run.")
    return store.rpc("research_queue_action", dict(p_study=study_id, p_actor=actor,
      p_job=identifier(body.get("job_id")), p_id=identifier(body.get("request_id")), p_action=action,
      p_reason=text(body.get("reason"), "Action reason", 5000), p_assessments=[identifier(i) for i in ids], p_run=identifier(body["run_id"]) if body.get("run_id") else None))
