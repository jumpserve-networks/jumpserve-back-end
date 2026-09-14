# Three-Run Mullvad External-Observer Summary

Date: 2026-08-27

This summary combines three measured fresh-key trials for each requested Mullvad
exit: Los Angeles, Frankfurt, and Singapore. Timing AUC uses assigned hot/cold
recent demand as the primary label and never uses `CF-Cache-Status` as a feature.
Each trial estimate compares hot and cold timing only within the same observed
`CF-Ray` colo, then weights those within-colo AUCs by the available hot/cold pair
counts. Results are averaged across independent trials without pooling raw timing
across trials.

## Dataset

- 9 measured vantage/trial files: 3 per requested VPN location.
- 360/360 measurement requests succeeded.
- Frankfurt: 60/60 hot `HIT`, 60/60 cold `MISS`.
- Singapore: 60/60 hot `HIT`, 60/60 cold `MISS`.
- Los Angeles: 58/60 hot `HIT`, 60/60 cold `MISS`.
- Overall treatment integrity: 178/180 hot `HIT`, 180/180 cold `MISS`.

The original second Los Angeles attempt is not one of the nine measured files. A
single warm-up body transfer was reset by the peer, so the safety runner skipped
its measurement phase. The 100 warm-up records and explicit failure remain in
`vpn-pilot-20260827-r2/`. A fresh-key Los Angeles replacement supplied the missing
measured trial.

## Primary Assigned-Demand Results

| Requested VPN location | Trials | Request-TTFB AUC mean | Run range | Trial-bootstrap 95% interval | TCP-RTT AUC mean |
|---|---:|---:|---:|---:|---:|
| Los Angeles | 3 | 0.895 | 0.887–0.904 | 0.887–0.904 | 0.470 |
| Singapore | 3 | 0.711 | 0.670–0.757 | 0.670–0.757 | 0.532 |
| Frankfurt | 3 | 0.598 | 0.495–0.688 | 0.495–0.688 | 0.454 |

The interval resamples only three independent trial estimates and is therefore a
descriptive, low-resolution uncertainty summary. It should not be presented as a
precise population interval.

Run-level request-to-first-byte AUCs were:

- Los Angeles: 0.894, 0.904, 0.887.
- Singapore: 0.757, 0.705, 0.670.
- Frankfurt: 0.688, 0.495, 0.613.

For request-to-first-byte minus the TCP-RTT estimate, across-run mean AUC was
0.820 in Los Angeles, 0.604 in Singapore, and 0.605 in Frankfurt.

## Routing and Treatment Findings

- Frankfurt remained at Cloudflare FRA and Singapore remained at SIN in all
  measured trials.
- The requested Los Angeles exits reached LAX, SJC, DFW, MCI, and PHX across the
  three trials. The initial Cloudflare trace did not predict all per-object colos.
- In one Los Angeles trial, routing changed during warm-up and measurement. Two
  assigned-hot objects reached colos where they were not resident and returned
  `MISS`, reducing hot treatment integrity to 18/20 for that trial.
- Its assigned-demand TTFB AUC remained 0.904 after strictly within-colo
  comparisons, while the diagnostic cache-mechanism result was cleaner. The
  assigned-demand estimate is the primary result.

## Interpretation

The data support a geographically heterogeneous result: request-to-first-byte
carried a strong assigned-demand signal through the tested Los Angeles exits, a
moderate and repeatable signal through Singapore, and a weak/inconsistent signal
through Frankfurt. TCP-connect RTT did not provide a consistent cache or demand
indicator.

These experiments provide direct external-observer evidence against one deployed
commercial CDN, but they do not yet justify a general claim across regions or CDN
providers. The next confirmatory analysis should preselect a decision rule on an
earlier trial and evaluate it unchanged on later trials, add more independent
days/exits, and repeat the byte-identical design on another commercial CDN.

## Inputs and Reproduction

- `vpn-pilot-20260827-0035/`: first three-location trial.
- `vpn-pilot-20260827-r2/`: valid Frankfurt/Singapore trial and rejected Los
  Angeles warm-up.
- `vpn-pilot-20260827-r3/`: third three-location trial, including Los Angeles
  treatment contamination.
- `vpn-pilot-20260827-r2-la-replacement/`: clean Los Angeles replacement.
- `../analyze_vpn_runs.py`: run-aware, within-colo primary analysis.

Reproduce the run-aware estimates with:

```bash
python3 experiments/cloudflare_cache/analyze_vpn_runs.py \
  experiments/cloudflare_cache/results/vpn-pilot-20260827-0035/*.measure.jsonl \
  experiments/cloudflare_cache/results/vpn-pilot-20260827-r2/*.measure.jsonl \
  experiments/cloudflare_cache/results/vpn-pilot-20260827-r3/*.measure.jsonl \
  experiments/cloudflare_cache/results/vpn-pilot-20260827-r2-la-replacement/*.measure.jsonl
```
