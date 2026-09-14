# Cloudflare Cache-Residency Experiment

This experiment tests a narrower, measurable version of the research idea:

> Can client-visible network timing predict whether an object was resident in the
> Cloudflare cache that handled the request?

Cloudflare's `CF-Cache-Status` is the ground-truth label. The analyzer deliberately
compares that label with timing-only features. If timing cannot separate known
`HIT` and `MISS` responses on this controlled CDN, it is unlikely to infer regional
YouTube popularity reliably. If it can, that supports a follow-up YouTube study; it
does not by itself prove that cache residency implies popularity.

## Recommended Vantage Points

Use short-lived EC2 instances for the first real trial. The important location is
the observed Cloudflare colo (`cf_colo`), not the AWS Region name. Cloudflare uses
Anycast, so two AWS Regions can occasionally reach an unexpected or shared colo.

A useful first spread is:

- `us-east-1` (Virginia)
- `us-west-2` (Oregon)
- `eu-west-2` (London)
- `ap-northeast-2` (Seoul)
- `ap-southeast-2` (Sydney)
- `sa-east-1` (Sao Paulo)

One small on-demand Linux instance in each Region is enough. Run the probe on the
instance itself. Instances need Python 3, a recent curl (with `%{json}` write-out),
DNS, and outbound HTTPS. They need no inbound port if you manage them through AWS
Systems Manager.

Lambda is cheaper and easier to fan out, but its standard Python HTTP stack does
not expose DNS, TCP, TLS, and first-byte phases as cleanly as curl. It is a good
second implementation after the EC2 experiment establishes which measurements
matter.

A commercial VPN is suitable for checking which colo and cache status an exit
reaches. It is not a clean latency vantage: timing includes the tunnel between your
computer and the VPN exit. Running curl on a remote host measures from that remote
location and avoids this confounder.

## Run A Sequential Mullvad Pilot

`run_vpn_pilot.py` automates a small external-observer trial through Mullvad exits
in Los Angeles, Frankfurt, and Singapore. It requires the Mullvad desktop app and
CLI to be installed and logged in. Start with Mullvad disconnected.

The runner creates fresh object keys at every location, warms only the hot class,
makes one shuffled measurement per object, records the actual Mullvad exit and
Cloudflare trace, disconnects after every location, and restores the configured
relay country when it finishes. Because Mullvad is a full-device VPN, switching
exits can briefly interrupt other network applications on the machine.

```bash
python3 experiments/cloudflare_cache/run_vpn_pilot.py \
  --output-dir experiments/cloudflare_cache/results/vpn-pilot-UNIQUE

python3 experiments/cloudflare_cache/analyze.py \
  --phase measure \
  experiments/cloudflare_cache/results/vpn-pilot-UNIQUE/*.measure.jsonl
```

After multiple independent VPN trials, use the run-aware analyzer for the primary
assigned-demand result. It produces one pair-weighted estimate per trial while
allowing timing comparisons only within each observed colo, then summarizes the
independent trial estimates:

```bash
python3 experiments/cloudflare_cache/analyze_vpn_runs.py \
  experiments/cloudflare_cache/results/vpn-pilot-*/*.measure.jsonl
```

Use `--location LABEL:COUNTRY:CITY` to run a subset or replacement trial. The
option can be repeated. For example, a single fresh New York trial is:

```bash
python3 experiments/cloudflare_cache/run_vpn_pilot.py \
  --output-dir experiments/cloudflare_cache/results/vpn-new-york-UNIQUE \
  --location us-new-york:us:nyc
```

Treat this as a pilot rather than a final experiment. Use at least three fresh-key
runs per location for confirmatory analysis, and group observations by the colo
reported in `CF-Ray` because a single VPN relay can reach more than one Cloudflare
colo.

## Prepare The Cloudflare Target

Use a hostname and origin you control. Create at least 50 `hot` and 50 `cold`
objects with identical sizes, content types, cache headers, and origin-generation
cost. A few hundred objects per class is better. Do not put credentials or signed
secrets in the URLs because results contain the URL.

Configure the `/cache-study/` path so that:

- the objects are eligible for Cloudflare cache;
- they have a positive Edge TTL long enough for the experiment;
- origin responses are stable (`ETag` and bytes do not change);
- no cookies, authorization, or cache-bypass rule changes eligibility;
- Tiered Cache is **off for the first trial**, then tested separately.

Cloudflare does not cache arbitrary extensions by default in every configuration.
Use an explicit Cache Rule for the experiment path instead of relying on the
example `.bin` extension. Verify the target by requesting one URL twice. A healthy
basic configuration normally changes from `MISS` to `HIT`, and later hits normally
carry `Age`.

Do not use `HEAD`: Cloudflare can turn a cold `HEAD` into an origin `GET` and fill
the object. The probe uses full `GET` requests by default. Its optional `--range`
mode is useful for a later video-segment experiment, but range behavior adds
another variable and should not be used for the first pass.

Copy and edit the example manifest:

```bash
cp experiments/cloudflare_cache/manifest.example.csv /tmp/cache-study.csv
```

## Run A Calibration Trial

First, use separate disposable objects and request each twice from one host:

```bash
python3 experiments/cloudflare_cache/probe.py \
  --manifest /tmp/cache-study.csv \
  --vantage us-east-1-host-a \
  --phase calibration \
  --samples 2 \
  --output /tmp/us-east-1-calibration.jsonl

python3 experiments/cloudflare_cache/analyze.py \
  --minimum-per-class 1 \
  /tmp/us-east-1-calibration.jsonl
```

