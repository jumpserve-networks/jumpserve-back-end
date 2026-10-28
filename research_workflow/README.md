# Research verification workflow

Shared evidence records and bounded execution for `/module/research-verification`.
The framework supports domain-specific studies; it does not automatically verify
every paper. Scientific findings require appropriate checks and declared review.

## Autonomous method completion

Recoverable `design` blockers now join implementation work in the native builder.
The versioned `autonomy-methods-v1` controller instruction is retained in the
actual numbered model request, alongside the immutable published baseline prompt.
It directs the planner to choose and justify prospective settings rather than
request routine configuration from the user. Unreported original settings remain
unknown, and external data/apparatus/authorization/provider gates remain intact.

New autonomous protocols freeze a `recorded-decision-v1` object. Source arithmetic
checks every declared calculation; empirical comparisons use explicit configuration,
metric, unit and observation identities, with equal-weight whole-run means and
descriptive fixed-target/difference/percentage/order comparisons. The controller
verifies exact review candidates, claim-specific protocols, full run/observation
counts, input/output hashes and retained raw output before allowing a numerical
finding. Extracted claims need no invented prepopulated metric list. Shared
acquisitions keep claim-specific criteria; later favorable results cannot silently
replace earlier comparable runs. The admitted criterion stays locked, including
after an unconfirmed queue acknowledgement. Unsupported causal or broader
conclusions remain unresolved; descriptive diagnostics are still executable.

Source-arithmetic reviewers see ordered expressions and original numeric operands.
Both difference operations use `[baseline,treatment]`. Changing an erroneous
configuration requires a new version; original outputs and prior-run references
remain retained. This preview shares the existing source parser and is not an
independent scientific check.

Run `python3 -B -m unittest discover -s research_workflow/tests -v`. The protocol is
[autonomy-methods-protocol-v1.md](autonomy-methods-protocol-v1.md). The separate
infra command `python3 -B bin/replay-assessment-numerics.py --assessment <uuid>`
performs a read-only independent arithmetic replay over public retained cell
lexemes, with original response hashes, retrieval attempts and an explicit scope.
It does not retrieve the protected PDF, execute a networking experiment or change
the production assessment. An explicit release must qualify the augmented
requests, pin the new `autonomy_methods.py` and changed runtime bytes, deploy them,
and resume only reconciled tasks under their original budget and deadline.

The data-only timing policy `data-only-window-v2` admits a 90-second total
response stream, a 120-second CLI and a 130-second process timeout. Its dispatcher
waits at most 250 seconds. Apply infrastructure migration
`202610280001_data_only_model_window.sql` before activating that runtime: it
requires 240 seconds of remaining task/paper time and fences dispatches at 235
seconds. The controller task/Lambda bounds stay 300 seconds. The native builder
keeps its separately bounded scheduling window. An earlier partial HTTP200 stream
remains unknown; longer windows never replay or relabel an original provider call.

The fixed netem runner's conservative 10x counter allowance does not admit a full
five-level 30-second/100-Mbit/s grid under its 10-GB ceiling. A generated package
needs correct, documented traffic accounting and its own reviewed method; dropping
conditions, shortening transfers or raising limits silently is not a fix. Internal
child-namespace counters and the runtime's non-loopback counter guard have different
scopes. No full-grid acquisition or mechanism instrument was validated by the
descriptive comparator's synthetic tests.

Shared paper queue links now use the anonymous read-only status projection at
`GET /research/studies/{id}/queue?public=1`, including before assessment
publication. It returns paper/claim titles, recorded workflow/task/job statuses,
attempts, timestamps, check coverage and commitments. It omits owners, prompts,
responses, uploads, raw inputs, artifact paths, resource names, private events and
free-form failure details. Unpublished ideas and anonymous study enumeration are
not exposed. Existing queue reads without `public=1`, history, writes and
execution controls keep their authentication/ownership checks. RLS/grants and the
schema are unchanged; the backend serves an explicit bounded projection.
Deploy the matching pinned backend before the frontend after an explicit release
request. See the frontend `docs/public-experiment-queue-v1.md` protocol and run
`npm run verify:research:public-queue` there for the fixed anonymous browser →
actual API handler → synthetic-store development campaign.

