# Network Emulation Benchmark

This repository currently centers on a small set of Python entrypoints for Linux network-emulation experiments plus a YAML queue runner. The older README content referred to `netem_cubic_benchmark.py`, but the files currently present in the repo are:

- `netem_cubic_benchmark_nines.py`
- `netem_nines.py`
- `netem_cubic_benchmark_hotnets.py`
- `run_queue.py`

## What The Python Files Do

### `netem_cubic_benchmark_nines.py`

This is the main multi-client benchmark runner in the current tree.

It can run in three modes:

- `orchestrator`: builds the full Linux namespace topology and runs the benchmark
- `sender`: opens sender sockets and pushes data to one or more receivers
- `receiver`: accepts a transfer and records byte counters for snapshots

In orchestrator mode it:

- creates Linux network namespaces and `veth` links for one sender and `N` clients
- applies per-client `tc netem` delay and loss on sender-side router links
- applies a shared bottleneck on the communal path for all clients
- sets TCP congestion control per flow
- supports per-client file size and per-client start delay
- emits synchronized JSON snapshot output during the run
- can collect out-of-band transport metrics with `ss`
- can persist results to Supabase when credentials are configured

The parser exposes the options the queue files use today, including:

- `--num-clients`
- `--client-names`
- `--client-delays-ms`
- `--client-ccas`
- `--client-file-sizes-mbytes`
- `--client-start-delays-ms`
- `--loss-pct`
- `--bottleneck-all-client-rate-mbit`
- `--bottleneck-buffer-kbytes`
- `--snapshot-metrics-source kernel|ss`
- `--ss-sample-interval-ms`
- `--ss-log-file`

Example:

```bash
sudo python3 netem_cubic_benchmark_nines.py \
  --num-clients 2 \
  --client-names client1,client2 \
  --client-delays-ms 10,19 \
  --client-ccas cubic,bbr \
  --client-file-sizes-mbytes 50,50 \
  --snapshot-metrics-source ss \
  --ss-sample-interval-ms 100 \
  --bottleneck-all-client-rate-mbit 100 \
  --bottleneck-buffer-kbytes 125
```

## `netem_nines.py`

This is a two-flow wrapper around `netem_cubic_benchmark_nines.py`.

It reuses the base sender, receiver, parsing, `ss`, and Supabase logic from `netem_cubic_benchmark_nines.py`, but changes the network topology to a ProxyDelay-style two-flow layout:

- `sender_a -> sender_router_a -> core_router -> mid -> client_router -> client`
- `sender_b -> sender_router_b -> core_router -> mid -> client_router -> client`

Key differences from the main multi-client runner:

- it requires exactly two clients
- it builds one shared client namespace with two competing flows
- it is intended for the `queues/nines/netem-nines-*.yaml` scenarios

If you want the dedicated two-flow topology, this is the script to run.

## `netem_cubic_benchmark_hotnets.py`

This file is another copy or variant of the multi-client benchmark entrypoint.

Based on the current repo contents:

- it has the same top-level structure and CLI shape as `netem_cubic_benchmark_nines.py`
- it supports the same orchestrator, sender, and receiver modes
- it includes the same `ss` snapshot sampling and Supabase persistence path
- current queue YAMLs in `queues/hotnets/` and `queues/hotnets_short/` appear to invoke `netem_cubic_benchmark_nines.py`, not this file

So this script exists in the tree, but it does not appear to be the queue target for the current HotNets scenario YAMLs.

## `run_queue.py`

This is the queue orchestrator for YAML scenario files under `queues/`.

It:

- loads a YAML file containing `defaults` plus `jobs`
- resolves scenario names like `staggered-start` to files in `queues/`
- expands `params` maps into command-line flags
- runs jobs sequentially
- supports per-job `cwd`, `env`, `retries`, `continue_on_error`, and `timeout_seconds`
- supports `--list`, `--dry-run`, and `--prefix`

Useful commands:

```bash
python3 run_queue.py --list
python3 run_queue.py --dry-run
python3 run_queue.py queues/nines/netem-nines-delay-10ms-vs-11-19ms.yaml --dry-run
python3 run_queue.py --prefix delay --dry-run
```

## Queue Layout

The current repo has a few different queue families:

- `queues/nines/`: queue files that call `netem_nines.py`
- `queues/hotnets/` and `queues/hotnets_short/`: queue files that currently call `netem_cubic_benchmark_nines.py`
- top-level files in `queues/`: older scenario files, many of which still reference the legacy script name `netem_cubic_benchmark.py`
- `queues/run_queue_nines.yaml`: a meta-queue that chains multiple queue files through `run_queue.py`

## Current State Notes

A few references in the repo still use the legacy script name `netem_cubic_benchmark.py`:

- the old README examples did
- `run_queue.py` still defaults `script` to `netem_cubic_benchmark.py` when a job omits `script`
- many older top-level queue YAMLs still set `script: netem_cubic_benchmark.py`

That means the README needed to be updated to describe the files that actually exist today, even though some queue content still reflects the older filename.

## Requirements

These scripts are Linux-specific and expect:

- root privileges for namespace and `tc` setup
- `ip`, `tc`, and usually `ss` from `iproute2`
- `python3`

On macOS, the benchmark scripts exit with guidance because `ip netns` and `tc netem` are Linux-only.
