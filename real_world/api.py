"""Public result reads and authenticated test execution, separate from emulated records."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time
import urllib.error
import urllib.request
import uuid

import cloud
import artifacts
from config import SCHEMA_VERSION, TERMINAL, canonical_json, nodes_for, public_job, validate_config
import store


class HttpError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def authenticate(event):
    headers = {key.lower(): value for key, value in event.get("headers", {}).items()}
    bearer = headers.get("authorization", "")
    if not bearer.startswith("Bearer ") or len(bearer) > 8192:
        raise HttpError(401, "Sign in to use real-world tests.")
    request = urllib.request.Request(os.environ["SUPABASE_URL"] + "/auth/v1/user", headers={
        "Authorization": bearer, "apikey": os.environ["SUPABASE_ANON_KEY"]})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            user = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise HttpError(401, "Your session has expired. Sign in again.") from error
        raise
    if user.get("is_anonymous") or not user.get("id") or not any(identity.get("provider") == "google" for identity in user.get("identities", [])):
        raise HttpError(403, "Google authentication is required.")
    return user["id"]


def owned(job_id, owner):
    job = store.load(job_id)
    if not job or job["owner"] != owner:
        raise HttpError(404, "Test not found.")
    return job


def launch(body, owner):
    config = validate_config(body.get("config"))
    try:
        request_id = str(uuid.UUID(body.get("request_id", "")))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("A valid request ID is required to prevent duplicate launches.") from error
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, owner + ":" + request_id))
    job = store.load(job_id)
    if job:
        if canonical_json(job["config"]) != canonical_json(config):
            raise HttpError(409, "This request ID was already used for a different configuration.")
    else:
        nodes = nodes_for(config)
        unique_regions = sorted({node["region"] for node in nodes})
        with ThreadPoolExecutor(max_workers=6) as pool:
            catalog = dict(zip(unique_regions, pool.map(cloud.locations, unique_regions)))
        for node in nodes:
            zone = next((z for z in catalog[node["region"]] if z["zone_id"] == node["zone_id"]), None)
            if not zone or not zone["available"] or node["instance_type"] not in zone["instance_types"]:
                raise ValueError(f'{node["name"]}: selected placement/type is unavailable. Refresh AWS locations.')
        now = int(time.time())
        job = {"job_id": job_id, "owner": owner, "config": config, "nodes": nodes, "status": "provisioning",
               "created_at": now, "updated_at": now, "deadline": now + 2700, "active": "yes",
               "schema_version": SCHEMA_VERSION, "runtime_revision": os.environ["RUNTIME_REVISION"]}
        if not store.create(job):
            job = owned(job_id, owner)
            if canonical_json(job["config"]) != canonical_json(config):
                raise HttpError(409, "This request ID was already used for a different configuration.")
    if job["status"] not in TERMINAL:
        try:
            cloud.client("stepfunctions").start_execution(stateMachineArn=os.environ["STATE_MACHINE_ARN"],
                name=job_id, input=canonical_json({"job_id": job_id}))
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") != "ExecutionAlreadyExists":
                raise
    return public_job(job)


def dispatch(event, owner):
    path = event["rawPath"].rstrip("/")
    method = event["requestContext"]["http"]["method"]
    query = event.get("queryStringParameters") or {}
    if (method != "GET" or path == "/real-world/tests") and not owner:
        raise HttpError(401, "Sign in to run or manage tests.")
    if method == "GET" and path == "/real-world/regions":
        return {"regions": cloud.regions()}
    if method == "GET" and path == "/real-world/locations":
        return {"zones": cloud.locations(query.get("region", ""))}
    if path == "/real-world/tests" and method == "POST":
        if event.get("isBase64Encoded"):
            raise ValueError("Send a JSON request body.")
        raw = event.get("body") or "{}"
        if len(raw) > 20000:
            raise ValueError("Request is too large.")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object.")
        return launch(body, owner)
    if path == "/real-world/tests" and method == "GET":
        return store.page(query.get("cursor"), owner)
    parts = path.split("/")
    if path == "/real-world/reports" and method == "GET":
        return store.page(query.get("cursor"))
    if len(parts) in (4, 5) and parts[1:3] == ["real-world", "reports"] and method == "GET":
        job = store.load(parts[3])
        if not job or job.get("schema_version") != SCHEMA_VERSION:
            raise HttpError(404, "Report not found.")
        if len(parts) == 4:
            from reports import load_report
            report = load_report(job, summary_only=query.get("summary") == "1")
            report["can_manage"] = job["owner"] == owner
            return report
        if parts[4] == "artifacts":
            return measurement_artifacts(job)
    if len(parts) in (4, 5) and parts[1:3] == ["real-world", "tests"]:
        job = store.load(parts[3])
        if not job or job.get("schema_version") != SCHEMA_VERSION:
            raise HttpError(404, "Test not found.")
        if len(parts) == 4 and method == "GET":
            return {**public_job(job), "can_manage": bool(owner and job["owner"] == owner)}
        if len(parts) == 5 and parts[4] == "cancel" and method == "POST":
            if job["owner"] != owner:
                raise HttpError(404, "Test not found.")
            if job["status"] not in TERMINAL:
                store.cancel(job["job_id"])
                job["cancel_requested"] = True
            return public_job(job)
        if len(parts) == 5 and parts[4] == "artifacts" and method == "GET":
            return measurement_artifacts(job)
    raise HttpError(404, "Endpoint not found.")


def measurement_artifacts(job):
    return artifacts.downloads(job)


def handler(event, context):
    try:
        method = event.get("requestContext", {}).get("http", {}).get("method")
        headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
        requires_login = method != "GET" or event.get("rawPath", "").rstrip("/") == "/real-world/tests"
        owner = authenticate(event) if requires_login or headers.get("authorization") else None
        result, status = dispatch(event, owner), 200
    except HttpError as error:
        result, status = {"error": error.message}, error.status
    except (ValueError, KeyError, TypeError) as error:
        result, status = {"error": str(error)}, 400
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__, "message": str(error)[:1000]}))
        result, status = {"error": "The real-world test service could not complete the request. Retry using the same request ID."}, 503
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"}, "body": json.dumps(result)}
