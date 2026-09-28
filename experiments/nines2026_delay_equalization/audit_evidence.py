#!/usr/bin/env python3
"""Read-only audit of the frozen campaign and its raw measurement artifacts.

This adds no post-hoc exclusions. Any inconsistency stops finalization for review.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
FROZEN = {
    "runner.py": "913125075877515f03cb427fba394d7670ed24d45cfdb5974d05f32979a7adb9",
    "preflight.py": "5f86cecc16996e15f47eca40ef294c4e69f66361a7b88358a275b6fa5b329e4f",
    "campaign-v1.json": "ebe43f2154514a3f2560b734e33d5c86256d7afb295c2addb4ebc2cdab760791",
    "PROTOCOL.md": "0fb1ef6303d0a6291a1e031031370e63231655433c36e4bf2fdb0ec8e7f5f9f4",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(root, require_complete=False):
    for name, expected in FROZEN.items():
        require(hashlib.sha256((HERE/name).read_bytes()).hexdigest() == expected,
                "Frozen source changed: "+name)
    manifest = json.loads((HERE/"campaign-v1.json").read_text())
    planned = {t["id"]: t for t in manifest["trials"]}
    paths = sorted(root.glob("worker-*/*/result.json"))
    observed, artifacts, statuses, workers = set(), {}, Counter(), Counter()
    iperf_versions = Counter()
    max_cpu = max_steal = max_calibration_error = 0
    total_flows = total_points = 0
    durations = []
    for path in paths:
        r = json.loads(path.read_text())
        identity = r["id"]
        require(identity in planned and identity not in observed, "Unexpected/duplicate trial: "+identity)
        observed.add(identity)
        t = planned[identity]
        require(all(r[k] == v for k,v in t.items()), "Schedule mismatch: "+identity)
        require(path.parent.name == identity and path.parent.parent.name == f"worker-{t['block_index'] % 8}",
                "Worker assignment mismatch: "+identity)
        require(r["runner_sha256"] == FROZEN["runner.py"], "Runner mismatch: "+identity)
        require(r["kernel"] == "6.13.7-jumpserve-nines2026+", "Kernel mismatch: "+identity)
        statuses[r["status"]] += 1
        workers[path.parent.parent.name] += 1
        require(r["status"] == "completed" and not r["validation_errors"], "Invalid trial requires review: "+identity)
        c = r["config"]
        require([f["flow"] for f in r["flows"]] == list(range(1,len(c["ccas"])+1)), "Flow identity mismatch")
        for f in r["flows"]:
            i = f["flow"]
            receiver = json.loads((path.parent/f"receiver-{i}.json").read_text())
            sender = json.loads((path.parent/f"sender-{i}.json").read_text())
            iperf_versions[receiver["start"]["version"]] += 1
            iperf_versions[sender["start"]["version"]] += 1
            require(not receiver.get("error") and not sender.get("error"), "iperf reported error")
            require(sender["end"]["sender_tcp_congestion"] == c["ccas"][i-1] == f["cca"], "CCA mismatch")
            intervals = [x["sum"] for x in receiver["intervals"]]
            require(len(intervals) == len(f["points"]), "Raw interval count mismatch")
            previous_end = 0
            for raw, point in zip(intervals, f["points"]):
                require(raw.get("sender") is not True and raw["bytes"] >= 0 and raw["seconds"] > 0,
                        "Invalid receiver interval")
                require(abs(raw["start"]-previous_end) < 0.001, "Receiver interval gap/overlap")
                previous_end = raw["end"]
                for key, source in [("start_seconds","start"),("end_seconds","end"),("seconds","seconds"),("bytes","bytes")]:
                    require(point[key] == raw[source], "Normalized sample differs from raw interval")
                require(math.isclose(point["goodput_mbps"], raw["bytes"]*8/raw["seconds"]/1e6, rel_tol=1e-12),
                        "Sample goodput mismatch")
            measured = [p for p in intervals if p["start"] >= c["warmup_seconds"]-0.05
                        and p["end"] <= c["duration_seconds"]+0.05 and p["seconds"] > 0.5]
            byte_count = sum(x["bytes"] for x in measured)
            seconds = sum(x["seconds"] for x in measured)
            require(seconds >= c["duration_seconds"]-c["warmup_seconds"]-2.1, "Incomplete measurement window")
            require(byte_count == f["received_bytes"] and math.isclose(seconds,f["measured_seconds"],rel_tol=1e-12),
                    "Steady-state aggregate differs from raw receiver intervals")
            require(math.isclose(byte_count*8/seconds/1e6,f["goodput_mbps"],rel_tol=1e-12), "Aggregate goodput mismatch")
            durations.append(seconds)
            total_flows += 1
            total_points += len(intervals)
        trace = [json.loads(line) for line in (path.parent/"socket-queue-trace.jsonl").read_text().splitlines()]
        require(len(trace) >= c["duration_seconds"]-1, "Incomplete socket/queue trace")
        for point in trace:
            q = point["queue"]
            require(len(q) == 1 and q[0]["kind"] == "netem", "Unexpected bottleneck qdisc")
            options = q[0]["options"]
            require(options["limit"] == c["queue_packets"] and options["rate"]["rate"]*8/1e6 == c["capacity_mbps"],
                    "Applied queue/rate differs from schedule")
        for cal in r["calibration"]:
            i = cal["flow"]-1
            natural_error = abs(cal["natural_min_rtt_ms"]-c["delays_ms"][i])
            target = c.get("target_rtt_ms",cal["natural_min_rtt_ms"])
            effective_error = abs(cal["effective_min_rtt_ms"]-target)
            max_calibration_error = max(max_calibration_error,natural_error,effective_error)
            require(natural_error <= 2 and effective_error <= 2, "Calibration outside frozen tolerance")
        for cpu in r["cpu"]:
            max_cpu = max(max_cpu,cpu["busy_percent"])
            max_steal = max(max_steal,cpu["steal_percent"])
        for artifact in sorted(path.parent.iterdir()):
            if artifact.is_file():
                artifacts[str(artifact.relative_to(root))] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    require(not require_complete or observed == set(planned), "The frozen campaign is incomplete")
    require(max_cpu <= 85 and max_steal <= 2, "CPU metrics outside frozen tolerance")
    preflights = sorted(root.glob("worker-*/preflight/preflight.json"))
    require(not require_complete or len(preflights) == 8, "Missing worker preflight evidence")
    latency_points = 0
    for path in preflights:
        report = json.loads(path.read_text())
        require(report["status"] == "passed" and report["runner_sha256"] == FROZEN["runner.py"]
                and report["cpu_count"] == 2 and report["kernel"] == "6.13.7-jumpserve-nines2026+",
                "Invalid preflight provenance")
        direction = json.loads((path.parent/"direction.json").read_text())
        records = {r["treatment"]:r for r in direction["records"]}
        require(set(records) == {"baseline","ack_plus_60","forward_plus_60"}, "Missing direction control")
        for r in records.values():
            points = r["points"]
            require(len(points) == r["received"] and 990 <= r["received"] <= r["sent"] == 1000,
                    "Invalid UDP probe counts")
            indices = [p["index"] for p in points]
            require(len(set(indices)) == len(indices) and all(0 <= i < r["sent"] for i in indices),
                    "Invalid/duplicate UDP packet identities")
            for metric in ("forward_ms","rtt_ms"):
                require(all(math.isfinite(p[metric]) and p[metric] >= 0 for p in points), "Invalid packet latency")
                require(math.isclose(statistics.median(p[metric] for p in points), r["median_"+metric], rel_tol=1e-12),
                        "UDP median differs from raw measurements")
            latency_points += len(points)
        base,ack,forward = (records[k] for k in ("baseline","ack_plus_60","forward_plus_60"))
        require(abs(ack["median_forward_ms"]-base["median_forward_ms"]) < 2
                and abs(ack["median_rtt_ms"]-base["median_rtt_ms"]-60) < 2
                and abs(forward["median_forward_ms"]-base["median_forward_ms"]-60) < 2
                and abs(forward["median_rtt_ms"]-base["median_rtt_ms"]-60) < 2,
                "Packet direction validation failed")
        for cca in ("bbr","bbr1"):
            capacity = json.loads((path.parent/cca/"result.json").read_text())
            rate = capacity["flows"][0]["goodput_mbps"]
            require(capacity["status"] == "completed" and 170 <= rate <= 202
                    and capacity["flows"][0]["cca"] == cca
                    and rate == report["checks"][cca+"_capacity_mbps"], "Single-flow capacity validation failed")
        for artifact in sorted(path.parent.rglob("*")):
            if artifact.is_file():
                artifacts[str(artifact.relative_to(root))] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return {
        "campaign_id":manifest["id"], "complete":observed == set(planned),
        "trials":len(observed), "planned_trials":len(planned), "statuses":dict(statuses),
        "workers":dict(workers), "flows":total_flows, "receiver_intervals":total_points,
        "iperf_version_artifact_counts":dict(iperf_versions),
        "validated_preflights":len(preflights), "udp_probe_points":latency_points,
        "max_trial_core_busy_percent":max_cpu, "max_trial_core_steal_percent":max_steal,
        "max_calibration_error_ms":max_calibration_error,
        "measurement_seconds_range":[min(durations),max(durations)] if durations else None,
        "frozen_sha256":FROZEN, "artifact_sha256":artifacts,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results",type=Path,default=HERE/"results"/"campaign-v1")
    parser.add_argument("--require-complete",action="store_true")
    args = parser.parse_args()
    report = audit(args.results,args.require_complete)
    (args.results/"evidence-audit.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k not in ("artifact_sha256","frozen_sha256")},indent=2))


if __name__ == "__main__":
    main()
