# Preregistration: three-CDN external regional-demand inference

Protocol ID: `cdn-demand-three-cdn-do-origin-v1`

Frozen: 2026-08-28, after target calibration and before any trial-key request
under this protocol. Collection can begin on 2026-08-28 UTC.

## Pre-data amendment

At 2026-08-28T00:51:19Z, the earliest collection date was changed from
2026-08-29 to 2026-08-28 UTC at the operator's request. The original one-day
buffer was administrative, not a scientific design requirement. At amendment
time, no trial keys, warmups, observations, or confirmatory day directories
existed. No hypothesis, endpoint, relay, treatment, sample size, seed, outcome,
eligibility rule, threshold, success rule, or analysis changed.

For auditability, the pre-amendment configuration SHA-256 was
`ff7ffed5de1ebf6ca305d13a8e0dbcc7554bb1d0f5126d885e34faa59e5f4b0a` and the
pre-amendment lock SHA-256 was
`2626b97311f1dc5bde1d1b35488d6ca051b51bd3ea6e0cdac72e978a364c5c8e`.

## Question and scope

Can an unauthenticated external observer distinguish zero from one-or-more recent
controlled requests for an object in a region of a deployed commercial HTTP CDN,
using public HTTP timing? The three independently operated CDNs are Cloudflare,
Fastly, and Amazon CloudFront. All fetch byte-identical objects from one directly
addressed DigitalOcean Droplet whose local disk is outside all three CDN provider
networks.

This is controlled binary recent-demand inference. It does not claim organic
popularity inference, exact request-volume recovery, requester identification, or
a universal timing threshold. The operator may prepare objects and generate
demand. The observer receives no provider credentials, dashboards, APIs, logs, or
cooperation during observation.

## Frozen design

- Collect five UTC calendar days in chronological order, with at most one batch
  per day and no outcome-dependent stopping or replacement day.
- Each day contains one trial through pinned Mullvad relays in Los Angeles
  (`us-lax-wg-006`), Frankfurt (`de-fra-wg-202`), and Singapore
  (`sg-sin-wg-102`). Record the response-reported CDN POP; the requested exit is
  never treated as the serving POP.
- Independently randomize 20 hot and 20 cold objects for every `(day, exit)` and
  use that exact assignment and object path across all three CDNs.
- Before any CDN request, create brand-new DigitalOcean-origin keys through SSH.
  This operator-only population channel is not an observer feature and is outside
  all CDN request paths. Each key is a copy of the same immutable 262,144-byte
  calibration object with SHA-256
  `a0f4fb786d29de3765726d8b0a2641608b533c1eba324157b95ede78feae0580`,
  `Content-Type: application/octet-stream`, and
  `Cache-Control: public, max-age=3600, immutable`.
- Warm only hot objects with three requests per CDN. Cold objects remain untouched
  before observation. Randomize and record provider warmup and observation order.
- Make exactly one randomized full GET per object and CDN in the observation
  phase. Never retry an observation because the first attempt may mutate state.
- Preserve every attempted row and disclose target deployment changes.

Before consuming a day, fetch only the dedicated calibration path through every
HTTPS target and require HTTP 200, exactly 262,144 bytes, and the frozen SHA-256.
This preflight never requests a trial key.

## Eligibility and failures

Timing eligibility requires phase `measure`, successful curl completion, HTTP
200, a complete 262,144-byte body, numeric timing, and a response-reported POP.
There is no imputation. A within-POP stratum lacking a hot/cold pair contributes
no pair; a day/exit cell with no pairs has undefined AUC and remains missing. Raw
timings are never pooled across POPs or exits. A location failure is recorded and
not rerun that day.

## Attacker models and outcomes

Vendor cache-status headers are diagnostic labels and never timing features.

1. All public headers and timing: report `HIT -> hot` as a secondary diagnostic
   treatment-integrity rule.
2. Portable metadata and timing: no confirmatory fitted metadata model is claimed;
   any later model is exploratory and must preserve provider/day holdouts.
3. Timing only: request TTFB is primary, TCP-connect RTT is the negative control,
   and TTFB minus RTT is secondary.

For every `(day, provider, requested exit)`, calculate assigned-hot versus
assigned-cold TTFB AUC using only pairs served by the same observed POP. Lower
timing is positive for hot. Each provider's estimand is the unweighted mean of
available day/exit AUCs. Compute a two-sided percentile 95% interval with 20,000
bootstrap iterations by resampling whole UTC days, retaining exit cells within a
sampled day, using seed `20260830` plus the provider index.

Success is evaluated independently for each CDN: at least 12 of 15 cells must
have defined AUC and the provider's day-cluster interval lower bound must exceed
0.5. A combined average cannot rescue a failed provider. The paper's plural-CDN
claim requires at least two providers to meet their own rule; results for all
three are reported regardless.

## Frozen operating points

The following thresholds were selected before this protocol from the disclosed
S3-origin pilots and will be applied unchanged to the new DigitalOcean-origin
data. This deliberately tests transfer across origin provider as a secondary
outcome; these thresholds are not tuned on confirmatory observations.

| Provider | Frankfurt | Singapore | Los Angeles |
|---|---:|---:|---:|
| Cloudflare | 429.232 ms | 837.223 ms | 259.939 ms |
| Fastly | 572.408 ms | 1211.483 ms | 474.772 ms |
| CloudFront | 353.934 ms | 630.106 ms | 252.151 ms |

Report sensitivity, specificity, accuracy, and balanced accuracy per provider.
Also report within-POP RTT and residual AUCs, cache-label mechanism AUCs, all
failures, treatment integrity, POP changes, and cell coverage.

## Interpretation and integrity

The result pertains to three controlled deployments, one object size, three VPN
exits, and this origin/configuration. Five days provide limited uncertainty
resolution. A systematic related-work review remains required before retaining
`first` in a title, abstract, or contribution statement.

`three-cdn-confirmatory-config-v1.json` is the machine-readable parameter source.
`three-cdn-protocol-lock-v1.json` hashes every protocol-critical source and target
snapshot. The pre-data date amendment above is incorporated into the refrozen
lock. Any later change after collection begins requires a new protocol ID; retain
and disclose data collected under the earlier lock.
