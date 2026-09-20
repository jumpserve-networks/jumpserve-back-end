"""Root-only EC2 runner. All measured TCP sockets bind to the WireGuard overlay."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import urllib.request

ROOT = Path("/var/lib/jumpserve")


def run(*args, check=True, input=None):
    result = subprocess.run(args, check=False, input=input, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(args[0] + ": " + result.stderr.strip()[-1000:])
    return result.stdout.strip()


def prepare():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = ROOT / "private-key"
    if not key.exists():
        key.write_text(run("wg", "genkey") + "\n")
        key.chmod(0o600)
    return {"public_key": run("wg", "pubkey", input=key.read_text()), "kernel": platform.release()}


def qdisc_commands(config):
    # Both directions use wg0, but only server -> receivers enters the shared FIFO.
    # ACKs use the separate high-rate class. No per-receiver shaping/fair queueing.
    return [
        ["tc", "qdisc", "replace", "dev", "wg0", "root", "handle", "1:", "htb", "default", "20"],
        ["tc", "class", "replace", "dev", "wg0", "parent", "1:", "classid", "1:10", "htb", "rate", f'{config["rate_mbit"]}mbit', "ceil", f'{config["rate_mbit"]}mbit', "burst", "1600", "cburst", "1600"],
        ["tc", "class", "replace", "dev", "wg0", "parent", "1:", "classid", "1:20", "htb", "rate", "10gbit"],
        ["tc", "qdisc", "replace", "dev", "wg0", "parent", "1:10", "handle", "10:", "bfifo", "limit", str(config["buffer_kbytes"] * 1000)],
        ["tc", "filter", "replace", "dev", "wg0", "parent", "1:", "protocol", "ip", "prio", "1", "u32", "match", "ip", "src", "10.254.0.2/32", "flowid", "1:10"],
    ]


def peer_config(node, nodes):
    hub = next(item for item in nodes if item["role"] == "bottleneck")
    peers = [item for item in nodes if item["name"] != node["name"]] if node["role"] == "bottleneck" else [hub]
    lines = ["[Interface]", "PrivateKey = " + (ROOT / "private-key").read_text().strip(), "ListenPort = 51820"]
    for peer in peers:
        allowed = peer["overlay_ip"] + "/32" if node["role"] == "bottleneck" else "10.254.0.0/24"
        lines += ["[Peer]", "PublicKey = " + peer["public_key"], "AllowedIPs = " + allowed,
                  f'Endpoint = {peer["public_ip"]}:51820', "PersistentKeepalive = 15"]
    return "\n".join(lines) + "\n"


def configure(config):
    node, nodes = config["node"], config["nodes"]
    prepare()
    run("modprobe", "wireguard")
    run("modprobe", "tcp_" + config["cca"], check=False)
    available = run("sysctl", "-n", "net.ipv4.tcp_available_congestion_control").split()
    if config["cca"] not in available:
        raise RuntimeError("Requested CCA is unavailable in this kernel: " + config["cca"])
    run("sysctl", "-w", "net.ipv4.tcp_congestion_control=" + config["cca"])
    run("ip", "link", "del", "wg0", check=False)
    run("ip", "link", "add", "wg0", "type", "wireguard")
    (ROOT / "wg.conf").write_text(peer_config(node, nodes))
    (ROOT / "wg.conf").chmod(0o600)
    run("wg", "setconf", "wg0", str(ROOT / "wg.conf"))
    run("ip", "address", "add", node["overlay_ip"] + "/24", "dev", "wg0")
    run("ip", "link", "set", "wg0", "mtu", "1380", "up")
    run("ethtool", "-K", "wg0", "gro", "off", "gso", "off", "tso", "off")
    run("sysctl", "-w", "net.ipv4.conf.all.rp_filter=0", "net.ipv4.conf.wg0.rp_filter=0")
    # Defense in depth: no measured service on the underlay, no forwarding bypass.
    run("iptables", "-P", "FORWARD", "DROP")
    run("iptables", "-F", "FORWARD")
    if node["role"] == "bottleneck":
        run("sysctl", "-w", "net.ipv4.ip_forward=1")
        for receiver in (item for item in nodes if item["role"] == "receiver"):
            for source, dest in [("10.254.0.2", receiver["overlay_ip"]), (receiver["overlay_ip"], "10.254.0.2")]:
                run("iptables", "-A", "FORWARD", "-i", "wg0", "-o", "wg0", "-s", source, "-d", dest, "-j", "ACCEPT")
        for command in qdisc_commands(config):
            run(*command)
    else:
        run("sysctl", "-w", "net.ipv4.ip_forward=0")
        run("tc", "qdisc", "replace", "dev", "wg0", "root", "fq")
    if node["role"] == "server":
        for receiver in (item for item in nodes if item["role"] == "receiver"):
            run("iperf3", "-s", "-D", "-B", node["overlay_ip"], "-p", str(receiver["port"]),
                "--logfile", str(ROOT / f'{receiver["name"]}-server.log'))
    if run("timedatectl", "show", "-p", "NTPSynchronized", "--value") != "yes":
        raise RuntimeError("Clock synchronization is not ready.")
    return {"configured": True, "cca": config["cca"], "kernel": platform.release()}


def preflight(config):
    node = config["node"]
    destination = "10.254.0.1" if node["role"] == "server" else "10.254.0.2"
    ping = run("ping", "-I", "wg0", "-c", "3", "-W", "5", destination)
    route = json.loads(run("ip", "-j", "route", "get", destination))
    if route[0].get("dev") != "wg0":
        raise RuntimeError("Test route does not use the bottleneck overlay.")
    (ROOT / "preflight.json").write_text(json.dumps({"ping": ping, "route": route}))
    return {"ready": True}


def receiver_command(config):
    node = config["node"]
    return ["iperf3", "-c", "10.254.0.2", "-B", node["overlay_ip"], "-p", str(node["port"]),
            "-R", "-C", config["cca"], "-t", str(config["duration_seconds"]), "-i", "1", "-J", "--get-server-output"]


def upload(url, data):
    payload = json.dumps(data).encode()
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=payload, method="PUT"), timeout=30) as response:
                if response.status == 200:
                    return
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def measure(config):
    node = config["node"]
    report = {"node": node["name"], "success": False, "kernel": platform.release(), "cca": config["cca"],
              "iperf_version": run("iperf3", "--version"), "planned_start_epoch": config["start_epoch"], "samples": []}
    report["job_id"] = config["job_id"]
    report["runtime_revision"] = config["runtime_revision"]
    report["configuration"] = {key: config[key] for key in ("server", "bottleneck", "receivers", "cca", "duration_seconds", "rate_mbit", "buffer_kbytes", "notes")}
    report["placement"] = {key: node.get(key) for key in ("name", "region", "zone_id", "instance_type", "instance_id", "image_id", "overlay_ip")}
    try:
        wait = config["start_epoch"] - time.time()
        if wait < -2:
            raise RuntimeError("Missed the common start barrier.")
        time.sleep(max(0, wait))
        report["started_at"] = time.time()
        report["preflight"] = json.loads((ROOT / "preflight.json").read_text())
        report["routes"] = json.loads(run("ip", "-j", "route"))
        report["available_ccas"] = run("sysctl", "-n", "net.ipv4.tcp_available_congestion_control")
        report["effective_cca"] = run("sysctl", "-n", "net.ipv4.tcp_congestion_control")
        if report["effective_cca"] != config["cca"]:
            raise RuntimeError("Effective server CCA differs from requested CCA.")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = None
            if node["role"] == "receiver":
                future = pool.submit(subprocess.run, receiver_command(config), capture_output=True, text=True,
                                     timeout=config["duration_seconds"] + 45)
            # Sender/queue traces bracket the common transfer window.
            end = config["start_epoch"] + config["duration_seconds"] + 10
            while time.time() < end:
                report["samples"].append({"epoch": time.time(), "ss": run("ss", "-tinm", check=False),
                    "qdisc": json.loads(run("tc", "-j", "-s", "qdisc", "show", "dev", "wg0")),
                    "load": os.getloadavg()})
                time.sleep(1)
            if future:
                completed = future.result()
                report["iperf"] = json.loads(completed.stdout)
                if completed.returncode or "error" in report["iperf"]:
                    raise RuntimeError(report["iperf"].get("error", completed.stderr)[-1000:])
                if report["iperf"].get("end", {}).get("sum_received", {}).get("bytes", 0) <= 0:
                    raise RuntimeError("No test bytes received.")
                reported_cca = report["iperf"].get("end", {}).get("sender_tcp_congestion")
                if reported_cca and reported_cca != config["cca"]:
                    raise RuntimeError("iperf sender reported a different congestion control algorithm.")
        report["wireguard"] = run("wg", "show", "wg0", "transfer")
        report["forward_counters"] = run("iptables", "-nvx", "-L", "FORWARD")
        report["success"] = True
    except Exception as error:
        report["error"] = str(error)[-1000:]
    finally:
        report["finished_at"] = time.time()
        (ROOT / "result.json").write_text(json.dumps(report))
        upload(config["upload_url"], report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "configure", "preflight", "start", "measure"])
    parser.add_argument("config", nargs="?")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text()) if args.config else {}
    if args.action == "prepare":
        result = prepare()
    elif args.action == "configure":
        result = configure(config)
    elif args.action == "preflight":
        result = preflight(config)
    elif args.action == "start":
        # Re-delivery must never create a second measurement.
        if not (ROOT / "started").exists():
            run("systemd-run", "--unit=jumpserve-test", "--property=RuntimeMaxSec=900", "/usr/bin/python3",
                str(Path(__file__).resolve()), "measure", str(Path(args.config).resolve()))
            (ROOT / "started").touch()
        result = {"started": True}
    else:
        measure(config)
        result = {"finished": True}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
