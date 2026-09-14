# Three-CDN confirmatory Day 2 result

- Protocol: `cdn-demand-three-cdn-do-origin-v1`
- UTC date: 2026-08-29
- Collection: 2026-08-29T00:19:10Z to 2026-08-29T00:40:31Z
- Runner exit code: 0

## Integrity and completeness

- The runner verified configuration SHA-256
  `913f8393ae89d3e399dff69607b62bd71cac65c1fa9048dfabdb6123aad7f2d6`
  and lock SHA-256
  `b747960973b255c10c1df65b8b2ef3eb7e9f9324af5d7271d35a62fd489b0c8e`.
- It created 120 brand-new DigitalOcean-origin keys before CDN requests.
- All 360 planned one-shot observations are present, curl-successful, HTTP 200,
  and complete at 262,144 bytes. There were no recorded runner failures.
- Fastly and CloudFront had exact treatment integrity: each produced 60/60 hot
  diagnostic `HIT`s and 60/60 cold diagnostic `MISS`es.
- Cloudflare produced 57/60 hot `HIT`s and 60/60 cold `MISS`es. All three hot
  misses occurred at ATL during the requested Los Angeles batch after its warmup
  traffic had reached other colos. They remain assigned-hot observations in the
  primary demand analysis and were not retried or replaced.
- Day 2 treatment integrity was therefore 177/180 hot `HIT`s and 180/180 cold
  `MISS`es overall.
- Mullvad used the three pinned relays and disconnected cleanly after collection.

## Assigned-demand timing results

All AUCs compare assigned-hot and assigned-cold observations only within the
response-observed CDN POP. Lower timing is positive for hot.

| Provider | Requested exit | Observed POPs | Eligible | RTT AUC | TTFB AUC | Residual AUC |
|---|---|---|---:|---:|---:|---:|
| Cloudflare | Frankfurt | FRA | 40/40 | 0.470 | 1.000 | 1.000 |
| Cloudflare | Singapore | SIN | 40/40 | 0.665 | 1.000 | 1.000 |
| Cloudflare | Los Angeles | ATL, DEN, DFW, SEA, SJC | 40/40 | 0.437 | 0.985 | 0.985 |
| CloudFront | Frankfurt | FRA60-P11 | 40/40 | 0.338 | 1.000 | 1.000 |
| CloudFront | Singapore | SIN2-P1 | 40/40 | 0.475 | 1.000 | 1.000 |
| CloudFront | Los Angeles | LAX50-P5 | 40/40 | 0.443 | 0.998 | 0.912 |
| Fastly | Frankfurt | FRA | 40/40 | 0.350 | 1.000 | 1.000 |
| Fastly | Singapore | SIN | 40/40 | 0.522 | 1.000 | 1.000 |
| Fastly | Los Angeles | BUR | 40/40 | 0.445 | 1.000 | 0.988 |

The Day 2 provider mean TTFB AUCs are 0.995 for Cloudflare, 1.000 for Fastly,
and 0.999 for CloudFront. The Cloudflare Los Angeles treatment-integrity loss is
reflected in the assigned-demand TTFB AUC of 0.985; the separate diagnostic
cache-label TTFB AUC for that cell is 0.992. RTT AUC remains inconsistent across
cells and is not evidence of cache presence.

## Frozen operating-point transfer

The thresholds learned on the earlier S3-origin pilots were applied unchanged.
Day 2 alone produced:

| Provider | Hot sensitivity | Cold specificity | Balanced accuracy |
|---|---:|---:|---:|
| Cloudflare | 59/60 (0.983) | 20/60 (0.333) | 0.658 |
| Fastly | 59/60 (0.983) | 57/60 (0.950) | 0.967 |
| CloudFront | 60/60 (1.000) | 38/60 (0.633) | 0.817 |

Across Days 1 and 2, the unchanged thresholds have balanced accuracy 0.667 for
Cloudflare, 0.975 for Fastly, and 0.762 for CloudFront. Strong rank AUC alongside
weaker Cloudflare and CloudFront fixed-threshold transfer remains evidence that
an absolute TTFB threshold is not universal across origin and configuration
changes.

## Two-day interim result

Across the first two preregistered dates, all 720 observations are eligible.
Combined treatment integrity is 357/360 hot `HIT`s and 360/360 cold `MISS`es.
The locked analyzer reports provider mean assigned-demand TTFB AUCs of 0.998 for
Cloudflare, 0.999 for Fastly, and 0.998 for CloudFront. Its day-cluster bootstrap
intervals are 0.995-1.000, 0.998-1.000, and 0.997-0.999, respectively, but these
two-day intervals have very limited day-level support.

No provider can yet satisfy the completion-dependent success rule: only 2 of 5
required UTC dates and 6 of 15 planned provider cells have been collected. Days
3-5 must be collected on later distinct UTC dates without retuning, replacement,
or observation retry.
