"""Durable job records. Cancellation is an independent atomic flag."""
from decimal import Decimal
import json
import os
import time


def table():
    import boto3
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def native(value):
    return json.loads(json.dumps(value, default=lambda n: int(n) if n == int(n) else float(n)))


def load(job_id):
    return native(table().get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item"))


def save(job):
    job["updated_at"] = int(time.time())
    values = {key: value for key, value in job.items() if key not in ("job_id", "cancel_requested", "lease_until")}
    table().update_item(Key={"job_id": job["job_id"]},
        UpdateExpression="SET " + ", ".join(f"#k{i} = :v{i}" for i in range(len(values))),
        ExpressionAttributeNames={f"#k{i}": key for i, key in enumerate(values)},
        ExpressionAttributeValues={f":v{i}": json.loads(json.dumps(value), parse_float=Decimal) for i, value in enumerate(values.values())})


def claim(job_id):
    try:
        table().update_item(Key={"job_id": job_id}, UpdateExpression="SET lease_until = :until",
            ConditionExpression="attribute_exists(job_id) AND (attribute_not_exists(lease_until) OR lease_until < :now)",
            ExpressionAttributeValues={":now": int(time.time()), ":until": int(time.time()) + 300})
        return True
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def release(job_id):
    table().update_item(Key={"job_id": job_id}, UpdateExpression="REMOVE lease_until")
