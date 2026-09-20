"""Versioned, authenticated analysis of EC2 artifacts. No inferred base RTT."""
import hashlib
import json
import math
import os
import re
from statistics import median

import cloud
from config import canonical_json, nodes_for, public_job

ANALYSIS_VERSION = "real-world-report-v1"
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def valid_shape(raw):
    """Reject malformed containers without crashing every machine's report."""
    def objects(value):
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)
    if not isinstance(raw, dict):
        return False
    if any(key in raw and not isinstance(raw[key], str) for key in ("kernel", "iperf_version")):
        return False
    if any(not isinstance(raw.get(key, {}), dict) for key in ("configuration", "placement", "preflight", "iperf")):
        return False
    if not isinstance(raw.get("preflight", {}).get("ping", ""), str) or not objects(raw.get("samples", [])):
        return False
    if any(not isinstance(s.get("ss", ""), str) or not objects(s.get("qdisc", [])) for s in raw.get("samples", [])):
        return False
    iperf = raw.get("iperf", {})
    if any(not isinstance(iperf.get(key, {}), dict) for key in ("start", "end")) or not objects(iperf.get("intervals", [])):
        return False
    start, end = iperf.get("start", {}), iperf.get("end", {})
    return (objects(start.get("connected", [])) and isinstance(start.get("test_start", {}), dict)
            and all(isinstance(end.get(key, {}), dict) for key in ("sum_received", "sum_sent"))
            and all(isinstance(i.get("sum", {}), dict) for i in iperf.get("intervals", [])))


