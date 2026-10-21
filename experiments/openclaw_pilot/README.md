# OpenClaw / JumpServe lab pilot

This is a local, bounded comparison for the IPv6 paper's **Published absolute
folded metrics** claim: six archived checks covering 1,152 cells in Figures
13–15 a/b. It evaluates OpenClaw as a model-turn runtime behind JumpServe's
existing controller. The controller still owns review phases, experiment
admission, queueing, arithmetic, evidence coverage and final assessments.

OpenClaw's native experiment tools, persistent claim conversations, parallel
subagents, multi-user isolation and website integration are outside this first
pilot. Public-site isolation is deferred as requested. Existing RLS and browser
write restrictions are retained. No production deployment is performed.

## Frozen conditions

- OpenClaw **2026.10.5**, project-local Node **24.16.0**, and npm lockfile.
- Same Claude Opus 4.6 Bedrock inference profile and evaluated JumpServe v8 core
  prompt in both arms; temperature 0, up to 4,096 output tokens. The interface
  exposes no seed; temperature 0 does not guarantee deterministic responses.
- Both arms use the same local controller and original archived plan. Each
  OpenClaw turn uses a new session to preserve fresh comprehension/critic review
  boundaries. Its additional system instructions and actual HTTP request body
  are retained, so the two complete prompts are not claimed byte-identical.
- The adapter maps the text-only OpenClaw request to the same Bedrock structured
  decision schema and renders the actual decision as JSON assistant text. All
  OpenClaw tools, Tool Search, Code Mode and plugins are disabled. The CLI's
  delivered answer must equal the retained provider decision.
- Shared conservative commitment cap **$100**, 25 provider calls per arm and a
  45-minute campaign window. Unknown or failed provider calls retain their
  commitment and prevent automatic further calls. No EC2 instances are launched.
- Estimates use the existing versioned conservative pricing ceiling. Invoice
  charges, local compute/storage and other overhead allocations remain unknown.
  The synthetic smoke response's displayed cost is not a measured model charge.

The [original protocol](protocol-v1.json) and [v2 amendment](protocol-v2.json)
precede their respective live attempts. V1 stopped before a paid provider call:
an arm-name variable was overwritten during preparation, and SQL rejected it.
Its frozen code, two local study records and failed report remain in
`artifacts/v1`. V2 validates exact arm identities and carries forward V1's
conservative overhead reservation. It does not change the scientific criteria.

The [v3 continuation protocol](protocol-v3.json) was excluded when its original
evaluation window expired before recovery. The separately declared [v4
follow-up](protocol-v4.json) resumed the same tasks after refreshed AWS sign-in,
preserving paper deadlines, attempts, data and commitments. It completed one
additional check and stopped on a read timeout with an uncertain provider
outcome. That request is retained and is not automatically replayed.

The [aggregate results](results-v4.json), [validation report](validation-v1.json)
and [private artifact manifest](artifacts-manifest-v4.json) describe the limited
outcome. Three live controller checks cover 576/1,152 cells, all agreeing under
their frozen criteria. The OpenClaw arm made **zero actual model calls** because
the shared provider-failure guard stopped admission. Its real CLI/transport smoke
passed against a synthetic provider. This does not establish live OpenClaw
feasibility or a performance comparison.

## Commands

Run from `jumpserve-back-end`. Install the locked runtime with Node 24.16.0
active (`npm ci` in `experiments/openclaw_pilot/runtime`). Do not use the machine's
older Node to install OpenClaw: its preinstall version check correctly refuses it.
The pilot uses sibling `jumpserve-infra` migrations and its existing local
Postgres fixture at `127.0.0.1:54477`. AWS CLI profile `jumpserve` is required only
for the live campaign.

```sh
python3 -B -m unittest discover -s experiments/openclaw_pilot -p 'test_*.py' -v
python3 -B experiments/openclaw_pilot/run.py smoke
python3 -B experiments/openclaw_pilot/run.py check
python3 -B experiments/openclaw_pilot/run.py prepare
python3 -B experiments/openclaw_pilot/run.py run
```

The preserved follow-up entry points are `followup.py prepare` and
`followup.py run` in the same directory. Their existing frozen/attempted campaigns
also refuse replay. `python3 -B experiments/openclaw_pilot/report.py` derives the
aggregate export from the retained v4 outcome.

`smoke` exercises the real OpenClaw CLI against an explicitly synthetic local
provider and makes no paid call. `check` applies actual migrations to a new local
database, checks private access and immutable request/response bytes after
retrieval, and executes the original six archived arithmetic checks.

`prepare` freezes exact source versions, original protocol inputs, the npm
lockfile, Node/CLI bytes and migration hashes. `run` refuses source drift or a
previously admitted campaign. It verifies AWS **395567831870** and Supabase
**regphejnlvfpyokpniny** before model calls. Supabase target inspection is read
only. All experimental writes are local.