This checks instrumentation. It is not the main experiment, because the first
measurement changes a cold object's state.

## Run The Controlled Hot/Cold Trial

1. Purge only the experiment URLs, or create a brand-new versioned path.
2. From each target colo, repeatedly request only the `hot` objects during a fixed
   warm-up window. Save these records with `--phase warmup` but exclude them from
   the final measurement dataset.
3. Leave every `cold` object untouched.
4. After a fixed quiet interval, make exactly one measurement request per hot and
   cold object from the same target colo. Use `--samples 1`.
5. Repeat with new object URLs on multiple days and at multiple times of day.

Example warm-up command on each remote host:

```bash
python3 experiments/cloudflare_cache/probe.py \
  --manifest /tmp/cache-study.csv \
  --treatment hot \
  --vantage eu-west-2-host-a \
  --phase warmup \
  --samples 10 \
  --delay-ms 1000 \
  --output /tmp/eu-west-2-warmup.jsonl
```

Example measurement command on each remote host:

```bash
python3 experiments/cloudflare_cache/probe.py \
  --manifest /tmp/cache-study.csv \
  --vantage eu-west-2-host-a \
  --phase measure \
  --samples 1 \
  --shuffle \
  --output /tmp/eu-west-2-measure.jsonl
```

Copy the JSONL files back to one machine and analyze them together:

```bash
python3 experiments/cloudflare_cache/analyze.py \
  --phase measure \
  results/*.jsonl
```

The report contains two distinct tests:

- **Treatment check:** hot objects should have a higher observed hit rate than cold
  objects in the same vantage/colo.
- **Demand-inference check:** timing-only AUC predicts the assigned hot/cold
  recent-demand treatment. This is the primary outcome for the research claim.
- **Mechanism check:** a separate timing-only AUC compares known `HIT` and `MISS`
  responses to test whether cache state explains the demand result. Cache-status
  headers label this analysis but are never timing features. AUC 0.5 is chance;
  AUC 1.0 is perfect lower-timing discrimination.

Pay particular attention to `request-to-first-byte` and `TTFB minus RTT estimate`.
The TCP RTT estimate itself should usually be a weak discriminator because both
hits and misses terminate at the same nearby Cloudflare ingress.

## Run From Placed Cloudflare Workers

`regional_probe_worker.js` is a Worker version of the randomized experiment. Its
`__PROBE_REGION__` and `__PROBE_SLUG__` placeholders are replaced during deployment,
and its upload metadata uses an explicit placement hint such as:

```json
{
  "compatibility_flags": ["global_fetch_strictly_public"],
  "placement": { "region": "aws:eu-west-2" }
}
```

The compatibility flag is required when the probe and cache target are Workers on
the same `workers.dev` zone. A service binding is intentionally not used because it
would bypass the public cache path under test.

Each deployed regional probe exposes three uncached endpoints:

- `/info` returns its configured placement and request context.
- `/warm?run=UNIQUE_ID&count=20` requests only its hot objects.
- `/measure?run=UNIQUE_ID&count=20` makes one randomized request to each hot and
  untouched cold object.

Use the same `run` value for warm-up and measurement in one region, but never reuse
that value for a later experiment. Object URLs include the probe slug, so the same
run value can be used concurrently across regions without sharing cache keys.

The Worker fully consumes each 256 KiB response and records response-header,
first-body-byte, and full-body time along with `CF-Cache-Status`, `Age`, and
`CF-Ray`. The target colo is the suffix of the target response's `CF-Ray` value.
The upstream request limit on Workers Free permits at most 20 objects per class in
the measurement endpoint (40 subrequests).

Analyze the downloaded regional measurement documents with:

```bash
python3 experiments/cloudflare_cache/analyze_regional.py \
  --minimum-per-class 10 \
  /tmp/cf-regional-*-measure.json
```

Placed Workers are a Cloudflare-network vantage, not a residential or ordinary
internet client. Treat these measurements as a distributed CDN calibration and
validate a subset later from VPN or independent hosts.

## Interpretation And Confounders

- Analyze within each `(vantage, cf_colo)` group. Pooling locations lets geographic
  RTT masquerade as a cache effect.
- Cloudflare may admit a cacheable object on its first request. This means a simple
  hot/cold experiment primarily tests cache residency, not a gradual popularity
  threshold.
- Tiered Cache can serve a lower-tier miss from an upper tier. `Age` can be inherited
  from that upper tier, and the first lower-tier fill can behave differently from a
  local hit. Run this as a separate treatment.
- `CF-Cache-Status: REVALIDATED`, `EXPIRED`, `BYPASS`, and `DYNAMIC` are not clean
  `HIT`/`MISS` labels. The analyzer reports them but excludes them from binary AUC.
- An EC2 source is a cloud-network vantage, not a residential ISP vantage. It is
  appropriate for validating the measurement method, but later YouTube work should
  add residential probes because Google Global Cache placement is ISP-dependent.
- Keep object size and origin delay constant. Otherwise download time or origin
  behavior can be mistaken for cache behavior.

Cloudflare references:

- [Cache response statuses and Age](https://developers.cloudflare.com/cache/concepts/cache-responses/)
- [Troubleshooting and confirming a cache fill](https://developers.cloudflare.com/cache/troubleshooting/investigating-uncached-responses/)
- [Cf-Ray header](https://developers.cloudflare.com/fundamentals/reference/http-headers/#cf-ray)
- [Tiered Cache](https://developers.cloudflare.com/cache/how-to/tiered-cache/)
