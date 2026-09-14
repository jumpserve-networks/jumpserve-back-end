# Cross-CDN Confirmatory Experiment

This directory contains the provider-neutral experiment shared by Cloudflare and
Fastly. Both CDN targets fetch the exact same immutable objects from one Amazon S3
regional HTTPS origin.

The [structured related-work search](related-work-search-20260827.md) records the
closest precedents and the narrow, qualified novelty claim supported as of
2026-08-27. The general cache side channel is established prior art; this study's
candidate novelty is the controlled, external, multi-provider evaluation.

## AWS origin

The CloudFormation template creates a retained, encrypted S3 bucket with ACLs
disabled. The bucket policy permits anonymous `GetObject` only for `objects/*`,
denies non-TLS requests, and grants no bucket-listing or write access.

```bash
aws cloudformation deploy \
  --template-file experiments/cdn_comparison/infrastructure/aws-origin.yaml \
  --stack-name jumpserve-cdn-origin \
  --parameter-overrides \
    BucketName=jumpserve-cdn-origin-395567831870-us-east-1 \
  --tags project=jumpserve-cdn-cache-study
```

The stack and bucket use retention safeguards. Do not delete them as routine test
cleanup. Trial cleanup should target only explicitly enumerated object keys after
the research retention period.

## Independent three-CDN successor origin

An independent origin is active at `https://origin.jumpserve.dev` on a directly
addressed DigitalOcean Droplet in `nyc3`. Caddy serves objects from local disk
over valid TLS; no DigitalOcean CDN, load balancer, object store, or proxy is in
the request path. The host uses the $6/month `s-1vcpu-1gb` plan and remains a
billable active resource.

The public calibration object is byte-identical to the frozen S3 object:

- Path: `/objects/calibration/2f4d7a9c7e034f8aa7d096a292beb386.bin`
- Size: 262,144 bytes
- SHA-256: `a0f4fb786d29de3765726d8b0a2641608b533c1eba324157b95ede78feae0580`
- Headers: `Content-Type: application/octet-stream` and
  `Cache-Control: public, max-age=3600, immutable`

The source is deployed by
[`infrastructure/digitalocean-origin-cloud-init.yaml`](infrastructure/digitalocean-origin-cloud-init.yaml),
and [`deployed-digitalocean-origin-snapshot-v1.json`](deployed-digitalocean-origin-snapshot-v1.json)
records the Droplet, firewall, DNS, software, payload, and validation state. The
bootstrap copied the calibration payload once and verified it before serving;
all runtime origin responses come from the Droplet's local disk.

Three isolated successor targets now use this origin, while the older S3-backed
targets and frozen confirmatory-v1 remain unchanged:

- Cloudflare: `https://cf-cache-do-origin-probe.jumpserve-cache-study-20260826.workers.dev`
- Fastly: `https://fastly-do.jumpserve.dev`
- CloudFront: `https://cloudfront-do.jumpserve.dev`

The deployed settings and calibration observations are frozen in
[`deployed-three-cdn-do-origin-snapshot-v1.json`](deployed-three-cdn-do-origin-snapshot-v1.json).
Cloudflare and CloudFront produced exact `MISS` then `HIT` calibration
transitions; Fastly produced an initial `MISS` header probe and exact verified
`HIT` bodies. Every downloaded calibration body matched the frozen size and hash.

## Public targets

- Cloudflare: `https://cf-cache-neutral-origin-probe.jumpserve-cache-study-20260826.workers.dev`,
  a cache-enabled Worker that fetches the S3 origin.
- Fastly: `https://fastly.jumpserve.dev`, attached to the isolated research
  service and the same S3 regional origin.

Both targets use public paths beginning with `/objects/`. A calibration object at
`objects/calibration/2f4d7a9c7e034f8aa7d096a292beb386.bin` is 262,144 bytes
with SHA-256
`a0f4fb786d29de3765726d8b0a2641608b533c1eba324157b95ede78feae0580`.
Local verification on 2026-08-27 returned that exact body from both targets and
observed diagnostic `MISS` then `HIT` transitions.

## Paired VPN trials

`run_vpn_trials.py` creates a new run identifier, randomly assigns hot/cold state
within each trial and requested region, copies the calibration payload to fresh S3
keys without touching either CDN, and then runs paired Cloudflare and Fastly
measurements through Mullvad. The provider warmup and measurement orders are
randomized and recorded. Main measurement makes one request per object.

```bash
python3 experiments/cdn_comparison/run_vpn_trials.py \
  --output-dir experiments/cdn_comparison/results/neutral-vpn-YYYYMMDD-HHMMSS

python3 experiments/cdn_comparison/analyze_vpn_trials.py \
  experiments/cdn_comparison/results/neutral-vpn-YYYYMMDD-HHMMSS
```

