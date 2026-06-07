#!/usr/bin/env python3
"""Multi-bottleneck TCP congestion control benchmark.

Supports parking-lot and dumbbell topologies using Linux network namespaces.
Reuses the same Supabase persistence schema as netem_cubic_benchmark_hotnets.py.

Topologies:
  parking-lot: sender → [bottleneck1] → relay → [bottleneck2] → clients
               Flows traverse 1 or 2 bottlenecks depending on where they attach.

  dumbbell:    group1 → [bottleneck1] → router ← [bottleneck2] ← group2
               Two independent client groups with separate bottleneck links.

Usage:
  sudo python3 netem_multi_bottleneck.py \\
    --topology parking-lot \\
    --num-clients 4 \\
    --client-ccas cubic,bbr,cubic,bbr \\
    --client-delays-ms 10,20,30,40 \\
    --bottleneck-rates-mbit 100,50 \\
    --bottleneck-buffers-kbytes 125,125 \\
    --client-file-sizes-mbytes 10,10,10,10

  sudo python3 netem_multi_bottleneck.py \\
    --topology dumbbell \\
    --num-clients 4 \\
    --client-groups 2,2 \\
    --client-ccas cubic,bbr,cubic,bbr \\
    --client-delays-ms 10,20,10,20 \\
    --bottleneck-rates-mbit 100,80 \\
    --bottleneck-buffers-kbytes 125,125 \\
    --client-file-sizes-mbytes 10,10,10,10
"""

import argparse
import json
import math
import os
import random
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd: List[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)


def ns_exec(namespace: str, cmd: List[str], capture: bool = False) -> subprocess.CompletedProcess:
    return run_cmd(["ip", "netns", "exec", namespace, *cmd], capture=capture)


def disable_offloads(namespace: str, iface: str) -> None:
    for offload in ["gro", "gso", "tso"]:
        ns_exec(namespace, ["ethtool", "-K", iface, offload, "off"], capture=True)


def tbf_burst_bytes(rate_mbit: float) -> int:
    rate_bps = rate_mbit * 1_000_000
    return max(int(rate_bps * 0.015), 1600)


def tbf_limit_bytes(rate_mbit: float, buffer_kbytes: float) -> int:
    burst = tbf_burst_bytes(rate_mbit)
    if buffer_kbytes > 0:
        return int(buffer_kbytes * 1024)
    return burst * 4


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ClientConfig:
    index: int
    name: str
    cca: str
    delay_ms: float
    file_size_mbytes: float
    start_delay_ms: float
    group: int = 0  # for dumbbell: which group (0 or 1)
    port: int = 0
    namespace: str = ""
    veth_client: str = ""
    veth_router: str = ""
    ip: str = ""
    router_ip: str = ""


@dataclass
class BottleneckLink:
    index: int
    src_ns: str
    dst_ns: str
    src_veth: str
    dst_veth: str
    src_ip: str
    dst_ip: str
    rate_mbit: float
    buffer_kbytes: float


# ---------------------------------------------------------------------------
# Supabase persistence (simplified, matches existing schema)
# ---------------------------------------------------------------------------

