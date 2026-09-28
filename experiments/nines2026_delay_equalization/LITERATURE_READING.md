# Citation reading log

Reference numbers follow the 60-entry bibliography in the primary PDF. Merely
downloading or extracting a document does not mean it was read. Detailed notes
below distinguish complete text review from remaining figure inspection.

## 4. FaiRTT (Abrol, Mohan, Truong-Huu, arXiv v1, 2024)

All six pages reviewed, including the algorithm and Figures 3–5. Proposes an internal
BBRv2 inflight/BDP adjustment based on minRTT/lastRTT, a threshold, discount 0.99,
and smoothing weight 0.8, rather than external ACK equalization. Uses ns-3,
10 Mbps DropTail dumbbell, 1 kB packets, 120 s, five random-error seeds, 95% CIs.
Sweeps 0.5–100 BDP and RTT pairs with one flow fixed at 5 ms, the other up to
30 ms. Reports mean ratio 1.08, Jain 0.98, utilization 98.78% (BBRv2 97.21%).

Interpretation limits: calling long-RTT flows elephants and short-RTT flows mice
does not establish different transfer lengths in this experiment. Do not import
that conflation into JumpServe. Reported improvements are for an altered CCA in
a simulator, not a direct test of lambda-proxy or BBRv3. The threshold estimates
flow count from unique minRTT values; its assumptions should not be treated as a
general observable count of competing flows. The stated bottleneck delay and
end-to-end RTT definitions need care; use explicit measured RTT in our protocol.

## 13. BBRv3 overview (Google, IETF 119, 2024)

All ten slides' text reviewed. Model includes max bandwidth, min RTT, ACK
aggregation, and max inflight, with loss and shallow-threshold ECN signals.
Describes v3 as v2 plus bug fixes/tuning. Reports 2024 Google/YouTube TCP rollout
and QUIC experiments; these dated deployment statements do not establish current
behavior of arbitrary public endpoints. The Linux module name remains `bbr`
across versions, so name alone is insufficient experimental provenance.

## 14. BBRv3 fixes (Google, IETF 117, 2023)

All thirteen slides reviewed, including plots on slides 5 and 7.
The authors explicitly say v2 results often do not apply to v3. Fix 1 removes
circular bandwidth/inflight limits that prematurely stopped probing after loss.
Fix 2 increases ProbeBW_UP cwnd gain to 2.25 and raises DOWN pacing gain to 0.9
to improve convergence in buffers above 1.5 BDP without congestion signals.
Startup gains reduced to 2.0 cwnd / 2.77 pacing, loss-exit requirement reduced to
six events. Examples use four flows, 50 Mbps, 40 ms, buffers 1 or 100 BDP, and
staggered starts. This supports preserving both queue size and start order as
experimental factors; no pooling across buffer sizes for a sensitivity estimate.
# Additional completed full-text readings

## 55 — Ware et al., Beyond Jain's Fairness Index

Read all eight pages. Throughput symmetry alone loses the direction of harm and
does not account for application demand, latency or FCT. The proposed symmetric
bounded-harm criterion compares an introduced CCA with a matched legacy workload
pair; the authors leave operational deployment thresholds open. Equal Jain indices
can describe opposite impacts on incumbent flows. Our delay sensitivity answers a
different question; neither delta nor equal throughput establishes deployability
or lack of application harm. Preserve flow identities and workload matches.

## 18 — Dukkipati and McKeown, Why Flow-Completion Time Is the Right Metric

Read all four pages. Steady throughput or utilization does not imply short FCT:
startup feedback, changing flow populations and buffer occupancy can dominate.
The RCP/processor-sharing argument addresses finite-flow workloads and cannot be
validated by our duration-limited bulk tests. Its historical flow-size assumptions
are not a present-day traffic census. Keep FCT, packet latency and goodput distinct.

## 20 — Ghobadi et al., Trickle

Read all six pages. Production randomized video experiments in two data centers,
two baseline groups and 15 days of measurements; cwnd clamping differs from either
bulk TCP or fixed ACK delay. At adequate link capacity, smaller bursts reduce
retransmits and RTT; low-bandwidth users seldom activate the clamp. Rebuffering
does not automatically improve when retransmissions fall. This supports explicit
application-level outcome measurements for the primary paper's video claims;
we must not infer stall seconds from bulk goodput alone.

## 26 — Hock, Bless, Zitterbart, Experimental Evaluation of BBR

Read all ten pages. Linux 4.9 with DPDK, 1/10 Gbps, five repetitions but figures
show selected individual trials. Goodput excludes headers/retransmissions; sending
rate does not. Large queues permit cwnd-limited positive feedback favoring long
RTT. In their smaller 0.8-BDP queues the short flow sometimes wins, unlike blanket
claims about all configurations. Multiple BBR flows can generate persistent queues
and loss; preserve queue, version, start times, and load rather than asserting an
unconditional bias. Netem had problems at 10 Gbps in their environment; we must
validate our lower-rate emulator empirically.

## 28 — Jaeger et al., Reproducible Measurements of TCP BBR

Read all sixteen pages. Mininet namespaces, single-packet TBF token bucket, no
reverse bottleneck, fq pacing, disabled measurement-time analysis. Five whole-run
repetitions; raw data and complete configuration are part of reproducibility.
They distinguish sending rate, post-bottleneck throughput, backlog, retransmits,
and internal estimates. CPU contention changes sampling accuracy. Startup order
relative to ProbeRTT changes convergence for tens of seconds; equal averages can
hide oscillating unfairness. They reproduce long-RTT bias and show buffer size,
number of flows and mixed CCAs strongly affect allocation. Our analysis must
retain time series and treat seconds as correlated observations, not replicates.

