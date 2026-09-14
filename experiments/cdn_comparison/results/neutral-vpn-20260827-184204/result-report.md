# Provider-neutral paired CDN VPN experiment

Date: 2026-08-27

This batch compares Cloudflare and Fastly from external Mullvad exits while both
CDNs fetch byte-identical immutable 262,144-byte objects from the same Amazon S3
HTTPS origin. The observation phase used public HTTP requests without CDN API,
dashboard, log, or credential access.

## Design

- Three fresh-key trials each through requested Los Angeles, Frankfurt, and
  Singapore exits.
- Twenty randomly assigned hot and twenty cold objects per trial and location.
- The assignment was identical across providers within a trial/location, while
  remaining independent across fresh trial/location object sets.
- Hot objects received three warm-up requests. Cold objects were untouched.
- Provider warm-up and measurement order was randomized and recorded.
- Main observation made one randomized full GET per object because observation
  mutates cache state.
- Timing AUC was computed only between hot and cold observations served by the
  same actual Cloudflare colo or Fastly POP.
- `CF-Cache-Status` and Fastly `X-Cache` were diagnostic labels only and were
  excluded from timing features.

The batch created 360 fresh S3 keys and attempted 720 one-shot observations: 40
objects x 3 locations x 3 trials x 2 providers.

## Primary assigned-demand results

Lower request-to-first-byte (TTFB) is treated as evidence for assigned-hot state.

| Trial | Provider | Requested exit | Observed measurement POPs | Success/fail | Hot `HIT` | Cold `MISS` | RTT AUC | TTFB AUC |
|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | Cloudflare | Frankfurt | AMS, FRA | 40/0 | 17/20 | 20/20 | 0.606 | 0.984 |
| 1 | Fastly | Frankfurt | FRA | 40/0 | 20/20 | 20/20 | 0.505 | 1.000 |
| 1 | Cloudflare | Singapore | SIN | 40/0 | 20/20 | 20/20 | 0.398 | 1.000 |
| 1 | Fastly | Singapore | SIN | 40/0 | 20/20 | 20/20 | 0.365 | 1.000 |
| 1 | Cloudflare | Los Angeles | ATL, PDX, SJC | 40/0 | 19/20 | 20/20 | 0.520 | 0.987 |
| 1 | Fastly | Los Angeles | BUR | 40/0 | 20/20 | 20/20 | 0.490 | 1.000 |
| 2 | Cloudflare | Frankfurt | AMS, FRA | 40/0 | 18/20 | 20/20 | 0.580 | 0.925 |
| 2 | Fastly | Frankfurt | FRA | 40/0 | 20/20 | 20/20 | 0.388 | 1.000 |
| 2 | Cloudflare | Singapore | SIN | 40/0 | 20/20 | 20/20 | 0.465 | 1.000 |
| 2 | Fastly | Singapore | SIN | 40/0 | 20/20 | 20/20 | 0.557 | 1.000 |
| 2 | Cloudflare | Los Angeles | ATL, PDX | 40/0 | 16/20 | 20/20 | 0.355 | 0.840 |
| 2 | Fastly | Los Angeles | HHR | 40/0 | 20/20 | 20/20 | 0.470 | 1.000 |
| 3 | Cloudflare | Frankfurt | AMS, FRA | 37/3 | 18/20 | 20/20 | 0.503 | 0.955 |
| 3 | Fastly | Frankfurt | FRA | 40/0 | 20/20 | 20/20 | 0.515 | 1.000 |
| 3 | Cloudflare | Singapore | SIN | 40/0 | 20/20 | 20/20 | 0.330 | 1.000 |
| 3 | Fastly | Singapore | SIN | 40/0 | 20/20 | 20/20 | 0.541 | 1.000 |
| 3 | Cloudflare | Los Angeles | ATL, DEN, PDX | 40/0 | 16/20 | 20/20 | 0.411 | 0.911 |
| 3 | Fastly | Los Angeles | BUR | 40/0 | 20/20 | 20/20 | 0.448 | 1.000 |

Across three runs per requested exit, mean within-POP TTFB AUC was:

