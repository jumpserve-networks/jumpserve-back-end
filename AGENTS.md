# Repository Guidance

## Tooling

- Always use Context7 when work requires code generation, setup or configuration
  steps, or library, API, CLI, or cloud-service documentation. Resolve the relevant
  Context7 library identifier before querying its documentation unless an exact
  identifier is already available.
- Prefer primary and official sources for technical claims.

## Research Scope

This repository contains an empirical study of object-level regional-demand
inference against deployed commercial HTTP CDNs. YouTube-specific experiments are
out of scope unless the user explicitly changes the research direction.

### Working paper claim

> To our knowledge, this is the first controlled, multi-provider empirical
> evaluation of whether an unauthenticated external observer can infer an HTTP
> object's recent region-specific request history on deployed commercial CDNs
> from public response metadata and timing.

Treat this as a qualified working claim, not an established conclusion:

- A structured novelty search completed on 2026-08-27 did not locate the exact
  combination claimed, but a literature search cannot prove nonexistence. Retain
  `to our knowledge`, repeat the search before submission, and expect peer review
  to challenge the boundary.
- The broad cache-side-channel idea is not new. Do not claim the first use of
  cache state or timing to infer access, the first geographic cache-based usage
  inference, the first black-box HTTP cache detection, or the first global
  per-object commercial-CDN cache measurement.
- `CDNs` requires experiments on at least two independent commercial CDN
  providers. Cloudflare alone supports only a singular claim.
- The primary evidence for `external observer` must come from VPNs, independent
  hosts, or comparable systems outside the CDN under test. Cloudflare-placed
  Workers are same-provider calibration infrastructure, not primary external
  evidence.
- Define `regional demand` precisely. The initial target is binary recent demand:
  zero versus one-or-more prior requests in a controlled region and time window.
  Do not imply that the experiments recover organic popularity or exact request
  volume.

A more precise operational formulation is:

> We empirically evaluate whether an unauthenticated external observer can infer
> an object's recent region-specific request history on deployed commercial HTTP
> CDNs using only public response metadata and timing.

## Threat Model

- The experiment operator may control the origin, CDN configuration, object set,
  demand generation, and ground-truth assignment.
- The observer receives no CDN dashboard access, provider API access, credentials,
  logs, or cooperation. Observer inputs must be available through public HTTP
  requests.
- Keep three attacker models separate:
  1. all public headers and timing;
  2. portable metadata and timing, excluding explicit vendor cache-status headers;
  3. timing only.
- Explicit cache-status headers may be used as diagnostic cache-state labels, but
  they must not leak into the portable or timing-only feature sets.
- Demand assignment is the primary experimental ground truth. Cache residency is a
  mechanism and intermediate label, not synonymous with demand magnitude.
- Report timing discrimination against assigned hot/cold demand separately from
  timing discrimination against diagnostic `HIT`/`MISS` cache labels. Do not use
  the cleaner cache-mechanism AUC as the primary demand-inference result when
  treatment integrity is imperfect.

## Required Experimental Design

- Deploy byte-identical objects with identical content types, cache policies,
  origin behavior, and object sizes on every CDN under comparison.
- Randomly assign hot and cold state independently for each `(object, region)`.
- Warm only the hot objects. Cold objects must remain untouched before measurement.
- Use a fresh run identifier and brand-new object keys for every trial and region.
- Make one randomized measurement request per object during the main observation
  phase because observation can mutate cache state.
- Record the actual serving colo from public response data such as `CF-Ray`; never
  assume that a requested VPN city or placement hint equals the serving colo.
- Analyze within each vantage and observed colo. Do not pool geographic timings in
  a way that lets baseline network distance masquerade as cache or demand effects.
- Repeat trials with fresh keys at different times. A minimum useful target is
  three independent runs per external location.
- Hold out entire objects, regions, days, and CDN providers where applicable.
  Select thresholds on training data and report performance unchanged on held-out
  data.
- Report AUC, a preselected operating-point accuracy or error rate, confidence
  intervals, treatment integrity, failures, and cross-region/cross-CDN transfer.