## 36 — Ma et al., Fairness of Congestion-Based Congestion Control

Read all eleven pages of arXiv v2 (the cited title refers to its earlier version).
Linux 4.9.18, 20-machine testbed, 120-s bulk transfers, default 2-MB queue.
Long-RTT flows probe longer, deposit more excess bytes and can leave short flows
cwnd-limited. BBQ caps the probing phase at 3 ms when queueing is detected; it
does not add a common RTT like the primary paper. The final version acknowledges
remaining cwnd-driven bias and tradeoffs. Its broad 'irrespective' wording should
not override the configuration-dependent findings in reference 26. The paper's
10-ms versus 50-ms 100-Mbps example is historical evidence, not our measurements.

## 6 — Arun et al., Starvation in End-to-End Congestion Control

Read all 16 pages, including proofs and non-peer-reviewed appendices; inspected
Figure 7 visually. The impossibility construction concerns deterministic,
delay-convergent, efficient controllers with bounded oscillation and adversarial
noncongestive jitter. It applies even at equal base RTT: adding constant ACK delay
does not remove the ambiguity between congestion and jitter. Their experiments
induce severe Copa, BBR (Linux 5.13), and PCC Vivace unfairness; these implementation
versions and constructed jitter do not imply every BBRv3 trial starves. Preserve
startup, scheduling jitter, ACK aggregation and offload settings in our provenance.
Do not claim universal fairness from a finite constant-delay sweep.

## 12 — Cardwell et al., BBR

Read all 34 pages of the 2016 ACM Queue author copy, including the state-machine
appendix. This is the longer-layout version of the cited 2017 CACM article; record
that distinction. RTprop is a window minimum, BtlBw a window maximum; neither is
an instantaneous throughput measurement. BBRv1's ProbeBW cycle is 1.25/0.75/six
ones, with cwnd gain 2 and periodic 200 ms ProbeRTT. Kernel implementation and
BBR generation must be pinned. Deployment gains and fairness statements are
historical experiments, not universal assurances; later cited evaluations qualify
them. Receiver flow control can cap throughput even when congestion control works.

## 25 — Hemminger, Network Emulation with NetEm

Read all eight pages. Netem is an egress queuing discipline: path direction must
be established from interfaces, not comments. Delay, rate limiting and queue size
are different controls. ICMP RTT alone may misrepresent application behavior.
The 2005 timer/PRNG limitations describe that historical implementation, not the
current kernel, but motivate empirical checks of actual delay, batching, loss and
rate. An emulator's controlled single path is not evidence about all Internet paths.
# Reference 11 — TCP Hybla (Caini and Firrincieli, 2004)

Read all 20 pages, including the analysis, ns-2 setup, results, limitations, and
references. Visually inspected Figures 6 and 10. Hybla uses the ratio between
measured RTT and a reference RTT to increase its window more quickly: congestion
avoidance scales by the squared ratio, with corresponding slow-start changes.
SACK, timestamps, and packet spacing address recovery and burst losses that the
ideal lossless analysis cannot remove. The 600-second FTP experiments use a
10 Mbps bottleneck, RED, 25–600 ms RTTs, and optional independent wireless losses.
Figure 6 still shows residual RTT dependence at long RTTs despite substantial
improvement. Reference-RTT choice determines friendliness; the paper explicitly
does not promise perfect equality among arbitrary Internet competitors. This
supports testing the actual Linux Hybla alongside other CCAs rather than treating
its name as evidence of delay invariance. Our short-RTT FIFO experiment is a
different regime and implementation from this ns-2 study.

## Reference 38 — TCP Westwood (2001)

Read all 11 pages; inspected page 3 equations and page 6 plots. The original
algorithm estimates ACK-delivered bandwidth using a time-varying low-pass filter
and resets recovery thresholds to bandwidth estimate × minimum RTT. It retains
Reno-style increase. Its informal fairness argument assumes equal RTTs; the
50/200 ms experiments improve but do not erase short-RTT advantage. It handles
delayed/cumulative ACKs explicitly and compares Bernoulli loss, fading, blackouts,
and satellite conditions. Bulk FIFO Linux tests here neither recreate those loss
models nor establish the same implementation. Useful warning: achieved ACK rate
is an estimator, not an independent measurement of receiver payload goodput.

## 23

Read all 11 pages and inspected Figure 5/6 curves. CUBIC v2.2 uses elapsed wall time, a cubic plateau, TCP-friendly region and fast convergence; its RTT-independent window-growth rule does not make throughput independent of RTT. The paper explicitly accepts inverse-RTT sharing and shows residual bias. Experiments use 400 Mbps dumbbells, bidirectional background traffic, typically 1 BDP/2 MB queues, and up to 600 s; several convergence examples take about 200 s. Linux 2.6-era beta=0.2 and implementation differ from our pinned modern kernel. Do not interpret a 60 s warmup as guaranteed convergence or infer RTT-insensitivity from the name CUBIC.

## 33

Reviewed complete 11-page author-uploaded accepted-manuscript text via web retrieval; PDF binary and figure images could not be downloaded. DA-BBR changes BBRv1 internally: RTT/(RTT+RTprop) scales the ProbeBW BDP estimate, with ProbeRTT and loss-recovery changes. Linux 4.14/Mininet experiments use 10/100 Mbps and queues defined relative to 10 or 50 ms depending on the experiment. Reports improved Jain fairness and retransmissions, but not exact equality at every bandwidth/RTT. This is neither ACK-path equalization nor BBRv3 evidence; parameter and version provenance matter.

