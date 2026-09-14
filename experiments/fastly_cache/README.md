# Fastly Cache-Demand Pilot

This experiment applies the controlled hot/cold design to a Fastly VCL service.
It uses public HTTP response metadata and curl timing from external Mullvad exits.

## Deployed target

- Fastly service: `jumpserve-fastly-cache-study-20260827-01`
- Service ID: `i9hnKYTkxIksRHg8fK0dx7`
- Active version: 1
- Client test route:
  `http://jumpserve-cache-study-20260827-01.global.ssl.fastly.net.global.prod.fastly.net`
- Origin: the existing controlled Cloudflare Worker object generator
- Object response: 256 KiB, `application/octet-stream`, `Cache-Control: public,
  max-age=3600`
- Fastly shielding: off

Fastly's pre-DNS test suffix is appended to the configured classic test domain.
This currently yields an HTTP route. Do not pool these timings with the existing
HTTPS Cloudflare trials.

The Cloudflare origin's `CF-Cache-Status` header passes through Fastly and may be
stored in Fastly's cached response. It is not a Fastly label. Use Fastly's public
`X-Cache` as the diagnostic Fastly cache-state label and `X-Served-By` as the
observed POP source.

## Run the VPN pilot

Start with Mullvad disconnected. The runner uses fresh keys per requested exit,
warms only the hot objects, makes one shuffled measurement per hot/cold object,
disconnects after each exit, and restores the US relay preference.

```bash
python3 experiments/fastly_cache/run_vpn_pilot.py \
  --output-dir experiments/fastly_cache/results/vpn-pilot-UNIQUE

python3 experiments/fastly_cache/analyze_vpn_pilot.py \
  experiments/fastly_cache/results/vpn-pilot-UNIQUE/*.measure.jsonl
```

Use `--location LABEL:COUNTRY:CITY` to select replacement Mullvad exits. Analyze
only within the POP extracted from `X-Served-By`, not the requested VPN city.

## Interpretation

Demand assignment is the primary label. `X-Cache` is a diagnostic mechanism label
and must not enter portable-metadata or timing-only feature sets. TCP-connect RTT
is a control; request-to-first-byte is the primary timing candidate.

The first run is a feasibility pilot, not confirmatory evidence for a fully
independent second CDN. Its origin is on Cloudflare, the client-facing route is
HTTP, and it has only one run with 10 objects per class at each exit. Before using
Fastly as cross-CDN confirmation, move the byte-identical object generator to a
provider-neutral origin, provision a valid TLS hostname, and collect at least
three fresh-key runs per external location on different days.
