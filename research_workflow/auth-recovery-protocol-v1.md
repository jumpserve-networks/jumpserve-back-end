# OpenClaw authorization recovery, local development v1

Question: can the existing renewable ChatGPT grant survive normal expiry,
worker restarts and recoverable storage/provider failures without unnecessary
browser sign-in? This campaign uses synthetic credentials and provider
responses only. It does not contact OpenAI, inspect real grants, spend model
allowance, deploy, migrate production, or claim a live refresh succeeded.

Freeze before implementation: preserve the existing production adapter revision
87d7a35d31decd7d1c9f6504e51850abca87840f and prior release evidence. Keep native
client/account/host identity and the published model/prompt unchanged. No paid
fallback or model-request replay is introduced.

Required checks: valid-token reuse; near-expiry refresh and restart; two-process
refresh serialization; fresh sign-in recognized without worker restart;
invalid-grant quarantine; temporary 429/5xx backoff across restarts; configuration
and permission failures distinguished from invalid grants; bounded refresh
attempts; storage/preflight failure before any provider call; durable recovery
of an issued replacement after SQLite failure; interrupted or uncertain refresh
preserved without automatic token reuse; invalid recovery records; private file
permissions and absence of credentials in diagnostics; low-spool-capacity
admission; quota hold independent of authorization recovery; existing original
model dispatches and unknown outcomes never restarted.

Units are provider-refresh calls, model calls, bytes, seconds and persisted state
transitions. Assertions are exact deterministic invariants. No confidence
intervals or empirical model-quality inference apply. Replication units are
isolated temporary SQLite/state directories, with an actual multi-process lock
check; mocks and injected I/O failures are identified in the report. No seeds
are needed. Keep missing usage/costs missing. Resource bounds: at most three
confirmed-rejection refresh attempts per fifteen-minute window, no automatic
retry of an uncertain rotation, a bounded recovery file allocated before
refresh, and a free-space check before claiming model jobs. Preserve research
spool files; no evidence cleanup is part of this change.

Run the focused auth and runtime tests, the existing research-workflow suite,
and relevant local infrastructure/database/frontend checks if those interfaces
change. Stop a failing campaign, retain its output, correct the implementation
and record the follow-up. A live expired-grant/reconnect test and production
rollout remain conditional on a later explicit release request. A provider
rotation lost before any durable replacement reaches this machine cannot be
guaranteed recoverable; report it as an uncertain refresh, not proven expiry.

Primary sources: OpenAI's token lifetimes, accounts/sessions and refresh-error
documentation at https://developers.openai.com/siwc/token-sharing-open-source/;
the pinned native OpenClaw 2026.10.5 refresh implementation; Node 24 SQLite and
filesystem documentation. Recovery state stays beside the native grant on the
existing protected encrypted host storage, outside all research artifact spools.
