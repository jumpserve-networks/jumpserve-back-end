# JumpServe Test Runners

## Application modules

JumpServe's frontend presents a module chooser after sign-in. The existing
application's runners and stored emulation results belong to **Congestion Control
Emulated Tests**, identified by `congestion-control-emulated`. Existing runner
arguments, queue formats, and `emulated_*` result tables retain their meaning.

**Congestion Control Real World Tests** (`congestion-control-real-world`) uses
[`real_world/`](real_world/README.md): fresh EC2 machines for one server, one shared
bottleneck, and independently placed receivers. Its authenticated API, workflow,
results, and cleanup are separate from emulated tests.

The CDN research experiments below are independent of the application's emulated
test module. They are not yet exposed as a selectable frontend module. Preserve
their protocols and result formats when adding future CDN application tools.

## Unit-test coverage

Prepare local test dependencies with:

```sh
python3 -m venv .venv-unit-tests
.venv-unit-tests/bin/python -m pip install -r requirements-dev.txt
npm ci
npm run test:coverage:report
```

`npm run test:coverage` runs the same fixed local suites and enforces 100%
coverage. A failing
suite or invalid source inventory exits 1; valid reports below the target exit
2. The Python suites run in separate processes to isolate their bare-module
imports. Their HTTP fixtures bind only to loopback, so the runner needs local
socket permission. They do not call live AWS, Supabase or model providers.

Python branch coverage includes every maintained module in `research_workflow/`,
`platform_supervisor/`, `real_world/`, and the root benchmark/queue runners,
including unloaded files. Standalone experiment programs, historical-tool archives,
vendor dependencies and test harnesses are excluded. Native helper tests use the
development-only Node 24.16.0 installed by `npm ci`; synthetic grants exercise
real SQLite, filesystem recovery and child-process entrypoints.

Benchmark transfer tests use synthetic sockets, clocks and subprocesses to check
byte counts, measurement units, cleanup and persistence. EC2 migration and release
verification scripts are exercised with mocked cloud/storage adapters; those unit
results do not establish a production migration or successful live experiment.

Use `test:coverage:python:report` or `test:coverage:native:report` for one language;
omit `:report` to enforce its target. Native reports require exact raw counts for
lines, statements, branches and functions. Python requires no missing lines or
branches, and both compare report files against the source inventory.
Python uses coverage.py's supported `pytrace` core. The local CPython 3.13 C
tracer loses conditional exception exits inside `with`; the tracer regression
checks that each genuinely untested outcome remains missing and that exercising
both outcomes covers both branches. These controls run before instrumentation
to avoid interfering with the suite's tracer. This preserves the executor's
pinned bytes.
`coverage/summary.json` records the repository result; per-language HTML, JSON
and interchange reports live under `coverage/python/` and `coverage/native/`.
These generated artifacts and isolated dependencies stay out of version control.

## Maintained and historical tools

The completed five-study conversion module and original OpenClaw pilot runner
have been retired together with their dedicated tests. `retired-workflows.json`
records the original paths, Git revisions and SHA-256 hashes; recover source with
`git show <source_revision>:<path>` in isolated checkouts at those revisions.
Pilot protocols, reports, artifact manifests, runtime dependencies and research
evidence retain their existing locations. Current OpenClaw integration checks
continue to use the retained runtime dependencies.

The current automated suites remain in their existing test entrypoints.
`maintained-tools.json` lists supported manual Linux/native qualification and
fixture-preparation programs, plus the explicitly retained CDN protocol directories.
Completed one-off experiment/pilot programs live under `archive/historical-tools/`.
Its manifest preserves their original paths, source revisions, sizes and SHA-256
hashes. Raw measurements, protocols, evidence, literature and reports retain their
existing locations and contents. Archive integrity is checked by the queue unit
suite included in `npm run test:coverage`.

For historical reproduction, use the recorded source revisions and original paths
in isolated checkouts, with the original protocol and evidence. Archived programs
retain their original path and sibling-repository assumptions. Add recurring checks
to the automated suites; archive completed one-off campaigns with their provenance.

For a focused Python diagnostic, run
`.venv-unit-tests/bin/python tests/run_unit_coverage.py --suite research --pattern test_builder_native_execution.py`.
Focused reports go under `coverage/python-focused/`, retain the full source
inventory and never replace the full-suite report or satisfy the repository gate.

## Network emulation benchmarks

Measurements belong in Supabase. EC2 launches use `benchmark_ingest.py` with
`JUMPSERVE_INGEST_URL`, `JUMPSERVE_JOB_ID`, and `JUMPSERVE_JOB_TOKEN` supplied by
the launcher. This short-lived capability can update that job and submit its
report; it cannot read or change other tests. Reports are inserted atomically,
and identical retries return the original IDs. The current upload limit is
32 MiB uncompressed / 3 MiB compressed; exceeding it fails the job explicitly.

For trusted local execution only, set `SUPABASE_SERVICE_ROLE_KEY` in the
environment (a modern Supabase secret key is supported). Preserve that variable
explicitly when using sudo. Secrets are never accepted as command-line arguments
and have no built-in default. Orchestrators fail before networking setup if
neither complete job ingestion settings nor the environment credential exists.
Sender and receiver subprocesses do not require persistence credentials.

Run `python3 -B -m unittest test_benchmark_ingest test_kernel_metrics test_sender_metrics`
to check persistence configuration and metric behavior without cloud access.