The default is three fresh-key trials per requested Los Angeles, Frankfurt, and
Singapore exit with 20 objects per class and three hot-object warmup requests.
The analyzer permits timing comparisons only within the actual `CF-Ray` colo or
`X-Served-By` Fastly POP and reports one estimate per trial/provider/vantage.

Do not use vendor cache-status headers as timing-only or portable-metadata model
features. They remain diagnostic treatment-integrity labels only.

## Completed provider-neutral batch

The first paired three-run batch completed on 2026-08-27 with 720 attempted
one-shot observations. Fastly produced exact treatment integrity and a 1.000
within-POP TTFB AUC in all nine location-runs. Cloudflare's mean within-colo TTFB
AUC was 0.955 from requested Frankfurt, 1.000 from Singapore, and 0.912 from Los
Angeles; routing changes reduced hot-state treatment integrity to 164/180 while
all 180 cold objects remained `MISS`es. RTT remained near chance/inconsistent.

See the [full result report](results/neutral-vpn-20260827-184204/result-report.md)
for the per-run results, failures, held-out operating-point check, and limitations.

## Exploratory CloudFront extension

CloudFront is deployed separately at `https://cloudfront.jumpserve.dev` using
[`infrastructure/cloudfront-exploratory.yaml`](infrastructure/cloudfront-exploratory.yaml).
The distribution uses HTTPS, no compression, a one-hour cache policy, no viewer
headers, cookies, or query strings in the cache key, and 100% CloudFront
`Server-Timing` sampling. Its public `X-Cache`, `X-Amz-Cf-Pop`, and
`Server-Timing` fields are diagnostic labels only. The deployment snapshot is in
[`deployed-cloudfront-snapshot-v1.json`](deployed-cloudfront-snapshot-v1.json).

This target deliberately reuses the study's S3 origin. Because CloudFront and S3
are both Amazon services, the resulting cold-miss path has a same-provider origin
confound. Keep these results exploratory and separate from the frozen,
provider-neutral Cloudflare/Fastly confirmatory protocol.

The exploratory runner and analyzer are invoked as follows:

```bash
python3 experiments/cdn_comparison/run_cloudfront_vpn_pilot.py \
  --output-dir experiments/cdn_comparison/results/cloudfront-vpn-YYYYMMDD-HHMMSS

python3 experiments/cdn_comparison/analyze_cloudfront_vpn_pilot.py \
  experiments/cdn_comparison/results/cloudfront-vpn-YYYYMMDD-HHMMSS \
  --report experiments/cdn_comparison/results/cloudfront-vpn-YYYYMMDD-HHMMSS/result-report.md
```

The first batch completed three fresh-key trials through pinned Los Angeles,
Frankfurt, and Singapore Mullvad relays. All 360 one-shot observations were
eligible. Observed POPs were LAX54-P9, FRA56-P16, and SIN2-P6; treatment integrity
was exact at 180/180 hot `HIT`s and 180/180 cold `MISS`es. Within-POP TTFB AUC was
1.000 in every location-run, and trial-1 thresholds classified all 240 trial-2/3
objects correctly without retuning. TCP-connect RTT AUC means were 0.588, 0.518,
and 0.548 respectively, with inconsistent run-level values.

One third warmup request in trial-3 Singapore ended in a connection reset after
the object already had successful `HIT`s. No observation had begun. The recovery
audited all 20 hot objects as successfully cached at SIN2-P6, required a same-POP
preflight, created no keys, and repeated neither warmup nor measurement. See the
[`full CloudFront report`](results/cloudfront-vpn-20260827-232700/result-report.md)
for the recorded deviation and per-run results.

## Locked confirmatory protocol

The multi-day protocol is preregistered in
[`preregistration-20260827.md`](preregistration-20260827.md). Its machine-readable
parameters and frozen training thresholds are in
[`confirmatory-config-v1.json`](confirmatory-config-v1.json), and
[`protocol-lock-v1.json`](protocol-lock-v1.json) records SHA-256 digests of all
protocol-critical source and configuration files.

Do not edit a locked file after confirmatory collection begins. A necessary
protocol change requires a new protocol ID, configuration, and lock; retain and
disclose any days collected under v1.

Day 1 cannot begin before 2026-08-28 UTC. Validate the frozen protocol without
creating objects or sending CDN requests:

```bash
python3 experiments/cdn_comparison/run_confirmatory_day.py --day 1 --dry-run
```

On each of five distinct UTC dates, run exactly one next-numbered day:

```bash
python3 experiments/cdn_comparison/run_confirmatory_day.py --day 1
python3 experiments/cdn_comparison/run_confirmatory_day.py --day 2
# Continue through --day 5, one command per later UTC date.
```