To reproduce a campaign, use a fresh checkout/evidence destination and preserve
the original frozen evidence. The CLI deliberately refuses to overwrite or reset
an attempted campaign. Amendments need a new protocol version and must retain
earlier commitments. Never delete reports to make a retry appear new.

## Persistence and assessment

Each arm has a separate local Postgres database with the original Supabase
migrations, actual controller and queue, and actual archived numerical adapter.
Google identity, HTTP/Storage transport and local prompt activation are explicit
fixtures. No real authentication or remote Storage roundtrip is claimed.

The coordinator database applies
`jumpserve-infra/database/202610200003_runtime_pilot_audit.sql`. It records the
protocol and configuration, every actual provider request before dispatch,
original CLI response bytes, hashes, controller-turn links, statuses, reservations,
usage and estimated costs. OpenClaw's submitted HTTP body is retained separately
from the translated Bedrock request. Provider HTTP response wire bytes are not
available. Original controller prompts remain in `research_autonomy_turns`;
measurements and published values occupy separate research relations.

Raw artifacts, state databases and exports are private local files under ignored
`artifacts/`. They include complete archived data, synthetic user identifiers and
ephemeral loopback credentials; do not publish the raw directory. A separate
report can publish aggregate results and artifact hashes. Remote Supabase import,
public download permissions and web queue integration remain later release work.

## ChatGPT Pro subscription campaign (v5–v9)

The separately versioned [v5 protocol](protocol-v5.json) and
[v6 correction](protocol-v6.json) use the same archived
IPv6 checks with **GPT-6.1 Sol** and OpenClaw's **Sign in with ChatGPT** grant.
This is a new, exposed model/provider trajectory, not an equivalent Claude arm
or independent scientific replication. The original v1–v4 runs and their $27.15
conservative commitments remain retained and count toward the same $100 cap.
V5's preparation was rejected by the existing prompt publication guard before
inference because the new model patch also affected the original local prompt
fixture. Its original code, inputs, database and failure receipt remain in
`artifacts/v5`; V6 applies the model switch only to inference, leaving original
fixture evidence and the publication guard intact. Another $10 conservative
preparation overhead is carried forward, so prior commitments total $37.15.
V6 then rejected the subscription profile before any model-catalog/inference
request: native OpenClaw had stored it in its shared state cell while the adapter
read only the empty per-agent table. [V7](protocol-v7.json) reads the exact native
layout, requires one subscription profile across both stores, and adds that
regression. V5/V6 preparation reserves remain counted, for $47.15 prior
commitments before V7's $10 overhead and bounded turn reservations.

Exploratory V7 catalog checks also caught Python's missing default CA file before
inference. The final configuration freezes the host public CA bundle and keeps
certificate and hostname verification enabled. The pre-correction source/tests
and unsuccessful preflights are retained. The actual account catalog then
confirmed GPT-6.1 Sol. These setup corrections are not scientific discrepancies.

V7's first Responses request was explicitly rejected because JSON mode required
“JSON” in an input message. [V8](protocol-v8.json) adds that developer instruction
and resumes the same task with its original counters, ledger and deadlines.
Another $10 conservative follow-up overhead remains reserved. The original HTTP
400 receipt is immutable and its unavailable usage is not recorded as zero.

V8 then received a valid, completed planning answer and usage, but the adapter
looked only at the empty terminal `output` array. The actual text was present in
`response.output_item.done`. [V9](protocol-v9.json) reconstructs completed items,
checks output/index identities and text consistency, and retains a separate
reanalysis of that original stream. It continues at the next numbered attempt;
the prior adapter failure is not overwritten or reused as a fresh model answer.
This correction is covered by V8's reserved overhead. Both recovery receipts are
append-only and cannot authorize unknown or different failures.

The [post-campaign verification](subscription-verification-v1.json),
[result export](subscription-results-v1.json),
[source inventory](subscription-sources-v1.json) and
[raw-byte manifest](subscription-artifacts-v1.json) retain the outcome. The actual
subscription campaign made 25 requests: 23 accepted decisions, one explicit
HTTP 400 rejection and one completed answer initially rejected by the adapter's
stream parser. That answer was reanalyzed separately, preserving its original
status. Known usage totals 312,000 tokens; the HTTP 400 request's usage remains
unavailable. All 100 original logical/request/transport/response byte records
were verified after SQL retrieval. The post-export manifest verifies 1,189
files totaling 749,632,367 logical bytes, excluding credentials.

All six archived jobs completed with 1,152 distinct recorded cells in percent,
all matching their original tolerances. The independent Decimal difference
check passed while sharing the archived inputs. The controller reached its
unchanged 25-attempt limit before the final assessment, so its claim label remains
inconclusive and the whole frozen workflow feasibility criterion did not pass.
Numerical agreement is reported separately. Conservative aggregate commitments
are $92.15/$100, including earlier setup/Bedrock reserves; billed subscription
allocation and local compute/storage charges are unknown. These are local lab
results; remote Supabase and production website integration remain unverified.