Selected paper queues display an extracted paper title when the original study
title was seeded from the upload filename. `paper_titles.py` uses the existing
hash-verified text packet for that exact uploaded PDF: corroborated PDF metadata
when available, otherwise a first-page title block with an identifiable byline
and affiliation. New text packets also retain optional PDF title metadata. Older
packets work without it. Scanned, unusual or ambiguous layouts can show **Paper
title unavailable**; filenames are not treated as bibliographic evidence. Explicit
study titles remain unchanged. This display-only lookup neither edits the study,
original files or initial prompt nor invokes a model, resumes work or writes
records. It reads at most one 8 MB text packet, caches for up to 30 seconds, and
enriches only the selected owned paper in `GET /research/queue?title_study={id}`.
The shared projection still exposes only the title, never upload metadata or
artifact bytes. Unit and browser fixtures are development cases, not validation
of title extraction across all publishers. A future explicitly requested release
must include `paper_titles.py` in the pinned runtime manifest before deploying.

`/research-verification/previous-assessments` lists all successfully created paper
assessments, including submitted, running, stopped and cancelled work and earlier
published imports. There is one entry per retained assessment identity, so repeat
submissions of a paper remain distinct. Pending/rejected upload reservations are
not created studies. Unpublished ideas remain in their separate pathway. The old
`/research-verification/test-results` URL and its `/module` and `/modules` aliases
redirect permanently with query parameters preserved.

The anonymous `GET /research/papers` catalogue serves only ID, optional paper title,
creation timestamp, recorded workflow status (null when unavailable) and current
publication membership. Its 10-row keyset pages have no global first-100 cutoff;
server-side page caps are exhausted, and invalid coverage fails explicitly. Public
snapshot membership is checked through the existing anonymous RLS policy, including
withdrawals. No draft scope, source URL, owner, prompt, upload metadata or artifacts
are listed. Original filename-based titles are omitted; the UI resolves them with
at most two concurrent `GET /research/papers/{id}/title` requests using the existing
hash-verified resolver. These reads never start, resume or assess work. Catalogue
visibility does not publish an assessment or change source review status.

Development uses the existing queue browser verifier with explicit synthetic
store data and the actual API handler. Production is unchanged on editing turns.
A future authorized release must pin `paper_catalog.py` and the changed API bytes,
stage that exact runtime, and deploy the two GET API Gateway routes with the
backend before releasing the frontend. No schema/RLS migration is required.

## Automatic networking execution

An unpublished idea can contain a `network-execution-v1` declaration. Expand
**Automatic networking execution**, select a runner, enter complete configuration
and metrics with units, apply the configuration, and save a new plan version.
Complete fresh reviews, accept readiness, freeze the candidate, and queue its jobs.
Preparation alone does not allocate machines. Each configuration × replication
becomes one durable invocation; packets, clients and time samples remain observations
within it. Four global jobs and two per study can run concurrently. Declare shared
exclusive resource identities to prevent interfering trials.

Registered runners are `emulated-netem-v1` (existing Linux netem workloads),
`real-world-iperf-v1` (existing multi-machine EC2 topology), and `custom-package-v1`
(one isolated EC2 host). Original Python source is privately preserved with its
version and SHA256; public Docker images require a digest. Private files have
separate same-study artifact/version/hash references. Containers can implement
namespaces/netem; scripts can collect real networking observations. Other hardware
or custom distributed topologies need an applicable package and runner extension.
Prose alone cannot execute, and other domains retain the explicit manual path.

The owner API at `POST /research/studies/{studyId}/network` accepts `upload-package`,
`upload-input`, `compile`, and `cancel`. Compilation preserves original configuration
text, checks numeric precision and adds specification hashes. It never supplies
missing scientific decisions. Enqueue verifies the frozen specification and private
inputs. IAM-only invocations carry only a saved job ID and action; launchers resolve
owner/configuration without forwarding or saving Google credentials. Lost launch
acknowledgements trigger lookup, never an automatic second experiment. Expired,
unconfirmed executions keep their resource reservations until cleanup is known;
operator recovery must reconcile the original attempt rather than replay it.