- RTT is a control or normalizer, not a cache-presence indicator. Prior experiments
  found RTT approximately at chance while request-to-first-byte remained useful.

## Current Evidence

- A controlled Cloudflare cache target and 14 explicitly placed regional probe
  Workers are deployed and remain active.
- Across the 14 placed probes, the randomized measurement produced 280 exact hot
  `HIT`s and 280 exact cold `MISS`es with no failures.
- Response-header timing AUC ranged from 0.871 to 1.000 across colos, with a
  macro-average of approximately 0.972.
- The placed probes reached IAD, SEA, LHR, ICN, SYD, GRU, YUL, FRA, ARN, BOM, SIN,
  NRT, and CPT. The `aws:me-central-1` placement unexpectedly reached BOM, showing
  that placement hints are not geographic guarantees.
- A randomized external local run through ORD produced request-to-first-byte AUC
  0.882, while its TCP RTT estimate was at chance (AUC 0.502).
- A first Mullvad external-observer pilot completed 120/120 one-shot measurements
  through requested Los Angeles, Frankfurt, and Singapore exits. Treatment
  integrity was exact: 60/60 hot objects were `HIT`s and 60/60 untouched cold
  objects were `MISS`es.
- The VPN pilot observed FRA, SIN, LAX, and SJC. One fixed Los Angeles Mullvad
  relay alternated between LAX and SJC, reinforcing that analysis must use the
  `CF-Ray` colo rather than the requested VPN city.
- Within-colo request-to-first-byte AUC ranged from 0.688 to 0.899 in the VPN
  pilot (unweighted macro-average 0.805). TCP-connect RTT AUC was inconsistent,
  ranging from 0.188 to 0.615 (macro-average 0.408).
- Three measured fresh-key trials are now available per requested Mullvad exit.
  The run-aware primary outcome predicts assigned hot/cold demand using only
  within-colo comparisons. Mean request-to-first-byte AUC was 0.895 for Los
  Angeles, 0.711 for Singapore, and 0.598 for Frankfurt; the corresponding
  run-level ranges were 0.887–0.904, 0.670–0.757, and 0.495–0.688.
- Across those trials, Frankfurt and Singapore had exact treatment integrity.
  Los Angeles had 58/60 hot `HIT`s and 60/60 cold `MISS`es because one relay
  reached LAX, SJC, DFW, MCI, and PHX during the experiment. Overall treatment
  integrity was 178/180 hot `HIT`s and 180/180 cold `MISS`es.
- Run-aware TCP-connect RTT AUC means remained near chance/inconsistent: 0.470
  for Los Angeles, 0.532 for Singapore, and 0.454 for Frankfurt. The repeated
  evidence therefore supports a heterogeneous TTFB signal, not a universal one.
- Cloudflare-to-Cloudflare results are unusually clean and establish calibration;
  they do not by themselves establish the external-observer claim.
- An isolated Fastly VCL service is active and a first external Mullvad pilot
  completed 60/60 one-shot HTTP measurements through requested Los Angeles,
  Frankfurt, and Singapore exits. It observed Fastly POPs BUR, FRA, and SIN.
- Fastly treatment integrity was exact: 30/30 assigned hot objects were Fastly
  `HIT`s and 30/30 untouched cold objects were Fastly `MISS`es. Within-POP
  request-to-first-byte AUC was 1.000 at BUR, 0.950 at FRA, and 1.000 at SIN.
  TCP-connect RTT AUC was inconsistent at 0.240, 0.630, and 0.360 respectively.
- The Fastly run is a feasibility pilot, not confirmatory independent-CDN
  evidence: it used only one run with 10 objects per class, an HTTP pre-DNS test
  route, and the controlled Cloudflare Worker as its origin. Repeat against a
  provider-neutral origin with valid TLS before making a two-CDN claim.
- The provider-neutral successor is now deployed over valid TLS. Cloudflare and
  Fastly fetch byte-identical 262,144-byte immutable objects from the same Amazon
  S3 HTTPS origin, while measurements come from external Mullvad exits.
