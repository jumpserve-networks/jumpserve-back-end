# Nine unresolved IPv6 claim follow-ups — 2026-10-07

The nine requested follow-ups were recorded in study `76c70fd9-0217-464e-8c26-0a47832409a3`. Five runs contain partial evidence; four substantive experiments were excluded with exact prerequisites and reasons. They are archived-data checks and source audits, with no new network measurements. All 20 previous queue jobs and all original assessments remain retained. No study publication or application deployment was performed.

Study workspace: https://jumpserve.quaint-lab.org/module/research-verification/workspace/76c70fd9-0217-464e-8c26-0a47832409a3

Reviewed paper: [PuRe item3670144_1/file3670145](https://pure.mpg.de/pubman/item/item_3670144_1/component/file_3670145/main.pdf),22 pages, DOI 10.1145/3730567.3764439. Original PDF SHA256: `f6378fb3de19aa8d3204f4d3a2028f95497f334862a5298aa6678ef1b816290b`.

## Findings and coverage

All 1,536 difference cells in Figures 13–15c/d and 16a/b match under the frozen primary criterion of 0.0051 percentage points. The largest absolute difference is0.004996056594pp. This reproduces arithmetic under published aggregation, not every time-series point or upstream packet parser.

The matched-day sensitivity changes 18 cells. One cell, Figure 13d row 13, column 4 (`fig13d-12-3`), crosses the printing-agreement criterion: published−0.01pp; difference of available-day means−0.005907273279pp; matched-day mean+0.001349018415pp. May 30, 2025 packet counts are absent for the DNSSEC/LGI/IPv6/MTU1500/EDNS1232 configuration. The primary comparison uses 20 versus 21 available days, while the sensitivity uses the exact 20-day intersection. This demonstrates an aggregation/coverage mechanism on the retained data; it does not establish a network causal effect or which released revision generated the paper.

Eight route summaries on two preselected sentinel dates contain 39,049 recorded traceroute counts. Their four class totals and all mapped-path multiplicities reconcile. These repeated counts are not 39,049 independent replications. No full-day census, raw Atlas/probe replay, independent IP-to-AS mapping or route-to-every-DNS-query join was completed.

Six fragment-loss quotations match [RFC7872 Tables 1–2](https://www.rfc-editor.org/rfc/rfc7872.txt). Of four percentages recomputed from the paper’s published integer counts, one exceeds the printing criterion:105/315×100=33.333333…%, rounding33.33%, versus33.34% in physical PDF page 13, Section 7.4. This is a small source arithmetic discrepancy; it does not resolve the unmatched historical TCP/ICMP comparison.

| Claim | Run | Current label | Remaining requirement |
|---|---|---|---|
| Fragment-drop causal effect | Partial | Inconclusive | Route-equal policy intervention and matched packet/query/population identities |
| DNSSEC/IPv6 association | Partial | Inconclusive | Independently assigned cohorts, NS-set/provider identities and weighting |
| Global DNS readiness | Excluded | Inconclusive | Parse historical domain/NS-set mappings and sampling weights under a larger protocol |
| Historical RFC comparison | Partial | Inconclusive | Matched historical targets, transports, epochs and raw traces |
| IPv6 operational recommendation | Excluded | Inconclusive | Multi-operator/resolver operational and security outcomes |
| APNIC global fragment loss | Excluded | Untested | Original APNIC ad observations, weights, targets and policies |
| Packet-size mean/SD | Excluded | Untested | Exact May1 PCAPs, parser revision and capture/length controls |
| RIPE Atlas route coverage | Partial | Inconclusive | Full raw probe/trace coverage, mapping versions and query joins |
| Every relative cell/plotted observation | Partial | Inconclusive | Remaining plotted observations in Figures 3–12 and17–21 |

Current claim totals:3 reproduced, 3 discrepant, 7 inconclusive, 2 untested. Two previously untested claims now have relevant partial checks and become inconclusive; no broad claim became fully resolved. Reproduced means the declared criterion passed under its recorded conditions; discrepant means examined evidence conflicts with a stated value or criterion; inconclusive means relevant checks cannot resolve the whole scope; untested means no adequate empirical check was completed. An availability audit does not change an untested claim into a reproduced result.

The four excluded runs appear as “Failed” jobs because the current queue maps excluded runs to that status. Their reasons explicitly say they are scientific exclusions, not software test failures. Five partial runs await review. All original jobs remain unchanged, including the earlier manual planning tasks; the nine follow-ups link to them through `followup_of`.

## Reproduction and provenance

Nine version 1 protocols were frozen locally before main execution and saved unchanged in `registration.json`. Inputs: 339 original files, 48,470,849 original bytes, 8,243,085 compressed container bytes. Container SHA256: `5d1ba531426b3d9eb3267b342ad1479e16941efd4146b401d9059ff28b29b26b`. Main execution:2026-10-07T23:02:17.517105+00:00 through2026-10-07T23:02:22.306642+00:00; measured shared wall time4.789368s. Exact implementation SHA256: `a979cf7c7dbfa7d251346bab4f414c196fcefb4ab48964b35045f4a50afb6192` in `analysis-main-v1.py`. The nine scoped records share this execution and inputs; they are not independent replications.

The initial network-sandbox failures, Python default-CA failure, and directory-report save failure are preserved separately from main results. System-CA verification enabled subsequent retrieval; no TLS verification was disabled. Seven recovered directory originals retain hashes, while lost retrieval timestamps/response metadata remain null. Three unused import serializations remain retained; later serialization removes repeated boilerplate and normalizes 256 full comparison configurations to fit the existing 4 MB interactive export bound. No frozen protocol or original numerical result changed.

From the backend repository, using Python 3.13 and `pypdf==6.19.0`:

```sh
experiments/http2_compliance/.venv/bin/python -B experiments/ipv6_dns/followup.py verify-local
```

This re-executes the retained exact implementation in a fresh append-only verification directory and compares all numerical outputs, identities and exclusions. If that directory already exists, use a fresh copy of this study directory; preserve earlier verification results. A fresh campaign follows `prepare-inputs`, `freeze`, then `run` in a fresh workspace with all retained original inputs. Saved registrations/results reject overwriting.

The 40-digit Decimal control agrees with all 1,536 float contrasts within 7.84×10⁻¹⁵pp. Recorded zero, missing numerator, zero denominator, bounded percentages and the potentially unbounded TCP/UDP ratio also pass their controls. These controls share original counts and axis mapping and do not validate upstream packet processing. Dates, paths, servers and changing NS sets overlap; no independent sample or inferential confidence interval is justified. Min/max and sensitivities are descriptive.

`comparisons.csv` exports 1,546 primary numerical/source checks with separate published and computed fields, full comparison identities, valid-day counts and paired sensitivities. Empty CSV fields are missing/inapplicable, not recorded zero. JSON retains nulls and reasons. `source-inventory.json`, `input-manifest.json`, the original retrieval reports and the two appended route-source records identify versions, hashes, access/review scope and limitations. Complete review requires substantive examination of an entire source including all figures, tables, appendices and supplements; new source reviews are partial. No human or independent review is claimed.

## Persistence and verification

Verified targets: AWS 395567831870 and Supabase `regphejnlvfpyokpniny`. 3,462 scientific records were appended: 9 frozen protocols/campaigns/checks/runs/assessments/gaps, 265 configurations, 1,546 separate published values, 1,586 measurements, 9 summaries and 2 source-review records. Ten initial private artifacts total 19,558,819 stored payload bytes. Every stored field and original artifact hash was checked after retrieval, including all 339 expanded original members. Supplementary reports, a paired-day summary and two route-source records are saved separately with their own storage receipt.

The retained-implementation/Decimal scientific controls and server-side record/hash checks pass. A legitimate authenticated browser request was not exercised in this turn; the study was updated by an authorized trusted operator using existing ownership, not a fabricated Google session. No frontend/backend application code or schema was deployed. Earlier release checks do not constitute a new browser check. Scientific exclusions and the source arithmetic discrepancy are findings, not failing software tests.

No experiment hosts or model-evaluation calls were requested. Local CPU/memory, database/storage/transfer, hosting and Codex usage costs are unallocated; measured and estimated charges remain missing, not zero. Only logical stored payload bytes and execution wall times are reported; charges, database/index overhead and peak memory were not reconciled.
