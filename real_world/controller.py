"""Resumable workflow; every failure converges on tagged-resource cleanup."""
import base64
import json
import os
from pathlib import Path
import time

import cloud
import artifacts
from config import TERMINAL
import store


def encoded(value):
    return base64.b64encode(value.encode()).decode()


def command(job, node, action):
    settings = dict(job["config"], node=node, nodes=job["nodes"], job_id=job["job_id"], runtime_revision=job.get("runtime_revision"))
    lines = ["set -eu", "install -d -m 700 /var/lib/jumpserve"]
    if action == "prepare":
        lines += ["timeout 900 sh -c 'until test -f /var/lib/jumpserve/bootstrap-ready; do sleep 3; done'",
                  "echo '" + encoded(Path(__file__).with_name("runtime.py").read_text()) + "' | base64 -d > /var/lib/jumpserve/runtime.py"]
    if action == "start":
        settings["start_epoch"] = job["start_epoch"]
        settings["upload_url"] = artifacts.signed_upload(job["job_id"], node["name"])
    lines += ["echo '" + encoded(json.dumps(settings)) + f"' | base64 -d > /var/lib/jumpserve/{action}.json",
              f"chmod 600 /var/lib/jumpserve/{action}.json",
              f"python3 /var/lib/jumpserve/runtime.py {action} /var/lib/jumpserve/{action}.json"]
    return cloud.client("ssm", node["region"]).send_command(InstanceIds=[node["instance_id"]],
        DocumentName="AWS-RunShellScript", TimeoutSeconds=1200,
        Parameters={"commands": lines, "executionTimeout": ["1000"]}, Comment=f'JumpServe {action} {job["job_id"]}')["Command"]["CommandId"]


def commands_complete(job, action):
    complete = True
    for node in job["nodes"]:
        key = action + "_command"
        if node.get(action + "_done"):
            continue
        if not node.get(key):
            # SSM registration trails EC2 running state.
            if action == "prepare":
                online = cloud.client("ssm", node["region"]).describe_instance_information(
                    Filters=[{"Key": "InstanceIds", "Values": [node["instance_id"]]}])["InstanceInformationList"]
                if not online or online[0]["PingStatus"] != "Online":
                    complete = False
                    continue
            node[key] = command(job, node, action)
            store.save(job)
            complete = False
            continue
        try:
            invocation = cloud.client("ssm", node["region"]).get_command_invocation(CommandId=node[key], InstanceId=node["instance_id"])
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") == "InvocationDoesNotExist":
                complete = False
                continue
            raise
        if invocation["Status"] == "Success":
            output = json.loads(invocation["StandardOutputContent"].strip().splitlines()[-1])
            if action == "prepare":
                node["public_key"] = output["public_key"]
                node["kernel"] = output["kernel"]
            node[action + "_done"] = True
            store.save(job)
        elif invocation["Status"] in ("Pending", "InProgress", "Delayed"):
            complete = False
        else:
            raise RuntimeError(f'{node["name"]}: {action} {invocation["Status"]}. ' + invocation.get("StandardErrorContent", "")[-500:])
    return complete


def collect(job):
    reports = []
    sources = artifacts.records(job["job_id"])
    for node in job["nodes"]:
        result = artifacts.read(job["job_id"], node["name"], sources)
        if result is None:
            return False
        report = json.loads(result[0])
        if not report.get("success"):
            raise RuntimeError(f'{node["name"]}: ' + report.get("error", "Measurement failed."))
        reports.append((node, report))
    job["results"] = [{"receiver": node["name"],
        "received_mbit_per_second": report["iperf"]["end"]["sum_received"]["bits_per_second"] / 1_000_000,
        "received_bytes": report["iperf"]["end"]["sum_received"]["bytes"],
        "seconds": report["iperf"]["end"]["sum_received"]["seconds"],
        "start_epoch": report["started_at"]} for node, report in reports if node["role"] == "receiver"]
    job["outcome"] = "completed"
    job["status"] = "cleaning"
    return True


def cleanup(job):
    errors, done = [], True
    for region in sorted({node["region"] for node in job["nodes"]}):
        try:
            if not cloud.cleanup_region(job, region):
                done = False
        except Exception as error:
            done = False
            errors.append(f"{region}: {error}")
    job["cleanup_error"] = "; ".join(errors)[-1500:] if errors else None
    if done:
        job["status"] = job.get("outcome", "failed")
        job["active"] = "no"
        for node in job["nodes"]:
            node["state"] = "terminated"


def step(job):
    previous_status = job["status"]
    if job.get("cancel_requested") and job["status"] != "cleaning":
        job.update(status="cleaning", outcome="cancelled")
    if int(time.time()) >= job["deadline"] and job["status"] != "cleaning":
        job.update(status="cleaning", outcome="failed", error="Test exceeded its 45-minute resource deadline.")
    if job["status"] == "cleaning" and previous_status != "cleaning":
        # Persist entry before cleanup can finish in this same invocation.
        store.save(job)
    state = job["status"]
    if state == "provisioning":
        node = next((node for node in job["nodes"] if not node.get("instance_id")), None)
        if node:
            cloud.provision(job, node, os.environ["INSTANCE_PROFILE_ARN"])
        else:
            job["status"] = "bootstrapping"
    elif state == "bootstrapping":
        ready = [cloud.refresh(node) for node in job["nodes"]]
        if all(ready) and commands_complete(job, "prepare"):
            for node in job["nodes"]:
                cloud.allow_peers(job, node)
            job["status"] = "configuring"
    elif state == "configuring":
        if commands_complete(job, "configure"):
            job["status"] = "checking"
    elif state == "checking":
        if commands_complete(job, "preflight"):
            job["status"] = "starting"
            job["start_epoch"] = int(time.time()) + 120
    elif state == "starting":
        if commands_complete(job, "start"):
            job["status"] = "running"
    elif state == "running":
        if not collect(job) and time.time() > job["start_epoch"] + job["config"]["duration_seconds"] + 240:
            raise RuntimeError("Timed out waiting for measurement artifacts.")
    elif state == "cleaning":
        cleanup(job)
    else:
        raise RuntimeError("Unknown lifecycle state: " + state)


def tick(job_id, force_cleanup=False):
    token = store.claim(job_id)
    if not token:
        return {"job_id": job_id, "finished": False}
    try:
        job = store.load(job_id)
        job["_lease_token"] = token
        if job["status"] in TERMINAL:
            finalize(job)
            return {"job_id": job_id, "finished": True}
        if force_cleanup:
            previous_status = job["status"]
            job.update(status="cleaning", outcome=job.get("outcome", "failed"), error=job.get("error", "Workflow interrupted or resource deadline reached."))
            if previous_status != "cleaning":
                store.save(job)
        try:
            step(job)
        except Exception as error:
            job.update(status="cleaning", outcome="failed", error=str(error)[-1500:])
        store.save(job)
        if job["status"] in TERMINAL:
            finalize(job)
        return {"job_id": job_id, "finished": job["status"] in TERMINAL}
    finally:
        store.release(job_id, token)


def handler(event, context):
    return tick(event["job_id"], event.get("force_cleanup", False))


def reap(event, context):
    for job_id in store.expired():
        try:
            tick(job_id, force_cleanup=True)
        except Exception as error:
            print(json.dumps({"job_id": job_id, "cleanup_error": str(error)}))


def finalize(job):
    from reports import persist_report
    artifacts.freeze(job)
    persist_report(job)
