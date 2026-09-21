"""Copy legacy terminal tests into Supabase, verify every byte, retain AWS backups.

Default: read-only inventory. --apply imports and verifies. --verify checks an
existing import without writing. --check-storage exercises signed uploads and
cleans up only its own temporary object. Never starts EC2 or a workflow.
"""
import argparse
import hashlib
import json
import os
import subprocess
import urllib.request
import uuid

import artifacts
import cloud
from config import TERMINAL, nodes_for
import database
import reports
import runtime
import store


def configure(stack):
    import boto3
    credentials = json.loads(subprocess.check_output(["aws", "configure", "export-credentials", "--format", "process"], text=True))
    boto3.setup_default_session(aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"],
                               aws_session_token=credentials.get("SessionToken"), region_name="us-east-1")
    resources = cloud.client("cloudformation").describe_stack_resources(StackName=stack)["StackResources"]
    def resource(kind, prefix):
        return next(r["PhysicalResourceId"] for r in resources if r["ResourceType"] == kind and r["LogicalResourceId"].startswith(prefix))
    function = resource("AWS::Lambda::Function", "RealWorldTestsApi")
    environment = cloud.client("lambda").get_function_configuration(FunctionName=function)["Environment"]["Variables"]
    os.environ["SUPABASE_URL"] = environment["SUPABASE_URL"]
    os.environ["SUPABASE_SECRET_ARN"] = environment.get("SUPABASE_SECRET_ARN", "jumpserve/supabase-service-key")
    table = boto3.resource("dynamodb").Table(resource("AWS::DynamoDB::Table", "RealWorldTestsJobs"))
    bucket = resource("AWS::S3::Bucket", "RealWorldTestsResults")
    return table, bucket


def inventory(table):
    jobs, args = [], {"ConsistentRead": True}
    while True:
        page = table.scan(**args)
        jobs.extend(store.native(page["Items"]))
        if "LastEvaluatedKey" not in page:
            break
        args["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    if any(job["status"] not in TERMINAL or job.get("active") != "no" for job in jobs):
        raise RuntimeError("Legacy tests are active. Finish their cleanup before the storage cutover.")
    return jobs


def migrate(jobs, bucket, apply):
    copied, total_bytes = 0, 0
    s3 = cloud.client("s3")
    for job in jobs:
        if apply:
            store.create(job)  # Conditional insert; never overwrite a live Supabase record.
        saved = store.load(job["job_id"])
        expected = {k: v for k, v in job.items() if k != "lease_until"}
        expected.setdefault("cancel_requested", False)
        if saved != expected:
            raise RuntimeError("Job metadata verification failed: " + job["job_id"])
        archived = artifacts.records(job["job_id"])
        raw_reports, sources = {}, []
        for node in nodes_for(job["config"]):
            name = node["name"]
            try:
                result = s3.get_object(Bucket=bucket, Key=f'{job["job_id"]}/{name}.json')
            except Exception as error:
                if getattr(error, "response", {}).get("Error", {}).get("Code") == "NoSuchKey":
                    continue
                raise
            stream = result["Body"]
            try:
                payload = stream.read(artifacts.MAX_BYTES + 1)
            finally:
                stream.close()
            if len(payload) != result["ContentLength"] or len(payload) > artifacts.MAX_BYTES:
                raise RuntimeError("Legacy artifact exceeds supported size or was truncated.")
            digest = hashlib.sha256(payload).hexdigest()
            existing = next((r for r in archived if r["node_name"] == name), None)
            if existing and existing["sha256"] != digest:
                raise RuntimeError("Existing archive differs from legacy evidence; refusing overwrite.")
            if apply and not existing:
                artifacts.archive(job["job_id"], name, payload, result.get("VersionId"))
            verified = artifacts.read(job["job_id"], name)
            if verified is None or verified[0] != payload or verified[1]["version_id"] != result.get("VersionId"):
                raise RuntimeError("Artifact verification failed: " + name)
            copied += 1
            total_bytes += len(payload)
            raw_reports[name] = json.loads(payload)
            sources.append(verified[1])
        if apply:
            reports.persist_report(saved)
        actual = reports.load_report(saved)
        expected_report = reports.build_report(saved, raw_reports, sources)
        for key in ("summary", "receivers", "queue", "comparison", "sources", "provenance"):
            if actual[key] != expected_report[key]:
                raise RuntimeError("Analysis changed during migration: " + key)
        print(json.dumps({"job_id": job["job_id"], "status": job["status"], "verified": True}), flush=True)
    return {"jobs": len(jobs), "artifacts": copied, "bytes": total_bytes, "checksums": "verified", "legacy_stores": "retained"}


def check_storage():
    job_id = "_verification/" + str(uuid.uuid4())
    key = job_id + "/server.json"
    value = {"purpose": "JumpServe signed storage verification", "id": job_id}
    try:
        url = artifacts.signed_upload(job_id, "server")
        runtime.upload(url, value)
        runtime.upload(url, value)  # Retry semantics: same capability and same bytes.
        payload = database.request("/storage/v1/object/" + artifacts.object_path(key), raw=True)
        if json.loads(payload) != value:
            raise RuntimeError("Signed upload changed the payload")
        signed = database.request("/storage/v1/object/sign/" + artifacts.object_path(key), "POST", {"expiresIn": 300})
        with urllib.request.urlopen(database.base_url() + "/storage/v1" + signed["signedURL"], timeout=15) as response:
            if response.read() != payload:
                raise RuntimeError("Signed download changed the payload")
        print(json.dumps({"signed_upload": "passed", "upload_retry": "passed", "signed_download": "passed"}))
    finally:
        database.request("/storage/v1/object/" + artifacts.BUCKET, "DELETE", {"prefixes": [key]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="JumpServeBenchmarkStack")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--apply", action="store_true")
    operation.add_argument("--verify", action="store_true")
    operation.add_argument("--check-storage", action="store_true")
    args = parser.parse_args()
    table, bucket = configure(args.stack)
    if args.check_storage:
        check_storage()
        return
    jobs = inventory(table)
    if args.apply or args.verify:
        print(json.dumps(migrate(jobs, bucket, args.apply)))
    else:
        print(json.dumps({"terminal_jobs": len(jobs), "active_jobs": 0, "apply": False}))


if __name__ == "__main__":
    main()