## 35

Read all 24 pages of the 2008 extended journal version of the cited 2006 paper, including equations/proofs and plotted fairness results. Illinois uses loss to choose direction and averaged queue delay to set alpha/beta; the stochastic model assumes one DropTail bottleneck, no non-congestion loss, shared alpha and fixed effective RTTs. Its fairness conclusions concern congestion-event samples, with all-time equality explicitly left open; equal cwnd still implies inverse-RTT throughput. Simulation uses 1000-byte packets, 100 Mbps/100 packets and 60–120 ms heterogeneous RTTs. The wireless result uses changed eta1=0.2 and one long run; Figure 14 calls plus/minus one standard deviation a 95% interval, which is not generally justified and is not copied in this study. No guarantee is provided for our mixed-CCA setup.

## 7

Read all 15 pages including appendix and inspected throughput/delay and RTT-fairness plots. Copa targets 1/(delta*queue_delay), uses standing-min RTT over srtt/2 and long-min RTT over 10 s, AIAD plus exponential velocity, and a competitive mode. Default-only Copa is RTT-fair in its homogeneous tests, but mode switching can falsely detect buffer fillers under heterogeneous RTTs and reintroduce bias. The Nash result assumes Markovian arrivals and steady state, not arbitrary mixed CCAs. Figure 6 uses 20 flows/15–300 ms/100 Mbps/1 BDP at 300 ms. Tests of application FCT, satellite links and coexistence use different settings; their outcomes cannot be inferred from our bulk throughput. Copa parameter delta is distinct from the primary paper delay-sensitivity delta.

## 8

Read all 22 pages including detailed trace appendix; inspected Figure 9. Vegas combines finer retransmission timing, expected-versus-actual throughput using minimum BaseRTT, and modified slow start. Alpha/beta are buffer targets (1/3 in this version), not delays. Internet tests were randomized hourly 1994 UA–NIH transfers over seven days with 45 s gaps and matched transfer sizes; improvements are historical and cannot be generalized to modern Linux. The paper explicitly says comparing short and long transfer throughput in its nested-flow experiment is meaningless because their overlap differs. Reno coexistence and minimum-RTT conclusions depend on the tested traffic/queues, as later work documents counterexamples. Sender buffer caps and ACK behavior change results.

## 39

Read all 16 pages, appendices and RTT/queue figures. The square-root model MSS*C/(RTT*sqrt(p)) applies to sustained congestion avoidance with enough sender/receiver window and no significant timeout dominance; p counts congestion signals per acknowledged packet, not every retransmission. Delayed ACK behavior changes C. RTT must be sampled by the active TCP connection, not substituted from unloaded propagation delay; queue phase effects give different per-flow losses despite one shared router. Overbuffering can make congestion cycles about 100 s, so fixed-duration bulk samples need convergence caveats. This model is not a BBR formula and is not applied as such in the study.

## 51

Reviewed all 15 pages of the accepted-manuscript text via web, including experiments and references; original PDF binary and figure images remain unavailable. Tests use Linux 5.4.0-rc6 BBRv2 alpha (Nov 2019), Mininet below 100 Mbps and a physical 1 Gbps testbed, 30 ms base RTT, 0.1–16 BDP queues and ten repetitions in paired-flow tests. BBRv2 reduces losses and improves small-buffer coexistence, but deep-buffer RTT bias and startup-order non-convergence remain. Bandwidth doubling takes about 8 s; loss sensitivity changes around the 2% threshold. RTTs and flow labels are inconsistent between one paragraph and Figure 14 caption; retain the stated topology and avoid assuming exact identities from prose alone. Version-specific findings must not be generalized to BBRv3.

## 46

Read all 23 pages of the May 1998 author technical-report version, including appendix, and inspected the timeout/window-limited formula. The Reno model includes triple-duplicate ACK and timeout recovery with exponential backoff plus a receiver-window bound. Its metric counts transmitted packets including retransmissions, unlike receiver goodput. It assumes correlated losses within a round, independent losses across rounds, and RTT independent of window size; dedicated-link modem traces violate the last assumption. Validation covers 37 historical datasets and explicitly omits some fast-recovery and slow-start detail. Loss-event probability, raw packet loss, and retransmission fraction are not interchangeable. This is a Reno steady-state model, not a BBR prediction or proof that adding RTT improves absolute throughput.

## 9

Read all 15 pages including non-peer-reviewed appendices and inspected allocation-error plots. Recursive Congestion Shares targets CCA-independent bandwidth rights aligned with access agreements using topology-dependent hierarchical weights, not equal per-flow rates. Random-topology counterexamples permit multiple equilibria; sampled CAIDA results are empirical, not a universal guarantee. Mininet evaluation compares FIFO, DRR, one-hop and hierarchical scheduling, with 10 CCA assignments per topology. Box plots are p5/p25/p50/p75/p95, not confidence intervals. Partial deployment and approximate scalable implementations retain limitations. Our fixed FIFO ACK-delay experiment does not test RCS, economic fairness, or Internet-wide deployment.

## 10

Read all nine pages and inspected the elasticity experiment. This is a position paper whose rarity-of-contention hypothesis remains unproven. Contention requires a shared path, bottleneck and queue; congestion alone is insufficient. The June 2023 M-Lab sample uses application/receiver filters and a 100 ms RTT-spread cellular heuristic, leaving only 35 eligible flows; reported initial totals differ between pages. Such sampling and passive breakpoints cannot establish absence of CCA interaction. A Nimbus-based proof of concept uses 48 Mbps/100 ms and 45-second traffic episodes, but is not an Internet-wide survey. Our saturated-flow test intentionally creates contention and estimates behavior conditional on that setup, not its population prevalence.

