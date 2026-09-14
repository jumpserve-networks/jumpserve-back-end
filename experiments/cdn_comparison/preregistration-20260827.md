# Preregistration: external object-level regional-demand inference

Protocol ID: `cdn-demand-confirmatory-v1`

Frozen: 2026-08-27, after the exploratory/training batch and before any
confirmatory-day collection.

## Research question and scope

Can an unauthenticated external observer distinguish zero from one-or-more recent
controlled requests for an object in a region of a deployed commercial HTTP CDN,
using only public HTTP timing?

The study evaluates controlled recent regional request history. It does not claim
to recover organic popularity, requester identity, or exact request volume. The
operator controls the origin, CDN configuration, object set, treatment assignment,
and warm-up traffic. The observer receives no CDN credentials, dashboard access,
logs, APIs, or cooperation during observation.

## Frozen training information

The exploratory batch at
`experiments/cdn_comparison/results/neutral-vpn-20260827-184204` is training data
and will not enter confirmatory estimates. It was used to choose one
request-to-first-byte threshold per provider and requested VPN exit:

| Provider | Requested exit | Frozen TTFB threshold (ms) |
|---|---|---:|
| Cloudflare | Frankfurt | 429.23199999999997 |
| Cloudflare | Singapore | 837.2225 |
| Cloudflare | Los Angeles | 259.93899999999996 |
| Fastly | Frankfurt | 572.4075 |
| Fastly | Singapore | 1211.483 |
| Fastly | Los Angeles | 474.772 |

These values maximize balanced accuracy on trial 1 of the training batch, with
lower TTFB predicting hot. They will not be changed after confirmatory collection
begins. Their training provenance and post hoc origin will remain disclosed.

## Confirmatory collection design

- Collect five UTC calendar days, numbered 1 through 5, in chronological order.
- Collect at most one confirmatory batch per day. There is no data-dependent
  stopping and no replacement day after looking at outcomes.
- Each day contains one fresh-key trial through each requested exit: Los Angeles,
  Frankfurt, and Singapore.
- Pin Mullvad relays to `us-lax-wg-006`, `de-fra-wg-202`, and `sg-sin-wg-102`.
  Never silently substitute a relay. Continue recording the response-reported CDN
  colo/POP because a fixed relay does not guarantee CDN routing.
- For each `(day, requested exit)`, independently randomize 20 hot and 20 cold
  objects. Use the same assignment and byte-identical object for both providers.
- Copy one immutable 262,144-byte calibration payload to brand-new Amazon S3 keys.
  Both CDNs use that same provider-neutral HTTPS origin, content type, cache policy,
  and object body.
- Warm only assigned-hot objects, using three requests per provider. Do not touch
  cold keys before measurement.
- Randomize and record provider warm-up and measurement order.
- Make exactly one randomized full GET per object in the observation phase. Never
  retry an observation, including a timeout or partial body, because the first
  request may have changed cache state.
- Preserve Cloudflare and Fastly resources and configurations during collection.
  Record and disclose any unavoidable deployment change.
- Before creating a day's output or trial keys, fetch the existing public
  calibration object through both HTTPS targets and require HTTP 200, 262,144
  bytes, and the frozen SHA-256. This preflight may affect only the dedicated
  calibration key, never a trial key, and therefore does not consume a day when
  it fails.

## Eligibility, failures, and missing data

An observation is eligible for timing analysis only when all of the following are
true:

1. phase is `measure`;
2. curl completed successfully;
3. HTTP status is 200;
4. the complete 262,144-byte body was downloaded;
5. the primary timing value is numeric; and
6. the response identifies the actual Cloudflare colo or Fastly POP.

All attempted observations remain in raw data. Ineligible observations are
reported as failures and receive no imputation. Diagnostic cache status may still
be reported if its response header arrived. A cell whose observed POP strata
contain no hot/cold pair has undefined AUC and remains missing; it is not pooled
across POPs or assigned a chance value.

A relay or location failure is recorded and is not rerun that day. An error before
the runner creates any day output does not consume the day because no experimental
object or observation exists.

## Attacker models and outcomes

Vendor cache-status headers are never timing features.

1. **All public headers and timing:** report the diagnostic rule `HIT -> hot`, all
   other statuses `-> cold`. This quantifies treatment integrity and is secondary.
2. **Portable metadata and timing:** no separately trained portable-metadata model
   is confirmatory in this protocol. Any such model is exploratory and must use a
   future frozen train/test design.
3. **Timing only:** the primary feature is request TTFB. TCP-connect RTT is a
   negative control. TTFB minus the RTT estimate is secondary.

### Primary estimand

For every `(day, provider, requested exit)`, compute assigned-hot versus
assigned-cold TTFB AUC using only hot/cold pairs served by the same observed POP.
Lower timing is positive for hot. The provider-level estimand is the unweighted
macro-average over available day/exit cell AUCs.

Compute a two-sided 95% percentile interval with 20,000 bootstrap iterations,
resampling whole UTC days and retaining the three exit cells within each sampled
day. Use seed `20260828`. Report cell coverage and all missing cells.

The confirmatory success rule is evaluated separately for Cloudflare and Fastly:
at least 12 of 15 day/exit cells must have defined within-POP AUC, and the
provider-level confidence interval lower bound must exceed 0.5. No combined
two-provider average can rescue a provider that fails this rule.

### Secondary outcomes

- Apply each frozen provider/exit TTFB threshold unchanged to confirmatory days.
  Report hot sensitivity, cold specificity, accuracy, and balanced accuracy.
- Report the same within-POP AUC analysis for TTFB minus RTT.
- Report TCP-connect RTT AUC as a negative control.
- Report diagnostic treatment integrity and timing discrimination of observed
  `HIT` versus `MISS` separately from assigned-demand results.
- Report exploratory cross-provider application of the frozen thresholds in both
  directions, labeled exploratory. Do not tune on confirmatory data.

## Reporting and interpretation

Report every day, provider, requested exit, observed colo/POP, attempted count,
eligible count, failure, treatment-integrity count, and outcome. Do not pool raw
geographic timings. Confidence intervals based on five days remain limited and
will be described as such.

The result may support controlled object-level recent-demand inference on the two
tested deployments. It cannot establish a universal threshold or generalize to
other object sizes, origin regions, CDN configurations, or organic traffic.
Retaining `first` in a paper title, abstract, or contribution remains conditional
on a systematic related-work review.

## Protocol integrity

`confirmatory-config-v1.json` is the machine-readable source of parameters and
thresholds. `protocol-lock-v1.json` records SHA-256 digests of the configuration,
preregistration, collection runner, and confirmatory analyzer. The day runner must
verify that lock before actual collection. Any post-freeze change requires a new
protocol ID and must be disclosed; previously collected days stay associated with
the lock under which they were collected.
