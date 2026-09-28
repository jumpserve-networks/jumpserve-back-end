# NINeS 2026: independent delay-equalization experiment

Protocol v1, prepared before campaign measurements. Pilots validate the measuring
apparatus and are kept separate. The primary paper has been read in full; the
reference review is still in progress and its coverage is explicitly catalogued.

## Questions and coverage

1. For two homogeneous BBRv1 or BBRv3 bulk TCP flows, does a common effective
   base RTT reduce sensitivity to their original propagation-delay assignment?
2. For heterogeneous CCAs, does a common shift or a narrower delay range reduce
   per-flow sensitivity without assuming equal bandwidth shares?
3. Does adding delay on the return path leave uncongested forward packet latency
   unchanged, while increasing round-trip delay?

These target the mechanisms in Figures 3, 4 and 7. The GCP deployment, Copa ns-3
simulation, and live YouTube/cloud-storage QoE experiments are distinct claims and
are **not assessed** by bulk TCP emulation. No experiment proves universal validity.

## Apparatus

Use isolated, nonburstable EC2 x86 workers with Linux namespaces and veth links,
MTU 1500, TSO/GSO/GRO disabled, sender fq pacing, and a single shared FIFO netem
rate limiter. Google BBR tree is pinned at
`90210de4b779d40496dee0b89081780eeddf2a60` (Linux 6.13.7). `bbr` is BBRv3;
`bbr1` is Google's bundled BBRv1 comparison implementation. Archive the source,
configuration and module hashes; do not infer the version from a module name alone.

Each flow has a sender namespace, a per-path router and a receiver namespace;
two shared routers delimit the forward bottleneck. The reverse shared path is
limited to 1 Gbps. Configured propagation delay is a **round-trip increment**,
inserted once on the ACK-return direction. There is no doubled delay calculation.

For homogeneous trials, measure each path's unloaded minimum ICMP RTT (five
probes), then insert a fixed `max(0, 100 - measured minimum)` ms on a separate
ACK-return queue. Check effective unloaded RTT again. This is a calibrated ideal
equalization intervention. It tests the paper's central mechanism but **does not
implement or validate the adaptive window estimator in Algorithm 1**. ICMP and TCP
RTT traces are retained separately; their equality is not presumed under load.

The provided artifact puts proxy delay on the forward interface despite ACK-path
comments. This experiment follows the paper's stated ACK-only mechanism. Packet
direction validation is mandatory. Kernel/version, traffic generator, grid and
measurement differences prevent calling this an exact numerical replication.

## Balanced schedule

Homogeneous experiment: every ordered pair from {6,18,30,42,54} ms, BBRv1 and
BBRv3, baseline and equalized, five independent repetitions: **500 trials**.
200 Mbps bottleneck; 8,256-packet queue (artifact's 50 BDP at 10 ms setting);
120 s transfers, primary receiver measurements after 60 s. This deep-queue choice
is explicitly recorded; the paper's text leaves the queue unspecified.

Heterogeneous experiment: every endpoint assignment for four flows (2^4), five
repetitions of each matched condition pair: **320 trials**. 100 Mbps, 16-packet
queue (artifact's 0.2 BDP at 10 ms), 100 s transfers, measure after 60 s.

- BBRv3/Hybla/Cubic/Reno: {10,30} versus {60,80} ms.
- Illinois/Westwood/Cubic/Reno: {5,30} versus {35,45} ms.

The finite grids are coarser than the artifact defaults. The heterogeneous grids
omit midpoint assignments. Sensitivity over the sampled domain is not an estimate
of every possible delay assignment without further assumptions.

Random seed 20260927 shuffles blocks and treatment order. Matched conditions use
the same worker and sender launch order. Start order is rotated among identified
flows and is stored. Flow starts are close, not asserted to be simultaneous;
retain iperf timestamps and warm-up traces. One trial runs at a time per worker.

## Measurements and validity

Record iperf3 receiver bytes and actual interval durations, sender CCA confirmation,
RTT/socket traces, retransmissions, queue counters, per-core CPU/steal time,
calibration, timestamps and all exit statuses. Primary goodput is receiver payload
bytes divided by covered time after warm-up. Do not average delivery-rate estimates.
Do not use sender offered load as delivered goodput. Whole intervals only; report
actual covered duration rather than interpolating byte arrivals.

Preflight must verify the kernel/CCAs, direction, delay calibration, offload state,
and single-flow rate capacity on the worker type. Reject trials on command failure,
missing/incomplete receiver intervals, wrong CCA, RTT calibration error >2 ms,
per-core average busy fraction >85%, or steal >2%. Halt a worker after validation
failure. Retain failures and expose counts. Never silently rerun until favorable.

## Analysis

The independent unit is the whole trial. For each fixed identified flow and
condition, first estimate mean steady goodput separately at each delay assignment.
Compute `delta = log2(max_assignment(mean goodput) / min_assignment(mean goodput))`.
Do not substitute a within-trial long/short ratio or Jain's index for delta.
Zero minimum with positive maximum is unbounded; all-zero data is undefined.

Compute 95% percentile bootstrap intervals with 2,000 seeded resamples of complete
matched trial pairs **within each delay-assignment stratum**, then recompute means
and delta. Resample all flows in a trial together. Also calculate the paired
baseline-minus-treatment delta interval. These intervals quantify trial variation
conditional on this fixed grid; five replicates give limited tail precision and
extremum selection adds uncertainty. Do not claim a global-domain bound from noisy
sample extrema. No per-second pseudoreplication.

Show per-cell repetition counts and confidence intervals, throughput-share
heatmaps with common scales, individual trial traces, paired contrasts, and the
paper's reported values in a separately labeled series. Numerical disagreement
is a result to investigate, not an exclusion criterion. A directional claim is
supported only when the paired improvement interval is positive; otherwise report
inconclusive or contrary evidence, scoped to this apparatus and sampled domain.

## Persistence and resource limits

Store configurations, whole trials, flows, per-second measurements, summaries,
claims and literature/provenance in Supabase relational tables. Raw diagnostic
files also live in a private research S3 bucket; workers have no Supabase key.
Use authenticated controller writes and public read-only RLS for published data.
At most 16 worker vCPUs concurrently; set a six-hour worker termination deadline,
stop on invalid setup, and terminate instances after evidence upload. Do not deploy
the website or push to main as part of this campaign.