## 15

Read all 14 scanned pages visually, including equations and geometric proofs. The AIMD result assumes one bottleneck, homogeneous linear controls, synchronous shared binary feedback, and continuously valued allocations. Additive increase improves equality while multiplicative decrease preserves it; efficiency is distinct from equality and responsiveness trades against oscillation size. The paper explicitly leaves delayed/asynchronous feedback and integer rounding as practical issues that can break its convergence guarantees. It does not prove RTT fairness for independently clocked TCP flows, heterogeneous CCAs, or BBR. Equalizing unloaded RTT addresses only one deviation from the ideal model; loss timing and queue dynamics remain empirical questions.

## 16

Read the complete 15-page proceedings PDF and inspected RTT-fairness and convergence plots. PCC Allegro uses randomized paired monitor-interval rate probes and a utility cutoff near 5% loss. Its fairness proof assumes homogeneous utility, a single bottleneck and a simplified update model without measurement delay; asynchrony is conjectured. Default utility is explicitly TCP-unfriendly, with a separate exploratory latency-aware alternative. RTT fairness uses 10 ms versus 20–100 ms, 100 Mbps, a queue sized to the short RTT, long flow first by five seconds, and 500 seconds of competition. PlanetLab trials compare sequential transfers separated by 500 seconds, so environmental drift remains possible. These are not the primary study delta metric or evidence that all finite-time heterogeneous CCA tests become fair.

## 17

Read all 15 pages and inspected convergence and application plots. Vivace replaces Allegro utility and rate updates with RTT-gradient/loss penalties and adaptive gradient ascent. The no-regret result compares against the best fixed rate under the realized history, not a counterfactual dynamic optimum. Equilibrium guarantees require the modeled homogeneous/compatible utility framework; practical probing, noise and delay deviate. Linux 4.10 BBR is BBRv1. Delay-sensitive Vivace performs poorly on the LTE trace with default parameters; tuning b trades supported concurrency for responsiveness. The 42 residential-WiFi-to-AWS pairs use five 100-second uplink transfers and a changed jitter threshold; downlink is omitted because virtualization affected pacing. Convergence, goodput, loss, latency and video buffering measure different effects. It does not establish BBRv3 or proxy-equalization behavior.

## 19

Read all six pages and inspected throughput/RTT plots. NewReno RTT bias arises in slow start, congestion avoidance, initial cwnd and slow-start threshold; persistent queuing changes the effective scaling. The proposed Tarheel uses an oracle for queue delay, scales initial windows/thresholds and both growth phases, and assumes nearly simultaneous losses. NS-2 forces losses in all flows near full queues, uses 10 senders/100 Mbps/50–500 ms, and crops comparison plots after the first loss. This does not validate deployable queue estimation, realistic asynchronous arrivals, recovery/timeouts, or all modern CCAs. It supports measuring actual baseline RTT and retaining startup traces, while warning against applying steady-state fairness arguments to finite transfers.

## 21

Read all 11 pages of the 2024 published journal version of the cited 2023 SSRN manuscript and inspected RTT/buffer and coexistence plots. Mininet/TBF/DropTail plus selected hardware comparisons use 1 Gbps, usually 20 ms RTT, 120 seconds and ten repetitions; buffer and concurrency strongly change sharing. For 20/100 ms BBRv3 pairs, Figure 5 reports near-fair sharing below one BDP and a long-RTT advantage above it; FQ-CoDel mitigates this. This directly cautions against an unconditional shallow-buffer long-RTT rule. Prose ambiguities include calling TBF limit queue occupancy, unclear sample totals, and contradictory CCA labels; do not copy those measurements without artifact verification. ss bandwidth estimates differ from receiver goodput. Results are specific to the tested BBRv3 release and differ from the primary paper grid and our pinned kernel.

## 22

Read all 19 pages including non-peer-reviewed appendices and inspected throughput, robustness and multiple-bottleneck plots. Nimbus detects elastic cross traffic through asymmetric 5 Hz rate pulses and a five-second FFT; its cross-rate estimator assumes a busy shared bottleneck with known capacity. It falls back to competitive control below one-twelfth link share. BBR is elastic when cwnd-limited; Vivace can evade the chosen pulse frequency. Capacity error, aggregation, time-varying links and multiple bottlenecks can impair classification. Heterogeneous RTT phase cancellation remains possible. CAIDA-derived workload comparisons use fixed aggregate cross-traffic bytes, so similar aggregate throughput does not establish stable allocations or equal FCT. The 25 EC2-to-five-residential-path trials are not a population survey. This detector is neither our intervention nor a guarantee that equal RTT removes all contention.

## 24

Read all 14 pages and inspected incast/utilization plots. NDP combines packet trimming, priority header queues, per-packet multipath forwarding, receiver-paced pulls and zero-RTT starts in provisioned Clos datacenters. Fairness is enforced at receiver pull queues; it is not a result for ordinary FIFO TCP or wide-area RTT heterogeneity. Implementation uses DPDK with dedicated polling cores and NetFPGA; much of the scale evaluation is simulation, whose host-processing delay is optimistic. TCP/TFO latency comparisons change substantially when CPU sleep states are disabled. Eight-packet queues and an administrator-set initial window work in the tested topology, while asymmetric or heavily oversubscribed networks need additional handling and NDP can shut out TCP without separate queues. These assumptions do not hold in our ACK-delay experiment.

## 27

