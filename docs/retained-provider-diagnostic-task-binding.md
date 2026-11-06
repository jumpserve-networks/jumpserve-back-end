# Retained provider diagnostic task-binding correction

## Reproduced defect

The delivered installed collector (source revision
`feea6589c5ddd1fe909ff6c3e0f58081db8864ca`, module SHA-256
`59e8a1e60b59dd73e5f08b3f0ca0d9045307d9df7386899307ce26cd9f67e621`)
uses fixed task, incident and dispatch identifiers. Against the supplied current
assessment repair packet it returns a different task/incident and
`identity-unavailable`, without inspecting the unresolved call.

The correction obtains identities from the trusted evidence packet, supports a
repair-sourced incident with unresolved native calls, and reports separately
whether each original request, response, completion sidecar and cleanup receipt
is indexed and whether its bytes can be hash-verified. Sidecars are selected only
from the matching dispatch and exact call number. Multiple unresolved calls are
bounded and explicit truncation never suggests that all calls were checked.

These are availability diagnostics, not terminal-response validation,
reconciliation, cleanup, replacement admission or scientific findings. No provider
client, credentials, persistence write or external operation was added. Existing
controller-request stream diagnostics remain in place.

## Qualification and release handoff

- `python -m unittest discover -s platform_supervisor/tests`: 158 tests passed.
- All existing predecessor test files are byte-for-byte unchanged; eight new
  regressions cover current packet binding, unrelated/stale identities, absent
  identity, multiple calls and bounds, unavailable originals, hash/index
  conflicts, exact sidecars, and propagation of persistence holds.
- Local inspection by the unchanged supervisor scope gate passed against
  hash-verified delivered installed source and delivered predecessor tests. Only
  the diagnostic module and its new regression file are candidate paths.
- Credential, authority, uncertain-action admission, transport and qualification
  kernel files/methods are unchanged.
- The literal capability descriptor and legacy exported fixture constants remain
  unchanged to satisfy frozen module-level and predecessor-test qualification.
  Those constants no longer bind collection. This is an operational source
  correction through the supervisor self-repair route, not proof that the earlier
  minimum-capability-revision-2 request has been fulfilled. The diagnostic report
  schema is v2; the pack descriptor remains revision 1. Verify exact deployed
  source revision and module hash, not the pack revision alone.

The controller must run its unchanged fixed checks and frozen predecessor
qualification, release only through `supervisor-runtime-release.yml`, and verify
the actual installed revision/module hash before collecting fresh private
evidence. Local tests and a proposed patch do not establish installation.

## Remaining assessment blocker

Assessment task `2277f9be-ce58-5e9b-b383-b282c7913e45` still has native call
`e1dd7f5c-ab3b-44e1-a8e7-0129968f2f7a` (number 79) in dispatch
`6bf4dac3-01d2-56e1-a273-bea290fe94d1` recorded as unknown. Original native
request SHA-256 is
`3f91b5124edb8abcb6cdc1f6e1f3bd88c1ddfe2cf4c5cc710fbc3c6f96e0022f`;
original dispatch request SHA-256 is
`c69a42b7baf5c541f73dc4a9cbf006c3967da4c89352cce7d17656220ef72cf5`.
The delivered packet indexes the native request pointer but provides no native
response pointer/hash, completion sidecar or cleanup receipt for this dispatch.
Private original bytes are not available in this sandbox. This proves neither
absence of transport nor terminal completion. Another dispatch's successful
responses and the context-delivery inventory cannot resolve it.

After qualified installation, the trusted collector must inspect current original
evidence. Reconciliation, if supported by that evidence, belongs exclusively to
the existing identity-bound research-controller adapter. If originals remain
unavailable, retain unknown status and the no-replacement fence. No assessment
resumption, production release or scientific completion is claimed here.