- A paired batch completed three fresh-key trials per requested Los Angeles,
  Frankfurt, and Singapore exit: 720 attempted one-shot measurements, with 717
  successful full-body responses. Fastly succeeded 360/360; Cloudflare had three
  partial-body timeouts after receiving HTTP 200 and diagnostic headers.
- Fastly treatment integrity was exact at 180/180 hot `HIT`s and 180/180 cold
  `MISS`es. Cloudflare produced 164/180 hot `HIT`s and 180/180 cold `MISS`es;
  VPN/anycast changes among observed colos caused the hot-state losses.
- Fastly within-POP TTFB AUC was 1.000 in all nine location-runs. Cloudflare's
  across-run mean within-colo TTFB AUC was 0.955 for requested Frankfurt, 1.000
  for Singapore, and 0.912 for Los Angeles. RTT macro-averages were 0.469 for
  Fastly and 0.463 for Cloudflare, remaining near chance/inconsistent.
- Post-collection thresholds learned on trial 1 and applied unchanged to trials
  2-3 achieved 240/240 accuracy on Fastly and 225/237 on Cloudflare. Exploratory
  cross-provider transfer was asymmetric, so these data do not support a
  universal absolute timing threshold.
- Confirmatory protocol `cdn-demand-confirmatory-v1` was frozen on 2026-08-27.
  It schedules one paired fresh-key trial from three pinned Mullvad relays on each
  of five distinct later UTC dates. Six provider/exit thresholds are locked from
  training trial 1, and ten protocol-critical files are protected by recorded
  SHA-256 digests. Day 1 cannot start before 2026-08-28 UTC.
- An exploratory CloudFront distribution is active at
  `https://cloudfront.jumpserve.dev`. It uses the same Amazon S3 origin as the
  provider-neutral Cloudflare/Fastly targets, creating a same-provider origin
  confound; do not merge it into confirmatory-v1 or treat it as provider-neutral
  CloudFront evidence.
- The first CloudFront external-VPN batch completed 360/360 eligible one-shot
  observations across three fresh-key trials from requested Los Angeles,
  Frankfurt, and Singapore relays. It observed LAX54-P9, FRA56-P16, and SIN2-P6,
  with exact treatment integrity: 180/180 hot `HIT`s and 180/180 cold `MISS`es.
- CloudFront within-POP request-to-first-byte AUC was 1.000 in all nine
  location-runs. Trial-1 timing thresholds applied unchanged to trials 2-3
  classified 240/240 objects correctly. TCP-connect RTT AUC means were 0.588 for
  Los Angeles, 0.518 for Frankfurt, and 0.548 for Singapore and remained
  inconsistent at run level.
- One third warmup request in trial-3 Singapore failed with a connection reset
  after all 20 hot objects had successful `HIT`s at SIN2-P6. No main observation
  had started. The recorded recovery required a same-POP preflight, created no
  new keys, and repeated neither warmup nor measurement.
- A directly addressed DigitalOcean origin is active at
  `https://origin.jumpserve.dev` on a $6/month `s-1vcpu-1gb` Droplet in `nyc3`.
  Caddy 2.11.4 serves local-disk objects over valid TLS without a DigitalOcean
  CDN, load balancer, object store, or proxy in the runtime request path.
- The DigitalOcean origin's calibration object is exactly 262,144 bytes with the
  frozen SHA-256
  `a0f4fb786d29de3765726d8b0a2641608b533c1eba324157b95ede78feae0580`.
  Public DNS, TLS, status, headers, size, and body hash were validated on
  2026-08-28 UTC.
- Three isolated CDN successor targets now use the DigitalOcean origin while the
  frozen S3-backed resources remain unchanged: Cloudflare at
  `cf-cache-do-origin-probe.jumpserve-cache-study-20260826.workers.dev`, Fastly at
  `fastly-do.jumpserve.dev`, and CloudFront at `cloudfront-do.jumpserve.dev`.
