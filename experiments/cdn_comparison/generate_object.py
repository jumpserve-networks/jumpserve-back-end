#!/usr/bin/env python3
"""Generate a deterministic fixed-size binary object for CDN experiments."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


DEFAULT_SIZE = 256 * 1024


def deterministic_bytes(seed: str, size: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < size:
        output.extend(
            hashlib.sha256(f"jumpserve:{seed}:{counter}".encode("utf-8")).digest()
        )
        counter += 1
    return bytes(output[:size])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    args = parser.parse_args()
    if args.size < 1:
        parser.error("--size must be positive")

    payload = deterministic_bytes(args.seed, args.size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(
        f"path={args.output} bytes={len(payload)} "
        f"sha256={hashlib.sha256(payload).hexdigest()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
