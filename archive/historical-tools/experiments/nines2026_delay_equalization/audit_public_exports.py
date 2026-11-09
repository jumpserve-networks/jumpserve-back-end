#!/usr/bin/env python3
"""Verify public website exports against complete local research evidence.

Checks every published summary/cell and both trials of the first scheduled block
in each of the four experiment/CCA groups. No cookies or credentials are sent.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent


def same(actual, expected, label):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(label + ": numeric value differs")
    elif actual != expected:
        raise ValueError(label + ": value differs")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:3001")
    parser.add_argument("--results", type=Path, default=HERE/"results"/"campaign-v1")
    args = parser.parse_args()
    manifest = json.loads((HERE/"campaign-v1.json").read_text())
    analysis = json.loads((args.results/"analysis.json").read_text())
    if analysis["missing"] or analysis["counts"] != {"completed":820,"invalid":0,"failed":0}:
        raise ValueError("Public final-export audit requires a complete valid campaign")
    exports = args.results/"public-exports"
    exports.mkdir(exist_ok=True)
    hashes = {}

    def fetch(suffix, name):
        url = args.base_url.rstrip("/") + "/module/propagation-delay-study" + suffix
        with urlopen(url, timeout=60) as response:
            if response.status != 200 or response.headers.get("Cache-Control") != "no-store":
                raise ValueError("Unexpected public export response: " + suffix)
            if not response.headers.get("Content-Disposition", "").startswith("attachment;"):
                raise ValueError("Export is not an attachment: " + suffix)
            body = response.read()
        data = json.loads(body)
        same(data["schema_version"], 1, "schema")
        units = {"throughput":"Mbps","delay":"ms","duration":"s"}
        units.update({"sensitivity":"log2(max mean goodput / min mean goodput)"} if suffix == "/data" else {"payload":"bytes"})
        same(data["units"], units, "units")
        (exports/name).write_bytes(body)
        hashes[name] = hashlib.sha256(body).hexdigest()
        return data

    public = fetch("/data", "study.json")
    same(public["campaign"]["status"], "completed", "campaign status")
    same(public["campaign"]["id"], manifest["id"], "campaign identity")
    same(public["campaign"]["protocol_sha256"],hashlib.sha256((HERE/"PROTOCOL.md").read_bytes()).hexdigest(),"protocol hash")
    same(public["campaign"]["manifest_sha256"],hashlib.sha256((HERE/"campaign-v1.json").read_bytes()).hexdigest(),"schedule hash")
    same(len(public["trials"]), 820, "trial count")
    same({t["id"] for t in public["trials"]}, {t["id"] for t in manifest["trials"]}, "trial identities")
    same({t["status"] for t in public["trials"]}, {"completed"}, "trial status")
    same(len(public["summaries"]), len(analysis["summaries"]), "summary count")
    for expected in analysis["summaries"]:
        row = next(s for s in public["summaries"] if (s["family"],s["cca_group"],s["flow_index"]) ==
                   (expected["family"],expected["cca_group"],expected["flow_index"]))
        for key,value in expected.items():
            if key != "campaign_id":
                same(row[key], value, "summary "+key)
    configs = {c["id"]:c for c in public["configurations"]}
    same(sum(len(c["delay_study_cells"]) for c in configs.values()), len(analysis["cells"]), "cell count")
    for expected in analysis["cells"]:
        row = next(c for c in configs[expected["configuration_id"]]["delay_study_cells"]
                   if c["flow_index"] == expected["flow_index"])
        for key,value in expected.items():
            if key != "configuration_id":
                same(row[key], value, "cell "+key)
    selected = {}
    for trial in sorted(manifest["trials"], key=lambda t:(t["block_index"],t["id"])):
        group = (trial["config"]["family"],tuple(trial["config"]["ccas"]))
        selected.setdefault(group,trial["block_index"])
    same(len(selected),4,"experiment groups")
    sample_count = flow_count = trial_count = 0
    for planned in manifest["trials"]:
        if planned["block_index"] not in selected.values():
            continue
        identity = planned["id"]
        directory = args.results/f"worker-{planned['block_index'] % 8}"/identity
        raw = json.loads((directory/"result.json").read_text())
        data = fetch(f"/test-results/{identity}/data", identity+".json")
        same(data["campaign_id"],manifest["id"],"trial campaign")
        for key in ("family","treatment","capacity_mbps","queue_packets","duration_seconds","warmup_seconds","target_rtt_ms"):
            same(data["configuration"].get(key),raw["config"].get(key),"configuration "+key)
        for key in ("id","replicate","block_index","status","start_order","runner_sha256"):
            same(data["trial"][key],raw[key],"trial "+key)
        pair = next(t for t in manifest["trials"] if t["block_index"] == planned["block_index"] and t["id"] != identity)
        same(data["paired"]["id"],pair["id"],"pair identity")
        same(len(data["flows"]),len(raw["flows"]),"flow count")
        expected_samples = 0
        for flow in raw["flows"]:
            i = flow["flow"]
            exported = next(f for f in data["flows"] if f["flow_index"] == i)
            for key in ("cca","configured_base_rtt_ms","goodput_mbps","measured_seconds","received_bytes","retransmits"):
                same(exported[key],flow[key],"flow "+key)
            calibration = next(c for c in raw["calibration"] if c["flow"] == i)
            for key in ("natural_min_rtt_ms","added_ack_delay_ms","effective_min_rtt_ms"):
                same(exported[key],calibration[key],"calibration "+key)
            receiver = json.loads((directory/f"receiver-{i}.json").read_text())
            points = sorted((p for p in data["samples"] if p["flow_index"] == i),key=lambda p:p["snapshot_index"])
            same(len(points),len(receiver["intervals"]),"receiver interval count")
            for index,(point,interval) in enumerate(zip(points,receiver["intervals"])):
                measured = interval["sum"]
                same(point["snapshot_index"],index,"snapshot identity")
                for key,source in (("received_bytes","bytes"),("start_seconds","start"),("end_seconds","end"),("interval_seconds","seconds")):
                    same(point[key],measured[source],"receiver "+key)
                same(point["goodput_mbps"],measured["bytes"]*8/measured["seconds"]/1e6,"receiver rate")
            expected_samples += len(points)
            flow_count += 1
        same(len(data["samples"]),expected_samples,"exported sample count")
        sample_count += expected_samples
        trial_count += 1
    report = {"status":"passed","checked_at":datetime.now(timezone.utc).isoformat(),
              "campaign_id":manifest["id"],"authentication":"none",
              "summaries":len(analysis["summaries"]),"cells":len(analysis["cells"]),
              "trial_details":trial_count,"flows":flow_count,"receiver_intervals":sample_count,
              "selection":"Both trials of the first scheduled block in each experiment/CCA group",
              "export_sha256":hashes}
    (args.results/"public-export-audit.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k != "export_sha256"},indent=2))


if __name__ == "__main__":
    main()