Read the complete 25-page November 1988 revision, including timing/window appendices, and checked the printed deviation formula visually. Packet conservation motivates ACK clocking, slow start, variance-sensitive retransmission timers and AIMD. The four-flow 230.4 Kbps experiment uses 1 MB transfers staggered three seconds, 50-packet buffering and 16 KB windows. Receiver ACK policy changes burst losses and produces unequal rates despite congestion avoidance; sender control alone does not ensure fair sharing. This revision changes RTO from mean+2 deviations to mean+4. Appendix A reverses the standard mean-absolute-deviation versus standard-deviation relation and incorrectly dismisses normalization, so its displayed statistical inequality is not copied into our analysis. Historical trace-based utilization and retransmission results are not BBR or modern-Linux performance predictions.

## 31

Read all 12 pages and visually checked model equations and measurement definitions. FAST TCP targets alpha packets of queuing and uses minimum observed RTT; weighted proportional-fair equilibrium assumes the modeled network and full-row-rank routing. Global geometric convergence is proved only for a single bottleneck with zero feedback delay, not general delayed networks. The prototype halves cwnd on loss despite the broader architecture goal. Dummynet experiments use 800 Mbps, 2000 packets, 50–200 ms paths and Linux 2.4; dynamic tests run for hours, and measured queuing differences leave residual RTT unfairness. Five-second receiver goodput excludes headers. Responsiveness uses convergence of a running average to its eventual sample mean, so its endpoint convergence is automatic and is not proof of stationary throughput. Our much shorter trials must retain a convergence limitation.

## 37

Read all nine pages and inspected topology, fairness and coexistence plots. Libra modifies both increase and decrease with RTT and scales an exponential backlog penalty by CapProbe capacity. RTT independence is approximate under T much less than the one-second reference and frozen alpha/loss assumptions. NS-2 tests run 1000 seconds, use 100 Mbps, 1000-byte packets and two buffer conventions; parking-lot flows start randomly in the first five seconds. Jain fairness over four versus all eight flows changes the result, illustrating why population definition matters. FAST comparisons are explicitly sensitive to an untuned alpha. The introductory one-packet-per-success description is not Reno congestion avoidance, and the fluid-model decrease expression differs from the earlier update rule; these are not copied into implementation. Linux deployment is discussed but evaluated results here are simulations.

## 40

Read all seven pages and inspected performance-envelope and bandwidth-share plots. QUICbench evaluates four pinned QUIC stacks against Linux 5.13 using two-minute bulk transfers, TBF/netem, 12 MiB socket buffers, 10/50 ms and 20/100 Mbps. Its performance envelope is a convex hull after rejecting 5% of points farthest from the centroid; it is not a confidence region. Implementation parameters materially matter: mvfst BBR applies a 1.2 pacing multiplier, Chromium CUBIC emulates two flows, and ACK frequency affects mvfst Reno. Partial fixes improve conformance without eliminating differences. Transitivity is empirical within tested conditions, not a theorem. Figure 5 plots fractional shares, while the accompanying discussion also quotes raw throughput ratios above one; these are different quantities and must not be interchanged. These findings justify recording exact kernel/CCA versions; they do not establish modern QUIC or our TCP proxy outcomes.

## 49

Read all eight pages and inspected fairness and throughput-share plots. A CloudLab/BESS dumbbell compares 100 Mbps with 2–50 flows to 10 Gbps with 1000–5000 flows, with fixed large DropTail buffers and identical RTTs. Flows start randomly within two minutes, exclude five-minute warmup, and run up to three hours or until a metric changes less than 1% over 20 minutes. Mathis predictions require congestion-window-halving events rather than raw packet losses; burst losses separate those quantities at scale. BBRv1 can be unfair among equal-RTT flows and dominate loss-based traffic at high concurrency; the proposed desynchronization explanation is explicitly unverified. These controlled saturated tests do not establish Internet prevalence or BBRv3 behavior. Our two/four-flow, 100/120-second runs cannot establish core-scale fairness or long-run convergence.

## 48

Read all 15 pages including non-peer-reviewed appendices and inspected application, fairness and queue plots. Prudentia compares deployed services, using 8/50 Mbps, normalized 50 ms RTT, roughly 4 BDP FIFO queues, full browsers with hardware decoding and 4K displays. Ten-minute trials retain the middle six minutes, use round-robin scheduling and 10–30 repetitions; plotted error bars are IQR, not 95% confidence intervals. Solo demand caps define max-min fair shares, which need not be half capacity. Application batching, parallel flows and ABR can reverse the behavior expected from a CCA alone; effects need not be transitive or monotone in bandwidth. Headless video can be render-limited, and changing kernel/provider versions changes outcomes. Contention is deliberately induced; measured unfairness is not its Internet-wide prevalence. Bulk TCP in our experiment cannot substantiate its service QoE conclusions.

## 34

Read all 14 pages of the author version and inspected short-flow and AQM plots. Six providers are tested in July–September 2019 against local Linux 5.2 BBRv1/v2/Cubic and each other, with 10 Mbps, RTT padded to 50 ms, 0.5/2 BDP and 30 repetitions. Long flows stagger five seconds and analyze 35 seconds; short flows have tightly matched roughly 2.47 MB responses and use FCT excluding handshake. Normalization is applied at intermediate ingress to avoid TCP Small Queues effects. Out-of-order packets approximate provider retransmissions and miss tail loss; provider algorithms/configurations are inferred, not fully controlled. FQ-CoDel improves sharing and balances FCT with a retransmission cost; CoDel alone can disadvantage loss-based traffic. The observed provider and early BBRv2 behavior is version/date-specific, and constant RTT alone does not remove startup-order, buffer, or implementation effects.

## Reference 30 — Jain, Chiu, and Hawe (1984)

