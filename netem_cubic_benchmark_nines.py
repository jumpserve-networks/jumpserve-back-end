#!/usr/bin/env python3
"""
Benchmark one sender transferring data to multiple clients over emulated links.

Linux mode:
- Builds a topology with network namespaces and veth pairs:
  sender <-> sender-router <-> switch <-> transit <-> fanout <-> client1..clientN
- Applies per-flow netem (delay/loss) on sender-side router links.
- Applies a shared rate bottleneck on the communal switch-to-transit path.
- Uses per-client TCP congestion control (CCA) at sender socket level.
- Supports per-client flow start delay and per-client transfer size.
- Runs one receiver process per client and one concurrent sender process.
- Prints synchronized JSON rate snapshots at a fixed interval.

macOS mode:
- Exits with guidance. Linux networking features (ip netns/tc) are required.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import random
import re
import shlex
import shutil
import socket
import subprocess
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List

BYTES_PER_MEGABYTE = 1_000_000
DEFAULT_FILE_SIZE_TO_BE_TRANSFERRED_IN_MBYTES = 50.0

SS_SKMEM_FIELD_KEYS = {
    "r",
    "rb",
    "t",
    "tb",
    "f",
    "w",
    "o",
    "bl",
    "d",
}

IP_PATH = shutil.which("ip") or "/usr/sbin/ip"
TC_PATH = shutil.which("tc") or "/usr/sbin/tc"
SS_PATH = shutil.which("ss") or "/usr/bin/ss"
ETHTOOL_PATH = shutil.which("ethtool") or "/usr/sbin/ethtool"
PYTHON3_PATH = shutil.which("python3") or "/usr/bin/python3"


def q(value: Any) -> str:
    return shlex.quote(str(value))


def shell_words(*words: Any) -> str:
    return " ".join(q(word) for word in words)


def ns_shell(namespace: str, cmd: str) -> str:
    return f"{q(IP_PATH)} netns exec {q(namespace)} {cmd}"


def run_shell(cmd: str, verbose: bool = True, check: bool = True) -> int:
    if verbose:
        print(f"running: |{cmd}|")
        sys.stdout.flush()
    status = os.system(cmd)
    if check and status != 0:
        raise RuntimeError(f"error {status} executing: {cmd}")
    return status


def run_and_grab_shell(cmd: str, verbose: bool = True) -> str:
    if verbose:
        print(f"running: |{cmd}|")
        sys.stdout.flush()
    output_stream = os.popen(cmd)
    output = output_stream.read()
    output_stream.close()
    return output


def print_banner(message: str) -> None:
    print("\n\n\n")
    print("****************")
    print(message)
    print("****************")
    print("\n")


@dataclass
class Target:
    host: str
    port: int
    name: str
    cca: str
    file_size_to_be_transferred_in_mbytes: float
    start_delay_ms: float


@dataclass
class ClientRunConfig:
    index: int
    name: str
    delay_ms: float
    cca: str
    port: int
    start_delay_ms: float
    file_size_to_be_transferred_in_mbytes: float
    sender_namespace: str = ""
    sender_veth: str = ""
    sender_router_namespace: str = ""
    sender_router_veth_sender: str = ""
    sender_router_veth_switch: str = ""
    switch_veth_in: str = ""
    sender_ip: str = ""
    sender_router_sender_ip: str = ""
    sender_router_switch_ip: str = ""
    switch_in_ip: str = ""
    fanout_veth_out: str = ""
    namespace: str = ""
    veth_client: str = ""
    veth_router: str = ""
    client_ip: str = ""
    router_ip: str = ""


@dataclass
class SSSocketMetrics:
    delivery_rate_bps: int = 0
    rtt_ms: float = 0.0
    in_flight_packets: int = 0
    congestion_window_bytes: int = 0
    skmem: Dict[str, int] = field(default_factory=dict)


def run_cmd(cmd: List[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture,
    )


def write_counter_file(path: str, value: int) -> None:
    tmp_path = f"{path}.tmp"
    Path(tmp_path).write_text(f"{value}\n", encoding="ascii")
    os.replace(tmp_path, path)


def write_float_file(path: str, value: float) -> None:
    tmp_path = f"{path}.tmp"
    Path(tmp_path).write_text(f"{value:.6f}\n", encoding="ascii")
    os.replace(tmp_path, path)


def read_counter_file(path: str) -> int:
    try:
        return int(Path(path).read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        return 0


def read_float_file(path: str) -> float:
    try:
        return float(Path(path).read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        return 0.0


def parse_snapshot_file_map(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not raw:
        return out
    for item in raw.split(","):
        name, sep, path = item.partition(":")
        if sep and name and path:
            out[name.strip()] = path.strip()
    return out


def parse_csv_arg(raw: str, arg_name: str) -> List[str]:
    text = raw.strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def parse_float_csv_arg(raw: str, arg_name: str) -> List[float]:
    values = parse_csv_arg(raw, arg_name)
    out: List[float] = []
    for value in values:
        try:
            out.append(float(value))
        except ValueError:
            continue
    return out


def _list_or_default(values: List[Any], defaults: List[Any], count: int) -> List[Any]:
    out = list(values[:count]) if values else []
    while len(out) < count:
        out.append(defaults[len(out)] if len(out) < len(defaults) else defaults[-1])
    return out[:count]


def choose_random_unique_ports(count: int, low: int = 20000, high: int = 65000) -> List[int]:
    if count <= 0:
        return []
    if high <= low:
        raise ValueError("Invalid port range for random port selection.")
    pool_size = high - low + 1
    if count > pool_size:
        raise ValueError("Requested more client ports than available in random selection range.")
    return random.sample(range(low, high + 1), count)


def resolve_client_run_configs(args: argparse.Namespace) -> List[ClientRunConfig]:
    if args.num_clients <= 0:
        raise ValueError("--num-clients must be > 0")
    if args.num_clients > 253:
        raise ValueError("--num-clients must be <= 253 due to subnet addressing limits.")

    names = parse_csv_arg(args.client_names, "--client-names")
    if not names:
        names = [f"client{i}" for i in range(1, args.num_clients + 1)]
    names = _list_or_default(names, names or ["client1"], args.num_clients)

    delays = parse_float_csv_arg(args.client_delays_ms, "--client-delays-ms")
    delays = _list_or_default(
        delays,
        [args.client1_delay_ms, args.client2_delay_ms, 20.0],
        args.num_clients,
    )

    ccas = parse_csv_arg(args.client_ccas, "--client-ccas")
    ccas = _list_or_default(
        ccas,
        [args.client1_cca, args.client2_cca, "cubic"],
        args.num_clients,
    )

    start_delays = parse_float_csv_arg(args.client_start_delays_ms, "--client-start-delays-ms")
    start_delays = _list_or_default(start_delays, [0.0], args.num_clients)

    file_sizes = parse_float_csv_arg(args.client_file_sizes_mbytes, "--client-file-sizes-mbytes")
    file_sizes = _list_or_default(file_sizes, [DEFAULT_FILE_SIZE_TO_BE_TRANSFERRED_IN_MBYTES], args.num_clients)

    ports = choose_random_unique_ports(args.num_clients)

    out: List[ClientRunConfig] = []
    for i in range(args.num_clients):
        out.append(
            ClientRunConfig(
                index=i + 1,
                name=names[i],
                delay_ms=float(delays[i]),
                cca=ccas[i],
                port=ports[i],
                start_delay_ms=float(start_delays[i]),
                file_size_to_be_transferred_in_mbytes=float(file_sizes[i]),
            )
        )
    return out


def parse_tc_backlog_bytes(raw: str) -> int:
    # tc examples: "backlog 0b 0p" / "backlog 12Kb 3p"
    matches = re.findall(r"backlog\s+([0-9]+(?:\.[0-9]+)?)([KMGTP]?)(?:i)?[bB]\b", raw)
    if not matches:
        return 0
    scale = {
        "": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "P": 1024**5,
    }
    values: List[int] = []
    for number, prefix in matches:
        try:
            value = float(number) * scale[prefix.upper()]
        except (ValueError, KeyError):
            continue
        values.append(int(round(value)))
    if not values:
        return 0
    return max(values)


def parse_tc_sent_bytes(raw: str) -> int:
    # tc example: "Sent 12345 bytes 67 pkt (dropped 0, overlimits 0 requeues 0)"
    matches = re.findall(r"\bSent\s+([0-9]+)\s+bytes\b", raw)
    if not matches:
        return 0
    values: List[int] = []
    for value in matches:
        try:
            values.append(int(value))
        except ValueError:
            continue
    if not values:
        return 0
    return max(values)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def parse_tc_root_qdisc_stats_json(raw: str) -> Dict[str, int] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None

    entries = [item for item in payload if isinstance(item, dict)]
    if not entries:
        return None

    root_entry = next((item for item in entries if item.get("root") is True), entries[0])
    sent_bytes = _coerce_int(root_entry.get("bytes"))
    backlog_bytes = _coerce_int(root_entry.get("backlog"))
    if sent_bytes is None and backlog_bytes is None:
        return None
    return {
        "sent_bytes": max(0, sent_bytes or 0),
        "backlog_bytes": max(0, backlog_bytes or 0),
    }


def parse_ss_socket_address(token: str) -> tuple[str, int] | None:
    host, sep, port_text = token.rpartition(":")
    if not sep or not host or not port_text:
        return None
    host = host.strip("[]")
    try:
        return host, int(port_text)
    except ValueError:
        return None


def parse_ss_value(info: str, pattern: str) -> str | None:
    match = re.search(pattern, info)
    if match is None:
        return None
    return match.group(1)


def parse_ss_skmem(info: str) -> Dict[str, int]:
    skmem_text = parse_ss_value(info, r"\bskmem:\(([^)]*)\)")
    if skmem_text is None:
        return {}
    out: Dict[str, int] = {}
    for item in skmem_text.split(","):
        entry = item.strip()
        if not entry:
            continue
        match = re.fullmatch(r"([a-z]+)(\d+)", entry)
        if match is None:
            continue
        key = match.group(1)
        if key not in SS_SKMEM_FIELD_KEYS:
            continue
        try:
            out[key] = max(0, int(match.group(2)))
        except ValueError:
            continue
    return out


def parse_ss_metrics_by_peer(raw: str) -> Dict[tuple[str, int], SSSocketMetrics]:
    metrics_by_peer: Dict[tuple[str, int], SSSocketMetrics] = {}
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split(maxsplit=5)
        if len(parts) < 6:
            continue
        peer = parse_ss_socket_address(parts[4])
        if peer is None:
            continue
        info = parts[5]
        delivery_rate_text = parse_ss_value(info, r"\bdelivery_rate\s+([0-9]+)bps\b")
        rtt_ms_text = parse_ss_value(info, r"\brtt:([0-9]+(?:\.[0-9]+)?)/")
        mss_text = parse_ss_value(info, r"\bmss:(\d+)\b")
        cwnd_packets_text = parse_ss_value(info, r"\bcwnd:(\d+)\b")
        in_flight_text = parse_ss_value(info, r"\bunacked:(\d+)\b")
        skmem = parse_ss_skmem(info)
        try:
            delivery_rate_bps = int(delivery_rate_text or "0")
        except ValueError:
            delivery_rate_bps = 0
        try:
            rtt_ms = float(rtt_ms_text or "0")
        except ValueError:
            rtt_ms = 0.0
        try:
            mss = int(mss_text or "0")
        except ValueError:
            mss = 0
        try:
            cwnd_packets = int(cwnd_packets_text or "0")
        except ValueError:
            cwnd_packets = 0
        try:
            in_flight_packets = int(in_flight_text or "0")
        except ValueError:
            in_flight_packets = 0
        metrics_by_peer[peer] = SSSocketMetrics(
            delivery_rate_bps=max(0, delivery_rate_bps),
            rtt_ms=max(0.0, rtt_ms),
            in_flight_packets=max(0, in_flight_packets),
            congestion_window_bytes=max(0, cwnd_packets) * max(0, mss),
            skmem=skmem,
        )
    return metrics_by_peer


def ss_metrics_to_log_dict(metrics: SSSocketMetrics) -> Dict[str, Any]:
    return {
        "delivery_rate_bps": metrics.delivery_rate_bps,
        "rtt_ms": metrics.rtt_ms,
        "in_flight_packets": metrics.in_flight_packets,
        "congestion_window_bytes": metrics.congestion_window_bytes,
        "skmem": dict(metrics.skmem),
    }


def buffer_kbytes_to_packet_limit(buffer_kbytes: float, mtu_bytes: int = 1500) -> int:
    if buffer_kbytes <= 0:
        return 0
    buffer_bytes = int(round(buffer_kbytes * 1024.0))
    if buffer_bytes <= 0:
        return 0
    return max(1, (buffer_bytes + mtu_bytes - 1) // mtu_bytes)


def buffer_kbytes_to_byte_limit(buffer_kbytes: float) -> int:
    if buffer_kbytes <= 0:
        return 0
    buffer_bytes = int(round(buffer_kbytes * 1024.0))
    return max(0, buffer_bytes)


def tbf_burst_bytes(rate_mbit: float, mtu_bytes: int = 1500) -> int:
    if rate_mbit <= 0:
        return mtu_bytes * 2
    rate_bytes_per_second = (rate_mbit * 1_000_000.0) / 8.0
    # Keep burst above a full-sized data packet plus qdisc/accounting overhead while
    # still tracking roughly one scheduler tick worth of bytes on low-HZ kernels.
    return max(mtu_bytes * 2, int(rate_bytes_per_second / 100.0 + 0.999999))


def tbf_limit_bytes(rate_mbit: float, buffer_kbytes: float, mtu_bytes: int = 1500) -> int:
    burst_bytes = tbf_burst_bytes(rate_mbit, mtu_bytes=mtu_bytes)
    configured_limit = buffer_kbytes_to_byte_limit(buffer_kbytes)
    if configured_limit > 0:
        return max(configured_limit, burst_bytes)
    return max(burst_bytes * 4, 64 * 1024)


def tcp_rtt_ms(sock: socket.socket) -> float:
    # Linux TCP_INFO.tcpi_rtt is a u32 at byte offset 68 in microseconds.
    try:
        info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
    except OSError:
        return 0.0
    if len(info) < 72:
        return 0.0
    rtt_us = struct.unpack_from("I", info, 68)[0]
    return rtt_us / 1000.0


def tcp_sender_metrics(sock: socket.socket) -> Dict[str, int | float]:
    # Linux TCP_INFO offsets (u32 fields): snd_mss=16, unacked=24, rtt_us=68, snd_cwnd=80.
    try:
        info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
    except OSError:
        return {"rtt_ms": 0.0, "in_flight_packets": 0, "congestion_window_bytes": 0}
    if len(info) < 84:
        return {"rtt_ms": 0.0, "in_flight_packets": 0, "congestion_window_bytes": 0}
    snd_mss = struct.unpack_from("I", info, 16)[0]
    unacked = struct.unpack_from("I", info, 24)[0]
    rtt_us = struct.unpack_from("I", info, 68)[0]
    snd_cwnd_packets = struct.unpack_from("I", info, 80)[0]
    return {
        "rtt_ms": rtt_us / 1000.0,
        "in_flight_packets": int(unacked),
        "congestion_window_bytes": int(snd_cwnd_packets) * int(snd_mss),
    }


def effective_snapshot_interval_ms(args: argparse.Namespace) -> int:
    if args.snapshot_metrics_source == "ss":
        return args.ss_sample_interval_ms
    return args.snapshot_interval_ms


def resolve_ss_log_path(configured_path: str, bench_id: str) -> str:
    if configured_path.strip():
        path = Path(configured_path).expanduser()
    else:
        path = Path.cwd() / f"ss_sampler_{bench_id}.jsonl"
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


class SSSampler:
    def __init__(self, bench: "NetnsBench", sample_interval_ms: int, log_path: str):
        self._bench = bench
        self._sample_interval_seconds = sample_interval_ms / 1000.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._snapshots: List[Dict[str, Any]] = []
        self._start_time = 0.0
        self._log_path = log_path
        self._log_handle: Any = None

    def start(self, start_time: float) -> None:
        self._start_time = start_time
        self._log_handle = open(self._log_path, "w", encoding="utf-8", buffering=1)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> List[Dict[str, Any]]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        return list(self._snapshots)

    def _run(self) -> None:
        while True:
            now = time.monotonic()
            self._snapshots.append(self._sample(now))
            if self._stop.wait(self._sample_interval_seconds):
                return

    def _sample(self, now: float) -> Dict[str, Any]:
        raw_ss_output, sampled_metrics = self._bench._read_ss_metrics_sample()
        bottleneck_stats = self._bench._read_bottleneck_qdisc_stats()
        bottleneck_backlog_bytes = max(0, bottleneck_stats["backlog_bytes"])
        bottleneck_rate_bits_per_second = max(0.0, self._bench.args.rate_mbit * 1_000_000.0)
        bottleneck_queuing_delay_ms = 0.0
        if self._bench.args.rate_mbit > 0 and bottleneck_rate_bits_per_second > 0:
            bottleneck_queuing_delay_ms = (
                bottleneck_backlog_bytes * 8.0 * 1000.0
            ) / bottleneck_rate_bits_per_second

        receivers: Dict[str, Any] = {}
        total_megabits_per_second = 0.0
        overall_in_flight_packets = 0
        overall_congestion_window_bytes = 0
        for client in self._bench.clients:
            metrics = sampled_metrics.get(client.name, SSSocketMetrics())
            client_mbps = metrics.delivery_rate_bps / 1_000_000.0
            total_megabits_per_second += client_mbps
            overall_in_flight_packets += metrics.in_flight_packets
            overall_congestion_window_bytes += metrics.congestion_window_bytes
            receivers[client.name] = {
                "megabits_per_second": client_mbps,
                "cca": client.cca,
                "delay_ms": client.delay_ms,
                "rtt_ms": metrics.rtt_ms,
                "in_flight_packets": metrics.in_flight_packets,
                "congestion_window_bytes": metrics.congestion_window_bytes,
                "skmem": dict(metrics.skmem),
            }
        receivers["total_megabits_per_second"] = total_megabits_per_second

        snapshot_index = len(self._snapshots) + 1
        snapshot = {
            "mode": "snapshot",
            "snapshot_index": snapshot_index,
            "elapsed_microseconds": int(round((now - self._start_time) * 1_000_000.0)),
            "bottleneck_queuing_delay_ms": bottleneck_queuing_delay_ms,
            "bottleneck_backlog_bytes": bottleneck_backlog_bytes,
            "bottleneck_rate_bits_per_second": bottleneck_rate_bits_per_second,
            "overall_in_flight_packets": overall_in_flight_packets,
            "overall_congestion_window_bytes": overall_congestion_window_bytes,
            "receivers": receivers,
        }
        if self._log_handle is not None:
            self._log_handle.write(
                json.dumps(
                    {
                        "sample_index": snapshot_index,
                        "elapsed_microseconds": snapshot["elapsed_microseconds"],
                        "sampled_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "sampled_metrics": {
                            client.name: ss_metrics_to_log_dict(sampled_metrics.get(client.name, SSSocketMetrics()))
                            for client in self._bench.clients
                        },
                        "raw_ss_output": raw_ss_output,
                    }
                )
                + "\n"
            )
        return snapshot


def parse_single_json_line(raw: str, label: str) -> Dict[str, Any]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"{label} produced no JSON output.")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} returned invalid JSON: {lines[-1]!r}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} JSON output must be an object.")
    return value


def to_smallint(value: float, field_name: str) -> int:
    out = int(round(value))
    if out < -32768 or out > 32767:
        raise ValueError(f"{field_name} must fit PostgreSQL smallint.")
    return out


def to_int32(value: Any, field_name: str) -> int:
    out = int(value)
    if out < -(2**31) or out > (2**31 - 1):
        raise ValueError(f"{field_name} must fit PostgreSQL int4.")
    return out


def to_int64(value: Any, field_name: str) -> int:
    out = int(value)
    if out < -(2**63) or out > (2**63 - 1):
        raise ValueError(f"{field_name} must fit PostgreSQL int8.")
    return out


def to_decimal_text(value: Any, field_name: str) -> str:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a finite numeric value.") from exc
    if not out.is_finite():
        raise ValueError(f"{field_name} must be a finite numeric value.")
    return format(out, "f")


def to_positive_smallint_milliseconds(value: Any, field_name: str) -> int:
    try:
        milliseconds = Decimal(str(value)) * Decimal("1000")
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a finite numeric value.") from exc
    if not milliseconds.is_finite():
        raise ValueError(f"{field_name} must be a finite numeric value.")
    if milliseconds <= 0:
        raise ValueError(f"{field_name} must be > 0.")
    rounded = int(milliseconds.to_integral_value())
    if rounded <= 0:
        raise ValueError(f"{field_name} must round to at least 1 ms.")
    return to_smallint(rounded, field_name)


class SupabaseRestClient:
    def __init__(self, project_id: str, service_role_key: str, timeout_seconds: float = 15.0):
        self.project_id = project_id
        self.service_role_key = service_role_key
        self.timeout_seconds = timeout_seconds
        self.base_url = f"https://{project_id}.supabase.co/rest/v1"

    def _request(
        self,
        method: str,
        table: str,
        *,
        query: str = "",
        payload: Any = None,
        prefer: str = "",
    ) -> Any:
        url = f"{self.base_url}/{table}"
        if query:
            url = f"{url}?{query}"

        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(url=url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8").strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Supabase {method} {table} failed ({exc.code}): {detail}") from exc

        if not body:
            return None
        return json.loads(body)

    def get_or_create_algorithm_id(self, name: str) -> int:
        encoded_name = urllib.parse.quote(name, safe="")
        query = f"select=id&name=eq.{encoded_name}&order=id.asc&limit=1"
        rows = self._request("GET", "congestion_control_algorithms", query=query)
        if rows:
            return int(rows[0]["id"])

        rows = self._request(
            "POST",
            "congestion_control_algorithms",
            payload=[{"name": name}],
            prefer="return=representation",
        )
        if not rows:
            raise RuntimeError(f"Failed to insert congestion control algorithm '{name}'.")
        return int(rows[0]["id"])

    def insert_emulated_parent_run(
        self,
        *,
        number_of_clients: int,
        bottleneck_rate_megabit: float,
        queue_buffer_size_kilobyte: float,
        snapshot_length_ms: int,
    ) -> int:
        bottleneck_rate_megabit_text = to_decimal_text(bottleneck_rate_megabit, "bottleneck_rate_megabit")
        if Decimal(bottleneck_rate_megabit_text) <= 0:
            raise ValueError("bottleneck_rate_megabit must be > 0")
        queue_buffer_size_kilobyte_text = to_decimal_text(
            queue_buffer_size_kilobyte,
            "queue_buffer_size_kilobyte",
        )
        rows = self._request(
            "POST",
            "emulated_parent_runs",
            payload=[
                {
                    "number_of_clients": to_smallint(number_of_clients, "number_of_clients"),
                    "bottleneck_rate_megabit": bottleneck_rate_megabit_text,
                    "queue_buffer_size_kilobyte": queue_buffer_size_kilobyte_text,
                    "snapshot_length_ms": to_smallint(snapshot_length_ms, "snapshot_length_ms"),
                }
            ],
            prefer="return=representation",
        )
        if not rows:
            raise RuntimeError("Failed to insert emulated_parent_runs row.")
        return to_int32(rows[0]["id"], "emulated_parent_runs.id")

    def insert_emulated_run(
        self,
        client_number: int,
        delay_added: int,
        algorithm_id: int,
        emulated_parent_run_id: int,
        client_file_size_megabytes: int,
        client_start_delay_ms: int,
        flow_completion_time_ms: int,
    ) -> int:
        rows = self._request(
            "POST",
            "emulated_runs",
            payload=[
                {
                    "client_number": client_number,
                    "delay_added": delay_added,
                    "congestion_control_algorithm_id": algorithm_id,
                    "emulated_parent_run_id": emulated_parent_run_id,
                    "client_file_size_megabytes": client_file_size_megabytes,
                    "client_start_delay_ms": client_start_delay_ms,
                    "flow_completion_time_ms": flow_completion_time_ms,
                }
            ],
            prefer="return=representation",
        )
        if not rows:
            raise RuntimeError("Failed to insert emulated_runs row.")
        return int(rows[0]["id"])

    def insert_emulated_snapshot_stats(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        self._request("POST", "emulated_snapshot_stats", payload=rows, prefer="return=minimal")

    def insert_run(self, row: Dict[str, Any]) -> None:
        self._request("POST", "runs", payload=[row], prefer="return=minimal")


def persist_to_supabase(
    args: argparse.Namespace,
    payload: Dict[str, Any],
    started_at_utc: datetime.datetime,
    ended_at_utc: datetime.datetime,
    client_configs: List[ClientRunConfig],
) -> Dict[str, Any]:
    if not args.supabase_project_id or not args.supabase_service_role_key:
        raise ValueError("Both Supabase project id and service role key are required for persistence.")

    client = SupabaseRestClient(
        project_id=args.supabase_project_id,
        service_role_key=args.supabase_service_role_key,
        timeout_seconds=args.supabase_timeout_seconds,
    )

    algorithm_ids: Dict[str, int] = {}
    snapshot_length_ms = to_smallint(effective_snapshot_interval_ms(args), "snapshot_length_ms")
    if snapshot_length_ms <= 0:
        raise ValueError("snapshot_length_ms must be > 0")
    emulated_parent_run_id = client.insert_emulated_parent_run(
        number_of_clients=len(client_configs),
        bottleneck_rate_megabit=args.rate_mbit,
        queue_buffer_size_kilobyte=args.bottleneck_buffer_kbytes,
        snapshot_length_ms=snapshot_length_ms,
    )
    emulated_runs: List[Dict[str, Any]] = []
    receiver_summaries = payload.get("receivers", {})
    if not isinstance(receiver_summaries, dict):
        raise RuntimeError("Benchmark payload has invalid 'receivers' field; expected an object.")
    for config in client_configs:
        client_number = config.index
        delay_ms = config.delay_ms
        cca = config.cca
        try:
            receiver_summary = receiver_summaries[config.name]
            flow_completion_time_ms = to_int32(
                receiver_summary["flow_completion_time_ms"],
                f"{config.name} flow_completion_time_ms",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Benchmark payload missing flow completion timing for receiver {config.name}."
            ) from exc
        if cca not in algorithm_ids:
            algorithm_ids[cca] = client.get_or_create_algorithm_id(cca)
        emulated_run_id = client.insert_emulated_run(
            client_number=client_number,
            delay_added=to_smallint(delay_ms, f"client{client_number} delay"),
            algorithm_id=algorithm_ids[cca],
            emulated_parent_run_id=emulated_parent_run_id,
            client_file_size_megabytes=to_smallint(
                config.file_size_to_be_transferred_in_mbytes,
                f"client{client_number} file_size_to_be_transferred_in_mbytes",
            ),
            client_start_delay_ms=to_smallint(
                config.start_delay_ms,
                f"client{client_number} start_delay_ms",
            ),
            flow_completion_time_ms=flow_completion_time_ms,
        )
        emulated_runs.append(
            {
                "id": emulated_run_id,
                "client_number": client_number,
                "name": config.name,
                "cca": cca,
                "emulated_parent_run_id": emulated_parent_run_id,
                "flow_completion_time_ms": flow_completion_time_ms,
            }
        )

    snapshots = payload.get("snapshots", [])
    if not isinstance(snapshots, list):
        raise RuntimeError("Benchmark payload has invalid 'snapshots' field; expected a list.")

    config = payload.get("config", {})
    if not isinstance(config, dict):
        raise RuntimeError("Benchmark payload has invalid 'config' field; expected an object.")

    run_id = uuid.uuid4().hex
    ss_log_file = ""
    if args.snapshot_metrics_source == "ss":
        ss_log_file = str(config.get("ss_log_file", "")).strip()
        if not ss_log_file:
            raise RuntimeError("Benchmark payload missing ss_log_file for ss snapshot source.")

    emulated_snapshot_rows: List[Dict[str, Any]] = []
    for emulated_run in emulated_runs:
        emulated_run_id = int(emulated_run["id"])
        receiver_key = str(emulated_run["name"])
        for snapshot in snapshots:
            if not isinstance(snapshot, dict) or "snapshot_index" not in snapshot:
                continue
            snapshot_index = to_smallint(float(snapshot["snapshot_index"]), "snapshot_index")
            try:
                elapsed_microseconds = to_int32(
                    snapshot["elapsed_microseconds"],
                    "elapsed_microseconds",
                )
                megabits_per_second = to_decimal_text(
                    snapshot["receivers"][receiver_key]["megabits_per_second"],
                    f"{receiver_key} megabits_per_second",
                )
                round_trip_time_ms = to_decimal_text(
                    snapshot["receivers"][receiver_key]["rtt_ms"],
                    f"{receiver_key} rtt_ms",
                )
                bottleneck_queuing_delay_ms = to_decimal_text(
                    snapshot["bottleneck_queuing_delay_ms"],
                    "bottleneck_queuing_delay_ms",
                )
                bottleneck_backlog_bytes = to_int32(
                    snapshot["bottleneck_backlog_bytes"],
                    "bottleneck_backlog_bytes",
                )
                in_flight_packets = to_int32(
                    snapshot["receivers"][receiver_key]["in_flight_packets"],
                    f"{receiver_key} in_flight_packets",
                )
                congestion_window_bytes = to_int64(
                    snapshot["receivers"][receiver_key]["congestion_window_bytes"],
                    f"{receiver_key} congestion_window_bytes",
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Snapshot payload missing expected metrics for {receiver_key}."
                ) from exc
            emulated_snapshot_rows.append(
                {
                    "snapshot_index": snapshot_index,
                    "emulated_run_id": emulated_run_id,
                    "elapsed_microseconds": elapsed_microseconds,
                    "megabits_per_second": megabits_per_second,
                    "round_trip_time_ms": round_trip_time_ms,
                    "bottleneck_queuing_delay_ms": bottleneck_queuing_delay_ms,
                    "bottleneck_backlog_bytes": bottleneck_backlog_bytes,
                    "in_flight_packets": in_flight_packets,
                    "congestion_window_bytes": congestion_window_bytes,
                }
            )

    client.insert_emulated_snapshot_stats(emulated_snapshot_rows)

    max_delay_added_ms = max(to_smallint(cfg.delay_ms, f"{cfg.name} delay") for cfg in client_configs)
    run_row = {
        "id": run_id,
        "started_at": started_at_utc.isoformat(),
        "ended_at": ended_at_utc.isoformat(),
        "hostname": socket.gethostname(),
        "runner_version": "netem_cubic_benchmark.py",
        "delay_added_ms": max_delay_added_ms,
        "log_path": ss_log_file or None,
        "raw_log": payload,
    }
    client.insert_run(run_row)

    summary = {
        "run_id": run_id,
        "emulated_parent_run_id": emulated_parent_run_id,
        "algorithm_ids": algorithm_ids,
        "emulated_runs": emulated_runs,
        "emulated_snapshot_stats_rows": len(emulated_snapshot_rows),
    }
    print(json.dumps({"mode": "supabase_store", **summary}, indent=2), flush=True)
    return summary


def require_linux() -> None:
    if platform.system().lower() != "linux":
        print(
            "This script requires Linux networking tools (ip netns, tc).\n"
            "You're on macOS. Run this inside a Linux VM/container host.\n"
            "Example: Ubuntu VM, or Docker/Colima with --privileged.",
            file=sys.stderr,
        )
        sys.exit(1)


def require_root() -> None:
    if os.geteuid() != 0:
        print("Run as root (or with sudo).", file=sys.stderr)
        sys.exit(1)


def require_tools(snapshot_metrics_source: str) -> None:
    required_tools = ["ethtool", "ip", "tc", "sysctl", "python3"]
    if snapshot_metrics_source == "ss":
        required_tools.append("ss")
    missing = [tool for tool in required_tools if shutil.which(tool) is None]
    if missing:
        print(f"Missing required tools: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def sender_mode(args: argparse.Namespace) -> int:
    writer_interval = args.snapshot_writer_interval_seconds if args.snapshot_writer_interval_seconds > 0 else 0.01
    targets: List[Target] = []
    target_items = [item.strip() for item in args.targets.split(",") if item.strip()]
    if not target_items:
        raise ValueError("--targets must include at least one target.")
    for item in target_items:
        parts = item.split(":")
        if len(parts) < 3:
            continue
        name, host, port = parts[0], parts[1], parts[2]
        cca = parts[3] if len(parts) >= 4 else "cubic"
        if len(parts) >= 5:
            try:
                file_size_to_be_transferred_in_mbytes = float(parts[4])
            except ValueError:
                file_size_to_be_transferred_in_mbytes = DEFAULT_FILE_SIZE_TO_BE_TRANSFERRED_IN_MBYTES
        else:
            file_size_to_be_transferred_in_mbytes = DEFAULT_FILE_SIZE_TO_BE_TRANSFERRED_IN_MBYTES
        if len(parts) >= 6:
            try:
                start_delay_ms = float(parts[5])
            except ValueError:
                start_delay_ms = 0.0
        else:
            start_delay_ms = 0.0
        targets.append(
            Target(
                host=host,
                port=int(port),
                name=name,
                cca=cca,
                file_size_to_be_transferred_in_mbytes=file_size_to_be_transferred_in_mbytes,
                start_delay_ms=start_delay_ms,
            )
        )

    payload = b"x" * args.chunk_size
    errors: List[str] = []
    counter_paths = parse_snapshot_file_map(args.snapshot_bytes_files)
    rtt_paths = parse_snapshot_file_map(args.snapshot_rtt_ms_files)
    in_flight_paths = parse_snapshot_file_map(args.snapshot_in_flight_files)
    cwnd_paths = parse_snapshot_file_map(args.snapshot_cwnd_bytes_files)
    sent_by_target: Dict[str, int] = {target.name: 0 for target in targets} if counter_paths else {}
    rtt_ms_by_target: Dict[str, float] = {target.name: 0.0 for target in targets}
    in_flight_packets_by_target: Dict[str, int] = {target.name: 0 for target in targets}
    congestion_window_bytes_by_target: Dict[str, int] = {target.name: 0 for target in targets}
    capture_sent_bytes = bool(counter_paths)
    capture_sender_metrics = bool(rtt_paths or in_flight_paths or cwnd_paths)
    enable_snapshot_writer = bool(counter_paths or rtt_paths or in_flight_paths or cwnd_paths)
    lock = threading.Lock()
    stop_writer = threading.Event()

    def flush_snapshot_counters() -> None:
        if not counter_paths and not rtt_paths and not in_flight_paths and not cwnd_paths:
            return
        with lock:
            byte_snapshot = dict(sent_by_target)
            rtt_snapshot = dict(rtt_ms_by_target)
            in_flight_snapshot = dict(in_flight_packets_by_target)
            cwnd_snapshot = dict(congestion_window_bytes_by_target)
        for name, value in byte_snapshot.items():
            bytes_path = counter_paths.get(name)
            if bytes_path:
                write_counter_file(bytes_path, value)
            rtt_path = rtt_paths.get(name)
            if rtt_path:
                write_float_file(rtt_path, rtt_snapshot.get(name, 0.0))
            in_flight_path = in_flight_paths.get(name)
            if in_flight_path:
                write_counter_file(in_flight_path, in_flight_snapshot.get(name, 0))
            cwnd_path = cwnd_paths.get(name)
            if cwnd_path:
                write_counter_file(cwnd_path, cwnd_snapshot.get(name, 0))

    def writer_loop() -> None:
        while not stop_writer.wait(writer_interval):
            flush_snapshot_counters()

    def send_one(target: Target) -> None:
        try:
            bytes_for_target = int(target.file_size_to_be_transferred_in_mbytes * BYTES_PER_MEGABYTE)
            if bytes_for_target <= 0:
                raise RuntimeError(
                    f"file_size_to_be_transferred_in_mbytes must be > 0 for target {target.name}"
                )
            total_sent = 0
            sample_counter = 0
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(10)
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, target.cca.encode("ascii"))
                except OSError as exc:
                    raise RuntimeError(f"failed to set CCA '{target.cca}': {exc}") from exc
                if target.start_delay_ms > 0:
                    time.sleep(target.start_delay_ms / 1000.0)
                sock.connect((target.host, target.port))
                while total_sent < bytes_for_target:
                    remaining = bytes_for_target - total_sent
                    sent = sock.send(payload[: min(len(payload), remaining)])
                    if sent == 0:
                        raise RuntimeError(f"Socket closed early for {target.name}")
                    total_sent += sent
                    sample_counter += 1
                    if capture_sent_bytes:
                        with lock:
                            sent_by_target[target.name] += sent
                    if capture_sender_metrics and (sample_counter % 8 == 0 or total_sent >= bytes_for_target):
                        metrics = tcp_sender_metrics(sock)
                        with lock:
                            rtt_ms_by_target[target.name] = float(metrics["rtt_ms"])
                            in_flight_packets_by_target[target.name] = metrics["in_flight_packets"]
                            congestion_window_bytes_by_target[target.name] = metrics["congestion_window_bytes"]
                sock.shutdown(socket.SHUT_WR)
        except Exception as exc:
            with lock:
                errors.append(f"{target.name}: {exc}")

    writer: threading.Thread | None = None
    if enable_snapshot_writer:
        writer = threading.Thread(target=writer_loop, daemon=True)
        flush_snapshot_counters()
        writer.start()

    t0 = time.monotonic()
    threads = [threading.Thread(target=send_one, args=(target,), daemon=True) for target in targets]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    if writer is not None:
        stop_writer.set()
        writer.join()
        flush_snapshot_counters()

    if errors:
        raise RuntimeError("; ".join(errors))
    total = time.monotonic() - t0

    out = {
        "mode": "sender",
        "targets": {
            target.name: {
                "cca": target.cca,
                "file-size-to-be-transferred-in-mbytes": target.file_size_to_be_transferred_in_mbytes,
                "start_delay_ms": target.start_delay_ms,
            }
            for target in targets
        },
        "total_seconds": total,
    }
    print(json.dumps(out))
    return 0


def receiver_mode(args: argparse.Namespace) -> int:
    writer_interval = args.snapshot_writer_interval_seconds if args.snapshot_writer_interval_seconds > 0 else 0.01
    total = 0
    current_rtt_ms = 0.0
    start = None
    flow_start = None
    capture_receiver_rtt = bool(args.snapshot_rtt_ms_file)
    enable_snapshot_writer = bool(args.snapshot_bytes_file or args.snapshot_rtt_ms_file)
    lock = threading.Lock()
    stop_writer = threading.Event()

    def flush_snapshot_counter() -> None:
        with lock:
            current_bytes = total
            current_rtt = current_rtt_ms
        if args.snapshot_bytes_file:
            write_counter_file(args.snapshot_bytes_file, current_bytes)
        if args.snapshot_rtt_ms_file:
            write_float_file(args.snapshot_rtt_ms_file, current_rtt)

    def writer_loop() -> None:
        while not stop_writer.wait(writer_interval):
            flush_snapshot_counter()

    writer: threading.Thread | None = None
    if enable_snapshot_writer:
        writer = threading.Thread(target=writer_loop, daemon=True)
        flush_snapshot_counter()
        writer.start()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((args.listen_ip, args.port))
        srv.listen(1)
        if args.ready_file:
            Path(args.ready_file).write_text("ready\n", encoding="ascii")
        conn, _ = srv.accept()
        flow_start = time.monotonic()
        with conn:
            while True:
                chunk = conn.recv(args.chunk_size)
                if not chunk:
                    break
                if start is None:
                    start = time.monotonic()
                with lock:
                    total += len(chunk)
                    if capture_receiver_rtt:
                        current_rtt_ms = tcp_rtt_ms(conn)

    if writer is not None:
        stop_writer.set()
        writer.join()
        flush_snapshot_counter()

    with lock:
        final_total = total

    duration = (time.monotonic() - start) if start is not None else 0.0
    flow_completion_time_ms = int(round((time.monotonic() - flow_start) * 1000.0)) if flow_start is not None else 0
    out = {
        "mode": "receiver",
        "name": args.name,
        "bytes": final_total,
        "seconds": duration,
        "flow_completion_time_ms": flow_completion_time_ms,
        "megabits_per_second": (final_total * 8 / duration) / 1_000_000 if duration > 0 else 0.0,
    }
    print(json.dumps(out))
    return 0


class NetnsBench:
    def __init__(self, args: argparse.Namespace, client_configs: List[ClientRunConfig]):
        self.args = args
        self.id = uuid.uuid4().hex[:8]
        self.ns_switch = f"ns_switch_{self.id}"
        self.ns_transit = f"ns_transit_{self.id}"
        self.ns_fanout = f"ns_fanout_{self.id}"
        self.switch_bottleneck_veth = "veth_sm"
        self.transit_switch_veth = "veth_ms"
        self.transit_fanout_veth = "veth_mf"
        self.fanout_bottleneck_veth = "veth_fm"
        self.switch_bottleneck_ip = "10.30.0.1"
        self.transit_switch_ip = "10.30.0.2"
        self.transit_fanout_ip = "10.31.0.1"
        self.fanout_bottleneck_ip = "10.31.0.2"
        self.clients: List[ClientRunConfig] = []
        for cfg in client_configs:
            idx = cfg.index
            self.clients.append(
                ClientRunConfig(
                    index=idx,
                    name=cfg.name,
                    delay_ms=cfg.delay_ms,
                    cca=cfg.cca,
                    port=cfg.port,
                    start_delay_ms=cfg.start_delay_ms,
                    file_size_to_be_transferred_in_mbytes=cfg.file_size_to_be_transferred_in_mbytes,
                    sender_namespace=f"ns_input{idx}_{self.id}",
                    sender_veth=f"veth_in{idx}",
                    sender_router_namespace=f"ns_sender_router{idx}_{self.id}",
                    sender_router_veth_sender=f"veth_ris{idx}",
                    sender_router_veth_switch=f"veth_rsw{idx}",
                    switch_veth_in=f"veth_si{idx}",
                    sender_ip=f"10.20.{idx}.1",
                    sender_router_sender_ip=f"10.20.{idx}.254",
                    sender_router_switch_ip=f"10.21.{idx}.1",
                    switch_in_ip=f"10.21.{idx}.254",
                    fanout_veth_out=f"veth_fo{idx}",
                    namespace=f"ns_client{idx}_{self.id}",
                    veth_client=f"veth_out{idx}",
                    veth_router=f"veth_so{idx}",
                    client_ip=f"10.10.{idx}.1",
                    router_ip=f"10.10.{idx}.254",
                )
            )

    def _all_namespaces(self) -> List[str]:
        out = [self.ns_switch, self.ns_transit, self.ns_fanout]
        out.extend(client.sender_router_namespace for client in self.clients)
        out.extend(client.sender_namespace for client in self.clients)
        out.extend(client.namespace for client in self.clients)
        return out

    def _ns(self, namespace: str, cmd: str) -> str:
        return ns_shell(namespace, cmd)

    def _read_bottleneck_qdisc_stats(self) -> Dict[str, int]:
        out = run_and_grab_shell(
            self._ns(
                self.ns_switch,
                shell_words(TC_PATH, "-s", "qdisc", "show", "dev", self.switch_bottleneck_veth),
            ),
            verbose=False,
        )
        return {"sent_bytes": parse_tc_sent_bytes(out), "backlog_bytes": parse_tc_backlog_bytes(out)}

    def _read_bottleneck_backlog_bytes(self) -> int:
        return self._read_bottleneck_qdisc_stats()["backlog_bytes"]

    def _read_bottleneck_sent_bytes(self) -> int:
        return self._read_bottleneck_qdisc_stats()["sent_bytes"]

    def _read_interface_counter_bytes(self, namespace: str, interface: str, counter_name: str) -> int:
        out = run_and_grab_shell(
            self._ns(namespace, shell_words("cat", f"/sys/class/net/{interface}/statistics/{counter_name}")),
            verbose=False,
        )
        try:
            return int(out.strip())
        except ValueError:
            return 0

    def _read_snapshot_byte_counters(self) -> Dict[str, int]:
        counters = {
            client.name: self._read_interface_counter_bytes(client.namespace, client.veth_client, "rx_bytes")
            for client in self.clients
        }
        bottleneck_stats = self._read_bottleneck_qdisc_stats()
        counters["__total__"] = bottleneck_stats["sent_bytes"]
        counters["__bottleneck_backlog_bytes__"] = bottleneck_stats["backlog_bytes"]
        return counters

    def _read_ss_metrics_sample(self) -> tuple[str, Dict[str, SSSocketMetrics]]:
        raw_outputs: List[str] = []
        metrics_by_name: Dict[str, SSSocketMetrics] = {}
        for client in self.clients:
            out = run_and_grab_shell(
                self._ns(client.sender_namespace, shell_words(SS_PATH, "-tinmOHn")),
                verbose=False,
            )
            raw_outputs.append(f"# {client.name}\n{out}")
            metrics_by_peer = parse_ss_metrics_by_peer(out)
            metrics = metrics_by_peer.get((client.client_ip, client.port))
            if metrics is not None:
                metrics_by_name[client.name] = metrics
        return "\n".join(raw_outputs), metrics_by_name

    def setup(self) -> None:
        c = ""
        for ns in self._all_namespaces():
            c += shell_words(IP_PATH, "netns", "add", ns) + "\n"

        c += shell_words(
            IP_PATH,
            "link",
            "add",
            self.switch_bottleneck_veth,
            "type",
            "veth",
            "peer",
            "name",
            self.transit_switch_veth,
        ) + "\n"
        c += shell_words(
            IP_PATH,
            "link",
            "add",
            self.transit_fanout_veth,
            "type",
            "veth",
            "peer",
            "name",
            self.fanout_bottleneck_veth,
        ) + "\n"
        for client in self.clients:
            c += shell_words(IP_PATH, "link", "add", client.sender_veth, "type", "veth", "peer", "name", client.sender_router_veth_sender) + "\n"
            c += shell_words(IP_PATH, "link", "add", client.sender_router_veth_switch, "type", "veth", "peer", "name", client.switch_veth_in) + "\n"
            c += shell_words(IP_PATH, "link", "add", client.veth_client, "type", "veth", "peer", "name", client.fanout_veth_out) + "\n"

        c += shell_words(IP_PATH, "link", "set", self.switch_bottleneck_veth, "netns", self.ns_switch) + "\n"
        c += shell_words(IP_PATH, "link", "set", self.transit_switch_veth, "netns", self.ns_transit) + "\n"
        c += shell_words(IP_PATH, "link", "set", self.transit_fanout_veth, "netns", self.ns_transit) + "\n"
        c += shell_words(IP_PATH, "link", "set", self.fanout_bottleneck_veth, "netns", self.ns_fanout) + "\n"
        for client in self.clients:
            c += shell_words(IP_PATH, "link", "set", client.sender_veth, "netns", client.sender_namespace) + "\n"
            c += shell_words(IP_PATH, "link", "set", client.sender_router_veth_sender, "netns", client.sender_router_namespace) + "\n"
            c += shell_words(IP_PATH, "link", "set", client.sender_router_veth_switch, "netns", client.sender_router_namespace) + "\n"
            c += shell_words(IP_PATH, "link", "set", client.switch_veth_in, "netns", self.ns_switch) + "\n"
            c += shell_words(IP_PATH, "link", "set", client.veth_client, "netns", client.namespace) + "\n"
            c += shell_words(IP_PATH, "link", "set", client.fanout_veth_out, "netns", self.ns_fanout) + "\n"

        c += self._ns(self.ns_switch, shell_words(IP_PATH, "addr", "add", f"{self.switch_bottleneck_ip}/24", "dev", self.switch_bottleneck_veth)) + "\n"
        c += self._ns(self.ns_transit, shell_words(IP_PATH, "addr", "add", f"{self.transit_switch_ip}/24", "dev", self.transit_switch_veth)) + "\n"
        c += self._ns(self.ns_transit, shell_words(IP_PATH, "addr", "add", f"{self.transit_fanout_ip}/24", "dev", self.transit_fanout_veth)) + "\n"
        c += self._ns(self.ns_fanout, shell_words(IP_PATH, "addr", "add", f"{self.fanout_bottleneck_ip}/24", "dev", self.fanout_bottleneck_veth)) + "\n"
        for client in self.clients:
            c += self._ns(client.sender_namespace, shell_words(IP_PATH, "addr", "add", f"{client.sender_ip}/24", "dev", client.sender_veth)) + "\n"
            c += self._ns(client.sender_router_namespace, shell_words(IP_PATH, "addr", "add", f"{client.sender_router_sender_ip}/24", "dev", client.sender_router_veth_sender)) + "\n"
            c += self._ns(client.sender_router_namespace, shell_words(IP_PATH, "addr", "add", f"{client.sender_router_switch_ip}/24", "dev", client.sender_router_veth_switch)) + "\n"
            c += self._ns(self.ns_switch, shell_words(IP_PATH, "addr", "add", f"{client.switch_in_ip}/24", "dev", client.switch_veth_in)) + "\n"
            c += self._ns(client.namespace, shell_words(IP_PATH, "addr", "add", f"{client.client_ip}/24", "dev", client.veth_client)) + "\n"
            c += self._ns(self.ns_fanout, shell_words(IP_PATH, "addr", "add", f"{client.router_ip}/24", "dev", client.fanout_veth_out)) + "\n"

        for ns in self._all_namespaces():
            c += self._ns(ns, shell_words(IP_PATH, "link", "set", "lo", "up")) + "\n"

        c += self._ns(self.ns_switch, shell_words(IP_PATH, "link", "set", self.switch_bottleneck_veth, "up")) + "\n"
        c += self._ns(self.ns_transit, shell_words(IP_PATH, "link", "set", self.transit_switch_veth, "up")) + "\n"
        c += self._ns(self.ns_transit, shell_words(IP_PATH, "link", "set", self.transit_fanout_veth, "up")) + "\n"
        c += self._ns(self.ns_fanout, shell_words(IP_PATH, "link", "set", self.fanout_bottleneck_veth, "up")) + "\n"
        for client in self.clients:
            c += self._ns(client.sender_namespace, shell_words(IP_PATH, "link", "set", client.sender_veth, "up")) + "\n"
            c += self._ns(client.sender_router_namespace, shell_words(IP_PATH, "link", "set", client.sender_router_veth_sender, "up")) + "\n"
            c += self._ns(client.sender_router_namespace, shell_words(IP_PATH, "link", "set", client.sender_router_veth_switch, "up")) + "\n"
            c += self._ns(self.ns_switch, shell_words(IP_PATH, "link", "set", client.switch_veth_in, "up")) + "\n"
            c += self._ns(client.namespace, shell_words(IP_PATH, "link", "set", client.veth_client, "up")) + "\n"
            c += self._ns(self.ns_fanout, shell_words(IP_PATH, "link", "set", client.fanout_veth_out, "up")) + "\n"

        c += self._ns(self.ns_switch, shell_words(ETHTOOL_PATH, "-K", self.switch_bottleneck_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_transit, shell_words(ETHTOOL_PATH, "-K", self.transit_switch_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_transit, shell_words(ETHTOOL_PATH, "-K", self.transit_fanout_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        c += self._ns(self.ns_fanout, shell_words(ETHTOOL_PATH, "-K", self.fanout_bottleneck_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
        for client in self.clients:
            c += self._ns(client.sender_namespace, shell_words(ETHTOOL_PATH, "-K", client.sender_veth, "gro", "off", "gso", "off", "tso", "off")) + "\n"
            c += self._ns(client.sender_router_namespace, shell_words(ETHTOOL_PATH, "-K", client.sender_router_veth_sender, "gro", "off", "gso", "off", "tso", "off")) + "\n"
            c += self._ns(client.sender_router_namespace, shell_words(ETHTOOL_PATH, "-K", client.sender_router_veth_switch, "gro", "off", "gso", "off", "tso", "off")) + "\n"
            c += self._ns(self.ns_switch, shell_words(ETHTOOL_PATH, "-K", client.switch_veth_in, "gro", "off", "gso", "off", "tso", "off")) + "\n"
            c += self._ns(client.namespace, shell_words(ETHTOOL_PATH, "-K", client.veth_client, "gro", "off", "gso", "off", "tso", "off")) + "\n"
            c += self._ns(self.ns_fanout, shell_words(ETHTOOL_PATH, "-K", client.fanout_veth_out, "gro", "off", "gso", "off", "tso", "off")) + "\n"

        for client in self.clients:
            c += self._ns(client.sender_namespace, shell_words(IP_PATH, "route", "add", "default", "via", client.sender_router_sender_ip, "dev", client.sender_veth)) + "\n"
            c += self._ns(client.sender_router_namespace, shell_words(IP_PATH, "route", "add", "default", "via", client.switch_in_ip, "dev", client.sender_router_veth_switch)) + "\n"
            c += self._ns(client.namespace, shell_words(IP_PATH, "route", "add", "default", "via", client.router_ip, "dev", client.veth_client)) + "\n"
            c += self._ns(
                self.ns_switch,
                shell_words(IP_PATH, "route", "add", f"{client.sender_ip}/32", "via", client.sender_router_switch_ip, "dev", client.switch_veth_in),
            ) + "\n"
            c += self._ns(
                self.ns_switch,
                shell_words(IP_PATH, "route", "add", f"{client.client_ip}/32", "via", self.transit_switch_ip, "dev", self.switch_bottleneck_veth),
            ) + "\n"
            c += self._ns(
                self.ns_transit,
                shell_words(IP_PATH, "route", "add", f"{client.client_ip}/32", "via", self.fanout_bottleneck_ip, "dev", self.transit_fanout_veth),
            ) + "\n"
            c += self._ns(
                self.ns_transit,
                shell_words(IP_PATH, "route", "add", f"{client.sender_ip}/32", "via", self.switch_bottleneck_ip, "dev", self.transit_switch_veth),
            ) + "\n"
            c += self._ns(
                self.ns_fanout,
                shell_words(IP_PATH, "route", "add", f"{client.sender_ip}/32", "via", self.transit_fanout_ip, "dev", self.fanout_bottleneck_veth),
            ) + "\n"

        c += self._ns(self.ns_switch, shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"
        c += self._ns(self.ns_transit, shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"
        c += self._ns(self.ns_fanout, shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"
        for client in self.clients:
            c += self._ns(client.sender_router_namespace, shell_words("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")) + "\n"
            netem_cmd = f"{q(TC_PATH)} qdisc add dev {q(client.sender_router_veth_sender)} root netem delay {client.delay_ms}ms"
            if self.args.loss_pct > 0:
                netem_cmd += f" loss {self.args.loss_pct}%"
            c += self._ns(client.sender_router_namespace, netem_cmd) + "\n"

        if self.args.rate_mbit > 0 or self.args.bottleneck_buffer_kbytes > 0 or self.args.loss_pct > 0:
            bottleneck_cmd = f"{q(TC_PATH)} qdisc add dev {q(self.switch_bottleneck_veth)} root netem"
            if self.args.bottleneck_buffer_kbytes > 0:
                bottleneck_cmd += f" limit {buffer_kbytes_to_packet_limit(self.args.bottleneck_buffer_kbytes)}"
            if self.args.loss_pct > 0:
                bottleneck_cmd += f" loss {self.args.loss_pct}%"
            if self.args.rate_mbit > 0:
                bottleneck_cmd += f" rate {self.args.rate_mbit}mbit"
            c += self._ns(self.ns_switch, bottleneck_cmd) + "\n"

        run_shell(c)

    def cleanup(self) -> None:
        for ns in self._all_namespaces():
            run_shell(f"{q(IP_PATH)} netns del {q(ns)} 2>/dev/null", verbose=False, check=False)

    def _snapshot_rates(self, current: Dict[str, int], previous: Dict[str, int], dt: float) -> Dict[str, object]:
        dt = max(dt, 1e-9)

        def to_mbps(delta_bytes: int) -> float:
            return (delta_bytes * 8 / dt) / 1_000_000

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
        with tempfile.TemporaryDirectory(prefix=f"netem_bench_{self.id}_") as tempdir:
            ready_files: Dict[str, str] = {}
            sender_rtt_files: Dict[str, str] = {}
            sender_in_flight_files: Dict[str, str] = {}
            sender_cwnd_files: Dict[str, str] = {}
            receiver_procs: Dict[str, subprocess.Popen] = {}
            sender_procs: Dict[str, subprocess.Popen] = {}
            use_ss_sampler = self.args.snapshot_metrics_source == "ss"
            ss_log_file = resolve_ss_log_path(self.args.ss_log_file, self.id) if use_ss_sampler else ""

            for client in self.clients:
                ready_files[client.name] = os.path.join(tempdir, f"{client.name}.ready")
                if not use_ss_sampler:
                    sender_rtt_files[client.name] = os.path.join(tempdir, f"sender_{client.name}.rtt_ms")
                    sender_in_flight_files[client.name] = os.path.join(tempdir, f"sender_{client.name}.in_flight_packets")
                    sender_cwnd_files[client.name] = os.path.join(tempdir, f"sender_{client.name}.congestion_window_bytes")

                receiver_procs[client.name] = subprocess.Popen(
                    [
                        IP_PATH,
                        "netns",
                        "exec",
                        client.namespace,
                        PYTHON3_PATH,
                        script,
                        "--mode",
                        "receiver",
                        "--name",
                        client.name,
                        "--listen-ip",
                        client.client_ip,
                        "--port",
                        str(client.port),
                        "--chunk-size",
                        str(self.args.chunk_size),
                        "--ready-file",
                        ready_files[client.name],
                    ],
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
                    f"{client.name}:{client.client_ip}:{client.port}:{client.cca}:"
                    f"{client.file_size_to_be_transferred_in_mbytes}:{client.start_delay_ms}"
                )
                sender_cmd = [
                    IP_PATH,
                    "netns",
                    "exec",
                    client.sender_namespace,
                    PYTHON3_PATH,
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
                sampler = SSSampler(self, sample_interval_ms=self.args.ss_sample_interval_ms, log_path=ss_log_file)
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
                        rtt_ms = read_float_file(sender_rtt_files[client.name])
                        rates["receivers"][client.name]["rtt_ms"] = rtt_ms
                        in_flight_packets = read_counter_file(sender_in_flight_files[client.name])
                        congestion_window_bytes = read_counter_file(sender_cwnd_files[client.name])
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
            for client in self.clients:
                out, err = sender_procs[client.name].communicate(timeout=10)
                sender_out_by_name[client.name] = out
                sender_err_by_name[client.name] = err
            receiver_out_by_name: Dict[str, str] = {}
            receiver_err_by_name: Dict[str, str] = {}
            for client in self.clients:
                out, err = receiver_procs[client.name].communicate(timeout=10)
                receiver_out_by_name[client.name] = out
                receiver_err_by_name[client.name] = err

            for client in self.clients:
                proc = sender_procs[client.name]
                if proc.returncode != 0:
                    raise RuntimeError(f"{client.name} sender failed: {sender_err_by_name[client.name].strip()}")
            for client in self.clients:
                proc = receiver_procs[client.name]
                if proc.returncode != 0:
                    raise RuntimeError(f"{client.name} receiver failed: {receiver_err_by_name[client.name].strip()}")

            sender_summaries = {
                client.name: parse_single_json_line(sender_out_by_name[client.name], f"{client.name} sender")
                for client in self.clients
            }
            sender_summary = {
                "mode": "sender_group",
                "targets": {name: summary["targets"][name] for name, summary in sender_summaries.items()},
                "total_seconds": max((summary.get("total_seconds", 0.0) for summary in sender_summaries.values()), default=0.0),
            }
            receiver_summaries = {
                client.name: parse_single_json_line(receiver_out_by_name[client.name], f"{client.name} receiver")
                for client in self.clients
            }
            legacy_client_config: Dict[str, Any] = {}
            for client in self.clients[:2]:
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
                            "input_namespace": client.sender_namespace,
                            "sender_router_namespace": client.sender_router_namespace,
                            "switch_namespace": self.ns_switch,
                            "transit_namespace": self.ns_transit,
                            "fanout_namespace": self.ns_fanout,
                            "output_namespace": client.namespace,
                            "delay_ms": client.delay_ms,
                            "port": client.port,
                            "start_delay_ms": client.start_delay_ms,
                            "file-size-to-be-transferred-in-mbytes": client.file_size_to_be_transferred_in_mbytes,
                        }
                        for client in self.clients
                    },
                    "topology": {
                        "shape": "many-input-hosts -> many-sender-routers -> switch-fabric -> communal-bottleneck -> transit -> fanout -> many-output-queues",
                        "sender_router_namespaces": [client.sender_router_namespace for client in self.clients],
                        "switch_fabric_namespace": self.ns_switch,
                        "transit_namespace": self.ns_transit,
                        "fanout_namespace": self.ns_fanout,
                        "communal_bottleneck_device": self.switch_bottleneck_veth,
                        "inputs": [client.sender_namespace for client in self.clients],
                        "outputs": [client.namespace for client in self.clients],
                    },
                    "loss_pct": self.args.loss_pct,
                    "rate_mbit": self.args.rate_mbit,
                    "bottleneck_buffer_kbytes": self.args.bottleneck_buffer_kbytes,
                    "bottleneck_buffer_limit_packets": buffer_kbytes_to_packet_limit(self.args.bottleneck_buffer_kbytes),
                    "bottleneck_buffer_limit_bytes": buffer_kbytes_to_byte_limit(self.args.bottleneck_buffer_kbytes),
                    "bottleneck_tbf_burst_bytes": tbf_burst_bytes(self.args.rate_mbit),
                    "snapshot_metrics_source": self.args.snapshot_metrics_source,
                    "snapshot_interval_ms": effective_snapshot_interval_ms(self.args),
                    "ss_sample_interval_ms": self.args.ss_sample_interval_ms,
                    "ss_log_file": ss_log_file,
                },
            }


def orchestrator_mode(args: argparse.Namespace) -> int:
    client_configs = resolve_client_run_configs(args)

    require_linux()
    require_root()
    require_tools(args.snapshot_metrics_source)

    bench = NetnsBench(args, client_configs)
    keep = args.keep_namespaces
    started_at_utc = datetime.datetime.now(datetime.timezone.utc)
    result: Dict[str, Any]

    try:
        print_banner("Pre test Cleanup")
        bench.cleanup()

        print_banner("Setup Namespaces")
        bench.setup()

        print_banner("Run Tests")
        result = bench.run_benchmark()
    finally:
        if not keep:
            print_banner("Post Test Cleanup")
            bench.cleanup()

    if args.supabase_project_id and args.supabase_service_role_key:
        ended_at_utc = datetime.datetime.now(datetime.timezone.utc)
        persist_to_supabase(args, result, started_at_utc, ended_at_utc, client_configs)
    elif args.supabase_project_id or args.supabase_service_role_key:
        print(
            "Supabase persistence skipped: provide both --supabase-project-id and --supabase-service-role-key.",
            file=sys.stderr,
        )

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Multi-client TCP transfer benchmark with netem and configurable CCA.")
    p.add_argument("--mode", choices=["orchestrator", "sender", "receiver"], default="orchestrator")
    p.add_argument("--chunk-size", type=int, default=64 * 1024, help="Socket read/write chunk size in bytes.")

    p.add_argument(
        "--targets",
        default="",
        help=(
            "sender mode: comma list of "
            "name:host:port[:cca[:file_size_mbytes[:start_delay_ms]]]"
        ),
    )
    p.add_argument("--num-clients", type=int, default=2, help="orchestrator mode: number of clients to emulate.")
    p.add_argument(
        "--client-names",
        default="",
        help="orchestrator mode: comma list of client names (length must equal --num-clients).",
    )
    p.add_argument(
        "--client-delays-ms",
        default="",
        help="orchestrator mode: comma list of per-client delays in ms (length must equal --num-clients).",
    )
    p.add_argument(
        "--client-ccas",
        default="",
        help="orchestrator mode: comma list of per-client CCAs (length must equal --num-clients).",
    )
    p.add_argument(
        "--client-file-sizes-mbytes",
        default="",
        help="orchestrator mode: comma list of per-client file sizes in mbytes (length must equal --num-clients).",
    )
    p.add_argument(
        "--client-start-delays-ms",
        default="",
        help="orchestrator mode: comma list of per-client flow start delays in ms (length must equal --num-clients).",
    )

    p.add_argument(
        "--client1-cca",
        default="cubic",
        help="legacy default for client1 CCA when --client-ccas is omitted.",
    )
    p.add_argument(
        "--client2-cca",
        default="cubic",
        help="legacy default for client2 CCA when --client-ccas is omitted.",
    )

    p.add_argument("--name", default="", help="receiver mode: label")
    p.add_argument("--listen-ip", default="0.0.0.0", help="receiver mode: bind IP")
    p.add_argument("--port", type=int, default=5001, help="receiver mode: bind port")
    p.add_argument("--ready-file", default="", help="receiver mode: touch file when ready")
    p.add_argument("--snapshot-bytes-file", default="", help=argparse.SUPPRESS)
    p.add_argument("--snapshot-bytes-files", default="", help=argparse.SUPPRESS)
    p.add_argument("--snapshot-rtt-ms-file", default="", help=argparse.SUPPRESS)
    p.add_argument("--snapshot-rtt-ms-files", default="", help=argparse.SUPPRESS)
    p.add_argument("--snapshot-in-flight-files", default="", help=argparse.SUPPRESS)
    p.add_argument("--snapshot-cwnd-bytes-files", default="", help=argparse.SUPPRESS)
    p.add_argument("--snapshot-writer-interval-seconds", type=float, default=0.01, help=argparse.SUPPRESS)

    p.add_argument(
        "--client1-delay-ms",
        type=float,
        default=20.0,
        help="legacy default delay for client1 when --client-delays-ms is omitted.",
    )
    p.add_argument(
        "--client2-delay-ms",
        type=float,
        default=20.0,
        help="legacy default delay for client2 when --client-delays-ms is omitted.",
    )
    p.add_argument("--loss-pct", type=float, default=0.0, help="netem packet loss percent")
    p.add_argument(
        "--bottleneck-all-client-rate-mbit",
        dest="rate_mbit",
        type=float,
        default=100.0,
        help="shared bottleneck rate in mbit/s across all clients (switch->transit communal bottleneck path)",
    )
    p.add_argument(
        "--bottleneck-buffer-kbytes",
        type=float,
        default=0.0,
        help="optional bottleneck queue size in kbytes; mapped to the TBF byte limit (0 uses an internal default).",
    )
    p.add_argument(
        "--snapshot-interval-ms",
        type=int,
        default=10,
        help="Snapshot interval for synchronized rate output in milliseconds.",
    )
    p.add_argument(
        "--snapshot-metrics-source",
        choices=("kernel", "ss"),
        default="kernel",
        help="Metric source for snapshots: legacy kernel counters/TCP_INFO or out-of-band `ss` sampling.",
    )
    p.add_argument(
        "--ss-sample-interval-ms",
        type=int,
        default=100,
        help="Sampling interval in milliseconds when --snapshot-metrics-source=ss.",
    )
    p.add_argument(
        "--ss-log-file",
        default="",
        help="Path to write raw `ss` sampler output as JSONL when --snapshot-metrics-source=ss. Defaults to ./ss_sampler_<benchid>.jsonl.",
    )
    p.add_argument(
        "--snapshot-interval-seconds",
        dest="snapshot_interval_seconds_legacy",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--supabase-project-id",
        default=os.environ.get("SUPABASE_PROJECT_ID", "regphejnlvfpyokpniny"),
        help="Supabase project id. Defaults to SUPABASE_PROJECT_ID env var.",
    )
    p.add_argument(
        "--supabase-service-role-key",
        default=os.environ.get(
            "SUPABASE_SERVICE_ROLE_KEY",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJlZ3BoZWpubHZmcHlva3BuaW55Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NDEwMzYyMCwiZXhwIjoyMDc5Njc5NjIwfQ.875_8XmyO48jk1aozUbYEB8ys8YhcGApCJ1P4uiMXFY",
        ),
        help="Supabase service role key. Uses hardcoded default when env var is unset.",
    )
    p.add_argument(
        "--supabase-timeout-seconds",
        type=float,
        default=15.0,
        help="HTTP timeout for Supabase REST requests.",
    )
    p.add_argument("--keep-namespaces", action="store_true", help="Do not delete namespaces after run")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.snapshot_interval_seconds_legacy is not None:
        args.snapshot_interval_ms = to_positive_smallint_milliseconds(
            args.snapshot_interval_seconds_legacy,
            "snapshot_interval_ms",
        )
    if args.mode == "sender":
        return sender_mode(args)
    if args.mode == "receiver":
        return receiver_mode(args)
    return orchestrator_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
