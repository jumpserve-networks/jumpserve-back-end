#!/usr/bin/env python3
"""Isolated Linux namespace experiments; workers never receive database keys.

Natural delay is a round-trip increment, placed once on returning ACKs.
Equalization is a calibrated, fixed ACK delay: an explicitly idealized control,
not a claim to implement the paper's adaptive Algorithm 1.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parent
PREFIX = "jsn-"


def command(*args, ns=None, check=True, timeout=20):
    argv = (["ip", "netns", "exec", PREFIX + ns] if ns else []) + list(map(str, args))
    return subprocess.run(argv, check=check, capture_output=True, text=True, timeout=timeout).stdout


def namespace(name):
    command("ip", "netns", "add", PREFIX + name)
    command("ip", "link", "set", "lo", "up", ns=name)
    command("sysctl", "-qw", "net.ipv4.ip_forward=1",
            "net.ipv4.conf.all.rp_filter=0", "net.ipv4.conf.default.rp_filter=0",
            "net.ipv4.tcp_rmem=4096 87380 67108864",
            "net.ipv4.tcp_wmem=4096 65536 67108864", ns=name)


def link(a, adev, aip, b, bdev, bip, index):
    left, right = f"jn{index}a", f"jn{index}b"
    command("ip", "link", "add", left, "type", "veth", "peer", "name", right)
    for temp, host, dev, address in ((left, a, adev, aip), (right, b, bdev, bip)):
        command("ip", "link", "set", temp, "netns", PREFIX + host)
        command("ip", "link", "set", temp, "name", dev, ns=host)
        command("ip", "link", "set", dev, "mtu", "1500", "up", ns=host)
        command("ip", "address", "add", address + "/24", "dev", dev, ns=host)
        command("ethtool", "-K", dev, "tso", "off", "gso", "off", "gro", "off", ns=host)


def route(host, network, via):
    command("ip", "route", "add", network, "via", via, ns=host)


def netem(host, dev, *parameters):
    command("tc", "qdisc", "replace", "dev", dev, "root", "netem", *parameters, ns=host)


def cleanup():
    # Only the dedicated research namespace prefix is eligible for cleanup.
    for line in command("ip", "netns", "list").splitlines():
        name = line.split()[0]
        if name.startswith(PREFIX):
            pids = command("ip", "netns", "pids", name).split()
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            command("ip", "netns", "delete", name)


def topology(config):
    cleanup()
    namespace("b")
    namespace("r")
    link("b", "out", "10.72.0.1", "r", "in", "10.72.0.2", 0)
    route("b", "10.73.0.0/16", "10.72.0.2")
    route("r", "10.70.0.0/16", "10.72.0.1")
    netem("b", "out", "rate", f"{config['capacity_mbps']}mbit", "limit", config["queue_packets"])
    netem("r", "in", "rate", "1000mbit", "limit", "1000")
    for i, delay in enumerate(config["delays_ms"], 1):
        s, p, c = f"s{i}", f"p{i}", f"c{i}"
        for host in (s, p, c):
            namespace(host)
        link(s, "out", f"10.70.{i}.2", p, "left", f"10.70.{i}.1", 3*i-2)
        link(p, "right", f"10.71.{i}.1", "b", f"p{i}", f"10.71.{i}.2", 3*i-1)
        link("r", f"c{i}", f"10.73.{i}.1", c, "in", f"10.73.{i}.2", 3*i)
        route(s, "default", f"10.70.{i}.1")
        route(p, "default", f"10.71.{i}.2")
        route("b", f"10.70.{i}.0/24", f"10.71.{i}.1")
        route(c, "default", f"10.73.{i}.1")
        command("tc", "qdisc", "replace", "dev", "out", "root", "fq", ns=s)
        netem(p, "left", "delay", f"{delay}ms", "limit", "50000")


def ping_min(flow):
    output = command("ping", "-n", "-c", "5", "-i", "0.12", "-W", "2",
                     f"10.73.{flow}.2", ns=f"s{flow}")
    samples = [float(x) for x in re.findall(r"time=([0-9.]+)", output)]
    if len(samples) != 5:
        raise ValueError("Incomplete RTT calibration: " + output)
    return min(samples), samples


def cpu_snapshot():
    return {p[0]: list(map(int, p[1:])) for line in Path("/proc/stat").read_text().splitlines()
            if (p := line.split()) and re.fullmatch(r"cpu\d+", p[0])}


def cpu_usage(before, after):
    values = []
    for core, old in before.items():
        delta = [b-a for a, b in zip(old[:8], after[core][:8])]
        total = sum(delta)
        values.append({"core": core, "busy_percent": 100*(total-delta[3]-delta[4])/total,
                       "steal_percent": 100*delta[7]/total})
    return values


def spawn(ns, arguments, output):
    return subprocess.Popen(["ip", "netns", "exec", PREFIX+ns, *map(str, arguments)],
                            stdout=output, stderr=subprocess.STDOUT, start_new_session=True)


def summarize_receiver(document, warmup, duration):
    if document.get("error"):
        raise ValueError(document["error"])
    points = []
    for interval in document["intervals"]:
        total = interval["sum"]
        if total.get("sender") is True:
            raise ValueError("Sender intervals cannot stand in for receiver goodput")
        points.append({"start_seconds": total["start"], "end_seconds": total["end"],
                       "seconds": total["seconds"], "bytes": total["bytes"],
                       "goodput_mbps": total["bytes"]*8/total["seconds"]/1e6})
    # Whole intervals only; do not invent sub-interval byte arrival times.
    steady = [p for p in points if p["start_seconds"] >= warmup - 0.05
              and p["end_seconds"] <= duration + 0.05 and p["seconds"] > 0.5]
    seconds = sum(p["seconds"] for p in steady)
    if seconds < duration-warmup-2.1:
        raise ValueError(f"Incomplete steady-state receiver coverage: {seconds}s")
    return {"goodput_mbps": sum(p["bytes"] for p in steady)*8/seconds/1e6,
            "measured_seconds": seconds, "received_bytes": sum(p["bytes"] for p in steady),
            "points": points}


def run_trial(trial, directory):
    config = trial["config"]
    directory.mkdir(parents=True, exist_ok=False)
    result = {**trial, "started_at": datetime.now(timezone.utc).isoformat(), "status": "failed",
              "attempt_id": directory.parent.name,
              "kernel": os.uname().release, "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "flows": [], "validation_errors": []}
    children, handles = [], []
    try:
        available = command("sysctl", "-n", "net.ipv4.tcp_available_congestion_control").split()
        if any(cca not in available for cca in config["ccas"]):
            raise ValueError(f"Unavailable congestion control; available: {available}")
        topology(config)
        calibration = []
        for i, delay in enumerate(config["delays_ms"], 1):
            minimum, samples = ping_min(i)
            if abs(minimum-delay) > 2:
                raise ValueError(f"Base RTT {minimum}ms does not match configured RTT increment {delay}ms")
            added = max(0.0, config.get("target_rtt_ms", minimum)-minimum)
            if config.get("target_rtt_ms") is not None:
                netem("b", f"p{i}", "delay", f"{added:.3f}ms", "limit", "50000")
            effective, observed = ping_min(i)
            target = config.get("target_rtt_ms", minimum)
            if abs(effective-target) > 2:
                raise ValueError(f"Effective RTT {effective}ms differs from target {target}ms")
            calibration.append({"flow": i, "natural_min_rtt_ms": minimum, "added_ack_delay_ms": added,
                                "effective_min_rtt_ms": effective, "natural_samples_ms": samples,
                                "effective_samples_ms": observed})
        result["calibration"] = calibration
        result["iperf_version"] = command("iperf3", "--version").splitlines()[0]
        for i in range(1, len(config["ccas"])+1):
            handle = (directory/f"receiver-{i}.json").open("w")
            handles.append(handle)
            children.append(spawn(f"c{i}", ["iperf3", "-s", "-1", "-J", "-p", str(5200+i)], handle))
        time.sleep(0.4)
        cpu_before = cpu_snapshot()
        started = time.monotonic()
        for i in trial["start_order"]:
            handle = (directory/f"sender-{i}.json").open("w")
            handles.append(handle)
            children.append(spawn(f"s{i}", ["iperf3", "-c", f"10.73.{i}.2", "-p", str(5200+i),
                                          "-t", str(config["duration_seconds"]), "-i", "1", "-J",
                                          "-C", config["ccas"][i-1], "--connect-timeout", "5000"], handle))
        raw = (directory/"socket-queue-trace.jsonl").open("w")
        handles.append(raw)
        while any(p.poll() is None for p in children):
            elapsed = time.monotonic()-started
            if elapsed > config["duration_seconds"]+20:
                raise TimeoutError("Traffic generator exceeded its deadline")
            sample = {"elapsed_seconds": elapsed, "cpu": cpu_snapshot(), "sockets": {},
                      "queue": json.loads(command("tc", "-s", "-j", "qdisc", "show", "dev", "out", ns="b"))}
            for i in range(1, len(config["ccas"])+1):
                sample["sockets"][str(i)] = command("ss", "-tin", ns=f"s{i}")
            raw.write(json.dumps(sample)+"\n")
            raw.flush()
            time.sleep(max(0.01, started + int(elapsed)+1 - time.monotonic()))
        result["cpu"] = cpu_usage(cpu_before, cpu_snapshot())
        for p in children:
            if p.wait() != 0:
                raise RuntimeError("iperf3 failed; retain sender/receiver output for diagnosis")
        for handle in handles:
            handle.flush()
        for i, cca in enumerate(config["ccas"], 1):
            sender = json.loads((directory/f"sender-{i}.json").read_text())
            if sender.get("error"):
                raise ValueError(sender["error"])
            actual = sender.get("end", {}).get("sender_tcp_congestion")
            if actual != cca:
                raise ValueError(f"Requested CCA {cca}, observed {actual}")
            receiver = json.loads((directory/f"receiver-{i}.json").read_text())
            summary = summarize_receiver(receiver, config["warmup_seconds"], config["duration_seconds"])
            result["flows"].append({"flow": i, "cca": cca, "configured_base_rtt_ms": config["delays_ms"][i-1],
                                    **summary, "retransmits": sender["end"]["sum_sent"].get("retransmits")})
        if any(c["busy_percent"] > 85 or c["steal_percent"] > 2 for c in result["cpu"]):
            result["validation_errors"].append("CPU saturation or virtualization steal exceeds protocol threshold")
        result["status"] = "invalid" if result["validation_errors"] else "completed"
    except Exception as error:
        result["error"] = str(error)
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        for handle in handles:
            handle.close()
        cleanup()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        (directory/"result.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--bucket")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Namespace experiments require root on the isolated Linux worker")
    manifest = json.loads(args.manifest.read_text())
    if not 0 <= args.shard < args.shards:
        parser.error("Invalid shard index")
    trials = [t for t in manifest["trials"] if t["block_index"] % args.shards == args.shard]
    if args.limit is not None:
        trials = trials[:args.limit]
    s3 = None
    if args.bucket:
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
    for trial in trials:
        directory = args.output/trial["id"]
        if directory.exists():
            raise RuntimeError(f"Refusing to overwrite a prior attempt: {directory}")
        result = run_trial(trial, directory)
        if s3:
            for path in directory.iterdir():
                s3.upload_file(str(path), args.bucket, f"campaigns/{manifest['id']}/{directory.parent.name}/{trial['id']}/{path.name}")
        print(json.dumps({"trial": trial["id"], "status": result["status"], "error": result.get("error")}), flush=True)
        if result["status"] != "completed":
            raise RuntimeError("Worker halted after unsuccessful validation; evidence has been retained")


if __name__ == "__main__":
    main()