Read all 38 scanned pages using OCR and inspected equations/figures visually. Jain separates choosing an allocation metric from quantifying its equality: J=(sum x)^2/(n sum x^2)=1/(1+CV^2) for nonnegative, nonzero allocations. Weighted entitlements and capped fractions of demand require transforming x first. For fixed n the minimum is 1/n, not zero; the all-zero case is undefined. Equal allocation to k of n users gives k/n but does not make every J a literal fraction of satisfied users. Exchange/addition proofs, single-coordinate maximum at the other users’ sum-of-squares/sum, and positive bounded-allocation lower bound 4K/(K+1)^2 were reviewed; exact extremizer fraction requires compatible integer counts. Window/throughput equivalence uses a homogeneous-hop exponential-service closed queueing model, not general heterogeneous-RTT TCP. Table 1 prints the uniform-distribution moment ratio inverted relative to the stated J definition; use the defining equation. This metric cannot replace the main paper’s identified-flow sensitivity across configurations.

## Reference 47 — Pan et al. (2021), BBR-ACW

Read all 18 pages, Algorithm 1, and key figures. BBR-ACW modifies BBRv1 cwnd_gain using a delivery-rate factor alpha and RTT factor beta=RTT/(RTT+RTprop), rather than ACK delay equalization. NS-3 DropTail, stated 1 kb packets, 300 s trials repeated ten times; typical two-flow 10/50 ms at 100 Mbps, 1/5 BDP, RTT ratios up to ten and 10/50/100 Mbps. Measures received application bytes, Jain allocation equality, retransmissions, and latency. Several published ambiguities limit reuse: Figure 7 caption reverses the buffer grouping described in the text and its flow legends oppose the stated long-RTT preference; the claim of fivefold Jain improvement is incompatible with the plotted roughly 0.59-to-0.92 values; 35-to-16 ms is a total-RTT reduction, despite being called a queuing-delay reduction. Alpha handling when delivery extrema coincide is not specified, and the paragraph describing non-dominant gain conflicts with its equation. These simulation findings motivate version/buffer-controlled experiments but are not BBRv3 or proxy validation.

## Reference 57 — Yang et al. (2023), pacing-gain models

Read all 16 pages and inspected the model equations and evaluation plots. Three BBRv1 pacing-gain variants use current RTT / connection-maximum RTT: inverse/cosine, symmetric rational, and gamma-shaped gains. Gamma variant uses up=1.5-0.5*omega^0.25 and down=1-0.5*omega^4. Evaluates NS-3 100 Mbps 10/50 ms and 0.1–100 BDP, LAN 100 Mbps 100 KB/1 MB with 2–10 flows and at least five 200 s repetitions, plus cloud WAN paths. Reports Jain equality, throughput, delay, and loss; WAN effects smaller than simulated/LAN effects and not explained by a proven shared bottleneck. Current maximum RTT is not necessarily a full-buffer RTT; the model equates these without independent validation. Kernel/variant commits, per-flow uncertainty, and exact BDP reference are insufficiently specified for exact replication. Published Linux-default-CCA and unbiased-minimum-estimator claims should not be adopted as facts. No inference to BBRv3, arbitrary mixtures, or ACK-delay intervention is warranted.

## Reference 50 — Scherrer et al. (2022), BBR fluid models

Read the 20-page arXiv v2 including Appendices B–D and inspected validation plots/proofs; this is the available preprint of the 19-page IMC citation. Builds BBRv1/v2 fluid models with smoothed phase pulses/mode variables, neglecting earlier-link loss/queues, approximating RED as instantaneous queue-proportional loss, and replacing randomized phase timing with deterministic flow-ID offsets. Mininet/OVS/Xeon/iPerf validation at 100 Mbps: 10 flows, RTT 30–40 ms (appendix 10–20), five-second metric traces averaged over three runs. Model misses packet jitter and can exaggerate deep-buffer RTT unfairness. Reduced stability results assume a single queue, equal propagation delays for the stated equilibria, ideal minimum-RTT discovery, and simplified background probing; they are not stability proofs for arbitrary RTT mixtures or BBRv3. Deep-buffer BBRv1 permits unequal allocations, while the simplified shallow-buffer equilibrium is equal but over-sends; BBRv2 startup inflight_hi can still cause deep-buffer bloat. The BBRv1 proof reduces to aggregate rate and queue, so it does not establish attraction to a unique per-flow allocation. Preserve startup, queue size, version, and receiver payload measurements in independent experiments.

## Reference 41 — Mishra et al. (2024), Nebby

Read all 15 pages including explicitly non-peer-reviewed appendices and inspected traces/tables. Nebby estimates bytes in flight, not cwnd, after adding 50/100 ms one-way delay at a local 200 Kbps 2-BDP bottleneck. TCP uses sequence/ACK accounting; QUIC assumes downstream data and constant average bytes acknowledged, validated on selected quiche traces. FFT removes faster-than-RTT components; backoff segments are polynomial-fit/Gaussian-classified, with separate BBR probe-period classifiers. Controlled Linux 5.18 plus BBRv2 accuracy 96.7%; QUIC implementations 92.8%, Copa extension 88%, Vivace 58%. June–October 2023 Alexa-top-20k observations across five AWS regions depend on classifier coverage, selected large objects, geography, and transport; only 52% of domains overlap the 2019 list. Domain counts and externally sourced traffic shares are different denominators. AkamaiCC has no operator ground truth; BBRv3 identification relies partly on correspondence/behavior. Browser assets can use different CCAs; per-flow queues used for classification differ from shared-queue contention experiments. These historical classifications are context, not current provider labels or measured CCA identities in our controlled study.

