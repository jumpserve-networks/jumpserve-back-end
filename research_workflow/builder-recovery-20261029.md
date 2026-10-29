# Builder and dispatch recovery investigation, 2026-10-29

This is a local software assessment of paper assessment
`4055396e-35c0-4962-ba39-cf23e443ad8d`, not a scientific reproduction.
No production code, configuration, database records or assessment limits were
changed. Production reads verified AWS account `395567831870` and Supabase
project `regphejnlvfpyokpniny`. The connected Supabase MCP has a different project
and was not used. The reviewer/implementer is Codex (AI), not independent and
not a human reviewer.

The protocol was frozen in
[builder-recovery-validation-v3.json](policies/builder-recovery-validation-v3.json)
before this campaign's main tests. Recorded cases and tuned regressions are
development evidence, not held-out checks. Earlier failed reports remain local.

## Demonstrated mechanisms and scope

* At the 20:11 UTC retained snapshot, all five checkpoints lacked experiment
  source files. The H2 checkpoint contained 121 native calls, all reads or exec
  commands used for inspection/setup. No write/edit calls were present. A fresh
  `agent exec` does not restore a previous native conversational session merely
  because its state directory is reused. The adapter retained history as a file
  but did not deliver a useful bounded continuation inventory directly.
* Dispatch `dc5d5273-f29c-5e9a-aea6-dcc8d17c0985` failed locally when the growing
  tool history crossed the old 1-MB file bound. All six admitted native calls were
  complete with retained terminal streams. The receipt recorded tool cleanup
  and a stopped container. Treating that parent failure as unknown provider
  transport prevented safe recovery. It does not mean the scientific claim was
  tested or that the failed parent should be relabeled successful.
  The later H2 parent `8983af13-bebc-5def-bd84-8d5b521c2ece` showed the same local
  history-bound failure, with both native calls complete and cleanup recorded.
  Its newly retrieved receipt and snapshot are retained separately in
  `20261029T213731633407Z`; the earlier snapshot is unchanged.
* Two interrupted parent dispatches (`af717ce4-6790-5647-9623-e67b5f9c4eca` and
  `353bf8b5-55e4-5e5e-b0a2-c0ffa757d765`) each had four completed native calls and
  matching response hashes in the worker spool, but no final parent receipt.
  The H3 interrupted parent `45176ed6-af9f-5d6e-99af-fa2174d9f774` had no native
  admissions and only a logical request in its spool. Missing output alone is
  not zero-transport proof; recovery needs the durable admission ledger,
  original request identity, inactive task lock and confirmed container cleanup.
* The matched old/new bootstrap replay used dispatch
  `14da2c0f-5f47-567e-a995-cb6776154142` and its exact delivered checkpoint context.
  Its original short feedback was present in BOTH versions. The old bootstrap
  was 15,523 bytes; the corrected one was 15,268 bytes and additionally supplied
  the no-implementation inventory and concrete next action. A separately labeled
  sensitivity replaced only feedback with the server's actual long repair
  instruction. The old code dropped it; corrected delivery retained it at
  15,893 bytes. This demonstrates a delivery defect under that condition, not
  that feedback omission caused every historical repeated read.

These faults explain operational failure paths. The evidence does not establish
that changing delivery alone will make a live model produce a valid package, that
all claims can become conclusive, or that any networking measurement exists.

## Changes

`dispatch_recovery.py` verifies every native request and terminal SSE response
against its original artifact hash and ledger identity. A separate immutable,
service-only reconciliation record accounts for interrupted parent work after
fixed cleanup under the task's workspace lock. Original dispatches, failed calls,
commitments, prompts, findings and deadlines remain unchanged. Live controller
turns, active dispatches and unknown native outcomes stay fenced. Candidate scans
are bounded and rotate fairly; one damaged spool does not block another claim.
The controller selects the latest verified checkpoint by recording time.

The worker now captures completed results in a private persistence outbox and
retries ONLY storage/final-receipt persistence. It never invokes a provider from
that retry path. Reconciliation runs off the heartbeat loop. Lost checkpoint
acknowledgments can adopt an exact byte-identical retained workspace; differing
local work remains fenced and is not overwritten.

Builder startup keeps the complete trusted system policies and exact source
context. It includes the actual implementation inventory and next action, with
long optional feedback retained inline or as an explicitly incomplete hash-bound
excerpt. Full context and original history remain readable. Large tool histories
use lossless gzip transport with verified original/compressed hashes and global
record indices. Limits are explicit: 16 MB expanded history, 1 MB compressed file,
and the existing 128-file/4-MB checkpoint envelope. There is no silent truncation.

Implementation stagnation uses source/configuration changes rather than delivered
pages, generated transport, status notes or bytecode. Repair no longer resets that
counter or replaces an incident-specific instruction with a generic directive.
File changes remain an operational inventory, not evidence of correctness.

