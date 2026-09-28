#!/usr/bin/env python3
"""Create an immutable, balanced trial schedule before observing results."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random
import uuid

SEED = 20260927


def schedule(pilot=False):
    rng = random.Random(SEED)
    blocks = []
    repetitions = 1 if pilot else 5
    levels = [6, 54] if pilot else [6, 18, 30, 42, 54]
    for cca in ["bbr1", "bbr"]:
        for delays in itertools.product(levels, repeat=2):
            for rep in range(repetitions):
                conditions = []
                for treatment in ("baseline", "ack_equalized"):
                    config = {"family": "homogeneous_bbr", "treatment": treatment,
                              "ccas": [cca, cca], "delays_ms": list(delays),
                              "capacity_mbps": 200, "queue_packets": 8256,
                              "duration_seconds": 12 if pilot else 120,
                              "warmup_seconds": 4 if pilot else 60}
                    if treatment == "ack_equalized":
                        config["target_rtt_ms"] = 100
                    conditions.append(config)
                blocks.append((rep, conditions))
    if not pilot:
        for family, ccas, low, high in (
            ("common_shift", ["bbr", "hybla", "cubic", "reno"], [10, 30], [60, 80]),
            ("imperfect_equalization", ["illinois", "westwood", "cubic", "reno"], [5, 30], [35, 45]),
        ):
            for assignment in itertools.product([0, 1], repeat=4):
                for rep in range(repetitions):
                    blocks.append((rep, [{"family": family, "treatment": treatment, "ccas": ccas,
                                           "delays_ms": [domain[x] for x in assignment],
                                           "assignment": list(assignment), "capacity_mbps": 100,
                                           "queue_packets": 16, "duration_seconds": 100, "warmup_seconds": 60}
                                          for treatment, domain in (("baseline", low), ("shifted", high))]))
    rng.shuffle(blocks)
    trials = []
    for block_index, (rep, conditions) in enumerate(blocks):
        rng.shuffle(conditions)
        n = len(conditions[0]["ccas"])
        order = list(range(1, n+1))
        rotation = (block_index+rep) % n
        order = order[rotation:]+order[:rotation]
        for config in conditions:
            identity = json.dumps({"pilot": pilot, "config": config, "replicate": rep,
                                   "seed": SEED}, sort_keys=True, separators=(",", ":"))
            trials.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "jumpserve:nines2026:v1:"+identity)),
                           "block_index": block_index, "replicate": rep+1, "start_order": order,
                           "config": config})
    return {"id": "nines2026-pilot-v1" if pilot else "nines2026-balanced-v1",
            "seed": SEED, "purpose": "preflight" if pilot else "measurement",
            "kernel_commit": "90210de4b779d40496dee0b89081780eeddf2a60", "trials": trials}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to replace a frozen schedule; use a new version")
    content = json.dumps(schedule(args.pilot), indent=2)+"\n"
    args.output.write_text(content)
    print(json.dumps({"path": str(args.output), "sha256": hashlib.sha256(content.encode()).hexdigest(),
                      "trials": len(json.loads(content)["trials"])}))
