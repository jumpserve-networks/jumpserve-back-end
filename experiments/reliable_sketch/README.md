ReliableSketch bounded assessment, version1
=========================================

This study is incomplete scientific validation. It separately records published values, author-code CPU executions on generated inputs, independent finite correctness controls, and a fixed-allocated-memory follow-up. Original trace curves, full theorem verification, FPGA/Tofino and longitudinal operational claims remain untested or inconclusive. All review and secondary inspection were AI work by Codex; there is no independent human review.

Main PDF: https://yangtonghome.github.io/uploads/ReliableSketch__IMC_2025.pdf (IMC2025, DOI10.1145/3730567.3764459,18pages), SHA256 ee674e8153757670ee527c5f3822e533c797af1dd724023dd016be229ad18eec. Artifact https://github.com/ReliableSketch/ReliableSketch at 5a83f03c775142401d23a78e7e81b163ddf7b604 (2024-01-17). Released revision identity does not establish publication-generating identity.

Run in a fresh study directory with these scripts, frozen protocols, implementation manifest and source inventory. Python3.13.3 and Apple clang21.0.0/O2 were used; author objects have different sizes on other ABIs. Exact retained inputs/hashes are authoritative. Research retrieval dependencies: pypdf6.19.0 and pypdfium2; experiments/analysis use Python standard library and C++11 compiler. Consult retained environment.json for actual installed versions rather than treating this prose as a dependency lock.

```sh
mkdir -p sources build evidence protocols implementation
git clone https://github.com/ReliableSketch/ReliableSketch.git sources/author
git -C sources/author checkout 5a83f03c775142401d23a78e7e81b163ddf7b604
python3 -B prepare.py --build-only
python3 -B check_controls.py --stage pilot
python3 -B check_controls.py --stage main
python3 -B run.py --stage main
python3 -B run.py --stage followup
python3 -B assemble.py
```

The downloaded reproduction archive retains original results under `recorded/`. Keep `evidence/implementation-manifest-v1.json` and protocols in place; create fresh output directories for reruns. Scripts refuse to overwrite an original protocol or result. Timestamp/platform and timing differences are expected. A new compiler/ABI needs an amended protocol, preserving the old one. Source retrieval is optional to executing retained-input CPU runs; `retrieve.py` performs direct bibliography retrieval, keeps every attempt and rejected identities, and never recursively expands references. `review_v1.py` records this session's actual inspection notes; it is not an automated review or a command to re-run to claim a new review.

The grid has108configurations and1080successful runs; no software-failed or excluded CPU runs occurred. Outliers, lost mass and interval violations are scientific findings, never silently excluded. Weighted controls preserve6pilot and30main calls, including scientific discrepancies. Run identity = campaign/configuration/seed; generated stream identities and exact keys are matched across algorithms. The three support-restricted streams each have250000positive unit updates; Zipf exponents0.3/1.2/3, support1..4096. Threshold25counts, budgets8/32/128KiB, RS/RS_raw/CM3/CM16/CU3/CU16. No additional rare-event trials were run after results appeared.

A census of distinct observed keys plus three absent probes supplies accuracy and interval metrics. AAE and ARE exclude zero-truth probes, but outliers include them. Timings repeat the input keys, with checksum retained. Repetitions are whole seed runs on fixed streams; ranges are descriptive min/max, not inferential intervals. Independent aggregate checks share input bytes and emitted estimates; finite bucket enumeration shares no author sketch implementation. Neither checks uniform independence of Murmur hashes or an unlimited universe. Exact fallback is absent, as in the empirical artifact.

Findings include nominal128KiB RS allocating587152counter bytes, key1/key2 weight5 estimates1/2 corrected to5 in a separate implementation, a printed weighted-remainder arithmetic issue, and a counterexample to unconditional multiplication of marginal success probabilities. These do not disprove the formal theorem under stronger assumptions, identify the published revision, or validate arbitrary weighted/deletion streams. See all16claims and their evidence/limits in assessment-v1.json. Labels: reproduced = declared finite check agrees; discrepant = demonstrated recorded mismatch; inconclusive = incomplete decisive evidence; untested = no equivalent-condition experiment.

Artifacts: protocols/*.json and locks; literature.json (main+45direct+supplement; exact byte hashes/attempts/access/review); published-values.json; evidence/implementation-manifest-v1.json; evidence/reliable-*/raw (original stdout/stderr); evidence/inputs-v1; controls; validation-v1.json; assessment-v1.json. Original publications and author code are preserved privately in Supabase Storage, with every member rehashed after download. Public downloads contain our generated CPU data, own adapters, patch and protocols, not redistributed full publications or author source trees.

Release blockers: independent raw hash/identity/aggregate coverage, full planned-run accounting, RLS/browser-write/private-storage checks, backend authorization/module isolation/missing-data/renderer bounds, declared actual-answer evaluations and AI secondary inspection, frontend lint/tests/build, completed and missing/invalid states in desktop/mobile, exports/navigation/themes/return links, reviewed agent-stack diff and matching published prompt/code. Conditional: actual Google-authenticated browser-to-model chat if legitimate session available; original data/hardware/full proof/full literature review. These gaps must remain visible and cannot be described as verified. Production deployment is authorized only after blockers pass.

Compute: one local process, no AWS experimental instances. Main12.710s and follow-up13.069s campaign wall time. Local power/host allocation, Codex usage, existing hosting/build charges and reconciled storage/model bills are unavailable, not zero. Chat reports retain returned token usage and list-price estimates; failures may lack usage. Storage manifest retains actual bytes and verified hashes.