Packages read `JUMPSERVE_SPEC` and private files under `JUMPSERVE_INPUTS`, and write
`JUMPSERVE_OUTPUT`. The `network-observations-v1` envelope requires `version`,
`observations`, `provenance` and `usage` (null when unknown). Each observation has
`observation_key`, `metric`, `units`, `value`, `status`, `reason`, and `metadata`.
Missing, invalid, ambiguous and excluded values require null and a reason; recorded
zero stays zero. Original output/stdout/stderr/runtime receipt bytes remain private
artifacts; retained prefixes are explicitly incomplete. The normalized executor
envelope has its own hash and transformation provenance. Built-in traces stay in
their original module relations and Storage, linked from the run. Invalid output
creates a failed run with available bytes retained. Successful execution records
measurements and declared coverage in Supabase and leaves scientific review pending.
It computes no inferential intervals, AUC, causality or generalization judgment.

Custom hosts have no IAM profile or metadata endpoint, no inbound access, and
independent deadline shutdown and cleanup. Containers get one CPU, 1 GiB and 256
PIDs; Python uses the finite host capacity. Output/network thresholds use 50-ms
sampling and may overshoot. Non-loopback interface counters can count forwarding
multiple times; unavailable/reset counters remain missing and are not billed traffic.
Built-in runners retain their existing traffic controls and do not enforce this
custom network byte threshold. Allocation, installation and cleanup consume the
wall budget. Packages must report dependency versions; actual machine image IDs
are retained. An image digest does not archive its Docker layers. Unavailable
charges and compute/model usage remain missing, never zero.

`tests/fixtures/network_http_probe_v1.py` measures request start through response
headers. It is not TCP RTT, exact TTFB or a CDN demand experiment. Existing frozen
CDN protocols and datasets remain separate. A new CDN campaign needs controlled
objects and demand assignments, genuine VPN vantages, origin credentials and the
declared analysis. Private inputs support those credentials; the engine does not
invent them. Local checks use actual loopback requests and isolated Postgres with
synthetic auth/cloud transport. They are exposed development cases reviewed by
the AI implementer, not human review or held-out scientific validation.

Deployment requires the additive migration, matching pinned runtimes and explicit
`networkExecutionEnabled=true`; see the infrastructure README and versioned
validation report for release order, commands and conditional cloud checks.

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

## Unpublished research ideas

Idea intake uses the existing authenticated study endpoint with
`study_kind: "idea"` and `paper_url: null`; existing paper request bodies and
replays remain valid. Ideas are private owner drafts, not invented papers.
GET/POST `/research/studies/{id}/idea` saves immutable hashed plan packets,
records explicit readiness acceptance, prepares a reviewed campaign, or resumes
its original queue identity. The candidate protocol is structured JSON and must
meet the existing protocol schema and resource bounds before acceptance.

Planning and fresh comprehension/critic calls use the existing authenticated
Agent Lambda with `action: "idea"`. The server selects the research-verification
prompt, verifies user/module/study ownership, and excludes planning history and
prior verdicts from fresh reviews. The single structured response channel never
executes an action. Every call, including failures, is retained; the latest review
attempt for each role controls readiness. Substantive revisions require new
reviews. Separate invocations of a shared model are not independent scientific
reviewers.

Campaign preparation preserves the actual unpublished proposal, hypothesis,
configurations, candidate protocol and untested evidence gap. It creates no
published values, measurements, scientific assessments or executed runs. Domain
jobs are manual: an operator must supply an applicable implementation, retain
actual observations and import/review the evidence through the existing workflow.
Plan readiness and an AI recommendation never establish a research result.

