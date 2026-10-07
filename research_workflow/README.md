# Research verification workflow

Shared evidence records and bounded execution for `/module/research-verification`.
The framework supports domain-specific studies; it does not automatically verify
every paper. Scientific findings require appropriate checks and declared review.

## Intake to publication

1. Record the exact paper URL, title, domain and scope in **Assess a Paper**. Google
   authentication creates a private owner-scoped workspace. Intake alone does not
   retrieve a paper or extract claims.
2. Retrieve original legally accessible resources with the operator CLI. Inventory
   the main paper, supplements, artifacts and direct references without recursively
   expanding bibliographies. Record attempts, exact versions, original-byte hashes,
   access and substantive review separately. Text extraction remains unreviewed.
3. Add atomic sourced claims with locations, conditions, metrics, units and priority.
   Keep numerical, correctness, association, causal, generalization, operational,
   theory and standards questions separate. Mark inapplicable checks with reasons.
4. Plan a pilot, main campaign or follow-up and map it to claims. Freeze the complete
   protocol before execution; disclose previous data inspection. Amend with a new
   version and rationale, retaining all earlier records. Configurations include
   dates/epochs, complete algorithm parameters, author revision, corrected revision
   when applicable, inputs, budgets and seeds or explicit inapplicability.
5. Execute a registered adapter, or import documented domain-specific external
   campaigns. Preserve failed/excluded runs, raw input/output and transformation
   provenance. The backend verifies private Storage bytes after retrieval.
6. Review evidence before assigning a claim finding. The numerical runner never
   upgrades a claim automatically. For every unresolved claim, record the exact gap,
   next check, inputs/dependencies, feasibility, cost basis, decision and stop rule.
7. Record genuine scientific-scope and software reviews, including human/AI identity
   and independence. Publish a fixed reviewed snapshot. New evidence requires new
   reviews. Public exports omit original private artifacts, audit actors and owners.

The first adapter, `matched-numeric-v1`, performs **reanalysis or independent
arithmetic checks**, with exact observation/configuration/metric identities,
matching units, declared absolute tolerances and optional bounds. It cannot be
labelled a new empirical or causal campaign. Published values remain separate.
JSON decimals that cannot be preserved by its number representation are invalid,
not rounded to zero. Use a separately declared adapter for arbitrary decimal input.
Descriptive min/max differences are not confidence intervals. Repeated observations
are not assumed independent; this adapter performs no inferential resampling.

Authors' implementations, independent invariants, interventions, new environments,
physical operation and proof/standards inspection require appropriate domain work.
External import checks schemas and bytes; it does not establish that the provided
revision actually generated published results. Controlled discrepancy investigations
and broader conclusions need separate campaigns and evidence.

## Local reproduction

Run from `jumpserve-back-end`. Commands write new files and refuse replacement.
The example is a synthetic software regression, never evidence for the IPv6 paper.

```sh
python3 -B -m unittest discover -s research_workflow/tests -v
python3 -B research_workflow/cli.py template \
  --input research_workflow/examples/matched-numeric-v1/input.json \
  --configurations research_workflow/examples/matched-numeric-v1/configurations.json \
  --metric latency --units ms --tolerance 0.1 \
  --output .test-artifacts/research-workflow/example-protocol-v1.json
```

Inspect every field in that template. Replace `prior_exposure` with the actual
development-data exposure and revise the question, dates, controls, resource/cost
basis and coverage to match the campaign. Then freeze and execute:

```sh
python3 -B research_workflow/cli.py freeze \
  --document .test-artifacts/research-workflow/example-protocol-v1.json \
  --study-id 8bfc65a1-61c9-49ae-90c9-7dd97165bd00 \
  --request-id 8bfc65a1-61c9-49ae-90c9-7dd97165bd02 \
  --output .test-artifacts/research-workflow/example-frozen-v1.json
python3 -B research_workflow/cli.py run \
  --input research_workflow/examples/matched-numeric-v1/input.json \
  --protocol .test-artifacts/research-workflow/example-frozen-v1.json \
  --campaign research_workflow/examples/matched-numeric-v1/campaign.json \
  --study-id 8bfc65a1-61c9-49ae-90c9-7dd97165bd00 \
  --request-id 8bfc65a1-61c9-49ae-90c9-7dd97165bd03 \
  --output .test-artifacts/research-workflow/example-results-v1.json
```

Expected: partial terminal run, one agreeing zero, one discrepant difference of
`0.2 ms`, one missing observation, planned=3/recorded=2/missing=1. The discrepancy
is a finding, not a software failure. Failed runs return nonzero after saving their
output. New conditions need new IDs/files; remote retries return existing runs.

## Sources and page-referenced text

Use a dedicated Python environment and install `requirements-cli.txt` for PDF
extraction. Preserve source files and audit JSON in the chosen output directory.

```sh
python3 -B research_workflow/cli.py retrieve \
  --url https://pure.mpg.de/pubman/item/item_3670144_1/component/file_3670145/main.pdf \
  --output-directory .test-artifacts/research-workflow/paper-retrieval-v1
python3 -B research_workflow/cli.py text-packet \
  --file .test-artifacts/research-workflow/paper-retrieval-v1/original.bin \
  --output .test-artifacts/research-workflow/paper-text-v1.json
python3 -B research_workflow/cli.py source \
  --file .test-artifacts/research-workflow/paper-retrieval-v1/original.bin \
  --retrieval-record .test-artifacts/research-workflow/paper-retrieval-v1/retrieval.json \
  --citation 'Fiebig and Feldmann, How I learned to stop worrying and love IPv6, IMC 2025' \
  --url https://pure.mpg.de/pubman/item/item_3670144_1/component/file_3670145/main.pdf \
  --version 'Exact PuRe item_3670144_1 component file_3670145 bytes retrieved in this audit' \
  --study-id STUDY_UUID --request-id SOURCE_UUID \
  --output .test-artifacts/research-workflow/paper-source-v1.json
```

