"""Versioned contract for EC2 congestion-control experiments."""
import json
import re

SCHEMA_VERSION = 1
CCAS = ("cubic", "bbr", "reno")
DEFAULT_INSTANCE_TYPE = "t3.medium"
INSTANCE_TYPES = ("t3.small", "t3.medium", "t3.large")
TERMINAL = ("completed", "failed", "cancelled")
AMI_PARAMETER = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def placement(value):
    if not isinstance(value, dict) or set(value) != {"region", "zone_id", "instance_type"}:
        raise ValueError("Each machine needs a Region, Availability Zone ID, and instance type.")
    if not isinstance(value["region"], str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", value["region"]):
        raise ValueError("Invalid AWS Region.")
    if not isinstance(value["zone_id"], str) or not re.fullmatch(r"[a-z0-9-]+-az\d+", value["zone_id"]):
        raise ValueError("Invalid Availability Zone ID.")
    if value["instance_type"] not in INSTANCE_TYPES:
        raise ValueError("Every machine must use t3.small, t3.medium, or t3.large.")
    return dict(value)


def validate_config(value):
    required = {"server", "bottleneck", "receivers", "cca", "duration_seconds", "rate_mbit", "buffer_kbytes", "notes"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Invalid real-world test configuration.")
    if value["cca"] not in CCAS:
        raise ValueError("Server CCA must be cubic, bbr, or reno.")
    receivers = value["receivers"]
    if not isinstance(receivers, list) or not 1 <= len(receivers) <= 16:
        raise ValueError("Choose between 1 and 16 receivers.")
    if not isinstance(value["notes"], str) or len(value["notes"]) > 4000:
        raise ValueError("Notes must be at most 4,000 characters.")
    return {
        "server": placement(value["server"]), "bottleneck": placement(value["bottleneck"]),
        "receivers": [placement(item) for item in receivers], "cca": value["cca"],
        "duration_seconds": integer(value["duration_seconds"], "Duration (seconds)", 10, 600),
        "rate_mbit": integer(value["rate_mbit"], "Bottleneck rate (Mbit/s)", 1, 1000),
        "buffer_kbytes": integer(value["buffer_kbytes"], "Buffer (decimal kB)", 2, 10000),
        "notes": value["notes"].strip(),
    }


def nodes_for(config):
    return [dict(config["server"], name="server", role="server", overlay_ip="10.254.0.2"),
            dict(config["bottleneck"], name="bottleneck", role="bottleneck", overlay_ip="10.254.0.1")] + [
        dict(item, name=f"receiver-{i + 1}", role="receiver", overlay_ip=f"10.254.0.{i + 10}", port=5201 + i)
        for i, item in enumerate(config["receivers"])
    ]


def public_job(job):
    """Never expose command details, signed URLs, or internal lease state."""
    fields = ("job_id", "status", "created_at", "updated_at", "config", "error", "cleanup_error",
              "outcome", "results", "deadline", "cancel_requested", "schema_version", "runtime_revision", "start_epoch")
    result = {key: job[key] for key in fields if key in job}
    result["nodes"] = [{key: node[key] for key in ("name", "role", "region", "zone_id", "instance_type",
                       "instance_id", "image_id", "overlay_ip", "state") if key in node} for node in job.get("nodes", [])]
    return result


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
