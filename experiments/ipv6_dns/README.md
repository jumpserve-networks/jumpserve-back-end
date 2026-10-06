# IPv6 DNS assessment v1

This is a limited assessment of Tobias Fiebig and Anja Feldmann, “‘How I learned to stop worrying and love IPv6’: Measuring the Internet’s Readiness for DNS over IPv6”, IMC 2025, DOI 10.1145/3730567.3764439. Exact requested version: PuRe item_3670144_1 / file_3670145, 22 pages, original SHA256 `f6378fb3de19aa8d3204f4d3a2028f95497f334862a5298aa6678ef1b816290b`.

All 1,152 absolute IPv4/IPv6 cells of Figures 13a/b, 14a/b and 15a/b match within the predeclared 0.0051 percentage-point rounding tolerance. Released folding and independent arithmetic agree on the same archived inputs. This is numerical agreement under recorded conditions, not universal readiness, causal identification, standards compliance or complete validation.

The inventory records 72 direct references, the main paper and one associated artifact. Three small standards received complete AI review; seven records received partial review; 48 retrieved records remain substantively unreviewed; 16 full texts were unavailable through recorded lawful attempts. All 22 main-paper pages were visually inspected, but dense relative cells, all per-point series and packet-size distributions were not fully examined. Retrieval/hash verification is not review completeness. No human or independent scientific review is claimed.

## Scope and results

The frozen archive census covers 158 calendar dates, 144 configuration/cohort combinations and six metrics. The three printed windows contain 147 days: 145 available, two unavailable (April 21 and August 26), and 11 dates outside those windows. A July 31 packet configuration is also absent. All 136,512 metric slots retain recorded, missing or excluded status; missing is never recorded zero.

Outcomes use NS-set denominators. Packet metrics use UDP server identities; TCP fallback is a TCP/UDP ratio. Day means, pooled denominator sensitivity, routing-anomaly exclusions and matched IPv6-minus-IPv4 percentage-point contrasts are separately recorded. Daily min/max is descriptive, not a confidence interval: servers, paths, NS sets and days overlap in a changing convenience panel from one AS.

The RQ3 fragmentation summary conflicts with its Section 4.4 direction. RFC 3901 recommends at least one IPv4-reachable authoritative server, not the paper’s two. Four finite controls demonstrate distinct-answer loss, status conflation, maximum-as-mean and failure on empty durations. `corrected.py` is a separate specification for these fixtures, not a validated replacement pipeline. The published generating revision, defect prevalence and impact remain unestablished.

No new network measurements, simulations, APNIC raw-data reanalysis, full raw-pcap replay, historical BGP intervention or operational causal validation was conducted. The paper reports about 55GB compressed packet data per daily run per path; a full replay exceeds this campaign’s 150MB summary-input budget. Seeds, orbital inputs and stochastic replication are inapplicable to deterministic summary arithmetic. Historical random domain selections cannot be regenerated without original selections.

## Reproduction

Use Python 3.13 with `pypdf==6.19.0` and `pypdfium2==5.14.0` (recorded environment versions take precedence in the validation report). The actual reading environment is `../http2_compliance/.venv`; create an equivalent environment for an independent rerun. Standard-library scripts perform retrieval, controls and arithmetic. Original paper and author tarball are retained privately; the public data archive excludes copyrighted full reference texts.

```sh
python3 bootstrap.py
python3 retrieve.py --output-version 2
../http2_compliance/.venv/bin/python analyze.py
python3 controls.py
```

For a fresh numerical rerun, unpack `data.zip` into a new directory, copy `implementation/*.py` to that directory, and retain its `protocols/` directory. Create a Python environment with the pinned dependencies and substitute that interpreter for the example `../http2_compliance/.venv/bin/python`. The commands above bootstrap hash-verified originals, retrieve version 2 inputs, run independent/author folding, and execute the four controls. Do not copy accepted generated results into this fresh directory. Review the new `evidence/reanalysis-v1.json` and `controls-v1.json` against the downloaded published/comparison files. Retrieval timestamps differ; compare observation identities and bytes, not entire report hashes.

`assessment.py`, `export.py` and `verify.py` package and check the accepted study workspace, including manually examined source records and original retrieval reports. They are not an automatic substitute for literature review and require those retained evidence inputs. The supplemental `paper-context.json` records Table 1, prose numerical values and coverage for all 21 figures separately from the 1,152 reproduced cells.

Run campaigns in a fresh copy with original inputs and locked protocols. Existing immutable result files intentionally reject overwriting; a rerun requires a new result version. `prepare.py` records original paper identity, seeds, author member hashes and initial protocol locks; do not run it to replace accepted locks. The original failed sandbox retrieval and subsequent network-access amendment remain preserved. `sources.py`, `sources.py --followup` and `sources.py --known-fallback` record bounded direct-reference access attempts; they do not recursively read bibliographies or establish full review.

Archive originals: https://data.measurement.network/dns-mtu-msmt/ . Requested paper: https://pure.mpg.de/pubman/item/item_3670144_1/component/file_3670145/main.pdf . `evidence/assessment-v1.json` links parsed measurements to original source hashes, configurations, attempts, dates and transformation hashes. `evidence/assessment-v2.json` preserves the numerical campaign and amends two source identities; wrong PDFs and v1 remain retained. Private chunked storage verifies every chunk and reassembled original after retrieval. Storage, hosting, failed-call charges and orchestration allocations are unknown unless separately recorded; unknown is not zero.

## Software and release evidence

JumpServe infrastructure owns versioned SQL migrations, guarded import/storage tools, module-specific evaluated prompts, backend read-only tools and release reports under `jumpserve-infra/docs/ipv6-study-validation`. Public results, methods, literature and data are under `/module/ipv6-dns-study`. Conversations are scoped by authenticated user and module; result links prepare editable questions without sending them.

Scientific claim labels: reproduced = agreement for the stated check and conditions; discrepant = conflict with directly examined text or declared comparison; inconclusive = relevant evidence cannot establish the claim; untested = no suitable executed check. Incomplete literature, packet, client-side and causal coverage are scientific limits, not software test failures. Actual Google-authenticated chat verification is conditional on an available legitimate session; mocked identity tests do not substitute for it.
