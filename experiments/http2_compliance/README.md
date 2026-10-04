# HTTP/2 compliance evidence assessment

Paper: [The Developer, the RFC, and the Middlebox: An HTTP/2 Compliance Story](https://doi.org/10.1145/3730567.3764447), IMC 2025, ACM version of record, 16 pages (258–273). The user supplied the full paper after ACM and KAUST denied automated retrieval. Original PDF SHA-256: `0624b02908d973efd875b908995107a9fee78cbf4320a31dcd14b15e33c5fa35`.

This is an archived measurement reanalysis plus a separate owned-loopback endpoint follow-up. There is no satellite simulator, orbital epoch, capacity maximum, or simulator verification in this paper. The study's reproduction coverage remains partial. It does not independently reproduce the historical proxy/cloud fleet.

## Evidence and results

- `protocol-v2.json` and `protocol-lock-v2.json` freeze the main questions, case universe, comparison criteria, versions and original input hashes before execution. The failed v1 and its implementation are retained.
- `published-values.json` contains independently transcribed paper values, kept separate from measurements.
- `literature.json` inventories the main paper and all 48 directly cited sources, including version, original-byte hash, download audit, review status, findings and limitations. No recursive bibliography expansion. Complete review: 3 entries, including main. Partial: 29. Unreviewed: 8. Unavailable: 9. Six unavailable research sources are references 1, 5, 7, 20, 36 and 37; unavailable web sources are 13, 39 and 40. Downloaded does not imply reviewed. Extracted texts are working copies; hashes identify originals.
- `review-notes.json` records the actual scope of supporting-source reviews. The main paper's text, Figures 1–8, Tables 1–9 and Appendices A–B were examined, including visual review of all pages. Figure 4's individual raster cells were not all independently checked.
- `verify_sources.py` independently checks all 40 retained original-file hashes; nine sources remain unavailable. `evidence/source-hash-validation.json` records each checked path and the review counts. Matching bytes do not establish a complete literature review.
- `evidence/analysis-summary.json`, `evidence/validation.json` and `evidence/assessment-v3.json` provide arithmetic checks and discrepancy evidence. Figures 7–8 checks cover transcribed numerical statements rather than every raster bar.
- `protocol-discrepancy-v1.json` preserves the separately declared exploratory plotting diagnosis. `assemble.py` leaves original author code and main analysis untouched.
- Loopback pilot v1 failed before measurement. Pilot v2 (12 observations) and main v1 (72 observations) have separate protocols, locks and `evidence/loopback-*.json` traces. Main uses 3 process restarts × 12 cases × two observation windows (0.25 and 1 second). Restarts on one host are correlated; no inferential confidence intervals.
- `evidence/failures.json` retains three failed execution attempts. There were no silently excluded main datasets. Missing original execution dates, resource counts and unobserved values remain null.

All 15 Table 5 rows reproduce exactly under the released author's classifier: 1,745 rejected / 1,950 cases, 205 accepted, including 856 silent drops. Rejection is not an RFC compliance rate. Rounded Figures 5–6 reproduce within 0.05 percentage points. The full archive contains 57 datasets and 7,176 case observations; preserving ambiguous worker states retains 443 unknown outcomes, including 129 in the Table 5 subset, all from Mitmproxy 11.1.0.

Figure 8's released bar generator assigns accepted H2EE outcomes to its dropped count. A controlled accepted-to-accepted fixture produces a spurious +1 change instead of 0. Correctly matched 78-client-case acceptance changes are Envoy +6, HAproxy −1, Nghttpx +1, H2O +1, versus published +8, +5, +5, +2. This diagnoses released code, without proving which version generated the camera-ready figure. For Figure 7, current raw Traefik drops increase by 90, versus the published/stored increase of 12; stale output or configuration drift remain hypotheses.

RFC 9113 §4.1 specifies a 9-octet (72-bit) frame header; paper §2.1 says 12 bytes, while Table 1 depicts 72 bits. Our independent raw-frame encoder/parser and hyperframe check use 9 octets. Main loopback controls yield 42 expected-code agreements and 30 mismatches: five malformed cases return code 2 instead of the planned code 1 at both windows. All positive controls pass. These are Node 24.4.1 endpoint observations on macOS arm64, without TLS; they are not historical Node 20.16.0 or proxy measurements.

## Reproduction commands

Run from the backend repository. Preserve an existing study directory before re-execution: scripts create dated run metadata and overwrite their local output filenames. The published Supabase campaign is not changed by these commands. For a new campaign, copy the experiment directory, assign new protocol/run IDs and freeze new locks. Never amend a frozen implementation to pass a hash check.

```sh
python3 -m venv experiments/http2_compliance/.venv
experiments/http2_compliance/.venv/bin/python -m pip install -r experiments/http2_compliance/requirements.txt
git clone https://github.com/attia-mahmoud/HTTP2-Compliance-Tests experiments/http2_compliance/sources/HTTP2-Compliance-Tests
git -C experiments/http2_compliance/sources/HTTP2-Compliance-Tests checkout 03bcd5d88c98cb82fac0c2afb626bb16ec2fa402
cp /path/to/legally-obtained/3730567.3764447.pdf experiments/http2_compliance/sources/main-paper.pdf
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/analyze_v2.py
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/check.py
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/verify_sources.py
```

Supporting artifact revisions audited, without executing remote workers:

| Repository under `https://github.com/attia-mahmoud/` | Commit |
| --- | --- |
| nopasaran-tests | 2468281e2e08fd4e661522f929a184f5627f0567 |
| HTTP2-Tests | e409a3fb65cd28dbe2ccf886dad8455fcfb48e32 |
| nopasaran-endpoint | e24bcf687b639d329730b702986ce09d3adc1ddf |

Source retrieval is optional and can encounter changed URLs or access controls. The committed inventory preserves this study's versions and reviews; obtain a copy before refreshing it. `retrieve.py`, `retrieve_web.py` and `finalize_sources.py` record retrieval attempts and reconcile original hashes with explicit review notes. Do not bypass publisher access controls or treat newly downloaded versions as previously reviewed.

```sh
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/retrieve.py
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/retrieve_web.py
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/finalize_sources.py
```

Owned loopback reproduction requires the exact recorded Node executable hash and a permitted local listener. A different Node build needs a new versioned protocol and lock. It performs no external network tests. Preserve pilot/main outputs before repeating.

```sh
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/loopback_v2.py --pilot
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/loopback_v2.py
experiments/http2_compliance/.venv/bin/python -B experiments/http2_compliance/assemble.py
```

`results/relational.json` is the main output; `results/assessment-v3.json` assembles source records and separately labeled follow-ups. Original author payloads remain private in Supabase and in the pinned artifact checkout. Local source files and large generated outputs are deliberately ignored by git. Original study bundle SHA-256: `17eccccb6b1f10f91aa5596cad6591e63b689ad8e22a62eb2286c32a51c04135`; new executions have different timestamps and therefore different bundle hashes even when numerical results agree.

## Persistence and release

Use `jumpserve-infra/bin/http2-study-database.py` to import the assembled bundle. It verifies project `regphejnlvfpyokpniny` and frozen main provenance before mutation. Migrations `202610040003`–`008` create relational research storage, guarded prompt publication, unsigned 32-bit error-code storage, authenticated answer provenance, required manual answer review and per-answer model usage/cost estimates. The original import stopped on an archived error code above signed integer range; migration 005 widened storage without clipping or discarding it, then the deterministic importer resumed. This was a persistence failure, not a new experimental observation.

Ten research relations are public SELECT-only with RLS. The eleventh, author raw/evaluation artifacts, is backend-only. Browser writes to research relations are denied. Prompt drafts, active selection, evaluated publication and chat answers reuse existing prompt/session tables. Chat is authenticated, user/module scoped, and restricted to this study's read-only tools. Failed evaluations are retained separately; prompts publish only after all eight required evaluations and a recorded review of their actual answers pass. An earlier automatic-only passing prompt was disabled when manual review found a factual error, before this module's backend was deployed.

Local experiments bought no compute and provisioned no AWS instances. Workstation allocation cost and Codex orchestration costs are unavailable. Live prompt evaluations record input/output tokens and estimated Bedrock cost using published list pricing; estimates are not reconciled billing. Public campaign costs include failed and successful evaluations. Existing hosting and build charges remain unallocated.

The public module provides results, methods, claim coverage, literature and JSON/CSV exports at `/module/http2-compliance-study`. Exact claim statuses and limitations are exported in `claims`; simulation, empirical archives and fresh endpoint controls are clearly distinguished. Release verification and any Google-session gap are recorded in the infrastructure release report.
