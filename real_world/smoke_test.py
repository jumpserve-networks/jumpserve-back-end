"""Bounded deployment verification through the real API launch implementation.

Requires operator AWS credentials. No service key or user token is fabricated.
Without --apply this only discovers resources/placements and prints the plan.
"""
import argparse
import json
import os
import subprocess
import time
import uuid

import api
import cloud
from config import INSTANCE_TYPE, TERMINAL
import store


def placement(region):
    zones = [z for z in cloud.locations(region) if z["available"] and z["type"] == "availability-zone"
             and INSTANCE_TYPE in z["instance_types"]]
    if not zones:
        raise RuntimeError("No eligible zones in " + region)
    zone = zones[0]
    return {"region": region, "zone_id": zone["zone_id"], "instance_type": INSTANCE_TYPE}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="JumpServeBenchmarkStack")
    parser.add_argument("--server-region", default="us-east-1")
    parser.add_argument("--bottleneck-region", default="us-east-1")
    parser.add_argument("--receiver-region", action="append")
    parser.add_argument("--cca", choices=["cubic", "bbr", "reno"], default="bbr")
    parser.add_argument("--cancel", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    # Support AWS CLI browser-login credentials even with an older local boto3.
    # Credentials stay in memory and are never written to files or printed.
    import boto3
    credentials = json.loads(subprocess.check_output(["aws", "configure", "export-credentials", "--format", "process"], text=True))
    boto3.setup_default_session(aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"],
                               aws_session_token=credentials.get("SessionToken"))
    resources = cloud.client("cloudformation").describe_stack_resources(StackName=args.stack)["StackResources"]
    for kind, variable in [("AWS::DynamoDB::Table", "TABLE_NAME"), ("AWS::S3::Bucket", "RESULTS_BUCKET"), ("AWS::StepFunctions::StateMachine", "STATE_MACHINE_ARN")]:
        os.environ[variable] = next(r["PhysicalResourceId"] for r in resources if r["ResourceType"] == kind and r["LogicalResourceId"].startswith("RealWorldTests"))
    api_function = next(r["PhysicalResourceId"] for r in resources if r["ResourceType"] == "AWS::Lambda::Function" and r["LogicalResourceId"].startswith("RealWorldTestsApi"))
    os.environ["RUNTIME_REVISION"] = cloud.client("lambda").get_function_configuration(FunctionName=api_function)["Environment"]["Variables"]["RUNTIME_REVISION"]
    config = {"server": placement(args.server_region), "bottleneck": placement(args.bottleneck_region),
              "receivers": [placement(r) for r in (args.receiver_region or ["us-east-1"])], "cca": args.cca,
              "duration_seconds": 10, "rate_mbit": 10, "buffer_kbytes": 125, "notes": "Bounded deployment validation; exclude from research comparisons."}
    print(json.dumps({"config": config, "machines": len(config["receivers"]) + 2, "apply": args.apply}), flush=True)
    if not args.apply:
        return
    launched = api.launch({"config": config, "request_id": str(uuid.uuid4())}, "deployment-smoke-test")
    job_id = launched["job_id"]
    print(json.dumps({"job_id": job_id}), flush=True)
    last_status = None
    cancelled = False
    try:
        until = time.time() + 2400
        while time.time() < until:
            job = store.load(job_id)
            if job["status"] != last_status:
                print(json.dumps({"job_id": job_id, "status": job["status"], "error": job.get("error"), "cleanup_error": job.get("cleanup_error")}), flush=True)
                last_status = job["status"]
            if args.cancel and not cancelled and any(n.get("instance_id") for n in job["nodes"]):
                store.table().update_item(Key={"job_id": job_id}, UpdateExpression="SET cancel_requested = :yes", ExpressionAttributeValues={":yes": True})
                cancelled = True
            if job["status"] in TERMINAL:
                expected = "cancelled" if args.cancel else "completed"
                if job["status"] != expected:
                    raise RuntimeError(json.dumps({"status": job["status"], "error": job.get("error")}))
                for region in {n["region"] for n in job["nodes"]}:
                    ec2 = cloud.client("ec2", region)
                    instances = [i for r in ec2.describe_instances(Filters=cloud.filters(job))["Reservations"] for i in r["Instances"]]
                    if any(i["State"]["Name"] != "terminated" for i in instances) or ec2.describe_vpcs(Filters=cloud.filters(job))["Vpcs"]:
                        raise RuntimeError("Residual test resources in " + region)
                if not args.cancel:
                    for node in job["nodes"]:
                        response = cloud.client("s3").get_object(Bucket=os.environ["RESULTS_BUCKET"], Key=f'{job_id}/{node["name"]}.json')
                        report = json.loads(response["Body"].read())
                        if not report["success"] or report["effective_cca"] != args.cca:
                            raise RuntimeError("Missing success/CCA evidence for " + node["name"])
                        if node["role"] == "bottleneck" and not any(q.get("kind") == "bfifo" and q.get("bytes", 0) > 0 for s in report["samples"] for q in s["qdisc"]):
                            raise RuntimeError("Shared FIFO has no measured traffic.")
                print(json.dumps({"verified": True, "job_id": job_id, "status": job["status"], "results": job.get("results", []), "resources_removed": True}), flush=True)
                return
            time.sleep(10)
        raise RuntimeError("Smoke test timed out; cancellation requested.")
    finally:
        latest = store.load(job_id)
        if latest and latest["status"] not in TERMINAL:
            store.table().update_item(Key={"job_id": job_id}, UpdateExpression="SET cancel_requested = :yes", ExpressionAttributeValues={":yes": True})
            print(json.dumps({"job_id": job_id, "cancel_requested": True, "deadline": latest["deadline"]}), flush=True)


if __name__ == "__main__":
    main()
