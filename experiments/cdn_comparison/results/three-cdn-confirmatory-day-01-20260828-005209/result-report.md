# Three-CDN confirmatory Day 1 result

- Protocol: `cdn-demand-three-cdn-do-origin-v1`
- UTC date: 2026-08-28
- Collection: 2026-08-28T00:52:09Z to 2026-08-28T01:13:31Z
- Runner exit code: 0

## Integrity and completeness

- The documented pre-data amendment moved the earliest collection date from
  2026-08-29 to 2026-08-28 UTC before any trial key or observation existed.
- The runner verified configuration SHA-256
  `913f8393ae89d3e399dff69607b62bd71cac65c1fa9048dfabdb6123aad7f2d6`
  and lock SHA-256
  `b747960973b255c10c1df65b8b2ef3eb7e9f9324af5d7271d35a62fd489b0c8e`.
- It created 120 brand-new DigitalOcean-origin keys before CDN requests.
- All 360 planned one-shot observations are present, curl-successful, HTTP 200,
  and complete at 262,144 bytes. There were no recorded runner failures.
- Treatment integrity was exact for every provider and requested exit: 180/180
  assigned-hot observations were diagnostic `HIT`s and 180/180 assigned-cold
  observations were diagnostic `MISS`es overall.
- Mullvad used the three pinned relays and disconnected cleanly after collection.

## Assigned-demand timing results

All AUCs compare hot and cold observations only within the response-observed CDN
POP. Lower timing is positive for hot.

| Provider | Requested exit | Observed POPs | Eligible | RTT AUC | TTFB AUC | Residual AUC |
|---|---|---|---:|---:|---:|---:|
| Cloudflare | Frankfurt | FRA | 40/40 | 0.335 | 1.000 | 1.000 |
| Cloudflare | Singapore | SIN | 40/40 | 0.492 | 1.000 | 1.000 |
| Cloudflare | Los Angeles | DEN, PDX, SEA | 40/40 | 0.466 | 1.000 | 0.990 |
| CloudFront | Frankfurt | FRA60-P11 | 40/40 | 0.623 | 0.990 | 0.932 |
| CloudFront | Singapore | SIN2-P1 | 40/40 | 0.552 | 1.000 | 1.000 |
| CloudFront | Los Angeles | LAX50-P5 | 40/40 | 0.520 | 1.000 | 0.958 |
| Fastly | Frankfurt | FRA | 40/40 | 0.660 | 1.000 | 1.000 |
| Fastly | Singapore | SIN | 40/40 | 0.588 | 1.000 | 1.000 |
| Fastly | Los Angeles | BUR | 40/40 | 0.405 | 0.995 | 0.998 |

The one-day provider mean TTFB AUCs are 1.000 for Cloudflare, 0.998 for Fastly,
and 0.997 for CloudFront. These are Day 1 estimates, not final confirmatory
results. With only one of five preregistered days, the day-cluster intervals are
degenerate and no provider can yet satisfy the completion-dependent success rule.

## Frozen operating-point transfer

The thresholds learned on the earlier S3-origin pilots were applied unchanged:

| Provider | Hot sensitivity | Cold specificity | Balanced accuracy |
|---|---:|---:|---:|
| Cloudflare | 60/60 (1.000) | 21/60 (0.350) | 0.675 |
| Fastly | 60/60 (1.000) | 58/60 (0.967) | 0.983 |
| CloudFront | 60/60 (1.000) | 25/60 (0.417) | 0.708 |

The near-perfect rank discrimination alongside weaker fixed-threshold transfer
for Cloudflare and CloudFront is evidence against a universal absolute TTFB
threshold. Changing the origin provider shifted cold-path timing even though the
within-POP hot/cold ordering remained strong on Day 1.

## Interpretation boundary

Day 1 is unusually clean but cannot establish the preregistered result. Four
additional distinct UTC-day batches must be collected without retuning,
replacement, or observation retry. Report all later failures and routing changes;
do not use this Day 1 outcome to alter the locked design.
