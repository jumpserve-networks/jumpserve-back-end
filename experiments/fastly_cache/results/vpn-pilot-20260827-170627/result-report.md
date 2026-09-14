# Fastly External VPN Feasibility Pilot

Run time: 2026-08-27 17:06–17:09 UTC

## Design

- External observer: Mullvad exits requested in Los Angeles, Frankfurt, and
  Singapore.
- Fresh assignment per exit: 10 hot and 10 cold objects.
- Warm-up: three GETs per hot object; cold objects untouched.
- Observation: one shuffled GET per object.
- Client transport: HTTP/1.1 to Fastly's pre-DNS test route.
- Fastly labels: `X-Cache`; observed POP: `X-Served-By`.
- Timing comparison: hot versus cold within each observed Fastly POP.

All 60 measurement requests returned HTTP 200. Treatment integrity was exact:
30/30 hot objects were Fastly `HIT`s and 30/30 cold objects were Fastly `MISS`es.

## Assigned-demand results

| Requested exit | Observed POP | Hot/COLD n | Hot/COLD median TTFB (ms) | TTFB AUC | Bootstrap 95% | RTT AUC |
|---|---:|---:|---:|---:|---:|---:|
| Los Angeles | BUR | 10/10 | 139.81 / 379.87 | 1.000 | 1.000–1.000 | 0.240 |
| Frankfurt | FRA | 10/10 | 259.32 / 358.71 | 0.950 | 0.820–1.000 | 0.630 |
| Singapore | SIN | 10/10 | 254.87 / 412.26 | 1.000 | 1.000–1.000 | 0.360 |

The unweighted macro-average TTFB AUC is 0.983. The corresponding RTT AUC
macro-average is 0.410, again showing that TCP-connect RTT is not a dependable
cache-presence signal.

Intervals are exploratory percentile bootstraps with 20,000 within-class
resamples. With only 10 objects per class, perfect observed separation produces a
degenerate bootstrap interval and should not be read as population certainty.

## Calibration

A separately saved local request pair reached MSP and changed from Fastly `MISS`
to `HIT`; request TTFB changed from 293.331 ms to 52.282 ms.

## Limitations and next test

This is promising second-provider feasibility evidence, not the final independent
cross-CDN result. Fastly fetched from a Cloudflare Worker origin, and the client
test route used HTTP rather than the HTTPS transport used in the earlier
Cloudflare VPN trials. Run count and object count are also too small for a
confirmatory claim.

The next Fastly experiment should use a provider-neutral origin and valid TLS
hostname, retain identical bytes and cache policy across providers, repeat at
least three fresh-key trials per exit, and evaluate a timing rule selected without
retuning on the new Fastly data.