| Provider | Requested exit | Mean TTFB AUC | Run range | Mean RTT AUC |
|---|---|---:|---:|---:|
| Cloudflare | Frankfurt | 0.955 | 0.925-0.984 | 0.563 |
| Cloudflare | Singapore | 1.000 | 1.000-1.000 | 0.398 |
| Cloudflare | Los Angeles | 0.912 | 0.840-0.987 | 0.428 |
| Fastly | Frankfurt | 1.000 | 1.000-1.000 | 0.469 |
| Fastly | Singapore | 1.000 | 1.000-1.000 | 0.488 |
| Fastly | Los Angeles | 1.000 | 1.000-1.000 | 0.469 |

The nine-run macro-average TTFB AUC was 0.956 for Cloudflare and 1.000 for
Fastly. The corresponding RTT macro-averages were 0.463 and 0.469. This supports
a strong TTFB signal in this controlled deployment and again does not support
TCP-connect RTT as a cache-presence indicator.

## Treatment integrity and diagnostic cache labels

- Cloudflare: 357 successful and 3 failed full-body observations; 164/180 hot
  objects returned `HIT`, and 180/180 cold objects returned `MISS`.
- Fastly: 360/360 successful observations; 180/180 hot objects returned `HIT`,
  and 180/180 cold objects returned `MISS`.
- Diagnostic within-POP TTFB AUC for observed `HIT` versus `MISS` was 1.000 in
  every provider/location/trial cell.

Cloudflare's hot-state failures were associated with VPN/anycast route changes:
one requested Los Angeles exit reached ATL, DEN, PDX, and SJC across the batch,
while requested Frankfurt alternated between AMS and FRA. Fastly was more stable
in this batch, reaching BUR or HHR from Los Angeles, FRA from Frankfurt, and SIN
from Singapore. These observations validate using the response-reported colo/POP
rather than the requested VPN city.

Three Cloudflare Frankfurt cold `MISS` downloads in trial 3 returned HTTP 200 and
headers but timed out after partial bodies. They are retained as failed attempts
and excluded from timing AUC and threshold evaluation. They were not retried,
because a retry would no longer be a first observation.

## Held-out operating point

As a post-collection exploratory check, one absolute TTFB threshold was selected
separately for each provider/requested-exit pair using trial 1, then applied
unchanged to successful observations in trials 2 and 3.

| Provider | Requested exit | Threshold (ms) | Held-out accuracy | Hot correct | Cold correct |
|---|---|---:|---:|---:|---:|
| Cloudflare | Frankfurt | 429.232 | 0.948 | 36/40 | 37/37 |
| Cloudflare | Singapore | 837.222 | 1.000 | 40/40 | 40/40 |
| Cloudflare | Los Angeles | 259.939 | 0.900 | 32/40 | 40/40 |
| Fastly | Frankfurt | 572.408 | 1.000 | 40/40 | 40/40 |
| Fastly | Singapore | 1211.483 | 1.000 | 40/40 | 40/40 |
| Fastly | Los Angeles | 474.772 | 1.000 | 40/40 | 40/40 |

Aggregated held-out accuracy was 225/237 (94.9%) for Cloudflare and 240/240
(100%) for Fastly. This split is not a preregistered confirmatory test: the
threshold analysis was specified after collection, even though trial 1 and later
trials are disjoint fresh-object sets.

Exploratory cross-provider transfer was asymmetric. Cloudflare trial-1 thresholds
classified all 240 held-out Fastly observations correctly. Fastly thresholds
transferred to Cloudflare with 94.8% accuracy in Frankfurt, 100% in Singapore,
and 76.2% in Los Angeles. The last result is evidence against claiming a universal
absolute timing threshold.

## Interpretation and limits

This experiment supplies substantially stronger two-provider evidence for the
operational claim that an unauthenticated external observer can infer controlled,
recent region-specific request history using public timing. It does not establish
organic popularity inference, exact request volume, or a universal signal across
objects, regions, days, providers, and configurations.

The batch was collected on one day, used one object size and one origin region,
and was run against researcher-controlled CDN configurations. Trial-bootstrap
intervals based on three runs are exploratory and should not be interpreted as
strong uncertainty estimates. Before using `first` in a paper contribution,
complete a systematic related-work search. Before treating the operating-point
result as confirmatory, preregister the decision rule and repeat it unchanged on
independent days and exits.

## Reproduction

Raw manifests, warm-up records, one-shot measurement JSONL, phase order, design,
and batch metadata are stored beside this report. Recompute the analysis with:

```bash
python3 experiments/cdn_comparison/analyze_vpn_trials.py \
  experiments/cdn_comparison/results/neutral-vpn-20260827-184204
```