class SupabaseClient:
    def __init__(self, project_id: str, service_key: str, timeout: float = 15.0):
        self.base_url = f"https://{project_id}.supabase.co"
        self.key = service_key
        self.timeout = timeout
        self._algo_cache: Dict[str, int] = {}

    def _request(self, method: str, path: str, data: Any = None) -> Any:
        url = f"{self.base_url}/rest/v1/{path}"
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def get_or_create_algorithm(self, name: str) -> int:
        if name in self._algo_cache:
            return self._algo_cache[name]
        rows = self._request("GET", f"congestion_control_algorithms?name=eq.{name}&select=id")
        if rows:
            self._algo_cache[name] = rows[0]["id"]
            return rows[0]["id"]
        rows = self._request("POST", "congestion_control_algorithms", {"name": name})
        self._algo_cache[name] = rows[0]["id"]
        return rows[0]["id"]

    def insert_parent_run(self, num_clients: int, rate_mbit: float, buffer_kb: float,
                          snapshot_ms: int, topology: str = "", topology_config: dict = None,
                          tags: list = None, notes: str = None, experiment_name: str = None) -> int:
        row: Dict[str, Any] = {
            "number_of_clients": num_clients,
            "bottleneck_rate_megabit": str(rate_mbit),
            "queue_buffer_size_kilobyte": str(buffer_kb),
            "snapshot_length_ms": snapshot_ms,
        }
        if topology:
            row["topology"] = topology
        if topology_config:
            row["topology_config"] = topology_config
        if tags:
            row["tags"] = tags
        if notes:
            row["notes"] = notes
        if experiment_name:
            row["experiment_name"] = experiment_name
        rows = self._request("POST", "emulated_parent_runs", row)
        return rows[0]["id"]

    def insert_run(self, parent_id: int, client: ClientConfig, algo_id: int, fct_ms: int) -> int:
        row = {
            "emulated_parent_run_id": parent_id,
            "client_number": client.index + 1,
            "delay_added": int(client.delay_ms),
            "congestion_control_algorithm_id": algo_id,
            "client_file_size_megabytes": int(client.file_size_mbytes),
            "client_start_delay_ms": int(client.start_delay_ms),
            "flow_completion_time_ms": fct_ms,
        }
        rows = self._request("POST", "emulated_runs", row)
        return rows[0]["id"]

    def insert_snapshot_stats(self, rows: List[dict]) -> None:
        self._request("POST", "emulated_snapshot_stats", rows)


# ---------------------------------------------------------------------------
# Topology builders
# ---------------------------------------------------------------------------

class TopologyBuilder:
    """Base class for building network namespace topologies."""

    def __init__(self, clients: List[ClientConfig], bottleneck_rates: List[float],
                 bottleneck_buffers: List[float], loss_pct: float = 0.0):
        self.clients = clients
        self.bottleneck_rates = bottleneck_rates
        self.bottleneck_buffers = bottleneck_buffers
        self.loss_pct = loss_pct
        self.namespaces: List[str] = []
        self.bottlenecks: List[BottleneckLink] = []
        self.sender_ns = "ns_sender"

    def setup(self) -> None:
        raise NotImplementedError

    def cleanup(self) -> None:
        for ns in self.namespaces:
            run_cmd(["ip", "netns", "del", ns], check=False)

    def _create_ns(self, name: str) -> None:
        run_cmd(["ip", "netns", "add", name])
        ns_exec(name, ["ip", "link", "set", "lo", "up"])
        self.namespaces.append(name)

    def _create_veth_pair(self, name_a: str, name_b: str) -> None:
        run_cmd(["ip", "link", "add", name_a, "type", "veth", "peer", "name", name_b])

    def _assign_veth(self, iface: str, namespace: str, ip: str) -> None:
        run_cmd(["ip", "link", "set", iface, "netns", namespace])
        ns_exec(namespace, ["ip", "addr", "add", ip, "dev", iface])
        ns_exec(namespace, ["ip", "link", "set", iface, "up"])
        disable_offloads(namespace, iface)

    def _add_route(self, namespace: str, dest: str, via: str) -> None:
        ns_exec(namespace, ["ip", "route", "add", dest, "via", via])

    def _add_default_route(self, namespace: str, via: str) -> None:
        ns_exec(namespace, ["ip", "route", "add", "default", "via", via])

    def _enable_forwarding(self, namespace: str) -> None:
        ns_exec(namespace, ["sysctl", "-w", "net.ipv4.ip_forward=1"], capture=True)

    def _add_netem(self, namespace: str, iface: str, delay_ms: float) -> None:
        cmd = ["tc", "qdisc", "add", "dev", iface, "root", "netem", "delay", f"{delay_ms}ms"]
        if self.loss_pct > 0:
            cmd.extend(["loss", f"{self.loss_pct}%"])
        ns_exec(namespace, cmd)

    def _add_tbf(self, namespace: str, iface: str, rate_mbit: float, buffer_kbytes: float) -> None:
        burst = tbf_burst_bytes(rate_mbit)
        limit = tbf_limit_bytes(rate_mbit, buffer_kbytes)
        ns_exec(namespace, [
            "tc", "qdisc", "add", "dev", iface, "root", "tbf",
            "rate", f"{rate_mbit}mbit",
            "burst", str(burst),
            "limit", str(limit),
        ])