This repository currently centers on a small set of Python entrypoints for Linux network-emulation experiments plus a YAML queue runner. The older README content referred to `netem_cubic_benchmark.py`, but the files currently present in the repo are:

- `netem_cubic_benchmark_nines.py`
- `netem_nines.py`
- `netem_cubic_benchmark_hotnets.py`
- `run_queue.py`

An independent, real-network experiment for measuring Cloudflare cache residency
from distributed vantage points is documented in
[`experiments/cloudflare_cache/README.md`](experiments/cloudflare_cache/README.md).
The provider-neutral paired Cloudflare/Fastly experiment is documented in
[`experiments/cdn_comparison/README.md`](experiments/cdn_comparison/README.md).
That directory also contains the isolated exploratory CloudFront extension and
its external-VPN pilot; its same-provider Amazon S3 origin confound is documented
separately from the frozen Cloudflare/Fastly protocol.

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
- resolves explicit scenario names or paths to files in `queues/`
- expands `params` maps into command-line flags
- runs jobs sequentially
- supports per-job `cwd`, `env`, `retries`, `continue_on_error`, and `timeout_seconds`
- supports `--list`, `--dry-run`, and `--prefix`

Useful commands:

```bash
python3 run_queue.py --list
python3 run_queue.py queues/nines/netem-nines-delay-10ms-vs-60ms-bbr-bbr.yaml --dry-run
python3 run_queue.py --prefix nines/ --dry-run
```

## Snapshot metric tests

Run the sender regression suite on macOS or Linux without network access:

```bash
python3 -B -m unittest test_sender_metrics -v
```

It checks RTT, congestion-window, and packets-in-flight file output for both
benchmark runners with byte counters disabled, enabled, or partially configured.
The kernel snapshot path collects throughput from network interfaces, so sender
TCP metric files must be written independently of optional sender byte counters.

For real Linux TCP measurements, run the integration suite as root in a
disposable environment with Python 3.10+, iproute2, ethtool, procps, and kernel
support for Cubic, BBR, network namespaces, and netem:

```bash
python3 -B -m unittest test_kernel_metrics -v
```

The suite runs the Nines, HotNets, and two-flow wrapper runners with two 100 MB
transfers, Cubic/BBR, 10/60 ms delays, a 100 Mbit/s bottleneck, a 125 KB queue, and
10 ms kernel snapshots. Set `JUMPSERVE_TEST_CCAS=cubic,cubic` on kernels without
BBR support. Unsupported algorithms fail before any receivers are started.
It requires nonzero RTT, congestion-window, and
packets-in-flight samples for each client and checks that both transfers finish.
It disables Supabase persistence and cleans up its namespaces after each run.

Docker Desktop can provide the Linux environment on macOS:

```bash
docker build -t jumpserve-kernel-metrics-test -f Dockerfile.metrics-test .
docker run --rm --network none --cap-add NET_ADMIN --cap-add SYS_ADMIN \
  -e JUMPSERVE_TEST_CCAS=cubic,cubic \
  --mount "type=bind,source=$PWD,target=/work,readonly" \
  jumpserve-kernel-metrics-test python3 -B -m unittest test_kernel_metrics -v
```

The namespace capabilities are required for this test. Use a disposable container;
the test mounts the checkout read-only and requires no external network access.

## Queue Layout

The current repo has a few different queue families:

- `queues/nines/`: queue files that call `netem_nines.py`
- `queues/hotnets/` and `queues/hotnets_short/`: queue files that currently call `netem_cubic_benchmark_nines.py`
- `archive/queues/retired/`: historical configurations excluded from discovery and execution.

## Current State Notes

The removed `netem_cubic_benchmark.py` runner is not a default or an alias for a
modern experiment. Parameter-based jobs must name their `script` explicitly;
the CLI also requires a scenario or `--prefix` unless using `--list`.

Twenty-seven old configurations and two meta-queues referencing absent
`queues/_nines/` files are archived unchanged. Their original paths, byte counts,
missing dependencies and SHA-256 hashes are recorded in
[`archive/queues/retired/manifest.json`](archive/queues/retired/manifest.json).
These files preserve historical experiment definitions without substituting new
methods or presenting them as runnable scenarios. The runner rejects explicit
attempts to execute this archive. Current scenarios remain under `queues/`.

## Requirements

These scripts are Linux-specific and expect:

- root privileges for namespace and `tc` setup
- `ip`, `tc`, and usually `ss` from `iproute2`
- `python3`

On macOS, the benchmark scripts exit with guidance because `ip netns` and `tc netem` are Linux-only.

## Benchmark deployment

Pushes to `main` build a new benchmark AMI through the reusable workflow in
[jumpserve-infra](https://github.com/jumpserve-networks/jumpserve-infra/blob/main/.github/workflows/benchmark-ami.yml).
Changes confined to Markdown or `experiments/` do not trigger a rebuild. The
`Deploy benchmark AMI` workflow can also be run manually on `main`.

The workflow pins the backend commit, tests successful and failed runs plus
automatic EC2 termination, selects the image only after verification, and checks
the deployed API. A failed deployment check restores the previous image.
Temporary builders and verification instances are cleaned up; AMIs and snapshots
are retained for rollback. Credentials come from GitHub OIDC, so this deployment
does not require a permanent EC2 server or backend AWS access-key secrets.