- Cloudflare and CloudFront calibration produced exact `MISS` then `HIT`
  transitions at STL and MSP50-P4 respectively. Fastly produced an initial
  `MISS` header probe and exact verified `HIT` bodies from MSP. Every calibration
  download was 262,144 bytes with the frozen SHA-256.
- Three-CDN protocol `cdn-demand-three-cdn-do-origin-v1` was frozen before any
  trial-key request. It schedules one matched fresh-key trial through three
  pinned Mullvad relays on five later UTC dates, applies S3-pilot thresholds
  unchanged as an origin-transfer outcome, and evaluates success separately for
  Cloudflare, Fastly, and CloudFront. A documented pre-data amendment moved the
  earliest Day 1 date from 2026-08-29 to 2026-08-28 UTC before any trial key or
  observation existed; no scientific parameter changed.
- Three-CDN confirmatory Day 1 completed on 2026-08-28 with runner exit code 0.
  All 360 planned one-shot observations were curl-successful, HTTP 200, complete
  at 262,144 bytes, and eligible. Treatment integrity was exact overall: 180/180
  assigned-hot `HIT`s and 180/180 assigned-cold `MISS`es.
- Day 1 within-POP TTFB AUC was 1.000 in all three Cloudflare cells, 0.995-1.000
  in the Fastly cells, and 0.990-1.000 in the CloudFront cells. Los Angeles
  Cloudflare observations reached DEN, PDX, and SEA but were compared only within
  observed colo. One-day provider means were 1.000, 0.998, and 0.997.
- Frozen S3-origin thresholds applied unchanged to Day 1 produced balanced
  accuracy 0.675 for Cloudflare, 0.983 for Fastly, and 0.708 for CloudFront.
  Strong rank AUC with weaker Cloudflare/CloudFront threshold transfer reinforces
  that no universal absolute TTFB threshold has been established.
- Three-CDN confirmatory Day 2 completed on 2026-08-29 with runner exit code 0.
  All 360 planned observations were curl-successful, HTTP 200, complete, and
  eligible. Fastly and CloudFront each had exact 60/60 hot `HIT` and 60/60 cold
  `MISS` integrity. Cloudflare had 57/60 hot `HIT`s and 60/60 cold `MISS`es; all
  three hot misses occurred at ATL during the requested Los Angeles batch and
  remain assigned-hot observations.
- Day 2 within-POP TTFB AUC was 0.985-1.000 for Cloudflare, 1.000 in all Fastly
  cells, and 0.998-1.000 for CloudFront. Across Days 1-2, all 720 observations
  are eligible, treatment integrity is 357/360 hot `HIT`s and 360/360 cold
  `MISS`es, and provider mean TTFB AUC is 0.998 for Cloudflare, 0.999 for Fastly,
  and 0.998 for CloudFront. Only 2/5 dates are complete, so the locked success
  rule is not yet evaluable.
- Frozen thresholds across Days 1-2 produced balanced accuracy 0.667 for
  Cloudflare, 0.975 for Fastly, and 0.762 for CloudFront. The rank/threshold
  contrast continues to argue against a universal absolute TTFB cutoff.
- Three-CDN confirmatory Day 3 completed on 2026-08-30 with runner exit code 0.
  All 360 planned observations were curl-successful, HTTP 200, complete, and
  eligible, with exact 180/180 hot `HIT` and 180/180 cold `MISS` integrity.
- Day 3 within-POP TTFB AUC was 0.995-1.000 for Cloudflare, 0.998-1.000 for
  Fastly, and 0.965-1.000 for CloudFront. Across Days 1-3, all 1,080 observations
  are eligible, treatment integrity is 537/540 hot `HIT`s and 540/540 cold
  `MISS`es, and provider mean TTFB AUC is 0.998 for Cloudflare, 0.999 for Fastly,
  and 0.995 for CloudFront. Only 3/5 dates are complete, so the locked success
  rule is not yet evaluable.