## Reference 42 — Mishra et al. (2019), Gordon census

Read all 24 pages and inspected profiles, classifier, tables, and unknown-variant traces. Gordon estimates maximum unacknowledged packets across restarted connections, with SACK disabled, small negotiated MTUs, and 15 repeats per window; maxima suppress negative loss noise but assume comparable connections. A 100 ms RTT/50-packet BDP profile alternates 500/334 packets per second every 1,500 packets and induces loss once cwnd exceeds 80. Shape plus growth/backoff features cannot separate Vegas/Veno, Reno/HSTCP, or CTCP/Illinois. July–October 2019 Alexa top-20k survey uses five AWS views, best successful classification across views, and repeated attempts until coverage no longer improves. Raw proportions (CUBIC 30.7%, BBR plus G1.1 18.59%) differ from the 36%/22% normalized figures; the latter denominator still includes unknown measurable hosts, despite wording about successful classifications. Traffic-volume >40% is an external Sandvine-based inference, not measured byte share. Static-page CCA may differ from video; Akamai can switch CCAs per connection/context and Google G1.1 may be missed at sub-RTT resolution. These are historical selected-domain observations, not current Internet prevalence or a guarantee of BBR dominance.

## Reference 43 — Mishra, Tiu and Leong (2022)

Read all 13 pages, equations and Figures 3–10. BBRv1/CUBIC fluid model uses equal base RTT, full utilization, nonempty FIFO, cwnd-limited BBR and symmetric same-CCA flows; synchronized versus desynchronized CUBIC gives model bounds, not statistical confidence intervals. Minimum-RTT inflation and CUBIC queue cycles explain why constant-full-queue approximations overestimate BBR share; large-buffer/large-RTT discrepancies remain. Throughput-only Nash equilibria are evaluated by finite unilateral strategy changes, not a universal prediction of adoption. Heterogeneous-RTT experiments favor short-RTT CUBIC and long-RTT BBR; BBRv2 extension is empirical, not a new derivation. Relevant to buffer/version-specific baseline behavior, not evidence that fixed ACK equalization reproduces adaptive proxy results.

The model’s synchronized/desynchronized bounds describe assumptions about aggregate CUBIC loss events. They must not be presented as resampling confidence intervals. The equal-RTT model assumes FIFO saturation and approximately 2 BDP BBR cwnd; its simplifications do not establish fairness for unequal RTTs or BBRv3. The multi-RTT numerical observations are a separate empirical extension.

## Reference 60

Read all 15 pages including appendices and Figure 14–17. Fluid and ns-3 analysis of DCQCN/TIMELY assumes a single drop-free bottleneck and ignores PFC pauses. DCQCN feedback latency and flow count have non-monotonic stability effects; discrete convergence proof additionally synchronizes loss/mark cycles and simplifies recovery. Original TIMELY has no exact fixed point; changing the zero-gradient branch yields infinitely many potentially unfair fixed points. Burst pacing can mask this without a proof. Patched TIMELY uses absolute RTT and smooth weighting, with stability limited by flow count/feedback delay. Egress ECN marking measures the current queue while packet delay reflects arrival-time occupancy; this advantage depends on marking location. The delay/fairness tradeoff theorem is for steady-state R=f(d,p) and explicitly excludes limit cycles. Short-flow FCT and jitter results do not establish Internet-wide or BBRv3 behavior; bulk throughput alone cannot verify them.

## Reference 59

Read all 14 pages and model/application plots. DS2 models statistical static round-trip delays among edge networks, not individual one-way paths or changing queue delay. October 2005 King measurements started with 5,000 DNS servers, retained 3,997 after minimum-sample/direction-consistency/range filters, with 13% missing matrix entries. Their statistical model assumes omitted values have comparable properties; this selection and historical DNS population limit generalization. Five-dimensional embedding plus global/local distortions preserves observed continental/local clustering, growth metrics and triangle-inequality violations. Synthesis learns scaling invariants from sub-samples (five repetitions), validates 2x against the larger measured set, and extrapolates up to 100,000 nodes without independent ground truth at that scale. Incorrect delay-space models change overlay/server-selection conclusions. Our balanced RTT grid is a controlled treatment design, not a sampled current Internet delay distribution.

## Reference 56 — Ware et al., CCAnalyzer

Read all 16 pages including non-peer-reviewed appendices and queue/clustering figures. CCAnalyzer records a deliberately imposed bottleneck queue, then applies thresholded 1-nearest-neighbor dynamic-time-warping classification and four-setting voting. Training uses Linux 5.19 AWS (three traces/CCA); testing Linux 5.15 Azure (five/CCA), with network settings empirically selected for distinguishability. Twenty-second traces, 5/10 Mbps, 85/130 ms RTT and nearest power-of-two roughly-1-BDP queues define the reported accuracy; 100% is finite test-set voting accuracy, not a population guarantee. Unknown-CCA leave-one-out tests still confuse CDG/BIC/Scalable. Fall 2023 CrUX-origin measurements exclude unresponsive, RTT>85 ms and low-utilization traces; 5,000+ measured sites is not 5,000 successful known-CCA labels. CDN clustering re-labels using at least 5% known samples; BBRv3 identification additionally had Google confirmation. Unknown traces can be known algorithms in unusual states. Reported 68 versus 456 MB is about 85% fewer bytes (one paragraph says 15% fewer). These are historical classified deployments, not ground truth for present providers or multi-flow fairness.

## Reference 32 — Kelly, fairness and stability