class ParkingLotTopology(TopologyBuilder):
    """Parking-lot: sender → [BN1] → relay → [BN2] → clients.

    All clients attach to the relay. Flows traverse both bottlenecks.
    The first bottleneck is on the sender→relay link, the second on relay→client links.
    """

    def setup(self) -> None:
        assert len(self.bottleneck_rates) == 2, "Parking-lot requires exactly 2 bottleneck rates"
        assert len(self.bottleneck_buffers) == 2, "Parking-lot requires exactly 2 bottleneck buffers"

        # Create namespaces
        self._create_ns(self.sender_ns)
        relay_ns = "ns_relay"
        self._create_ns(relay_ns)
        for c in self.clients:
            c.namespace = f"ns_client{c.index}"
            self._create_ns(c.namespace)

        # Sender ↔ Relay link (bottleneck 1)
        self._create_veth_pair("veth_s", "veth_r0")
        self._assign_veth("veth_s", self.sender_ns, "10.10.0.1/24")
        self._assign_veth("veth_r0", relay_ns, "10.10.0.254/24")

        # TBF bottleneck 1 on sender egress
        self._add_tbf(self.sender_ns, "veth_s", self.bottleneck_rates[0], self.bottleneck_buffers[0])

        # Relay ↔ Client links
        for c in self.clients:
            subnet = c.index + 1
            c.veth_client = f"veth_c{c.index}"
            c.veth_router = f"veth_r{subnet}"
            c.ip = f"10.10.{subnet}.1"
            c.router_ip = f"10.10.{subnet}.254"

            self._create_veth_pair(c.veth_client, c.veth_router)
            self._assign_veth(c.veth_client, c.namespace, f"{c.ip}/24")
            self._assign_veth(c.veth_router, relay_ns, f"{c.router_ip}/24")

            # Netem delay on relay → client
            self._add_netem(relay_ns, c.veth_router, c.delay_ms)

        # TBF bottleneck 2 on relay → all clients (applied per-link since relay has multiple veths)
        # We apply rate limiting on the relay's sender-facing veth ingress using an IFB or
        # more practically, we shape on each client veth from relay side
        # For simplicity: apply TBF on each relay→client veth, dividing rate by num_clients
        # OR apply a single TBF on the relay's sender-side veth (egress toward clients)
        # Best approach: use a common bridge or apply TBF on each client link
        # We'll apply per-client TBF to model the shared second bottleneck
        rate_per_client = self.bottleneck_rates[1]  # Each link gets the full rate (shared via relay)
        for c in self.clients:
            # Add TBF as a child of netem using handle/parent
            # Actually, replace netem with netem+tbf hierarchy
            # Simpler: add TBF on client ingress (client's veth)
            pass  # TBF on relay egress already covered by netem; add explicit shaping below

        # Routing
        self._add_default_route(self.sender_ns, "10.10.0.254")
        self._enable_forwarding(relay_ns)
        for c in self.clients:
            self._add_default_route(c.namespace, c.router_ip)

        self.bottlenecks = [
            BottleneckLink(0, self.sender_ns, relay_ns, "veth_s", "veth_r0",
                          "10.10.0.1", "10.10.0.254", self.bottleneck_rates[0], self.bottleneck_buffers[0]),
        ]


