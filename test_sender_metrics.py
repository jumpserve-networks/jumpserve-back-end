"""Sender snapshot regressions; no network access or Linux privileges required."""

import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUNNERS = ("netem_cubic_benchmark_nines", "netem_cubic_benchmark_hotnets")
METRICS = {
    9001: {"rtt_ms": 21.25, "in_flight_packets": 12, "congestion_window_bytes": 18000},
    9002: {"rtt_ms": 121.75, "in_flight_packets": 24, "congestion_window_bytes": 36000},
}
OUTPUTS = {
    "rtt_ms": "--snapshot-rtt-ms-files",
    "in_flight_packets": "--snapshot-in-flight-files",
    "congestion_window_bytes": "--snapshot-cwnd-bytes-files",
}


class FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, timeout):
        pass

    def setsockopt(self, *args):
        pass

    def connect(self, address):
        self.port = address[1]

    def send(self, payload):
        return len(payload)

    def shutdown(self, how):
        pass


class SenderMetricsTests(unittest.TestCase):
    def check_sender(self, module, metric_names, byte_clients=()):
        with tempfile.TemporaryDirectory(prefix="sender-metrics-test-") as directory:
            folder = Path(directory)
            argv = [
                "--targets", "client1:127.0.0.1:9001:cubic:0.002,client2:127.0.0.1:9002:cubic:0.003",
                "--chunk-size", "128",
                # Keep this offline even if credentials are configured locally.
                "--supabase-project-id", "", "--supabase-service-role-key", "",
            ]
            for metric in metric_names:
                argv.extend([OUTPUTS[metric], ",".join(
                    f"{client}:{folder / (client + '.' + metric)}" for client in ("client1", "client2")
                )])
            if byte_clients:
                argv.extend(["--snapshot-bytes-files", ",".join(
                    f"{client}:{folder / (client + '.bytes')}" for client in byte_clients
                )])
            args = module.build_parser().parse_args(argv)
            with (
                patch.object(module.socket, "socket", side_effect=lambda *args: FakeSocket()),
                patch.object(module.socket, "TCP_CONGESTION", 13, create=True),
                patch.object(module, "tcp_sender_metrics", side_effect=lambda sock: METRICS[sock.port]) as sample,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(module.sender_mode(args), 0)
            if metric_names:
                self.assertGreaterEqual(sample.call_count, 2)
            else:
                sample.assert_not_called()
            expected_files = set()
            for client, port in (("client1", 9001), ("client2", 9002)):
                for metric in metric_names:
                    filename = f"{client}.{metric}"
                    expected_files.add(filename)
                    self.assertTrue((folder / filename).is_file(), f"Missing {filename}")
                    self.assertEqual(float((folder / filename).read_text()), METRICS[port][metric])
                if client in byte_clients:
                    filename = f"{client}.bytes"
                    expected_files.add(filename)
                    self.assertEqual(int((folder / filename).read_text()), 2000 if client == "client1" else 3000)
            self.assertEqual({path.name for path in folder.iterdir()}, expected_files)

    def test_tcp_metrics_without_byte_counters(self):
        for name in RUNNERS:
            with self.subTest(runner=name):
                self.check_sender(importlib.import_module(name), tuple(OUTPUTS))

    def test_each_tcp_metric_can_be_enabled_independently(self):
        for name in RUNNERS:
            for metric in OUTPUTS:
                with self.subTest(runner=name, metric=metric):
                    self.check_sender(importlib.import_module(name), (metric,))

    def test_tcp_metrics_with_partial_byte_counter_configuration(self):
        for name in RUNNERS:
            with self.subTest(runner=name):
                self.check_sender(importlib.import_module(name), tuple(OUTPUTS), ("client1",))

    def test_byte_counters_without_tcp_metrics(self):
        for name in RUNNERS:
            with self.subTest(runner=name):
                self.check_sender(importlib.import_module(name), (), ("client1", "client2"))

    def test_no_snapshot_outputs(self):
        for name in RUNNERS:
            with self.subTest(runner=name):
                self.check_sender(importlib.import_module(name), ())


if __name__ == "__main__":
    unittest.main()
