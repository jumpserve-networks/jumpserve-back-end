#!/usr/bin/env python3
"""Validate direction, shaping and CCA identity before a worker runs research."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import statistics
import struct
import subprocess
import sys
import time

import runner


def echo_server(address):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((address, 5900))
    sock.settimeout(12)
    while True:
        packet, peer = sock.recvfrom(128)
        received = time.monotonic_ns()
        sock.sendto(packet + struct.pack("!Q", received), peer)


def echo_client(address):
    # All namespaces share the host's monotonic clock. No inter-host clock inference.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    start = time.monotonic()
    sent, points = 0, []
    while time.monotonic()-start < 4 and len(points) < 1000:
        now = time.monotonic()
        if sent < 1000 and now >= start+sent*0.001:
            sock.sendto(struct.pack("!QQ", sent, time.monotonic_ns()), (address, 5900))
            sent += 1
        ready, _, _ = select.select([sock], [], [], 0.0002)
        if ready:
            packet, _ = sock.recvfrom(128)
            arrived = time.monotonic_ns()
            index, departed, received = struct.unpack("!QQQ", packet)
            points.append({"index": index, "forward_ms": (received-departed)/1e6,
                           "rtt_ms": (arrived-departed)/1e6})
    print(json.dumps({"sent": sent, "received": len(points), "points": points}))


def direction_probe(output):
    records = []
    config = {"ccas": ["bbr"], "delays_ms": [3], "capacity_mbps": 200, "queue_packets": 8256}
    for treatment in ("baseline", "ack_plus_60", "forward_plus_60"):
        child = None
        try:
            runner.topology(config)
            if treatment == "ack_plus_60":
                runner.netem("b", "p1", "delay", "60ms", "limit", "50000")
            if treatment == "forward_plus_60":
                runner.netem("p1", "right", "delay", "60ms", "limit", "50000")
            # Prime ARP separately so neighbor discovery is not an intervention effect.
            runner.ping_min(1)
            child = runner.spawn("c1", [sys.executable, __file__, "--echo-server", "10.73.1.2"], subprocess.DEVNULL)
            time.sleep(0.15)
            raw = runner.command(sys.executable, __file__, "--echo-client", "10.73.1.2", ns="s1")
            data = json.loads(raw)
            if data["received"] < 990:
                raise ValueError("Direction probe lost more than 1% of datagrams")
            records.append({"treatment": treatment, **data,
                            "median_forward_ms": statistics.median(p["forward_ms"] for p in data["points"]),
                            "median_rtt_ms": statistics.median(p["rtt_ms"] for p in data["points"])})
        finally:
            if child:
                child.kill()
                child.wait()
            runner.cleanup()
    base, ack, forward = records
    checks = {
        "ack_keeps_forward_latency": abs(ack["median_forward_ms"]-base["median_forward_ms"]) < 2,
        "ack_increases_rtt_60ms": abs(ack["median_rtt_ms"]-base["median_rtt_ms"]-60) < 2,
        "forward_increases_forward_60ms": abs(forward["median_forward_ms"]-base["median_forward_ms"]-60) < 2,
        "forward_increases_rtt_60ms": abs(forward["median_rtt_ms"]-base["median_rtt_ms"]-60) < 2,
    }
    (output/"direction.json").write_text(json.dumps({"checks": checks, "records": records})+"\n")
    if not all(checks.values()):
        raise ValueError(f"Direction validation failed: {checks}")
    return checks


def preflight(output, expected_cpus):
    output.mkdir(parents=True, exist_ok=False)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "failed",
              "kernel": os.uname().release, "cpu_count": os.cpu_count(), "checks": {}}
    try:
        if os.cpu_count() != expected_cpus:
            raise ValueError(f"Expected {expected_cpus} online CPUs, found {os.cpu_count()}")
        if not os.uname().release.startswith("6.13.7-jumpserve-nines2026"):
            raise ValueError("Incorrect research kernel")
        report["checks"].update(direction_probe(output))
        for cca in ("bbr", "bbr1"):
            trial = {"id": f"capacity-{cca}", "start_order": [1], "block_index": -1, "replicate": 1,
                     "config": {"family": "preflight", "treatment": "capacity", "ccas": [cca],
                                "delays_ms": [30], "capacity_mbps": 200, "queue_packets": 8256,
                                "duration_seconds": 15, "warmup_seconds": 5}}
            result = runner.run_trial(trial, output/cca)
            if result["status"] != "completed":
                raise ValueError(f"{cca} preflight: {result.get('error') or result.get('validation_errors')}")
            rate = result["flows"][0]["goodput_mbps"]
            report["checks"][f"{cca}_capacity_mbps"] = rate
            if not 170 <= rate <= 202:
                raise ValueError(f"Unexpected single-flow capacity: {rate} Mbps")
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["runner_sha256"] = hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest()
    (output/"preflight.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report), flush=True)
    return report["status"] == "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--echo-server")
    parser.add_argument("--echo-client")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-cpus", type=int, default=2)
    args = parser.parse_args()
    if args.echo_server:
        echo_server(args.echo_server)
    elif args.echo_client:
        echo_client(args.echo_client)
    elif args.output:
        sys.exit(0 if preflight(args.output, args.expected_cpus) else 1)
    else:
        parser.error("Supply --output")