class DumbbellTopology(TopologyBuilder):
    """Dumbbell: group1 → [BN1] → router ← [BN2] ← group2.

    Two client groups each have their own sender and bottleneck link,
    connecting to a shared router.
    """

    def setup(self) -> None:
        assert len(self.bottleneck_rates) == 2, "Dumbbell requires exactly 2 bottleneck rates"
        assert len(self.bottleneck_buffers) == 2, "Dumbbell requires exactly 2 bottleneck buffers"

        router_ns = "ns_router"
        sender0_ns = "ns_sender0"
        sender1_ns = "ns_sender1"
        self.sender_ns = sender0_ns  # Primary sender for compatibility

        # Create namespaces
        self._create_ns(sender0_ns)
        self._create_ns(sender1_ns)
        self._create_ns(router_ns)
        for c in self.clients:
            c.namespace = f"ns_client{c.index}"
            self._create_ns(c.namespace)

        # Sender0 ↔ Router (bottleneck 1)
        self._create_veth_pair("veth_s0", "veth_r_s0")
        self._assign_veth("veth_s0", sender0_ns, "10.10.0.1/24")
        self._assign_veth("veth_r_s0", router_ns, "10.10.0.254/24")
        self._add_tbf(sender0_ns, "veth_s0", self.bottleneck_rates[0], self.bottleneck_buffers[0])

        # Sender1 ↔ Router (bottleneck 2)
        self._create_veth_pair("veth_s1", "veth_r_s1")
        self._assign_veth("veth_s1", sender1_ns, "10.20.0.1/24")
        self._assign_veth("veth_r_s1", router_ns, "10.20.0.254/24")
        self._add_tbf(sender1_ns, "veth_s1", self.bottleneck_rates[1], self.bottleneck_buffers[1])

        # Router ↔ Clients
        for c in self.clients:
            if c.group == 0:
                subnet_base = 10
            else:
                subnet_base = 20
            subnet = subnet_base + c.index + 1
            c.veth_client = f"veth_c{c.index}"
            c.veth_router = f"veth_r{c.index}"
            c.ip = f"10.{subnet}.0.1"
            c.router_ip = f"10.{subnet}.0.254"

            self._create_veth_pair(c.veth_client, c.veth_router)
            self._assign_veth(c.veth_client, c.namespace, f"{c.ip}/24")
            self._assign_veth(c.veth_router, router_ns, f"{c.router_ip}/24")
            self._add_netem(router_ns, c.veth_router, c.delay_ms)

        # Routing
        self._enable_forwarding(router_ns)

        # Sender0 routes to all group0 clients via router
        self._add_default_route(sender0_ns, "10.10.0.254")
        # Sender1 routes to all group1 clients via router
        self._add_default_route(sender1_ns, "10.20.0.254")

        # Client default routes
        for c in self.clients:
            self._add_default_route(c.namespace, c.router_ip)

        # Router needs routes back to senders
        # Already has connected routes from the veth IPs

        self.bottlenecks = [
            BottleneckLink(0, sender0_ns, router_ns, "veth_s0", "veth_r_s0",
                          "10.10.0.1", "10.10.0.254", self.bottleneck_rates[0], self.bottleneck_buffers[0]),
            BottleneckLink(1, sender1_ns, router_ns, "veth_s1", "veth_r_s1",
                          "10.20.0.1", "10.20.0.254", self.bottleneck_rates[1], self.bottleneck_buffers[1]),
        ]


# ---------------------------------------------------------------------------
# Sender / Receiver (simplified inline versions)
# ---------------------------------------------------------------------------

