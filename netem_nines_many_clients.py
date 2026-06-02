#!/usr/bin/env python3
"""
Benchmark two sender flows over the ProxyDelay `nsperf_two_flows` topology.

Linux mode:
- Builds a routed topology with network namespaces and veth pairs:
  sender_a <-> sender_router_a <-> core_router <-> mid <-> client_router <-> client
  sender_b <-> sender_router_b <-> core_router <-> mid <-> client_router <-> client
- Applies per-flow netem delay/loss on each sender-router link that faces a sender.
- Applies a shared rate bottleneck on the client-router -> client path.
- Uses per-flow TCP congestion control (CCA) at sender socket level.
- Supports per-flow start delay and per-flow transfer size.
- Runs two receiver processes inside one shared client namespace.
- Prints synchronized JSON rate snapshots at a fixed interval.

macOS mode:
- Exits with guidance. Linux networking features (ip netns/tc) are required.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import netem_cubic_benchmark_nines as base


def resolve_client_run_configs(args: argparse.Namespace) -> List[base.ClientRunConfig]:
    if args.num_clients != 2:
        raise ValueError("netem_nines.py requires --num-clients=2 to match the ProxyDelay two-flow topology.")

    names = base.parse_csv_arg(args.client_names, "--client-names")
    if names:
        if len(names) != 2:
            raise ValueError("--client-names must include exactly two entries.")
    else:
        names = ["client1", "client2"]

    for name in names:
        if ":" in name or "," in name:
            raise ValueError("Client names cannot contain ':' or ','.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Client names must match [A-Za-z0-9_-]+.")
        if name == "total_megabits_per_second":
            raise ValueError("'total_megabits_per_second' is a reserved client name.")
    if len(set(names)) != 2:
        raise ValueError("Client names must be unique.")

    delays = base.parse_float_csv_arg(args.client_delays_ms, "--client-delays-ms")
    if delays and len(delays) != 2:
        raise ValueError("--client-delays-ms must include exactly two entries.")
    delays = base._list_or_default(delays, [args.client1_delay_ms, args.client2_delay_ms], 2)
    if any(delay < 0 for delay in delays):
        raise ValueError("All client delays must be >= 0.")

    ccas = base.parse_csv_arg(args.client_ccas, "--client-ccas")
    if ccas and len(ccas) != 2:
        raise ValueError("--client-ccas must include exactly two entries.")
    ccas = base._list_or_default(ccas, [args.client1_cca, args.client2_cca], 2)

    start_delays = base.parse_float_csv_arg(args.client_start_delays_ms, "--client-start-delays-ms")
    if start_delays and len(start_delays) != 2:
        raise ValueError("--client-start-delays-ms must include exactly two entries.")
    start_delays = base._list_or_default(start_delays, [0.0, 0.0], 2)
    if any(delay < 0 for delay in start_delays):
        raise ValueError("All client start delays must be >= 0.")

    file_sizes = base.parse_float_csv_arg(args.client_file_sizes_mbytes, "--client-file-sizes-mbytes")
    if file_sizes and len(file_sizes) != 2:
        raise ValueError("--client-file-sizes-mbytes must include exactly two entries.")
    file_sizes = base._list_or_default(
        file_sizes,
        [base.DEFAULT_FILE_SIZE_TO_BE_TRANSFERRED_IN_MBYTES, base.DEFAULT_FILE_SIZE_TO_BE_TRANSFERRED_IN_MBYTES],
        2,
    )
    if any(size <= 0 for size in file_sizes):
        raise ValueError("All client file sizes must be > 0.")

    ports = base.choose_random_unique_ports(2)
    return [
        base.ClientRunConfig(
            index=index + 1,
            name=names[index],
            delay_ms=float(delays[index]),
            cca=ccas[index],
            port=ports[index],
            start_delay_ms=float(start_delays[index]),
            file_size_to_be_transferred_in_mbytes=float(file_sizes[index]),
        )
        for index in range(2)
    ]


class ProxyDelayTwoFlowBench:
    def __init__(self, args: argparse.Namespace, client_configs: List[base.ClientRunConfig]):
        if len(client_configs) != 2:
            raise ValueError("ProxyDelayTwoFlowBench requires exactly two flow configs.")

        self.args = args
        self.id = uuid.uuid4().hex[:8]

        self.ns_core = f"ns_srt_{self.id}"
        self.ns_mid = f"ns_mid_{self.id}"
        self.ns_client_router = f"ns_crt_{self.id}"
        self.ns_client = f"ns_cli_{self.id}"

        self.core_mid_veth = "veth_cm"
        self.mid_core_veth = "veth_mc"
        self.mid_client_router_veth = "veth_mr"
        self.client_router_mid_veth = "veth_rm"
        self.client_router_client_veth = "veth_rc"
        self.client_veth = "veth_cl"

        self.core_mid_ip = "192.168.1.1"
        self.mid_core_ip = "192.168.1.100"
        self.mid_client_router_ip = "192.168.2.1"
        self.client_router_mid_ip = "192.168.2.100"
        self.client_router_client_ip = "192.168.3.1"
        self.client_ip = "192.168.3.100"

        cfg_a, cfg_b = client_configs
        self.clients = [
            base.ClientRunConfig(
                index=cfg_a.index,
                name=cfg_a.name,
                delay_ms=cfg_a.delay_ms,
                cca=cfg_a.cca,
                port=cfg_a.port,
                start_delay_ms=cfg_a.start_delay_ms,
                file_size_to_be_transferred_in_mbytes=cfg_a.file_size_to_be_transferred_in_mbytes,
                sender_namespace=f"ns_srv_{self.id}",
                sender_veth="veth_sa",
                sender_router_namespace=f"ns_srta_{self.id}",
                sender_router_veth_sender="veth_al",
                sender_router_veth_switch="veth_ar",
                switch_veth_in="veth_ca",
                sender_ip="192.168.0.1",
                sender_router_sender_ip="192.168.0.100",
                sender_router_switch_ip="192.168.6.1",
                switch_in_ip="192.168.6.100",
                fanout_veth_out=self.client_router_client_veth,
                namespace=self.ns_client,
                veth_client=self.client_veth,
                veth_router=self.client_router_client_veth,
                client_ip=self.client_ip,
                router_ip=self.client_router_client_ip,
            ),
            base.ClientRunConfig(
                index=cfg_b.index,
                name=cfg_b.name,
                delay_ms=cfg_b.delay_ms,
                cca=cfg_b.cca,
                port=cfg_b.port,
                start_delay_ms=cfg_b.start_delay_ms,
                file_size_to_be_transferred_in_mbytes=cfg_b.file_size_to_be_transferred_in_mbytes,
                sender_namespace=f"ns_srvb_{self.id}",
                sender_veth="veth_sb",
                sender_router_namespace=f"ns_srtb_{self.id}",
                sender_router_veth_sender="veth_bl",
                sender_router_veth_switch="veth_br",
                switch_veth_in="veth_cb",
                sender_ip="192.168.4.1",
                sender_router_sender_ip="192.168.4.100",
                sender_router_switch_ip="192.168.5.1",
                switch_in_ip="192.168.5.100",
                fanout_veth_out=self.client_router_client_veth,
                namespace=self.ns_client,
                veth_client=self.client_veth,
                veth_router=self.client_router_client_veth,
                client_ip=self.client_ip,
                router_ip=self.client_router_client_ip,
            ),
        ]

        self._receiver_byte_files: Dict[str, str] = {}

    def _all_namespaces(self) -> List[str]:
        return [
            self.ns_client,
            self.ns_client_router,
            self.ns_mid,
            self.ns_core,
            self.clients[0].sender_namespace,
            self.clients[1].sender_namespace,
            self.clients[0].sender_router_namespace,
            self.clients[1].sender_router_namespace,
        ]

    def _ns(self, namespace: str, cmd: str) -> str:
        return base.ns_shell(namespace, cmd)

    def _read_bottleneck_qdisc_stats(self) -> Dict[str, int]:
        raw = base.run_and_grab_shell(
            self._ns(
                self.ns_client_router,
                base.shell_words(base.TC_PATH, "-s", "qdisc", "show", "dev", self.client_router_client_veth),
            ),
            verbose=False,
        )
        return {"sent_bytes": base.parse_tc_sent_bytes(raw), "backlog_bytes": base.parse_tc_backlog_bytes(raw)}

    def _read_bottleneck_backlog_bytes(self) -> int:
        return self._read_bottleneck_qdisc_stats()["backlog_bytes"]

    def _read_bottleneck_sent_bytes(self) -> int:
        return self._read_bottleneck_qdisc_stats()["sent_bytes"]

    def _read_snapshot_byte_counters(self) -> Dict[str, int]:
        counters = {
            client.name: base.read_counter_file(self._receiver_byte_files.get(client.name, ""))
            for client in self.clients
        }
        bottleneck_stats = self._read_bottleneck_qdisc_stats()
        counters["__total__"] = bottleneck_stats["sent_bytes"]
        counters["__bottleneck_backlog_bytes__"] = bottleneck_stats["backlog_bytes"]
        return counters

    def _read_ss_metrics_sample(self) -> tuple[str, Dict[str, base.SSSocketMetrics]]:
        raw_outputs: List[str] = []
        metrics_by_name: Dict[str, base.SSSocketMetrics] = {}
        for client in self.clients:
            raw = base.run_and_grab_shell(
                self._ns(client.sender_namespace, base.shell_words(base.SS_PATH, "-tinmOHn")),
                verbose=False,
            )
            raw_outputs.append(f"# {client.name}\n{raw}")
            metrics_by_peer = base.parse_ss_metrics_by_peer(raw)
            metrics = metrics_by_peer.get((client.client_ip, client.port))
            if metrics is not None:
                metrics_by_name[client.name] = metrics
        return "\n".join(raw_outputs), metrics_by_name

    def setup(self) -> None:
        a = self.clients[0]
        b = self.clients[1]
        c = ""

        for ns in self._all_namespaces():
            c += base.shell_words(base.IP_PATH, "netns", "add", ns) + "\n"

        c += base.shell_words(base.IP_PATH, "link", "add", a.sender_veth, "type", "veth", "peer", "name", a.sender_router_veth_sender) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "add", b.sender_veth, "type", "veth", "peer", "name", b.sender_router_veth_sender) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "add", a.sender_router_veth_switch, "type", "veth", "peer", "name", a.switch_veth_in) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "add", b.sender_router_veth_switch, "type", "veth", "peer", "name", b.switch_veth_in) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "add", self.core_mid_veth, "type", "veth", "peer", "name", self.mid_core_veth) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "add", self.mid_client_router_veth, "type", "veth", "peer", "name", self.client_router_mid_veth) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "add", self.client_router_client_veth, "type", "veth", "peer", "name", self.client_veth) + "\n"

        c += base.shell_words(base.IP_PATH, "link", "set", a.sender_veth, "netns", a.sender_namespace) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", b.sender_veth, "netns", b.sender_namespace) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", a.sender_router_veth_sender, "netns", a.sender_router_namespace) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", a.sender_router_veth_switch, "netns", a.sender_router_namespace) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", b.sender_router_veth_sender, "netns", b.sender_router_namespace) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", b.sender_router_veth_switch, "netns", b.sender_router_namespace) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", a.switch_veth_in, "netns", self.ns_core) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", b.switch_veth_in, "netns", self.ns_core) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", self.core_mid_veth, "netns", self.ns_core) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", self.mid_core_veth, "netns", self.ns_mid) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", self.mid_client_router_veth, "netns", self.ns_mid) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", self.client_router_mid_veth, "netns", self.ns_client_router) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", self.client_router_client_veth, "netns", self.ns_client_router) + "\n"
        c += base.shell_words(base.IP_PATH, "link", "set", self.client_veth, "netns", self.ns_client) + "\n"

        c += self._ns(a.sender_namespace, base.shell_words(base.IP_PATH, "addr", "add", f"{a.sender_ip}/24", "dev", a.sender_veth)) + "\n"
        c += self._ns(b.sender_namespace, base.shell_words(base.IP_PATH, "addr", "add", f"{b.sender_ip}/24", "dev", b.sender_veth)) + "\n"
        c += self._ns(a.sender_router_namespace, base.shell_words(base.IP_PATH, "addr", "add", f"{a.sender_router_sender_ip}/24", "dev", a.sender_router_veth_sender)) + "\n"
        c += self._ns(a.sender_router_namespace, base.shell_words(base.IP_PATH, "addr", "add", f"{a.sender_router_switch_ip}/24", "dev", a.sender_router_veth_switch)) + "\n"
        c += self._ns(b.sender_router_namespace, base.shell_words(base.IP_PATH, "addr", "add", f"{b.sender_router_sender_ip}/24", "dev", b.sender_router_veth_sender)) + "\n"
        c += self._ns(b.sender_router_namespace, base.shell_words(base.IP_PATH, "addr", "add", f"{b.sender_router_switch_ip}/24", "dev", b.sender_router_veth_switch)) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "addr", "add", f"{a.switch_in_ip}/24", "dev", a.switch_veth_in)) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "addr", "add", f"{b.switch_in_ip}/24", "dev", b.switch_veth_in)) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "addr", "add", f"{self.core_mid_ip}/24", "dev", self.core_mid_veth)) + "\n"
        c += self._ns(self.ns_mid, base.shell_words(base.IP_PATH, "addr", "add", f"{self.mid_core_ip}/24", "dev", self.mid_core_veth)) + "\n"
        c += self._ns(self.ns_mid, base.shell_words(base.IP_PATH, "addr", "add", f"{self.mid_client_router_ip}/24", "dev", self.mid_client_router_veth)) + "\n"
        c += self._ns(self.ns_client_router, base.shell_words(base.IP_PATH, "addr", "add", f"{self.client_router_mid_ip}/24", "dev", self.client_router_mid_veth)) + "\n"
        c += self._ns(self.ns_client_router, base.shell_words(base.IP_PATH, "addr", "add", f"{self.client_router_client_ip}/24", "dev", self.client_router_client_veth)) + "\n"
        c += self._ns(self.ns_client, base.shell_words(base.IP_PATH, "addr", "add", f"{self.client_ip}/24", "dev", self.client_veth)) + "\n"

        for ns in self._all_namespaces():
            c += self._ns(ns, base.shell_words(base.IP_PATH, "link", "set", "lo", "up")) + "\n"

        c += self._ns(a.sender_namespace, base.shell_words(base.IP_PATH, "link", "set", a.sender_veth, "up")) + "\n"
        c += self._ns(b.sender_namespace, base.shell_words(base.IP_PATH, "link", "set", b.sender_veth, "up")) + "\n"
        c += self._ns(a.sender_router_namespace, base.shell_words(base.IP_PATH, "link", "set", a.sender_router_veth_sender, "up")) + "\n"
        c += self._ns(a.sender_router_namespace, base.shell_words(base.IP_PATH, "link", "set", a.sender_router_veth_switch, "up")) + "\n"
        c += self._ns(b.sender_router_namespace, base.shell_words(base.IP_PATH, "link", "set", b.sender_router_veth_sender, "up")) + "\n"
        c += self._ns(b.sender_router_namespace, base.shell_words(base.IP_PATH, "link", "set", b.sender_router_veth_switch, "up")) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "link", "set", a.switch_veth_in, "up")) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "link", "set", b.switch_veth_in, "up")) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "link", "set", self.core_mid_veth, "up")) + "\n"
        c += self._ns(self.ns_mid, base.shell_words(base.IP_PATH, "link", "set", self.mid_core_veth, "up")) + "\n"
        c += self._ns(self.ns_mid, base.shell_words(base.IP_PATH, "link", "set", self.mid_client_router_veth, "up")) + "\n"
        c += self._ns(self.ns_client_router, base.shell_words(base.IP_PATH, "link", "set", self.client_router_mid_veth, "up")) + "\n"
        c += self._ns(self.ns_client_router, base.shell_words(base.IP_PATH, "link", "set", self.client_router_client_veth, "up")) + "\n"
        c += self._ns(self.ns_client, base.shell_words(base.IP_PATH, "link", "set", self.client_veth, "up")) + "\n"

        c += self._ns(a.sender_namespace, base.shell_words(base.ETHTOOL_PATH, "-K", a.sender_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(b.sender_namespace, base.shell_words(base.ETHTOOL_PATH, "-K", b.sender_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(a.sender_router_namespace, base.shell_words(base.ETHTOOL_PATH, "-K", a.sender_router_veth_sender, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(a.sender_router_namespace, base.shell_words(base.ETHTOOL_PATH, "-K", a.sender_router_veth_switch, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(b.sender_router_namespace, base.shell_words(base.ETHTOOL_PATH, "-K", b.sender_router_veth_sender, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(b.sender_router_namespace, base.shell_words(base.ETHTOOL_PATH, "-K", b.sender_router_veth_switch, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.ETHTOOL_PATH, "-K", a.switch_veth_in, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.ETHTOOL_PATH, "-K", b.switch_veth_in, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.ETHTOOL_PATH, "-K", self.core_mid_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_mid, base.shell_words(base.ETHTOOL_PATH, "-K", self.mid_core_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_mid, base.shell_words(base.ETHTOOL_PATH, "-K", self.mid_client_router_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_client_router, base.shell_words(base.ETHTOOL_PATH, "-K", self.client_router_mid_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_client_router, base.shell_words(base.ETHTOOL_PATH, "-K", self.client_router_client_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_client, base.shell_words(base.ETHTOOL_PATH, "-K", self.client_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"

        c += self._ns(a.sender_namespace, base.shell_words(base.IP_PATH, "route", "add", "default", "via", a.sender_router_sender_ip, "dev", a.sender_veth)) + "\n"
        c += self._ns(b.sender_namespace, base.shell_words(base.IP_PATH, "route", "add", "default", "via", b.sender_router_sender_ip, "dev", b.sender_veth)) + "\n"
        c += self._ns(a.sender_router_namespace, base.shell_words(base.IP_PATH, "route", "add", "default", "via", a.switch_in_ip, "dev", a.sender_router_veth_switch)) + "\n"
        c += self._ns(b.sender_router_namespace, base.shell_words(base.IP_PATH, "route", "add", "default", "via", b.switch_in_ip, "dev", b.sender_router_veth_switch)) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "route", "add", "192.168.0.0/24", "via", a.sender_router_switch_ip, "dev", a.switch_veth_in)) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "route", "add", "192.168.4.0/24", "via", b.sender_router_switch_ip, "dev", b.switch_veth_in)) + "\n"
        c += self._ns(self.ns_core, base.shell_words(base.IP_PATH, "route", "add", "default", "via", self.mid_core_ip, "dev", self.core_mid_veth)) + "\n"
        c += self._ns(self.ns_mid, base.shell_words(base.IP_PATH, "route", "add", "192.168.3.0/24", "via", self.client_router_mid_ip, "dev", self.mid_client_router_veth)) + "\n"
        c += self._ns(self.ns_mid, base.shell_words(base.IP_PATH, "route", "add", "default", "via", self.core_mid_ip, "dev", self.mid_core_veth)) + "\n"
        c += self._ns(self.ns_client_router, base.shell_words(base.IP_PATH, "route", "add", "default", "via", self.mid_client_router_ip, "dev", self.client_router_mid_veth)) + "\n"
        c += self._ns(self.ns_client, base.shell_words(base.IP_PATH, "route", "add", "default", "via", self.client_router_client_ip, "dev", self.client_veth)) + "\n"

        c += self._ns(self.ns_core, base.shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"
        c += self._ns(self.ns_mid, base.shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"
        c += self._ns(self.ns_client_router, base.shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"
        c += self._ns(a.sender_router_namespace, base.shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"
        c += self._ns(b.sender_router_namespace, base.shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"

        for client in self.clients:
            delay_cmd = f"{base.q(base.TC_PATH)} qdisc add dev {base.q(client.sender_router_veth_sender)} root netem delay {client.delay_ms}ms"
            if self.args.loss_pct > 0:
                delay_cmd += f" loss {self.args.loss_pct}%"
            c += self._ns(client.sender_router_namespace, delay_cmd) + "\n"

        client_return_cmd = f"{base.q(base.TC_PATH)} qdisc add dev {base.q(self.client_router_mid_veth)} root netem rate 1000mbit"
        c += self._ns(self.ns_client_router, client_return_cmd) + "\n"

        if self.args.rate_mbit > 0 or self.args.bottleneck_buffer_kbytes > 0 or self.args.loss_pct > 0:
            bottleneck_cmd = f"{base.q(base.TC_PATH)} qdisc add dev {base.q(self.client_router_client_veth)} root netem"
            if self.args.bottleneck_buffer_kbytes > 0:
                bottleneck_cmd += f" limit {base.buffer_kbytes_to_packet_limit(self.args.bottleneck_buffer_kbytes)}"
            if self.args.loss_pct > 0:
                bottleneck_cmd += f" loss {self.args.loss_pct}%"
            if self.args.rate_mbit > 0:
                bottleneck_cmd += f" rate {self.args.rate_mbit}mbit"
            c += self._ns(self.ns_client_router, bottleneck_cmd) + "\n"

        base.run_shell(c)

    def cleanup(self) -> None:
        for ns in self._all_namespaces():
            base.run_shell(f"{base.q(base.IP_PATH)} netns del {base.q(ns)} 2>/dev/null", verbose=False, check=False)

    def _snapshot_rates(self, current: Dict[str, int], previous: Dict[str, int], dt: float) -> Dict[str, object]:
        dt = max(dt, 1e-9)

        def to_mbps(delta_bytes: int) -> float:
            return (delta_bytes * 8 / dt) / 1_000_000.0

        receivers: Dict[str, Any] = {}
        for client in self.clients:
            client_mbps = to_mbps(current[client.name] - previous[client.name])
            receivers[client.name] = {
                "megabits_per_second": client_mbps,
                "cca": client.cca,
                "delay_ms": client.delay_ms,
            }
        receivers["total_megabits_per_second"] = to_mbps(current["__total__"] - previous["__total__"])
        return {"receivers": receivers}

    def run_benchmark(self) -> Dict[str, Any]:
        script = os.path.abspath(__file__)
        with tempfile.TemporaryDirectory(prefix=f"netem_nines_{self.id}_") as tempdir:
            ready_files: Dict[str, str] = {}
            receiver_byte_files: Dict[str, str] = {}
            sender_rtt_files: Dict[str, str] = {}
            sender_in_flight_files: Dict[str, str] = {}
            sender_cwnd_files: Dict[str, str] = {}
            receiver_procs: Dict[str, subprocess.Popen] = {}
            sender_procs: Dict[str, subprocess.Popen] = {}
            use_ss_sampler = self.args.snapshot_metrics_source == "ss"
            ss_log_file = base.resolve_ss_log_path(self.args.ss_log_file, self.id) if use_ss_sampler else ""

            for client in self.clients:
                ready_files[client.name] = os.path.join(tempdir, f"{client.name}.ready")
                receiver_byte_files[client.name] = os.path.join(tempdir, f"{client.name}.rx_bytes")
                if not use_ss_sampler:
                    sender_rtt_files[client.name] = os.path.join(tempdir, f"sender_{client.name}.rtt_ms")
                    sender_in_flight_files[client.name] = os.path.join(tempdir, f"sender_{client.name}.in_flight_packets")
                    sender_cwnd_files[client.name] = os.path.join(tempdir, f"sender_{client.name}.congestion_window_bytes")

                receiver_cmd = [
                    base.IP_PATH,
                    "netns",
                    "exec",
                    self.ns_client,
                    base.PYTHON3_PATH,
                    script,
                    "--mode",
                    "receiver",
                    "--name",
                    client.name,
                    "--listen-ip",
                    self.client_ip,
                    "--port",
                    str(client.port),
                    "--chunk-size",
                    str(self.args.chunk_size),
                    "--ready-file",
                    ready_files[client.name],
                ]
                if not use_ss_sampler:
                    receiver_cmd.extend(["--snapshot-bytes-file", receiver_byte_files[client.name]])

                receiver_procs[client.name] = subprocess.Popen(
                    receiver_cmd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            deadline = time.time() + 10
            while time.time() < deadline:
                if all(os.path.exists(path) for path in ready_files.values()):
                    break
                time.sleep(0.05)
            else:
                for proc in receiver_procs.values():
                    proc.kill()
                raise RuntimeError("Receiver startup timeout.")

            for client in self.clients:
                target_arg = (
                    f"{client.name}:{self.client_ip}:{client.port}:{client.cca}:"
                    f"{client.file_size_to_be_transferred_in_mbytes}:{client.start_delay_ms}"
                )
                sender_cmd = [
                    base.IP_PATH,
                    "netns",
                    "exec",
                    client.sender_namespace,
                    base.PYTHON3_PATH,
                    script,
                    "--mode",
                    "sender",
                    "--targets",
                    target_arg,
                    "--chunk-size",
                    str(self.args.chunk_size),
                ]
                if not use_ss_sampler:
                    sender_cmd.extend(
                        [
                            "--snapshot-rtt-ms-files",
                            f"{client.name}:{sender_rtt_files[client.name]}",
                            "--snapshot-in-flight-files",
                            f"{client.name}:{sender_in_flight_files[client.name]}",
                            "--snapshot-cwnd-bytes-files",
                            f"{client.name}:{sender_cwnd_files[client.name]}",
                        ]
                    )

                sender_procs[client.name] = subprocess.Popen(
                    sender_cmd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            snapshots: List[Dict[str, Any]] = []
            if use_ss_sampler:
                sampler = base.SSSampler(self, sample_interval_ms=self.args.ss_sample_interval_ms, log_path=ss_log_file)
                sampler.start(time.monotonic())
                try:
                    while any(proc.poll() is None for proc in sender_procs.values()) or any(
                        proc.poll() is None for proc in receiver_procs.values()
                    ):
                        time.sleep(0.05)
                finally:
                    snapshots = sampler.stop()
                for snapshot in snapshots:
                    print(json.dumps(snapshot, indent=2), flush=True)
            else:
                self._receiver_byte_files = dict(receiver_byte_files)
                previous = self._read_snapshot_byte_counters()
                start = time.monotonic()
                last = start
                snapshot_interval_seconds = self.args.snapshot_interval_ms / 1000.0
                next_tick = start + snapshot_interval_seconds

                while True:
                    all_done = all(proc.poll() is not None for proc in sender_procs.values()) and all(
                        proc.poll() is not None for proc in receiver_procs.values()
                    )
                    now = time.monotonic()
                    if not all_done and now < next_tick:
                        time.sleep(min(0.05, next_tick - now))
                        continue

                    current = self._read_snapshot_byte_counters()
                    rates = self._snapshot_rates(current, previous, now - last)
                    bottleneck_backlog_bytes = max(0, current.get("__bottleneck_backlog_bytes__", 0))
                    bottleneck_rate_bits_per_second = max(0.0, self.args.rate_mbit * 1_000_000.0)
                    bottleneck_queuing_delay_ms = 0.0
                    if self.args.rate_mbit > 0 and bottleneck_rate_bits_per_second > 0:
                        bottleneck_queuing_delay_ms = (
                            bottleneck_backlog_bytes * 8.0 * 1000.0
                        ) / bottleneck_rate_bits_per_second

                    overall_in_flight_packets = 0
                    overall_congestion_window_bytes = 0
                    for client in self.clients:
                        rtt_ms = base.read_float_file(sender_rtt_files[client.name])
                        rates["receivers"][client.name]["rtt_ms"] = rtt_ms
                        in_flight_packets = base.read_counter_file(sender_in_flight_files[client.name])
                        congestion_window_bytes = base.read_counter_file(sender_cwnd_files[client.name])
                        rates["receivers"][client.name]["in_flight_packets"] = in_flight_packets
                        rates["receivers"][client.name]["congestion_window_bytes"] = congestion_window_bytes
                        overall_in_flight_packets += in_flight_packets
                        overall_congestion_window_bytes += congestion_window_bytes

                    snapshot = {
                        "mode": "snapshot",
                        "snapshot_index": len(snapshots) + 1,
                        "elapsed_microseconds": int(round((now - start) * 1_000_000.0)),
                        "bottleneck_queuing_delay_ms": bottleneck_queuing_delay_ms,
                        "bottleneck_backlog_bytes": bottleneck_backlog_bytes,
                        "bottleneck_rate_bits_per_second": bottleneck_rate_bits_per_second,
                        "overall_in_flight_packets": overall_in_flight_packets,
                        "overall_congestion_window_bytes": overall_congestion_window_bytes,
                        **rates,
                    }
                    snapshots.append(snapshot)
                    print(json.dumps(snapshot, indent=2), flush=True)
                    previous = current
                    last = now
                    next_tick = now + snapshot_interval_seconds

                    if all_done:
                        break

            sender_out_by_name: Dict[str, str] = {}
            sender_err_by_name: Dict[str, str] = {}
            receiver_out_by_name: Dict[str, str] = {}
            receiver_err_by_name: Dict[str, str] = {}

            for client in self.clients:
                out, err = sender_procs[client.name].communicate(timeout=10)
                sender_out_by_name[client.name] = out
                sender_err_by_name[client.name] = err
            for client in self.clients:
                out, err = receiver_procs[client.name].communicate(timeout=10)
                receiver_out_by_name[client.name] = out
                receiver_err_by_name[client.name] = err

            for client in self.clients:
                if sender_procs[client.name].returncode != 0:
                    raise RuntimeError(f"{client.name} sender failed: {sender_err_by_name[client.name].strip()}")
            for client in self.clients:
                if receiver_procs[client.name].returncode != 0:
                    raise RuntimeError(f"{client.name} receiver failed: {receiver_err_by_name[client.name].strip()}")

            sender_summaries = {
                client.name: base.parse_single_json_line(sender_out_by_name[client.name], f"{client.name} sender")
                for client in self.clients
            }
            sender_summary = {
                "mode": "sender_group",
                "targets": {name: summary["targets"][name] for name, summary in sender_summaries.items()},
                "total_seconds": max((summary.get("total_seconds", 0.0) for summary in sender_summaries.values()), default=0.0),
            }
            receiver_summaries = {
                client.name: base.parse_single_json_line(receiver_out_by_name[client.name], f"{client.name} receiver")
                for client in self.clients
            }

            legacy_client_config: Dict[str, Any] = {}
            for client in self.clients:
                legacy_client_config[f"client{client.index}"] = {
                    "cca": client.cca,
                    "delay_ms": client.delay_ms,
                }

            return {
                "mode": "benchmark_result",
                "bench_id": self.id,
                "snapshots": snapshots,
                "sender": sender_summary,
                "receivers": receiver_summaries,
                "config": {
                    **legacy_client_config,
                    "num_clients": len(self.clients),
                    "clients": {
                        client.name: {
                            "index": client.index,
                            "cca": client.cca,
                            "sender_namespace": client.sender_namespace,
                            "sender_router_namespace": client.sender_router_namespace,
                            "core_namespace": self.ns_core,
                            "mid_namespace": self.ns_mid,
                            "client_router_namespace": self.ns_client_router,
                            "client_namespace": self.ns_client,
                            "shared_client_ip": self.client_ip,
                            "port": client.port,
                        }
                        for client in self.clients
                    },
                    "topology": {
                        "shape": "sender_a -> sender_router_a -> core_router -> mid -> client_router -> client <- sender_b",
                        "core_namespace": self.ns_core,
                        "mid_namespace": self.ns_mid,
                        "client_router_namespace": self.ns_client_router,
                        "client_namespace": self.ns_client,
                        "bottleneck_device": self.client_router_client_veth,
                        "shared_receiver_ip": self.client_ip,
                    },
                    "loss_pct": self.args.loss_pct,
                    "rate_mbit": self.args.rate_mbit,
                    "bottleneck_buffer_kbytes": self.args.bottleneck_buffer_kbytes,
                    "bottleneck_buffer_limit_packets": base.buffer_kbytes_to_packet_limit(self.args.bottleneck_buffer_kbytes),
                    "bottleneck_buffer_limit_bytes": base.buffer_kbytes_to_byte_limit(self.args.bottleneck_buffer_kbytes),
                    "bottleneck_tbf_burst_bytes": base.tbf_burst_bytes(self.args.rate_mbit),
                    "snapshot_metrics_source": self.args.snapshot_metrics_source,
                    "snapshot_interval_ms": base.effective_snapshot_interval_ms(self.args),
                    "ss_sample_interval_ms": self.args.ss_sample_interval_ms,
                    "ss_log_file": ss_log_file,
                },
            }


def orchestrator_mode(args: argparse.Namespace) -> int:
    client_configs = resolve_client_run_configs(args)

    base.require_linux()
    base.require_root()
    base.require_tools(args.snapshot_metrics_source)

    bench = ProxyDelayTwoFlowBench(args, client_configs)
    keep = args.keep_namespaces
    started_at_utc = datetime.datetime.now(datetime.timezone.utc)
    result: Dict[str, Any]

    try:
        base.print_banner("Pre test Cleanup")
        bench.cleanup()

        base.print_banner("Setup Namespaces")
        bench.setup()

        base.print_banner("Run Tests")
        result = bench.run_benchmark()
    finally:
        if not keep:
            base.print_banner("Post Test Cleanup")
            bench.cleanup()

    if args.supabase_project_id and args.supabase_service_role_key:
        ended_at_utc = datetime.datetime.now(datetime.timezone.utc)
        base.persist_to_supabase(args, result, started_at_utc, ended_at_utc, client_configs)
    elif args.supabase_project_id or args.supabase_service_role_key:
        print(
            "Supabase persistence skipped: provide both --supabase-project-id and --supabase-service-role-key.",
            file=__import__("sys").stderr,
        )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = base.build_parser()
    parser.description = "Two-flow TCP transfer benchmark over the ProxyDelay nsperf_two_flows topology."
    parser.set_defaults(num_clients=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.snapshot_interval_seconds_legacy is not None:
        args.snapshot_interval_ms = base.to_positive_smallint_milliseconds(
            args.snapshot_interval_seconds_legacy,
            "snapshot_interval_ms",
        )
    if args.mode == "sender":
        return base.sender_mode(args)
    if args.mode == "receiver":
        return base.receiver_mode(args)
    return orchestrator_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
