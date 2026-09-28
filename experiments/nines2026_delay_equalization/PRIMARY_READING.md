# Primary paper reading and reproduction audit

Source: Illick, Roger, Misra, Rubenstein, *Making Congestion Control Algorithms
Insensitive to Underlying Propagation Delays*, NINeS 2026, article 27,
<https://doi.org/10.4230/OASIcs.NINeS.2026.27>. Read all 26 PDF pages on
2026-09-27. Figure inspection is tracked separately below. The PDF has 60
references; the publisher landing page omits the last three. Use the PDF as the
bibliography of record.

## Definitions and interpretation

- Propagation delay **d is base RTT**, including minimum forwarding/processing
  time (p. 2), not one-way delay. Measured TCP RTT includes queueing and jitter.
- The intervention adds a constant to ACK processing, not a constant to every
  observed RTT. Queueing/jitter signals remain visible. Target is lambda=100 ms
  for section 6. Algorithm 1 repeatedly estimates base RTT using a window minimum
  and subtracts the previously installed artificial delay (p. 13).
- For fixed workload, competing CCAs, bottleneck capacity, queue size/discipline,
  and allowed delay assignments D, delta is log2(max rate / min rate) over D.
  Base 2 is explicit in the worked example (p. 10). Delta is not Jain fairness.
  Sampling only part of D gives a lower bound on population sensitivity; this
  statement assumes true rates and does not remove sampling error in estimates.
- Homogeneous BBR's preference for longer RTTs is expected. Heterogeneous CCA
  direction need not follow homogeneous rules (p. 8). Equalizing delays within
  each trial does not remove dependence on the chosen equalization point.
- A zero observed rate gives unbounded log sensitivity. Never silently drop or
  replace zeros, or clip negative queue-delay estimates into evidence of validity.

## Claim inventory

| Evidence | Configuration and reported outcome | Replication considerations |
|---|---|---|
| Table 1, sections 3/7.3 | YouTube 4K locked quality versus OneDrive same 5 GB file; 50 Mbps; buffer 2 BDP at 15 ms; five delay pairs; 4 repeats of 150 s | Real applications, versions, content, endpoint paths, and receiver environment are uncontrolled today. Bulk TCP substitutes cannot validate video stall claims. |
| Figure 3 | Timestamped application packet every 1 ms; roughly 1 ms forward / 3 ms RTT; 60 ms inserted at four possible locations | ACK delay versus forward delay; uncongested paths. No claim that delayed feedback never affects application latency under congestion. |
| Figure 4 / section 6 | Two bulk BBRv1 or BBRv3 flows; 200 Mbps; d1,d2 in [6,54] ms; observe after 60 s; lambda 100 ms | Paper says 1,600 experiments; exact grid, kernel commit, buffer size, run duration and repetition scheme not specified in text. Reported deltas 4.58/0.27 (v1 baseline/proxy), 4.9/0.38 (v3). |
| Figure 5 | Four flows from GCP locations into NYC x86/Raspberry Pi receivers; two Virginia, two farther; 7–75 ms RTT; 1 Gbps with UDP bursts | BBRv3 long flows share 79.2% baseline versus 51% proxy; Jain 0.95 with proxy; worst-flow share 9.4% baseline versus 19.3% proxy. AWS substitution is a conceptual extension, not an identical network replication. |
| Figure 6 | ns-3: two Copa flows (20/80 ms), BBR flow (5 ms), bottleneck 50 Mbps, BBR capped 20 Mbps; staggered starts | Heterogeneity raises Copa sensitivity >=1.3; equal Copa delay restriction delta 0.19. Linux BBR-only tests cannot validate this simulation. |
| Figure 7a | Four flows BBRv3/Hybla/Cubic/Reno, 100 Mbps; [10,30] shifted to [60,80] ms | Constant 50 ms reduces proportional asymmetry, not absolute asymmetry. All four delta values decrease in published chart. |
| Figure 7b | Illinois/Westwood/Cubic/Reno, 100 Mbps; [5,30] versus [35,45] ms | Models imperfect equalization around 40 ms (+/-5). All four reported delta values below 1 after narrowing. |
| Figure 8 | All pairs of YouTube/OneDrive/Google Drive/Dropbox; 50 Mbps; 7–37 ms in approx. 5 ms increments; full versus equal-delay domain | Different application workloads and provider-controlled stacks; cannot substitute algorithm labels for verified present-day provider behavior. |

Table 1 data (published, not new measurements):

| 1D RTT ms | YT RTT ms | 1D Mbps | YT Mbps | YT stall s |
|---:|---:|---:|---:|---:|
|7|7|33.0|15.8|35.4|
|12|12|30.7|17.9|7.7|
|12|37|30.6|18.3|5.9|
|37|12|35.4|12.4|72.2|
|37|37|25.7|18.2|6.8|