Read all 36 pages of the corrected author version, including all three appendices and the explicit correction to Section 4.2. Primal/dual fluid algorithms optimize concave utilities under stated resource/feedback assumptions; weighted alpha-fairness is distinct from Jain equality or this study’s delay-sensitivity delta. AIMD equilibrium rate scales inversely with RTT at fixed loss; changing gain rules to remove RTT bias also changes delay and stochastic stability. Sufficient local delay-stability conditions are not global guarantees for actual TCP. Stochastic covariance calculations linearize independent-mark/no-delay models; combining delay and noise is left open. The author correction rejects the original claim that full buffers stabilize the differential-equation model: for overflow p(y)=[y-C]+/y, larger cwnd worsens the sufficient bound; avoiding timeouts is a different possible benefit. Slow start, flow arrivals/departures and most implementation details are excluded. This supports recording gain/version/queue/RTT semantics and avoiding universal fairness claims from a finite static experiment.

## Reference 58 — Zarchy et al., axiomatic congestion control

Read all 33 pages including derivations and appendices of the full POMACS paper (citation is its two-page SIGMETRICS abstract). Eight parameterized axioms separate efficiency, convergence, fairness, friendliness, fast utilization, loss, latency and noncongestion-loss robustness. The deterministic, synchronous equal-RTT, single-FIFO fluid/window model assigns common proportional loss and excludes pacing, random protocols, slow start and timeout behavior; BBR/PCC are explicitly outside the formal model. Worst-case eventual guarantees differ from finite time-average goodput or quantiles: 0.5-second intervals in the Emulab evaluation are temporal observations, not independent repetitions or confidence intervals. Dynamic-capacity bounds are worst-case/unknown-capacity tradeoffs with different time-versus-packet weighting, not estimates for fixed-capacity ACK-delay treatment. Axioms and Pareto tradeoffs guide limitations but cannot certify our Linux experiment. The author PDF clips rightmost columns of Tables 1–3; corresponding protocol properties were read in Appendix B and prose, but cropped numeric cells were not recoverable. Table 3 contains values below 1.5x despite the accompanying blanket >1.5x statement.

## Reference 5 — Akyildiz et al., InterPlaNetary Internet

Read all 38 pages, architecture/state diagrams, tables and bibliography. This is a 2003 cross-layer survey, with then-future mission schedules and dated protocol/deployment forecasts, not current operational guidance. Long propagation, changing contacts, errors, bandwidth asymmetry and power limits jointly motivate bundles/custody, regional protocols, scheduled routing, FEC and specialized time synchronization. TP-Planet uses rate adaptation and priority probes requiring router support; RCP-Planet adds block FEC and rate probing; delayed/block ACKs here reduce feedback traffic and are not lambda-proxy RTT equalization. TCP simulations for minute-scale RTTs, loss and 1 MB/s links do not establish that our 6–100 ms fixed Linux topology models space links. Transport reliability, buffer storage BDP, application deadlines, routing contacts and clock error are distinct constraints. ACK delay cannot remove the physical forward propagation bound, establish uninterrupted connectivity or prove acceptable multimedia latency.


## Reference 1 — cited web resource

Reviewed the cited project homepage. Selenium provides browser automation through WebDriver, recording with IDE, and distributed browser execution with Grid. It can automate an application experiment, but does not independently measure network goodput or establish video quality of experience. Current project news and versions postdate the paper; no claim is made to have audited the entire software source tree.


## Reference 2 — cited web resource

Read the complete ss manual. The -i output includes TCP congestion-control identity, smoothed RTT and variation in milliseconds, MSS, congestion window and byte counters; estimated send and pacing rates are distinct from receiver-delivered bytes per actual interval. Namespace selection, numeric output and socket filters matter when tracing multiple flows. The live manual may describe a newer iproute2 than the pinned experiment binary; actual worker versions and raw output are retained. The displayed module name bbr alone cannot distinguish BBR generations.


## Reference 3 — cited web resource

Read the cited January 17, 2023 Sandvine press release in full. It reports usage from over 177 providers and attributes 65% of measured traffic volume to video, with 24% video-volume growth in 2022. These are vendor-reported historical volume statistics, not flow counts, current Internet prevalence, or evidence that ACK equalization improves application outcomes. This citation links to the press release; the underlying full report and raw provider data have not been reviewed.


## Reference 52 — cited web resource

Read the cited repository README and build/run guidance. It implements BBR and Copa within ns-3.33 and describes its sending-rate trace as the maximum-bandwidth-filter output, distinct from delivered payload goodput. Copa latency-factor examples use 0.05 and 0.5. This simulator supports the primary paper Copa experiment, which the current Linux study does not reproduce. No claim is made to have audited every simulator source file or validated its equivalence to Linux BBRv3.


## Reference 53 — cited web resource

Read the repository overview and version links; the experiment separately pins and builds Linux TCP BBRv3 at commit 90210de4b779d40496dee0b89081780eeddf2a60 and retains BBRv1 under a separate module name. The overview distinguishes Linux TCP and QUIC implementations and directs BBRv3 users to its v3 branch. Current branch contents or historical deployment presentations cannot identify the version used by an unrelated endpoint or the paper. No claim is made to have formally verified the entire kernel.


## Reference 54 — cited web resource

Read the complete ip manual. ip manages interfaces, addresses, routes and namespaces; -n selects a namespace, while -j requests structured JSON output. Batch mode stops on an error unless forced; successful execution returns zero, syntax errors one and kernel errors two. These controls establish the topology, but do not by themselves establish packet direction, applied delay or bottleneck capacity. Those properties require the separate qdisc configuration and measured preflight checks. The current manual is not substituted for recorded worker tool versions.
