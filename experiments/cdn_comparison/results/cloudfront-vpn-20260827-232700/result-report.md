# Exploratory CloudFront External-VPN Pilot

CloudFront uses the same Amazon S3 origin in this pilot. The results are therefore exploratory and are not provider-neutral confirmatory evidence.
The frozen Cloudflare/Fastly confirmatory protocol was not changed.

## Per-run results

AUC comparisons are weighted within the observed CloudFront POP. Explicit CloudFront cache diagnostics are labels only and are not timing features.

| Trial | Requested vantage | Observed POP | Eligible/attempted | Hot HIT | Cold MISS | RTT AUC | TTFB AUC | Residual AUC | Cache-label TTFB AUC |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| trial-01 | de-frankfurt | FRA56-P16 | 40/40 | 20/20 | 20/20 | 0.490 | 1.000 | 1.000 | 1.000 |
| trial-01 | sg-singapore | SIN2-P6 | 40/40 | 20/20 | 20/20 | 0.752 | 1.000 | 1.000 | 1.000 |
| trial-01 | us-los-angeles | LAX54-P9 | 40/40 | 20/20 | 20/20 | 0.552 | 1.000 | 1.000 | 1.000 |
| trial-02 | de-frankfurt | FRA56-P16 | 40/40 | 20/20 | 20/20 | 0.515 | 1.000 | 1.000 | 1.000 |
| trial-02 | sg-singapore | SIN2-P6 | 40/40 | 20/20 | 20/20 | 0.497 | 1.000 | 1.000 | 1.000 |
| trial-02 | us-los-angeles | LAX54-P9 | 40/40 | 20/20 | 20/20 | 0.725 | 1.000 | 1.000 | 1.000 |
| trial-03 | de-frankfurt | FRA56-P16 | 40/40 | 20/20 | 20/20 | 0.550 | 1.000 | 1.000 | 1.000 |
| trial-03 | sg-singapore | SIN2-P6 | 40/40 | 20/20 | 20/20 | 0.395 | 1.000 | 1.000 | 1.000 |
| trial-03 | us-los-angeles | LAX54-P9 | 40/40 | 20/20 | 20/20 | 0.485 | 1.000 | 1.000 | 1.000 |

## Totals

- Eligible responses: 360/360
- Assigned hot objects diagnosed as HIT: 180/180
- Untouched cold objects diagnosed as MISS: 180/180
- `X-Cache`/`Server-Timing` diagnostic agreement: 360/360
- Reported hit layers: EDGE=180, NONE=180

## Across-run estimates

- `de-frankfurt` (3 trials):
  - TCP connect RTT estimate: mean 0.518, range 0.490-0.550, trial-bootstrap 95% 0.490-0.550.
  - request-to-first-byte: mean 1.000, range 1.000-1.000, trial-bootstrap 95% 1.000-1.000.
  - TTFB minus RTT estimate: mean 1.000, range 1.000-1.000, trial-bootstrap 95% 1.000-1.000.
- `sg-singapore` (3 trials):
  - TCP connect RTT estimate: mean 0.548, range 0.395-0.752, trial-bootstrap 95% 0.395-0.752.
  - request-to-first-byte: mean 1.000, range 1.000-1.000, trial-bootstrap 95% 1.000-1.000.
  - TTFB minus RTT estimate: mean 1.000, range 1.000-1.000, trial-bootstrap 95% 1.000-1.000.
- `us-los-angeles` (3 trials):
  - TCP connect RTT estimate: mean 0.588, range 0.485-0.725, trial-bootstrap 95% 0.485-0.725.
  - request-to-first-byte: mean 1.000, range 1.000-1.000, trial-bootstrap 95% 1.000-1.000.
  - TTFB minus RTT estimate: mean 1.000, range 1.000-1.000, trial-bootstrap 95% 1.000-1.000.

## Held-out operating point

Thresholds below were selected on trial 1 and applied unchanged to trials 2-3 within the same requested vantage.

| Vantage | Threshold (ms) | Training balanced accuracy | Test accuracy | Test balanced accuracy | Hot correct | Cold correct |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| de-frankfurt | 353.934 | 1.000 | 1.000 | 1.000 | 40/40 | 40/40 |
| sg-singapore | 630.106 | 1.000 | 1.000 | 1.000 | 40/40 | 40/40 |
| us-los-angeles | 252.151 | 1.000 | 1.000 | 1.000 | 40/40 | 40/40 |

## Collection deviations

- `trial-03/sg-singapore` had 1 failed warmup transport request after 59/60 successful warmup requests. The main observation had not started; all 20/20 hot objects already had a successful HIT at `SIN2-P6`. Recovery created no new keys, did not repeat warmup or measurement, and required a same-POP preflight at `SIN2-P6`. Recovery failure: `none`.

## Interpretation limits

- The S3 origin and CloudFront are operated by the same provider, so cold-miss timing is not directly comparable with the provider-neutral Cloudflare/Fastly study.
- `X-Cache`, `Server-Timing`, POP, and hit-layer fields are diagnostic metadata. Only curl timing fields enter the timing AUC and threshold results.
- Requested VPN locations are not assumed to be serving POPs; analysis uses the observed CloudFront POP for every AUC comparison.
- This exploratory batch was not preregistered and must not be merged into the frozen confirmatory-v1 success criterion.