Run `python3 -B -m unittest discover -s research_workflow/tests -v` locally.
The additive migrations, frozen evaluation protocols, actual development-answer
reports and release order are recorded in jumpserve-infra's README and
`docs/research-ideas-protocol-v1.json`. No production deployment is implied by
local implementation or fixture publication.

## Autonomous AI turns

The `research-autonomy-v3` application defaults are $100 in aggregate paper
commitments and seven elapsed days, with **no AI turn-count limit**. A null
`attempts_per_claim` / `max_attempts` means unlimited turns, not missing usage or
zero allowed calls. Intake and per-claim planning, reviews, reads and assessments
continue while the existing resource and dependency gates admit useful work.
Turn numbers remain cumulative; every request, failure and response stays in
owner-private, paginated history. Parallel claim and experiment capacity is
unchanged. Removing the count limit does not guarantee a conclusive finding.

The versioned `policies/autonomy-turn-policy-v1.json` controller instruction is
retained in every complete request. It explicitly supersedes obsolete count
ceilings in the immutable historical published prompt and initial requests.
Budget/deadline authorization, scientific criteria, subscription pauses and
uncertain-call reconciliation remain in force. Original prompts are not edited.

Deploy the matching infrastructure migration
`202610200008_remove_autonomy_turn_cap.sql` before enabling this worker. It stores
an immutable policy-change event for existing papers without resetting costs,
deadlines or evidence. The corrected worker can resume eligible historical
`attempt-limit` stops at their retained stage; expired, exhausted, cancelled,
externally blocked or uncertain work is not revived by that recovery gate.

Earlier OpenClaw/Bedrock protocols, results and trajectory-evaluation policies
are historical experiments with their own frozen ceilings. They remain intact;
this application change does not replay them or overwrite their findings. A
future pilot continuation needs a new versioned protocol within retained paper
resources. Local validation and release gaps are documented in
`jumpserve-infra/docs/autonomy-turn-policy-validation-v1.md`.

## OpenClaw subscription worker

`RESEARCH_AI_RUNTIME=openclaw-production-v1` selects the separately deployed,
SSM-managed worker with OpenClaw 2026.10.5, Node 24.16.0 and `gpt-6.1-sol`.
The host has its own native Sign in with ChatGPT grant and explicit plan-use
consent. Its grant is never copied from a developer machine, sent to the browser
or inherited by the per-turn OpenClaw process. API-key and Bedrock fallback are
disabled for this configuration. Quota/authentication pauses remain visible.

The local `openclaw-auth-recovery-v1` candidate keeps the native grant in its
existing protected host directory, outside research spools. A single host lock
serializes bridge refreshes. The helper preallocates a 256-KiB private recovery
file, records refresh intent and flushes the returned replacement before the
SQLite commit. A later process can recover that replacement after a failed
commit. It never restores an older grant over a fresh sign-in or signed-out
profile. SQLite uses a write transaction and full synchronization, with an
8-MiB free-space preflight before provider refresh. The worker stops claiming
model work below 512 MiB free; original research evidence is retained.

Confirmed invalid grants require reconnection and are quarantined without
repeating refresh requests for the same credential. Temporary negative refresh
responses back off after the failure, at most three attempts per fifteen-minute
window. Configuration, permission and storage failures report unavailable,
not expired authentication. The helper timeout allows the pinned SDK's token
exchange and signing-key check to finish. An uncertain refresh can have consumed
a rotating token: it stays unavailable for reconciliation and is not blindly
replayed. Provider rotation lost before any durable replacement reaches this
machine cannot be guaranteed recoverable.

Fresh credentials are recognized without restarting the worker. Quota holds
persist separately and survive reauthorization/restarts; an operator who has
checked ChatGPT usage can explicitly clear only that hold with
`/opt/jumpserve/python/bin/python -B /opt/jumpserve/runtime/openclaw_runtime.py --resume-quota`
as the `jumpserve` service user. This command does not verify provider allowance
or repeat an earlier model request. Safe reason/cooldown values are retained in
private worker configuration and failed-turn provenance. Public status fields,
RLS, prompts, model choice and inference billing/fallback rules are unchanged.

