# Independent propagation-delay experiment

Campaign: `nines2026-balanced-v1` · collection date: 2026-09-28 UTC.

**Status: complete and audited.** All 820 scheduled trials were valid, with no
failed or invalid main-campaign trials. Collection ran from 00:36:41 to 04:08:02
UTC on 2026-09-28. The finalized results are stored in Supabase.

The tested interventions reduced sensitivity in all 12 identified-flow
comparisons, with positive pointwise paired reduction intervals. The stronger
Figure 7b claim that imperfect equalization brings every tested flow below δ=1
was not reproduced: Illinois, CUBIC and Reno remained above one. These findings
are conditional on this apparatus and finite grid, and do not establish the
accuracy of all experiments in the paper.

## Scope

This study evaluates selected mechanisms from Illick, Roger, Misra and
Rubenstein, [Making Congestion Control Algorithms Insensitive to Underlying
Propagation Delays](https://doi.org/10.4230/OASIcs.NINeS.2026.27), NINeS 2026.
It is an independent partial replication, with a frozen design and an explicit
record of differences from the publication and accompanying source artifact.

The tested questions are whether calibrated ACK-delay equalization reduces
homogeneous BBR delay sensitivity, whether two heterogeneous delay interventions
reduce sensitivity, and whether ACK-side delay preserves uncongested forward
packet latency. This campaign does not evaluate the adaptive estimator in
Algorithm 1, the GCP deployment, Copa simulations, or live video/cloud-storage
application performance.

## Experimental design

The [frozen protocol](PROTOCOL.md) and [machine-readable schedule](campaign-v1.json)
were fixed before campaign collection. Eight non-burstable `c7i.large` EC2
workers ran isolated Linux namespace/veth topologies, one trial at a time per
worker. Each baseline/intervention pair ran on the same worker with the same
sender launch order. Seed `20260927` randomized blocks and treatment order;
flow launch order rotated across blocks. Flows started close together, but were
not assumed to start simultaneously.

| Experiment | Identified flows | Delay assignments, baseline → intervention | Fixed conditions | Trials |
|---|---|---|---|---:|
| Homogeneous BBRv1 | Two BBRv1 flows | All 25 ordered pairs from {6,18,30,42,54} ms → calibrated 100 ms effective base RTT | 200 Mbps; 8,256 packets; 120 s | 250 |
| Homogeneous BBRv3 | Two BBRv3 flows | Same 25 ordered pairs → calibrated 100 ms effective base RTT | 200 Mbps; 8,256 packets; 120 s | 250 |
| Common shift | BBRv3, Hybla, CUBIC, Reno | All 16 endpoint assignments from {10,30} → {60,80} ms | 100 Mbps; 16 packets; 100 s | 160 |
| Imperfect equalization | Illinois, Westwood, CUBIC, Reno | All 16 endpoint assignments from {5,30} → {35,45} ms | 100 Mbps; 16 packets; 100 s | 160 |

Every configuration has five matched repetitions. The first 60 seconds are
excluded from the primary aggregate; complete receiver intervals and diagnostic
traces, including startup, are retained. Goodput uses receiver payload bytes
divided by actual covered time. Configured wire-rate capacity is not assumed to
equal TCP payload goodput.

The configured natural delay is a **round-trip increment**, inserted once on the
ACK path. The homogeneous intervention adds `max(0, 100 − measured unloaded
minimum RTT)` ms on a separate ACK-return queue. It is fixed after calibration,
not continuously adapted. MTU is 1500; TSO/GSO/GRO are disabled; sender pacing is
`fq`; the shared forward bottleneck uses FIFO `netem`. Queue and rate settings
are recorded during every trial.

The kernel is Linux `6.13.7-jumpserve-nines2026+`, built from Google BBR commit
`90210de4b779d40496dee0b89081780eeddf2a60`. In this pinned tree, `bbr` is BBRv3
and the separate `bbr1` implementation is BBRv1. These meanings are local to this
verified kernel; they are not inferred for historical JumpServe runs.

## Statistical interpretation

For each identified flow and condition, compute its mean goodput at each tested
delay assignment, then

`δ = log₂(maximum assignment mean / minimum assignment mean)`.

This is not a within-trial long/short ratio or Jain's fairness index. A value of
one means a factor-of-two range. The reported reduction is baseline δ minus
intervention δ; positive values indicate less sensitivity.

Intervals are 95% percentile intervals from 2,000 seeded bootstrap resamples of
whole matched trial pairs within each delay-assignment stratum. Each draw retains
both conditions and every flow in a trial together. Per-second observations are
not independent replicates. A directional reduction is labeled supported when
its interval lies above zero, contrary when it lies below zero, and otherwise
inconclusive. These are pointwise intervals without a multiple-comparison
adjustment, conditional on this grid and apparatus.

Five repetitions provide limited information about tails. Max/min selection can
bias a noisy sensitivity estimate and produces non-smooth bootstrap behavior;
an interval need not contain the plug-in estimate. The finite grid omits other
possible delay assignments, and no interval bounds the entire continuous delay
domain. Repeated trials on eight workers are not eight independently replicated
deployments. Fixed 60-second warm-up does not establish asymptotic convergence.

The homogeneous intervention changes both the absolute effective RTT and its
variation across assignments. With a fixed packet queue, its size in BDP units
also changes. The comparison evaluates that combined intervention; it does not
separately identify every causal contribution or establish that adding delay
improves every workload. The heterogeneous common-shift experiment is a different
CCA/queue setting, not an additional homogeneous BBR control. Uncongested UDP
forward latency is likewise not TCP flow completion time or application QoE.

## Measurements and final assessment

All intervals below are pointwise 95% paired-bootstrap intervals, without a
multiple-comparison adjustment. Positive reduction favors the intervention.
There are 125 matched pairs per homogeneous CCA group and 80 per heterogeneous
group, with five repetitions at every assignment.

### Homogeneous BBRv1

| Flow | Baseline δ [95% CI] | Intervention δ [95% CI] | Reduction [95% CI] |
|---|---:|---:|---:|
| 1 · BBRv1 | 3.821 [3.738, 3.944] | 0.080 [0.083, 0.161] | 3.741 [3.606, 3.836] |
| 2 · BBRv1 | 3.773 [3.740, 3.824] | 0.082 [0.085, 0.160] | 3.690 [3.596, 3.719] |

### Homogeneous BBRv3

| Flow | Baseline δ [95% CI] | Intervention δ [95% CI] | Reduction [95% CI] |
|---|---:|---:|---:|
| 1 · BBRv3 | 3.157 [3.077, 3.273] | 0.125 [0.122, 0.221] | 3.032 [2.897, 3.113] |
| 2 · BBRv3 | 3.230 [3.098, 3.557] | 0.122 [0.120, 0.219] | 3.108 [2.917, 3.390] |

### Common +50 ms shift

| Flow | Baseline δ [95% CI] | Intervention δ [95% CI] | Reduction [95% CI] |
|---|---:|---:|---:|
| 1 · BBRv3 | 0.883 [0.790, 0.972] | 0.265 [0.201, 0.391] | 0.619 [0.457, 0.737] |
| 2 · Hybla | 2.869 [2.749, 3.009] | 0.408 [0.290, 0.573] | 2.461 [2.242, 2.644] |
| 3 · CUBIC | 2.883 [2.739, 3.010] | 1.148 [0.989, 1.305] | 1.735 [1.519, 1.949] |
| 4 · Reno | 2.636 [2.500, 2.793] | 0.773 [0.694, 0.939] | 1.863 [1.633, 2.021] |

### Imperfect equalization

| Flow | Baseline δ [95% CI] | Intervention δ [95% CI] | Reduction [95% CI] |
|---|---:|---:|---:|
| 1 · Illinois | 2.955 [2.759, 4.081] | 1.155 [1.086, 1.447] | 1.800 [1.462, 2.855] |
| 2 · Westwood | 3.849 [3.716, 4.044] | 0.886 [0.822, 0.957] | 2.963 [2.822, 3.136] |
| 3 · CUBIC | 4.373 [4.247, 4.517] | 1.532 [1.345, 1.824] | 2.841 [2.646, 2.960] |
| 4 · Reno | 4.034 [3.863, 4.185] | 1.430 [1.337, 1.519] | 2.604 [2.409, 2.791] |

### Interpretation against the published claims

- **Homogeneous BBR:** the central directional result is supported in this setup.
  Both identified BBRv1 flows move from δ≈3.77–3.82 to ≈0.080–0.082; BBRv3
  moves from ≈3.16–3.23 to ≈0.122–0.125. Equalization makes throughput sharing
  substantially more consistent across the sampled assignments.
- **Common +50 ms shift:** all four identified flows have positive reduction
  intervals. This supports the directional claim on the tested endpoint grid;
  it does not imply equal shares across different CCAs.
- **Imperfect equalization:** every flow improves, but the below-one claim is
  not reproduced for Illinois (1.155 [1.086, 1.447]), CUBIC (1.532
  [1.345, 1.824]) or Reno (1.430 [1.337, 1.519]). Only Westwood's entire
  interval is below one (0.886 [0.822, 0.957]). Reduction and a threshold
  crossing are distinct findings. This disagreement does not establish that
  the original measurements were wrong; the apparatus and sampled domain differ.

For comparison only, Figure 4 reports BBRv1 δ 4.58 → 0.27 and BBRv3
4.90 → 0.38. Those published values are not observations in this dataset and
are not used as exact numerical pass/fail targets. Our estimates retain the
identity of both competing flows instead of collapsing them into one ratio.

### Packet-direction control

All eight workers received all 1,000 timestamp probes in each condition:
24,000 measurements in total. The following ranges summarize the eight worker
medians; they are not confidence intervals or ranges over every individual packet.

| Condition | Median forward latency across workers (ms) | Median RTT across workers (ms) |
|---|---:|---:|
| Baseline | 0.013346–0.016949 | 3.033924–3.101562 |
| ACK +60 ms | 0.013534–0.016185 | 63.035124–63.040268 |
| Forward +60 ms | 60.015550–60.019187 | 63.032953–63.038700 |

The ACK intervention raises RTT by approximately 60 ms while forward latency
remains near its baseline. The forward-delay positive control raises both. This
supports the intended packet-direction mechanism on this uncongested virtual
path; it is not evidence that feedback delay leaves application latency unchanged.

### Integrity and persistence

The final audit reconciled **820 trials, 164 configurations, 456 configuration-flow
cells, 2,280 measured flows, 249,602 receiver intervals, 24 UDP-control records,
and 24,000 UDP measurements**. All 456 cells have five matched repetitions.
The audit hashes 6,280 raw artifacts and checks original receiver bytes and
intervals, applied bottleneck qdiscs/rates, recorded flow identity, calibration,
CPU thresholds, the frozen schedule and all normalized database counts.

The maximum per-core trial-average busy time was 5.955% and steal time 0.021%,
below the frozen 85% and 2% limits. The largest calibration error was 0.030 ms,
below the 2 ms limit. Actual aggregate measurement windows ranged from
39.999020 to 60.000993 seconds, covering the planned 40- and 60-second windows.
All 4,560 sender/receiver JSON artifacts identify iperf 3.16.

A separate unauthenticated end-to-end audit compared the website's 12 sensitivity
summaries and 456 cell estimates with the analysis, verified all 820 trial IDs,
and matched eight preselected trial exports (24 flows, 2,576 receiver intervals)
to raw receiver files and calibration. It passed. The selected trials are both
conditions of the first scheduled block in each experiment/CCA group; this is
an integrity check, not an additional experimental sample.


Two short setup pilots are retained under the separate private S3 prefix
`campaigns/nines2026-pilot-v1/`. One completed; one was invalid because its only
active core was 100% busy during overlapping setup work. Neither enters this
campaign or its estimates. The kernel configuration was corrected to expose
both worker vCPUs, setup work was separated from measurement, and each of the
eight campaign workers passed a fresh preflight before collection. Thus a zero
invalid count for the main campaign must not be read as a claim that every
development pilot succeeded.

## Provenance and source-artifact findings

The downloaded source artifact is commit
`0cbb5d9edc1d28a7a6918aa4e0062459ac592c13` of
[CyrusIllick/ProxyDelay](https://github.com/CyrusIllick/ProxyDelay).
Its Git tree is `748439634ef08b81cd32abcfdac79141814e4f9b`, exactly matching
the paper's Software Heritage directory identifier. The clean checkout passed
`git fsck --full`; Software Heritage documents
[Git-compatible directory identifiers](https://docs.softwareheritage.org/devel/swh-model/persistent-identifiers.html#git-compatibility).
The archive website was unavailable, so the verification used the content hash.

Static inspection found that the artifact's two-flow stream direction and veth
topology place its proxy-delay qdisc on forward egress, despite ACK-path comments.
The natural-delay qdisc is on ACK egress. Both the initial proxy qdisc and its
adaptive updates were checked. This campaign follows the paper's stated ACK-only
mechanism and validates packet directions empirically before measurement. This
is a source-code finding, not an assertion that the authors' full sweep was run
or that the publication's mechanism is false.

Other differences are documented in [the primary reading audit](PRIMARY_READING.md):
artifact defaults have 9,604 two-flow trials rather than the paper's stated
1,600; the artifact aggregates sampled delivery-rate estimates over a different
time window; its heatmap ratio is not generally equivalent to identified-flow
δ. The paper does not specify the exact kernel or the two-flow queue size.
Our coarser grid, explicit kernel, fixed intervention and receiver-based metric
therefore do not permit an exact numerical replication claim. Original raw data
for the published figures were not present in the reviewed artifact.

## Literature coverage

All 26 pages of the primary paper were reviewed, including visual inspection of
its principal figures. Its PDF bibliography contains 60 entries; the publisher
landing page lists only 57. The PDF is the bibliography of record.

- 49 cited PDFs were downloaded and reviewed, including two BBR presentations.
  Reference 58's available manuscript has clipped right-hand table columns;
  those unavailable cells were not treated as read.
- Two additional complete author-manuscript web texts were reviewed; their
  original PDF/figure access limitations are recorded.
- Six cited software, manual and press-release resources were reviewed and
  saved. The Sandvine citation is a press release, not access to its underlying
  full report.
- Three papers remain unavailable and unread: Jaffe, *Bottleneck Flow Control*
  (1981); Nandagiri et al., *BBRv1 vs BBRv2* (2020); and Njogu et al., *BBR-EFRA*
  (2023). Publisher, repository and author-copy searches did not produce
  accessible full texts. The user has no alternative copies.

Individual source URLs, versions, access status, hashes and substantive reading
notes are in [literature.json](literature.json) and
[LITERATURE_READING.md](LITERATURE_READING.md), and in the module's public
literature page. Unavailable material was not used as verified evidence.

## Database, website and reproducibility

Normalized configurations, trials, flows, receiver intervals, UDP measurements,
cell estimates, sensitivity summaries, claims and literature records are stored
in the existing JumpServe Supabase project. All 12 study tables have RLS enabled,
public SELECT access and no browser write access. Workers receive no database
credentials; a trusted local controller imports the private S3 evidence.

The new module is
`/module/propagation-delay-study/test-results`, with methods and literature
sections. It provides common-scale throughput-share heatmaps, paired bootstrap
intervals, matched configuration counts, individual trial time series, paired
trial links and JSON exports. Published paper values are explicitly separated
from measured results. Controls use the existing Base UI/shadcn and Tailwind
components, with public access and light/dark themes.

Final figures are exported as PDF, SVG and PNG, with captions and SHA-256 hashes.
Raw evidence remains in the private research S3 bucket and the ignored local
`results/campaign-v1/` directory. The protocol, runner, preflight and schedule
hashes are checked during finalization; raw interval bytes, applied queue/rate,
calibration, CPU, flow identity, coverage and normalized database counts are
audited before marking the campaign complete.

Frontend verification passed 97 tests, lint and the production build. Browser
checks covered public access, theme/mobile layouts, selection, pagination,
matched-trial navigation, JSON downloads and invalid-trial handling. Scientific
analysis tests and evidence-audit tampering checks passed. This verification
covered the local application; it does not certify a production deployment.


## Exported figures and resource cleanup

The final [sensitivity figure](results/campaign-v1/figures/delay-sensitivity.pdf),
[throughput-share heatmaps](results/campaign-v1/figures/bbr-throughput-share.pdf)
and [packet-direction controls](results/campaign-v1/figures/packet-direction-control.pdf)
are available as PDF, SVG and PNG in `results/campaign-v1/figures/`.
`figure-manifest.json` records captions and file hashes. The raw-evidence audit,
public-export audit and final analysis are in the same result directory.

All eight campaign workers were verified terminated after their uploads. The
kernel builder had already terminated. The private S3 evidence, research AMI
`ami-0b550a96183614a18` and its snapshot are retained for reproducibility.
`jumpserve-infra/research/nines2026_delay_equalization/termination-evidence.json`
records the worker state observations; these are verification times, not invented
per-instance shutdown timestamps.
