"""Offline Linux integration checks for real TCP_INFO snapshot measurements.

Run as root in a disposable Linux environment with iproute2 and ethtool:
    python3 -B -m unittest test_kernel_metrics -v
No Supabase credentials or external network access are used.
"""

import importlib
import json
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNNERS = ("netem_cubic_benchmark_nines", "netem_cubic_benchmark_hotnets", "netem_nines")


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0, "Requires Linux network namespaces and root")
class KernelMetricsTests(unittest.TestCase):
    def test_real_kernel_snapshots_for_each_runner(self):
        for runner in RUNNERS:
            with self.subTest(runner=runner):
                result = subprocess.run(
                    [sys.executable, "-B", __file__, "--run", runner],
                    cwd=ROOT, capture_output=True, text=True, timeout=180,
                    env={**os.environ, "SUPABASE_PROJECT_ID": "", "SUPABASE_SERVICE_ROLE_KEY": ""},
                )
                self.assertEqual(result.returncode, 0, result.stderr[-4000:])
                report = json.loads(result.stdout)
                self.assertEqual(len(report["clients"]), 2)
                for client in report["clients"]:
                    self.assertEqual(client["bytes_received"], 100_000_000)
                    for metric in ("rtt_ms", "congestion_window_bytes", "in_flight_packets"):
                        self.assertGreater(client[metric]["positive_samples"], 0, f"{runner}: {client}")
                    self.assertGreater(client["megabits_per_second"]["positive_samples"], 0)
                print(json.dumps(report), flush=True)


def run_benchmark(runner):
    import contextlib
    import io

    if runner not in RUNNERS:
        raise ValueError("Unsupported runner")
    module = importlib.import_module(runner)
    args = module.build_parser().parse_args([
        "--num-clients", "2",
        "--client-ccas", os.environ.get("JUMPSERVE_TEST_CCAS", "cubic,bbr"),
        "--client-delays-ms", "10,60",
        "--client-file-sizes-mbytes", "100,100",
        "--client-start-delays-ms", "0,0",
        "--bottleneck-all-client-rate-mbit", "100",
        "--bottleneck-buffer-kbytes", "125",
        "--snapshot-metrics-source", "kernel",
        "--snapshot-interval-ms", "10",
        "--supabase-project-id", "", "--supabase-service-role-key", "",
    ])
    configs = module.resolve_client_run_configs(args)
    # Fail before creating namespaces or receivers if the kernel lacks a CCA.
    for config in configs:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, config.cca.encode("ascii"))
            except OSError as error:
                raise RuntimeError(f"Kernel does not support requested CCA {config.cca!r}") from error
    bench_class = module.ProxyDelayTwoFlowBench if runner == "netem_nines" else module.NetnsBench
    bench = bench_class(args, configs)
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            bench.setup()
            result = bench.run_benchmark()
        finally:
            bench.cleanup()
    clients = []
    for config in configs:
        client = {"name": config.name, "cca": config.cca,
                  "bytes_received": result["receivers"][config.name]["bytes"],
                  "snapshot_count": len(result["snapshots"])}
        for metric in ("rtt_ms", "congestion_window_bytes", "in_flight_packets", "megabits_per_second"):
            values = [snapshot["receivers"][config.name][metric] for snapshot in result["snapshots"]]
            positive = [value for value in values if value > 0]
            client[metric] = {"positive_samples": len(positive),
                              "min_positive": min(positive, default=None), "max": max(values, default=None)}
        clients.append(client)
    print(json.dumps({"runner": runner, "clients": clients}), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run":
        run_benchmark(sys.argv[2])
    else:
        unittest.main()