## Reproduction and evidence

Run in `jumpserve-back-end`:

```sh
python3 -m unittest discover -s research_workflow/tests
python3 -B research_workflow/tests/check_experiment_builder_linux.py
```

Run in `jumpserve-infra`:

```sh
python3 -B test/check_experiment_builder_database.py
python3 -B bin/inspect-assessment-4055396e.py --local-builder-replay
```

The last command requires the original retained local diagnostic directory.
`--worker-spool` performs a fixed read-only AWS inspection; invoking the helper
without arguments retrieves the fixed assessment's records and verifies original
artifact bytes. These inspection paths never invoke AI or launch experiments.

Evidence is retained under `jumpserve-infra/.test-artifacts/assessment-4055396e/`:

* `20261029T201122273408Z`: original database snapshot, verified checkpoint ZIPs,
  receipts, native streams and parsed histories. These are private diagnostics,
  not public research datasets.
* `20261029T201649511755Z/worker-spool-inspection.json`: fixed SSM inspection,
  file hashes, selected logs and exact command identity; no auth/config reads.
* `20261029T213554831096Z`: matched replay, labeled sensitivity, bootstrap bytes,
  and `original-artifact-manifest.json` covering the original diagnostic files.
  Original request SHA-256 is
  `a432a1b0d59c47c07e43eccb2facb3ce733f00aaf662ff2a68aa1d8b6d9c8a54`;
  checkpoint SHA-256 is
  `42568d6b04ac561bb39ab1714cc630c137457d0d1a50923385e2a7c188d06fc2`.

Unit checks cover completed-call/local-failure recovery, missing/unknown/altered
streams, full call coverage, zero-admission proof requirements, persistence retry
without inference, history round-trip/decompression bounds, retained feedback,
stagnation and checkpoint identity. PostgreSQL checks exercise the actual worker
through the real store/RPC adapter into an isolated database and back through
controller verification. Cleanup is mocked in that database fixture; actual
Docker cleanup, native write/edit/exec/read, installed-state continuation and
context-boundary behavior are checked separately by the pinned CLI campaign.
The database fixture's synthetic published prompt is not a production publication.

Final validation: 379 backend tests passed (30.833 seconds); five isolated
PostgreSQL check groups passed, including the actual store/RPC/worker recovery
path; the pinned native CLI/Docker campaign passed three scheduling slices with
synthetic provider responses. The final unit log is
`jumpserve-back-end/.test-artifacts/builder-recovery-v3-unit-final.log`; the final
native report is
`jumpserve-back-end/.test-artifacts/experiment-builder/20261029T213923112418Z/report.json`.
The latest database report is
`jumpserve-infra/.test-artifacts/research-ideas/database/20261029T213349143062Z/report.json`.
Earlier failing runs remain alongside these reports. An existing nonfatal
SQLite ResourceWarning was observed in the unit suite; it is not a provider,
experiment or scientific validation result.

The 21:36 UTC public snapshot still had zero experiment jobs and five untested
claims waiting for reconciliation, including a new H2 parent stop. Commitments
were 34,545 cents against the existing 100,000-cent cap. Commitments are conservative
reservations, not measured charges; actual charges and subscription allocation
remain unknown. The deadline remains November 4, 20:25:04 UTC. This campaign made
no live model calls and started no cloud instances. Local compute and read/storage
charges were not measured and are not reported as zero cost.

## Release status and remaining checks

The changes are local and uncommitted. No merge, push or deployment is authorized
by this investigation. Scientific coverage remains incomplete: no paper campaign
was executed by these software checks. No browser UI changed.

For a separately requested release:

1. Pin the new committed backend, including `dispatch_recovery.py`, in the
   versioned research runtime manifest; the current production manifest remains
   unchanged on its previous revision.
2. Verify AWS and Supabase targets again and apply
   `jumpserve-infra/database/202610290001_builder_reconciliation.sql` BEFORE
   activating the new controller and worker. Keep the published prompt guards,
   authentication, RLS, cancellation and spending/time admission intact.
3. Qualify the exact production linux/amd64 image/runtime. The local pinned
   Docker image and host architecture differ from production; local success is
   not qualification of that deployed image or kernel.
4. Freeze a separate bounded live-provider qualification protocol and evaluate
   actual continuation/package-building behavior. The zero-live-call development
   protocol and synthetic answers do not satisfy that check. A qualification
   window is not a new per-claim AI turn cap.
5. Verify append-only reconciliation, original artifacts/limits, resumed claim
   progress and actual experiment ingestion through the deployed API. Preserve
   new failures and scientifically unresolved results. Do not promise conclusive
   findings or resume any genuinely unknown native invocation by replay.