The wrapper verifies the protocol lock and pinned relay catalog. Before consuming
a day, it also checks the public calibration object through both HTTPS targets for
HTTP 200, exact size, and frozen SHA-256. It then invokes the paired fresh-key
runner. A created day directory is never automatically replaced or retried.

After one or more days exist, analyze their directories without threshold tuning:

```bash
python3 experiments/cdn_comparison/analyze_confirmatory.py \
  experiments/cdn_comparison/results/confirmatory-day-01-* \
  experiments/cdn_comparison/results/confirmatory-day-02-*
```

The analyzer will not declare confirmatory success until all five distinct days
are present. For each provider separately, success requires at least 12 of 15
defined day/exit within-POP AUC cells and a day-cluster bootstrap 95% lower bound
above 0.5.

## Locked three-CDN DigitalOcean-origin protocol

Protocol `cdn-demand-three-cdn-do-origin-v1` is preregistered in
[`preregistration-three-cdn-do-origin-v1.md`](preregistration-three-cdn-do-origin-v1.md).
Its machine-readable parameters are in
[`three-cdn-confirmatory-config-v1.json`](three-cdn-confirmatory-config-v1.json),
and [`three-cdn-protocol-lock-v1.json`](three-cdn-protocol-lock-v1.json) hashes the
protocol-critical source, infrastructure, and deployment snapshots.

The fresh-key runner copies the frozen payload to brand-new local-disk paths over
SSH before making any CDN request. It then applies one matched hot/cold assignment
to all three providers, randomizes provider order, warms only hot objects, and
makes one observation per object. The analyzer permits timing pairs only within
the response-observed provider POP.

Following the documented pre-data amendment, Day 1 may begin on 2026-08-28 UTC.
The validated dry-run is:

```bash
python3 experiments/cdn_comparison/run_three_cdn_confirmatory_day.py \
  --day 1 --dry-run
```

On five distinct UTC dates, run one next-numbered day without replacement:

```bash
python3 experiments/cdn_comparison/run_three_cdn_confirmatory_day.py --day 1
python3 experiments/cdn_comparison/run_three_cdn_confirmatory_day.py --day 2
# Continue through --day 5, one command per later UTC date.
```

Analyze collected days without retuning:

```bash
python3 experiments/cdn_comparison/analyze_three_cdn_confirmatory.py \
  experiments/cdn_comparison/results/three-cdn-confirmatory-day-01-* \
  experiments/cdn_comparison/results/three-cdn-confirmatory-day-02-*
```

The primary success rule is evaluated separately for Cloudflare, Fastly, and
CloudFront. The plural-CDN claim requires at least two providers to meet their
own preregistered rule; all three results and failures are reported regardless.

Day 1 completed on 2026-08-28 with all 360 planned observations eligible and
exact treatment integrity. Within-POP TTFB AUC was 1.000 in all three Cloudflare
cells, 0.995-1.000 in the Fastly cells, and 0.990-1.000 in the CloudFront cells.
The S3-trained fixed thresholds transferred well to Fastly but poorly to cold
observations on Cloudflare and CloudFront, reinforcing that absolute thresholds
are origin/configuration dependent. See the
[`Day 1 report`](results/three-cdn-confirmatory-day-01-20260828-005209/result-report.md).

Day 2 completed on 2026-08-29 with all 360 observations eligible. Fastly and
CloudFront again had exact treatment integrity. Cloudflare had 57/60 hot `HIT`s
and 60/60 cold `MISS`es because the requested Los Angeles batch reached ATL,
DEN, DFW, SEA, and SJC; the three hot misses occurred at ATL and were retained
as assigned-hot observations. Day 2 within-POP TTFB AUC ranged from 0.985-1.000
for Cloudflare, was 1.000 for all Fastly cells, and ranged from 0.998-1.000 for
CloudFront. Across both dates, the provider mean TTFB AUCs are 0.998, 0.999, and
0.998, respectively. See the
[`Day 2 report`](results/three-cdn-confirmatory-day-02-20260829-001910/result-report.md).

Day 3 completed on 2026-08-30 with all 360 observations eligible and exact
treatment integrity. The requested exits reached stable provider POPs: SJC, FRA,
and SIN for Cloudflare; BUR, FRA, and SIN for Fastly; and LAX50-P5, FRA60-P11,
and SIN2-P1 for CloudFront. Day 3 within-POP TTFB AUC ranged from 0.995-1.000
for Cloudflare, 0.998-1.000 for Fastly, and 0.965-1.000 for CloudFront. Across
the first three dates, all 1,080 observations are eligible and provider mean TTFB
AUC is 0.998, 0.999, and 0.995, respectively. See the
[`Day 3 report`](results/three-cdn-confirmatory-day-03-20260830-035320/result-report.md).
No confirmatory success decision is available until all five days are collected.