`subscription.py` supplies a loopback bridge from the real pinned OpenClaw CLI to
the official Responses API. Only the dedicated OpenClaw SIWC OAuth credential
with explicit plan-use permission is accepted. It never loads API keys, alternate
accounts or Bedrock credentials and does not implement paid API or model
fallback. OAuth/PKCE and consent run through the installed OpenClaw CLI.

```sh
python3 -B experiments/openclaw_pilot/subscription.py check
python3 -B experiments/openclaw_pilot/subscription.py prepare
python3 -B experiments/openclaw_pilot/subscription.py login
python3 -B experiments/openclaw_pilot/subscription.py catalog
python3 -B experiments/openclaw_pilot/subscription.py run
python3 -B experiments/openclaw_pilot/subscription.py status
```

The latest versioned continuation entry points and current read-only status are:

```sh
python3 -B experiments/openclaw_pilot/subscription_stream_followup.py check
python3 -B experiments/openclaw_pilot/subscription_stream_followup.py prepare
python3 -B experiments/openclaw_pilot/subscription_stream_followup.py run
python3 -B experiments/openclaw_pilot/subscription_status.py
python3 -B experiments/openclaw_pilot/subscription_report.py
```

Existing frozen/attempted campaigns refuse replay. `subscription_status.py`
reads the latest campaign's actual local queue stage, token totals with missing
counts, recorded measurements and commitments. An unavailable database produces
unknown counts, not zeros. `subscription_report.py` writes a new timestamped
validation report, documentation inventory and original-byte manifest; it never
replaces earlier exports. Run exports after the campaign finishes to avoid a
snapshot of actively changing artifacts.

Start the existing isolated local Postgres server on port 54477 before `check`,
`prepare` or `run`. `check` preserves both successful and failed gate reports and
raw output. `prepare` requires a passing gate for the exact frozen source files.
It creates a new local study, preserves its protocol and original inputs, and
requires no model call. `run` verifies the exact account model catalog before
inference, refuses source drift or a previously attempted campaign, and records
the new model identity in controller turns. No AWS session is needed for this
subscription campaign because it makes no AWS API calls or remote mutations.

Credentials live only in the ignored `.subscription-auth/` directory (private
directory permissions). They are not copied into run artifacts or fresh per-turn
workspaces. Treat that login directory as credentials, not downloadable evidence.
Do not publish it or the ignored raw artifact directory.
The endpoint consumes the selected account's plan allowance and any additional
ChatGPT credits allowed by that account's settings. The adapter cannot inspect
that setting or infer remaining allowance. To limit usage to the included plan,
disable app spending of additional credits in ChatGPT's Usage settings where
available. The adapter never enables credit purchases or automatic paid API
fallback.

The subscription route requires `store:false`, `stream:true`, and excludes
temperature and output-token-limit fields. The logical controller envelope keeps
its compatibility fields; the separately retained actual Responses request is
the authoritative provider configuration. Input requests are bounded to 96,000
bytes, client response collection to 1 MiB plus one over-limit detection byte,
and each provider operation has a 120-second admission/stream deadline. Closing
a stream is not proof of provider cancellation. The campaign admits at most 25
model requests within a 45-minute window. Each accepted response must complete
its stream, contain one valid data-only decision, and match OpenClaw's delivered
answer. No model tools execute.

Quota errors are recognized both before and during streaming. Quota exhaustion,
unavailable allowance, auth failure, unknown transport outcomes and invalid
answers stop further admission. OpenClaw loopback retries cannot issue another
actual provider request. `status` reads retained local results without making an
inference call. It reports known token totals alongside missing counts and keeps
unavailable quota/reset times and subscription cost allocation null. View the
account's allowance at <https://chatgpt.com/settings/usage>. A pause requires a
separate versioned follow-up; do not delete receipts or reset counters to retry.

New $1 turn reservations are conservative **orchestration allowances**, not
API prices or measured subscription charges. Native runtime cost fields are
required numeric placeholders; their displayed zero is not used as a charge.
The monthly subscription, compute/storage allocation and any unavailable usage
remain unknown. The new private local SQL migration is
`202610200005_subscription_pilot.sql`; it retains immutable original logical,
OpenClaw transport, Responses request and provider SSE response bytes with hashes.
It has not been applied to production Supabase. No website deployment or new
production prompt publication is included.

The success criterion requires both arms to resolve the scoped claim, all 1,152
matched cells, matching measurements, an independent Decimal arithmetic check
sharing the same archived inputs, and verified stored original bytes. A repeated
cached numerical check does not independently validate the original measurements
or establish correctness, causality, operational effectiveness or generalization.

This is one exposed claim and one trajectory per runtime. Review is by the
primary AI implementer, without an independent or human reviewer. Any latency,
cost or turn-count comparison is descriptive. No confidence intervals or claim
of OpenClaw superiority are justified. Failed setup checks, invalid answers and
unresolved outcomes remain part of the evidence.
