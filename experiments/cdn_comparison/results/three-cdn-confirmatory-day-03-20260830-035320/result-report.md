# Three-CDN confirmatory Day 3 result

- Protocol: `cdn-demand-three-cdn-do-origin-v1`
- UTC date: 2026-08-30
- Collection: 2026-08-30T03:53:20Z to 2026-08-30T04:14:16Z
- Runner exit code: 0

## Integrity and completeness

- The runner verified configuration SHA-256
  `913f8393ae89d3e399dff69607b62bd71cac65c1fa9048dfabdb6123aad7f2d6`
  and lock SHA-256
  `b747960973b255c10c1df65b8b2ef3eb7e9f9324af5d7271d35a62fd489b0c8e`.
- It created 120 brand-new DigitalOcean-origin keys before CDN requests.
- All 360 planned one-shot observations are present, curl-successful, HTTP 200,
  and complete at 262,144 bytes. There were no recorded runner failures.
- Treatment integrity was exact for all providers and exits: 180/180 assigned-hot
  observations were diagnostic `HIT`s and 180/180 assigned-cold observations
  were diagnostic `MISS`es overall.
- Observed POPs were stable within each provider and requested exit: Cloudflare
  reached SJC, FRA, and SIN; Fastly reached BUR, FRA, and SIN; CloudFront reached
  LAX50-P5, FRA60-P11, and SIN2-P1.
- Mullvad used the three pinned relays and disconnected cleanly after collection.

## Assigned-demand timing results

All AUCs compare assigned-hot and assigned-cold observations only within the
response-observed CDN POP. Lower timing is positive for hot.

| Provider | Requested exit | Observed POPs | Eligible | RTT AUC | TTFB AUC | Residual AUC |
|---|---|---|---:|---:|---:|---:|
| Cloudflare | Frankfurt | FRA | 40/40 | 0.550 | 0.995 | 0.988 |
| Cloudflare | Singapore | SIN | 40/40 | 0.380 | 1.000 | 1.000 |
| Cloudflare | Los Angeles | SJC | 40/40 | 0.395 | 1.000 | 1.000 |
| CloudFront | Frankfurt | FRA60-P11 | 40/40 | 0.588 | 1.000 | 1.000 |
| CloudFront | Singapore | SIN2-P1 | 40/40 | 0.652 | 1.000 | 1.000 |
| CloudFront | Los Angeles | LAX50-P5 | 40/40 | 0.443 | 0.965 | 0.882 |
| Fastly | Frankfurt | FRA | 40/40 | 0.455 | 0.998 | 0.998 |
| Fastly | Singapore | SIN | 40/40 | 0.583 | 1.000 | 1.000 |
| Fastly | Los Angeles | BUR | 40/40 | 0.410 | 0.998 | 0.998 |

The Day 3 provider mean TTFB AUCs are 0.998 for Cloudflare, 0.998 for Fastly,
and 0.988 for CloudFront. CloudFront Los Angeles had the day's lowest TTFB AUC
at 0.965, while retaining exact cache-state integrity. RTT AUC ranged from 0.380
to 0.652 across cells and remains an inconsistent control rather than evidence
of cache presence.

## Frozen operating-point transfer

The thresholds learned on the earlier S3-origin pilots were applied unchanged.
Day 3 alone produced:

| Provider | Hot sensitivity | Cold specificity | Balanced accuracy |
|---|---:|---:|---:|
| Cloudflare | 60/60 (1.000) | 17/60 (0.283) | 0.642 |
| Fastly | 59/60 (0.983) | 58/60 (0.967) | 0.975 |
| CloudFront | 60/60 (1.000) | 32/60 (0.533) | 0.767 |

Across Days 1-3, the unchanged thresholds have balanced accuracy 0.658 for
Cloudflare, 0.975 for Fastly, and 0.764 for CloudFront. The continued contrast
between high within-POP rank AUC and weaker Cloudflare/CloudFront fixed-threshold
transfer argues against a universal absolute TTFB threshold.

## Three-day interim result

Across the first three preregistered dates, all 1,080 observations are eligible.
Combined treatment integrity is 537/540 hot `HIT`s and 540/540 cold `MISS`es.
The locked analyzer reports provider mean assigned-demand TTFB AUCs of 0.998 for
Cloudflare, 0.999 for Fastly, and 0.995 for CloudFront. Its day-cluster bootstrap
intervals are 0.995-1.000, 0.998-1.000, and 0.988-0.999, respectively, but these
interim intervals still have only three day-level clusters.

No provider can yet satisfy the completion-dependent success rule: only 3 of 5
required UTC dates and 9 of 15 planned provider cells have been collected. Days
4-5 must be collected on later distinct UTC dates without retuning, replacement,
or observation retry.
