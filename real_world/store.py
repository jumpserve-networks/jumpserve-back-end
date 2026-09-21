"""Supabase job persistence with atomic cancellation and fenced worker leases."""
import base64
import json
import time
import uuid

from config import SCHEMA_VERSION, public_job
import database


def native(value):
    # Also used by the one-time DynamoDB importer.
    return json.loads(json.dumps(value, default=lambda n: int(n) if n == int(n) else float(n)))


def load(job_id):
    try:
        job_id = str(uuid.UUID(job_id))
    except (ValueError, TypeError, AttributeError):
        return None
    rows = database.rest("real_world_jobs", params={"job_id": "eq." + job_id, "select": "record,cancel_requested"})
    return dict(rows[0]["record"], cancel_requested=rows[0]["cancel_requested"]) if rows else None


def create(job):
    return database.rpc("real_world_put_job", {"payload": job, "visible": public_job(job), "create_only": True})


def save(job):
    job["updated_at"] = int(time.time())
    payload = {key: value for key, value in job.items() if key not in ("_lease_token", "lease_until", "cancel_requested")}
    if not database.rpc("real_world_put_job", {"payload": payload, "visible": public_job(job),
                        "create_only": False, "token": job["_lease_token"]}):
        raise RuntimeError("Worker lease expired; refusing stale job update.")


def claim(job_id):
    return database.rpc("real_world_claim_job", {"target": job_id})


def release(job_id, token):
    database.rpc("real_world_release_job", {"target": job_id, "token": token})


def cancel(job_id):
    database.rpc("real_world_cancel_job", {"target": job_id})


def page(cursor=None, owner=None):
    params = {"select": "record", "schema_version": "eq." + str(SCHEMA_VERSION),
              "order": "created_at.desc,job_id.desc", "limit": "51"}
    if owner:
        params["owner"] = "eq." + owner
        params["select"] = "record,cancel_requested"
    if cursor:
        try:
            key = json.loads(base64.urlsafe_b64decode(cursor))
            expected = {"job_id", "created_at", "owner" if owner else "schema_version"}
            if (set(key) != expected or type(key["created_at"]) is not int
                    or (key.get("owner") != owner if owner else key["schema_version"] != SCHEMA_VERSION)):
                raise ValueError()
            job_id = str(uuid.UUID(key["job_id"]))
            created = key["created_at"]
            params["or"] = f"(created_at.lt.{created},and(created_at.eq.{created},job_id.lt.{job_id}))"
        except Exception as error:
            raise ValueError("Invalid pagination cursor.") from error
    rows = database.rest("real_world_jobs" if owner else "real_world_runs", params=params)
    jobs = [dict(row["record"], cancel_requested=row["cancel_requested"]) if owner else row["record"] for row in rows[:50]]
    next_cursor = None
    if len(rows) > 50:
        last = jobs[-1]
        key = {"job_id": last["job_id"], "created_at": last["created_at"]}
        key.update({"owner": owner} if owner else {"schema_version": SCHEMA_VERSION})
        next_cursor = base64.urlsafe_b64encode(json.dumps(key).encode()).decode()
    return {"tests": [public_job(job) for job in jobs], "cursor": next_cursor}


def expired():
    # Keyset pagination remains stable while cleanup changes the active set.
    after = None
    while True:
        rows = database.rpc("real_world_reap_candidates", {"after_id": after})
        for row in rows:
            yield row["job_id"]
        if len(rows) < 50:
            return
        after = rows[-1]["job_id"]