Replace uppercase UUID placeholders with real identifiers. Retrieval: HTTPS only,
two attempts, 10 MB body limit, 20-second socket timeout and a 40-second POSIX wall
deadline per attempt. Platforms without this deadline support require documented
external retrieval. `downloaded_bytes` counts captured application body bytes, not
billed network traffic. Invalid or
captured prefix responses are retained separately and never called full text.
PDF parser: fixed subprocess, 30-second wall/20-second CPU budget, 300 pages,
2 million characters; 512 MiB address-space cap on Linux. macOS address-space
enforcement is unavailable and recorded as missing. Failures have a separate
`.failure.json` journal. Visual/semantic review, figures, tables, appendices,
supplements, extraction order and direct-reference identification remain necessary.

Complete review means substantive examination of all material under the explicitly
recorded review definition, with findings and no undeclared omissions. Available
statuses: complete-review, partial-review, retrieved-unreviewed,
unavailable-full-text. Hash verification is byte identity only.

## External campaign import

`import-campaign --bundle FILE --request-id IMPORT_UUID --study-id STUDY_UUID
--output NEW_REPORT` validates locally. The JSON bundle has exactly these fields:

```json
{
  "version": 1,
  "records": [{"kind": "runs", "record": {"id": "RUN_UUID"}}],
  "artifacts": [{"path": "raw-input.bin", "sha256": "SHA256", "media_type": "application/octet-stream", "version": "original author artifact revision", "transformation": "none"}],
  "provenance": {"producer": "Actual executor", "execution_revision": "Exact revision", "execution_dates": "Recorded dates", "limitations": "Execution provenance and scientific limits"}
}
```

This illustrates the envelope only: every record must include all fields declared
in `workflow.FIELDS`, with explicit nulls and reasons. Original paths stay inside
the bundle directory. Each run input and available output hash identifies a
supplied file. Include source/configuration/protocol/campaign records as needed,
with study-consistent foreign keys. Reviews/publications are not imported. Limits:
10 files, 10 MB each, 30 MB aggregate, 10,000 records, 180 seconds storage work.
Input metadata and all original files are privately stored and round-trip verified.

## Persistence and API

Commands default to local evidence files. Remote writes need `--persist`, the
existing owner's `--actor-id`, the exact JumpServe project URL, public API key and
a **server-only** service credential (or the guarded AWS secret). Verify targets
and deployment authorization using the infrastructure guide first. `intake`
creates ownership; other commands verify it. Local receipts do not imply remote
persistence. A failed storage request requires retaining inputs and retrying the
same ID; storage cannot be guaranteed during an outage or Lambda timeout.

API routes share the benchmark HTTP API origin:

| Endpoint | Access / behavior |
| --- | --- |
| GET `/research/capabilities` | Public adapter and resource inventory |
| GET `/research/studies` | Public reviewed snapshots; first 100 with has_more |
| GET `/research/studies?mine=1` | Google user-owned drafts; bounded listing |
| POST `/research/studies` | Google-authenticated intake, stable request UUID |
| GET `/research/studies/{id}` | Owner draft or complete published snapshot |
| POST `.../records` | Owner metadata; no fabricated execution rows |
| POST `.../protocols` | Owner freeze/amend; server hash and time |
| POST `.../runs` | Owner registered numerical execution, ≤1000 observations/10 s |
| POST `.../publish` | Owner reviewed publication or withdrawal with reason |
| GET `.../queue` | Owner-private complete queue and event history |
| POST `.../queue` | Owner immutable campaign job with typed prerequisites |
| POST `.../queue-actions` | Owner review, cancel unstarted work or attach a recorded domain run |

Bodies are ≤512 KB; browser raw-input editor caps 256 KB. CLI campaigns allow
≤10,000 observations, ≤10 MB input and ≤300 seconds arithmetic. Snapshots refuse
truncation: interactive ≤10,000 records per kind/4 MB; operator export ≤100,000
per kind/128 MB. Missing usage/cost is null. No experiment instances, arbitrary
submitted code or model calls are launched by this API.

Queue execution is described in `jumpserve-infra/docs/research-queue.md` and uses
`scheduler.py`/`worker.py`. Atomic Postgres scheduling bounds four global jobs/two
per study and declared exclusive resources. Only registered numerical jobs run
automatically; manual/source-review tasks require a domain operator. Two slots per
poll can overlap, without implying independent samples or reviewers. Immutable
definitions, fenced leases and attempt events retain failures/expiry and explicit
follow-ups; there are no automatic experiment retries or claim assessments.
Worker activation defaults off and is reported separately from job readiness.

`run.output_sha256` identifies canonical UTF-8 JSON for `published_values`,
`measurements`, `summaries` and `comparisons`, excluding run metadata. The stored
complete-output artifact has a separate original-byte hash, verified after retrieval.
Do not compare these different hash scopes as if they identify the same bytes.
Run provenance records the adapter implementation's file SHA256 and Python version.

`import-ipv6` bridges the original assessment-v2 register: 74 sources, 15 claims,
15 assessments and nine gaps. It does not copy/re-execute the 136,512 original
measurements or change their statuses; the original module remains authoritative.
The shared module links its existing evaluated chat. Generic AI interpretation is
disabled pending a module prompt/tool design, frozen evaluation criteria, actual
answer evaluation, provenance and declared publication checks.