- Frozen thresholds across Days 1-3 produced balanced accuracy 0.658 for
  Cloudflare, 0.975 for Fastly, and 0.764 for CloudFront. Absolute-threshold
  transfer remains materially weaker than within-POP rank discrimination for
  Cloudflare and CloudFront.
- A structured related-work search found no exact predecessor combining
  controlled object-by-region recent-demand assignment, unauthenticated external
  observation, and multi-provider deployed commercial HTTP CDN evaluation.
  However, Akcan et al. (2008) already inferred geographic web usage from public
  DNS caches; Acs et al. (2013) inferred recent accesses from NDN cache timing;
  Golinelli and Crispo (2024) detected HTTP caches using black-box timing; and
  Abdullah et al. (2025) measured per-object hit rates from eight external
  vantages across five commercial cache providers. The novelty is only the
  controlled conjunction and held-out demand-prediction evaluation.

## Important Artifacts

- `experiments/cloudflare_cache/probe.py`: external curl-based measurement tool.
- `experiments/cloudflare_cache/analyze.py`: external JSONL analyzer.
- `experiments/cloudflare_cache/regional_probe_worker.js`: placed Worker source.
- `experiments/cloudflare_cache/analyze_regional.py`: regional Worker analyzer.
- `experiments/cloudflare_cache/regional-results-2026-08-27.csv`: combined
  14-placement result summary.
- `experiments/cloudflare_cache/run_vpn_pilot.py`: sequential Mullvad runner.
- `experiments/cloudflare_cache/analyze_vpn_runs.py`: run-aware assigned-demand
  analysis that permits timing comparisons only within observed colos.
- `experiments/cloudflare_cache/results/vpn-pilot-20260827-0035/`: first external
  Mullvad pilot, including raw data and a result report.
- `experiments/cloudflare_cache/results/vpn-three-run-summary-20260827.md`:
  combined three-run external-observer result and limitations.
- `experiments/cloudflare_cache/README.md`: experiment procedures and caveats.
- `experiments/fastly_cache/run_vpn_pilot.py`: sequential Mullvad runner for the
  isolated Fastly service.
- `experiments/fastly_cache/analyze_vpn_pilot.py`: within-POP Fastly assigned-demand
  analyzer with exploratory bootstrap intervals.
- `experiments/fastly_cache/results/vpn-pilot-20260827-170627/`: first Fastly
  external VPN pilot, including raw measurements and a result report.
- `experiments/fastly_cache/README.md`: deployed target, procedures, and caveats.
- `experiments/cdn_comparison/run_vpn_trials.py`: paired fresh-key Cloudflare and
  Fastly runner using the provider-neutral S3 HTTPS origin.
- `experiments/cdn_comparison/analyze_vpn_trials.py`: within-observed-POP paired
  analyzer with diagnostic cache labels and held-out threshold evaluation.
- `experiments/cdn_comparison/results/neutral-vpn-20260827-184204/`: raw paired
  neutral-origin batch and result report.
- `experiments/cdn_comparison/preregistration-20260827.md`: frozen hypotheses,
  collection design, eligibility rules, outcomes, and success criterion.
- `experiments/cdn_comparison/confirmatory-config-v1.json`: machine-readable
  five-day collection parameters, pinned relays, and frozen thresholds.
- `experiments/cdn_comparison/protocol-lock-v1.json`: SHA-256 protocol lock.
- `experiments/cdn_comparison/run_confirmatory_day.py`: lock-verifying one-day
  runner with relay and public-target preflights.
- `experiments/cdn_comparison/analyze_confirmatory.py`: no-retuning confirmatory
  analyzer with day-cluster intervals and fixed-threshold evaluation.
- `experiments/cdn_comparison/related-work-search-20260827.md`: structured
  novelty search, closest-predecessor matrix, limitations, and qualified claim.
- `experiments/cdn_comparison/infrastructure/cloudfront-exploratory.yaml`:
  isolated CloudFront distribution, cache policy, TLS certificate, DNS, and
  public diagnostic timing configuration.