def number(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= minimum else None


def quantile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def trace_stats(points, key, duration):
    values = [p[key] for p in points if 0 <= p["seconds"] <= duration and p.get(key) is not None]
    return {"samples": len(values), "median": median(values) if values else None,
            "p95": quantile(values, .95)}


def socket_metrics(text, local, peer):
    """Match the iperf data socket's full tuple, excluding control and SSM sockets."""
    blocks, current = [], []
    for line in text.splitlines():
        if line and not line[0].isspace():
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    matches = [" ".join(block[1:]) for block in blocks
               if local in block[0].split() and peer in block[0].split()]
    if len(matches) != 1:
        return {"rtt_ms": None, "cwnd_bytes": None}
    def field(name, minimum=0):
        found = re.search(r"(?:^|\s)" + name + r":([0-9]+(?:\.[0-9]+)?)", matches[0])
        return number(float(found.group(1)), minimum) if found else None
    cwnd, mss = field("cwnd", 1), field("mss", 1)
    return {"rtt_ms": field("rtt", .000001),
            "cwnd_bytes": cwnd * mss if cwnd is not None and mss is not None else None}


def tcp_trace(server, iperf, expected_node, planned_start):
    connected = iperf.get("start", {}).get("connected", [])
    if len(connected) != 1 or planned_start is None:
        return []
    connection = connected[0]
    if (connection.get("local_host") != expected_node["overlay_ip"]
            or connection.get("remote_host") != "10.254.0.2"
            or connection.get("remote_port") != expected_node["port"]
            or number(connection.get("local_port"), 1) is None):
        return []
    local = f'10.254.0.2:{expected_node["port"]}'
    peer = f'{expected_node["overlay_ip"]}:{connection["local_port"]}'
    points = []
    for sample in server.get("samples", []):
        epoch = number(sample.get("epoch"))
        if epoch is not None:
            points.append({"seconds": epoch - planned_start,
                           **socket_metrics(sample.get("ss", ""), local, peer)})
    return sorted(points, key=lambda point: point["seconds"])


def throughput_trace(iperf):
    points, invalid = [], 0
    previous_end = None
    for interval in iperf.get("intervals", []):
        summary = interval.get("sum", {})
        start, end = number(summary.get("start")), number(summary.get("end"))
        seconds, received = number(summary.get("seconds"), .000001), number(summary.get("bytes"))
        if (summary.get("omitted") is True or summary.get("sender") is not False
                or None in (start, end, seconds, received) or end <= start
                or abs(end - start - seconds) > .02 or (previous_end is not None and start < previous_end - .001)):
            invalid += 1
            continue
        points.append({"start": start, "seconds": end, "duration_seconds": seconds,
                       "received_bytes": received, "mbit_per_second": received * 8 / seconds / 1_000_000})
        previous_end = end
    return points, invalid


def queue_trace(report, planned_start, rate):
    points = []
    if planned_start is None:
        return points
    for sample in report.get("samples", []):
        epoch = number(sample.get("epoch"))
        queues = [q for q in sample.get("qdisc", []) if q.get("kind") == "bfifo" and q.get("handle") == "10:"]
        if epoch is None:
            continue
        q = queues[0] if len(queues) == 1 else {}
        backlog = number(q.get("backlog"))
        points.append({"seconds": epoch - planned_start, "backlog_bytes": backlog,
                       "queue_delay_ms": backlog * 8 / (rate * 1000) if backlog is not None and rate else None,
                       "drops": number(q.get("drops")), "sent_bytes": number(q.get("bytes"))})
    return sorted(points, key=lambda point: point["seconds"])


def build_report(job, artifacts, sources=None, load_issues=None, summary_only=False):
    """Normalize supported evidence; keep missing/invalid values distinct from zero."""
    config = job["config"]
    expected = nodes_for(config)
    recorded = {node["name"]: node for node in job.get("nodes", [])}
    issues = list(load_issues or [])
    for name, raw in artifacts.items():
        if not valid_shape(raw):
            issues.append(f"{name}: malformed report fields prevent analysis; inspect the raw artifact.")
    artifacts = {name: raw for name, raw in artifacts.items() if valid_shape(raw)}
    exclusions = []
    provenance = []
    duration, rate = config["duration_seconds"], config["rate_mbit"]
    server = artifacts.get("server", {})
    planned = number(server.get("planned_start_epoch"))
    starts = []
    if job["status"] != "completed":
        exclusions.append("Only completed tests enter comparisons; failed and incomplete transfers are not imputed as zero.")
    if not job.get("runtime_revision"):
        exclusions.append("Measurement runtime revision is missing.")
    if job.get("owner") == "deployment-smoke-test":
        exclusions.append("Operator deployment validation is not a research repetition.")
    for node in expected:
        name = node["name"]
        raw = artifacts.get(name)
        machine = recorded.get(name, {})
        if raw is None:
            exclusions.append(f"{name}: raw report is unavailable.")
            provenance.append({"name": name, "kernel": None, "iperf_version": None, "image_id": machine.get("image_id"), "success": False})
            continue
        kernel = raw.get("kernel")
        version = (raw.get("iperf_version") or "").splitlines()[0] if raw.get("iperf_version") else None
        provenance.append({"name": name, "kernel": kernel, "iperf_version": version,
                           "image_id": machine.get("image_id"), "success": raw.get("success") is True})
        if raw.get("job_id") != job["job_id"] or raw.get("node") != name:
            exclusions.append(f"{name}: artifact identity does not match this test.")
        if canonical_json(raw.get("configuration")) != canonical_json(config):
            exclusions.append(f"{name}: artifact configuration differs from the stored test.")
        if not raw.get("runtime_revision") or raw.get("runtime_revision") != job.get("runtime_revision"):
            exclusions.append(f"{name}: measurement runtime provenance is missing or inconsistent.")
        if not kernel or not version or not machine.get("image_id"):
            exclusions.append(f"{name}: kernel, iperf, or image provenance is missing.")
        if raw.get("success") is not True:
            exclusions.append(f"{name}: measurement did not finish successfully.")
        placement = raw.get("placement", {})
        if any(placement.get(key) != machine.get(key) for key in ("region", "zone_id", "instance_type", "image_id", "instance_id")):
            exclusions.append(f"{name}: recorded placement differs between the test and artifact.")
        if raw.get("effective_cca") != config["cca"]:
            exclusions.append(f"{name}: effective CCA is missing or differs from the requested CCA.")
        if planned is None or raw.get("planned_start_epoch") != planned:
            exclusions.append(f"{name}: common start-barrier provenance is missing or inconsistent.")
        started = number(raw.get("started_at"))
        if started is not None:
            starts.append(started)
        else:
            issues.append(f"{name}: actual measurement start timestamp is missing.")

    receivers = []
    for node in (n for n in expected if n["role"] == "receiver"):
        raw = artifacts.get(node["name"], {})
        iperf = raw.get("iperf", {})
        received = iperf.get("end", {}).get("sum_received", {})
        byte_count, seconds = number(received.get("bytes"), 1), number(received.get("seconds"), .000001)
        mbps = byte_count * 8 / seconds / 1_000_000 if byte_count is not None and seconds is not None else None
        if mbps is None:
            exclusions.append(f'{node["name"]}: complete receiver throughput is unavailable.')
        elif abs(seconds - duration) > max(1, duration * .05):
            exclusions.append(f'{node["name"]}: recorded transfer duration differs materially from the configured duration.')
        protocol = iperf.get("start", {}).get("test_start", {})
        if protocol.get("protocol") != "TCP" or protocol.get("reverse") != 1 or protocol.get("num_streams") != 1:
            exclusions.append(f'{node["name"]}: expected single-stream server-to-receiver TCP workload is unverified.')
        if iperf.get("end", {}).get("sender_tcp_congestion") != config["cca"]:
            exclusions.append(f'{node["name"]}: iperf sender CCA is missing or inconsistent.')
        throughput, invalid = throughput_trace(iperf)
        tcp = tcp_trace(server, iperf, node, planned)
        rtt = trace_stats(tcp, "rtt_ms", duration)
        if not rtt["samples"]:
            issues.append(f'{node["name"]}: no valid sender RTT samples matched the data socket in the scheduled window.')
        if invalid:
            issues.append(f'{node["name"]}: {invalid} invalid or omitted throughput intervals were excluded.')
        if not throughput:
            issues.append(f'{node["name"]}: receiver interval trace is unavailable.')
        ping = re.search(r"=\s*[\d.]+/([\d.]+)/[\d.]+/[\d.]+\s*ms", raw.get("preflight", {}).get("ping", ""))
        receiver = {"name": node["name"], "placement": {k: node[k] for k in ("region", "zone_id", "instance_type")},
                    "received_bytes": byte_count, "duration_seconds": seconds, "mean_mbit_per_second": mbps,
                    "retransmits": number(iperf.get("end", {}).get("sum_sent", {}).get("retransmits")),
                    "preflight_rtt_ms": float(ping.group(1)) if ping else None,
                    "rtt": rtt, "throughput_intervals": len(throughput),
                    "tcp_samples": sum(1 for point in tcp if point["rtt_ms"] is not None)}
        if not summary_only:
            receiver.update(throughput=throughput, tcp=tcp)
        receivers.append(receiver)

    queue = queue_trace(artifacts.get("bottleneck", {}), planned, rate)
    queue_stats = trace_stats(queue, "queue_delay_ms", duration)
    if not queue_stats["samples"]:
        issues.append("Shared FIFO queue samples are unavailable; queue delay is not inferred from RTT.")
    counters = [p["drops"] for p in queue if p["drops"] is not None]
    resets = any(b < a for a, b in zip(counters, counters[1:]))
    if resets:
        issues.append("Queue drop counter reset; its total is unavailable.")
    values = [r["mean_mbit_per_second"] for r in receivers]
    complete = all(value is not None for value in values)
    total = sum(values) if complete else None
    fairness = total ** 2 / (len(values) * sum(v ** 2 for v in values)) if complete and len(values) > 1 and total > 0 else None
    configuration = {key: value for key, value in config.items() if key not in ("cca", "notes")}
    comparison_config = {"configuration": configuration, "runtime_revision": job.get("runtime_revision"),
                         "machines": [{key: p[key] for key in ("name", "kernel", "iperf_version", "image_id")} for p in provenance]}
    eligible = not exclusions
    result = {"analysis_version": ANALYSIS_VERSION, "job": public_job(job), "receivers": receivers,
              "summary": {"combined_mean_mbit_per_second": total, "jain_fairness": fairness,
                          "received_bytes": sum(r["received_bytes"] for r in receivers) if complete else None,
                          "queue_delay": queue_stats,
                          "observed_queue_drops": counters[-1] - counters[0] if len(counters) >= 2 and not resets else None,
                          "start_skew_ms": (max(starts) - min(starts)) * 1000 if len(starts) == len(expected) else None},
              "provenance": provenance, "sources": sources or [], "warnings": list(dict.fromkeys(issues)),
              "comparison": {"eligible": eligible, "exclusions": list(dict.fromkeys(exclusions)),
                             "configuration": comparison_config,
                             "key": hashlib.sha256(canonical_json(comparison_config).encode()).hexdigest() if eligible else None}}
    if not summary_only:
        result["queue"] = queue
    return result


def load_report(job, summary_only=False):
    client = cloud.client("s3")
    artifacts, sources, issues = {}, [], []
    total_bytes = 0
    for node in nodes_for(job["config"]):
        name = node["name"]
        key = f'{job["job_id"]}/{name}.json'
        try:
            response = client.get_object(Bucket=os.environ["RESULTS_BUCKET"], Key=key)
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                issues.append(f"{name}: raw report has not been stored.")
                continue
            raise
        stream = response["Body"]
        try:
            length = response["ContentLength"]
            if length > MAX_ARTIFACT_BYTES or total_bytes + length > MAX_TOTAL_BYTES:
                issues.append(f"{name}: artifact exceeds interactive analysis limits; use the raw download.")
                continue
            payload = stream.read(min(MAX_ARTIFACT_BYTES, MAX_TOTAL_BYTES - total_bytes) + 1)
            total_bytes += len(payload)
            if len(payload) != length:
                issues.append(f"{name}: artifact length is inconsistent.")
                continue
            raw = json.loads(payload, parse_constant=lambda value: None)
            if not isinstance(raw, dict):
                raise ValueError("Expected an object")
            artifacts[name] = raw
            sources.append({"name": name + ".json", "sha256": hashlib.sha256(payload).hexdigest(),
                            "version_id": response.get("VersionId"), "bytes": length})
        except (ValueError, UnicodeDecodeError):
            issues.append(f"{name}: artifact is not a valid JSON object.")
        finally:
            stream.close()
    return build_report(job, artifacts, sources, issues, summary_only)
