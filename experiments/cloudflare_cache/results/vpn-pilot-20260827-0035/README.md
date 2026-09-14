# Mullvad VPN Pilot: 2026-08-27

This pilot tested whether client-visible timing could distinguish controlled hot
and cold Cloudflare objects from ordinary external VPN exits.

## Design

- Mullvad exits: Los Angeles, Frankfurt, and Singapore.
- Fresh object keys were generated independently for every VPN location.
- Each location used 20 hot and 20 cold 256 KiB objects.
- Only hot objects were warmed, with five requests per object.
- The measurement phase issued one shuffled GET per object.
- `CF-Cache-Status` supplied the diagnostic cache-state label; it was not used as
  a timing feature.
- Results were grouped by the Cloudflare colo in `CF-Ray`, not by the requested
  VPN city.

All 300 warm-up requests and all 120 measurement requests succeeded. In the
measurement phase, all 60 hot objects were `HIT`s and all 60 cold objects were
`MISS`es.

## Timing Results

Lower-is-positive AUC is the probability that a randomly chosen `HIT` has a lower
timing value than a randomly chosen `MISS` in the same vantage and observed colo.

| VPN vantage | Observed colo | HIT | MISS | TCP RTT AUC | Request TTFB AUC | TTFB minus RTT AUC |
|---|---:|---:|---:|---:|---:|---:|
| Frankfurt | FRA | 20 | 20 | 0.335 | 0.688 | 0.757 |
| Singapore | SIN | 20 | 20 | 0.615 | 0.757 | 0.600 |
| Los Angeles | LAX | 6 | 8 | 0.188 | 0.875 | 0.875 |
| Los Angeles | SJC | 14 | 12 | 0.494 | 0.899 | 0.756 |

The unweighted macro-average AUC across the four observed colo groups was 0.408
for TCP-connect RTT, 0.805 for request-to-first-byte, and 0.747 for
request-to-first-byte minus the RTT estimate. This supports treating RTT as a
control rather than a cache-presence indicator.

## Interpretation and Limitations

- This is a successful external-observer pilot, not a confirmatory result. It has
  one run per requested VPN location and no confidence intervals.
- The Los Angeles relay alternated between Cloudflare LAX and SJC during one
  connection. This confirms that the VPN city cannot be used as a substitute for
  the observed serving colo and leaves small samples in those two groups.
- Absolute timing includes the tunnel from the experiment host to the VPN exit.
  Only within-vantage, within-colo comparisons are meaningful.
- The experiment infers controlled recent request history/cache residency. It
  does not establish inference of organic demand or exact request volume.
- `CF-Cache-Status` makes treatment integrity directly observable. A portable
  attack evaluation must exclude that header from its feature set.
- At least three independent fresh-key runs per external location are needed
  before estimating confidence intervals or reporting a confirmatory result.

The raw manifests, warm-up JSONL, one-shot measurement JSONL, Mullvad connected
status, and Cloudflare trace are stored in this directory. The status and trace
files contain public VPN exit addresses but no Mullvad account number.