- `experiments/cdn_comparison/deployed-cloudfront-snapshot-v1.json`: deployed
  CloudFront configuration, calibration observation, and tool digests.
- `experiments/cdn_comparison/run_cloudfront_vpn_pilot.py`: exploratory
  fresh-key CloudFront runner using pinned Mullvad relays.
- `experiments/cdn_comparison/analyze_cloudfront_vpn_pilot.py`: within-observed-
  POP CloudFront demand analyzer and held-out threshold evaluation.
- `experiments/cdn_comparison/resume_cloudfront_measurement.py`: audited
  same-POP continuation for an unstarted measurement after a warmup transport
  failure.
- `experiments/cdn_comparison/results/cloudfront-vpn-20260827-232700/`: raw
  CloudFront pilot data, recovery audit, and result report.
- `experiments/cdn_comparison/infrastructure/digitalocean-origin-cloud-init.yaml`:
  credential-free Ubuntu/Caddy bootstrap for the independent origin.
- `experiments/cdn_comparison/deployed-digitalocean-origin-snapshot-v1.json`:
  deployed Droplet, firewall, DNS, software, payload, and validation snapshot.
- `experiments/cdn_comparison/deployed-three-cdn-do-origin-snapshot-v1.json`:
  isolated Cloudflare, Fastly, and CloudFront deployment and calibration snapshot.
- `experiments/cdn_comparison/preregistration-three-cdn-do-origin-v1.md`:
  frozen three-provider hypotheses, collection design, outcomes, and success rule.
- `experiments/cdn_comparison/three-cdn-confirmatory-config-v1.json`: frozen
  three-provider targets, relays, prior-pilot thresholds, and analysis parameters.
- `experiments/cdn_comparison/three-cdn-protocol-lock-v1.json`: SHA-256 lock for
  the three-CDN DigitalOcean-origin protocol.
- `experiments/cdn_comparison/run_three_cdn_vpn_trials.py`: matched fresh-key
  DigitalOcean-origin runner for Cloudflare, Fastly, and CloudFront.
- `experiments/cdn_comparison/analyze_three_cdn_vpn_trials.py`: exploratory
  within-observed-POP three-provider analyzer.
- `experiments/cdn_comparison/run_three_cdn_confirmatory_day.py`: lock-verifying
  one-day three-provider runner with relay and public-target preflights.
- `experiments/cdn_comparison/analyze_three_cdn_confirmatory.py`: no-retuning
  three-provider analyzer with day-cluster intervals and frozen thresholds.
- `experiments/cdn_comparison/results/three-cdn-confirmatory-day-01-20260828-005209/`:
  complete Day 1 raw data, metadata, manifests, randomized phase orders, and
  result report.
- `experiments/cdn_comparison/results/three-cdn-confirmatory-day-02-20260829-001910/`:
  complete Day 2 raw data, metadata, manifests, randomized phase orders, and
  result report.
- `experiments/cdn_comparison/results/three-cdn-confirmatory-day-03-20260830-035320/`:
  complete Day 3 raw data, metadata, manifests, randomized phase orders, and
  result report.

## Next Research Steps

1. Run confirmatory protocol v1 on five distinct UTC dates beginning no earlier
   than 2026-08-28; do not tune thresholds or replace failed days.
2. Treat the VPN measurements as the primary external validation dataset and the
   placed Workers as calibration/comparison data.
3. Preregister a timing decision rule and repeat the provider-neutral paired
   protocol unchanged on independent days and stable external exits.
4. Test whether thresholds or models learned on one region and CDN transfer to
   held-out regions and another CDN without retuning.
5. Repeat and extend the related-work search before submission, inspect new
   forward citations of the closest papers, and retain `to our knowledge` in any
   first-of-kind statement.
6. Continue `cdn-demand-three-cdn-do-origin-v1` with Days 4-5 on two later,
   distinct UTC dates; do not tune thresholds, retry observations, replace failed
   days, or modify its locked files.

Preserve existing Cloudflare experiment resources unless the user explicitly asks
to remove or replace them.