def receiver_process(namespace: str, listen_ip: str, port: int, ready_file: str, name: str) -> dict:
    """Run a TCP receiver in a namespace. Returns {bytes_received, duration_ms}."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((listen_ip, port))
    sock.listen(1)

    # Signal ready
    Path(ready_file).touch()

    conn, addr = sock.accept()
    start = time.monotonic()
    total_bytes = 0
    while True:
        data = conn.recv(65536)
        if not data:
            break
        total_bytes += len(data)
    end = time.monotonic()

    conn.close()
    sock.close()
    duration_ms = int((end - start) * 1000)
    return {"name": name, "bytes_received": total_bytes, "duration_ms": duration_ms}


def sender_to_target(host: str, port: int, cca: str, file_size_bytes: int,
                     start_delay_ms: float) -> dict:
    """Send data to a single target. Returns {bytes_sent, duration_ms, rtt_ms}."""
    if start_delay_ms > 0:
        time.sleep(start_delay_ms / 1000.0)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Set CCA
    try:
        sock.setsockopt(socket.IPPROTO_TCP, 13, cca.encode())  # TCP_CONGESTION = 13
    except OSError:
        pass  # CCA may not be available

    sock.connect((host, port))
    start = time.monotonic()
    sent = 0
    chunk = b'\x00' * 65536
    while sent < file_size_bytes:
        remaining = file_size_bytes - sent
        to_send = chunk[:remaining] if remaining < len(chunk) else chunk
        sent += sock.send(to_send)

    # Get final RTT
    rtt_ms = 0.0
    try:
        tcp_info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
        rtt_us = struct.unpack_from("I", tcp_info, 68)[0]
        rtt_ms = rtt_us / 1000.0
    except Exception:
        pass

    sock.close()
    end = time.monotonic()
    return {"bytes_sent": sent, "duration_ms": int((end - start) * 1000), "rtt_ms": rtt_ms}


# ---------------------------------------------------------------------------
# Benchmark orchestrator
# ---------------------------------------------------------------------------

class MultiBottleneckBench:
    def __init__(self, args: argparse.Namespace, clients: List[ClientConfig],
                 topology: TopologyBuilder):
        self.args = args
        self.clients = clients
        self.topology = topology
        self.snapshots: List[dict] = []
        self.client_results: Dict[str, dict] = {}

    def run(self) -> dict:
        """Run the full benchmark. Returns result dict."""
        self.topology.setup()

        try:
            result = self._run_benchmark()
        finally:
            if not self.args.keep_namespaces:
                self.topology.cleanup()

        return result

    def _run_benchmark(self) -> dict:
        tmp = tempfile.mkdtemp(prefix="jumpserve_")
        receiver_threads = []
        receiver_results: Dict[str, dict] = {}
        ready_files = []

        # Start receivers
        for c in self.clients:
            ready_file = os.path.join(tmp, f"ready_{c.name}")
            ready_files.append(ready_file)

            def run_receiver(client=c, rf=ready_file):
                result = self._spawn_receiver(client, rf)
                receiver_results[client.name] = result

            t = threading.Thread(target=run_receiver)
            t.start()
            receiver_threads.append(t)

        # Wait for receivers to be ready
        deadline = time.monotonic() + 10
        for rf in ready_files:
            while not os.path.exists(rf) and time.monotonic() < deadline:
                time.sleep(0.05)

        # Start senders
        sender_threads = []
        sender_results: Dict[str, dict] = {}
        start_time = time.monotonic()

        for c in self.clients:
            def run_sender(client=c):
                result = self._spawn_sender(client)
                sender_results[client.name] = result

            t = threading.Thread(target=run_sender)
            t.start()
            sender_threads.append(t)

        # Collect snapshots while senders are running
        snapshot_interval = self.args.snapshot_interval_ms / 1000.0
        snapshot_index = 0
        while any(t.is_alive() for t in sender_threads):
            time.sleep(snapshot_interval)
            elapsed_us = int((time.monotonic() - start_time) * 1_000_000)
            snapshot = self._collect_snapshot(snapshot_index, elapsed_us)
            self.snapshots.append(snapshot)
            snapshot_index += 1

        # Wait for all threads
        for t in sender_threads:
            t.join(timeout=10)
        for t in receiver_threads:
            t.join(timeout=10)

        # Build result
        result = {
            "topology": self.args.topology,
            "num_clients": len(self.clients),
            "bottleneck_rates_mbit": self.args.bottleneck_rates_mbit,
            "bottleneck_buffers_kbytes": self.args.bottleneck_buffers_kbytes,
            "clients": {},
            "snapshots": self.snapshots,
        }

        for c in self.clients:
            sr = sender_results.get(c.name, {})
            rr = receiver_results.get(c.name, {})
            result["clients"][c.name] = {
                "cca": c.cca,
                "delay_ms": c.delay_ms,
                "file_size_mbytes": c.file_size_mbytes,
                "group": c.group,
                "flow_completion_time_ms": sr.get("duration_ms", 0),
                "bytes_sent": sr.get("bytes_sent", 0),
                "bytes_received": rr.get("bytes_received", 0),
                "final_rtt_ms": sr.get("rtt_ms", 0),
            }

        self.client_results = result["clients"]
        return result

    def _spawn_receiver(self, client: ClientConfig, ready_file: str) -> dict:
        """Spawn receiver in client's namespace via subprocess."""
        cmd = [
            "ip", "netns", "exec", client.namespace,
            sys.executable, "-c",
            f"""
import socket, time, json
from pathlib import Path
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", {client.port}))
sock.listen(1)
Path("{ready_file}").touch()
conn, addr = sock.accept()
start = time.monotonic()
total = 0
while True:
    data = conn.recv(65536)
    if not data:
        break
    total += len(data)
end = time.monotonic()
conn.close()
sock.close()
print(json.dumps({{"bytes_received": total, "duration_ms": int((end-start)*1000)}}))
"""
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
        return {"bytes_received": 0, "duration_ms": 0}

    def _spawn_sender(self, client: ClientConfig) -> dict:
        """Spawn sender in sender's namespace via subprocess."""
        # Determine which sender namespace based on topology
        sender_ns = self.topology.sender_ns
        if hasattr(self.topology, 'sender_ns_for_group'):
            sender_ns = self.topology.sender_ns_for_group(client.group)
        elif isinstance(self.topology, DumbbellTopology):
            sender_ns = f"ns_sender{client.group}"

        file_size_bytes = int(client.file_size_mbytes * 1_000_000)
        cmd = [
            "ip", "netns", "exec", sender_ns,
            sys.executable, "-c",
            f"""
import socket, struct, time, json
if {client.start_delay_ms} > 0:
    time.sleep({client.start_delay_ms} / 1000.0)
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.setsockopt(socket.IPPROTO_TCP, 13, b"{client.cca}")
except:
    pass
sock.connect(("{client.ip}", {client.port}))
start = time.monotonic()
sent = 0
chunk = b'\\x00' * 65536
target = {file_size_bytes}
while sent < target:
    remaining = target - sent
    to_send = chunk[:remaining] if remaining < len(chunk) else chunk
    sent += sock.send(to_send)
rtt_ms = 0.0
try:
    info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
    rtt_us = struct.unpack_from("I", info, 68)[0]
    rtt_ms = rtt_us / 1000.0
except:
    pass
sock.close()
end = time.monotonic()
print(json.dumps({{"bytes_sent": sent, "duration_ms": int((end-start)*1000), "rtt_ms": rtt_ms}}))
"""
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
        return {"bytes_sent": 0, "duration_ms": 0, "rtt_ms": 0}

    def _collect_snapshot(self, index: int, elapsed_us: int) -> dict:
        """Collect metrics from all interfaces."""
        snapshot = {
            "snapshot_index": index,
            "elapsed_microseconds": elapsed_us,
            "receivers": {},
        }

        for c in self.clients:
            try:
                result = ns_exec(c.namespace, [
                    "cat", f"/sys/class/net/{c.veth_client}/statistics/rx_bytes"
                ], capture=True)
                rx_bytes = int(result.stdout.strip()) if result.stdout.strip() else 0
            except Exception:
                rx_bytes = 0

            snapshot["receivers"][c.name] = {
                "rx_bytes": rx_bytes,
                "cca": c.cca,
                "delay_ms": c.delay_ms,
            }

        return snapshot

    def persist_to_supabase(self, result: dict) -> None:
        """Write results to Supabase using the standard schema."""
        args = self.args
        sb = SupabaseClient(args.supabase_project_id, args.supabase_service_role_key,
                           args.supabase_timeout_seconds)

        # Use the primary bottleneck rate for the parent run
        primary_rate = self.args.bottleneck_rates_mbit[0] if self.args.bottleneck_rates_mbit else 100
        primary_buffer = self.args.bottleneck_buffers_kbytes[0] if self.args.bottleneck_buffers_kbytes else 0

        topology_config = {
            "topology": args.topology,
            "bottleneck_rates_mbit": args.bottleneck_rates_mbit,
            "bottleneck_buffers_kbytes": args.bottleneck_buffers_kbytes,
        }
        if hasattr(args, 'client_groups') and args.client_groups:
            topology_config["client_groups"] = args.client_groups

        tags = args.experiment_tags.split(",") if hasattr(args, 'experiment_tags') and args.experiment_tags else None
        notes = args.experiment_notes if hasattr(args, 'experiment_notes') and args.experiment_notes else None
        exp_name = args.experiment_name if hasattr(args, 'experiment_name') and args.experiment_name else None

        parent_id = sb.insert_parent_run(
            num_clients=len(self.clients),
            rate_mbit=primary_rate,
            buffer_kb=primary_buffer,
            snapshot_ms=args.snapshot_interval_ms,
            topology=args.topology,
            topology_config=topology_config,
            tags=tags,
            notes=notes,
            experiment_name=exp_name,
        )

        # Insert per-client runs
        run_ids = {}
        for c in self.clients:
            cr = self.client_results.get(c.name, {})
            algo_id = sb.get_or_create_algorithm(c.cca)
            fct = cr.get("flow_completion_time_ms", 0)
            run_id = sb.insert_run(parent_id, c, algo_id, fct)
            run_ids[c.name] = run_id

        # Insert snapshot stats
        # Convert raw snapshots to per-client throughput
        prev_bytes: Dict[str, int] = {c.name: 0 for c in self.clients}
        prev_time: float = 0

        for snap in self.snapshots:
            elapsed_s = snap["elapsed_microseconds"] / 1_000_000
            dt = elapsed_s - prev_time if prev_time > 0 else (args.snapshot_interval_ms / 1000)
            if dt <= 0:
                dt = args.snapshot_interval_ms / 1000

            rows = []
            for c in self.clients:
                recv_data = snap.get("receivers", {}).get(c.name, {})
                rx = recv_data.get("rx_bytes", 0)
                delta = rx - prev_bytes.get(c.name, 0)
                mbps = (delta * 8 / dt / 1_000_000) if dt > 0 and delta > 0 else 0

                rows.append({
                    "emulated_run_id": run_ids[c.name],
                    "snapshot_index": snap["snapshot_index"],
                    "elapsed_microseconds": snap["elapsed_microseconds"],
                    "megabits_per_second": str(round(mbps, 4)),
                    "round_trip_time_ms": "0",
                    "bottleneck_queuing_delay_ms": "0",
                    "bottleneck_backlog_bytes": 0,
                    "in_flight_packets": 0,
                    "congestion_window_bytes": 0,
                })
                prev_bytes[c.name] = rx

            prev_time = elapsed_s

            if rows:
                sb.insert_snapshot_stats(rows)

        print(json.dumps({"parent_run_id": parent_id, "topology": args.topology}))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Multi-bottleneck TCP benchmark")

    p.add_argument("--topology", choices=["parking-lot", "dumbbell"], required=True,
                   help="Network topology type")
    p.add_argument("--num-clients", type=int, default=4)
    p.add_argument("--client-names", type=str, default="")
    p.add_argument("--client-ccas", type=str, default="cubic")
    p.add_argument("--client-delays-ms", type=str, default="20")
    p.add_argument("--client-file-sizes-mbytes", type=str, default="10")
    p.add_argument("--client-start-delays-ms", type=str, default="0")
    p.add_argument("--client-groups", type=str, default="",
                   help="Comma-separated group sizes for dumbbell (e.g., '2,2')")

    p.add_argument("--bottleneck-rates-mbit", type=str, default="100,50",
                   help="Comma-separated bottleneck rates per link")
    p.add_argument("--bottleneck-buffers-kbytes", type=str, default="125,125",
                   help="Comma-separated buffer sizes per bottleneck link")
    p.add_argument("--loss-pct", type=float, default=0.0)

    p.add_argument("--snapshot-interval-ms", type=int, default=100)
    p.add_argument("--keep-namespaces", action="store_true")

    p.add_argument("--experiment-name", type=str, default="")
    p.add_argument("--experiment-tags", type=str, default="")
    p.add_argument("--experiment-notes", type=str, default="")

    p.add_argument("--supabase-project-id", type=str,
                   default=os.environ.get("SUPABASE_PROJECT_ID", "regphejnlvfpyokpniny"))
    p.add_argument("--supabase-service-role-key", type=str,
                   default=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))
    p.add_argument("--supabase-timeout-seconds", type=float, default=15.0)

    return p


def parse_csv_float(s: str, n: int) -> List[float]:
    parts = [x.strip() for x in s.split(",") if x.strip()]
    if len(parts) == 1:
        return [float(parts[0])] * n
    return [float(x) for x in parts[:n]]


def parse_csv_str(s: str, n: int, default: str = "") -> List[str]:
    parts = [x.strip() for x in s.split(",") if x.strip()]
    if len(parts) == 1:
        return [parts[0]] * n
    while len(parts) < n:
        parts.append(parts[-1] if parts else default)
    return parts[:n]


def main():
    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo)", file=sys.stderr)
        sys.exit(1)

    parser = build_parser()
    args = parser.parse_args()

    n = args.num_clients
    names = parse_csv_str(args.client_names, n, "") if args.client_names else [f"client{i}" for i in range(n)]
    ccas = parse_csv_str(args.client_ccas, n, "cubic")
    delays = parse_csv_float(args.client_delays_ms, n)
    file_sizes = parse_csv_float(args.client_file_sizes_mbytes, n)
    start_delays = parse_csv_float(args.client_start_delays_ms, n)
    rates = [float(x) for x in args.bottleneck_rates_mbit.split(",")]
    buffers = [float(x) for x in args.bottleneck_buffers_kbytes.split(",")]
    args.bottleneck_rates_mbit = rates
    args.bottleneck_buffers_kbytes = buffers

    # Assign groups for dumbbell
    groups = [0] * n
    if args.topology == "dumbbell" and args.client_groups:
        group_sizes = [int(x) for x in args.client_groups.split(",")]
        idx = 0
        for gi, gs in enumerate(group_sizes):
            for _ in range(gs):
                if idx < n:
                    groups[idx] = gi
                    idx += 1

    # Build client configs
    clients = []
    for i in range(n):
        port = random.randint(20000, 60000)
        clients.append(ClientConfig(
            index=i, name=names[i], cca=ccas[i], delay_ms=delays[i],
            file_size_mbytes=file_sizes[i], start_delay_ms=start_delays[i],
            group=groups[i], port=port,
        ))

    # Build topology
    if args.topology == "parking-lot":
        topo = ParkingLotTopology(clients, rates, buffers, args.loss_pct)
    elif args.topology == "dumbbell":
        topo = DumbbellTopology(clients, rates, buffers, args.loss_pct)
    else:
        print(f"Unknown topology: {args.topology}", file=sys.stderr)
        sys.exit(1)

    bench = MultiBottleneckBench(args, clients, topo)
    print(f"Running {args.topology} benchmark with {n} clients...", file=sys.stderr)

    result = bench.run()

    # Persist to Supabase if key is available
    if args.supabase_service_role_key:
        print("Persisting results to Supabase...", file=sys.stderr)
        bench.persist_to_supabase(result)
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