## Artifact audit

Artifact <https://github.com/CyrusIllick/ProxyDelay>, downloaded commit
`0cbb5d9edc1d28a7a6918aa4e0062459ac592c13`. The paper separately identifies
Software Heritage directory `swh:1:dir:748439634ef08b81cd32abcfdac79141814e4f9b`.
Verification on 2026-09-28: `git rev-parse 'HEAD^{tree}'` returns exactly
`748439634ef08b81cd32abcfdac79141814e4f9b`; `git fsck --full` succeeds and the
checkout is clean. Software Heritage documents Git-compatible directory hashes
in its [identifier specification](https://docs.softwareheritage.org/devel/swh-model/persistent-identifiers.html#git-compatibility).
Thus the reviewed source tree matches the directory identified in the paper.
The archive website itself was unavailable to the browser; this check uses the
content identifier, not an assertion that its archive download was retrieved.

- Two-flow shell defaults: 49 delay values per flow (6–54 in 1 ms steps), two
  CCAs, proxy off/on, 120 s. This is 9,604 trials, rather than the text's 1,600.
  It sets 50 BDP at reference RTT 10 ms, which the paper does not specify.
- Four-flow defaults: four families, 3^4 assignments each, 4 repeats, 100 s,
  100 Mbps, 0.2 BDP at 10 ms. This is 1,296 trials. Publication text does not
  specify these buffer/repetition details.
- **Direction discrepancy requiring validation:** in the two-flow engine,
  `srta.l` connects toward the sender and `srta.r` toward the shared router.
  Natural delay is installed on `srta.l` egress (ACK path), but proxy delay is
  installed/updated on `srta.r` egress (forward data path), similarly for srtb.
  This conflicts with the paper's ACK-only method and the function comments.
  The stream launcher puts netserver in `cli` and netperf in `srv`/`srvb`;
  topology, routing, initial qdisc and subsequent adaptive updates were checked.
  These are static source findings, not a claim to have executed the original
  full sweep. Our independent preflight measures both packet directions before
  the separate ACK-only reproduction is allowed to run.
  Reproducible source locations in `emulated/engines/nsperf_two_flows.py` at the
  pinned commit: [veth peers, lines 141–142](https://github.com/CyrusIllick/ProxyDelay/blob/0cbb5d9edc1d28a7a6918aa4e0062459ac592c13/emulated/engines/nsperf_two_flows.py#L141),
  [natural and proxy qdiscs, lines 494–530](https://github.com/CyrusIllick/ProxyDelay/blob/0cbb5d9edc1d28a7a6918aa4e0062459ac592c13/emulated/engines/nsperf_two_flows.py#L494),
  [adaptive update, line 666](https://github.com/CyrusIllick/ProxyDelay/blob/0cbb5d9edc1d28a7a6918aa4e0062459ac592c13/emulated/engines/nsperf_two_flows.py#L666),
  [netserver, line 731](https://github.com/CyrusIllick/ProxyDelay/blob/0cbb5d9edc1d28a7a6918aa4e0062459ac592c13/emulated/engines/nsperf_two_flows.py#L731),
  and [sender launch, line 778](https://github.com/CyrusIllick/ProxyDelay/blob/0cbb5d9edc1d28a7a6918aa4e0062459ac592c13/emulated/engines/nsperf_two_flows.py#L778).
- The controller parses BBR `mrtt` without structured validation and commands
  log errors without consistently aborting. Independent execution must fail
  closed for unsupported CCAs, malformed metrics or failed traffic control.

## Limits and analysis safeguards

No universal result for short flows, transient convergence, Wi-Fi/cellular paths,
or arbitrary deployment scale is established. Lambda selection and widespread
adoption remain open. Artificial delay cannot promise equal allocations for
different CCAs/workloads. Preserve startup traces as well as steady-state data.

Match configurations, balance delay sampling and repetitions, randomize trial
order, and report complete counts/failures. Use whole trial as replication unit;
per-second observations are not independent replications. Publish uncertainty
and distinguish measured outcomes, digitized paper values, and hypotheses.

Additional analysis discrepancies: the two-flow dataset uses sampled TCP delivery
rate estimates (54–114 s by default), whereas receiver byte counts over 60–120 s
are a direct goodput measure aligned with the paper's stated warm-up. Its heatmap
uses a maximum within-trial long/short ratio rather than each identified flow's
maximum/minimum rate over delay assignments. These are not generally equivalent.
Figure 8's Dropbox/Google Drive equal-delay cell is 0.2, whereas the prose on
page 20 says 0.8; retain this disagreement when displaying published claims.

Figure pages inspected visually: 12, 14, 15, 17, 18, 19 (Figures 2–8).