See [the local auth recovery protocol](auth-recovery-protocol-v1.md), run
`python3 -B -m unittest discover -s research_workflow/tests -v`, and consult
`jumpserve-infra/docs/openclaw-auth-recovery-v1.md` before an explicitly requested
release. These are local development checks with synthetic credentials/provider
responses; they do not exercise production authorization or spend plan allowance.

The controller first reserves paper resources and saves the complete numbered
turn. A private SQL dispatch binds that exact request to one fenced worker lease.
OpenClaw executes a fresh data-only turn through a private loopback bridge; the
bridge admits at most one actual provider request. Tool execution stays with the
existing reviewed networking/measurement adapters. An uncertain provider outcome
requires reconciliation and is never retried automatically under a new identity.

Original logical/native/provider request and response bytes, native CLI output,
typed controller response, available usage and transformation provenance are
stored as private hash-verified artifacts. Missing usage and subscription cost
allocation remain unknown. The $1 per-turn reserve is a conservative orchestration
commitment, not a measured inference price. Numeric zeros in OpenClaw's internal
model cost catalog are adapter placeholders and must not become reported charges.

The phase contract is checked on actual output: reviews cannot issue findings,
planning cannot declare evidence coverage, and evidence references belong to
assessment. Candidate v9 failed substantive AI inspection and is preserved;
candidate v10 has its own sixteen-answer evaluation. The evaluator and reviewer
are the same AI implementer, not human or independent reviewers. These software
checks do not reproduce a paper's scientific claims.

Deployment order, immutable evaluation/publication guards, host setup and release
evidence are in `jumpserve-infra/docs/openclaw-production-protocol-v1.json` and its
versioned amendments. Native host authorization and deployed authenticated
execution must be verified before announcing the subscription path as available.
### Executable assessment methods (local implementation, pending release)

`netem-iperf-v1` runs a fixed duration-based TCP loss sweep on one isolated Linux
experiment host. Each invocation contains the complete loss grid in randomized
order; replications are separate invocations. The five outputs are receiver
goodput in decimal Mbit/s, received bytes, actual seconds, median unloaded ICMP
RTT in ms, and sender retransmissions. Raw command outputs, versions, counters,
MTU, offload settings and buffer settings remain in hash-bound artifacts. A
condition-order seed is distinct from `loss_seed`; null means an uncontrolled
kernel loss PRNG, not a reproducible packet-loss sequence. Missing values remain
missing. This runner does not identify a causal window/queue mechanism.

`source-arithmetic-v1` reads immutable extracted PDF text using its exact hash
and original-PDF binding. Every cell names a unique quote, page, numeric token
and units. Bounded Decimal mean/difference/relative-change/strict-order operations
produce measurements separately from source-reported values. It accepts no
submitted expression or executable code. These are source consistency checks;
they do not automatically establish empirical or causal paper claims.

The shared-acquisition coordinator atomically binds identical source/configuration/
input/metric/resource acquisitions to one campaign and its original jobs. Every
claim retains its own frozen protocol, criterion, reviews and configuration mapping.
Human labels do not change acquisition identity. Shared observations are the same
evidence, never additional independent replications. A changed physical design or
source version produces a new acquisition.

Confirmed transient HTTP model failures are retained before a new numbered turn
is scheduled with 15–300 second backoff. New turns use the original $100/7-day
admission controls. Unconfirmed dispatches, auth/quota failures and experiments
are not blindly replayed; cloud polling reconciles their original identities.
There is no AI-turn-count cap and no paid API fallback.

The fixed built-in EC2 runner uses a separately pinned executor version and
server-owned cost envelope; it can be admitted while arbitrary custom-package
submission stays disabled. The original built-in script bytes are retained as
assessment artifacts and checked on the experiment host. Apply infrastructure
migration `202610260001_executable_assessment_methods.sql` before releasing this
backend. See `jumpserve-infra/docs/executable-methods-validation-v1.json` for exact
verification commands, release blockers and conditional live checks. Existing
assessments are not resumed or rewritten by this migration.
