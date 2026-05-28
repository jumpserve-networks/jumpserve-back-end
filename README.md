# Network Emulation Benchmark

This repo includes `netem_cubic_benchmark.py`, a Python script that:

- Creates Linux network namespaces for `sender`, `router`, and `N` clients
- Applies `tc netem` delay/loss on each `router -> client` link
- Applies a shared `tc netem` rate bottleneck on `sender -> router`
- Sets TCP congestion control per sender flow (each client can differ)
- Supports per-client flow start delay and per-client file size
- Streams synchronized rate snapshots as JSON lines
- Can sample per-flow transport metrics out-of-band with `ss` every 100 ms

## Requirements (Linux)

- Root privileges (`sudo`)
- `ip` (from `iproute2`)
- `tc`
- `ss` (from `iproute2`) when using `--snapshot-metrics-source ss`
- `sysctl`
- `python3`

## Run

```bash
sudo python3 netem_cubic_benchmark.py \
  --num-clients 3 \
  --client-names client1,client2,client3 \
  --client-delays-ms 10,60,35 \
  --client-ccas cubic,reno,bbr \
  --client-file-sizes-mbytes 50,35,20 \
  --client-start-delays-ms 0,150,300 \
  --loss-pct 0 \
  --snapshot-interval-ms 10 \
  --bottleneck-all-client-rate-mbit 100 \
  --bottleneck-buffer-kbytes 125
```

Use out-of-band `ss` sampling instead of the legacy live kernel-counter/TCP_INFO path:

```bash
sudo python3 netem_cubic_benchmark.py \
  --num-clients 3 \
  --client-names client1,client2,client3 \
  --client-delays-ms 10,60,35 \
  --client-ccas cubic,reno,bbr \
  --client-file-sizes-mbytes 50,35,20 \
  --client-start-delays-ms 0,150,300 \
  --snapshot-metrics-source ss \
  --ss-sample-interval-ms 100 \
  --ss-log-file ./run1-ss.jsonl \
  --bottleneck-all-client-rate-mbit 100 \
  --bottleneck-buffer-kbytes 125
```

`--num-clients`, `--client-names`, `--client-delays-ms`, `--client-ccas`,
`--client-file-sizes-mbytes`, and `--client-start-delays-ms` are order-aligned
and should all describe the same client set.

Ports are auto-selected randomly per run and guaranteed unique within that run.

`--bottleneck-buffer-kbytes` is optional. When set, it caps bottleneck queue depth
for the sender link (mapped to netem packet limit using a 1500-byte MTU estimate).

## Queue Multiple Runs From YAML

Store queue scenarios under `queues/`. `run_queue.py` will use
`queues/default.yaml` by default, or you can select any other scenario file.

```bash
python3 run_queue.py
```

Dry-run without executing commands:

```bash
python3 run_queue.py --dry-run
```

Run a named scenario from `queues/`:

```bash
python3 run_queue.py staggered-start
python3 run_queue.py staggered-start --dry-run
```

Run every scenario whose filename starts with a prefix:

```bash
python3 run_queue.py --prefix delay
python3 run_queue.py --prefix delay --dry-run
```

You can still pass an explicit path:

```bash
python3 run_queue.py queues/staggered-start.yaml
```

List the available scenarios:

```bash
python3 run_queue.py --list
python3 run_queue.py --list --prefix delay
```

`--prefix` matches queue filename stems in `queues/` and runs the matching files in
sorted filename order. It cannot be combined with an explicit `config` argument.

Each queue YAML supports:
- `defaults.retries` (integer >= 0)
- `defaults.continue_on_error` (`true`/`false`)
- `defaults.cwd` (optional working directory)
- `defaults.env` (environment variable map)
- `defaults.timeout_seconds` (optional positive number or `null`)
- `jobs[]` entries with:
  - `name`
  - `params` (recommended): map of benchmark args where `num_clients` maps to `--num-clients`
    - booleans: `true` adds flag only, `false` omits it
    - lists: joined as comma-separated values
  - `run` (optional fallback): raw shell command
  - optional per-job overrides: `sudo`, `python`, `script`, `cwd`, `env`, `retries`, `continue_on_error`, `timeout_seconds`

Each job runs only after the previous one finishes. By default the queue stops on
the first failed job unless `continue_on_error: true` is set for that job/default.
For files inside `queues/`, relative paths are resolved from the project root so
`script: netem_cubic_benchmark.py` keeps working without extra `cwd` settings.

If Supabase credentials are configured, results are persisted to:
- `public.congestion_control_algorithms`
- `public.emulated_parent_runs` (`number_of_clients`, `bottleneck_rate_megabit`, `queue_buffer_size_kilobyte`, `snapshot_length_ms`)
- `public.emulated_runs` (`emulated_parent_run_id`, `client_file_size_megabytes`, `client_start_delay_ms`, `flow_completion_time_ms`; links each client run to one parent run)
- `public.emulated_snapshot_stats` (`snapshot_index`, `elapsed_microseconds`, `megabits_per_second`, `round_trip_time_ms`, `bottleneck_queuing_delay_ms`, `bottleneck_backlog_bytes`, `in_flight_packets`, `congestion_window_bytes`, `emulated_run_id`)
- `public.runs` (`raw_log` stores full benchmark JSON payload)

Snapshot throughput is sampled from kernel counters: per-client rates use each client
interface's `rx_bytes`, and `total_megabits_per_second` uses the bottleneck qdisc's
sent-byte counter.

With `--snapshot-metrics-source ss`, per-flow throughput comes from `ss` `delivery_rate`,
RTT comes from `ss` `rtt`, and congestion window comes from `ss` `cwnd * mss`. The
bottleneck backlog and queueing delay still come from `tc` qdisc stats. In this mode the
benchmark does not also collect overlapping live snapshot throughput/RTT/cwnd metrics from the
legacy kernel-counter or `TCP_INFO` path. `in_flight_packets`
falls back to zero when the local `ss` build does not expose `unacked`. Socket-memory state is
also collected from `ss -m` and exposed under each receiver's `skmem` map using the native `ss`
keys such as `r`, `rb`, `t`, `tb`, `f`, `w`, `o`, `bl`, and `d` when present.

Raw `ss` samples are also written to a JSONL file in this mode. Each line contains
`sample_index`, `elapsed_microseconds`, `sampled_at_utc`, parsed `sampled_metrics`, and
`raw_ss_output`. If
`--ss-log-file` is omitted, the benchmark writes `./ss_sampler_<benchid>.jsonl`.
That file is the raw source for any later `ss` parsing.

You can set credentials via environment variables:
- `SUPABASE_PROJECT_ID` (defaults to `regphejnlvfpyokpniny`)
- `SUPABASE_SERVICE_ROLE_KEY` (optional override; a hardcoded default is present)

## Example Snapshot Output

```json
{"mode":"snapshot","snapshot_index":1,"elapsed_microseconds":10000,"receivers":{"client1":{"megabits_per_second":47.7,"cca":"cubic","delay_ms":10.0},"client2":{"megabits_per_second":64.8,"cca":"reno","delay_ms":60.0},"total_megabits_per_second":112.5}}
{"mode":"snapshot","snapshot_index":2,"elapsed_microseconds":20000,"receivers":{"client1":{"megabits_per_second":48.0,"cca":"cubic","delay_ms":10.0},"client2":{"megabits_per_second":65.2,"cca":"reno","delay_ms":60.0},"total_megabits_per_second":113.2}}
```

## macOS Note

`ip netns` and `tc netem` are Linux-specific. On macOS, run this in a Linux VM or privileged Linux container.
